# Adaptive K1M6A Learning — Full Watch First, Then Learn to Skip

## 2026-10-03 owner instruction: deep autonomous study

The owner should not have to sit through videos or supervise each processing
step. Process one video at a time until the full-watch baseline and review
workflow have demonstrated useful recall. Reserve meaningful wall time for
each video; do not optimize for rapid queue completion.

For every video, Bossman must complete this study loop locally:

1. Save source metadata, the best available public caption track, the local
   model identities, and a checksum of every input artifact.
2. Read the complete transcript in overlapping time windows. Keep exact quotes
   and video-relative timestamps. A second local pass classifies teaching,
   dated opinion, worked example, and noise; rejected quotes are retained as
   extraction diagnostics.
3. Build broad chart coverage across the whole video, then add dense frames
   around candidate setups, level changes, invalidations, and post-trade
   review. Prefer changed-screen/scene samples plus transcript anchors; never
   let a transcript-only pre-scan suppress unexamined material during the
   golden full-watch phase.
4. Read sampled charts with the local vision model. Record unreadable values
   as null. Manually compare a subset of the actual frame pixels to the vision
   output and document ticker, venue, timeframe, panel, and overlay mistakes.
5. Reconcile high-value spoken claims against neighboring transcript and
   chart frames. Relative percentages, incomplete phrases, chat messages, and
   giveaways must not become precise price levels or teacher rules.
6. Write a study sheet: setup context, observable trigger, invalidation,
   risk/size management, outcome evidence, counterexample/uncertainty, and
   what Bossman would need to see before acting. Missing information stays
   explicitly missing.
7. Run a closed-book local retrieval check: hide the source notes, ask the
   model to reconstruct a few candidate ideas with evidence timestamps, then
   compare every answer against the source. Store misses and corrections as
   learning diagnostics, not as confirmed market truths.
8. Produce a per-video audit, source/frame hashes, runtime, and selected chart
   images for the authorized Pult. Only after successful delivery mark the
   queue item reported.

Use only local Bossman for study/inference. Jev is permitted only when its
already-authorized function is necessary; do not route study to paid/cloud
fallbacks. Raw teacher material remains unverified. No fine-tuning or canonical
rule promotion follows from a single video or from a model's own quiz answer.

### Free open-source tools considered for accuracy

