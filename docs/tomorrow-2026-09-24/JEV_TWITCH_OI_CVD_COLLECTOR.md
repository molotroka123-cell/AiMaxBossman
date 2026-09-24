# Jev Twitch OI/CVD Collector — owner run 2026-09-24

Status: **SPEC READY / READ-ONLY DATA COLLECTION / NO TRADING AUTHORITY**

Owner goal: on the local Bossman machine, observe the Twitch stream at
`https://m.twitch.tv/k1m6a`, extract visible crypto market metrics such as
Open Interest (OI) and Cumulative Volume Delta (CVD), and build an evidence-backed
time series that can later be used to study trading setups.

This is a **data collection experiment only**. It must not place trades, send
orders, manage exchange credentials, or convert an observation into a buy/sell
instruction. Trading remains READ-ONLY / PAPER until a separate owner-approved
stage with holdout evidence.

## 1. Why Jev is not the pixel parser

Jev Ultrafast is DOM/state oriented. Twitch market overlays are likely rendered
inside a video/canvas surface, so Jev must not invent selectors or pretend it can
read pixels that are not represented as DOM text.

Use Jev for:
- opening/navigating the channel;
- detecting obvious player/page state;
- controlling low-risk observation cadence;
- proposing when to sample, refresh or re-open the player;
- structured routing to the existing Bossman verifier.

Use the existing local screenshot/vision/OCR path for:
- capturing the player frame or configured metric regions;
- reading numeric OI/CVD labels;
- recognizing symbol, exchange, timeframe and units;
- producing a confidence score and raw OCR text.

If a value cannot be verified from the frame, record `null` / `UNREADABLE`.
Never interpolate a missing OI/CVD value and never copy the previous sample forward
as if it were fresh.

## 2. Local-first architecture

```
Twitch channel
    |
Bossman browser session
    |
Jev low-risk observer / scheduler
    |
fresh player observation
    |
bounded screenshot / metric crops
    |
LOCAL vision/OCR extractor
    |
schema validator
    |
independent plausibility + freshness checks
    |
append-only raw observation log
    |
normalized OI/CVD time-series
    |
later: research / paper-trading evaluation
```

No frame leaves the machine by default. Do not send Twitch screenshots, crops,
cookies, auth state or private browser context to cloud models unless the owner
explicitly enables a separate cloud-vision route.

## 3. Proposed capability surface

Add this as capabilities of the existing Bossman, not a second agent stack:

- `market.twitch.open`
- `market.twitch.observe`
- `market.twitch.capture`
- `market.metrics.extract`
- `market.metrics.record`
- `market.metrics.export`
- `market.metrics.status`

Jev remains behind the existing browser/router policy. It does not bypass
`never / ask / allowed`, STOP, budgets, privacy, evidence or restart rules.

## 4. Source identity

Initial source:
- channel: `k1m6a`
- requested URL: `https://m.twitch.tv/k1m6a`
- record the final resolved browser URL on every session;
- record whether the stream is LIVE / OFFLINE / PLAYER_ERROR / LOGIN_REQUIRED.

Do not treat OFFLINE as a product failure.

## 5. Sampling policy

Default active-stream cadence: **15 seconds**.

Configurable range:
- fast experiment: 5 s;
- normal collection: 15 s;
- low-resource collection: 30–60 s.

When the stream is offline, re-check at a much slower interval, for example every
60–120 seconds. Do not burn Jev/model calls at active-stream cadence while offline.

Each sample must be based on a fresh observation. A stale screenshot is never
allowed to produce a new market row.

## 6. What to extract

Required when visible:
- instrument/symbol, e.g. BTC or ETH;
- market/exchange if shown;
- price if shown;
- OI numeric value;
- OI unit/currency;
- CVD numeric value;
- CVD unit/currency;
- whether CVD is spot/perp/combined if the UI says so;
- source timeframe if shown.

Useful visual metadata:
- OI direction: rising / falling / flat;
- CVD direction: rising / falling / flat;
- only if a deterministic validated visual extractor supports it.

Important: if CVD is shown only as a graph and no reliable numeric label exists,
leave `cvd_value=null`. A visual impression must not be stored as a fabricated
number.

## 7. Raw observation schema

Recommended append-only JSONL record:

```json
{
  "schema": "bossman.market-observation/1",
  "captured_at_utc": "2026-09-24T09:00:00.000Z",
  "source": {
    "platform": "twitch",
    "channel": "k1m6a",
    "requested_url": "https://m.twitch.tv/k1m6a",
    "resolved_url": null,
    "stream_state": "LIVE"
  },
  "instrument": {
    "symbol": "BTC",
    "exchange": null,
    "market_type": null,
    "timeframe": null
  },
  "metrics": {
    "price": null,
    "oi_value": null,
    "oi_unit": null,
    "cvd_value": null,
    "cvd_unit": null,
    "cvd_type": null
  },
  "quality": {
    "status": "VERIFIED",
    "confidence": 0.0,
    "fresh_frame": true,
    "manual_review": false
  },
  "evidence": {
    "frame_sha256": null,
    "crop_sha256": null,
    "bbox": null,
    "ocr_raw": null,
    "extractor": null
  }
}
```

The example contains nulls deliberately. A missing metric is better than fake data.

## 8. Storage

Keep runtime data outside Git.

Recommended:
```
%LOCALAPPDATA%\Bossman\CommandCenter\market-data\twitch\k1m6a\
  raw\YYYY-MM-DD\observations.jsonl
  crops\YYYY-MM-DD\...
  market-observations.sqlite
  exports\YYYY-MM-DD.csv
  reports\collector-status.json
```

