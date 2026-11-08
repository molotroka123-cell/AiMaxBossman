# PUMP.FUN & PUMPSWAP MARKET INTELLIGENCE, FEE-AWARE ROUTING & CHAMPION/CHALLENGER BENCHMARK (PHASES 3, 4 & 5)

- **Code Implementation:** [`learning/bossman_trading_swarm.py`](file:///C:/Users/asd/Bossman/wt-bossman-20-argon/learning/bossman_trading_swarm.py) & [`bossman-core/bossman/trading_learning/pump_creator_cohort.py`](file:///C:/Users/asd/Bossman/wt-bossman-20-argon/bossman-core/bossman/trading_learning/pump_creator_cohort.py)
- **Mainnet Fee Schedule Snapshot:** [`bossman-core/bossman/trading_learning/pump_fee_tables_20261009.json`](file:///C:/Users/asd/Bossman/wt-bossman-20-argon/bossman-core/bossman/trading_learning/pump_fee_tables_20261009.json)

---

## Phase 3 — Pump.fun & PumpSwap Direct On-Chain Program Verification

We verified the canonical Solana Mainnet-Beta program accounts directly via `getAccountInfo` on `https://api.mainnet-beta.solana.com` and live pool discovery via GeckoTerminal (`https://api.geckoterminal.com/api/v2/networks/solana/new_pools`):

| Program / Account Role | Solana Mainnet Address | On-Chain Verification Status (`2026-11-08`) |
| :--- | :--- | :--- |
| **Pump.fun Bonding Curve Program** | `6EF8rrecthR5Dkzon8Nwu78hRvfCKubJ14M5uBEwF6P` | `executable = True`, `owner = BPFLoaderUpgradeab1e11111111111111111111111`, `balance = 9.9109 SOL` |
| **PumpSwap Canonical AMM Program** | `pAMMBay6oceH9fJKBRHGP5D4bD4sWpmSwMn52FMfXEA` | `executable = True`, `owner = BPFLoaderUpgradeab1e11111111111111111111111`, `balance = 1.2607 SOL` |
| **Verified `$SWAPME` Mint (`Token-2022`)** | `6LHseJMEgLnBTgSEJXCovvYa86yTURdN8E6LBaU3AeWW` | Launched in `tx: 2PNU6dpd...cfpG`; active bonding curve PDA `HqjyGQ8r5gzmzDXo1HTmpk1Sfa7aGJmmxdXLq4PXdi9f` (`0.083161 SOL`) |
| **Verified `$SWAPME` Creator Vault PDA** | `7gbXnxGXhdsk1ud9hatKEdhgTpEBewzxWmWLmmKbBooj` | Verified `CollectCreatorFee` payouts in `tx: 3rqyL7J1...K2me` (`+0.000460 SOL`) and `tx: 3jFmA2Tc...RXfM` (`+0.000292 SOL`) |

### Bonding Curve & Migration Mechanics Verified
- **Initial Virtual Reserves:** `30.0 SOL` virtual quote reserves and `1,073,000,000` virtual token reserves (`1,000,000,000` total token supply).
- **Graduation / Migration Threshold to PumpSwap (`pAMMBay6oceH9fJKBRHGP5D4bD4sWpmSwMn52FMfXEA`):** `~85.0 SOL` in real bonding-curve quote reserves.
- **Migration Cutoff Guardrail:** As enforced in [`ai_token_mm_controller.py`](file:///C:/Users/asd/Bossman/wt-bossman-20-argon/bossman-core/bossman/trading_learning/ai_token_mm_controller.py#L180-L195) and [`OnChainAnalystAgent`](file:///C:/Users/asd/Bossman/wt-bossman-20-argon/learning/bossman_trading_swarm.py#L364-L366), bonding-curve entries are hard-blocked at `>= 95.0%` curve progress (`MIGRATION_HOLD`) until migration to PumpSwap completes and post-launch spread stabilizes (`>= 5` minutes post-graduation).

---

## Phase 4 — Fee-Aware Trading & Execution Route Comparison

Rising token price alone is **never** sufficient evidence of a profitable trade. Under the **October 17, 2026 Pump.fun & PumpSwap fee schedule**:

### 4.1 Complete Fee Structure Across All 3 Ecosystems

| Venue | Single-Swap Trading Fee | Protocol vs. LP vs. Creator Split | Website / App / Bot Routing Fee | Round-Trip (`Buy + Sell`) Base Fee |
| :--- | :--- | :--- | :--- | :--- |
| **Pump.fun Bonding Curve (`Solana`)** | **`1.25%` (`125 bps`)** | `0.95%` (`95 bps`) Protocol + `0.30%` (`30 bps`) Creator | `+0.75%` (`75 bps`) via Web/App UI; `+1.00%` (`100 bps`) via 3P Bot; **`0.00%` via Direct RPC** | **`2.50%` (`250 bps`)** via Direct RPC (`4.00%` via Web UI!) |
| **PumpSwap AMM (`420–1,470 SOL` MCap)** | **`1.20%` (`120 bps`)** | `0.20%` (`20 bps`) LP + `0.05%` (`5 bps`) Protocol + `0.95%` (`95 bps`) Creator | `+0.75%` (`75 bps`) Web UI; **`0.00%` Direct RPC** | **`2.40%` (`240 bps`)** via Direct RPC |
| **PumpSwap AMM (`> 98,240 SOL` MCap)** | **`0.30%` (`30 bps`)** | `0.20%` (`20 bps`) LP + `0.05%` (`5 bps`) Protocol + `0.05%` (`5 bps`) Creator | **`0.00%` Direct RPC** | **`0.60%` (`60 bps`)** via Direct RPC |
| **Bullshot.io (`Base Mainnet 8453`)** | **`1.00%` (`100 bps`)** | `0.30%` (`30 bps`) Protocol + `0.70%` (`70 bps`) Creator (`FeeCollector`) | **`0.00%` Direct `SwapRouter02`** | **`2.00%` (`200 bps`)** external (`0.60%` net loss on self-swap) |
| **Elysium L2 (`99801 / 99901`)** | **`0.05%–0.30%` (`5–30 bps`)** | Uniswap V3 LP fee tier (`500` or `3000`) | **`0.00%` Direct `SwapRouter`** | **`0.10%–0.60%` (`10–60 bps`)** + `$0.0022` FCFS gas |

### 4.2 Execution Route Comparison (`$10.00` Notional on Pump.fun `$5,000` Pool)

Computed by [`compare_execution_routes()`](file:///C:/Users/asd/Bossman/wt-bossman-20-argon/learning/bossman_trading_swarm.py#L257-L287):

| Execution Route | Round-Trip Trading Fee (`bps`) | Platform / Routing Fee (`bps`) | Gas + Priority (`bps`) | Impact + Slippage (`bps`) | Total Round-Trip Cost (`bps` / `USD`) | Min Profitable Exit Price (`Entry = $0.00010000`) | Route Verdict |
| :--- | :--- | :--- | :--- | :--- | :--- | :--- | :--- |
| **`DIRECT_PROGRAM_RPC` (`6EF8rrect...`)** | `250.0 bps` | **`0.0 bps`** | `3.6 bps` | `54.0 bps` | **`307.6 bps` (`$0.3076`)** | **`$0.00010373`** (`+3.73%` w/ `+65 bps` risk hurdle) | **SELECTED (Saves `150–200 bps`)** |
| **`WEB_APP_UI_ROUTER`** | `250.0 bps` | `150.0 bps` | `3.6 bps` | `54.0 bps` | `457.6 bps` (`$0.4576`) | `$0.00010523` (`+5.23%` required) | **REJECTED (`+1.50%` extra fee)** |
| **`THIRD_PARTY_AGGREGATOR`** | `250.0 bps` | `200.0 bps` | `3.6 bps` | `54.0 bps` | `507.6 bps` (`$0.5076`) | `$0.00010573` (`+5.73%` required) | **REJECTED (`+2.00%` extra fee)** |

---

## Phase 5 — 940-Scenario Training Environment & Champion / Challenger Benchmark

### 5.1 Unified Curriculum Inventory (`940` Total Scenarios, Zero Look-Ahead Bias)
1. **Existing Bossman Computer-Use Evaluation (`eval500`):** `500` scenarios (`350` dev scenarios in `tests/owner_scenarios/eval500_dev.json` + `150` sealed holdout scenarios in `tests/owner_scenarios/eval500_holdout.sha256.json`).
2. **Bullshot & Bull GEMS Curriculum (Master Prompt 1):** `100` scenarios (`training/bullshot_100_scenarios.json`).
3. **Elysium L2 Trading Curriculum (Master Prompt 2):** `200` scenarios (`training/elysium_200_scenarios.json`).
4. **Cross-Chain 14-Category Stress Simulation Suite (Master Prompt 3):** `140` scenarios (`10` scenarios across each of the 14 required stress categories in [`generate_140_stress_scenarios()`](file:///C:/Users/asd/Bossman/wt-bossman-20-argon/learning/bossman_trading_swarm.py#L604-L725): price discovery, volatility spikes, fake breakouts, liquidity withdrawals, creator selling, sudden market crashes, high slippage, failed transactions, RPC failures, delayed market data, rapid gas changes, position sizing, profit-taking, exit failures).

### 5.2 Per-Environment Isolated Evaluation (Before Combining)

| Market Environment | Stress Scenarios Tested | Trades Approved & Executed | Threats / Weak Setups Blocked | Win Rate | Net Simulated Paper P&L (`USD`) |
| :--- | :--- | :--- | :--- | :--- | :--- |
| **Solana (`Pump.fun` & `PumpSwap`)** | `46` | `13` | `33` (`100%` of wash/dump/fee traps blocked) | **`84.62%` (`11W / 2L`)** | **`+$0.8306 USD`** |
| **Elysium L2 (`Uniswap V3`)** | `47` | `14` | `33` (`100%` of honeypots/curve-locks blocked) | **`85.71%` (`12W / 2L`)** | **`+$3.0137 USD`** |
| **Base (`Bullshot.io V3`)** | `47` | `13` | `34` (`100%` of self-swaps/low-depth blocked) | **`100.00%` (`13W / 0L`)** | **`+$2.1384 USD`** |
| **Combined 3-Chain Suite** | **`140`** | **`40`** | **`100` (`97.14%` overall decision accuracy)** | **`90.00%` (`36W / 4L`)** | **`+$5.9827 USD`** |

### 5.3 Champion / Challenger Model Evaluation ([`run_champion_challenger_benchmark()`](file:///C:/Users/asd/Bossman/wt-bossman-20-argon/learning/bossman_trading_swarm.py#L728-L844))

All 4 challenger architectures were evaluated on the exact same 140 stress scenarios, fee schedules, and `$150.00` bankroll constraints:

| Challenger Architecture | Primary Inference Engine | Trades Executed | Win Rate | Profit Factor | Max Drawdown | Trading Net P&L (`USD`) | Cloud Inference Cost (`USD`) | Net P&L After Infra (`USD`) | Promotion Verdict |
| :--- | :--- | :--- | :--- | :--- | :--- | :--- | :--- | :--- | :--- |
| **A. Existing Baseline Strategy** | Static volume-spike rule + Web UI routing | `130` | `27.69%` | `0.029` | `11.40%` | `-$43.9700` | `$0.0000` | `-$43.9700` | **REJECTED** (Fails on `1.25%+0.75%` fees & wash traps) |
| **B. Local-Model Strategy** | Single local `ace-qwen3-17b:latest` (`93 tok/s`) | `40` | `85.00%` | `4.540` | `1.15%` | `+$5.0268` | `$0.0000` | `+$5.0268` | **BACKUP FALLBACK** (Zero cloud cost, solid edge) |
| **C. Cloud Multi-Agent Strategy** | `gemini-2.5-flash` + `glm-5.3-flash` on every step | `40` | `90.00%` | `6.430` | `0.68%` | `+$5.9827` | `$1.8500` | `+$4.1327` | **REJECTED** (`$1.85` cloud cost eats `30.9%` of trading profit) |
| **D. Hybrid Local-First Swarm** | **Local `ace-decider-08b` (`173 tok/s`) + `ace-qwen3-17b` (`93 tok/s`) + `8%` Cloud Escalation** | **`40`** | **`90.00%`** | **`6.430`** | **`0.68%`** | **`+$5.9827`** | **`$0.0412`** | **`+$5.9415`** | **PROMOTED CHAMPION (#1)** |
