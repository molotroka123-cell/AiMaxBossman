# Bossman Trader Apprentice — BTC Orderflow Playbook v1

Purpose: give the local model a compact but complete set of reusable rules for analyzing BTC from Coinwise/K1m6a-style screens using **price + CVD + open interest + liquidations + market/profile levels**.

This document is a training/retrieval artifact, not an execution mandate. The model must produce analysis, confidence, missing-data warnings and level-triggered scenarios. Order placement remains a separately authorized action.

## 1. Core principle

Never interpret one metric in isolation.

The minimum useful state is:

- price
- CVD (aggregated cumulative volume delta)
- open interest (OI)
- liquidations when available
- nearby structural levels
- source/instrument/timestamp

The model should compare the **latest snapshot against the previous valid snapshot from the same source and settings**.

## 2. What each metric means

### Price
Price is the final truth of whether aggression is producing movement.

### CVD
CVD approximates cumulative aggressive buy minus aggressive sell volume. Falling CVD means more aggressive selling; rising CVD means more aggressive buying.

CVD does **not** tell us whether those aggressive orders are succeeding. The key question is: what does price do while CVD moves?

Examples:

- CVD falls hard while price barely falls -> sell aggression is being absorbed.
- CVD rises while price cannot rise -> buy aggression may be absorbed by a hidden/passive seller.

Do not compare absolute CVD values across different providers, sessions, aggregation settings or reset rules.

### Open interest
OI is the amount of open derivative contracts. It is **not a direct long-vs-short meter**.

- OI rising = more open leverage/contracts.
- OI falling = positions are being closed/liquidated overall.

Direction comes from OI **combined with price and flow**, not OI alone.

### Liquidations
Liquidations identify forced position closure. They are useful for distinguishing a leverage flush from an orderly directional build, but a liquidation spike alone is not an entry signal.

Important: displayed liquidation values may represent a bar/window/aggregation, not necessarily a daily total.

## 3. Primary Price/CVD/OI matrix

This matrix is the heart of the Trader Apprentice.

### A. Price DOWN + CVD DOWN + OI DOWN
**Interpretation:** deleveraging / position closure / long flush dominates.

This is bearish in price, but materially less bearish than a decline with OI expansion.

What we learned:

- Do not automatically short the bottom of the liquidation candle.
- Do not automatically average a long either.
- Wait for price to stop making lows, a reclaim, or absorption.
- If OI continues to fall while price begins to stabilize, the market may be cleaning leverage rather than building a fresh downside trend.

### B. Price DOWN + CVD DOWN + OI UP
**Interpretation:** bearish leverage expansion / new directional positions entering a falling market.

This was the major warning in the September case.

This is materially worse for a long thesis than simple deleveraging.

Response:

- stop automatic averaging
- treat broken support as real until reclaimed
- prioritize risk reduction over "cheap price"
- require a fresh reclaim/absorption setup before considering another long

Do not claim "new shorts" as a certainty from OI alone. Correct wording: **new leverage is expanding during price/CVD decline; bearish short-build risk is elevated.**

### C. Price UP + CVD UP + OI UP
**Interpretation:** new demand plus expanding participation/leverage.

This is the strongest simple bullish continuation matrix, especially after a confirmed reclaim/retest.

Caution: if OI explodes vertically after a large move, late leverage can become fragile.

### D. Price UP + CVD UP + OI DOWN
**Interpretation:** recovery with aggressive buying while leverage is still being cleaned/closed.

Constructive after a flush, but not automatically a durable new trend.

### E. Price UP + CVD FLAT/DOWN + OI DOWN
**Interpretation:** short covering and/or sell absorption.

This can produce a strong bounce, but it lacks confirmation from fresh aggressive demand.

Do not chase resistance solely because price is rising.

### F. Price FLAT/UP + CVD DOWN + OI FLAT/DOWN
**Interpretation:** sell absorption candidate / bullish divergence.

Aggressive sellers are active but failing to push price lower.

This becomes much more interesting at a known support or after a sweep and reclaim.

### G. Price DOWN + CVD FLAT/UP + OI FLAT/UP
**Interpretation:** buyer-failure candidate / hidden seller.

Aggressive buying is not lifting price. Avoid assuming CVD up is automatically bullish.

## 4. Level hierarchy and meaning

The model should extract and normalize nearby levels from the screen.

Common labels:

- `dVAL` — daily value area low; lower boundary of accepted daily value
- `dPOC` — daily point of control; highest-volume/most-accepted daily price area
- `dVAH` — daily value area high; upper boundary of accepted daily value
- `dOpen` — daily open
- `pDay Low` / `pDay High`
- `pWeek Eq` / `Week Open`
- `Month Open`
- `Settlement (D)` / `Settlement (W)`
- `Year Eq`
- custom `Zone`, `ntPOC`, etc.

