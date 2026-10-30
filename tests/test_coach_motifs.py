import chess
import pytest

from coach.engine import Engine
from coach.motifs import label, mistake_motifs, tactic_motifs


def tags(fen: str, san: str, line: list[str] | None = None) -> set[str]:
    board = chess.Board(fen)
    moves = []
    if line:
        b = board.copy()
        for s in line:
            moves.append(b.push_san(s))
    return {m.tag for m in tactic_motifs(board, board.parse_san(san), moves)}


def test_fork():
    assert "knight_fork" in tags("r3k3/8/8/3N4/8/8/8/4K3 w - - 0 1", "Nc7+")


def test_pin():
    assert "pin" in tags("4k3/8/2n5/8/8/8/8/4KB2 w - - 0 1", "Bb5")


def test_skewer():
    assert "skewer" in tags("4q3/8/8/4k3/8/8/8/R5K1 w - - 0 1", "Re1+")


def test_discovered_attack():
    assert "discovered_attack" in tags("7q/8/8/8/3N4/8/1B6/K6k w - - 0 1", "Nf5")


def test_hanging_piece():
    assert "hanging_piece" in tags("4k3/8/8/3n4/8/8/8/3QK3 w - - 0 1", "Qxd5")


def test_back_rank_mate():
    assert "back_rank_mate" in tags("6k1/5ppp/8/8/8/8/8/R5K1 w - - 0 1", "Ra8#", ["Ra8#"])


def test_quiet_move_has_no_motif():
    assert tags(chess.STARTING_FEN, "e4") == set()


def test_labels():
    assert label("knight_fork") == "knight forks"
    assert label("missed_mate") == "missed mates"
    assert label("hanging_piece") == "hanging pieces"


@pytest.fixture(scope="module")
def engine():
    try:
        e = Engine(time_s=0.2)
    except FileNotFoundError:
        pytest.skip("Stockfish not installed")
    yield e
    e.close()


def test_blunder_into_a_knight_fork(engine):
    board = chess.Board("4k3/8/8/8/3n4/8/7P/R3K3 w Q - 0 1")
    played = board.parse_san("h3")  # ignores ...Nc2+, which forks the king and the rook
    before = engine.analyse(board)
    after_board = board.copy()
    after_board.push(played)
    after = engine.analyse(after_board)
    found = {m.tag for m in mistake_motifs(board, played, before, after)}
    assert before.cp(chess.WHITE) - after.cp(chess.WHITE) > 300
    assert "knight_fork" in found, found
