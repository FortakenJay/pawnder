"""Command line.

python -m coach serve                        # server on :8000 (vision posts here; ESP32 polls here)
python -m coach serve --replay coach/data/demo1_fried_liver.pgn   # + feed a PGN in-process
python -m coach replay game.pgn --url http://localhost:8000   # feed a running server over HTTP
python -m coach models                       # Nemotron models your Token Factory key can use
python -m coach ping                         # one live-comment call, to check key + model
"""

from __future__ import annotations

import argparse
import logging
import socket
import threading
from pathlib import Path

from .config import Settings
from .engine import Engine
from .llm import Nemotron
from .memory import Memory
from .replay import HttpFeed, replay
from .service import CoachService

logger = logging.getLogger("coach")


def build_service(settings: Settings) -> CoachService:
    engine = Engine(settings.stockfish_path, settings.engine_time_s)
    llm = Nemotron(settings) if settings.llm_enabled else None
    if llm is None:
        logger.warning("NEBIUS_API_KEY / NEBIUS_BASE_URL / NEMOTRON_FAST_MODEL not set: template comments only")
    return CoachService(settings, engine, llm, Memory(settings.db_path))


def lan_ip() -> str:
    """This laptop's address on the network it uses (the hotspot); nothing is sent."""
    with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as s:
        try:
            s.connect(("192.0.2.1", 80))  # TEST-NET address: picks the route, sends no packet
            return s.getsockname()[0]
        except OSError:
            return "127.0.0.1"


def cmd_serve(args: argparse.Namespace, settings: Settings) -> int:
    import uvicorn

    from .server import create_app

    service = build_service(settings)
    if args.replay:
        threading.Thread(
            target=replay,
            args=(Path(args.replay), service, args.interval, args.noise, args.player),
            name="replay",
            daemon=True,
        ).start()
    app = create_app(service, on_shutdown=service.engine.close)
    logger.info("Web page: http://localhost:%d", args.port)
    logger.info('E-paper config.h: COACH_HOST "%s" (or "%s"), COACH_PORT %d', lan_ip(), socket.gethostname(), args.port)
    uvicorn.run(app, host=args.host, port=args.port, log_level="warning")
    return 0


def cmd_replay(args: argparse.Namespace, _settings: Settings) -> int:
    replay(Path(args.pgn), HttpFeed(args.url), args.interval, args.noise, args.player)
    return 0


def cmd_models(_args: argparse.Namespace, settings: Settings) -> int:
    if not settings.api_key or not settings.base_url:
        print("Set NEBIUS_API_KEY and NEBIUS_BASE_URL in .env first")
        return 1
    ids = Nemotron(settings).models()
    nemotron = [m for m in ids if "nemotron" in m.lower()]
    print("\n".join(nemotron or ids))
    return 0


def cmd_ping(_args: argparse.Namespace, settings: Settings) -> int:
    if not settings.llm_enabled:
        print("Set NEBIUS_API_KEY, NEBIUS_BASE_URL and NEMOTRON_FAST_MODEL in .env first")
        return 1
    from .coach import LIVE_SYSTEM

    llm = Nemotron(settings)
    facts = (
        "Situation: the player (White) just made a blunder.\nMove played: 1. h3\n"
        "Engine's best move instead: Kf2\nOpponent's strongest reply now: Nc2+ Ke2 Nxa1\n"
        "Tactic on the board: Nc2+: the knight attacks the rook on a1 and the king on e1 at once"
    )
    for model in (settings.fast_model, settings.review_model):
        if model:
            text = llm.chat(model, LIVE_SYSTEM, facts, max_tokens=400)
            print(f"{model} ({llm.last_latency_s:.1f}s): {text}")
    return 0


def main(argv: list[str] | None = None) -> int:
    logging.basicConfig(
        level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s", datefmt="%H:%M:%S"
    )
    p = argparse.ArgumentParser(prog="python -m coach", description="Pawnder, an over-the-board chess coach")
    sub = p.add_subparsers(dest="cmd", required=True)

    s = sub.add_parser("serve", help="run the coach server")
    s.add_argument("--host", default="0.0.0.0", help="0.0.0.0 so the ESP32 can reach it on the hotspot")
    s.add_argument("--port", type=int, default=8000)
    s.add_argument("--replay", help="also feed this PGN in-process")

    r = sub.add_parser("replay", help="feed a PGN to a running server over HTTP")
    r.add_argument("pgn")
    r.add_argument("--url", default="http://localhost:8000")

    for q in (s, r):
        q.add_argument("--interval", type=float, default=4.0, help="seconds between moves")
        q.add_argument("--noise", type=float, default=0.0, help="chance of a misread before each move")
        q.add_argument("--player", choices=("white", "black", "both"), help="side to coach (default: .env)")

    sub.add_parser("models", help="list Nemotron models on Token Factory")
    sub.add_parser("ping", help="test the Nemotron key and models")

    args = p.parse_args(argv)
    commands = {"serve": cmd_serve, "replay": cmd_replay, "models": cmd_models, "ping": cmd_ping}
    return commands[args.cmd](args, Settings.load())


if __name__ == "__main__":
    raise SystemExit(main())