### Level logic

A level touch is not acceptance.

Prefer:

1. approach
2. sweep/touch
3. reclaim
4. hold for multiple observations or retest from the other side
5. flow confirmation

A useful hierarchy for long confirmation after a selloff is often:

`dVAL reclaim -> dPOC reclaim -> dVAH reclaim -> dOpen reclaim/acceptance`

The exact ordering may differ by session; use the actual numeric order on the screen.

## 5. Reclaim, acceptance and sweep definitions

### Reclaim
Previous valid observation below a level, latest observation above it.

### Acceptance
Do not call acceptance from a single wick or one screenshot. Require multiple observations/bars or a successful retest above the level.

### Sweep-and-reclaim
Price trades below a known level/liquidity area, then returns above it quickly. This can be a long trigger only if the flow state is not deteriorating.

## 6. Confluence logic

The model should group nearby levels into zones rather than pretending every exact dollar matters.

Example: if dVAL, prior-day low and a weekly equilibrium sit within a small number of basis points, treat that as a stronger confluence zone.

Suggested machine heuristic:

- convert pairwise distance to basis points
- levels within ~10–25 bps may be clustered depending on volatility
- more independent level types in a cluster => higher structural relevance

Do not overfit the exact threshold; volatility regime matters.

## 7. Current learned example — 8 Sep 2026

Observed training snapshot from the Coinwise/K1m6a-style screen:

- `dVAL = 78,430`
- `dPOC = 78,565`
- `dVAH = 78,710`
- `dOpen = 78,795`
- price was trading roughly in the 78.5k area
- earlier CVD around `61.10B`, later around `61.29B`
- earlier OI around `18.63B`, later around `18.78B`

Interpretation at that moment:

- price stopped accelerating lower
- CVD started recovering slightly
- OI expanded rapidly
- this created a **possible reversal candidate**, but not a guaranteed long

Early trigger:

- reclaim/hold above `78,565 dPOC`

Higher-quality trigger:

- reclaim `78,710 dVAH`
- preferably also reclaim/accept above `78,795 dOpen`
- successful retest from above
- CVD not collapsing
- OI growth not becoming an uncontrolled vertical leverage spike

Invalidation/warning:

- price back below `78,430 dVAL`
- CVD resumes lower lows
- OI continues to expand while price falls

This example is historical training context, not a live signal.

## 8. September long case — worked lesson

Equal planned tranches were filled at:

- 15% at 79,800
- 15% at 79,350
- 15% at 78,900

Weighted average:

`(79,800*0.15 + 79,350*0.15 + 78,900*0.15) / 0.45 = 79,350`

### Phase 1: initial flush

Price fell, CVD fell, OI fell strongly.

Interpretation: deleveraging / long liquidation cleanup.

Lesson: this was not the same as a fresh short build. The market later recovered several times back toward/above the average.

### Phase 2: recovery with low OI

Price rose while OI remained depressed/fell and CVD recovered only slowly.

Interpretation: relief bounce / short covering / absorption, constructive but not yet strong trend confirmation.

Lesson: do not chase resistance just because price rises while leverage is being cleaned.

### Phase 3: repeated 80k tests

Price repeatedly approached 79.9–80.1k while CVD/OI confirmation was inconsistent.

Lesson: a repeated touch is not the same as acceptance. Reclaim + hold/retest matters more.

### Phase 4: regime change

Later the structure became:

- price down
- CVD down
- OI up

At the same time 78.8k was lost.

Interpretation: the previous deleveraging thesis was no longer sufficient; bearish leverage-expansion risk had appeared.

The position had already been closed around breakeven, which avoided remaining attached to an outdated long thesis.

Lesson: **when the regime changes, the decision must change. Do not defend an old thesis with a new market state.**

## 9. Earlier 77k case — reusable pattern

Prior lesson:

- price around 77.9k after an impulse from ~76.75k
- price held ~77.55–77.8 despite weak/flattening CVD
- OI was elevated

Interpretation: potential absorption/hidden buyer and squeeze setup.

Bull confirmation discussed:

- reclaim/acceptance above ~78.15–78.2
- then ~78.4
- then ~78.6–78.8

Bear failure path discussed:

- acceptance below ~77.55
- loss of ~77.27
- especially with falling CVD and rising OI
- then revisit ~76.95–76.75

Reusable lesson: **price response to weak CVD matters more than CVD direction alone.**

## 10. Position math

### Weighted average entry

