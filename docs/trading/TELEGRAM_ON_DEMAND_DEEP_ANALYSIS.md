# Telegram on-demand deep market analysis

Purpose: let the owner request a **deep analysis now**, outside the automatic
15m/30m/60m reporting cadence.

This is analysis-only. It does not authorize or place trades.

## Owner commands

Preferred natural-language forms:

- `анализ btc`
- `глубокий анализ btc`
- `разбери рынок сейчас`
- `/market_deep BTC`

Optional:
- `/market_deep BTC 6h`
- `/market_deep BTC 24h`

Only the configured Telegram owner may invoke it.

## Difference from automatic notifications

Automatic monitor:
- continuously collects fresh evidence;
- maintains 15m / 30m / 60m state;
- sends only material changes/cooldown notifications.

On-demand deep analysis:
- ignores notification cooldown for the requested report;
- does NOT bypass data-quality gates;
- does NOT fabricate a fresh frame;
- asks the running collector for the latest verified state;
- if the latest evidence is too old, requests/awaits one fresh collection cycle;
- analyzes multiple horizons and historical context in one workflow.

## Deep workflow

```
OWNER TELEGRAM REQUEST
        |
        v
latest fresh verified observation
        |
        +--> 1m / 5m micro context when enough samples exist
        +--> 15m
        +--> 30m
        +--> 60m
        +--> optional 4h / 24h context
        |
        +--> Price/CVD/OI
        +--> dPOC/dVAH/dVAL/dOpen
        +--> reclaim/loss/retest history
        +--> liquidations/funding/public feeds when available
        +--> CASE-001/002/003 retrieval
        +--> promoted YouTube lessons only
        |
        v
deterministic evidence bundle
        |
        v
local reasoning model
        |
        v
independent verifier
        |
        v
Telegram owner report
```

## Output contract

Deep report should contain only sections with actual evidence:

```text
BTC — ГЛУБОКИЙ АНАЛИЗ СЕЙЧАС

СЕЙЧАС
Цена ...
CVD ...
OI ...

15m ...
30m ...
60m ...

СТРУКТУРА
dVAH ...
dPOC ...
dVAL ...
dOpen ...
recent events ...

ORDER FLOW
...

ПОХОЖИЕ CASE
CASE-00X ...

ЧТО ПОДТВЕРЖДАЕТ LONG-СЦЕНАРИЙ
...

ЧТО ПОДТВЕРЖДАЕТ BEAR-СЦЕНАРИЙ
...

ЧТО СЛОМАЕТ ТЕКУЩУЮ ИНТЕРПРЕТАЦИЮ
...

ДАННЫЕ
freshness ...
missing ...
sources ...
```

Do not print rows of UNKNOWN placeholders. Missing evidence belongs in the
compact DATA section.

## Depth policy

The deep command may spend substantially more local compute than the background
monitor:
- retrieve a longer ledger window;
- inspect additional source frames/crops;
- run CASE retrieval;
- inspect promoted video episodes;
- query multiple public read-only feeds;
- use the stronger local reasoning model;
- run a second local verifier.

Jev should compile/select the workflow once promoted in 1.5. Until then the
workflow can be deterministic orchestration.

## Hard gates

- Owner-only.
- Read-only market capabilities.
- No exchange order/write methods.
- Cooldown bypass applies only to *report delivery*, never to freshness,
  verification or safety gates.
- A manual request cannot turn UNKNOWN into a number.
- CVD/OI from incompatible series are never subtracted.
- Historical/future outcomes are retrieval context, never leaked into the
  timestamp being evaluated.
- Local model prose cannot alter raw evidence.
- If fresh data cannot be obtained, answer with the latest timestamp and clearly
  mark the report STALE/PARTIAL rather than pretending it is current.

## Acceptance

1. Owner sends `/market_deep BTC`.
2. Request is authenticated as owner.
3. A fresh observation is obtained or freshness failure is explicit.
4. 15m/30m/60m analyses are computed where history permits.
5. Verified levels/events are included when available.
6. CASE retrieval runs.
7. Strong local model writes the synthesis.
8. Independent verifier checks that every number exists in evidence.
9. Telegram receives one deep report regardless of normal notification cooldown.
10. No order/trade side effect exists.
