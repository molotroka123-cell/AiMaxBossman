# YouTube Teacher — URL-only ingestion

Goal: the owner should only paste a public YouTube URL. Bossman does the rest locally.

Pipeline:

`YouTube URL -> validate allowlisted YouTube host -> yt-dlp metadata/captions/video -> local ASR fallback when captions are absent -> ffmpeg sampled frames -> local multimodal model -> typed trading observations -> Trader Apprentice deterministic analysis -> future-frame outcomes -> UNVERIFIED teacher casebook -> independent verification/promotion`

## Owner UX

The intended command is one argument:

```bash
python tools/youtube_trader_ingest_auto.py "https://youtu.be/VIDEO_ID"
```

No transcript, screenshots, downloaded file or timestamps are required from the owner.

The lower-level `tools/youtube_trader_ingest.py` is the visual/captions pipeline. `youtube_trader_ingest_auto.py` wraps it and adds automatic local speech-to-text when YouTube captions are unavailable.

## Runtime requirements

- `yt-dlp` available on PATH;
- `ffmpeg` available on PATH;
- an OpenAI-compatible **local multimodal** endpoint at `BOSSMAN_LOCAL_API_BASE` (default `http://127.0.0.1:8000/v1`);
- optional `BOSSMAN_LOCAL_VLM_MODEL`; if absent the tool asks `/v1/models` and selects the first available model;
- for videos without captions, an OpenAI-compatible local `/audio/transcriptions` endpoint; `BOSSMAN_LOCAL_ASR_MODEL` defaults to `whisper-1`.

If local ASR is unavailable and the video has no captions, Bossman still parses the visual/chart stream. Missing speech remains missing; it is never fabricated.

The downloader uses ordinary public YouTube access only. It does not bypass DRM, authentication or paywalls.

## What Bossman extracts

For every sampled frame it stores, when visible:

- reference/chart price and execution/tape price separately;
- CVD;
- open interest;
- long/short liquidations;
- buy/sell volume;
- dVAL, dPOC, dVAH, dOpen and other structural levels;
- nearby subtitle/local-ASR transcript text;
- the teacher's explicit claim, trigger and invalidation if stated;
- extraction confidence.

Unreadable values are `null`/`UNKNOWN`; an old value must never be copied forward just to fill a field.

## Why frames + transcript

Trading videos teach through both speech and the chart. Transcript-only ingestion loses CVD/OI/levels; frame-only ingestion loses the author's stated setup and invalidation. Each learning episode therefore joins a timestamped frame with nearby transcript text.

## Automatic labels

Consecutive compatible observations are passed into `learning.trader_apprentice.analyze()` so the model does not have to reinvent Price/CVD/OI semantics. Candidate episodes also receive future in-video price outcomes when a later frame exists (5m/15m/60m by default).

## Storage

Each URL receives its own local folder:

`data/trading/youtube_inbox/<video_id>/`

with at minimum:

- `manifest.json` — source/model/coverage/failure metadata;
- `candidate_cases.jsonl` — timestamped observations + deterministic regime labels + future outcomes;
- sampled frames;
- downloaded subtitle or local-ASR VTT when available.

This inbox is training evidence, not automatically trusted canonical memory.

## Trust boundary

YouTube is an **untrusted teacher**. A video's claims are stored with `learning_status=UNVERIFIED`. They are not promoted into canonical trading knowledge merely because the speaker sounds confident.

Promotion should require one or more of:

- the same video later shows the claimed outcome;
- an independent historical price/order-flow source verifies the outcome;
- the pattern repeats across independent episodes;
- a stronger verifier/human explicitly approves the lesson.

Never fine-tune directly on raw YouTube speech. Fine-tune only on filtered, outcome-labelled, verified episodes.

## Router rule

When a user supplies a YouTube URL in a trading-learning request, Bossman should invoke `tools/youtube_trader_ingest_auto.py` automatically rather than asking the user to download the video, make screenshots, transcribe it, or provide timestamps.
