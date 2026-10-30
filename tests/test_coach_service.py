import time

import chess
import pytest
from fastapi.testclient import TestClient

from coach.config import Settings
from coach.engine import Engine
from coach.memory import Memory
from coach.server import create_app
from coach.service import CoachService

FORK = "4k3/8/8/8/3n4/8/7P/R3K3 w Q - 0 1"  # White to move; h3?? allows Nc2+


@pytest.fixture
def service(tmp_path):
    try:
        engine = Engine(time_s=0.2)
    except FileNotFoundError:
        pytest.skip("Stockfish not installed")
    yield CoachService(Settings.load(), engine, None, Memory(tmp_path / "coach.db"))
    engine.close()


def play(service: CoachService, san: str) -> None:
    board = service.game.board.copy()
    board.push_san(san)
    assert service.position(board.fen())["status"] == "move"
    deadline = time.time() + 15
    while len(service.evals) < len(board.move_stack):  # wait for the worker
        assert time.time() < deadline, "analysis took too long"
        time.sleep(0.05)


def kinds(service: CoachService) -> list[str]:
    return [f.kind for f in service.feed]


def test_only_the_coached_side_is_judged_and_the_pick_carries_over(service):
    service.new_game(player="black", fen=FORK)
    play(service, "h3")  # White's blunder, but we're coaching Black
    assert "mistake" not in kinds(service)

    service.set_player("white")
    assert service.feed[-1].text == "Now coaching white."
    row = service.memory.db.execute("SELECT player FROM games WHERE id = ?", (service.game_id,)).fetchone()
    assert row[0] == "white"

    service.new_game(fen=FORK)  # no side given: keeps the one picked last
    assert service.player == "white"
    play(service, "h3")
    assert service.feed[-1].kind == "mistake"
    assert service.game.board.peek() == chess.Move.from_uci("h2h3")


def test_player_endpoint_rejects_unknown_sides(service):
    client = TestClient(create_app(service))
    assert client.post("/player", json={"player": "both"}).json() == {"player": "both"}
    assert client.post("/player", json={"player": "purple"}).status_code == 422
    assert client.get("/api/state").json()["player"] == "both"
