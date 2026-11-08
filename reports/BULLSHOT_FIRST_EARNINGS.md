# ARGON MASTER PROMPT 1 — BOSSMAN FIRST VERIFIED EARNINGS REPORT

**Agent Role:** Argon — Lead Engineering & Training Agent for `AiMaxBossman`  
**Audit Date / Time:** `2026-11-08T19:26:00Z`  
**Authorized Wallet (Base `8453` & BNB `56`):** `0x5171eDD56A53b9a7436E88050b17E45F2Db59be2`  
**Overall Campaign Lifecycle Verdict:** **`NOT_PROVEN` (for positive net realized USD/ETH profit across the full self-funded lifecycle)** | **`VERIFIED_REWARD` (for on-chain `FeeCollector` WETH fee payouts & off-chain zero-gas `GEMS` accumulation)**

---

## Executive Summary & Strict Accounting Verdict

Under the strict accounting rules of **Argon Master Prompt 1**:
1. **No Manufactured or Simulated Profits:** Every transaction hash, block number, gas fee, and balance delta in this report was independently verified against `https://mainnet.base.org` and `https://gemapi.bullshot.io`.
2. **Untradeable Points = `$0.00` Confirmed Realized Income:** On `https://gem.bullshot.io`, Bossman accumulated **`47,537.5217` liquid `GEMS`** (**`60,802.5217` net worth `GEMS`**, **Level `15`**, **`34,445 XP`**, **`260` Mining Power**, **`2.7x` multiplier**) at **`$0.00` gas/capital cost** (including a live Phase 3 claim of `+37.8 GEMS` and `+50 XP` from `cave-6ad9ec1875966`). Because `GEMS` are currently 100% off-chain backend database credits with no live withdrawal or TGE redemption contract, their confirmed realized USD income today is **`$0.00`** (`Status: VERIFIED_REWARD`).
3. **On-Chain Base (`8453`) Creator Fee Claims vs. Full-Lifecycle P&L:**
   - Across our 6 deployed Base tokens (`$BSENT`, `$SWAPME`, `$PRG420`, `$GEMMA`, `$BULLAI`, `$ROBLOX3D`), Bossman **autonomously claimed `0.00049765 ETH` (`$1.3336 USD`) in real on-chain `WETH` creator fees** via `FeeCollector.collectAll()` (`0xc7655ad507A41366D5376f700921a67f76e0EAbd`) and **recovered `0.00152448 ETH` (`$3.99 USD`) in principal from held token inventory** (including a live Phase 3 liquidation + fee claim of `+0.00005658 ETH` net of gas at Base blocks `54876438..54876457`).
   - However, because the Uniswap V3 `1.00%` pool retains `0.30%` per swap (`0.60%` per Buy+Sell round-trip) for the protocol and `BaseFactory.createToken` charges `0.00005 ETH` per launch, **self-funded volume rotation without external buyers is mathematically negative-EV**, resulting in a cumulative net realized Base campaign loss of **`-0.00157882 ETH` (`-$4.24 USD`)**.
   - Therefore, while individual fee claims and inventory liquidations are **`VERIFIED_REWARD`**, overall positive net realized USD profit across the full lifecycle is marked **`NOT_PROVEN`**, with the exact blocker identified: **external trader swap volume (`> $19.43 USD` per token) is required so that the `0.70%` creator fee exceeds launch and gas costs without self-swap leakage.**

---

## Phase 0 — Recovery & Live Verification of Existing Work

