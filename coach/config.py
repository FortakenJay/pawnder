"""Settings from the environment (.env at the repo root). Nothing secret or model-specific is hardcoded."""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

from dotenv import load_dotenv

ROOT = Path(__file__).resolve().parent.parent


@dataclass(frozen=True)
class Settings:
    api_key: str
    base_url: str
    fast_model: str  # live comments: Nemotron Super/Nano
    review_model: str  # post-game review: Nemotron Ultra
    stockfish_path: str
    engine_time_s: float
    player: str  # side being coached: white, black or both
    mistake_cp: int
    threat_cp: int
    db_path: Path
    tavily_key: str

    @property
    def llm_enabled(self) -> bool:
        return bool(self.api_key and self.base_url and self.fast_model)

    @classmethod
    def load(cls) -> Settings:
        load_dotenv(ROOT / ".env")
        env = os.environ.get
        db = Path(env("COACH_DB") or "coach.db")
        return cls(
            api_key=env("NEBIUS_API_KEY", ""),
            base_url=env("NEBIUS_BASE_URL", "").rstrip("/"),
            fast_model=env("NEMOTRON_FAST_MODEL", ""),
            review_model=env("NEMOTRON_REVIEW_MODEL", ""),
            stockfish_path=env("STOCKFISH_PATH", ""),
            engine_time_s=float(env("ENGINE_TIME_S") or 0.4),
            player=(env("COACH_PLAYER") or "white").lower(),
            mistake_cp=int(env("COACH_MISTAKE_CP") or 150),
            threat_cp=int(env("COACH_THREAT_CP") or 250),
            db_path=db if db.is_absolute() else ROOT / db,
            tavily_key=env("TAVILY_API_KEY", ""),
        )
