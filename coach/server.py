"""HTTP interface: the vision pipeline posts positions, the e-paper ESP32 and the web page read.

POST /position {"fen": ...}       after each move vision detects
POST /game/new, POST /game/end    start / finish a game
GET  /display.bmp                 the e-paper screen, 1-bit 800x480
GET  /display/version             changes only when that screen does
GET  /                            the coach's web page (board, eval, feed, voice)
"""

from __future__ import annotations

import logging
import threading
from contextlib import asynccontextmanager
from pathlib import Path

import chess
import chess.svg
from fastapi import FastAPI, HTTPException
from fastapi.responses import FileResponse, PlainTextResponse, Response
from pydantic import BaseModel

from .replay import replay
from .service import CoachService

logger = logging.getLogger(__name__)

STATIC = Path(__file__).resolve().parent / "static"
DEMOS = Path(__file__).resolve().parent / "data"


class PositionIn(BaseModel):
    fen: str


class NewGameIn(BaseModel):
    white: str = "White"
    black: str = "Black"
    player: str | None = None  # white, black or both; default from .env
    fen: str | None = None


class EndIn(BaseModel):
    result: str = "*"


class ReplayIn(BaseModel):
    pgn: str  # a file name from GET /api/demos
    interval: float = 4.0
    noise: float = 0.0
    player: str | None = None


class Replayer:
    """At most one in-process replay at a time; starting a new one stops the old."""

    def __init__(self, service: CoachService) -> None:
        self.service = service
        self.stop = threading.Event()
        self.thread: threading.Thread | None = None

    def start(self, path: Path, interval: float, noise: float, player: str | None) -> None:
        self.stop.set()
        if self.thread is not None:
            self.thread.join(timeout=interval + 2)
        self.stop = threading.Event()
        self.thread = threading.Thread(
            target=self._run, args=(path, interval, noise, player, self.stop), name="replay", daemon=True
        )
        self.thread.start()

    def _run(self, path: Path, interval: float, noise: float, player: str | None, stop: threading.Event) -> None:
        try:
            replay(path, self.service, interval, noise, player, stop=stop)
        except Exception:
            logger.exception("Replay of %s failed", path)


def create_app(service: CoachService, on_shutdown=None) -> FastAPI:  # noqa: C901 (flat list of routes)
    @asynccontextmanager
    async def lifespan(_app: FastAPI):
        yield
        if on_shutdown:
            on_shutdown()

    app = FastAPI(title="Pawnder", lifespan=lifespan)
    replayer = Replayer(service)

    @app.post("/position")
    def position(body: PositionIn) -> dict:
        return service.position(body.fen)

    @app.post("/game/new")
    def new_game(body: NewGameIn) -> dict:
        if body.player and body.player.lower() not in ("white", "black", "both"):
            raise HTTPException(422, "player must be white, black or both")
        return service.new_game(body.white, body.black, body.player, body.fen)

    @app.post("/game/end")
    def end_game(body: EndIn) -> dict:
        return service.end_game(body.result)

    @app.get("/api/state")
    def state() -> dict:
        return service.state()

    @app.get("/display.bmp")
    def display_bmp() -> Response:
        return Response(service.bmp(), media_type="image/bmp", headers={"Cache-Control": "no-store"})

    @app.get("/display.png")
    def display_png() -> Response:
        return Response(service.png(), media_type="image/png", headers={"Cache-Control": "no-store"})

    @app.get("/display/version", response_class=PlainTextResponse)
    def display_version() -> str:
        return str(service.screen_version)

    @app.get("/board.svg")
    def board_svg() -> Response:
        with service.lock:
            board = service.game.board.copy()
            flipped = service.player == "black"
        last = board.peek() if board.move_stack else None
        check = board.king(board.turn) if board.is_check() else None
        svg = chess.svg.board(board, lastmove=last, check=check, flipped=flipped, size=520)
        return Response(svg, media_type="image/svg+xml", headers={"Cache-Control": "no-store"})

    @app.get("/api/demos")
    def demos() -> list[str]:
        return sorted(p.name for p in DEMOS.glob("*.pgn"))

    @app.post("/replay")
    def start_replay(body: ReplayIn) -> dict:
        path = DEMOS / Path(body.pgn).name
        if not path.exists():
            raise HTTPException(404, f"no demo game {body.pgn}")
        replayer.start(path, body.interval, body.noise, body.player)
        return {"status": "started", "pgn": path.name}

    @app.get("/")
    def index() -> FileResponse:
        return FileResponse(STATIC / "index.html", headers={"Cache-Control": "no-store"})

    return app
