# Bullshot.io & Bull GEMS — Ranked Legitimate Earning Opportunities (Phase 1 & Phase 3)

**Audit Timestamp:** `2026-11-08T19:26:00Z`  
**Authorized Wallet:** `0x5171eDD56A53b9a7436E88050b17E45F2Db59be2` (Base Mainnet `8453` / BNB Chain `56`)  
**Accounting Rule:**
$$\text{Expected Net Earnings} = \text{Realizable Reward Value} - \text{Direct Costs} - \text{Transaction Fees} - \text{Execution Costs}$$
*Untradeable off-chain points, `GEMS`, `XP`, and ores are strictly valued at **`$0.00` confirmed realized USD income** until an on-chain withdrawal, TGE redemption, or cash payout contract is live.*

---

## 1. Executive Opportunity Ranking Table

| Rank | Opportunity ID & Name | Surface / Chain | On-Chain vs Backend | Capital Required | Realizable Reward Value (USD) | Direct Costs (USD) | Tx / Gas Fees (USD) | Execution Costs (USD) | Expected Net Earnings (USD) | Status / Verdict |
| :--- | :--- | :--- | :--- | :--- | :--- | :--- | :--- | :--- | :--- | :--- |
| **#1** | **OPP-01: Creator LP Fee Harvest from External Traders (`FeeCollector.collectAll`)** | `bullshot.io` (Base `8453`) | **On-Chain** (`0xc7655ad507A41366D5376f700921a67f76e0EAbd`) | `0.00005 ETH` (`$0.11–$0.13`) launch fee + `0.00001 ETH` gas | **`0.70%` of external swap volume** (`$0.70` per `$100` external volume in `WETH`) | `$0.1338` (one-time launch fee) | `$0.0058` (`collectAll` + `WETH.withdraw`) | `$0.00` (local harness) | **`+$0.5604` per `$100` external volume** (Break-even at **`$19.43`** external volume) | **`VERIFIED_REWARD`** (`0.00049765 ETH` / `$1.33` WETH fees claimed on-chain; positive net lifecycle profit requires external volume `> $19.43`) |
| **#2** | **OPP-02: Held Token Inventory Capital Recovery (`SwapRouter02.multicall`)** | `bullshot.io` (Base `8453`) | **On-Chain** (`0x2626664c2603336E57B271c5C0b26F421741e481`) | Existing held token bag (`33,780.25 BSENT` = `0.000060 ETH` entry cost) | `$0.1319` (`0.00005916 ETH` gross swap + creator fee proceeds) | `$0.1338` (`0.00006000 ETH` original entry cost) | `$0.0058` (`0.00000258 ETH` across 3 Base txs) | `$0.00` | **Isolated Recovery: `+$0.1261` (`+0.00005658 ETH`)** / **Full Lifecycle: `-$0.0077` (`-0.00000342 ETH`)** | **`VERIFIED_REWARD`** (Executed live in Phase 3 at blocks `54876438..54876457`) |
| **#3** | **OPP-03: Zero-Gas Underground Cave Mining (`2.7x` Quest Multiplier)** | `gem.bullshot.io` (`gemapi.bullshot.io`) | **Backend-Only** (Off-chain PostgreSQL/MySQL ledger) | **`$0.00`** (`0 ETH` gas; SIWE signature only) | **`$0.00` confirmed now** (`~338 GEMS/hr` = `~8,112 GEMS/day` unmonetized points) | `$0.00` | `$0.00` | `$0.00` (local scheduler) | **`$0.00` realized USD** (`+37.8 GEMS` & `+50 XP` verified live in Phase 3; future TGE optionality) | **`VERIFIED_REWARD`** (`47,537.52` liquid `GEMS` / `60,802.52` net worth `GEMS` accumulated at `$0.00` cost) |
| **#4** | **OPP-04: Zero-Lock Gem Staking Pools (`12%–35%` APY)** | `gem.bullshot.io` (`/api/staking/pools`) | **Backend-Only** | **`$0.00`** (`Ruby`, `Sapphire`, `Emerald`, `Diamond`, `Obsidian`, `Void Gem` from caves) | **`$0.00` confirmed now** (`12%` Ruby, `14%` Sapphire, `18%` Emerald, `22%` Diamond, `28%` Obsidian, `35%` Void Gem APY) | `$0.00` | `$0.00` | `$0.00` | **`$0.00` realized USD** (`2 Ruby` & `3 Sapphire` staked active in `stk-6af0b88e62a29` & `stk-6af07846ccf02`) | **`VERIFIED_REWARD`** (Accruing off-chain rewards at `$0.00` risk) |
| **#5** | **OPP-05: Legitimate Organic Referral Commission (`10%` of Cave Claims)** | `gem.bullshot.io` (`/api/referral/stats`) | **Backend-Only** | **`$0.00`** (Public link `https://gem.bullshot.io/r/0xdf407995`) | **`$0.00` confirmed now** (`10%` of referred miners' cave claims; `+261.56 GEMS` earned) | `$0.00` | `$0.00` | `$0.00` | **`$0.00` realized USD** (`+261.56 GEMS` claimed via `POST /api/referral/claim`) | **`VERIFIED_REWARD`** (Strictly organic only; Sybil/self-referrals prohibited) |
| **#6** | **OPP-06: Self-Funded Buy/Sell Volume Rotation on Own Base Pool** | `bullshot.io` (Base `8453`) | **On-Chain** | `0.003–0.010 ETH` working capital | `$0.70` creator rebate per `$100` volume (`0.70%`) | `$0.00` | **`$1.00` pool fee per `$100` volume (`1.00%`) + Base gas** | `$0.00` | **`-$0.305` per `$100` volume (`-0.60%` per Buy+Sell round-trip + gas)** | **`FAILED` (Negative-EV)** — Protocol retains `30%` of the `1%` pool fee (`0.30%` per swap). Never self-rotate for profit. |
| **#7** | **OPP-07: RWA Synthetic Stock Purchase & Staking (`NVDAX`, `AAPLX`, `TSLAX`)** | `gem.bullshot.io` (`/api/rwa/stocks`) | **Backend-Only** | `100+ GEMS` | `$0.00` | `$0.00` | `$0.00` | `$0.00` | **`$0.00` (Blocked by HTTP 404)** | **`FAILED`** — `POST /api/rwa/stocks/:symbol/buy` returns `HTTP 404 Endpoint not found`. |

