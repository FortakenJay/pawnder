"""SQLite memory across sessions: games, the player's mistakes, and the tactics behind them.

This is what turns an engine into a coach: "3rd game in a row you've dropped a piece to a knight
fork" comes from counting motif tags across games here.
"""

from __future__ import annotations

import sqlite3
import threading
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path

from .motifs import label

SCHEMA = """
CREATE TABLE IF NOT EXISTS games (
    id INTEGER PRIMARY KEY,
    started_at TEXT NOT NULL,
    ended_at TEXT,
    white TEXT,
    black TEXT,
    player TEXT,
    result TEXT,
    pgn TEXT,
    review TEXT
);
CREATE TABLE IF NOT EXISTS mistakes (
    id INTEGER PRIMARY KEY,
    game_id INTEGER NOT NULL REFERENCES games(id),
    ply INTEGER NOT NULL,
    color TEXT,
    move TEXT,
    best TEXT,
    refutation TEXT,
    cp_loss INTEGER,
    severity TEXT,
    fen_before TEXT,
    message TEXT
);
CREATE TABLE IF NOT EXISTS motifs (
    mistake_id INTEGER NOT NULL REFERENCES mistakes(id) ON DELETE CASCADE,
    game_id INTEGER NOT NULL,
    motif TEXT NOT NULL
);
"""


def ordinal(n: int) -> str:
    suffix = "th" if 10 <= n % 100 <= 20 else {1: "st", 2: "nd", 3: "rd"}.get(n % 10, "th")
    return f"{n}{suffix}"


@dataclass
class Recurrence:
    motif: str
    streak: int  # consecutive games, ending with this one, that had it
    games_with: int  # games that had it, out of ...
    games: int  # ... this many (all games so far, this one included)

    def sentence(self) -> str | None:
        name = label(self.motif)
        if self.streak >= 2:
            return f"{ordinal(self.streak)} game in a row with {name}"
        if self.games_with >= 2:
            return f"{name} in {self.games_with} of your {self.games} games"
        return None


class Memory:
    def __init__(self, path: Path | str) -> None:
        self.db = sqlite3.connect(str(path), check_same_thread=False)
        self.db.row_factory = sqlite3.Row
        self.db.execute("PRAGMA foreign_keys = ON")
        self.db.executescript(SCHEMA)
        self.lock = threading.Lock()

    def start_game(self, white: str, black: str, player: str) -> int:
        with self.lock, self.db:
            cur = self.db.execute(
                "INSERT INTO games (started_at, white, black, player) VALUES (?, ?, ?, ?)",
                (datetime.now(UTC).isoformat(timespec="seconds"), white, black, player),
            )
            return int(cur.lastrowid or 0)

    def finish_game(self, game_id: int, result: str, pgn: str) -> None:
        with self.lock, self.db:
            self.db.execute(
                "UPDATE games SET ended_at = ?, result = ?, pgn = ? WHERE id = ?",
                (datetime.now(UTC).isoformat(timespec="seconds"), result, pgn, game_id),
            )

    def set_review(self, game_id: int, review: str) -> None:
        with self.lock, self.db:
            self.db.execute("UPDATE games SET review = ? WHERE id = ?", (review, game_id))

    def add_mistake(self, game_id: int, row: dict, motifs: list[str]) -> int:
        with self.lock, self.db:
            cur = self.db.execute(
                "INSERT INTO mistakes (game_id, ply, color, move, best, refutation, cp_loss, severity, fen_before,"
                " message) VALUES (:game_id, :ply, :color, :move, :best, :refutation, :cp_loss, :severity,"
                " :fen_before, :message)",
                {"game_id": game_id, **row},
            )
            mistake_id = int(cur.lastrowid or 0)
            self.db.executemany(
                "INSERT INTO motifs (mistake_id, game_id, motif) VALUES (?, ?, ?)",
                [(mistake_id, game_id, m) for m in dict.fromkeys(motifs)],
            )
            return mistake_id

    def set_message(self, mistake_id: int, message: str) -> None:
        with self.lock, self.db:
            self.db.execute("UPDATE mistakes SET message = ? WHERE id = ?", (message, mistake_id))

    def forget_from(self, game_id: int, ply: int) -> None:
        """A takeback: mistakes from `ply` on never happened."""
        with self.lock, self.db:
            self.db.execute("DELETE FROM mistakes WHERE game_id = ? AND ply >= ?", (game_id, ply))

    def recurrence(self, game_id: int, motif: str) -> Recurrence:
        with self.lock:
            ids = [r[0] for r in self.db.execute("SELECT id FROM games WHERE id <= ? ORDER BY id DESC", (game_id,))]
            having = {r[0] for r in self.db.execute("SELECT DISTINCT game_id FROM motifs WHERE motif = ?", (motif,))}
        streak = 0
        for gid in ids:
            if gid not in having:
                break
            streak += 1
        return Recurrence(motif, streak, len(having & set(ids)), len(ids))

    def most_recurring(self, game_id: int, motifs: list[str]) -> Recurrence | None:
        """The most telling recurrence among `motifs` (longest streak, then most games)."""
        found = [self.recurrence(game_id, m) for m in dict.fromkeys(motifs)]
        found = [r for r in found if r.sentence()]
        return max(found, key=lambda r: (r.streak, r.games_with), default=None)

    def top_weakness(self, last_games: int = 10) -> Recurrence | None:
        """The motif behind mistakes in the most of the last `last_games` games."""
        with self.lock:
            ids = [r[0] for r in self.db.execute("SELECT id FROM games ORDER BY id DESC LIMIT ?", (last_games,))]
            if not ids:
                return None
            marks = ",".join("?" * len(ids))
            row = self.db.execute(
                f"SELECT motif, COUNT(DISTINCT game_id) AS n FROM motifs WHERE game_id IN ({marks})"
                " GROUP BY motif ORDER BY n DESC, motif LIMIT 1",
                ids,
            ).fetchone()
        if row is None or row["n"] < 2:
            return None
        return self.recurrence(ids[0], row["motif"])

    def mistakes(self, game_id: int) -> list[dict]:
        with self.lock:
            rows = self.db.execute("SELECT * FROM mistakes WHERE game_id = ? ORDER BY ply", (game_id,)).fetchall()
            out = []
            for r in rows:
                tags = [t[0] for t in self.db.execute("SELECT motif FROM motifs WHERE mistake_id = ?", (r["id"],))]
                out.append({**dict(r), "motifs": tags})
            return out

    def motif_counts(self, last_games: int = 10) -> list[tuple[str, int, int]]:
        """(motif, games it appeared in, games looked at) over the last games, most frequent first."""
        with self.lock:
            ids = [r[0] for r in self.db.execute("SELECT id FROM games ORDER BY id DESC LIMIT ?", (last_games,))]
            if not ids:
                return []
            marks = ",".join("?" * len(ids))
            rows = self.db.execute(
                f"SELECT motif, COUNT(DISTINCT game_id) FROM motifs WHERE game_id IN ({marks})"
                " GROUP BY motif ORDER BY 2 DESC, motif",
                ids,
            ).fetchall()
        return [(r[0], r[1], len(ids)) for r in rows]

    def close(self) -> None:
        self.db.close()
