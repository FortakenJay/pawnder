"""Send each move the camera reads to the coach server (`python -m coach serve`).

Fire-and-forget on a background thread, in order: the vision loop never waits on the network, and
a coach that is down only costs a log line. Off unless `coach.url` is set in config.yaml.
"""

from __future__ import annotations

import logging
import queue
import threading

import requests

from .session import SessionHooks
from .storage import GameRecord

logger = logging.getLogger(__name__)


class CoachBridge:
    def __init__(self, url: str, player: str = "") -> None:
        self.url = url.rstrip("/")
        self.player = player
        self._queue: queue.Queue[tuple[str, dict]] = queue.Queue()
        self._http = requests.Session()
        self._http.trust_env = False  # the coach is on this machine or the hotspot, never behind a proxy
        self._warned = False
        threading.Thread(target=self._run, name="coach-bridge", daemon=True).start()
        logger.info("Moves go to the coach at %s", self.url)

    def attach(self, hooks: SessionHooks) -> None:
        hooks.on_game_start.append(self._game_start)
        hooks.on_move.append(self._move)
        hooks.on_game_end.append(self._game_end)

    def _game_start(self, record: GameRecord) -> None:
        h = record.game.headers
        body = {"white": h.get("White"), "black": h.get("Black"), "fen": record.starting_fen}
        body["player"] = self.player or None  # None: the side picked on the coach's web page
        self._queue.put(("/game/new", body))

    def _move(self, record: GameRecord) -> None:
        self._queue.put(("/position", {"fen": record.board.fen()}))

    def _game_end(self, record: GameRecord) -> None:
        self._queue.put(("/game/end", {"result": record.game.headers.get("Result", "*")}))

    def _run(self) -> None:
        while True:
            path, body = self._queue.get()
            try:
                r = self._http.post(self.url + path, json=body, timeout=5)
                r.raise_for_status()
                self._warned = False
                if path == "/position" and r.json().get("status") == "rejected":
                    logger.warning("Coach couldn't follow that move: %s", r.json().get("reason"))
            except requests.RequestException as exc:
                if not self._warned:
                    logger.warning("Coach not reachable at %s (%s); still playing without it", self.url, exc)
                    self._warned = True
