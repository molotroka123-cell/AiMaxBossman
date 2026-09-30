# PASS 3 integration — what exists at HEAD, flags, evidence policy, rollback

Scope: narrow layer over existing engines. No second Gateway, cache engine, learning store,
approval system, dashboard or provider adapter was created.

## Shared contract

`bossman_shared/cache_observation.py` (repo root, imported by both apps through
`bossman/_shared.py` / `bcc/_shared.py`; degrades to "no observation" when absent).
Schema: `schemas/cache_observation.schema.json` (event_version 1). States HIT / WRITE / MISS /
BYPASS / UNKNOWN / DEGRADED are derived from provider usage only:

| Evidence | State |
|---|---|
| cache_read_input_tokens > 0 (or OpenAI-style cached_tokens) | HIT |
| no read, cache_creation_input_tokens > 0 | WRITE |
| usage present, no read/write | MISS |
| cache disabled / unsupported provider / not requested | BYPASS |
| no usage evidence | UNKNOWN (`cache_control` alone is never a hit) |
| metadata rejected / partial telemetry | DEGRADED |

Buckets are mutually exclusive: `total_input = fresh + read + write`. Anthropic `input_tokens`
is the fresh bucket; OpenAI/OpenRouter `prompt_tokens` already includes cached tokens and is
split, never added twice. Streaming usage comes from the observe-only collector (bytes unchanged).
Observations carry hashes and numbers only; `validate_observation` rejects content fields.

## Routes

- Gateway (OpenRouter path): `bossman/gateway/app.py` records an observation on the JSON and
  streaming success paths; `/metrics.cache_observations` summarises counts, tokens, measured
  actual cost, estimated baseline, unknown-cost requests, degraded events.
- Command Center direct Anthropic path: `bcc/engine.py::cache_observation_for` emits
  `cache.observation` on the EventBus for every model call (HIT/WRITE/MISS/BYPASS/UNKNOWN).
  `_cost` charges fresh / cache-read / cache-write separately; unknown cache prices fall back
  to the fresh price (conservative upper bound) and the observation reports `actual_cost_usd =
  null` — no invented savings. Numeric telemetry is exempt from key-name redaction; strings are
  still redacted.

## Dashboard (API on the existing Command Center)

- `GET /api/cache/economics` — Provider Cache Economics: `measured` / `estimated` / `unknown`
  blocks kept separate; `saved_usd` is null whenever any cost is unknown; warning when
  cache_control was applied without provider usage; hit rate flagged diagnostic, not KPI.
- `GET /api/cache/intelligence` — Cognitive Reuse Intelligence: verified-success rate from
  evaluations (measured), degraded/stale events, learning candidates, and `unknown` for anything
  not instrumented (false-success rate, same-model A/B, time-to-resume). Waste signals and
  advice appear only with their flags on.
- UI: the existing System page (`command-center/ui/pages.js`, `SystemPage`) renders both
  panels under the health list. Every row sits under a header pill that names its evidence
  level (`измерено` / `оценка` / `неизвестно`); savings show "cannot be claimed" when any cost
  is unknown, and flag-gated sections show "disabled by flag" instead of an empty list. The
  panels are fetched with `Promise.allSettled`, so a failing cache endpoint never breaks the
  system metrics view. Rendering was checked by syntax only (`node --check`); a browser
  screenshot was not taken on this host (NOT_TESTED_ON_THIS_HOST).

## Observe-only / advisory-only layers

`bossman_shared/cache_intelligence.py`: `detect_context_waste` (signals only),
`cache_advice` (BLOCK on any security-context movement; NO_ACTION below 20 samples; never
suggests moving policy, caching credentials/approvals, enabling cache or promoting memory),
`allow_local_cognitive_reuse` (same-model A/B, isolated holdout, ≥20 samples per arm,
non-inferior VerifiedSuccess, continuity or compute gain, no stale/false-success/security
regression; no API-dollar objective for local models), `fresh_observation_wins`.

## Autonomy Trainer (shadow only)

`bossman/learning_guard/autonomy_trainer.py` over Learning Guard: episodes are eligible only as
state → typed action → fresh observation → independent verification; UI actions need a semantic
anchor; self-reported success, holdout, stale sessions and hidden chain-of-thought are rejected.
`evaluate_candidate` → SHADOW only with ≥3 independent verified episodes (≥10 for route/budget/
weakness_patch or `scope.risky`), one explicit environment fingerprint and model version,
non-inferior VerifiedSuccess; otherwise CANDIDATE with `INSUFFICIENT_EVIDENCE`, QUARANTINED on
holdout/security regression, REJECTED on false success. `promote_candidate` goes through
`guard_promotion` (A/B verified-only, ≥20 shadow runs, security snapshot veto) and `promote`
(owner approval + tested rollback). Schema: `schemas/autonomy_candidate.schema.json`.

## Feature flags (defaults)

| Flag | Default | Effect |
|---|---|---|
| BOSSMAN_CACHE_TELEMETRY_V2 | ON (numeric only) | direct-route observations on the bus |
| BOSSMAN_CONTEXT_WASTE_OBSERVE | OFF | waste signals in `/api/cache/intelligence` |
| BOSSMAN_CACHE_ADVISOR | OFF | advisory text in `/api/cache/intelligence` |
| BOSSMAN_AUTONOMY_TRAINER_SHADOW | OFF | `record_candidate` returns None when off |
| BOSSMAN_COGNITIVE_REUSE_EXPERIMENT | OFF | reuse gate available; no runtime reuse path enabled |
| AI_COMPANY_MODE_ENABLED | OFF | experimental; synthetic demo only |
| BOSSMAN_DEEP_FIX_ENABLED | OFF | plan-binding gate + Deep Fix state machine |

## Live evidence

No Anthropic key and no permission for paid calls on this host:
`LIVE_CACHE_HIT_NOT_PROVEN`. Unit tests prove request shaping and provider-usage
classification with golden fixtures only. Prewarm policy (5m default, 1h explicit,
`max_tokens: 0` if supported by the installed SDK) is not exercised live.

## Rollback

All layers are additive: unset the flags above (telemetry: `BOSSMAN_CACHE_TELEMETRY_V2=0`),
or revert the commits `feat(cache)` / `feat(intelligence)`; no schema migrations, no
persisted cache contents, no changed request payloads.
