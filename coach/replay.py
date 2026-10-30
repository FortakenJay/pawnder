"""Replay mode: feed a PGN's positions on a timer, exactly as the vision pipeline would.

The feed goes either straight into the coach (same process) or over HTTP to a running server, so
the demo can be developed and filmed without the camera. `noise` sends a misread before some moves
to show that bad reads are flagged and ignored.
"""

from __future__ import annotations

import logging
import random
import threading
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Protocol

import chess
import chess.pgn
import requests

logger = logging.getLogger(__name__)


class Feed(Protocol):
    def new_game(self, white: str, black: str, player: str | None = None, fen: str | None = None) -> dict: ...
    def position(self, fen: str) -> dict: ...
    def end_game(self, result: str) -> dict: ...


@dataclass
class HttpFeed:
    """Talks to a coach server the way chessreader does."""

    url: str = "http://localhost:8000"

    def _post(self, path: str, body: dict) -> dict:
        r = requests.post(self.url.rstrip("/") + path, json=body, timeout=10)
        r.raise_for_status()
        return r.json()

    def new_game(self, white: str, black: str, player: str | None = None, fen: str | None = None) -> dict:
        return self._post("/game/new", {"white": white, "black": black, "player": player, "fen": fen})

    def position(self, fen: str) -> dict:
        return self._post("/position", {"fen": fen})

    def end_game(self, result: str) -> dict:
        return self._post("/game/end", {"result": result})


def load_game(path: Path) -> chess.pgn.Game:
    with path.open(encoding="utf-8") as f:
        game = chess.pgn.read_game(f)
    if game is None:
        raise ValueError(f"No game in {path}")
    return game


def misread(board: chess.Board, rng: random.Random) -> str:
    """A FEN no legal move explains: one non-king piece jumps to an empty square."""
    for _ in range(50):
        b = board.copy(stack=False)
        pieces = [sq for sq, p in b.piece_map().items() if p.piece_type != chess.KING]
        empty = [sq for sq in chess.SQUARES if b.piece_at(sq) is None]
        src, dst = rng.choice(pieces), rng.choice(empty)
        b.set_piece_at(dst, b.remove_piece_at(src))
        if all(_after(board, m) != b.board_fen() for m in board.legal_moves):
            return b.fen()
    return "8/8/8/8/8/8/8/8 w - - 0 1"


def _after(board: chess.Board, move: chess.Move) -> str:
    board.push(move)
    fen = board.board_fen()
    board.pop()
    return fen


def replay(
    path: Path,
    feed: Feed,
    interval_s: float = 4.0,
    noise: float = 0.0,
    player: str | None = None,
    seed: int | None = None,
    stop: threading.Event | None = None,
) -> None:
    """Send every position of the game in `path`, one every `interval_s` seconds."""
    rng = random.Random(seed)
    game = load_game(path)
    h = game.headers
    board = game.board()
    start = None if board.fen() == chess.STARTING_FEN else board.fen()
    feed.new_game(h.get("White", "White"), h.get("Black", "Black"), player, start)
    logger.info("Replaying %s (%s vs %s)", path.name, h.get("White"), h.get("Black"))
    for move in game.mainline_moves():
        if stop is not None and stop.is_set():
            return
        time.sleep(interval_s)
        if noise and rng.random() < noise:
            bad = feed.position(misread(board, rng))
            logger.info("Sent a misread: %s", bad.get("reason"))
        board.push(move)
        feed.position(board.fen())
    time.sleep(interval_s)
    feed.end_game(h.get("Result", "*"))