---

## 2. Mathematical Proof of Break-Even & Why Self-Volume Cannot Generate Net Profit

1. **Uniswap V3 Pool Fee Split on Bullshot.io (Base `8453`)**:
   - Every token launched via `BaseFactory` (`0xfb8F5F0Ee20d7Ea08238e2eBcA8C246AC17843b9`) deploys a Uniswap V3 pool in the `1.00%` (`10000` bps) fee tier.
   - When `FeeCollector.collectAll(token)` (`0xc7655ad507A41366D5376f700921a67f76e0EAbd`) is called:
     - **Creator Share:** `70%` of collected LP fees (`0.70%` of swap volume).
     - **Protocol Share:** `30%` of collected LP fees (`0.30%` of swap volume).
2. **Self-Swap Round-Trip Loss (`Buy + Sell`)**:
   - For any self-funded swap amount $V$:
     $$\text{Net P\&L}_{\text{self-cycle}} = V \times (0.0070 - 0.0100)_{\text{buy}} + V \times (0.0070 - 0.0100)_{\text{sell}} - \text{Gas} = -0.0060\,V - \text{Gas}$$
   - Even with `70%` creator fee reclamation, every self-funded Buy+Sell cycle loses **`0.60%` of notional volume plus Base L2 gas**.
3. **External Volume Break-Even Threshold**:
   - With a token creation fee of `0.00005 ETH` (`~$0.1115–$0.1340 USD`) and `~0.00001 ETH` in total deployment + claim gas (`~$0.022 USD`), total fixed cost is `~$0.136 USD`.
   - Since external traders pay the `1.00%` pool fee and the creator receives `0.70%` (`0.0070`) in `WETH`/Token fees at zero principal cost to the creator:
     $$V_{\text{ext, break-even}} = \frac{\$0.136}{0.0070} = \$19.43\text{ USD}$$
   - Once external traders execute `> $19.43 USD` (`~0.0087 ETH`) of swaps on a Bossman-created Base token, `FeeCollector.collectAll(token)` transitions the token from `VERIFIED_REWARD` into **`VERIFIED_NET_PROFIT`**.