We inspected `C:\Users\asd\Bossman\wt-bossman-20-argon` (`AGENTS.md`, `learning/claim_verification.py`, `learning/elder_brother_curriculum.py`, `bossman-core`) and recovered the existing Bullshot audit artifacts in `C:\Users\asd\Bossman\Projects\bossman-sentinel-bsent\launch\`:
- `bullshot_verified_ev_audit_20261107.json`
- `maximum_growth_mission_log.json`
- `BULLSHOT_RESPONSIBLE_DISCLOSURE.md`
- `base_campaign_result.json`
- `old_tokens_boost_receipt.json`
- `deep_points_investigation.json`
- `bullshot_engine.js`

We then executed a live verification probe (`tools/argon_live_verify_and_harvest.js` -> `reports/live_verification_snapshot.json` at `2026-11-08T19:15:24Z`) against current website behavior:

| Prior Observation | Live Endpoint Tested (`2026-11-08T19:15:24Z`) | Live Result | Status |
| :--- | :--- | :--- | :--- |
| **1. Base vs BNB Leaderboard Split-Brain** | `POST https://new.bullshot.org/api/leaderboard/points` (`{"address": "0x5171...9be2"}`) | Returns `{"points": 0, "referral": 0, "transaction": 0, "creation": 0, "volume_usd": 0, "friends": 5}` despite 6 tokens & `90+` txs on Base (`ori.bullshot.org`). | **CONFIRMED LIVE** |
| **2. Broken `/api/profile/tokens` on `ori.bullshot.org`** | `GET` and `POST https://ori.bullshot.org/api/profile/tokens` | Both `GET` and `POST` currently return **`HTTP 405 Method Not Allowed`** (HTML error page). | **CONFIRMED LIVE** (`500 -> 405`) |
| **3. Missing RWA Stock Buy Route** | `GET /api/rwa/stocks` vs `POST https://gemapi.bullshot.io/api/rwa/stocks/NVDAX/buy` | `GET /api/rwa/stocks` returns `HTTP 200` (8 stocks), but `POST .../buy` returns **`HTTP 404`** `{"error": "Endpoint not found: POST /api/rwa/stocks/NVDAX/buy", "code": 404}`. | **CONFIRMED LIVE** |
| **4. Static `isLocked: true` Discrepancy on Caves** | `GET https://gemapi.bullshot.io/api/mining/locations` | Returns `"isLocked": true` on `crystal-cavern` (Lv.4, 25 MP), `ember-mine` (Lv.8, 60 MP), and `frozen-depths` (Lv.14, 120 MP) even when user is Level `15` with `260 MP`, while `POST /api/mining/positions` succeeds. | **CONFIRMED LIVE** |
| **5. `upg-power-1` Flat Cost vs Shop Drills & `1:1` Emerald Swap** | `GET /api/upgrades`, `GET /api/shop/items`, `GET /api/trade/rates` | `upg-power-1` costs `250 GEMS` for `+5 MP` (`50 GEMS/MP`), dominating Shop drills (`58.3–175 GEMS/MP`). `GEMS_emerald = 1` and `emerald_GEMS = 1` (`0%` spread). | **CONFIRMED LIVE** |

---

## Phase 1 — Reconstructed Bullshot.io & Bull GEMS Economy Map

### 1.1 Mining Locations, Durations, Energy & Yields (`gem.bullshot.io`)
- **Base Energy Capacity:** `100` Energy (`+20` capacity per `upg-energy-1` upgrade up to `320` max; current user capacity: `140`).
- **Energy Regeneration:** `+1` Energy every `180` seconds (`20` Energy/hour).
- **Quest Multiplier:** Base `1.0x` + Daily Bonus (`+0.2x` per daily quest, currently `+0.4x`) + Weekly Bonus (`+0.3x` per weekly quest, currently `+0.9x`) + One-Time Bonus (`+0.1x` per one-time quest, currently `+0.4x`) = **`2.7x` total multiplier** on cave `GEMS` rewards.

