# K1M6A 30% Precision Selector v2 — evidence-driven design

## Problem

The owner's working hypothesis is that roughly 30% of each K1M6A video contains
high-value trading education. A fixed "keep 30%" sampler is unsafe: rare entries,
losses, stop/BE decisions and short visual events can live inside the other 70%.

The correct objective is constrained optimization:

**minimize expensive VLM/video compute while maximizing recall of verified
high-value trading episodes.**

The retained fraction is an OUTPUT of the selector, not a quota.

## Research incorporated

### FOCUS — ICLR 2026
https://github.com/NUS-HPC-AI-Lab/FOCUS

FOCUS treats temporal clips as arms in an exploration/exploitation problem and
uses confidence bounds to preserve exploration of uncertain regions instead of
only selecting the current top scores. Reported long-video QA results use under
2% of video frames while improving accuracy on long videos.

Bossman adaptation:
- divide video into temporal arms;
- estimate value + uncertainty for each arm;
- allocate more samples to high-value OR uncertain arms;
- never eliminate an arm solely because its first cheap score is low.

### Q-Gate — ACM MM 2026
https://github.com/shaoguangwang/Q_Gate

Q-Gate fuses multiple experts rather than relying on one relevance signal:
visual grounding, global image-text matching and narrative/subtitle alignment.

Bossman adaptation: five independent expert scores:
1. transcript/teacher-language relevance;
2. visual/chart-change relevance;
3. numeric/OCR relevance;
4. order-flow/structure relevance;
5. novelty/contradiction relevance.

No single scorer can discard a segment.

### ReMem — ECCV 2026
https://github.com/jinlab-imvr/ReMem

ReMem adapts temporal granularity to the question and builds structural memory
over the video instead of static query-to-frame selection.

Bossman adaptation:
- coarse granularity for market-plan/context questions;
- fine granularity around entry, wick, stop, BE, reclaim and liquidation events;
- temporal memory links setup -> trigger -> management -> outcome.

### LENS — ECCV 2026
https://github.com/zhangce01/LENS

LENS allocates a fixed budget between spatial zoom-in and temporal zoom-out.

Bossman adaptation:
- spatial zoom-in for tiny CVD/OI/level labels and execution marks;
- temporal zoom-out for setup development and event ordering;
- do not use one image resolution/sampling rate for both jobs.

### ISSF / RSKP — temporal action localization
https://github.com/wuli55555/ISSF
https://github.com/LeonHLJ/RSKP

Relevant idea: salient changes between neighboring snippets and representative
snippet propagation help discover action intervals from weak supervision.

Bossman adaptation:
- compute neighbor-change score;
- expand a detected high-value center into its temporal boundaries;
- learn recurring representative snippets from FULL_WATCH_GOLD.

### Diversity + representativeness video summarization
Research literature shows a useful compact summary should not merely take the
highest-scoring near-duplicate frames; selection should preserve both
representativeness and diversity.

Bossman adaptation:
- cluster near-duplicate chart states;
- select medoids/representatives;
- add unique outliers/contradictions;
- preserve temporal coverage.

## Selector v2

Split a video into 15-30 second coarse arms first.

For each arm compute cheap features:

### Expert A — language
- entry/entered/long/short;
- stop/BE/breakeven;
- CVD/OI;
- dPOC/dVAH/dVAL/dOpen;
- sweep/reclaim/retest;
- liquidation/absorption;
- teacher uncertainty/correction language.

### Expert B — visual change
- scene change;
- chart layout appears/disappears;
- large candle/wick change;
- position/order overlay appears;
- new drawn level/annotation;
- DOM/order-flow pane movement.

### Expert C — numeric evidence
Cheap OCR is candidate generation only:
- new price numbers;
- CVD/OI changes;
- level labels;
- position/entry/stop text.

Trusted numeric values still require calibrated Qwen verification.

### Expert D — market structure
From already verified observations:
- regime change;
- level proximity/cross;
- CVD/OI jump;
- liquidation event;
- divergence/inefficiency candidate.

