# K1M6A bulk learning sprint — channel-scale ingestion

Owner sources:
- Streams archive: https://m.youtube.com/@k1m6a/streams?ra=m
- Example video: https://m.youtube.com/watch?v=fhqPMOWIwUQ&ra=m

Goal: compress roughly one week of teacher material into <= one day of machine
processing when hardware/network permit, and eventually process the archive back
through approximately one year. Speed never overrides evidence quality.

## Core insight

Do NOT run full Qwen vision over every second of every video. Split the work
into cheap parallel passes and expensive targeted passes.

### Pass A — inventory (very cheap)
Use yt-dlp flat playlist/channel metadata:
- video id/url/title/date/duration/live status;
- distinguish streams vs uploads where metadata permits;
- persist archive ids;
- skip already-completed ids on later runs.

This makes YouTube an archive/replay source and can complement Twitch. Do not
assume the YouTube replay is byte/timestamp-identical to the Twitch live stream;
store them as separate source identities and deduplicate only after evidence.

### Pass B — transcript first (cheap)
For every video in the batch:
1. native captions when available;
2. local faster-whisper fallback;
3. precise alignment only around candidate trading moments.

Transcript jobs may run in parallel subject to RAM/CPU/GPU limits.

### Pass C — candidate mining (cheap)
Search timestamped transcript for trading cues:
CVD, OI/open interest, dPOC/dVAH/dVAL/dOpen, liquidity, wick, sweep, reclaim,
retest, entry, stop, breakeven/BE, long, short, liquidation, absorption.

Combine with scene-change timestamps and periodic heartbeat frames.

### Pass D — targeted vision (expensive)
Qwen Vision sees:
- candidate timestamp;
- pre-window;
- trigger window;
- post-window;
- later outcome frames.

Use calibrated Price/CVD/OI/level extraction. Never run expensive VLM over
near-identical frames.

### Pass E — Pixel Perfect episode builder
For every candidate, store:
- decision-time evidence;
- teacher entry/stop/BE claim if explicit;
- wick/level geometry;
- Price/CVD/OI/liquidations;
- reclaim/retest/acceptance;
- 1m/5m/15m/30m/60m/4h future labels;
- MFE/MAE and R path;
- BE-policy simulations;
- win/loss/scratch/no-trigger.

### Pass F — verifier
Only outcome-labelled and independently checked episodes may be promoted.

## Parallelism design

Pipeline parallelism is safer than starting unlimited full-video VLM agents.

Suggested queues:
- inventory/download workers: configurable, network-bound;
- caption workers: high concurrency;
- ASR workers: hardware benchmark decides;
- scene/dedup workers: CPU pool;
- Qwen Vision workers: small bounded pool because 128GB unified memory is shared;
- deterministic episode/outcome workers: high concurrency;
- verifier: separate bounded pool.

Start conservatively, benchmark throughput/thermals/memory, then autotune.

## OSS patterns to reuse

- yt-dlp download archive: skip ids already processed on repeated channel runs.
- jona/scrub-cli: playlist ingestion, --parallel jobs, skip-existing, scene-aware visuals.
- baboonzero/youtube-channel-transcriber: channel database, batched downloads and bulk transcription architecture.
- faster-whisper: batched local ASR.
- PySceneDetect: scene candidates.
- watch-video / claude-video: frame budgets and dedup.

Do not copy code with unclear/incompatible licenses without a license gate.

## Throughput telemetry

For every batch record:
- source video hours;
- wall-clock time;
- download time;
- transcript time;
- scene/dedup time;
- Qwen vision time;
- videos/hour;
- source-hours processed per wall-hour;
- candidate episodes/hour;
- verified Pixel Perfect episodes/hour;
- peak RAM/VRAM;
- local calls;
- cloud cost (target zero for normal path).

Primary speed KPI:
`source_video_hours / wall_clock_hours`.

Target for "one week in one day" depends on how many source hours the week
contains. Bossman reports measured throughput rather than claiming the target
before benchmarking.

## Archive learning order

1. Most recent 7 days.
2. Previous 30 days.
3. Previous 90 days.
4. Backfill toward 1 year.
5. Continue automatically with new uploads/stream replays.

Recent material is processed first because it best matches the current chart
layout and market tooling. Older layout eras are tagged separately.

## Twitch vs YouTube

YouTube replay can become:
- historical training archive;
- crash recovery/backfill;
- post-session outcome verification.

Twitch remains useful for live low-latency monitoring.

When the same session exists on both:
- preserve both source ids;
- align by timestamps/content hashes where possible;
- deduplicate learning episodes;
- never count the duplicate as independent evidence.

## Safety/truth

The learner must include losses and no-setups. A curated YouTube archive can
have selection bias. "K1m6a almost never loses" remains a hypothesis until
measured across all visible candidate setups.
