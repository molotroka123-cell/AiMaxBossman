# Twitch OI/CVD collector — runbook (owner PC)

Spec: `docs/tomorrow-2026-09-24/JEV_TWITCH_OI_CVD_COLLECTOR.md` (on `main`).
Status: **DATA_COLLECTION**. The collector has no trade, order, exchange or
credential capability (negative test `test_no_trading_surface`).

## Commands (from installed Bossman CMD)

```powershell
bossman market watch --cadence 15 --minutes 60  # foreground; read-only
bossman market status
bossman market export
bossman market stop                             # writes collector STOP
```

These commands use the same `bcc.market.collector` and local ledger. `watch`
keeps running in its own terminal process; close it with `bossman market stop`.
For an isolated test, pass the same `--root <dir>` to every command.

## Direct module commands (from `command-center/`)

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
   three times from 8x colour Lanczos, grayscale Lanczos and grayscale bicubic
   renderings. Only unanimous, schema-valid readings become numbers; anything
   else is `LOW_CONFIDENCE` / `UNREADABLE` with the raw reads kept as evidence.

## Known model behaviour (measured 2026-09-24)

- At 3–6x renderings the vision model reads some `5` as `6` — twice with all
  three reads agreeing (false VERIFIED). 8x renderings removed it on every
  eye-checked crop. The owner golden JPEG exposed a final `B` misread as `8`
  in the inverted grayscale rendering. Version v4 uses grayscale bicubic for
  that third read. Rows of earlier extractor versions stay in the ledger;
  `research.py` accepts calibrated v3 and v4 only.
- On 480p frames the model invents text ("Epic Material: 50,000"); the label +
  unit validator rejects it.

## Owner analyzer and notifications

`run` processes every observation from the ledger. Only two complete,
individually VERIFIED v3 observations with the same channel, symbol, exchange,
timeframe, CVD type, and extractor can enter `bcc.market.analyzer`.
It normalizes K/M/B/T before calculating deltas and calls
`learning/trader_apprentice.py` for the single live classification path.
Historical matches come from the September casebook and canonical CASE files.
No level is inferred from a value badge; absent levels stay `UNKNOWN`.
Classification confidence measures the quality of the classification, never
the chance of profit. Analysis JSON goes to `reports/analyses.jsonl`.

The local Ollama text model may explain the already classified result in
Russian. Numeric fields, regime, scenarios, case IDs and confidence in the
Telegram message are rendered deterministically from typed analysis. A failed
text-model call falls back to deterministic reasons, never to cloud vision or
to a fabricated number. `reports/delivery.jsonl` records the returned Telegram
message ID or an error code. Delivery uses the existing Companion configuration
and outbound transport with its bound owner identity and egress guard. The
collector keeps collecting if explanation or Telegram fails.

By default, messages are sent for a new regime, level crossing, large CVD/OI
change, or data-quality transition, with a persistent fingerprint and a
15-minute cooldown. The owner can use `/market_verbose on|off` in Companion to
send every verified observation. This switch uses the default collector data
root; a custom `--root` requires placing/removing `VERBOSE_NOTIFICATIONS` there.

After a restart the last fresh frame SHA and last individually verified v3
CVD/OI baselines are restored from the JSONL-backed ledger. A repeated frame
therefore becomes `STALE_FRAME` with null metrics even on the first new attempt.
