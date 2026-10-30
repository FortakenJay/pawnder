import chess
import pytest

from coach.coach import Coach
from coach.config import Settings
from coach.engine import Engine


@pytest.fixture(scope="module")
def coach():
    try:
        engine = Engine(time_s=0.2)
    except FileNotFoundError:
        pytest.skip("Stockfish not installed")
    yield Coach(Settings.load(), engine, None)
    engine.close()


def test_blunder_is_called_out_with_its_motif(coach):
    board = chess.Board("4k3/8/8/8/3n4/8/7P/R3K3 w Q - 0 1")
    report = coach.judge(board, board.parse_san("h3"))
    assert report.severity == "blunder"
    assert "knight_fork" in [m.tag for m in report.motifs]
    comment = coach.on_mistake(report, "3rd game in a row with knight forks")
    assert comment.source == "template"
    assert "Nc2+" in comment.text and "3rd game in a row" in comment.text


def test_normal_move_is_silent(coach):
    board = chess.Board()
    assert coach.judge(board, board.parse_san("e4")).severity == ""
    board.push_san("e4")
    board.push_san("e5")
    assert coach.threat(board) is None


def test_fork_threat_is_announced(coach):
    board = chess.Board("4k3/8/2n5/8/8/8/7P/R3K3 b Q - 0 1")
    board.push_san("Nd4")
    threat = coach.threat(board)
    assert threat is not None and threat.line.startswith("Nc2+")
    assert "knight_fork" in [m.tag for m in threat.motifs]
