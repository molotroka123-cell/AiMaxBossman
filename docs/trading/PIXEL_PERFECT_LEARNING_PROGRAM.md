# Pixel-Perfect Long/Short Learning Program

## Goal

Teach Bossman to recognize, test, explain and eventually coach the owner on the
rare high-precision entry style observed in k1m6a material: entries taken very
near a wick/structural extreme, with invalidation close to the entry and, when
the move proves itself, rapid protection toward breakeven.

"Pixel Perfect" is a **research label**, not a claim that the setup is perfect,
risk-free, or historically profitable. The system must measure every candidate,
including failed attempts, rather than learning only memorable winners.

## What Bossman must learn

The target is NOT "buy every wick" or "short every wick".

A candidate needs a sequence:

1. **Location** — price reaches a pre-existing structural/liquidity area.
2. **Sweep** — price trades through/into the area and leaves a wick/extreme.
3. **Order-flow reaction** — CVD/OI/liquidations show a meaningful change.
4. **Failure to continue** — aggressive flow stops producing price progress or
   leverage is flushed.
5. **Reclaim/rejection** — price returns through the trigger level.
6. **Acceptance** — hold/retest confirms that the reclaim/rejection was not one
   tick of noise.
7. **Tight invalidation** — a clearly defined structural condition exists close
   to the entry.
8. **Payoff path** — measure how quickly price moves away from entry and whether
   moving protection to breakeven would have been feasible without hindsight.

Long and short must be learned symmetrically.

## Candidate long archetype

Typical evidence sequence:

`support/liquidity sweep -> seller aggression/deleveraging -> price stops
responding down -> reclaim -> retest/acceptance -> long candidate`

Possible order-flow variants to test, never assume:
- Price DOWN + CVD DOWN + OI DOWN -> deleveraging/forced closure;
- Price FLAT/UP while CVD DOWN -> sell absorption candidate;
- CVD stabilizes/turns up while price reclaims structure;
- liquidation spike followed by failure to make a fresh accepted low.

## Candidate short archetype

Mirror:

`resistance/liquidity sweep -> buyer aggression/leverage -> price stops
responding up -> rejection/loss -> failed reclaim -> short candidate`

Variants:
- Price UP + CVD UP while price progress stalls -> buyer inefficiency candidate;
- Price DOWN + CVD DOWN + OI UP after failed reclaim -> bearish leverage expansion;
- short-side trigger is structural failure, not merely "CVD is high".

## Pixel-Perfect episode schema

Every candidate extracted from YouTube must store:

- video/channel/source URL;
- exact timestamp;
- chart/instrument/provider identity;
- frame hashes before/at/after entry;
- wick high/low;
- named levels visible before the entry;
- entry shown/stated by teacher;
- stop shown/stated by teacher;
- whether stop was initial or moved;
- CVD/OI/liquidations before/at/after;
- trigger;
- invalidation;
- first retest;
- time to +0.25R / +0.5R / +1R / +2R where observable;
- maximum favorable excursion (MFE);
- maximum adverse excursion (MAE);
- whether breakeven was moved and WHEN;
- whether BE would have been hit afterward;
- final observable outcome;
- teacher explanation;
- Bossman deterministic interpretation;
- extraction confidence.

Do not invent an entry/stop merely because a wick looks visually attractive.

## Critical anti-hindsight rule

The learner gets two views:

### Decision view
Only information visible **before or at the decision timestamp**.

### Outcome view
Future frames used only for labels.

The model must never see the later successful bounce when deciding whether the
earlier wick was a valid setup. Otherwise it learns hindsight, not trading.

## Breakeven research

"Move stop to BE quickly" must be measured, not copied as folklore.

For each verified candidate simulate policies such as:
- BE after +0.25R;
- BE after +0.5R;
- BE after +1R;
- BE after first accepted structural retest;
- no BE, structural invalidation only.

Measure:
- win rate;
- scratch/BE rate;
- expectancy in R;
- average/median R;
- profit factor;
- MFE lost by early BE;
- losses prevented by BE;
- percentage of eventual winners stopped at BE first.

The winning policy may differ by regime.

## Rarity is a feature

Bossman should have a `NO_SETUP` state.

A high-precision strategy should be allowed to produce zero candidates for long
periods. Never lower thresholds merely to create more signals.

Track:
- candidates/day;
- verified setups/week;
- false candidates;
- skipped opportunities;
- outcome by regime.

## Teacher bias protection

YouTube can make a trader look nearly unbeatable because losing attempts may be
absent, edited out, or discussed less often.

Therefore Bossman must separately record:
- all visible wins;
- all visible losses;
- stopped-at-BE examples;
- abandoned setups;
- setup mentions that never triggered;
- videos where the same pattern failed.

"Almost never lost" is a hypothesis to test, not a training label.

## Relationship to owner CASEs

CASE-001 teaches that a support touch is not acceptance and that rising OI
during a selloff can materially worsen a long.

CASE-002 contributes the mirror pair:
- failed repair below value -> short edge candidate;
- accepted reclaim above value -> long edge candidate.

CASE-003 adds event risk:
- FOMC/CPI/NFP can sweep both sides;
- first impulse is not confirmation;
- post-event acceptance matters more than the first wick.

Pixel-Perfect learning should retrieve these cases but not force every setup to
match them.

## Training curriculum for the owner

Bossman should eventually teach the owner in stages:

### Level 1 — Location
Owner marks the structural/liquidity area before seeing the outcome.

### Level 2 — Sweep vs breakout
Owner labels whether the excursion was accepted or rejected.

### Level 3 — Flow
Owner interprets Price/CVD/OI jointly.

### Level 4 — Trigger
Owner identifies reclaim/failed reclaim/retest.

### Level 5 — Invalidation
Owner states exactly what would prove the setup wrong.

### Level 6 — Management
Owner chooses a BE policy before seeing future frames.

### Level 7 — Blind replay
Hide the future, advance the chart frame-by-frame, score the decision.

Bossman should grade process separately from PnL. A correct process can lose;
a bad decision can make money.

## Promotion gate

A "Pixel Perfect" rule becomes trusted only after:
1. enough independently verified examples;
2. losing/BE examples included;
3. chronological holdout;
4. realistic spread/fees/slippage;
5. no future leakage;
6. positive expectancy in R on untouched data;
7. stability across more than one market regime;
8. paper/shadow validation.

Until then status = RESEARCH.