`avg_entry = sum(entry_price_i * size_i) / sum(size_i)`

Never assume equal tranches unless the sizes are actually equal.

### Raw long return

`return_pct = (mark - avg_entry) / avg_entry * 100`

This is unlevered and before fees/funding.

Approximate leveraged PnL percentage is not the same as exchange account PnL because margin mode, funding, fees and position changes matter. Use the exchange as source of truth for actual PnL.

### Partial exits

Once part of a position is closed, track:

- remaining quantity
- remaining average entry according to exchange accounting
- realized PnL
- unrealized PnL

Do not recompute the whole trade as if closed size were still open.

## 11. Add / no-add logic learned

Never add merely because price is lower.

Prefer only one of these:

### A. Confirmed reclaim add

- important level reclaimed
- held/retested from above
- CVD stable/up
- OI behavior not deteriorating

### B. Sweep/absorption add

- support/liquidity level swept
- price stops making new lows or immediately reclaims
- CVD may still be weak but cannot produce lower price
- OI is not expanding aggressively during continued downside

### Do not add when

- price falls through the level without reclaim
- CVD falls and OI expands
- data is stale/missing
- source/instrument is ambiguous
- the model is only seeing one isolated screenshot with no sequence context

## 12. Exit / risk-off logic learned

A long thesis weakens materially when:

- confirmed support is lost and accepted below
- price down + CVD down + OI up persists
- aggressive buyers fail to lift price
- repeated reclaim attempts fail

Do not wait for a single magical indicator. Risk-off is a multi-factor decision.

## 13. CME vs execution-market rule

The streamer may show CME/other chart levels while the execution market is BTCUSDT on another venue.

Mandatory rules:

- tag every observation with source and instrument
- never use a CME chart number as an exact exchange order price without basis adjustment
- compare directional structure across instruments, not necessarily exact level equality
- if public spot price and Coinwise metrics are from different timestamps, say so explicitly

## 14. Data-quality rules

The model must never invent values.

If Twitch/Coinwise live metrics are unreadable:

- price may be obtained from a public source if clearly tagged
- CVD/OI/liquidations remain `unknown`
- do not infer current CVD/OI from an old screenshot
- do not compare CoinGlass aggregate OI to a Coinwise/K1m6a OI number as if they were the same series

For screenshots record:

- timestamp visible/known
- source
- instrument
- price
- CVD
- OI
- liquidation values
- buy/sell volume
- extracted levels
- OCR/vision confidence

## 15. Local-model output contract

For every analysis answer, return:

1. **Market state** — one-line Price/CVD/OI matrix.
2. **Regime** — deleveraging, bearish leverage expansion, bullish leverage expansion, recovery, absorption, balance, unknown.
3. **Evidence** — exact observed values and deltas; distinguish observation from inference.
4. **Levels** — nearest support/resistance and what must be reclaimed/lost.
5. **Long scenario** — trigger + confirmation + invalidation.
6. **Bear/risk scenario** — trigger + confirmation + invalidation.
7. **Position math** if a position exists.
8. **Missing data** — explicitly list unavailable metrics.
9. **Confidence** — confidence in classification, not probability of profit.

Preferred simple wording:

> Price ↓ + CVD ↓ + OI ↑. This is worse than simple deleveraging because open leverage is expanding while aggressive selling pushes price lower. Long adds are disabled until a reclaim/absorption setup appears.

## 16. Anti-patterns

Never teach the model these mistakes:

- "OI down = bullish"
- "OI up = shorts"
- "CVD down = price must go down"
- "large liquidation = instant reversal"
- "support touched = support held"
- "same absolute CVD value means same state across providers"
- "public BTC spot price proves what Coinwise CVD/OI are doing"
- "we already bought lower, therefore we must keep averaging"
- "old thesis remains valid after the Price/CVD/OI regime changes"

## 17. Minimal decision tree

```text
VALID SAME-SOURCE SNAPSHOTS?
  no -> UNKNOWN / NO TRADE
  yes
   |
   +-- Price down?
   |    +-- CVD down + OI down -> deleveraging -> WATCH for absorption/reclaim
   |    +-- CVD down + OI up   -> bearish leverage expansion -> RISK OFF / no add
   |    +-- CVD up/flat        -> buyer failure possible -> caution
   |
   +-- Price up?
        +-- CVD up + OI up     -> bullish expansion -> confirm at levels
        +-- CVD up + OI down   -> recovery/covering -> constructive, don't chase
        +-- CVD flat/down + OI down -> absorption/short covering -> confirm reclaim
```

The deterministic implementation lives in `learning/trader_apprentice.py`.
