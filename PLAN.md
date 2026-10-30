# Pawnder (over-the-board chess coach): build plan

Deadline: **Fri Oct 30, 10:00 PDT**. Scope is ruthless: P0 must run end to end before anything else.

## Architecture

```
 ESP32-CAM ──phone hotspot──▶ chessreader (vision, existing)        PGN file
                                  │ POST /position {fen}                │ replay (timer)
                                  ▼                                     ▼
                        ┌──────────────── coach server (FastAPI) ────────────────┐
                        │ game.py     FEN → legal move (bad reads flagged)       │
                        │ engine.py   Stockfish: eval, best move, cp loss        │
                        │ motifs.py   hanging piece, fork, pin, skewer, ...      │
                        │ coach.py    speak or stay silent? (engine decides)     │
                        │ llm.py      Nemotron (Token Factory) phrases it        │
                        │ memory.py   SQLite: games, mistakes, motifs            │
                        │ review.py   Nemotron Ultra post-game review   (P1)     │
                        │ display.py  Pillow → 800x480 1-bit screen              │
                        └────────────────────────────────────────────────────────┘
                           │ GET /display.bmp + /display/version      │ GET / (web UI)
                           ▼                                          ▼
                   ESP32 e-paper (personal coach)            laptop: board, eval, feed, voice
```

- Everything joins the **phone hotspot**: the laptop keeps internet (Token Factory) and reaches both ESP32s.
- **Stockfish is the judge.** Nemotron only turns engine facts into one sentence; it never evaluates moves.
- Fast model (Nemotron Super/Nano) for live comments, Nemotron Ultra for the review. IDs come from `.env`.
- Every Nemotron call has a template fallback, so the demo never stalls on the network.

## P0 (tonight)

- [x] Replay mode: feed a PGN's positions on a timer (in-process or over HTTP), optional misread noise
- [x] Game state: FEN → inferred legal move, takeback, bad reads flagged and ignored
- [x] Stockfish: eval before/after, best move, centipawn loss, threat check (null move)
- [x] Motif tags: hanging piece, fork, pin, skewer, discovered attack, back rank, missed mate
- [x] Coach decision + Nemotron comment (template fallback)
- [x] SQLite memory + recurring-pattern line ("3rd game in a row ...")
- [x] `/display.bmp` (1-bit 800x480) + `/display/version`
- [x] E-ink ESP32 firmware (GxEPD2, polls version, redraws on change)
- [x] Camera firmware joins the phone hotspot (falls back to its own AP)
- [x] chessreader → coach bridge (POST /position after each move)
- [x] LICENSE (MIT), README, .env.example

## P1

- [ ] Post-game review with Nemotron Ultra (turning points, recurring weakness, 3 drills)
- [x] Local web page: board, eval bar, coach feed, live (for the video); same look as the drills site; side picker
- [x] Voice: coach messages spoken (browser speech synthesis, no new dependency; not yet checked by ear)

## P2 (only if P0/P1 are solid)

- [ ] Tavily: opening explanation + master games after the game
- [ ] Push mistakes into the drills site

## Split (two people)

- A: server, coach, memory, Nemotron prompts, review.
- B: e-ink firmware + hardware, camera on the hotspot, demo video script.