### Expert E — novelty
Compare against existing promoted lessons:
- duplicate explanation -> lower expensive budget;
- contradiction/exception -> HIGH priority;
- unseen combination -> HIGH priority.

## Fusion

Do not use a plain average.

Use a union-preserving score:
- high confidence from ANY safety-critical trading expert can promote an arm;
- consensus raises priority further;
- disagreement raises uncertainty and therefore inspection.

Conceptually:

`priority = relevance + uncertainty_bonus + novelty + boundary_bonus`

A low transcript score cannot cancel a strong visual/numeric event.

## Exploration reserve

Reserve part of the expensive budget for exploration of apparently LOW segments.

Initial recommendation for calibration:
- 70% of expensive budget -> highest value arms;
- 20% -> uncertain/disagreeing arms;
- 10% -> deterministic audit sample from LOW arms.

These are starting research parameters, not permanent constants.

If false-skip recall is poor, automatically increase exploration.

## Boundary expansion

A detected moment is not processed alone.

For every high-value center T:
- coarse pre-window: T-120s .. T-30s;
- dense pre-window: T-30s .. T;
- trigger: T +/- 10s at highest useful density;
- management: T .. T+120s;
- outcome: sparse 1m/5m/15m/30m/60m/4h labels.

This prevents the selector from capturing the teacher saying "I entered" while
missing the setup that made the entry meaningful.

## Multi-resolution vision

### Spatial detail mode
Use high-resolution ROI crops for:
- CVD/OI;
- price axis;
- dPOC/dVAH/dVAL/dOpen;
- order/position overlays.

### Temporal context mode
Use contact sheets/hyperframes or low-resolution frames for:
- event sequence;
- setup progression;
- repeated failed attempts.

Do not spend full-resolution VLM tokens on every context frame.

## FULL_WATCH_GOLD labels

For the first gold videos, annotate not only HIGH/MEDIUM/LOW but event spans:

- SETUP_CONTEXT
- LEVEL_DEFINED
- SWEEP
- ENTRY_TRIGGER
- ENTRY_VISIBLE
- STOP_DEFINED
- BE_MOVED
- MANAGEMENT
- EXIT
- LOSS
- SCRATCH
- NO_TRIGGER
- POST_TRADE_REVIEW
- NON_TRADING

This gives the selector temporal boundaries rather than isolated "interesting"
frames.

## False-skip learning

Every audited LOW segment that contains a missed useful event becomes:

`FALSE_SKIP(type, cues_missing, expert_scores, video_layout, timestamp)`.

Maintain a false-skip taxonomy:
- transcript miss;
- visual-only event;
- OCR miss;
- new teacher phrase;
- unusual chart layout;
- very short event;
- contradiction;
- selector boundary too narrow.

Improve the responsible expert rather than globally making all sampling denser.

## Evaluation

Do NOT evaluate by "percentage of video watched".

Primary:
- HIGH_VALUE episode recall;
- explicit ENTRY recall;
- STOP/BE recall;
- Pixel Perfect recall;
- LOSS/SCRATCH recall;
- false-skip rate.

Secondary:
- VLM calls avoided;
- source-hours / wall-hour;
- frames inspected;
- energy;
- duplicate lesson rate.

Promotion gate for selector v2:
- >=98% HIGH_VALUE recall on held-out FULL_WATCH_GOLD;
- 100% explicit ENTRY/STOP/BE recall in the gold evaluation set;
- no known systematic miss category;
- measured compute reduction vs full-watch baseline.

## Progressive target

Stage A: 100% broad watch.
Stage B: selector shadows full-watch; it does not save compute yet.
Stage C: selector may skip expensive VLM but audits 20% of skipped arms.
Stage D: reduce audit toward 10% only after recall evidence.
Stage E: adaptive budget per video.

If the resulting expensive coverage is ~30%, good. If reliable recall requires
42%, use 42%. If a promotional video needs only 7%, use 7%.

**30% is not the target. Information recall per unit compute is the target.**