- FFmpeg is already used for deterministic frame extraction. PySceneDetect is
  a possible scene-change pre-scan, but chart streams often keep one static
  layout while the plot moves; scene changes are therefore extra frame anchors,
  never a substitute for broad temporal sampling. Repositories:
  [FFmpeg](https://github.com/FFmpeg/FFmpeg),
  [PySceneDetect](https://github.com/Breakthrough/PySceneDetect).
- The local `faster-whisper` package/model can provide a second transcript
  where captions are absent or suspect. Its output must be aligned and checked
  against the original captions/audio; do not silently replace teacher quotes.
  Repository: [SYSTRAN/faster-whisper](https://github.com/SYSTRAN/faster-whisper).
- WhisperX word alignment can improve word timing, but its extra model and
  dependency footprint should be justified by a local quality comparison
  before installation. Never download model weights automatically.
  Repository: [m-bain/WhisperX](https://github.com/m-bain/whisperX).

The current host has `faster_whisper` and FFmpeg, but not PySceneDetect,
OpenCV, or WhisperX (checked 2026-10-03). Do not claim those are integrated.
Evaluate additions on a fixed, manually reviewed set: quote exactness,
chart-anchor coverage, timestamp error, missed high-value episodes, runtime,
and disk/RAM/GPU cost. Keep the existing pipeline as baseline. Adopt a tool
only when the local before/after comparison shows improved evidence quality
without a recall regression.

## Owner objective

Assume only ~30% of each video contains high-value trading material. Bossman
must first learn what "useful" actually looks like by watching complete videos,
then progressively reduce expensive processing while preserving recall of the
best teaching moments.

The optimization target is **not maximum speed**. It is:

`maximum verified learning value / wall-clock minute`

subject to a hard recall floor for important trading episodes.

## Phase 0 — Golden full-watch set

Start with a representative recent set (for example 10-20 streams/videos).

For these videos Bossman performs the expensive baseline:
- complete timestamped transcript;
- broad scene coverage;
- periodic frames across the entire duration;
- targeted dense frames around chart changes;
- local Qwen vision over enough frames to establish ground truth;
- Price/CVD/OI/levels;
- teacher claims;
- Pixel Perfect candidates;
- negative/no-setup moments;
- future outcomes.

This becomes the **FULL_WATCH_GOLD** dataset.

Nothing is skipped merely because a heuristic thinks it is boring.

## Phase 1 — Learn usefulness

Every segment receives labels such as:

HIGH_VALUE:
- explicit entry/exit;
- stop/BE management;
- Pixel Perfect setup;
- CVD/OI explanation;
- level explanation;
- sweep/reclaim/retest;
- liquidation/absorption;
- post-trade review;
- losing setup / invalidation;
- macro-event trading lesson.

MEDIUM_VALUE:
- broader market plan;
- contextual structure;
- relevant news/macro;
- preparation of levels.

LOW_VALUE:
- intro/outro;
- NFT/promotion;
- unrelated conversation;
- repeated explanation already captured;
- static screen with no new information.

LOW_VALUE is still retained in the golden set so the selector learns negatives.

## Phase 2 — Cheap pre-scan

For new videos:

1. ingest metadata/captions;
2. local ASR when needed;
3. scene-change map;
4. cheap OCR/text scan;
5. low-cost thumbnail/contact-sheet pass;
6. segment the whole timeline.

A lightweight local model scores every segment:
`usefulness_score 0..1`.

It must also emit reasons/tags, not only a score.

## Phase 3 — Adaptive sampling

HIGH score:
- dense frames;
- full local VLM;
- pre/trigger/post windows;
- outcome extraction.

MEDIUM:
- normal scene/cue frames;
- VLM only if evidence changes.

LOW:
- sparse heartbeat only;
- no expensive VLM unless random audit selects it.

Always retain:
- first/last coverage;
- random audit samples from skipped material;
- transcript for searchable recovery.

## Phase 4 — Recall audit

The selector is compared against FULL_WATCH_GOLD.

Primary metric:
`important_episode_recall`.

Secondary:
- Pixel Perfect recall;
- losing-setup recall;
- level/CVD/OI lesson recall;
- frames avoided;
- VLM calls avoided;
- wall-clock saved.

Do not optimize precision by throwing away useful moments.

Initial promotion target:
- >= 98% recall of HIGH_VALUE episodes on held-out full-watch videos;
- 100% recall target for explicit entry/stop/BE mentions;
- zero known systematic blind spot.

If recall falls, sampling becomes denser automatically.

## Phase 5 — Active learning

Bossman learns from misses.

When random audit discovers a useful skipped segment:
1. mark FALSE_SKIP;
2. store why it was missed;
3. add it to selector training/evaluation;
4. update cues/features;
5. replay held-out videos;
6. promote new selector only if recall improves without a new blind spot.

## Phase 6 — Progressive compression

Only after measured recall is high:

Generation A:
100% transcript + broad frames + dense VLM.

Generation B:
100% transcript + scene/cue frames + ~50% VLM workload.

Generation C:
100% transcript + learned candidate frames + random audit + ~30% VLM workload.

Generation D:
adaptive per-video workload based on uncertainty.

The 30% number is a hypothesis, not a forced quota. A high-value stream may
deserve 70%; a mostly promotional video may deserve 5%.

## Uncertainty routing

If selector confidence is low, process MORE, not less.

`uncertain -> dense local inspection`

This prevents the optimization system from becoming confidently blind.

## Duplicate knowledge

A segment can be informative but redundant.

Store:
- novelty_score;
- nearest existing lesson;
- same explanation count.

Repeated explanations may use cheaper processing after the first verified
examples, while exceptions/contradictions remain high priority.

## Teacher-specific adaptation

Maintain a K1M6A cue profile learned from verified videos:
- phrases preceding entries;
- phrases around BE/stop movement;
- recurring chart layouts;
- typical sequence before Pixel Perfect entries;
- recurring non-trading sections.

The profile may improve sampling, but never becomes truth about market outcome.

## Final fast path

```
video
 -> transcript/ASR for full searchable coverage
 -> scene map
 -> cheap segment scorer
 -> HIGH/MEDIUM/LOW
 -> adaptive frame budget
 -> Qwen only where valuable/uncertain
 -> episode builder
 -> outcome verifier
 -> random skipped-segment audit
 -> selector learning
```

Thus Bossman first learns by watching broadly, then learns **where to look**.
