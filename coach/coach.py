"""The coach: decide whether a move is worth a word, then have Nemotron say it like a human.

The decision is the engine's alone: speak after the coached player's mistake (centipawn loss past a
threshold, or a missed mate), or when the opponent's move sets up a real tactic against them. Every
other move is met with silence. Nemotron only phrases the facts; if it is unreachable, a template
says the same thing.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field

import chess

from .config import Settings
from .engine import Engine, Eval, san_line
from .llm import Nemotron
from .motifs import Motif, mistake_motifs, tactic_motifs

logger = logging.getLogger(__name__)

BLUNDER_CP = 300
SERIOUS_THREATS = ("fork", "pin", "skewer", "discovered_attack", "back_rank_mate")

LIVE_SYSTEM = """You are an over-the-board chess coach speaking quietly to a club player during a real game.
You are given facts computed by the Stockfish engine. Reply with ONE short sentence (at most 25 words),
warm and direct, speaking to the player as "you".
Rules:
- Use only the moves, pieces and squares in the facts. Never invent moves, lines or evaluations.
- Never contradict the engine and never judge a move yourself.
- No centipawns or engine jargon.
- If a recurring pattern is given, mention it briefly.
Reply with the sentence only."""


def side(color: chess.Color) -> str:
    return "White" if color == chess.WHITE else "Black"


def move_label(board: chess.Board, move: chess.Move) -> str:
    """'14. Qd3' or '14... Nc2+' for `move` played on `board`."""
    prefix = f"{board.fullmove_number}." if board.turn == chess.WHITE else f"{board.fullmove_number}..."
    return f"{prefix} {board.san(move)}"


def eval_words(ev: Eval, color: chess.Color) -> str:
    mine, theirs = ev.mate_for(color), ev.mate_for(not color)
    if ev.mate == 0:
        return "checkmate"
    if mine is not None:
        return f"you have mate in {mine}"
    if theirs is not None:
        return f"the opponent has mate in {theirs}"
    return f"{ev.cp(color) / 100:+.1f} pawns for you"


@dataclass
class MoveReport:
    """Engine facts about one move."""

    ply: int
    color: chess.Color
    san: str
    label: str
    fen_before: str
    before: Eval
    after: Eval
    cp_loss: int
    severity: str  # "", "mistake" or "blunder"
    best_san: str | None
    refutation: str | None  # the opponent's best reply, as a short SAN line
    motifs: list[Motif] = field(default_factory=list)


@dataclass
class Threat:
    label: str  # the opponent's move that created it
    line: str  # what happens if it's ignored, SAN
    gain_cp: int
    mate_in: int | None
    motifs: list[Motif]


@dataclass
class Comment:
    kind: str  # "mistake" or "threat"
    text: str
    facts: str  # exactly what Nemotron was given
    motifs: list[str]
    source: str  # "nemotron" or "template"
    severity: str = ""


class Coach:
    def __init__(self, settings: Settings, engine: Engine, llm: Nemotron | None) -> None:
        self.s = settings
        self.engine = engine
        self.llm = llm

    def coaches(self, color: chess.Color, player: str) -> bool:
        return player == "both" or player == side(color).lower()

    # ---------------------------------------------------------------- engine facts

    def judge(self, board: chess.Board, move: chess.Move) -> MoveReport:
        """`board` is the position before `move`."""
        mover = board.turn
        before = self.engine.analyse(board)
        after_board = board.copy()
        after_board.push(move)
        after = self.engine.analyse(after_board)
        loss = max(0, before.clamped(mover) - after.clamped(mover))
        missed_mate = (before.mate_for(mover) or 99) <= 3 and after.mate_for(mover) is None
        severity = ""
        if loss >= BLUNDER_CP or missed_mate:
            severity = "blunder"
        elif loss >= self.s.mistake_cp:
            severity = "mistake"
        report = MoveReport(
            ply=len(board.move_stack) + 1,
            color=mover,
            san=board.san(move),
            label=move_label(board, move),
            fen_before=board.fen(),
            before=before,
            after=after,
            cp_loss=loss,
            severity=severity,
            best_san=board.san(before.best) if before.best and before.best != move else None,
            refutation=" ".join(san_line(after_board, after.pv, 4)) or None,
        )
        if severity:
            report.motifs = mistake_motifs(board, move, before, after)
        return report

    def threat(self, board: chess.Board) -> Threat | None:
        """A serious threat against the side to move, created by the opponent's last move."""
        if not board.move_stack:
            return None
        last = board.pop()
        was_capture, label = board.is_capture(last), move_label(board, last)
        board.push(last)
        if was_capture:
            return None  # after a capture the obvious threat is just "recapture"
        found = self.engine.threat(board)
        if found is None:
            return None
        ev, gain = found
        opponent = not board.turn
        mate_in = ev.mate_for(opponent)
        if ev.best is None or (gain < self.s.threat_cp and mate_in is None):
            return None
        passed = board.copy(stack=False)
        passed.turn, passed.ep_square = opponent, None
        motifs = tactic_motifs(passed, ev.best, ev.pv)
        if mate_in is None and not any(m.tag.endswith(SERIOUS_THREATS) for m in motifs):
            return None  # a plain "your piece is attacked": the player can see that
        return Threat(label, " ".join(san_line(passed, ev.pv, 4)), gain, mate_in, motifs)

    # ---------------------------------------------------------------- words

    def mistake_facts(self, r: MoveReport, recurring: str | None) -> str:
        lines = [
            f"Situation: the player ({side(r.color)}) just made a {r.severity}.",
            f"Move played: {r.label}",
            f"Engine evaluation: before the move {eval_words(r.before, r.color)}; "
            f"after it {eval_words(r.after, r.color)}.",
        ]
        if r.best_san:
            lines.append(f"Engine's best move instead: {r.best_san}")
        if r.refutation:
            lines.append(f"Opponent's strongest reply now: {r.refutation}")
        for m in r.motifs:
            lines.append(f"Tactic on the board: {m.detail}")
        if recurring:
            lines.append(f"Recurring pattern from the player's past games: {recurring}")
        return "\n".join(lines)

    def threat_facts(self, t: Threat, color: chess.Color) -> str:
        lines = [
            f"Situation: the opponent just played {t.label}. It is the player's ({side(color)}) move.",
            f"If the player ignores it, the engine's line for the opponent is: {t.line}",
        ]
        if t.mate_in is not None:
            lines.append(f"That line is mate in {t.mate_in}.")
        else:
            lines.append(f"It would win about {t.gain_cp / 100:.0f} pawns of material.")
        for m in t.motifs:
            lines.append(f"Tactic on the board: {m.detail}")
        lines.append("Warn the player about the danger without telling them their move.")
        return "\n".join(lines)

    def on_mistake(self, r: MoveReport, recurring: str | None) -> Comment:
        facts = self.mistake_facts(r, recurring)
        text, source = self._say(facts, self._mistake_template(r, recurring))
        return Comment("mistake", text, facts, [m.tag for m in r.motifs], source, r.severity)

    def on_threat(self, t: Threat, color: chess.Color) -> Comment:
        facts = self.threat_facts(t, color)
        first = t.line.split()[0] if t.line else "a tactic"
        tactic = f" ({t.motifs[0].tag.replace('_', ' ')})" if t.motifs else ""
        fallback = f"Careful: {'mate is coming' if t.mate_in else 'there is a threat'}, watch out for {first}{tactic}."
        text, source = self._say(facts, fallback)
        return Comment("threat", text, facts, [m.tag for m in t.motifs], source)

    def _mistake_template(self, r: MoveReport, recurring: str | None) -> str:
        parts = [f"{r.severity.capitalize()}: {r.san}"]
        if r.motifs:
            parts.append(
                f"allows {r.motifs[0].detail}" if not r.motifs[0].tag.startswith("missed") else r.motifs[0].detail
            )
        elif r.refutation:
            parts.append(f"allows {r.refutation.split()[0]}")
        text = " ".join(parts) + "."
        if r.best_san:
            text += f" {r.best_san} was the move."
        if recurring:
            text += f" {recurring[0].upper()}{recurring[1:]}."
        return text

    def _say(self, facts: str, fallback: str) -> tuple[str, str]:
        if self.llm is None:
            return fallback, "template"
        try:
            text = self.llm.chat(self.s.fast_model, LIVE_SYSTEM, facts, max_tokens=300, temperature=0.6)
            return text[:280], "nemotron"
        except Exception as exc:
            logger.warning("Nemotron unavailable (%s); using the template", exc)
            return fallback, "template"
