# Adaptive K1M6A Learning — Full Watch First, Then Learn to Skip

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
