"""The e-paper screen: the server draws all of it, the ESP32 only copies pixels.

800x480, 1 bit. Eval bar on the left (the coached player's side at the bottom), last move, the
coach's latest message, and the recurring weakness from memory along the bottom.
"""

from __future__ import annotations

import io
import math
from dataclasses import dataclass
from functools import cache
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont

WIDTH, HEIGHT = 800, 480
BLACK, WHITE = 0, 1

_FONTS = {
    True: (  # bold
        "/System/Library/Fonts/Supplemental/Arial Bold.ttf",
        "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf",
        "C:/Windows/Fonts/arialbd.ttf",
    ),
    False: (
        "/System/Library/Fonts/Supplemental/Arial.ttf",
        "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",
        "C:/Windows/Fonts/arial.ttf",
    ),
}


@cache
def font(size: int, bold: bool = False) -> ImageFont.FreeTypeFont | ImageFont.ImageFont:
    for path in _FONTS[bold]:
        if Path(path).exists():
            return ImageFont.truetype(path, size)
    return ImageFont.load_default(size)


@dataclass
class Screen:
    title: str = "PAWNDER"
    status: str = "Waiting for a game"
    last_move: str = ""
    opening: str = ""
    eval_text: str = ""
    white_share: float = 0.5  # 0..1, White's expected score
    player_is_black: bool = False
    kind: str = "info"  # "mistake", "threat" or "info"
    heading: str = ""  # e.g. "BLUNDER  on 14. Qd3"
    message: str = "Set up the pieces. I'll speak up when it matters."
    weakness: str = ""


def white_share(white_cp: int, mate: int | None) -> float:
    """Expected score for White from the engine eval (the usual logistic curve)."""
    if mate is not None:
        return 1.0 if mate > 0 else 0.0 if mate < 0 else (1.0 if white_cp > 0 else 0.0)
    return 1 / (1 + math.exp(-0.00368208 * white_cp))


def wrap(draw: ImageDraw.ImageDraw, text: str, f, width: int) -> list[str]:
    lines: list[str] = []
    for paragraph in text.split("\n"):
        line = ""
        for word in paragraph.split():
            trial = f"{line} {word}".strip()
            if draw.textlength(trial, font=f) <= width or not line:
                line = trial
            else:
                lines.append(line)
                line = word
        lines.append(line)
    return lines


def fit(draw: ImageDraw.ImageDraw, text: str, f, width: int) -> str:
    """`text`, cut with an ellipsis so it fits in `width` pixels."""
    if draw.textlength(text, font=f) <= width:
        return text
    while text and draw.textlength(text + "…", font=f) > width:
        text = text[:-1]
    return text.rstrip() + "…"


def _eval_bar(draw: ImageDraw.ImageDraw, s: Screen, box: tuple[int, int, int, int]) -> None:
    x0, y0, x1, y1 = box
    height = y1 - y0
    black_share = 1 - s.white_share
    # the player's colour sits at the bottom of the bar
    if s.player_is_black:
        split = y0 + round(height * s.white_share)
        draw.rectangle((x0, y0, x1, split), fill=WHITE)
        draw.rectangle((x0, split, x1, y1), fill=BLACK)
    else:
        split = y0 + round(height * black_share)
        draw.rectangle((x0, y0, x1, split), fill=BLACK)
        draw.rectangle((x0, split, x1, y1), fill=WHITE)
    draw.rectangle(box, outline=BLACK, width=3)
    mid = y0 + height // 2
    draw.line((x0 - 6, mid, x0, mid), fill=BLACK, width=2)
    draw.line((x1, mid, x1 + 6, mid), fill=BLACK, width=2)


def render(s: Screen) -> Image.Image:
    img = Image.new("1", (WIDTH, HEIGHT), WHITE)
    d = ImageDraw.Draw(img)

    # header
    d.rectangle((0, 0, WIDTH, 52), fill=BLACK)
    d.text((18, 26), s.title, font=font(28, True), fill=WHITE, anchor="lm")
    d.text((WIDTH - 18, 26), s.status, font=font(24), fill=WHITE, anchor="rm")

    # eval bar + number
    _eval_bar(d, s, (24, 72, 84, 392))
    d.text((54, 418), s.eval_text or "-", font=font(26, True), fill=BLACK, anchor="mm")

    # last move and opening
    x, right = 112, WIDTH - 24
    d.text((x, 70), "LAST MOVE", font=font(18, True), fill=BLACK)
    d.text((x, 92), s.last_move or "-", font=font(46, True), fill=BLACK)
    if s.opening:
        d.text((x, 148), fit(d, s.opening, font(20), right - x), font=font(20), fill=BLACK)
    d.line((x, 180, right, 180), fill=BLACK, width=2)

    # the coach's message
    y = 194
    if s.heading:
        f = font(22, True)
        w = int(d.textlength(s.heading, font=f))
        if s.kind in ("mistake", "threat"):
            d.rectangle((x, y, x + w + 20, y + 34), fill=BLACK)
            d.text((x + 10, y + 17), s.heading, font=f, fill=WHITE, anchor="lm")
        else:
            d.text((x, y + 17), s.heading, font=f, fill=BLACK, anchor="lm")
        y += 48
    for size in (34, 30, 26, 22):
        f = font(size)
        lines = wrap(d, s.message, f, right - x)
        if len(lines) * (size + 8) <= 424 - y:
            break
    for line in lines:
        d.text((x, y), line, font=f, fill=BLACK)
        y += size + 8

    # recurring weakness from memory
    d.line((0, 436, WIDTH, 436), fill=BLACK, width=3)
    footer = f"Recurring: {s.weakness}" if s.weakness else "No recurring weakness yet"
    d.text((18, 458), fit(d, footer, font(22, True), WIDTH - 36), font=font(22, True), fill=BLACK, anchor="lm")
    return img


def to_bmp(img: Image.Image) -> bytes:
    """1-bit BMP (62-byte header, bottom-up rows of 100 bytes; palette 0 = black, 1 = white)."""
    buf = io.BytesIO()
    img.convert("1").save(buf, format="BMP")
    return buf.getvalue()


def to_png(img: Image.Image) -> bytes:
    buf = io.BytesIO()
    img.save(buf, format="PNG")
    return buf.getvalue()