Rules:
- JSONL is the immutable raw ledger;
- SQLite is the query/index layer;
- CSV is an export, never the source of truth;
- use WAL/transactional writes;
- deduplicate using source + frame hash + instrument + metric identity;
- keep full-frame screenshots only when needed for debugging;
- prefer bounded OI/CVD evidence crops to recording whole Twitch video;
- configure retention separately for crops and numeric records.

Never commit the runtime dataset, screenshots, cookies or stream recordings to Git.

## 9. Freshness and false-PASS controls

Before accepting a sample:
1. browser observation belongs to the active session;
2. frame timestamp/hash changed or freshness is otherwise proven;
3. crop is inside the expected player region;
4. OCR/vision output matches strict numeric/unit schema;
5. confidence is above the configured threshold;
6. value is not merely copied from previous state;
7. implausible jumps are flagged, not silently corrected.

Suggested statuses:
- `VERIFIED`
- `LOW_CONFIDENCE`
- `UNREADABLE`
- `STALE_FRAME`
- `AMBIGUOUS_SYMBOL`
- `AMBIGUOUS_UNIT`
- `STREAM_OFFLINE`
- `PLAYER_ERROR`

Low-confidence rows remain in raw evidence but must be excluded from the training
dataset by default.

## 10. Calibration run on the owner machine

### Stage A — browser/player

1. Open the source URL in the existing Bossman browser.
2. Confirm the final resolved URL and channel identity.
3. Confirm the player is live.
4. Capture one full player frame.
5. Locate OI/CVD labels/regions manually once if automatic region discovery is
   uncertain.
6. Save the region config locally.

PASS requires an actual fresh player image, not only a successful HTTP response.

### Stage B — extractor

Collect 20 manually reviewed samples.

For each:
- save crop;
- save OCR raw text;
- save parsed values;
- owner/verifier checks exact visible value.

Do not start unattended collection until numeric accuracy on the visible fields is
at least **95% on the 20-sample calibration set**, with no silent unit/symbol swaps.

### Stage C — unattended collection

Run at 15 s cadence for at least 60 minutes while live.

Acceptance evidence:
- >= 200 attempted samples;
- every attempt has a timestamp + status;
- no stale-frame duplicate presented as fresh;
- no fabricated numeric values;
- restart/resume preserves the dataset;
- STOP stops new captures quickly;
- browser/player recovery does not duplicate rows.

## 11. Restart / STOP

On STOP:
- no new screenshots;
- no new Jev decisions;
- finish or abort the current local parse safely;
- flush the current ledger transaction.

On backend/browser restart:
- reopen the exact channel;
- prove stream identity again;
- require a fresh frame before recording;
- never backfill the downtime with repeated last-known values.

## 12. Jev-specific rollout

### Phase 1 — shadow / observer

Jev may propose:
- sample now;
- wait;
- refresh player;
- re-open channel;
- escalate to the full browser path.

Existing Bossman remains authoritative.

### Phase 2 — low-risk execution

Only after the normal Jev shadow acceptance is green, Jev may execute reversible
browser/player observation actions.

It still cannot:
- place trades;
- open exchange order tickets as an execution target;
- send credentials;
- approve purchases;
- publish externally;
- disable verification.

## 13. Dataset for learning to trade

Do not train on raw rows immediately.

First collect enough history, then create a derived research table with:
- price return after 1m / 5m / 15m / 30m;
- OI change over matching windows;
- CVD change/slope when numeric data is valid;
- OI-price divergence flags;
- CVD-price divergence flags;
- volatility/regime context;
- missing-data flags.

Split train/validation/holdout **by time/day**, not by random rows, otherwise the
same market episode leaks across the split.

Keep future returns as labels generated only after the horizon has passed.

Initial goal:
`COLLECT -> VERIFY -> ANALYZE -> PAPER TEST`

Not:
`COLLECT -> AUTO TRADE`.

## 14. First research questions

Once the collector has enough clean observations, test questions such as:
- price rising + OI rising vs price rising + OI falling;
- price rising while CVD falls;
- price falling while CVD rises;
- sudden OI expansion before/after volatility;
- whether combinations have different forward returns in different regimes.

These are hypotheses to measure, not trading rules.

## 15. Minimum implementation acceptance for today

Today is successful when:

- [ ] collector is integrated into the existing Bossman runtime;
- [ ] Twitch source opens on the owner PC;
- [ ] Jev/browser status is observable;
- [ ] video/canvas correctly escalates to local screenshot/vision;
- [ ] one manually configured or auto-discovered OI/CVD crop works;
- [ ] raw JSONL is written outside Git;
- [ ] SQLite/CSV export works;
- [ ] 20-sample manual calibration report exists;
- [ ] one-hour collection can run/restart/STOP without duplicate false data;
- [ ] no order/trading capability is present in this collector;
- [ ] end-of-run report states exact sample counts and quality rates.

## 16. End-of-run report

Write:

`%LOCALAPPDATA%\Bossman\CommandCenter\market-data\twitch\k1m6a\reports\owner-run-2026-09-24.md`

Include:
- source URL and resolved URL;
- implementation SHA;
- Bossman build SHA;
- Jev mode;
- extractor/model identity;
- collection start/end;
- attempted / VERIFIED / LOW_CONFIDENCE / UNREADABLE / stale counts;
- manual calibration accuracy;
- OI coverage rate;
- CVD coverage rate;
- restart/STOP result;
- storage paths;
- blockers;
- next action.

Do not claim `TRADING_MODEL_READY` from one stream or one day of data.
