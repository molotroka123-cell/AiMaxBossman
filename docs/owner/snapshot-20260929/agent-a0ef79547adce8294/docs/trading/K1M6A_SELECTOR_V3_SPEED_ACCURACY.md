# K1M6A Selector v3 — target >=50% faster and >=10% more accurate

## Definition first

These are **acceptance targets**, not claims already achieved.

Speed target:
`wall_clock_v3 <= 0.50 * wall_clock_v2`
for the same held-out video batch and same hardware state.

Accuracy target:
`quality_score_v3 >= 1.10 * quality_score_v2`
where quality is a pre-registered composite dominated by recall of rare trading
events, not generic video QA.

No merge/promotion if either target is missed.

## Why speed and accuracy can improve together

The v2 selector still wastes compute in three places:
1. re-decoding/re-processing the same video for multiple passes;
2. using expensive vision on redundant temporal context;
3. using the same spatial resolution for tiny chart numbers and broad temporal
   understanding.

v3 separates these workloads.

## Technique 1 — decode once, fan out many consumers

Create one local media cache per video:
- normalized video;
- audio;
- transcript;
- scene timestamps;
- low-res frame embeddings/contact frames;
- high-res source frame access by timestamp.

Download/decode once. ASR, scene detection, OCR candidates and selector consume
the same cached artifacts.

Expected effect: remove repeated ffmpeg/yt-dlp decode/download work.

## Technique 2 — asynchronous pipeline overlap

Do not process Video A end-to-end before Video B.

Pipeline:
`download N+2 || ASR N+1 || cheap-select N || Qwen N-1 || verify N-2`.

Use bounded queues/backpressure. Qwen workers remain bounded by unified-memory
benchmark; CPU/network stages may run wider.

Measure stage utilization and queue wait separately.

## Technique 3 — two-resolution vision

Research inspiration:
- LENS: allocate budget between spatial zoom-in and temporal zoom-out.
- NVILA/VILA-HD: efficiency/accuracy frontier and detail-aware visual processing.
- VideoLLaMA3: controllable FPS/max-frame visual input.

Bossman:
- low-res temporal frames/contact sheets for event sequence;
- high-res ROI only for price/CVD/OI/levels/position overlays;
- never send a full 1080p frame to the expensive numeric reader when a verified
  ROI is sufficient.

This should both reduce visual tokens and improve tiny-number accuracy.

## Technique 4 — coarse-to-fine temporal search

Pass 1: 30-60s arms with cheap transcript/scene/embedding features.
Pass 2: only promising/uncertain arms split into 5-10s windows.
Pass 3: trigger zones sampled densely around +/-10s.

This replaces dense inspection of an entire 40-60 minute stream.

FOCUS-style uncertainty prevents early coarse filtering from deleting rare
events.

## Technique 5 — retrieval before VLM

Build a cheap index over:
- transcript chunks;
- OCR candidates;
- scene embeddings;
- known K1M6A cue phrases;
- deterministic market tags.

Only retrieve candidate intervals into Qwen. Preserve exploration/audit samples.

## Technique 6 — semantic cache

Cache by:
`video_id + time_window + frame_sha + extractor_version + model_version + prompt_version`.

Reuse:
- transcript;
- frame descriptions;
- verified OCR/metrics;
- embeddings;
- selector scores;
- episode features.

Never rerun an unchanged expensive inference.

## Technique 7 — teacher-profile prior, never hard filter

After FULL_WATCH_GOLD learn:
- phrases before entries;
- chart layouts;
- typical management language;
- recurring promo/non-trading blocks.

Use as a prior that adjusts priority. It may never veto a strong visual/numeric
event.

## Technique 8 — ensemble only on uncertainty

Do NOT run multiple VLMs/readers on every segment.

Easy/clear segment:
one cheap selector.

Financial numeric ROI:
existing triple-read calibrated Qwen contract.

Ambiguous high-value episode:
second local verifier/model.

This spends redundancy where it improves accuracy.

## Technique 9 — temporal boundary propagation

Once an entry/BE/stop center is found, infer its surrounding event interval and
reuse neighboring evidence instead of rediscovering each frame independently.

Store episode graph:
`context -> setup -> trigger -> management -> outcome`.

## Technique 10 — hard-negative accuracy curriculum

Accuracy will not improve 10% merely by sampling less.

Build hard-negative sets:
- wick that looked Pixel Perfect but failed;
- reclaim that immediately failed;
- CVD/OI move with no price response;
- promotional "trade" language;
- historical chart review mistaken for live entry;
- repeated replay/duplicate stream;
- visually similar dPOC/dVAL labels;
- B/8 OCR confusions.

Train/evaluate selector specifically on these confusing examples.

## Quality metric

Use weighted event recall + numeric correctness + false-positive penalty:

- 25% Pixel Perfect event recall
- 15% explicit ENTRY recall
- 10% STOP/BE recall
- 10% LOSS/SCRATCH recall
- 15% Price/CVD/OI exact accepted correctness
- 10% structural-level exact accepted correctness
- 5% temporal-boundary accuracy
- 5% duplicate suppression
- 5% false-positive control

Critical gate overrides score:
**zero tolerated wrong VERIFIED financial numbers in calibration.**

"10% more accurate" means >=10% relative improvement in this frozen composite
against v2 on the same held-out gold set. Do not redefine the metric after
seeing results.

## Benchmark protocol

1. Freeze FULL_WATCH_GOLD train/dev/holdout videos.
2. Run v2 on holdout, record quality + wall time.
3. Clear only transient process state, preserve legitimate media cache policy
   consistently for both systems.
4. Run v3 on the exact same holdout/hardware.
5. Repeat at least 3 times; compare median wall time.
6. Report per-stage times and quality components.
7. Promotion requires BOTH:
   - >=2.0x throughput / >=50% wall-time reduction;
   - >=1.10x frozen quality score.
8. If quality improves but speed misses, optimize pipeline.
9. If speed improves but quality misses, increase uncertainty/exploration or
   improve hard-negative/ROI verification.
10. Never claim target met from estimated savings.

## Progressive rollout

v3-SHADOW: runs next to full-watch and v2.
v3-CANDIDATE: may skip expensive inference but full-watch audits selected videos.
v3-PROMOTED: only after benchmark gates.