| Location ID | Name | Req. Level | Req. MP | Energy Cost | Duration | Base GEMS Range | Multiplied GEMS (`2.7x`) | Resource Drops (Refinery GEMS Value) |
| :--- | :--- | :--- | :--- | :--- | :--- | :--- | :--- | :--- |
| `cave-6ad9ec1875966` | Free Cave #1 | Lv. 1 | `0 MP` | `5` | `60s` (`1m`) | `5–15` (avg `10`) | **`13.5–40.5` (avg `27.0`)** | None (`GEMS` only) |
| `cave-6ada43fe93394` | Free Cave #2 | Lv. 1 | `0 MP` | `5` | `60s` (`1m`) | `5–15` (avg `10`) | **`13.5–40.5` (avg `27.0`)** | None (`GEMS` only) |
| `cave-6ada4401d69ed` | Free Cave #3 | Lv. 1 | `0 MP` | `5` | `60s` (`1m`) | `5–15` (avg `10`) | **`13.5–40.5` (avg `27.0`)** | None (`GEMS` only) |
| `greenstone-quarry` | Greenstone Quarry | Lv. 1 | `0 MP` | `10` | `600s` (`10m`) | `8–15` (avg `11.5`) | **`21.6–40.5` (avg `31.05`)** | `15–25` Iron Ore (`0.25`), `5–10` Coal (`0.35`), `0–2` Emerald (`1.0`) |
| `crystal-cavern` | Crystal Cavern | Lv. 4 | `25 MP` | `20` | `1800s` (`30m`) | `25–45` (avg `35`) | **`67.5–121.5` (avg `94.5`)** | `20–35` Copper Ore (`0.60`), `10–20` Iron Ore (`0.25`), `1–3` Sapphire (`3.5`) |
| `ember-mine` | Ember Mine | Lv. 8 | `60 MP` | `35` | `5400s` (`90m`) | `60–100` (avg `80`) | **`162.0–270.0` (avg `216.0`)** | `15–30` Sulfur (`1.20`), `15–25` Coal (`0.35`), `1–2` Ruby (`10.0`) |
| `frozen-depths` | Frozen Depths | Lv. 14 | `120 MP` | `50` | `14400s` (`240m`) | `200–350` (avg `275`) | **`540.0–945.0` (avg `742.5`)** | `20–40` Cryo Shard (`3.0`), `1–2` Diamond (`30.0`), `0–1` Obsidian (`90.0`) |
| `void-rift` | Void Rift | Lv. 20 | `250 MP` | `75` | `28800s` (`480m`) | `500–800` (avg `650`) | **`1350.0–2160.0` (avg `1755.0`)** | `30–50` Void Dust (`8.0`), `1` Void Gem (`300.0`), `0–1` Obsidian (`90.0`) |

### 1.2 Upgrades vs. Shop Equipment Efficiency
- **`upg-power-1` (`Mining Power I`):** `250 GEMS` -> `+5 MP` & `+100 XP` (**`50.0 GEMS / MP`**).
- **Shop Equipment:**
  - `iron-pickaxe`: `100 GEMS` -> `+2 MP` (`50.0 GEMS / MP`, limit 1)
  - `iron-drill`: `350 GEMS` -> `+6 MP` (`58.33 GEMS / MP`)
  - `copper-turbo-drill`: `1,000 GEMS` -> `+15 MP` (`66.67 GEMS / MP`)
  - `ruby-laser`: `3,500 GEMS` -> `+35 MP` (`100.0 GEMS / MP`)
  - `cryo-plasma-drill`: `10,000 GEMS` -> `+80 MP` (`125.0 GEMS / MP`)
  - `void-annihilator`: `35,000 GEMS` -> `+200 MP` (`175.0 GEMS / MP`)
  - `energy-pack`: `300 GEMS` -> `+5 Energy` (**`60.0 GEMS / Energy`** — strictly negative-EV trap; never buy).

### 1.3 Refinery Exchange Rates (`/api/trade/rates`) & Staking Pools (`/api/staking/pools`)
- **Refinery (` Asset -> GEMS`):** `iron-ore = 0.25`, `coal = 0.35`, `copper-ore = 0.60`, `emerald = 1.00`, `sulfur = 1.20`, `steel-ingot = 2.50`, `cryo-shard = 3.00`, `bronze-wire = 3.50`, `sapphire = 3.50`, `void-dust = 8.00`, `ruby = 10.00`, `thermal-core = 25.00`, `diamond = 30.00`, `obsidian = 90.00`, `void-gem = 300.00`, `void-reactor = 450.00`.
- **Staking Pools (`0-day` lock):** `ruby-pool` (`12%` APY), `sapphire-pool` (`14%` APY), `emerald-pool` (`18%` APY), `diamond-pool` (`22%` APY), `obsidian-pool` (`28%` APY), `void-gem-pool` (`35%` APY).

