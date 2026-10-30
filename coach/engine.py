"""Stockfish: the source of truth for every judgement the coach makes.

Evaluations are cached by FEN, so the position after one move is the position before the next and
is only analysed once.
"""

from __future__ import annotations

import logging
import shutil
import threading
from dataclasses import dataclass, field
from pathlib import Path

import chess
import chess.engine

logger = logging.getLogger(__name__)

MATE_CP = 10_000  # a mate in n scores MATE_CP - n
CLAMP_CP = 800  # beyond this the game is decided; losing more doesn't make a mistake bigger

_COMMON_PATHS = (
    "/opt/homebrew/bin/stockfish",
    "/usr/local/bin/stockfish",
    "/usr/games/stockfish",
    "/usr/bin/stockfish",
)


def find_stockfish(configured: str = "") -> str:
    for candidate in (configured, shutil.which("stockfish"), *_COMMON_PATHS):
        if candidate and Path(candidate).exists():
            return candidate
    raise FileNotFoundError("Stockfish not found: install it (brew install stockfish) or set STOCKFISH_PATH")


@dataclass
class Eval:
    white_cp: int  # centipawns for White; mates are ±(MATE_CP - n)
    mate: int | None  # moves to mate, + = White mates; 0 = the side to move is checkmated
    best: chess.Move | None
    pv: list[chess.Move] = field(default_factory=list)

    def cp(self, color: chess.Color) -> int:
        return self.white_cp if color == chess.WHITE else -self.white_cp

    def clamped(self, color: chess.Color) -> int:
        return max(-CLAMP_CP, min(CLAMP_CP, self.cp(color)))

    def mate_for(self, color: chess.Color) -> int | None:
        """Moves until `color` mates, or None."""
        if self.mate is None or self.mate == 0:
            return None
        n = self.mate if color == chess.WHITE else -self.mate
        return n if n > 0 else None

    @property
    def text(self) -> str:
        if self.mate is not None:
            return "#" if self.mate == 0 else (f"M{self.mate}" if self.mate > 0 else f"-M{-self.mate}")
        return f"{self.white_cp / 100:+.1f}"


def san_line(board: chess.Board, moves: list[chess.Move], limit: int = 6) -> list[str]:
    b = board.copy(stack=False)
    out = []
    for move in moves[:limit]:
        if not b.is_legal(move):
            break
        out.append(b.san(move))
        b.push(move)
    return out


class Engine:
    def __init__(self, path: str = "", time_s: float = 0.4) -> None:
        self.path = find_stockfish(path)
        self.limit = chess.engine.Limit(time=time_s)
        self._engine = chess.engine.SimpleEngine.popen_uci(self.path)
        self._engine.configure({"Threads": 2})
        self._lock = threading.Lock()
        self._cache: dict[str, Eval] = {}
        logger.info("Stockfish ready: %s", self._engine.id.get("name", self.path))

    def analyse(self, board: chess.Board) -> Eval:
        key = board.fen()
        if key in self._cache:
            return self._cache[key]
        if board.is_checkmate():
            ev = Eval(-MATE_CP if board.turn == chess.WHITE else MATE_CP, 0, None)
        elif board.is_game_over():
            ev = Eval(0, None, None)
        else:
            with self._lock:
                info = self._engine.analyse(board, self.limit)
            white = info["score"].white()
            pv = info.get("pv", [])
            ev = Eval(white.score(mate_score=MATE_CP) or 0, white.mate(), pv[0] if pv else None, pv)
        if len(self._cache) > 5000:
            self._cache.clear()
        self._cache[key] = ev
        return ev

    def threat(self, board: chess.Board) -> tuple[Eval, int] | None:
        """What the opponent would do if the side to move passed: (their eval, how much they'd gain).

        None when passing isn't meaningful (in check, or the game is over).
        """
        if board.is_check() or board.is_game_over():
            return None
        now = self.analyse(board)
        passed = board.copy(stack=False)  # no move history: UCI has no portable null move
        passed.turn, passed.ep_square = not board.turn, None
        ev = self.analyse(passed)
        opponent = not board.turn
        return ev, ev.clamped(opponent) - now.clamped(opponent)

    def close(self) -> None:
        with self._lock:
            self._engine.quit()
