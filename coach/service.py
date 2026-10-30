"""Ties it together: positions in, coach messages, memory and a rendered screen out.

`position()` only checks the FEN and returns at once (the vision loop must never wait). Engine
analysis, the coach's decision and the Nemotron call run in order on one worker thread.
"""

from __future__ import annotations

import logging
import queue
import threading
import time
from collections.abc import Callable
from dataclasses import asdict, dataclass
from typing import Any

import chess
import chess.pgn

from .coach import Coach, Comment, side
from .config import Settings
from .display import Screen, render, to_bmp, to_png, white_share
from .engine import Engine, Eval
from .game import GameState
from .llm import Nemotron
from .memory import Memory

logger = logging.getLogger(__name__)


def opening_name(board: chess.Board) -> str:
    """'C50 Italian Game' from chessreader's opening book, or '' if unknown."""
    try:
        from chessreader.openings import default_book

        info = default_book().lookup(board)
    except Exception:  # the book is a nicety; never let it break coaching
        return ""
    return f"{info.eco or ''} {info.name or ''}".strip()


@dataclass
class FeedItem:
    ply: int
    label: str  # the move it is about, "14. Qd3"
    kind: str  # "mistake", "threat", "info", "review"
    text: str
    severity: str = ""
    motifs: tuple[str, ...] = ()
    source: str = ""  # "nemotron", "template" or "system"
    facts: str = ""
    at: float = 0.0


