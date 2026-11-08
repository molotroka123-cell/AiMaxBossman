---
name: bulls-volume-and-p2p-consolidation
description: Autonomous Bullshot.io (Base Mainnet 8453) old-token volume & held-balance rotation with 70% LP fee rebate + Gem.Bullshot.io zero-fee P2P market GEMS consolidation & achievement cascade.
---

# Bulls (`bullshot.io` & `gem.bullshot.io`) Volume, Balance & P2P Consolidation Skill

## 1. Base Mainnet (`8453`) Old-Token Volume & Held-Balance Rotation (`bullshot.io`)
When boosting trading volume and on-chain held balances across existing ecosystem tokens on `bullshot.io` (`$BSENT`, `$SWAPME`, `$PRG420`, `$GEMMA`, `$BULLAI`, `$ROBLOX3D`):
1. **Core Contracts on Base Mainnet (`chainId = 8453`)**:
   - `BASE_FACTORY`: `0xfb8F5F0Ee20d7Ea08238e2eBcA8C246AC17843b9`
   - `SWAP_ROUTER` (`Uniswap V3 SwapRouter02`): `0x2626664c2603336E57B271c5C0b26F421741e481`
   - `FEE_COLLECTOR` (`Txt` in `bullshot.io` bundle): `0xc7655ad507A41366D5376f700921a67f76e0EAbd` (`collectAll(address)` selector: `0xd657c9e7`)
   - `WETH`: `0x4200000000000000000000000000000000000006`
2. **Execution Loop (`boost_old_tokens_bulls.js`)**:
   - Verify `allowance(wallet, SWAP_ROUTER) >= 10^27`; if lower, call `approve(SWAP_ROUTER, MaxUint256)`.
   - Run Buy (`exactInputSingle` with `WETH -> Token`, `fee = 10000`) <-> Sell (`multicall([exactInputSingle(Token -> WETH, recipient = SWAP_ROUTER), unwrapWETH9(1, wallet)])`) cycles (`0.003 ETH` per leg).
   - Call `FeeCollector.collectAll(tokenAddress)` on `0xc7655ad507A41366D5376f700921a67f76e0EAbd` to reclaim **70% of the 1% pool fee** in `WETH` and `Token`.
   - Sell claimed token fees via `multicall` and sync backend state via `POST https://ori.bullshot.org/api/fees/sync`.
   - Execute a **final Green Hold Buy (`0.00006 ETH` = `~33,780 tokens`)** on each token and **retain** that balance on `Master` so:
     - `bullshot.io/profile/<wallet>` (`sht(h)` on-chain `balanceOf` multicall) displays all 6 coins under **Held Coins**.
     - `bullshot.io/tokens/<token>?chain=base` (`As()` on-chain `Hht` check) displays `Master` (`CREATOR`) in the **Holders** tab.
   - Unwrap all accumulated `WETH` via `WETH.withdraw(balance)` so **>95% of Base ETH equity** is preserved.

## 2. Bull Gems (`gem.bullshot.io`) Zero-Fee P2P Consolidation & Achievement Cascade
To rapidly scale liquid `GEMS` and Mining Power on `Master` (`0x5171eDD56A53b9a7436E88050b17E45F2Db59be2`):
1. **Maker Fleet Achievement Bootstrap**:
   - Register each sub-wallet (`Maker_01..Maker_05`) with `referralCode = "0xdf407995"`.
   - Claim social quests (`+600 GEMS`), run `10` zero-fee `1:1` swaps (`GEMS <-> emerald`, `+50 XP` each) to unlock `ten_swaps` (`+750 GEMS`), `level_5` (`+500 GEMS`), and `level_10` (`+1,500 GEMS`), and complete 1 P2P sale for `first_sale` (`+500 GEMS`) = **`~3,850 GEMS` per Maker wallet**.
2. **Zero-Fee P2P Market Transfer (`POST /api/market/listings`)**:
   - The P2P market enforces `totalPrice <= 1000 * baseValue` (`baseValue = 5 GEMS` for `iron-ore`, so max `5,000 GEMS` per `1 iron-ore`) and charges **0% fee**.
   - List `1 iron-ore` from `Master` at `pricePerUnit = makerBalance` (`<= 5,000 GEMS`) and buy it from the Maker wallet via `POST /api/market/listings/:id/buy`.
   - Once `Master` crosses `10,000 GEMS` liquid, immediately claim `POST /api/achievements/gems_10k/claim` (**`+2,500 GEMS` Gem Baron bonus**).

## 3. Authenticated Browser Screenshots via Playwright Chrome
- When capturing `gem.bullshot.io` in headless Chrome (`chrome.exe`), always set:
  - `localStorage.setItem('rg_wallet_address', wallet)`
  - `localStorage.setItem('rg_jwt_token', jwt)`
  - `localStorage.setItem('rg_wallet_type', 'walletconnect')` (prevents `injected` provider check from auto-disconnecting when no extension is loaded).
