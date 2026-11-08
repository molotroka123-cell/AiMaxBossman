# ELYSIUM NETWORK INTELLIGENCE AUDIT (PHASE 1)

- **Audit Date:** `2026-11-08T19:42:00Z` (1 day prior to scheduled public mainnet launch on **November 9, 2026**)
- **Target Network:** **Elysium** — Kinetiq's EVM-compatible Layer 2 built on Arbitrum Orbit and settled on Hyperliquid's HyperEVM (`Chain ID 999`)
- **Chain Disambiguation Guard:** Verified in [`ElysiumNetworkConfig.validate_chain_id()`](file:///C:/Users/asd/Bossman/wt-bossman-20-argon/learning/elysium_trading_intelligence.py#L61-L67). **Never** confuse Kinetiq's Elysium L2 (`Mainnet 99901` / `Testnet 99801`) with the unrelated Vulcan Forged Elysium gaming chain (`Chain ID 1339`).

---

## 1. Verified Chain Parameters & Live RPC Probe Results

All RPC endpoints were probed directly via JSON-RPC (`eth_chainId`, `eth_blockNumber`, `eth_gasPrice`, `eth_getBlockByNumber`) on `2026-11-08`:

| Network Layer | Chain ID (Dec / Hex) | Official RPC Endpoint | Live Status (`2026-11-08`) | Observed Block Height & Cadence | Native Gas Asset & Base Fee |
| :--- | :--- | :--- | :--- | :--- | :--- |
| **Elysium Mainnet (Kinetiq L2)** | `99901` (`0x1863d`) | `https://rpc.elysium.kinetiq.xyz` | **PRE-LAUNCH TLS GATED** (`SSLV3_ALERT_HANDSHAKE_FAILURE` prior to Nov 9, 2026 launch) | Launches publicly **Nov 9, 2026** (`~100–165 ms` target block time, `300 Mgas/s`) | **HYPE** (`18` decimals) |
| **Elysium Testnet (Kinetiq L2)** | `99801` (`0x185d9`) | `https://testnet-rpc.elysium.kinetiq.xyz` | **LIVE & VERIFIED** | Block `26,311,975+` (`~6.0 blocks/sec` = **`~165 ms` block time**) | **HYPE** (`18` decimals), `gasPrice = 0.10 Gwei` (`100,000,000` wei), `baseFee = 0.10003 Gwei` |
| **HyperEVM Mainnet (L1 Settlement Layer)** | `999` (`0x3e7`) | `https://rpc.hyperliquid.xyz/evm` | **LIVE & VERIFIED** | Block `50,741,350+` | **HYPE** (`18` decimals), `gasPrice = 0.346 Gwei` (`346,228,169` wei) |

### Official Explorer, Bridge & Analytics Endpoints
- **Official Portal & Bridge:** `https://elysium.kinetiq.xyz` (bridges native `HYPE` 1:1 from HyperEVM `999` to Elysium `99901`/`99801`; `https://elysp.com` also provides bridge/trading routing)
- **Official Testnet Explorer:** `https://elysium.kinetiq.xyz/testnet-explorer`
- **Market Feeds & Analytics:** `https://elyscreener.com` (real-time DEX & launchpad screener for Elysium L2)
- **Sequencer Architecture & Fee Economics:**
  - Powered by **Arbitrum Orbit (stock ArbOS)** with **First-Come-First-Served (FCFS)** sequencer ordering and **Conduit G2** sequencer infrastructure (`300 Mgas/s` capacity).
  - Because the sequencer processes transactions in FCFS arrival order rather than running a Priority Gas Auction (PGA), overpaying `maxPriorityFeePerGas` provides zero ordering advantage. Setting `maxFeePerGas = ceil(baseFee * 1.05)` (`~0.105 Gwei`) and `maxPriorityFeePerGas = 0` minimizes execution cost.
  - Sequencer net revenue is routed by Kinetiq into the **KNTQ Assistance Fund**, which executes programmatic market buybacks of `KNTQ` on HyperCore.
  - Custom precompiles (`EL1Read` and `EL1Write`) expose synchronous read/write access to Hyperliquid L1 (`HyperCore`) perp and spot orderbooks.

---

## 2. Verified Smart Contracts Discovered on Elysium Testnet (`99801`)

Instead of trusting third-party lists, we scanned blocks `26,307,000..26,312,050` on `https://testnet-rpc.elysium.kinetiq.xyz` and verified the deployed bytecode, storage getters (`factory()`, `WETH9()`, `token0()`, `token1()`, `fee()`, `slot0()`, `liquidity()`), and transaction receipts:

| Contract Role | Verified Address (`99801`) | Bytecode Size | On-Chain Verification Evidence |
| :--- | :--- | :--- | :--- |
| **ArbOS Precompile** | `0x00000000000000000000000000000000000a4b05` | `1` byte (`0xfe`) | Confirmed Arbitrum Orbit precompile (`selector 0x6bf6a42d` invoked every block) |
| **Wrapped HYPE (`WHYPE`)** | `0xcd57f65c2b0e5881cfc2e609f7cd53b746e1f234` | `3,574` bytes | Returned by `SwapRouter.WETH9()`; `name() = "Wrapped HYPE"`, `symbol() = "WHYPE"`, `decimals() = 18` |
| **Uniswap V3 Factory** | `0x520bf060f58cee054f725877279328f5f73a7893` | `23,365` bytes | Returned by `SwapRouter.factory()`; verified via `getPool(WHYPE, KALOS, fee)` across all 4 fee tiers |
| **Uniswap V3 SwapRouter** | `0x752f6816bc514e1eeff852b2eadcd96f6c0f9f3f` | `12,843` bytes | Observed executing `exactInputSingle` (`0x04e45aaf`) in live `tx: 0xa1e10c41c57d4a20e6f8dd702ef96afce3bb3051577c87a472065026025d412c` |
| **Launchpad / Token Factory** | `0xb94a38a4ac46970559e89e566f2486a3fc56be5a` | `20,766` bytes | Observed in `tx: 0xeb07cede19b63b44a4f13ead5249b4f2976417ac298c5a3dfca4cfd66cffc821` deploying `Elysium Test Token` (`ETT` `0x53c234f77ad1c174962c86fca9c6d2827aafffbf`) and bonding-curve vault `0x8919a7fb88f61b448ec21f1a6fa5f14d2a48b4ca` (`gasUsed: 343,689`) |
| **Active Traded Token (`KALOS`)** | `0x8a2201953af29e8be7700bcb53da3ce5cad18726` | `3,726` bytes | `name() = "KALOS"`, `symbol() = "KALOS"`, `decimals() = 18`, `totalSupply() = 1,000,000,000 KALOS` |

### Active `WHYPE / KALOS` Uniswap V3 Liquidity Pools on `99801`

| Fee Tier (`bps` / `%`) | Pool Contract Address | `WHYPE` Reserve | `KALOS` Reserve | Active In-Range Liquidity (`L`) | `eth_call` Quote for `0.001 HYPE` Buy |
| :--- | :--- | :--- | :--- | :--- | :--- |
| **`100` (`0.01%`)** | `0x30b402c6429ecea14fc1e0a8963ebbedd5b920c1` | `0.1592 WHYPE` | `4,757.58 KALOS` | `14,687,892,506,158,342,272` | `5.052186 KALOS` (`estGas: 149,478`) |
| **`500` (`0.05%`)** | `0x3cb12abff6060dff9f31837f3a66236ae2da1907` | `24.7632 WHYPE` | `114,206.18 KALOS` | `124,782,953,167,694,559,531` | **`5.056125 KALOS`** (`estGas: 149,558`) |
| **`3000` (`0.30%`)** | `0x224966692c8b849af834885fe07848ffd8c2c375` | `408.0896 WHYPE` | `2,086,123.86 KALOS` | `191,599,500,829,014,625,747` | `5.054042 KALOS` (`estGas: 143,230`) |
| **`10000` (`1.00%`)** | `0xc1ad66dfb1530a555a85eb167ffc9e4aa20e0771` | `77.2630 WHYPE` | `456,874.17 KALOS` | `163,401,561,511,012,188,204` | `5.053417 KALOS` (`estGas: 149,983`) |

---

## 3. Live On-Chain Round-Trip Cost & Gas Verification (`99801`)

Using `eth_call` and `eth_estimateGas` against `SwapRouter` (`0x752f6816bc514e1eeff852b2eadcd96f6c0f9f3f`) on the `fee=500` (`0.05%`) `WHYPE/KALOS` pool (`0x3cb12abff6060dff9f31837f3a66236ae2da1907`):
1. **Buy Leg (`0.00100000 HYPE -> KALOS`)**:
   - Output: `5.056125008816420731 KALOS`
   - Estimated Gas: `149,558` units (`0.00001496 HYPE` = `$0.00115 USD` at `$76.95/HYPE`)
2. **Sell Leg (`5.056125008816420731 KALOS -> WHYPE`)**:
   - Output: `0.000998910099118895 WHYPE`
   - Estimated Gas: `136,378` units (`0.00001364 HYPE` = `$0.00105 USD` at `$76.95/HYPE`)
3. **Total Verified Round-Trip Friction**:
   - **AMM Fee + Price Impact**: `0.00100000 - 0.00099891 = 0.00000109 HYPE` (**`10.9 bps` / `0.109%`**)
   - **Total Round-Trip Gas (`Buy + Sell`)**: `285,936` gas at `0.10 Gwei` = **`0.00002860 HYPE` (`$0.0022 USD`)**

---

## 4. Launchpad & DEX Ecosystem Landscape

- **Token Launchpads on Elysium:**
  - **ElysPump (`elysp.com`)**: Bonding-curve meme/token launchpad migrating graduated tokens into Uniswap V3 `WHYPE` pools.
  - **Ascend & Kleos**: Ecosystem token launchpads and liquidity bootstrapping venues.
  - **Testnet Launchpad Factory (`0xb94a38a4ac46970559e89e566f2486a3fc56be5a`)**: Verified on-chain factory deploying token + bonding curve vault pairs (`343,689` gas per launch).
- **Critical Launchpad Migration Hazard (`99.5% -> 100%` `curveLocked` Window)**:
  - When a bonding curve approaches `100%` graduation target, the curve contract sets a temporary `curveLocked` state while migrating `WHYPE` and token reserves to the Uniswap V3 pool. Swaps submitted to the bonding curve during this window revert on-chain. Our Phase 2 risk engine (`evaluate_token_risk`) hard-vetoes any token with `curve_locked == True` or `99.2% <= bonding_curve_pct < 100.0` before DEX graduation completes.
