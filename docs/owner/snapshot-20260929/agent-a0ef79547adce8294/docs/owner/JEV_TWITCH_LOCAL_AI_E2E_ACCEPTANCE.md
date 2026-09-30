# Jev × Twitch × Local AI — owner E2E acceptance

This document is a gate checklist, not a promise of readiness.

## Gate A — branch convergence

PASS only if current HEAD contains both:
- Exact candidate lineage `c489cc62398b7213efbc820f10b64e57f4f054e5`;
- collector lineage `2a74c63be9df0c85758bd82be741e38742836d48`.

No Jev live test before this gate.

## Gate B — market collector correctness

For every accepted numeric observation:

- stream_state = LIVE;
- fresh_frame = true;
- frame hash exists;
- metric crop hash exists;
- raw local-vision reads exist;
- symbol is bound;
- extractor identity/version is present.

A stale/offline/player-error row must not contain price/CVD/OI.

Readable calibration errors accepted as VERIFIED: target **0**.

Coverage may be below 100%; incorrect accepted numbers are worse than UNKNOWN.

## Gate C — restart durability

After restart:
- previous frame identity is recovered or first post-restart sample is otherwise proven fresh;
- no old market value is copied into a new timestamp;
- dedupe survives;
- JSONL remains the source of truth;
- SQLite can be rebuilt from JSONL.

## Gate D — Jev

PASS:
- contract verified;
- selftest passes;
- shadow run completes;
- fallback works;
- Jev cannot bypass STOP/approval;
- video/canvas pixel metric request escalates to local vision;
- secrets absent from logs.

Jev result is independent of market-data correctness.

## Gate E — deterministic market reasoning

Analyzer must compare only compatible observations:
- same source/provider;
- same instrument/venue semantics;
- compatible CVD/OI series/settings.

Required:
- exact observed values;
- exact deltas;
- explicit UNKNOWN;
- regime from canonical rules;
- evidence vs inference separated;
- historical case match optional but sourced.

Disallowed:
- "OI up = shorts" as a fact;
- "CVD up = bullish" in isolation;
- one screenshot treated as a trend;
- invented probability of profit;
- exact CME/reference level silently used as perp order price.

## Gate F — local LLM

The LLM receives a typed analysis object, not arbitrary Twitch page text.

It must:
- preserve numbers;
- preserve UNKNOWN;
- state confidence as classification confidence;
- give both bullish and bearish scenario conditions;
- avoid adding unsupported facts.

Adversarial page/chat text must remain untrusted data.

## Gate G — Telegram

A valid owner message must contain enough information to audit the claim:

- time;
- price/CVD/OI;
- delta/state;
- regime;
- important level/trigger if available;
- data quality/confidence.

Suppress duplicates unless owner explicitly enables verbose mode.

## Final E2E acceptance

One real trace must connect:

`Twitch frame_sha → crop_sha → extracted values → ledger row → analysis JSON → local LLM text → Telegram delivery`

All ids/timestamps must refer to the same observation chain.

### Verdict rules

**PASS** — complete real owner-PC trace, no unresolved correctness blocker.

**PARTIAL** — useful path works but at least one non-fabricated component is missing/blocked (for example Jev provider unavailable while local collection works).

**BLOCKED** — cannot establish trustworthy fresh market data or cannot produce a valid owner delivery trace.
