"""Name the tactic behind a mistake with simple python-chess heuristics.

The engine says *that* a move was bad and which reply punishes it; these rules say *what kind* of
tactic that reply is (fork, pin, ...), so the coach can name it and memory can count it. They are
deliberately simple: a tag can be missing, but each one is checked on the actual board.
"""

from __future__ import annotations

from dataclasses import dataclass

import chess

from .engine import Eval

VALUE = {chess.PAWN: 1, chess.KNIGHT: 3, chess.BISHOP: 3, chess.ROOK: 5, chess.QUEEN: 9, chess.KING: 100}
SLIDERS = (chess.BISHOP, chess.ROOK, chess.QUEEN)


@dataclass(frozen=True)
class Motif:
    tag: str  # stable name for memory: "knight_fork", "pin", "missed_mate", ...
    detail: str  # what happens on this board, in plain words for the LLM


def _name(board: chess.Board, square: int) -> str:
    piece = board.piece_at(square)
    return f"{chess.piece_name(piece.piece_type)} on {chess.square_name(square)}" if piece else "?"


def _worth_attacking(board: chess.Board, attacker: int, target: int) -> bool:
    """A target that costs material: the king, a bigger piece, or an undefended piece."""
    a, t = board.piece_at(attacker), board.piece_at(target)
    if a is None or t is None:
        return False
    if t.piece_type == chess.KING:
        return True
    if VALUE[t.piece_type] < 3:
        return False
    return VALUE[t.piece_type] > VALUE[a.piece_type] or not board.is_attacked_by(t.color, target)


def _hanging(board: chess.Board, move: chess.Move) -> Motif | None:
    victim = board.piece_at(move.to_square)
    mover = board.piece_at(move.from_square)
    if victim is None or mover is None or VALUE[victim.piece_type] < 3:
        return None
    defended = board.is_attacked_by(victim.color, move.to_square)
    if defended and VALUE[victim.piece_type] <= VALUE[mover.piece_type]:
        return None
    how = "undefended" if not defended else f"worth more than the {chess.piece_name(mover.piece_type)} taking it"
    return Motif("hanging_piece", f"{board.san(move)} wins the {_name(board, move.to_square)} ({how})")


def _fork(board: chess.Board, move: chess.Move, after: chess.Board) -> Motif | None:
    sq = move.to_square
    piece = after.piece_at(sq)
    if piece is None:
        return None
    targets = [
        t for t in after.attacks(sq) if after.color_at(t) == (not piece.color) and _worth_attacking(after, sq, t)
    ]
    if len(targets) < 2:
        return None
    names = " and the ".join(_name(after, t) for t in targets[:3])
    kind = chess.piece_name(piece.piece_type)
    return Motif(f"{kind}_fork", f"{board.san(move)}: the {kind} attacks the {names} at once")


def _pin(board: chess.Board, move: chess.Move, after: chess.Board) -> Motif | None:
    sq = move.to_square
    piece = after.piece_at(sq)
    if piece is None or piece.piece_type not in SLIDERS:
        return None
    enemy = not piece.color
    for target in (
        after.pieces(chess.KNIGHT, enemy)
        | after.pieces(chess.BISHOP, enemy)
        | after.pieces(chess.ROOK, enemy)
        | after.pieces(chess.QUEEN, enemy)
    ):
        if after.is_pinned(enemy, target) and after.pin(enemy, target) & chess.BB_SQUARES[sq]:
            return Motif("pin", f"{board.san(move)} pins the {_name(after, target)} to its king")
    return None


def _skewer(board: chess.Board, move: chess.Move, after: chess.Board) -> Motif | None:
    sq = move.to_square
    piece = after.piece_at(sq)
    if piece is None or piece.piece_type not in SLIDERS:
        return None
    for front in after.attacks(sq):
        x = after.piece_at(front)
        if x is None or x.color == piece.color or VALUE[x.piece_type] < 5:
            continue
        df = (chess.square_file(front) > chess.square_file(sq)) - (chess.square_file(front) < chess.square_file(sq))
        dr = (chess.square_rank(front) > chess.square_rank(sq)) - (chess.square_rank(front) < chess.square_rank(sq))
        f, r = chess.square_file(front) + df, chess.square_rank(front) + dr
        while 0 <= f < 8 and 0 <= r < 8:
            behind = chess.square(f, r)
            y = after.piece_at(behind)
            if y is not None:
                if y.color == x.color and 3 <= VALUE[y.piece_type] < VALUE[x.piece_type]:
                    return Motif(
                        "skewer",
                        f"{board.san(move)} skewers the {_name(after, front)}; the {_name(after, behind)} is behind it",
                    )
                break
            f, r = f + df, r + dr
    return None


