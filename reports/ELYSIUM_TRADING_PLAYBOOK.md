# ELYSIUM TRADING INTELLIGENCE & STRATEGY PLAYBOOK (PHASES 2 & 3)

- **Code Implementation:** [`learning/elysium_trading_intelligence.py`](file:///C:/Users/asd/Bossman/wt-bossman-20-argon/learning/elysium_trading_intelligence.py)
- **Automated Test Suite:** [`tests/test_elysium_trading_intelligence.py`](file:///C:/Users/asd/Bossman/wt-bossman-20-argon/tests/test_elysium_trading_intelligence.py) (`15/15` tests passing)
- **Zero-Look-Ahead Enforcement:** [`assert_no_lookahead_bias()`](file:///C:/Users/asd/Bossman/wt-bossman-20-argon/learning/elysium_trading_intelligence.py#L90-L103) raises `LookAheadBiasError` if any candle or market event has `timestamp > decision_timestamp`.

---

## Phase 2 — 10-Dimension Live Discovery Engine & Risk Scorer

[`evaluate_token_risk()`](file:///C:/Users/asd/Bossman/wt-bossman-20-argon/learning/elysium_trading_intelligence.py#L298-L454) inspects all 10 required market dimensions at `t <= t_decision` and computes a deterministic `0..100` risk score (`0` = cleanest, `100` = guaranteed honeypot/rug):

| # | Discovery Dimension | Key Metrics Inspected | Hard Veto / Penalty Threshold |
| :--- | :--- | :--- | :--- |
| **1** | **Newly Created Tokens & LP Pools** | `is_verified_factory`, `age_minutes` | Hard veto (`+35`) if not deployed by verified Launchpad (`0xb94a...be5a`) or `UniswapV3Factory` (`0x520b...7893`); `+15` penalty if `age_minutes < 2.0` |
| **2** | **Bonding Curve & Token Migrations** | `bonding_curve_pct`, `curve_locked`, `is_graduated_dex`, `migration_age_minutes` | Hard veto (`+85`) if `curve_locked == True` or `99.2% <= bonding_curve_pct < 100.0%` (graduation lock window); `+30` penalty if `migration_age_minutes < 3.0` |
| **3** | **Trading Volume & Acceleration** | `volume_5m_hype`, `volume_acceleration_ratio` | `+25` penalty if `volume_5m_hype < 8.0 HYPE`; positive signal when `volume_acceleration_ratio >= 1.35x` |
| **4** | **Real Liquidity Depth & Spreads** | `reserve_whype`, `effective_spread_bps`, `pool_fee_bps` | Hard veto (`+75`) if `reserve_whype < 15.0 WHYPE`; hard veto (`+45`) if `effective_spread_bps > 250 bps` |
| **5** | **Creator History & Concentration** | `creator_prior_rugs`, `creator_holding_pct` | Hard veto (`+90`) if `creator_prior_rugs > 0`; hard veto (`+55`) if `creator_holding_pct > 12.0%` |
| **6** | **Holder Distribution & Sybil Clusters** | `top10_non_pool_pct`, `sybil_cluster_share_pct`, `unique_buyers_5m` | Hard veto (`+65`) if `sybil_cluster_share_pct > 20.0%`; hard veto (`+45`) if `top10_non_pool_pct > 35.0%` |
| **7** | **Net Buying & Selling Flows** | `taker_buy_ratio_5m`, `net_inflow_5m_hype` | `+30` penalty if `taker_buy_ratio_5m < 0.45` or `net_inflow_5m_hype < -5.0 HYPE` |
| **8** | **On-Chain Smart-Money Behavior** | `smart_money_net_buys`, `smart_money_dumping` | Hard veto (`+45`) if `smart_money_dumping == True`; positive signal when `smart_money_net_buys >= 2` |
| **9** | **Contract Restrictions & Exit Liquidity** | `eth_call_buy_ok`, `eth_call_sell_ok`, `round_trip_tax_bps`, `has_mutable_tax_or_blacklist` | Hard veto (`+100`) if `eth_call` buy or sell reverts; hard veto (`+80`) if `round_trip_tax_bps > 500 bps`; hard veto (`+60`) if mutable tax/blacklist selector exists |
| **10** | **Wash Volume & Entropy Filter** | `trade_interval_entropy_bits` ($H(\Delta t)$) | Hard veto (`+70` / `+35`) if Shannon entropy $H(\Delta t) < 3.8\text{ bits}$ (detects deterministic wash-trading timer loops) |

### Explicit Classification of Signals ([`classify_discovery_signals()`](file:///C:/Users/asd/Bossman/wt-bossman-20-argon/learning/elysium_trading_intelligence.py#L134-L214))

1. **Predictive Signals (Verified On-Chain & Empirically Predictive of Positive Net Edge)**:
   - **`eth_call` Round-Trip Simulation (`round_trip_tax_bps <= 150` & `eth_call_sell_ok == True`)**: Directly proves at the current block that tokens can be bought and sold without honeypot reverts or hidden transfer taxes.
   - **Effective Liquidity Depth & Bounded Impact (`reserve_whype >= 20.0` & $dx^* \le 1.8\%$ of pool)**: Prevents self-inflicted slippage and sandwich vulnerability.
   - **Entropy-Filtered Net Orderflow (`taker_buy_ratio_5m >= 0.57` & $H(\Delta t) \ge 3.8\text{ bits}$)**: Separates genuine multi-wallet accumulation from single-operator wash loops.
   - **Creator & Sybil Cluster Bounds (`creator_holding_pct <= 8%`, `top10_non_pool_pct <= 28%`, `sybil_cluster_share_pct <= 15%`)**: Bounds single-block dump risk.
   - **Post-Migration Stabilization Window (`is_graduated_dex == True` & `migration_age_minutes >= 5.0`)**: Avoids both the `99.5%->100%` `curveLocked` freeze and the immediate post-graduation early-buyer dump.
2. **Unverified Signals (Never Used for Trade Execution)**:
   - **Off-Chain Social Hype / Telegram Alpha Claims**: Easily spoofed by coordinated botnets to lure exit liquidity.
   - **Promised Future Launchpad Airdrop Points**: Have `$0.00` confirmed realized value until an on-chain redemption contract is deployed.
3. **Merely Correlational Signals (Insufficient Alone)**:
   - **Raw Gross 1h/24h Volume & Transaction Count**: On a `$0.001/tx` L2 (`0.10 Gwei` FCFS), high gross volume correlates with activity but is trivially faked by zero-net-flow wash loops unless paired with Shannon entropy $H(\Delta t) \ge 3.8\text{ bits}$.
   - **Ticker/Narrative Theme Similarity**: Copycat tickers launch continuously without real liquidity or developer commitment.

---

## Phase 3 — Three Quantitative Trading Strategies

All three strategies size positions using [`compute_optimal_position_size_hype()`](file:///C:/Users/asd/Bossman/wt-bossman-20-argon/learning/elysium_trading_intelligence.py#L481-L496):
$$dx^* = \min\bigl(0.018 \times R_{\text{WHYPE}},\; 0.05 \times \text{Portfolio}_{\text{HYPE}},\; 0.50\text{ HYPE}\bigr)$$
ensuring pool price impact never exceeds **`1.8%`** of `WHYPE` reserves and single-trade exposure never exceeds **`5.0%`** of capital.

### Strategy A — Early Momentum ([`evaluate_strategy_a_early_momentum()`](file:///C:/Users/asd/Bossman/wt-bossman-20-argon/learning/elysium_trading_intelligence.py#L499-L614))
- **Objective Entry Conditions:**
  - `risk.is_tradeable == True` (`risk_score < 50`, zero hard vetoes)
  - Token age `3.0 <= age_minutes <= 90.0`
  - Volume acceleration `volume_acceleration_ratio >= 1.45x` and `volume_5m_hype >= 18.0 HYPE`
  - Orderflow: `taker_buy_ratio_5m >= 0.60`, `net_inflow_5m_hype >= 5.0 HYPE`, `unique_buyers_5m >= 14`
  - Organic arrival entropy: `trade_interval_entropy_bits >= 3.9 bits`
  - Momentum: `3.5% <= price_change_5m_pct <= 28.0%`, `pullback_from_local_high_pct >= -10.0%`
  - Liquidity depth: `reserve_whype >= 40.0 WHYPE`
  - Expected net edge after round-trip tax + price impact + gas: `expected_net_edge_bps >= +60.0 bps`
- **Invalidation Conditions:**
  - 5-minute taker buy ratio drops below `0.46`
  - 1-minute candle closes more than `-3.5%` below 15-minute VWAP
- **Exit Rules:**
  - **Hard Stop-Loss:** `-6.5%`
  - **Take-Profit:** `+16.0%`
  - **Time-Stop:** `900 seconds` (`15 minutes`) if momentum stalls
- **200-Scenario Evaluation Result:** `28` trades, `64.29%` win rate, Profit Factor `0.898`, Net Simulated P&L `-0.017216 HYPE` (demonstrates why ungraduated early momentum faces higher false-breakout noise than Strategies B and C).

### Strategy B — Pullback Entry ([`evaluate_strategy_b_pullback_entry()`](file:///C:/Users/asd/Bossman/wt-bossman-20-argon/learning/elysium_trading_intelligence.py#L617-L723))
- **Objective Entry Conditions:**
  - `risk.is_tradeable == True` (`risk_score < 50`, zero hard vetoes)
  - Established token: `age_minutes >= 15.0` and `reserve_whype >= 55.0 WHYPE`
  - Controlled pullback from local high: `-35.0% <= pullback_from_local_high_pct <= -18.0%`
  - Holding 15-minute VWAP support zone: `-4.5% <= vwap_distance_pct <= +3.0%`
  - Orderflow stabilization & rebound: `taker_buy_ratio_5m >= 0.54`, `net_inflow_5m_hype >= +2.0 HYPE`, `trade_interval_entropy_bits >= 3.85 bits`
  - Expected net edge after round-trip costs: `expected_net_edge_bps >= +55.0 bps`
- **Invalidation Conditions:**
  - Pullback deepens past `-38.0%` from local high (structural breakdown)
  - 5-minute net flow turns negative (`< -3.0 HYPE`)
- **Exit Rules:**
  - **Hard Stop-Loss:** `-5.5%`
  - **Take-Profit:** `+14.0%`
  - **Time-Stop:** `1,200 seconds` (`20 minutes`)
- **200-Scenario Evaluation Result:** `14` trades, `85.71%` win rate (`12W / 2L`), Profit Factor **`3.804`**, Net Simulated P&L **`+0.086655 HYPE` (`+$6.67 USD` simulated)**, Avg Profit `+0.006190 HYPE/trade`.

### Strategy C — Post-Launch / Migration ([`evaluate_strategy_c_post_launch_migration()`](file:///C:/Users/asd/Bossman/wt-bossman-20-argon/learning/elysium_trading_intelligence.py#L726-L831)) — **RANKED #1 BEST STRATEGY**
- **Objective Entry Conditions:**
  - `risk.is_tradeable == True` (`risk_score < 50`, zero hard vetoes)
  - Confirmed DEX Graduation: `is_graduated_dex == True`, `curve_locked == False`, `bonding_curve_pct >= 100.0%`
  - Post-Graduation Stabilization Window: `5.0 <= migration_age_minutes <= 60.0` (skips the chaotic first 5 minutes of early-buyer migration selling)
  - Deep DEX Liquidity & Tight Spread: `reserve_whype >= 80.0 WHYPE` and `effective_spread_bps <= 95.0 bps`
  - Resumed Institutional/Smart-Money Demand: `taker_buy_ratio_5m >= 0.56`, `net_inflow_5m_hype >= 6.0 HYPE`, `trade_interval_entropy_bits >= 4.0 bits`, and `smart_money_net_buys >= 1`
  - Expected net edge after round-trip costs: `expected_net_edge_bps >= +65.0 bps`
- **Invalidation Conditions:**
  - Pool `WHYPE` reserve drops by more than `-12.0%` (LP removal warning)
  - Tracked smart-money wallets flip to net selling (`smart_money_dumping == True`)
- **Exit Rules:**
  - **Hard Stop-Loss:** `-5.0%`
  - **Take-Profit:** `+15.0%`
  - **Time-Stop:** `1,500 seconds` (`25 minutes`)
- **200-Scenario Evaluation Result:** `43` trades, **`88.37%` win rate (`38W / 5L`)**, Profit Factor **`5.830`**, Net Simulated P&L **`+0.325551 HYPE` (`+$25.05 USD` simulated)**, Avg Profit `+0.007571 HYPE/trade`.
