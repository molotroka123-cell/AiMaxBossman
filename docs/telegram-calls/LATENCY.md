# Telegram calls — latency

**No latency of a real call has been measured. There is no claim of «instant» or «real-time» speech.**

## What is measured and where

The number that matters is `response_latency_ms` = end of the interlocutor's speech (VAD decision) -> first PCM
frame handed to the transport (`TurnMetrics` in `types.py`), broken down into `stt_final_ms`, `llm_first_token_ms`,
`tts_first_audio_ms`, `send_ms`. `latency_stats` reports p50/p95/max over the turns of a call.

| Where | Engines | What the number means | Status |
|---|---|---|---|
| `bossman call selftest`, no models | scripted STT + tone TTS + energy VAD, loopback, line sped up ~2.5x | plumbing only | measured, see below |
| `bossman call selftest`, real Whisper + Piper | real STT/TTS, scripted brain, loopback, real time | our audio path without Telegram and without an LLM | NOT_RUN (needs the owner's models) |
| real call | real everything | the actual user-visible latency | NOT_RUN, OWNER_REQUIRED |

## Numbers measured so far (Line D, fake engines) — PLUMBING ONLY

Three runs of `selftest.run()` with scripted engines, 10 turns each, sped-up loopback line: p50 = 500.0 ms,
p95 between 500.0 and 515.0 ms. The value sits at the endpointer's 500 ms end-of-utterance hangover plus a few
milliseconds, i.e. it shows that our own code adds only a few ms on top of the endpointing rule with zero-cost
scripted engines. It says **nothing** about speech recognition, synthesis, the LLM, the network or Telegram, and must
not be quoted as the product's latency. The run was not done at real time (the line was accelerated), so even the
plumbing figure is indicative.

## Where the time will go in a real call (design, not measurement)

Endpoint hangover (configured 500 ms) + Whisper decode of the utterance (speculative decoding starts while the
person is still finishing, `speculative_ms`) + first LLM token (local model) + first TTS sentence (Piper streams by
sentence) + transport send. Each stage is timestamped per turn so the real call shows which one dominates.
Nothing above is a promise.

## How to measure for real

Owner checklist in `ACCEPTANCE.md`, steps 3 and 7-9: at least 10 turns in one real call; copy `latency_ms` (n, p50,
p95, max) from the call record; report the self-test numbers separately and never mix them.