def _discovered(board: chess.Board, move: chess.Move, after: chess.Board) -> Motif | None:
    us = board.turn
    for slider in after.pieces(chess.BISHOP, us) | after.pieces(chess.ROOK, us) | after.pieces(chess.QUEEN, us):
        if slider == move.to_square:
            continue
        new = after.attacks(slider) & ~board.attacks(slider) & after.occupied_co[not us]
        for target in new:
            if chess.between(slider, target) & chess.BB_SQUARES[move.from_square] and _worth_attacking(
                after, slider, target
            ):
                return Motif(
                    "discovered_attack",
                    f"{board.san(move)} uncovers the {_name(after, slider)} against the {_name(after, target)}",
                )
    return None


def _back_rank_mate(board: chess.Board, line: list[chess.Move]) -> Motif | None:
    b = board.copy(stack=False)
    for move in line[:7]:
        if not b.is_legal(move):
            return None
        b.push(move)
    if not b.is_checkmate():
        return None
    king = b.king(b.turn)
    last = b.peek()
    piece = b.piece_at(last.to_square)
    back = 0 if b.turn == chess.WHITE else 7
    if king is None or piece is None or piece.piece_type not in (chess.ROOK, chess.QUEEN):
        return None
    if chess.square_rank(king) == back and chess.square_rank(last.to_square) == back:
        return Motif("back_rank_mate", f"mate on the back rank with {board.variation_san(line[: len(b.move_stack)])}")
    return None


def tactic_motifs(board: chess.Board, move: chess.Move, line: list[chess.Move] | None = None) -> list[Motif]:
    """Tactics that `move` (by the side to move on `board`) creates."""
    if not board.is_legal(move):
        return []
    after = board.copy(stack=False)
    after.push(move)
    found = [_fork(board, move, after), _pin(board, move, after), _skewer(board, move, after)]
    found.append(_discovered(board, move, after))
    if board.is_capture(move):
        found.append(_hanging(board, move))
    if line:
        found.append(_back_rank_mate(board, line))
    return [m for m in found if m is not None]


def mistake_motifs(board: chess.Board, played: chess.Move, before: Eval, after: Eval) -> list[Motif]:
    """Why `played` was a mistake: what it allows, and what it missed.

    `board` is the position before the move; `before`/`after` are the engine's evals around it.
    """
    mover = board.turn
    motifs: list[Motif] = []
    played_board = board.copy(stack=False)
    played_board.push(played)

    # what the opponent can do now
    refutation = after.best
    if refutation is not None:
        recapture = board.is_capture(played) and refutation.to_square == played.to_square
        for m in tactic_motifs(played_board, refutation, after.pv):
            if not (recapture and m.tag == "hanging_piece"):
                motifs.append(m)
    if after.mate_for(not mover) is not None and not any(m.tag == "back_rank_mate" for m in motifs):
        line = played_board.variation_san(after.pv[:7]) if after.pv else ""
        motifs.append(Motif("allowed_mate", f"the opponent now has mate in {after.mate_for(not mover)}: {line}"))

    # what the best move would have done instead
    best = before.best
    if best is not None and best != played:
        mate = before.mate_for(mover)
        if mate is not None and mate <= 3 and after.mate_for(mover) is None:
            motifs.append(Motif("missed_mate", f"{board.san(best)} started a mate in {mate}"))
        for m in tactic_motifs(board, best, before.pv):
            if m.tag != "back_rank_mate":
                motifs.append(Motif(f"missed_{m.tag}", f"missed: {m.detail}"))
    return motifs


def label(tag: str) -> str:
    """Plural human name for a tag: 'knight_fork' -> 'knight forks', 'missed_pin' -> 'missed pins'."""
    if tag.startswith("missed_"):
        return "missed " + label(tag[len("missed_") :])
    special = {
        "hanging_piece": "hanging pieces",
        "allowed_mate": "walking into mate",
        "back_rank_mate": "back-rank mates",
    }
    if tag in special:
        return special[tag]
    return tag.replace("_", " ") + ("es" if tag.endswith("h") else "s")
