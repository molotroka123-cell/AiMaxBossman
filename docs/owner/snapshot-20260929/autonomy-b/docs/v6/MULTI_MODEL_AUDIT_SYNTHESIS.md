# Epoch 6 — Multi-model Audit Synthesis

## Purpose

Astra/GPT, Perplexity, Grok и другие независимые модели используются как источники гипотез и cross-check, а не как голосование, которое автоматически делает утверждение истинным.

## Evidence tiers

1. **MEASURED** — exact-SHA benchmark/profile result;
2. **CODE_EVIDENCED** — конкретный hot path подтверждён текущим кодом;
3. **REPRODUCED** — проблема воспроизводится, но не обязательно измерена полноценно;
4. **HYPOTHESIS** — правдоподобное предложение из аудита;
5. **REJECTED/STALE** — не соответствует актуальному коду или измерениям.

## Consensus table format

| Finding | Astra | Perplexity | Grok | Current code | Measurement | Final disposition |
|---|---|---|---|---|---|---|
| Startup eager work | hypothesis/evidence | hypothesis | pending | inspect | baseline | TBD |
| UI polling/render waste | hypothesis/evidence | hypothesis | pending | inspect | request+CPU profile | TBD |
| Gateway per-request connection | rejected if reusable client exists | claimed | pending | reusable GatewayClient already exists | profile other paths | PARTIAL/STALE |
| Model residency | recommended | recommended | pending | inspect runtime | reload/load metrics | TBD |
| Media contention | recommended | recommended | pending | inspect | export+UI profile | TBD |

## Synthesis rules

- consensus raises **investigation priority**, not truth level;
- one exact benchmark can overrule three speculative audits;
- stale findings are preserved with reason, not silently deleted;
- optimistic gains are converted to testable acceptance hypotheses;
- claims about future Ryzen hardware remain forecast until owner-machine measurement;
- findings already fixed in current code move to `ALREADY_DONE`, not implementation backlog.

## Current known corrections

- exact idle RAM/VRAM: UNKNOWN;
- exact startup seconds: UNKNOWN until baseline;
- exact total speedup: UNKNOWN;
- Gateway generic connection-reuse fix must not be duplicated where reusable client already exists;
- safe observation reuse already exists in Computer Operator and further reduction needs freshness proof.

## When Grok/other audit lands

Record exact file path + commit SHA in `docs/optimization/AUDIT_INDEX_2026-09-07.md`, classify every claim with the tiers above, then update this synthesis. Do not copy its numbers into README as facts without measurement.