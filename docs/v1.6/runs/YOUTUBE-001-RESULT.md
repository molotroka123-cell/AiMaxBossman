# YOUTUBE-001 — owner run, 2026-09-26

Status: **PARTIAL / NOT ACCEPTED**. No trading signal, promoted lesson, or unseen transfer is certified.

## Observed source and local evidence

- Source video: `https://www.youtube.com/watch?v=6pYfEfZof3c` (24/7 stream). The recording is about 6,314.67 seconds; the local MP4 contains video only, with separate valid PCM audio, subtitle files, and 40 sampled frames. Evidence stays outside Git at `C:\Users\asd\AppData\Local\Bossman\owner-run\youtube-001-live-20260925\6pYfEfZof3c`.
- The stream switches presenter and chart. Sample frame 10 shows BTC CME Futures 30m; frame 35 shows BTC/USD Coinbase 1D. These are distinct source series, so claims and outcomes cannot be joined merely because they appear in one video.
- A local vision attempt for frame 10 (`vision-frame-000010.json`) timed out at 180.05 seconds. It produced no verified chart observation.
- The ingest selector was corrected to request video plus audio when available. `yt-dlp --simulate` selected video format 136 plus audio 251. The actual existing MP4 remains video only; separate audio is present.
- Ingest now carries instrument, venue/feed, and timeframe identity into future-outcome attachment; missing or mismatched identity yields `UNKNOWN`. The focused regression passed 9 tests.

## Acceptance gap

There are no verified future outcomes, candidate cases, purged unseen transfer result, or completed bounded `2026-08-14..2026-08-27` batch in this run. The earlier three-role Nemotron pilot remains quarantined teacher material, not proof of learning. Source identity must be read from each chart segment when this 24/7 stream changes presenter or feed. Live trading remains disabled.
