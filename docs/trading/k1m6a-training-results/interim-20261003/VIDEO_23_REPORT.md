# Video #23 audit — August 1st! We Ready For The Month! Bitcoin Setups!

**Date:** 2025-08-01 · **ID:** `RdR-z4nQN7k` · **Result:** local processing complete; learning claims remain unverified.

## Work performed locally

- Source video: 25:14.5, VP9/Opus, 1280×720; original `video.webm` and subtitles kept. The remuxed `video.mp4` is a separate derived file.
- Local ASR: faster-whisper-small, CPU int8, 4 threads; 361 timestamped segments in 112.6 seconds. Transcription model was already cached; no weights downloaded and no cloud endpoint used.
- Lesson extraction: `bossman-community-qwen-uncensored:latest`, 13 windows of 120 seconds, 22 timestamped candidates accepted and 17 rejected, 199.3 seconds cumulative model latency. Candidate quotes are labeled by source; none promoted.
- Frame selection: 25 semantic frames from transcript/scenes (not evenly spaced), contact-sheet reviewed; all 25 image files open. Local vision model returned 22 observations and 3 errors/timeouts. Successful-call time totals 730.0 seconds (timeouts are excluded).

## Evidence about usefulness and limits

The audio plus source-labeled captions produced a navigable, timestamped record. The strongest concrete quality finding is a corrected OCR error: in `smart_011_00659.jpg`, the model called the OHLC open (114,471.51) the current price; the right-side current marker is 114,573.28. This demonstrates why the evidence ledger and manual checks are useful. It does **not** prove improved market prediction or profitable learning.

The frame sweep found repeated views of the same chart, visible compression artifacts, and channel graphics at the end. Twenty-five frames are not twenty-five independent examples. The 22 accepted lesson candidates contain generic risk/structure ideas mixed with dated forecasts and noise; keep every item `UNVERIFIED_TRANSCRIPT_CANDIDATE`.

No closed-book recall test, independent market-outcome check, or backtest was run. Do not promote specific prices, Fed forecasts, or the speaker’s personal bias to trading rules.

## Failures and provenance

An initial attempted ingest routed images to the text-only `bossman-community-qwen-uncensored:latest` model and failed on 24 sampled frames. That raw `manifest.json` is preserved as the failed attempt. The corrected pass used the installed vision-capable `bossman-fast-qwen36-vision:latest` against semantic frames. Three of 25 requests timed out; raw output and manual correction remain separate. No model weights changed.

## Queue state

#23 is retained as `IN_PROGRESS` in the queue until the complete 30-video daily audit is closed. #5 remains without a local source video; #22 still needs its audio disagreements and visual coverage completed. See `study_sheet.md` and `processing_audit.json` for all item-level data.