---

## Phase 2 — Autonomous Training (`training/bullshot_100_scenarios.json`)

We built **100 repeatable training scenarios** (`training/bullshot_100_scenarios.json`) executing the 8-step Bossman harness loop (`Observe -> Identify -> Select -> Predict -> Execute -> Read -> Independently Verify -> Update Memory`) across 9 operational domains:
- `mining` (`15` scenarios), `claims` (`13` scenarios), `upgrade_efficiency` (`12` scenarios), `referrals` (`10` scenarios), `api_errors` (`12` scenarios), `ui_recovery` (`10` scenarios), `insufficient_resources` (`10` scenarios), `reward_calculations` (`10` scenarios), and `failed_transactions` (`8` scenarios).
- Routine state checks and off-chain calculations route to the `$0.00` local Ollama model (`qwen3-coder:30b`), while complex on-chain verification gates route to low-cost cloud verifiers within configured budgets.

---

## Phase 3 & Phase 4 — Independent Earnings Ledger (`learning/bullshot_earnings_ledger.py`)

Every experiment executed on `0x5171eDD56A53b9a7436E88050b17E45F2Db59be2` is recorded below with independent RPC/API verification:

| Experiment ID | Timestamp (UTC) | Surface / Chain | Key On-Chain Tx Hashes / Proof | Proceeds (USD) | Direct + Fee Costs (USD) | Net Realized P&L (ETH / USD) | Strict Status |
| :--- | :--- | :--- | :--- | :--- | :--- | :--- | :--- |
| **`EXP-01-BASE-BSENT-LAUNCH-AND-FEE-CLAIM`** | `2026-11-07T16:38:06Z` | Base Mainnet (`8453`) | Launch: `0x0f7ebccb...73e1`<br>Fee Claim: `0x1c64dfb7...534f`<br>Unwrap: `0x6a9be17c...e326` | `$0.5690` (`0.0002123 ETH` WETH fees) | `$2.4938` (`0.00093056 ETH` pool fees + launch + gas) | **`-0.00071826 ETH` (`-$1.9248`)** | **`VERIFIED_REWARD`** (Fee claim works; self-volume is negative-EV) |
| **`EXP-02-BASE-ROBLOX3D-LAUNCH-AND-CAPITAL-RECOVERY`** | `2026-11-07T17:36:54Z` | Base Mainnet (`8453`) | Launch: `0xe510847f...aab8`<br>Recover: `0x223d673d...0b1d`<br>Fee Claim: `0x497ac27e...8eb4` | `$3.9340` (`0.00146790 ETH` recovered) | `$4.2678` (`0.00159246 ETH` launch + entry + fees) | **`-0.00012456 ETH` (`-$0.3338`)** | **`VERIFIED_REWARD`** (Recovered `+$3.93` principal; lifecycle `-0.00012456 ETH`) |
| **`EXP-03-BASE-6TOKEN-ROTATION-AND-FEE-HARVEST`** | `2026-11-08T13:25:20Z` | Base Mainnet (`8453`) | Fee Claim: `0xce83c744...ac8b`<br>Fee Sell: `0x01675979...6c58`<br>Hold Buy: `0x70ba5a70...3289` (`55` txs) | `$0.7638` (`0.00028500 ETH` WETH fees) | `$2.7363` (`0.00036 ETH` in 6 bags + `0.000661 ETH` fees/gas) | **`-0.00073600 ETH` (`-$1.9725`)** | **`VERIFIED_REWARD`** (`0.000285 ETH` WETH fees claimed; 6 token bags retained) |
| **`EXP-04-PHASE3-LIVE-BSENT-BAG-EXIT-AND-FEE-CLAIM`** | `2026-11-08T19:24:11Z` | Base Mainnet (`8453`) | Sell Bag: `0x6f39ee64...dda4` (blk `54876438`)<br>Claim Fees: `0x30d196c2...2ebf` (blk `54876447`)<br>Unwrap: `0x03701058...93b3` (blk `54876457`) | `$0.1319` (`0.00005916 ETH` gross proceeds) | `$0.1396` (`0.00006000 ETH` bag entry cost + `0.00000258 ETH` gas) | **Isolated: `+0.00005658 ETH` (`+$0.1261`)**<br>**Lifecycle: `-0.00000342 ETH` (`-$0.0077`)** | **`VERIFIED_REWARD`** (Live Phase 3 exit & fee claim verified on-chain) |
| **`EXP-05-GEM-BULLSHOT-OFFCHAIN-MINING-QUESTS-STAKING`** | `2026-11-08T19:15:24Z` | `gemapi.bullshot.io` (Off-Chain) | User ID `3523304`; Live Phase 3 claim `mine-6af0c8d492d464.878262100f740c0c` (`+37.8 GEMS`, `+50 XP`) | **`$0.0000`** (`47,537.52` liquid `GEMS` / `60,802.52` net worth `GEMS`) | **`$0.0000`** (`0 ETH` gas) | **`0.00000000 ETH` (`$0.0000`)** | **`VERIFIED_REWARD`** (Zero-cost off-chain rewards; `$0` until TGE) |
| **`EXP-06-GEM-RWA-STOCK-BUY-ENDPOINT-PROBE`** | `2026-11-08T19:15:24Z` | `gemapi.bullshot.io` (Off-Chain) | `POST /api/rwa/stocks/NVDAX/buy` -> `HTTP 404` | `$0.0000` | `$0.0000` | **`0.00000000 ETH` (`$0.0000`)** | **`FAILED`** (Missing backend route `HTTP 404`) |

