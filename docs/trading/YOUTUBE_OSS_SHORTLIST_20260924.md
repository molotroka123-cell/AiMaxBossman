# YouTube learning OSS shortlist — 2026-09-24

Purpose: avoid rebuilding generic video ingestion. This document is for the
isolated market-learning branch, not Bossman 1.0 release.

## Recommended building blocks

### 1. fuleinist/video-to-skill — MIT
Use as architecture/reference for URL -> transcript/Whisper/VLM -> topic
segments -> compact reusable knowledge. Valuable idea: compile once into
on-demand segments instead of injecting an entire transcript on every query.
Do not keep its Claude-specific final stage; Bossman should feed local models.

### 2. coah80/youtube-mcp — MIT
Strongest direct reference for timestamp-aligned frame + transcript ingestion.
Useful patterns:
- scene-change frames;
- transcript visual-cue timestamps ("look here", "as you can see");
- 3-minute segmented processing;
- scene overview / targeted frame drill-down;
- dense frame<->spoken-text interleaving.
Port/adapt the algorithms, not an MCP dependency unless needed.

### 3. bradautomates/claude-video — MIT
Useful extraction engineering:
- captions-first;
- keyframe/scene-change/uniform fallback modes;
- frame budgets by duration;
- near-duplicate frame filtering;
- first/last coverage;
- targeted timestamps.
Replace Claude handoff with Bossman typed episode generation.

### 4. SYSTRAN/faster-whisper — MIT
Preferred local ASR baseline. Batch transcription, timestamps and quantized
execution are more appropriate than calling paid Whisper for every video.

### 5. m-bain/whisperX — BSD-2-Clause
Optional second-stage alignment when exact spoken-word timestamps matter.
Provides word-level alignment and diarization. Do not make diarization a hard
dependency for single-speaker trading videos.

### 6. Breakthrough/PySceneDetect — BSD-3-Clause
Mature scene detection. Candidate replacement/verification layer for ad-hoc
fixed-interval frame sampling. AdaptiveDetector is especially relevant for
screen recordings with motion.

### 7. QwenLM/Qwen2.5-VL / qwen-vl-utils
Reference for native video ingestion, dynamic FPS, temporal grounding and video
OCR. Evaluate locally against the installed Qwen vision stack before changing
models.

### 8. minseokii/OTT-Vid
Experimental optimization only. It can retain a small fraction of video tokens
for Qwen2.5-VL/LLaVA-style video inference. Benchmark before adoption; never
trade away numeric chart OCR accuracy for token savings.

### 9. EvolvingLMMs-Lab/lmms-eval
Use to build a reproducible local video-model evaluation lane instead of
choosing a VLM by anecdotes.

## Missing pieces in Bossman before "learning from YouTube" is real

1. **Timestamp synchronizer** — one canonical clock joining transcript words,
   frames, chart observations and future outcomes.
2. **Adaptive frame sampler** — scene cuts + visual-language cues + periodic
   heartbeat + dedupe. Fixed interval alone wastes inference and can miss the
   exact chart transition.
3. **Chart ROI detector** — locate price pane, CVD/OI panes and named levels
   even when the creator changes layout/resolution.
4. **Level calibration** — dPOC/dVAH/dVAL/dOpen needs a reviewed crop suite with
   zero wrong VERIFIED values before live use.
5. **Multi-horizon episode builder** — 15m/30m/60m before/after state around a
   teacher claim.
6. **Claim extractor** — distinguish observation, hypothesis, trigger,
   invalidation, target and retrospective explanation.
7. **Outcome verifier** — what actually happened after 15/30/60m; teacher
   confidence is never a label.
8. **Leakage guard** — a frame from the future must not enter the features used
   to classify the earlier setup.
9. **Dedup/near-duplicate lessons** — do not learn the same clip/re-upload ten
   times as independent evidence.
10. **Source identity** — channel/video/timestamp/frame hash/model version and
    extractor version on every lesson.
11. **Quarantine/promote state machine** — RAW -> UNVERIFIED ->
    OUTCOME_LABELLED -> VERIFIED/PROMOTED or REJECTED.
12. **Contradiction memory** — store when two teachers/cases disagree instead of
    averaging them into fake certainty.
13. **Benchmark pack** — hand-labelled clips for OCR, levels, event timing,
    transcript alignment and outcome labels.
14. **Cost telemetry** — video minutes, frames inspected, local model calls,
    wall time, energy estimate and avoided cloud calls.
15. **Replay test** — the same saved video must produce materially identical
    typed observations after restart/version changes or explicitly flag drift.

## Proposed Bossman pipeline

YouTube URL
-> yt-dlp metadata/captions
-> faster-whisper fallback
-> optional WhisperX alignment
-> PySceneDetect + cue timestamps + dedupe
-> local Qwen vision
-> Price/CVD/OI + structural levels
-> 15/30/60m deterministic state
-> teacher claim schema
-> future outcome labels
-> UNVERIFIED episode
-> independent verifier/replay
-> promoted CASE/skill

## Adoption rule

Prefer permissive MIT/BSD/Apache components. Pin versions and keep attribution.
Every external component gets a local fixture test before becoming required.
No raw YouTube claim is allowed to update trusted trading memory directly.