class CoachService:
    def __init__(self, settings: Settings, engine: Engine, llm: Nemotron | None, memory: Memory) -> None:
        self.s = settings
        self.engine = engine
        self.llm = llm
        self.memory = memory
        self.coach = Coach(settings, engine, llm)
        self.on_game_end: list[Callable[[int, chess.pgn.Game], None]] = []  # e.g. the post-game review
        self.lock = threading.RLock()
        self.jobs: queue.Queue[tuple[str, int, Any]] = queue.Queue()

        self.game = GameState()
        self.game_id = 0
        self.player = settings.player
        self.white, self.black = "White", "Black"
        self.result: str | None = None
        self.feed: list[FeedItem] = []
        self.evals: list[int] = []  # White's clamped eval after each analysed ply
        self.last_eval: Eval | None = None
        self.opening = ""
        self.rejected = 0
        self.last_rejection = ""
        self.review: str | None = None
        self.version = 0  # bumps whenever anything in the web UI changes
        self.screen_version = 1  # bumps only when the e-paper image changes
        self._screen = Screen()
        self._bmp = to_bmp(render(self._screen))

        self._worker = threading.Thread(target=self._run, name="coach", daemon=True)
        self._worker.start()

    # ---------------------------------------------------------------- inputs (fast)

    def new_game(self, white: str = "White", black: str = "Black", player: str | None = None, fen: str | None = None):
        with self.lock:
            if self.game_id and self.result is None and self.game.board.move_stack:
                self.memory.finish_game(self.game_id, "*", str(self.pgn()))  # abandoned
            self.game = GameState(fen or chess.STARTING_FEN)
            self.white, self.black = white or "White", black or "Black"
            self.player = (player or self.s.player).lower()
            self.result, self.review = None, None
            self.game_id = self.memory.start_game(self.white, self.black, self.player)
            self.feed, self.evals, self.last_eval, self.opening = [], [], None, ""
            self.rejected, self.last_rejection = 0, ""
            self._post(0, "", "info", f"New game: {self.white} vs {self.black}. Coaching {self.player}.", "system")
            logger.info("Game %d: %s vs %s, coaching %s", self.game_id, self.white, self.black, self.player)
            self.jobs.put(("start", self.game_id, self.game.board.copy()))
            return {"game_id": self.game_id}

    def position(self, fen: str) -> dict:
        with self.lock:
            if not self.game_id:
                self.new_game()
            if self.result is not None:
                return {"status": "rejected", "reason": "game is over: POST /game/new"}
            update = self.game.apply_fen(fen)
            if update.kind == "rejected":
                self.rejected += 1
                self.last_rejection = update.reason
                logger.info("Ignored a bad read (%s): %s", update.reason, fen)
                self.version += 1  # the web page shows the count of ignored reads
            elif update.kind == "move":
                before = self.game.board.copy()
                before.pop()
                if update.replaced is not None:  # the previous move was taken back first
                    self.jobs.put(("takeback", self.game_id, len(before.move_stack) + 1))
                self.jobs.put(("move", self.game_id, (before, update.move)))
                if self.game.board.is_game_over():
                    self.end_game(self.game.board.result())
            elif update.kind == "takeback":
                self.jobs.put(("takeback", self.game_id, len(self.game.board.move_stack) + 1))
            return update.as_dict()

    def end_game(self, result: str = "*") -> dict:
        with self.lock:
            if not self.game_id or self.result is not None:
                return {"status": "ignored"}
            self.result = result
            self.jobs.put(("end", self.game_id, (result, self.pgn())))
            logger.info("Game %d over: %s", self.game_id, result)
            self._refresh()
            return {"status": "ended", "result": result}

    # ---------------------------------------------------------------- outputs

    def bmp(self) -> bytes:
        with self.lock:
            return self._bmp

    def png(self) -> bytes:
        with self.lock:
            return to_png(render(self._screen))

    def pgn(self) -> chess.pgn.Game:
        game = chess.pgn.Game.from_board(self.game.board)
        game.headers.update({"White": self.white, "Black": self.black, "Result": self.result or "*"})
        game.headers["Event"] = "Over-the-board game"
        return game

    def state(self) -> dict:
        with self.lock:
            board = self.game.board
            weakness = self.memory.top_weakness()
            return {
                "version": self.version,
                "screen_version": self.screen_version,
                "game_id": self.game_id,
                "white": self.white,
                "black": self.black,
                "player": self.player,
                "fen": board.fen(),
                "last_move": board.peek().uci() if board.move_stack else None,
                "turn": side(board.turn),
                "ply": len(board.move_stack),
                "result": self.result,
                "opening": self.opening,
                "eval": None
                if self.last_eval is None
                else {"text": self.last_eval.text, "white_cp": self.last_eval.white_cp, "mate": self.last_eval.mate},
                "evals": self.evals,
                "feed": [asdict(f) for f in self.feed[-30:]],
                "weakness": weakness.sentence() if weakness else None,
                "rejected": self.rejected,
                "last_rejection": self.last_rejection,
                "review": self.review,
                "screen": asdict(self._screen),
                "llm": {
                    "enabled": self.llm is not None,
                    "fast_model": self.s.fast_model,
                    "review_model": self.s.review_model,
                },
            }

    # ---------------------------------------------------------------- worker

    def _run(self) -> None:
        while True:
            kind, game_id, payload = self.jobs.get()
            if game_id != self.game_id and kind != "end":
                continue  # left over from a previous game
            try:
                if kind == "end":
                    self._finish(game_id, *payload)
                elif kind == "move":
                    self._analyse_move(*payload)
                elif kind == "takeback":
                    self._takeback(payload)
                elif kind == "start":
                    self.last_eval = self.engine.analyse(payload)
                    self._refresh()
            except Exception:
                logger.exception("Coach job %s failed", kind)

    def _analyse_move(self, before: chess.Board, move: chess.Move) -> None:
        report = self.coach.judge(before, move)
        after = before.copy()
        after.push(move)
        comment: Comment | None = None
        mistake_id = None
        if self.coach.coaches(report.color, self.player) and report.severity:
            tags = [m.tag for m in report.motifs]
            mistake_id = self.memory.add_mistake(
                self.game_id,
                {
                    "ply": report.ply,
                    "color": side(report.color).lower(),
                    "move": report.san,
                    "best": report.best_san,
                    "refutation": report.refutation,
                    "cp_loss": report.cp_loss,
                    "severity": report.severity,
                    "fen_before": report.fen_before,
                    "message": "",
                },
                tags,
            )
            rec = self.memory.most_recurring(self.game_id, tags)
            comment = self.coach.on_mistake(report, rec.sentence() if rec else None)
            self.memory.set_message(mistake_id, comment.text)
        elif self.coach.coaches(after.turn, self.player) and not after.is_game_over():
            threat = self.coach.threat(after)
            if threat is not None:
                comment = self.coach.on_threat(threat, after.turn)
        with self.lock:
            if len(self.game.board.move_stack) < report.ply:
                return  # taken back while we were thinking
            self.last_eval = report.after
            self.evals = [*self.evals[: report.ply - 1], max(-1000, min(1000, report.after.white_cp))]
            self.opening = opening_name(after) or self.opening
            if comment is not None:
                self._post(
                    report.ply,
                    report.label,
                    comment.kind,
                    comment.text,
                    comment.source,
                    comment.severity,
                    comment.motifs,
                    comment.facts,
                )
            self._refresh()

    def _takeback(self, ply: int) -> None:
        self.memory.forget_from(self.game_id, ply)
        with self.lock:
            self.feed = [f for f in self.feed if f.ply < ply]
            self.evals = self.evals[: ply - 1]
            self.last_eval = self.engine.analyse(self.game.board)
            self._post(ply, "", "info", "Move taken back.", "system")
            self._refresh()

    def _finish(self, game_id: int, result: str, game: chess.pgn.Game) -> None:
        """Runs after every move of the game has been analysed, so hooks see all its mistakes."""
        self.memory.finish_game(game_id, result, str(game))
        n = len(self.memory.mistakes(game_id))
        summary = f"Game over: {result}. " + (
            "No big mistakes from you: well played." if n == 0 else f"{n} mistake{'s' * (n != 1)} to look at."
        )
        with self.lock:
            if game_id == self.game_id:
                self._post(len(self.game.board.move_stack), "", "info", summary, "system")
        for hook in self.on_game_end:
            threading.Thread(target=hook, args=(game_id, game), daemon=True).start()

    # ---------------------------------------------------------------- screen

    def _post(self, ply: int, label: str, kind: str, text: str, source: str, severity: str = "", motifs=(), facts=""):
        self.feed.append(FeedItem(ply, label, kind, text, severity, tuple(motifs), source, facts, time.time()))
        self._refresh()

    def set_review(self, game_id: int, text: str) -> None:
        with self.lock:
            if game_id == self.game_id:
                self.review = text
                self._post(len(self.game.board.move_stack), "", "review", text, "nemotron")

    def _refresh(self) -> None:
        """Rebuild the e-paper screen; bump the version only if a pixel changed."""
        board = self.game.board
        ev = self.last_eval
        latest = next((f for f in reversed(self.feed) if f.kind != "review"), None)
        if self.result is not None:
            status = f"Game over  {self.result}"
        elif board.move_stack:
            status = f"Move {board.fullmove_number}  {side(board.turn)} to move"
        else:
            status = "Ready"
        heading, message, kind = "", Screen.message, "info"
        if latest is not None:
            kind, message = latest.kind, latest.text
            if latest.kind == "mistake":
                heading = f"{latest.severity.upper()}  {latest.label}"
            elif latest.kind == "threat":
                heading = f"THREAT  after {latest.label}"
        weakness = self.memory.top_weakness()
        last_move = ""
        if board.move_stack:
            b = board.copy()
            m = b.pop()
            last_move = f"{b.fullmove_number}{'.' if b.turn == chess.WHITE else '...'} {b.san(m)}"
        self._screen = Screen(
            status=status,
            last_move=last_move,
            opening=self.opening,
            eval_text=ev.text if ev else "",
            white_share=white_share(ev.white_cp, ev.mate) if ev else 0.5,
            player_is_black=self.player == "black",
            kind=kind,
            heading=heading,
            message=message,
            weakness=(weakness.sentence() or "") if weakness else "",
        )
        bmp = to_bmp(render(self._screen))
        if bmp != self._bmp:
            self._bmp = bmp
            self.screen_version += 1
        self.version += 1