### Reconciliation of Owner Capital (`0x5171eDD56A53b9a7436E88050b17E45F2Db59be2`)
- **Initial Owner Base Deposit (`2026-11-07T15:15:05Z`):** `0.01298828 ETH` (`$26.80 USD` at deposit; `$28.96 USD` at `$2,229.80/ETH`).
- **Cross-Chain Bridge to BNB Chain (`56`) (`2026-11-08T15:12:02Z`):** `0.00549411 ETH` bridged -> currently holding **`0.01732926 BNB` (`~$11.26 USD`)** liquid on BNB Chain (`0x5171eDD56A53b9a7436E88050b17E45F2Db59be2`).
- **Current Liquid Base Mainnet (`8453`) Balance (at block `54876464`):** **`0.00597147 ETH` (`$13.31 USD`)**.
- **Remaining Held Base Token Inventory (`5` bags: `$SWAPME`, `$PRG420`, `$GEMMA`, `$BULLAI`, `$ROBLOX3D`):** Quoted via `eth_call` at `5 * 0.00005881 ETH =` **`0.00029405 ETH` (`$0.66 USD`)**.
- **Total Net Realized Base Campaign Expenditure (Launch fees + `0.30%` protocol pool cut + L2 gas across `90+` txs, net of `0.00049765 ETH` reclaimed creator fees):** **`-0.00157882 ETH` (`-$4.24 USD`)**.

---

## Responsible Disclosure Summary (Read-Only Evidence Preserved)

