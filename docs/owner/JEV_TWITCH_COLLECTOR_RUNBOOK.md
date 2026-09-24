# Twitch OI/CVD collector — runbook (owner PC)

Spec: `docs/tomorrow-2026-09-24/JEV_TWITCH_OI_CVD_COLLECTOR.md` (on `main`).
Status: **DATA_COLLECTION**. The collector has no trade, order, exchange or
credential capability (negative test `test_no_trading_surface`).

## Commands (from `command-center/`)

```powershell
python -m bcc.market.collector calibrate --samples 20 --root <dir>   # keeps frames + crops for review
python -m bcc.market.collector run --cadence 15 --minutes 70         # unattended
python -m bcc.market.collector status
python -m bcc.market.collector stop                                  # writes STOP; capture ends within one attempt
python -m bcc.market.collector export                                # CSV from the SQLite index
python -m bcc.market.research                                        # research table, labels only after horizon
```

Bossman API (same backend, owner token): `GET /api/market/status`,
`GET /api/market/export`, `POST /api/market/stop`. There is deliberately no
start endpoint: the collector runs as its own process so a browser crash never
touches the backend.

Data (never in Git): `%LOCALAPPDATA%\Bossman\CommandCenter\market-data\twitch\k1m6a\`
— `raw/<day>/observations.jsonl` (truth) → `market-observations.sqlite` (WAL
index) → `exports/*.csv`; `crops/<day>/` evidence; `reports/`.

## How a number gets recorded

1. Playwright opens `https://www.twitch.tv/k1m6a` (the desktop player of the
   owner's `m.twitch.tv/k1m6a`: only it lets us pin quality — adaptive bitrate
   dropped to 480p under load and made labels illegible). Cookies are declined,
   quality is pinned to 1080p60 through the player's own menu.
2. Jev routing: values drawn inside `<video>`/`<canvas>` are not DOM →
   `escalate_local_vision`; Jev never reads or guesses a number.
3. Fresh frame proof per attempt: video time advanced while sampling, playing,
   frame hash differs from the last recorded frame. Otherwise `STALE_FRAME`
   with nulls.
4. Badges move with the value; they are located per frame. The badge's own
   label ("CVD", "Open Interest") decides the metric.
5. LOCAL vision (`bossman-fast-qwen36-vision`, loopback only) reads each badge
   three times from decorrelated 8x renderings (colour, grayscale, inverted
   grayscale). Only unanimous, schema-valid readings become numbers; anything
   else is `LOW_CONFIDENCE` / `UNREADABLE` with the raw reads kept as evidence.

## Known model behaviour (measured 2026-09-24)

- At 3–6x renderings the vision model reads some `5` as `6` — twice with all
  three reads agreeing (false VERIFIED). 8x renderings removed it on every
  eye-checked crop. Rows of earlier extractor versions stay in the ledger but
  `research.py` only accepts `triple-read-unanimous/v3`.
- On 480p frames the model invents text ("Epic Material: 50,000"); the label +
  unit validator rejects it.
