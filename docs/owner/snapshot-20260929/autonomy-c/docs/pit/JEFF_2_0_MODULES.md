# Jeff 2.0 - the module layer

Jeff 2.0 = Jeff 1.1 (trust and survive) + 1.5 (passport and long tasks) + 1.8 (models, voice, speed) + ten independent
modules on one contract. People talk to Jeff; the modules make him understand more, remember better, answer safer and
faster, and let the owner see what is going on. Everything is local-first, consent-gated and isolated per participant.

## Contract (already in the tree)

`bcc/pit/j2/contract.py` (`TurnContext`, `Advice`, `J2Module`, `BaseModule`) and `bcc/pit/j2/pipeline.py` (`J2Pipeline`).

* A module lives in `bcc/pit/j2/<name>.py` and exposes `create(runtime) -> J2Module`.
* Hooks: `pre_route` (may answer at once), `augment` (extra system notes = DATA for the model), `post_reply`
  (may adjust the text), `status()`, optional `start()` / `stop()` for background work.
* The pipeline caps time (0.4 / 0.6 / 0.8 s per hook), total note size (1800 chars), isolates faults and opens a circuit
  breaker after 3 consecutive failures. `BOSSMAN_JEFF_J2=off` switches the layer off. A module NEVER sees another
  participant's data and never calls a cloud model: local route or fakes only.
* Order numbers: safety 10, model_guard 20, director 30, memory_palace 40, persona 50, research 60, media 70,
  proactive 80, quality_lab 90, insights 95.

## The ten modules

| # | Module | What it does | Must be true |
|---|---|---|---|
| 1 | `safety` (Safety and Moderation Core) | Policy engine over the existing public_guard: prompt-injection and jailbreak patterns, abuse/harassment handling, boundary replies in Jeff's voice, per-participant rate limiting, escalation to the owner with an audit trail. Real participants test Jeff with jailbreak prompts, so this is first. | Deterministic, no model call in pre_route; never leaks system text; every block is audited without message content. |
| 2 | `model_guard` (Model Health Guard) | Canary probe of the local model (short prompt with a known answer), garbage/empty/repetition detection, automatic unload and, when allowed, Ollama restart; routing hint to a healthy model. Found live: Ollama returns garbled text until restarted. | A degraded model is detected within one turn; restart is rate-limited and never loops; status shows the last canary. |
| 3 | `director` (Conversation Director) | Intent and dialogue-act classification (rules first, model second), when to ask ONE clarifying question, multi-turn topic state, response-length and shape plan. | Adds notes only; never blocks a turn; classification tested on a labelled Russian set. |
| 4 | `memory_palace` (Memory Palace) | Retrieval over the participant's passport (facts, events, style): lexical BM25 plus optional local embeddings, recency and importance scoring, contradiction handling, "what do you know about me / why" answers with provenance. | Only consented layers; correct/forget/pause obey immediately; retrieval budget bounded. |
| 5 | `persona` (Persona and Style Engine) | Per-participant register, length, humour and directness from the style layer (Master Parser narratives, behaviour scales); prompt assembly within a token budget; safe A/B of prompt variants. | Style never widens permissions or facts; owner overlay wins; variants logged per turn. |
| 6 | `research` (Research Desk) | Question decomposition, keyless web search and fetch, citations, cache, uncertainty statements, prompt-injection-safe handling of fetched text. | Sources always shown; unverified statements labelled; no cloud model. |
| 7 | `media` (Media Understanding) | Photos and documents through the local vision model: captioning, OCR-style extraction, safe handling of files, size/time limits, memory of what the participant allowed. | Never stores images without consent; bounded latency; failures degrade to a plain reply. |
| 8 | `proactive` (Proactive Companion) | Reminders, follow-ups on open threads and long tasks (uses `bcc/pit/tasks.py`), daily digest, quiet hours, rate limits, one-tap opt-out. | Consent-gated; never spams; survives restart; no duplicate sends (idempotent). |
| 9 | `quality_lab` (Quality Lab) | Scores conversations with a rubric and a local LLM judge (plus deterministic checks), keeps regression corpora, produces the Jeff 1.0 vs 2.0 comparison report and the owner-readable LLM report. | Judge runs offline on stored/synthetic data; numbers are measured, never invented; per-participant isolation. |
| 10 | `insights` (Owner Insights) | Owner-only overview: participants, narratives (Master Parser 2.0), health, trends, weekly digest into the "Pult" and an API/UI page. | Owner only; no participant text in public artefacts; reads existing stores, no second database. |

## Rules for every module

Failing test first; fakes only in tests (no real Ollama, Telegram, network or participant data); LF endings and no
trailing blank line; no `asyncio.shield` (use `bcc.single_flight.await_shared`); deterministic tests; no secret-looking
literals; each module documents its status keys and its privacy stance in its own docstring. Modules are switched off
individually by the owner overlay (`bcc.pit.jeff_settings`).