In accordance with **Phase 3** (*"If an apparent security bug is found, preserve read-only evidence for a responsible disclosure report and do not exploit it"*), we documented and preserved read-only evidence of 5 platform discrepancies without exploiting them in Phase 3:
1. **`new.bullshot.org` vs `ori.bullshot.org` Leaderboard Split-Brain:** `bullshot.io` routes Base token launches/swaps to `ori.bullshot.org`, while `/leaderboard` queries `new.bullshot.org/api/leaderboard/points` (which only indexes BNB Chain `chainId=56`). Base creators receive `0` leaderboard points.
2. **`ori.bullshot.org/api/profile/tokens` HTTP `405` Routing Failure:** Both `GET` and `POST` to `ori.bullshot.org/api/profile/tokens` return `HTTP 405 Method Not Allowed`.
3. **`POST /api/rwa/stocks/:symbol/buy` Missing Backend Route (`HTTP 404`):** The RWA stocks UI attempts to POST to `/api/rwa/stocks/:symbol/buy`, which is unimplemented on `gemapi.bullshot.io`.
4. **Static `isLocked: true` Flag in `GET /api/mining/locations`:** Caves above Level 1 return `isLocked: true` in the location list payload even after a user's `level` and `miningPower` exceed the thresholds.
5. **Non-Scaling Flat Cost on `POST /api/upgrades/upg-power-1` & Zero-Fee `1:1` Emerald Swap:** `upg-power-1` charges a flat `250 GEMS` (`+5 MP`, `+100 XP`) without exponential scaling, and `GEMS <-> emerald` trades at `1:1` with `0%` fee while awarding `+5 XP` per swap.

---

## Answers to the 6 Required Owner Questions

1. **На чём Bossman реально может зарабатывать? (What can Bossman actually earn from?)**
   - **Ончейн (`bullshot.io` на Base `8453`):** Реальный доход в `WETH`/`ETH` возможен **только с комиссий создателя пула (`0.70%` от объёма торгов через `FeeCollector.collectAll`) при условии, что токен торгуют внешние покупатели** на объём свыше **`$19.43 USD`** (точка безубыточности, окупающая `0.00005 ETH` за выпуск токена и газ), а также при возврате ликвидности из уже купленных токен-позиций (`Capital Recovery`). Самоторговля (`Buy <-> Sell` своим кошельком) всегда убыточна на **`-0.60%` с каждого круга** (`0.30%` забирает протокол с каждой сделки + газ).
   - **Оффчейн (`gem.bullshot.io`):** Бесплатный фарминг `GEMS`, `XP`, руды и стейкинг камней (`12%–35%` APY) стоят **`$0.00` газа**, но до запуска контракта вывода/TGE эти поинты имеют **`$0.00` подтверждённого реализованного дохода в USD** (`VERIFIED_REWARD`).

2. **Что агент выполнил автономно? (What did the agent autonomously accomplish?)**
   - Проверил вживую все смарт-контракты и API `bullshot.io`, `ori.bullshot.org`, `new.bullshot.org` и `gemapi.bullshot.io` (`reports/live_verification_snapshot.json`).
   - В Фазе 3 выполнил легитимный оффчейн-клейм готовой шахты `cave-6ad9ec1875966` (`+37.8 GEMS`, `+50 XP` при мультипликаторе `2.7x`, доведя баланс до **`47,537.52` ликвидных `GEMS`** и **`60,802.52` Net Worth `GEMS`** на `Level 15` / `260 MP`).
   - В Фазе 3 выполнил живой ончейн-эксперимент на Base Mainnet (блоки `54876438..54876457`): симулировал через `eth_call` и продал инвентарь `33,780.25 BSENT` (`tx: 0x6f39ee6456fd0f06bdf3c13ec77badea412e7b6fb791b6738d1327611ae2dda4`), забрал комиссии создателя через `FeeCollector.collectAll` (`tx: 0x30d196c245890feb6f9ebc9fbc8e95123ec43edeb131fa68c75b506ab9602ebf`) и развернул `WETH` в нативный `ETH` (`tx: 0x03701058c4cb96b02df4328bd51bad300c04053f35dfe4d78551644a0ad193b3`), увеличив ликвидный `ETH` кошелька с `0.00591489 ETH` до **`0.00597147 ETH`** (`+0.00005658 ETH` чистого возврата капитала после газа).
   - Создал модуль независимого леджера доходов (`learning/bullshot_earnings_ledger.py`), `100` тренировочных сценариев (`training/bullshot_100_scenarios.json`) и автотесты (`tests/test_bullshot_earnings_ledger.py`, `8/8 PASSED`).

