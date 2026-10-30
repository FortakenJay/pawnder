"""The game as the coach sees it: a python-chess board fed by FEN snapshots.

Vision sends a FEN after each move it sees. Only the piece placement is compared (camera
pipelines often get castling rights or the side to move wrong). A FEN is accepted when exactly one
legal move leads to it, when it is the position before the last move (a takeback), or when the last
move was taken back and a different one played (vision reports only the new position). Anything
else is a misread: it is flagged and ignored, and the game carries on from the last good position.
"""

from __future__ import annotations

from dataclasses import dataclass

import chess


@dataclass
class Update:
    kind: str  # "move", "takeback", "same" (duplicate read) or "rejected"
    move: chess.Move | None = None
    san: str | None = None
    reason: str = ""
    replaced: chess.Move | None = None  # a "move" that took the previous move's place

    def as_dict(self) -> dict:
        uci = self.move.uci() if self.move else None
        return {"status": self.kind, "san": self.san, "uci": uci, "reason": self.reason}


def placement(fen: str) -> str:
    """The piece-placement field of a FEN; raises ValueError if it isn't a valid board."""
    field = fen.strip().split()[0] if fen.strip() else ""
    chess.Board(f"{field} w - - 0 1")  # validates the placement
    return field


class GameState:
    def __init__(self, start_fen: str = chess.STARTING_FEN) -> None:
        self.board = chess.Board(start_fen)
        self.rejected = 0

    def apply_fen(self, fen: str) -> Update:
        try:
            target = placement(fen)
        except ValueError:
            return self._reject("not a valid FEN")
        if target == self.board.board_fen():
            return Update("same")
        matches = self._moves_to(target)
        if len(matches) == 1:
            return self.push(matches[0])
        if len(matches) > 1:
            return self._reject("ambiguous")
        if self.board.move_stack:
            last = self.board.pop()
            if self.board.board_fen() == target:
                return Update("takeback", last, self.board.san(last))
            instead = self._moves_to(target)
            if len(instead) == 1:
                update = self.push(instead[0])
                update.replaced = last
                return update
            self.board.push(last)
        return self._reject("no legal move leads there")

    def _moves_to(self, target: str) -> list[chess.Move]:
        matches = []
        for move in self.board.legal_moves:
            self.board.push(move)
            if self.board.board_fen() == target:
                matches.append(move)
            self.board.pop()
        return matches

    def push(self, move: chess.Move) -> Update:
        san = self.board.san(move)
        self.board.push(move)
        return Update("move", move, san)

    def _reject(self, reason: str) -> Update:
        self.rejected += 1
        return Update("rejected", reason=reason)
