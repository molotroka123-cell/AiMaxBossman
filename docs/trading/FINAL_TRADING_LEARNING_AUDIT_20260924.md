# FINAL AUDIT — Trading Learning / K1M6A / Pixel Perfect (2026-09-24)

Branch audited: `feat/bossman-1.5-economy-learning-20260924`

## Executive verdict

Architecture is promising but **NOT READY to claim a trading edge**. The code
already contains good anti-lookahead, paper-only and evidence gates, but several
statistical traps can still produce a strategy that looks excellent and is
mathematically false.

This document freezes the traps and required fixes before promotion.

## P0 — statistical correctness

### 1. Purged / embargoed walk-forward split
Current simple chronological split can leak information when episodes overlap in
time or share future outcome windows.

Required:
- group episodes by source session/day;
- purge training examples whose label horizon overlaps validation/test;
- embargo a configurable period around split boundaries;
- final untouched holdout by date, not random episode.

### 2. Multiple-testing / strategy-mining bias
Bossman will test many combinations of CVD/OI/levels/BE rules. The best backtest
will be biased upward simply because many variants were tried.

Required:
- immutable experiment ledger;
- count every tried hypothesis, including failures;
- one untouched final holdout;
- bootstrap confidence interval for expectancy;
- apply a multiple-testing-aware threshold / false-discovery control before
  calling an edge robust;
- never repeatedly inspect the final holdout while tuning.

### 3. Dependence / effective sample size
Ten setups from one FOMC stream are not ten independent trades.

Required:
- cluster by day/session/event/regime;
- report both raw N and effective/clustered N;
- cluster-bootstrap by session/day;
- cap confidence contribution from near-duplicate YouTube/Twitch episodes.

### 4. Intrabar ambiguity
OHLC cannot prove whether stop or target was hit first inside one candle.
Existing backtest correctly chooses stop first when both are touched. Preserve
this pessimistic rule unless tick data proves ordering.

### 5. Pixel Perfect fill realism
A wick-touch entry can be impossible or materially worse in reality.

Required:
- bid/ask/spread when available;
- limit-order queue/fill uncertainty;
- configurable missed-fill probability;
- latency;
- slippage;
- stop gap-through;
- partial fills;
- markout after signal;
- compare ideal touch vs executable fill.

A setup that only works with perfect wick fills is rejected.

### 6. BE path dependence
BE simulations must use sequential path data. Never infer BE success from final
candle high/low alone. If temporal ordering is unknown, choose pessimistic
ordering or mark ambiguous.

## P0 — data truth

### 7. Series identity
Never subtract CVD/OI across different:
- venue;
- instrument;
- aggregation;
- CVD definition;
- chart settings;
- provider;
- contract roll/session.

Every observation must carry `series_id`.

### 8. Futures/spot/perp basis
CME BTC levels and crypto perpetual execution prices are not interchangeable.
Store venue/basis and use levels as reference unless explicitly mapped.

### 9. Session/day boundaries
dPOC/dVAH/dVAL/dOpen are session-dependent. Store timezone/session definition and
reset semantics. Never carry yesterday's "dPOC" as today's without explicit
context.

### 10. Twitch/YouTube duplicates
Same stream replayed on YouTube is ONE evidence episode, not independent
confirmation. Deduplicate by source/session/content/frame fingerprints.

### 11. YouTube survivorship/selection bias
A creator may publish/highlight winners more than losers. Estimate visible
coverage and keep losses, scratches, no-triggers and missing periods. Do not
infer win rate from edited examples.

## P1 — mathematical trading layer

### 12. Return unit = R first
For Pixel Perfect research, normalize each trade by initial structural risk:
`R = (exit-entry)/(entry-stop)` with side sign.

Primary edge metric:
`E[R] = p(win)*E[R_win] - p(loss)*E[|R_loss|] - costs_R`.

Win rate is secondary.

### 13. Distribution, not just mean
Report:
- median R;
- 5/25/75/95 percentiles;
- downside tail;
- max drawdown in R;
- loss streak distribution;
- MFE/MAE;
- bootstrap CI for expectancy.

Rare large losses can hide behind a high win rate.

### 14. Bayesian shrinkage for sparse regimes
Pixel Perfect is intentionally rare. Do not trust a 90% rate from 10 examples.
Shrink regime estimates toward a conservative global/base prior and show
credible intervals until sample size grows.

