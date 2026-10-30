"""NVIDIA Nemotron on Nebius Token Factory (OpenAI-compatible chat completions).

Two uses: a fast model writes one coaching sentence during the game, Nemotron Ultra writes the
post-game review. Both only ever see facts computed by Stockfish and are told not to add analysis
of their own. Base URL, key and model IDs come from .env.
"""

from __future__ import annotations

import logging
import re
import time

import requests

from .config import Settings

logger = logging.getLogger(__name__)

_THINK = re.compile(r"<think>.*?(</think>|$)", re.DOTALL)


def clean(text: str) -> str:
    """Drop reasoning blocks and wrapping quotes that some models put around the answer."""
    text = _THINK.sub("", text or "").strip()
    if len(text) > 1 and text[0] == text[-1] and text[0] in "\"'":
        text = text[1:-1].strip()
    return text


class Nemotron:
    def __init__(self, settings: Settings) -> None:
        self.s = settings
        self.http = requests.Session()
        self.http.headers["Authorization"] = f"Bearer {settings.api_key}"
        self._thinking_switch = True  # send enable_thinking=False; dropped if the API rejects the field
        self.last_latency_s: float | None = None

    def chat(
        self,
        model: str,
        system: str,
        user: str,
        max_tokens: int = 200,
        temperature: float = 0.5,
        timeout_s: float = 20,
        think: bool = False,
    ) -> str:
        body: dict = {
            "model": model,
            "messages": [{"role": "system", "content": system}, {"role": "user", "content": user}],
            "max_tokens": max_tokens,
            "temperature": temperature,
        }
        if self._thinking_switch:
            body["chat_template_kwargs"] = {"enable_thinking": think}
        start = time.monotonic()
        r = self.http.post(f"{self.s.base_url}/chat/completions", json=body, timeout=timeout_s)
        if r.status_code in (400, 422) and "chat_template_kwargs" in body:
            logger.info("Token Factory rejected chat_template_kwargs (%s); retrying without it", r.status_code)
            self._thinking_switch = False
            del body["chat_template_kwargs"]
            r = self.http.post(f"{self.s.base_url}/chat/completions", json=body, timeout=timeout_s)
        r.raise_for_status()
        self.last_latency_s = time.monotonic() - start
        message = r.json()["choices"][0]["message"]
        text = clean(message.get("content") or "")
        if not text:
            raise ValueError("empty answer (the token budget may have gone to reasoning)")
        logger.info("%s answered in %.1fs", model, self.last_latency_s)
        return text

    def models(self) -> list[str]:
        r = self.http.get(f"{self.s.base_url}/models", timeout=15)
        r.raise_for_status()
        return sorted(m["id"] for m in r.json().get("data", []))
