# Pawnder

**A chess coach that watches your real board.**

Online players get instant analysis after every game. People playing on a real board get nothing.
This project watches a physical chess board through a camera and coaches you **while you play**:
it stays quiet on normal moves and speaks up when it matters ("that's the 3rd game in a row you've
walked into a knight fork"). The coach's words show on an **e-paper display** next to the board and
on a live web page on the laptop, and the page can speak them aloud.

- **Stockfish decides what's right, Nemotron explains why.** LLMs hallucinate chess, so the model
  never judges a move. The engine computes the evaluation, best move, centipawn loss and the
  punishing reply; simple python-chess rules name the tactic (fork, pin, skewer, ...). Nemotron
  only turns those facts into one human sentence, and is told not to invent moves.
- **Fast vs. deep model split.** A fast Nemotron (Super/Nano) writes the live comments in about a
  second; Nemotron Ultra writes the post-game review.
- **Memory across games.** SQLite remembers every mistake and the tactic behind it, so the coach
  can call out recurring weaknesses instead of single blunders.

```
 ESP32 camera ──phone hotspot──▶ chessreader (vision) ──POST /position {fen}──▶ coach server ◀── PGN replay (dev/demo)
                                                                                  │
                         Stockfish (judge) ◀──────────────────────────────────────┤
                         Nemotron on Nebius Token Factory (words) ◀───────────────┤
                         SQLite memory ◀──────────────────────────────────────────┤
                                                                                  ├──▶ GET /display.bmp ──▶ ESP32 e-paper (personal coach)
                                                                                  └──▶ GET /  web page: board, eval, coach feed, voice
```

---

## How NVIDIA Nemotron and Nebius Token Factory are used

All LLM calls go to **Nebius Token Factory** through its OpenAI-compatible API
(`POST {NEBIUS_BASE_URL}/chat/completions`). The base URL, key and model IDs live in `.env`, not in
the code ([coach/llm.py](coach/llm.py)).

| When | Model (`.env`) | What it gets | What it returns |
|---|---|---|---|
| During the game, after a mistake or a serious threat | `NEMOTRON_FAST_MODEL` (Nemotron 3 Super/Nano) | engine facts: move played, eval before/after, best move, the opponent's punishing line, the tactic found on the board, the recurring pattern from memory | one short coaching sentence for the e-paper screen and the voice |
| After the game | `NEMOTRON_REVIEW_MODEL` (Nemotron 3 Ultra) | the whole game + engine annotations + memory of past games | key turning points, the recurring weakness, 3 things to drill *(P1, in progress)* |

Why this split works:

- **The engine is the judge.** The coach decides *whether* to speak from Stockfish alone (centipawn
  loss past `COACH_MISTAKE_CP`, a missed mate, or a null-move threat check that finds a real tactic
  against you). Nemotron is never asked "was this a good move?"
- **Grounded prompts.** The model sees only facts the engine computed, and the system prompt forbids
  new moves or evaluations. The web page shows the exact facts sent with every comment ("engine facts
  sent to Nemotron"), so you can check the model against the engine.
- **Latency and credits.** Live comments use a fast model with reasoning switched off
  (`enable_thinking: false`, dropped automatically if the endpoint rejects it); the slow, deep model
  is called once per game.
- **Never stalls the game.** If Token Factory is unreachable or no key is set, a template says the
  same engine facts, so the board, display and memory keep working.

Check your key and the model IDs:

```bash
.venv/bin/python -m coach models
```

```bash
.venv/bin/python -m coach ping
```

---

## Setup

Python 3.12+, macOS or Linux (Windows works with `.venv\Scripts\python`).

```bash
python3 -m venv .venv
```

```bash
.venv/bin/python -m pip install -r requirements.txt
```

**Stockfish:** `brew install stockfish` (macOS) or `apt install stockfish`, or set `STOCKFISH_PATH` in `.env`.

**Secrets:** copy `.env.example` to `.env` and set `NEBIUS_API_KEY` (from
[tokenfactory.nebius.com](https://tokenfactory.nebius.com)). Without a key the coach still runs, with
template comments.

```bash
cp .env.example .env
```

## Run it without the camera (replay mode)

Replay feeds a PGN's positions on a timer, exactly as the vision pipeline would. Three demo games
are included in which Black falls for a knight fork, so memory builds up a streak.

```bash
.venv/bin/python -m coach serve --player black --replay coach/data/demo2_sicilian.pgn --interval 3
```

Open **http://localhost:8000**: board, eval bar and graph, the coach feed, the e-paper preview and a
**Voice** button. The page also has a **Replay demo** button to start any demo game.

To feed a running server over HTTP instead (this goes through the same `/position` endpoint the
camera uses; `--noise 0.1` injects misreads to show they are ignored):

```bash
.venv/bin/python -m coach replay coach/data/demo3_queens_gambit.pgn --url http://localhost:8000 --interval 2 --noise 0.1
```

## Run it with the camera and the e-paper display

Everything joins **one 2.4 GHz phone hotspot** (iPhone: Personal Hotspot → *Maximize Compatibility*
on). The laptop keeps internet for Token Factory and reaches both ESP32 boards.

1. **Camera:** copy `firmware/CameraWebServer/wifi_config.example.h` to `wifi_config.h`, fill in the
   hotspot, flash it ([docs/hardware.md](docs/hardware.md)). It becomes `http://chesscam.local`
   (`camera.host` in [config.yaml](config.yaml)). If it can't join, it falls back to its old open
   `ChessCam` hotspot at `192.168.4.1`.
2. **Coach server** on the laptop: `.venv/bin/python -m coach serve`. It logs the address the
   display needs (`COACH_HOST "172.20.10.3" (or "my-laptop.local")`).
3. **E-paper display:** see below.
4. **Vision:** `.venv/bin/python run.py play --start`. With `coach.url` set in
   [config.yaml](config.yaml) (default `http://localhost:8000`), every move it reads is posted to the
   coach. `coach.player` picks the side being coached. Vision details: [docs/vision.md](docs/vision.md).

### E-paper display (Waveshare 7.5" V2 + Waveshare ESP32 driver board)

The firmware is deliberately dumb: the server renders the whole 800×480 screen with Pillow (eval bar,
last move, coach message, recurring weakness) and the ESP32 copies pixels.

1. Arduino IDE: install the **esp32** boards package and the **GxEPD2** library (Library Manager).
2. Copy `firmware/EinkCoach/config.example.h` to `config.h`: hotspot name/password, `COACH_HOST`
   (the laptop's IP or `name.local`) and `COACH_PORT`.
3. Board **ESP32 Dev Module**, flash `firmware/EinkCoach/EinkCoach.ino`.

It polls `GET /display/version` every 3 s and only downloads `GET /display.bmp` when the version
changes (partial refresh, a full refresh every 8th update). If the panel stays blank, check the
driver board's A/B switch position for your panel in Waveshare's manual.

---

## Architecture

| Module | Job |
|---|---|
| [coach/game.py](coach/game.py) | Game state from FEN snapshots: the one legal move that explains the new placement, takebacks, misreads flagged and ignored |
| [coach/engine.py](coach/engine.py) | Stockfish: evals (cached by FEN), best move, principal variation, null-move threat check |
| [coach/motifs.py](coach/motifs.py) | Names the tactic: fork, pin, skewer, discovered attack, hanging piece, back-rank mate, allowed/missed mate |
| [coach/coach.py](coach/coach.py) | Speak or stay silent (engine thresholds), engine facts → Nemotron prompt, template fallback |
| [coach/llm.py](coach/llm.py) | Nemotron on Token Factory (OpenAI-compatible chat completions) |
| [coach/memory.py](coach/memory.py) | SQLite: games, mistakes, motif tags; streaks like "3rd game in a row" |
| [coach/display.py](coach/display.py) | Pillow → 1-bit 800×480 BMP for the e-paper |
| [coach/service.py](coach/service.py) | Ties it together; analysis runs on one worker thread so `/position` returns at once |
| [coach/server.py](coach/server.py) | FastAPI endpoints and the web page ([coach/static/index.html](coach/static/index.html)) |
| [coach/replay.py](coach/replay.py) | Replay mode: PGN → positions on a timer, in-process or over HTTP |
| [chessreader/](chessreader/) | Vision (pre-existing): camera → board → moves; [coach_bridge.py](chessreader/coach_bridge.py) posts them to the coach |
| [firmware/EinkCoach](firmware/EinkCoach) | E-paper ESP32 sketch |

### HTTP interface

| Endpoint | Used by |
|---|---|
| `POST /position {"fen": "..."}` | vision, after each move it reads. Returns `move`, `takeback`, `same` or `rejected` (with a reason) |
| `POST /game/new {"white", "black", "player", "fen"}` | vision, at game start (`player`: white, black or both) |
| `POST /game/end {"result": "1-0"}` | vision, at game end (checkmate/stalemate end the game on their own) |
| `GET /display.bmp`, `GET /display/version` | e-paper ESP32 |
| `GET /`, `GET /api/state`, `GET /board.svg`, `GET /display.png` | web page |
| `POST /replay {"pgn", "interval", "noise"}` | web page's Replay demo button |

### Coach settings (`.env`)

`COACH_PLAYER` (white/black/both), `COACH_MISTAKE_CP` (default 150; 300+ is called a blunder),
`COACH_THREAT_CP` (250), `ENGINE_TIME_S` (0.4), `COACH_DB` (coach.db).

## Tests

```bash
.venv/bin/python -m pytest -q
```

## What was built during the hackathon

The vision pipeline (`chessreader/`, the camera firmware) existed before the hackathon and is
documented in [docs/vision.md](docs/vision.md). New during the submission period: the whole coach
(`coach/`: game state from FEN, Stockfish judging, motif tagging, the Nemotron coach, memory,
e-paper rendering, server, web page, replay mode), the e-paper firmware, the vision → coach bridge,
and moving the camera from its own access point onto a shared hotspot.

## License

[MIT](LICENSE)