### 15. Probability calibration
A confidence score must be calibrated out-of-sample. Use Brier/log loss plus
reliability bins. Never call classification confidence "probability of profit".

### 16. Regime-conditioned edge
Estimate expectancy separately for:
- trend/range;
- high/low volatility;
- macro-event/non-event;
- deleveraging/leverage expansion;
- above/below value;
- long/short.

An aggregate positive edge that is negative in the current regime is not a
usable current edge.

### 17. Volatility-normalized geometry
Fixed USD/bps thresholds can drift with volatility. Compare wick/stop/level
distance also in ATR/realized-volatility units. Keep raw bps for interpretability.

### 18. MAE/MFE-based management
Optimize BE/stop management from full path:
- conditional probability of reaching +1R after MAE=x;
- probability winner is scratched by BE;
- probability loss is saved by BE;
- expected R by BE trigger.

Never choose BE threshold from winners only.

### 19. No martingale inference
Owner scale-in plans are recorded facts, not evidence that averaging down
improves expectancy. Strategy research must evaluate each scale-in policy with a
fixed maximum risk budget.

### 20. Risk of ruin / drawdown budget
Before any future live pilot, estimate risk of ruin under clustered/bootstrapped
trade sequences and define maximum strategy/account risk independently of model
confidence.

## P1 — selector / learning correctness

### 21. Selector-induced sampling bias
If Qwen only deeply analyzes segments already predicted useful, training labels
become biased toward the selector's worldview.

Fix:
- FULL_WATCH_GOLD;
- exploration/audit sample from LOW;
- inverse-probability/coverage metadata for research;
- false-skip ledger.

### 22. Missing-not-at-random
Unreadable OCR often happens during fast/high-volatility moments. Treat UNKNOWN
as potentially informative missingness. Report extraction coverage by regime and
volatility; never assume missing rows are random.

### 23. Teacher language leakage
Words such as "perfect", "winner", "stopped", "I made..." spoken after the trade
must not leak into decision-time features. Freeze transcript at T for decision
view.

### 24. Historical-review detection
Detect whether K1M6A is reviewing an old chart rather than trading live. A past
trade explanation must not be timestamped as a current market signal.

### 25. Layout drift
Chart layout/colors/labels may change over a year. Version ROI/layout profiles
and force recalibration on drift rather than silently applying old coordinates.

## P1 — engineering

### 26. One canonical trading learner
There are multiple old/new trading classifiers in the repo. The live/research
path must expose one canonical source of truth and explicitly quarantine legacy
classifiers from promotion decisions.

### 27. One canonical CASE store
CASE-001/002/003 exist under canonical data; mirrored/shared copies must be
generated/synced, not independently edited.

### 28. Experiment reproducibility
Every research result stores:
- git SHA;
- model/extractor/prompt versions;
- dataset manifest hash;
- split hash;
- random seed;
- costs;
- thresholds;
- code/config hash.

### 29. Fail closed on stale/missing data
Deep analysis may become PARTIAL/STALE but never silently use old CVD/OI as now.

### 30. No autonomous live execution
Current paper-only safety is correct. Keep exchange write/order credentials
physically absent from the learning runtime until a separately authorized future
phase.

## Promotion ladder

RAW
-> UNVERIFIED
-> OUTCOME_LABELLED
-> VERIFIED_EPISODE
-> RESEARCH_EDGE
-> HOLDOUT_CANDIDATE
-> SHADOW_VALIDATED
-> PAPER_VALIDATED

Nothing becomes a trusted strategy directly from YouTube or owner CASEs.

## Tomorrow's acceptance order

1. Import/compile/unit tests for every new market module.
2. FULL_WATCH_GOLD small batch.
3. Validate level OCR with zero wrong VERIFIED numbers.
4. Validate selector recall and false-skip audit.
5. Benchmark v2 vs v3 speed/quality.
6. Build deduplicated Pixel Perfect episodes including losses/scratches.
7. Run purged walk-forward research with realistic execution.
8. Produce statistical report; expect INSUFFICIENT_EVIDENCE initially.
9. Telegram deep-analysis E2E.
10. Only then consider promoting any learning rule.

The system is successful if it says "I do not know yet" when the mathematics
does not support an edge.