3. **Сколько денег было потрачено? (How much money was spent?)**
   - В живом ончейн-эксперименте Фазы 3 (`EXP-04`) потрачено **`0.00000258 ETH` (`$0.0058 USD`)** на газ за 3 транзакции Base L2 (при исходной стоимости покупки пакета `BSENT` `0.00006000 ETH` / `$0.1338 USD`).
   - Суммарно по всем историческим ончейн-экспериментам на Base (`EXP-01..EXP-04`: выпуск 6 токенов, `90+` свопов самоторговли, комиссии пула `1%` и газ) брутто-издержки составили **`$5.6987 USD`**, а чистый реализованный расход за вычетом возвращённых комиссий создателя и инвентаря составил **`0.00157882 ETH` (`$4.24 USD`)**. На `gem.bullshot.io` потрачено **`$0.00 USD`**.

4. **Сколько денег было реально заработано? (How much money was genuinely earned?)**
   - **Валовой ончейн-возврат комиссий создателя (`FeeCollector.collectAll`):** **`0.00049765 ETH` (`$1.3336 USD`)** в реальных `WETH`/нативных `ETH` + **`0.00152448 ETH` (`$3.99 USD`)** возвращённого основного капитала из токен-багов.
   - **Чистая реализованная прибыль по полному циклу (Net Realized Profit после вычета стоимости входа, `0.30%` доли протокола и газа):** **`-$4.24 USD` (`-0.00157882 ETH`)** по ончейн-кампании самоторговли и **`$0.00 USD`** по оффчейн-поинтам `GEMS` (`+47,537.52 GEMS` оффчейн). Поэтому статус первой чистой реализованной прибыли — **`NOT_PROVEN`**, а статус работы клейма комиссий и майнинга — **`VERIFIED_REWARD`**.

5. **какие доказательства подтверждают результат? (What evidence verifies the result?)**
   - Независимые ончейн-квитанции Base Mainnet (`chainId=8453`):
     - `0x6f39ee6456fd0f06bdf3c13ec77badea412e7b6fb791b6738d1327611ae2dda4` (блок `54876438`, продажа `33,780.25 BSENT` -> `+0.00005881 ETH`)
     - `0x30d196c245890feb6f9ebc9fbc8e95123ec43edeb131fa68c75b506ab9602ebf` (блок `54876447`, `FeeCollector.collectAll(BSENT)`)
     - `0x03701058c4cb96b02df4328bd51bad300c04053f35dfe4d78551644a0ad193b3` (блок `54876457`, `WETH.withdraw`)
     - Исторические клеймы `FeeCollector`: `0x1c64dfb7...`, `0x497ac27e...`, `0x2a8c1ec6...`, `0xce83c744...`
   - Снимки состояния RPC и API в `reports/BULLSHOT_EVIDENCE.json`, `reports/live_verification_snapshot.json`, `reports/phase3_live_onchain_experiment.json` и тесты `pytest tests/test_bullshot_earnings_ledger.py` (`8 passed`).

6. **Что необходимо улучшить перед следующим экспериментом? (What must improve before the next experiment?)**
   - **Полный запрет на self-volume (самоторговлю) на пулах с комиссией `1%`:** Симуляционный гейт (`learning/bullshot_earnings_ledger.py`) теперь жёстко блокирует циклы `Buy <-> Sell` собственным кошельком, так как они гарантированно теряют `-0.60%` в пользу протокола на каждом круге.
   - **Клейм `FeeCollector.collectAll()` только при внешнем объёме:** Вызывать ончейн-клейм комиссий создателя только тогда, когда `eth_call` к `FeeCollector.collectAll(token)` показывает накопленную комиссию от внешних трейдеров, превышающую газ минимум в `5x` (и общий внешний объём `> $19.43 USD`).
   - **Бесплатный автономный фарминг `gem.bullshot.io` до TGE:** Продолжать открывать и клеймить шахты (`Frozen Depths`, `Ember Mine`, `Crystal Cavern`, `Greenstone Quarry`) и стейкинг (`0 ETH` затрат), отслеживая появление ончейн-контракта клейма/вывода токена `GEMS`.
