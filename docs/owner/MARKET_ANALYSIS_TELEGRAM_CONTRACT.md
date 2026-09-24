# Market analysis → Telegram contract

Goal: convert verified Twitch observations into a short owner notification without allowing the text model to invent market data.

## Input object

Minimum typed fields:

```json
{
  "observation_id": "...",
  "captured_at_utc": "...",
  "source": "twitch:k1m6a",
  "instrument": {"symbol": "BTC1!", "exchange": "CME", "timeframe": "30"},
  "price": 0,
  "cvd": {"value": 0, "unit": "B"},
  "oi": {"value": 0, "unit": "B"},
  "quality": {
    "status": "VERIFIED",
    "confidence": 0.0,
    "frame_sha256": "...",
    "extractor": "..."
  }
}
```

Missing fields stay null/UNKNOWN.

## Deterministic analysis object

```json
{
  "previous_observation_id": "...",
  "current_observation_id": "...",
  "compatible": true,
  "delta_price": null,
  "delta_price_pct": null,
  "delta_cvd": null,
  "delta_oi": null,
  "price_direction": "UP|DOWN|FLAT|UNKNOWN",
  "cvd_direction": "UP|DOWN|FLAT|UNKNOWN",
  "oi_direction": "UP|DOWN|FLAT|UNKNOWN",
  "regime": "...",
  "classification_confidence": 0.0,
  "levels": {},
  "long_scenario": {
    "trigger": "...",
    "confirmation": "...",
    "invalidation": "..."
  },
  "bear_scenario": {
    "trigger": "...",
    "confirmation": "...",
    "invalidation": "..."
  },
  "missing_data": [],
  "case_matches": [],
  "evidence_refs": []
}
```

The deterministic layer owns numbers, deltas, compatibility and regime.

The LLM owns wording only.

## Notification triggers

Send when at least one is true:

1. regime changed;
2. important structural level was reclaimed/lost;
3. CVD/OI change exceeds the configured materiality threshold;
4. previously unreadable data becomes verified;
5. verified data becomes unreliable/offline for a sustained interval;
6. periodic digest timer fires.

Do not trigger on every identical 15-second sample.

## Owner message template

```text
BTC • HH:MM

Цена: <price> (<delta>)
CVD: <value> (<delta>)
OI: <value> (<delta>)

STATE: Price ↓ | CVD ↑ | OI ↓
РЕЖИМ: Buyer failure with deleveraging

ПОЧЕМУ:
<one short explanation grounded in deterministic evidence>

УРОВНИ:
<nearest relevant levels or UNKNOWN>

LONG:
<trigger → confirmation → invalidation>

BEAR:
<trigger → confirmation → invalidation>

CASE:
<closest historical case(s), if any>

DATA:
VERIFIED / confidence X
```

## Hard invariants

- No market number may originate from the text LLM.
- Jev may route, but does not read canvas/video values.
- UNKNOWN must remain UNKNOWN.
- Confidence is classification/data confidence, not chance of making money.
- Notification delivery does not authorize a trade.
- No exchange write credentials are required by this pipeline.
