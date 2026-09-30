# ASTER owner Twitch test — 2026-09-24

Verdict: **PARTIAL**. This is a read-only market observation test, not a trading
model or an owner-phone E2E pass. No exchange order/write endpoint was called.

## Code and tests

- Base: `c489cc62398b7213efbc820f10b64e57f4f054e5`; eight requested
  collector commits cherry-picked onto one `test/aster-owner-twitch-20260924`
  branch. Exact privacy, red-team and Windows changes in the base were retained.
- Live classification now uses `learning/trader_apprentice.py` after a complete
  v3 VERIFIED compatible observation pair. `coinwise/classify.py` is not in this
  live path. Case retrieval reads the September casebook and canonical CASEs.
- Restart restores the last fresh frame SHA and per-metric verified baselines
  from the JSONL-backed ledger. Repeating the frame after restart yields nulls.
- The text model receives a typed deterministic classification and contributes
  only Russian prose. Its output cannot supply the displayed numeric fields.
  Telegram delivery uses Companion's configured owner and outbound adapter;
  fingerprint/cooldown state persists outside Git.
- Collector/Jev/browser/Telegram suite: **297 passed**. Trader Apprentice and
  YouTube learning: **50 passed**. Core apprentice/trading: **110 passed,
  1 skipped**. Jev offline selftest: **SELFTEST_PASS** (stub fixtures only).

## Local preflight and 20-frame live calibration

- Ollama listening on `127.0.0.1:11434`; installed vision model
  `bossman-fast-qwen36-vision:latest` answered a local image probe.
  Extractor refuses non-loopback endpoints and has no cloud fallback.
- Live `calibrate --samples 20 --headed` ran from 17:39:46.690 to
  17:44:23.327 UTC. All 20 rows were `LIVE`, `BTC1! · 30 · CME`, native
  1920×1080, with player quality `1080p60`. Frame SHA values were all unique.
- Ledger: 19 VERIFIED, 1 LOW_CONFIDENCE; 20/20 visible CVD exact, 19/20 visible
  OI exact, one readable OI left UNKNOWN, zero wrong VERIFIED CVD/OI on manual
  crop comparison. SQLite `integrity_check=ok`, 20 index rows.
- JSONL SHA-256: `a9f79ef0d7bbb629aebd90d5e5d0e63a73fc8a860de98688194c07619050215d`.
  Evidence directory outside Git:
  `C:\Users\asd\Bossman\owner-test-pack\aster-market-calibration-20260924`.
  It contains frames, crops, raw observations, SQLite WAL index, manual
  contact sheet, calibration summary, analysis JSON and local explanation.
- Last observation at `2026-09-24T17:44:23.327Z`: price 84,690;
  CVD 74.59B; OI 19.35B. Frame SHA:
  `024d1bafd26e5d91911e44e58d0a93348cae19795f90583d3317eb9e54a8c829`.
  CVD crop SHA: `4edc45ec869b24e5a4764f2bc425148fd5be5bbc504c09d79198c03f5e182f46`;
  OI crop SHA: `093344726c23d2466a00b9d1e11f2472f9c31a0f81aecf703867f01400d2947b`.
- The deterministic latest-pair analysis was
  `Price FLAT + CVD FLAT + OI FLAT → NEUTRAL_BALANCE`, with price delta 0,
  CVD delta −0.01B and OI delta 0. The local text model produced a Russian
  explanation. No levels were observed, so level-based scenarios stayed UNKNOWN.

## Remaining E2E gates

- The requested owner golden screenshot (CVD 75.32B, OI 19.52B) was not
  supplied as an accessible file. Its exact extractor test remains NOT RUN.
- Live Jev shadow cannot run: neither `BOSSMAN_JEV_API_KEY` nor
  `TYPESAFE_API_KEY` is present. The market pixel route remains deterministic
  `escalate_local_vision` and collection still works without Jev.
- Existing Companion config has one bound owner but no bot token available to
  this collector process. No Telegram delivery result or owner-phone receipt
  can be claimed.
- The specified 60-minute `run --cadence 15 --minutes 60`, crash recovery,
  restart during that run, and STOP exercise were not performed after the
  missing golden/owner-delivery gates. The 20-frame calibration is not a
  substitute for that run.

This test branch is an isolated candidate for the same Bossman backend and
Terminal Run control surface. Its North Star evidence remains at the existing
`SELF_REPAIR_SINGLE_CYCLE_PASS (coached)` historical checkpoint; this market
test does not demonstrate transfer gain, soak, week mode or revenue capability.
