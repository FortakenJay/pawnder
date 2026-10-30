import random

import chess

from coach.game import GameState
from coach.replay import misread


def fen_after(*sans: str) -> str:
    b = chess.Board()
    for san in sans:
        b.push_san(san)
    return b.fen()


def test_infers_the_move_from_a_fen():
    g = GameState()
    u = g.apply_fen(fen_after("e4"))
    assert (u.kind, u.san) == ("move", "e4")
    assert g.apply_fen(fen_after("e4", "e5")).san == "e5"


def test_placement_only_side_to_move_and_castling_ignored():
    g = GameState()
    placement = fen_after("Nf3").split()[0]
    assert g.apply_fen(f"{placement} w KQkq - 0 1").san == "Nf3"  # wrong side to move from vision


def test_duplicate_read_is_same():
    g = GameState()
    g.apply_fen(fen_after("e4"))
    assert g.apply_fen(fen_after("e4")).kind == "same"


def test_bad_read_is_rejected_and_game_carries_on():
    g = GameState()
    g.apply_fen(fen_after("e4"))
    assert g.apply_fen(fen_after("e4", "e5", "Nf3")).kind == "rejected"  # two moves at once
    assert g.apply_fen("garbage").kind == "rejected"
    assert g.rejected == 2
    assert g.apply_fen(fen_after("e4", "e5")).san == "e5"


def test_takeback():
    g = GameState()
    g.apply_fen(fen_after("e4"))
    g.apply_fen(fen_after("e4", "e5"))
    u = g.apply_fen(fen_after("e4"))
    assert (u.kind, u.san) == ("takeback", "e5")
    assert len(g.board.move_stack) == 1


def test_castling_and_promotion():
    g = GameState("4k3/1P6/8/8/8/8/8/R3K2R w KQ - 0 1")
    b = g.board.copy()
    b.push_san("b8=N")
    assert g.apply_fen(b.fen()).san == "b8=N"
    b.push_san("Kf7")
    g.apply_fen(b.fen())
    b.push_san("O-O+")
    assert g.apply_fen(b.fen()).san == "O-O+"


def test_misread_is_never_a_legal_move():
    rng = random.Random(1)
    g = GameState()
    for _ in range(20):
        assert g.apply_fen(misread(chess.Board(), rng)).kind == "rejected"


def test_takeback_and_a_different_move_in_one_read():
    g = GameState()
    g.apply_fen(fen_after("e4"))
    g.apply_fen(fen_after("e4", "e5"))
    u = g.apply_fen(fen_after("e4", "c5"))  # vision undid e5 and saw c5; it only reports the result
    assert (u.kind, u.san, u.replaced) == ("move", "c5", chess.Move.from_uci("e7e5"))
    assert [m.uci() for m in g.board.move_stack] == ["e2e4", "c7c5"]
