# YouTube learning OSS hunt — pass 2 (2026-09-24)

This is an implementation shortlist, not a dependency lock. Bossman 1.0 release
is intentionally untouched.

## Tier A — reuse/port first

### WebDevBar/watch-video — MIT
Best immediately reusable local-first extraction reference found in pass 2.
It already implements:
- yt-dlp/local-file acquisition;
- local faster-whisper;
- first + scene-change + periodic frames;
- perceptual-hash frame deduplication;
- strict max-frame budgets;
- optional OCR, including a tuned mode for number-heavy dashboards;
- timeline.md interleaving frame + transcript + OCR;
- Windows-aware paths/tooling.

Bossman should port/adapt the generic extraction/dedup/timeline ideas and replace
Tesseract-as-truth with the stricter Qwen triple-read contract for financial
numbers. OCR text may nominate a candidate; the source frame remains evidence.

### Breakthrough/PySceneDetect — BSD-3-Clause
Use its ContentDetector/AdaptiveDetector rather than maintaining a home-grown
scene detector.

### SYSTRAN/faster-whisper — MIT
Default local ASR candidate.

### m-bain/whisperX — BSD-2-Clause
Optional precise word alignment. Use only where timestamp accuracy justifies
extra compute.

## Tier B — architecture to adapt

### RomGai/VideoStir — MIT
Useful for the *memory/retrieval* layer after ingestion. Its key idea is not to
flatten a long video into independent chunks: preserve a spatio-temporal clip
graph and retrieve multiple related moments. For trading this maps naturally to
"setup -> trigger -> reaction -> invalidation/outcome" even when those moments
are far apart.

### HKUDS/VideoRAG
Architecture worth studying: graph-driven knowledge indexing, hierarchical
context and adaptive multimodal retrieval over very long video collections.
License is dual/mixed, so treat implementation reuse cautiously and perform a
per-component license gate before copying code.

### Leon1207/Video-RAG-master
Research/reference architecture: retrieve visually aligned OCR + ASR auxiliary
text instead of forcing the VLM to consume every frame. This is attractive for
Bossman because exact chart text can be indexed cheaply and the VLM can inspect
only retrieved source frames. License was not verified in this pass: reference
only until cleared.

### city1517/OneClip-RAG
Interesting instruction-aware chunk/retrieval approach for hour-long video.
License was not verified in this pass: reference/benchmark only.

### facebookresearch/egagent
Useful research reference for agentic very-long-video understanding and
retrieval-recall evaluation. CC-BY-NC-4.0: do NOT copy into a commercial Bossman
runtime. Ideas/evaluation only.

## Proposed implementation deltas

### A. Timeline artifact
Every video should compile once into a stable local artifact:

```
video_id/
  manifest.json
  transcript.words.jsonl
  timeline.jsonl
  scenes.jsonl
  frames/
  ocr/
  episodes/
  retrieval/
```

Each timeline row:
```json
{
  "t": 1122.4,
  "frame_sha256": "...",
  "scene_id": "s014",
  "transcript": "...",
  "ocr_candidates": ["..."],
  "visual_cue": true,
  "source": "youtube:VIDEO_ID"
}
```

### B. Adaptive frame budget
Candidates = first/last + scene changes + periodic heartbeat + transcript visual
cues + explicit trading keywords ("CVD", "OI", "open interest", "dPOC",
"dVAH", "dVAL", "reclaim", "liquidation").

Then:
1. perceptual dedup;
2. keep cue/scene frames preferentially;
3. enforce max budget;
4. allow drill-down around an episode at denser sampling.

### C. Two-stage OCR
Stage 1 cheap OCR nominates text/ROI candidates.
Stage 2 local Qwen triple-read verifies financial numbers/labels.
No Tesseract/Paddle/EasyOCR numeric result becomes trusted market truth by
itself.

### D. Episode graph
Do not store only flat chunks. Link:
`claim -> setup -> trigger -> level interaction -> 15m -> 30m -> 60m outcome`.

This lets retrieval answer "show me previous dPOC reclaims with rising CVD and
falling OI" without loading entire videos.

### E. Retrieval before VLM
Index transcript, OCR candidates, deterministic regimes, levels and event tags.
At query/training time retrieve top episodes first, then ask Qwen to inspect only
the supporting source frames. This should reduce local vision calls materially.

### F. Reproducible eval
Metrics:
- transcript timestamp error;
- frame recall for manually marked important moments;
- dedup reduction ratio;
- CVD/OI exact accepted error rate;
- structural-level exact accepted error rate;
- event timing accuracy;
- 15/30/60m label correctness;
- retrieval Recall@K;
- promoted-lesson precision;
- inference seconds/video-minute;
- local calls/video-minute;
- estimated cloud calls avoided.

## Hard license rule

MIT/BSD/Apache: eligible after tests and attribution.
Mixed/unknown license: no code copying until cleared.
Non-commercial license: research/reference only for this commercial project.
