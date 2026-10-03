# K1m6a — 24-hour live gap study

Status: **PREPARED, NOT STARTED**. Start only after the dated-video queue is closed and every included item has a Pult report. The study is read-only and collects public stream evidence. It cannot place or manage trades.

## Purpose

Use the 30-video batch to identify unanswered questions in K1m6a's method, then compare those questions with fresh live chart observations. This is a coverage audit, not proof of a profitable strategy. A repeated claim, a memory match, or a readable number is not a verified edge.

## Existing local capability

The existing `bcc.market.collector` watches the public Twitch channel `k1m6a`, samples chart fields, stores fresh frame/crop evidence and emits quality statuses. It has a durable ledger and `STOP` file. It does **not** transcribe speech or capture a searchable 24-hour audio transcript. Do not present chart-only collection as a completed study of K1m6a's spoken teaching.

The current host has `faster-whisper` and local `small`/`tiny` model snapshots. `tools/k1m6a_transcribe_chunks.py` can transcribe already-captured local audio chunks without downloading a model. It emits `UNVERIFIED_ASR` segments; it does not capture live audio. Each capture segment needs its start time in UTC, SHA-256, duration and, if it must be joined to chart frames, a measured audio/video delay. Missing clock calibration stays unaligned.

## Stage gate and run

1. Close every included dated highlight in `AppData\Local\Bossman\CommandCenter\learning\k1m6a-youtube\queue.json` and deliver its audit to Pult. An owner-confirmed exclusion must be explicitly recorded as `EXCLUDED_BY_OWNER_CONFIRMED`; an unresolved item does not count as closed.
2. Check disk space and available memory. Reserve at least 10 GB free disk and 8 GB available RAM. Confirm the expected channel is live and the collector status has no active PID. Calibrate readable 1080p60 chart fields before the 24-hour run.
3. Run read-only numeric sampling from `command-center`:

   ```powershell
   $marketRoot = Join-Path $env:LOCALAPPDATA 'Bossman\CommandCenter\market-data\twitch\k1m6a-live-study'
   python -m bcc.market.collector run --cadence 30 --offline-cadence 180 --minutes 1440 --root $marketRoot
   ```

   Stop at any time with:

   ```powershell
   python -m bcc.market.collector stop --root $marketRoot
   ```

   Status and export:

   ```powershell
   python -m bcc.market.collector status --root $marketRoot
   python -m bcc.market.collector export --root $marketRoot
   ```

4. If a separately authorized local audio recorder has produced chunks, create `audio-chunks.jsonl` beside those files. One JSON object per chunk:

   ```json
   {"chunk_id":"part-0001","path":"part-0001.m4a","started_at_utc":"2026-10-04T00:00:00Z","duration_s":300,"video_audio_sync_offset_s":null}
   ```

   Transcribe from an existing model directory, for example the host's local `faster-whisper-small` snapshot:

   ```powershell
   python tools/k1m6a_transcribe_chunks.py <audio-folder>\audio-chunks.jsonl `
     --model-path $env:USERPROFILE\.cache\huggingface\hub\models--Systran--faster-whisper-small\snapshots\536b0662742c02347bc0e980a01041f333bce120 `
     --out <audio-folder>\transcript.jsonl --device cpu --compute-type int8
   ```

   If the folder, model files or capture timestamps are absent, report `AUDIO_NOT_CAPTURED` or `ASR_NOT_RUN`; never substitute cloud ASR or fabricate lines.
5. Build the gap report:

   ```powershell
   python tools/k1m6a_live_gap_audit.py `
     --root (Join-Path $env:LOCALAPPDATA 'Bossman\CommandCenter\learning\k1m6a-youtube') `
     --market-root $marketRoot
   ```

## What the gap audit checks

- Which video folders have transcript, visual audit, claims and manually reviewed notes.
- How often setup conditions, trigger, invalidation, risk, asset and timeframe are explicitly specified in retained notes.
- Which market observation fields are absent or unreadable in the live ledger, by quality status, symbol and timeframe.
- Whether the 30-item source queue is actually closed.

It does not assert that a missing note means the teacher never covered a topic. It does not join old highlight timestamps to today's market, merge duplicated replay footage as independent samples, promote raw claims, or tune model weights.

## Follow-up after the 24-hour capture

Review failures/unreadable states and transcript coverage first. Turn each measured gap into a question for a source replay: e.g. exact level identity, indicator type, timeframe, entry trigger, invalidation, size/risk, counterexample, and post-trade outcome. Keep teacher statements, chat statements, chart readings and independently verified outcomes in separate fields. Promote a reusable lesson only after evidence review and an independent check.

## Readiness limitations

- Audio capture is not implemented by the market collector; a chart-only soak will not capture verbal methodology.
- Continuous recording/ASR has not been launched or soak-tested. The presence of this runbook and offline transcriber is not a `24H_SOAK_PASS`.
- Twitch and highlight replay clocks can differ. Require a measured sync offset before multimodal joins.
- Current video evidence is still unverified; no canonical trading memory or model weights were changed by this preparation.
