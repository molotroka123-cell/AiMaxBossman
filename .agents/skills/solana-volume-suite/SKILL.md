---
name: solana-volume-suite
description: QUARANTINED (MP21-D audit 2026-10-11). Do not invoke. The suite generates artificial volume and hides wallet relationships; it is not a legitimate earning method and agents must not run it.
compatibility: BOSSMAN, OpenCode, Claude-compatible agent skills
metadata:
  owner: bossman
  version: "1.0"
  category: finance-execution
---
> **QUARANTINED 2026-10-11.** Do not use this skill, do not run its commands, do not generate volume or
> fund wallets through transit hops. See `docs/owner/MP21_D_TRADING_TOKEN_AUDIT_20261011.md`.

# Solana AI Volume Suite & Autonomous Market Maker

Use this skill whenever the user or autonomous agent needs to generate volume, provide market-making liquidity, run Pump.fun bonding curve pushes, protect swaps with Jito MEV bundles, or manage Solana sub-wallet clusters without exposing private keys.

## When to Call This Skill

Invoke this skill when the prompt or task mentions:
- "Solana volume bot" / "волюм-бот на солане" / "маркет-мейкер"
- "Сгенерируй объем" / "Pump.fun bonding curve" / "Raydium AMM volume"
- "Jito MEV protection" / "сэндвич-атаки" / "Zero-Knowledge vault"
- "Anti-Bubblemaps" / "каскадное финансирование"
- "Запусти дашборд / command center solana volume"

## Quick Actions & CLI Commands

### 1. Launch Interactive Prototype (One-Command Start)
Starts the Command Center web dashboard on `http://localhost:8501`, auto-generates test sub-wallets, and opens the browser:
```bash
python solana_volume_suite/start_prototype.py
```

### 2. Mainnet Easy Connection Setup Wizard
Connects to Solana Mainnet-Beta (Helius, QuickNode, Triton, or public RPC), checks connectivity, validates target token mint, sets Jito tip accounts, and displays anti-clustering funding addresses:
```bash
python solana_volume_suite/setup_mainnet.py
```

### 3. Generate New Encrypted Sub-Wallet Pool
Generates 10–50 sub-wallets encrypted with AES-256-GCM + PBKDF2 (100k iterations):
```bash
python solana_volume_suite/scripts/generate_vault.py --count 20 --password "YourVaultPassword123!"
```

### 4. Run Test Suite
```bash
python -m pytest solana_volume_suite/tests/ tests/test_solana_safety.py -v
```

## Architectural Invariants

1. **Zero-Knowledge Key Isolation**: Raw private keys are never exposed in prompt contexts or logs. The AI LLM operates solely with virtual wallet IDs (`wallet_0` ... `wallet_49`).
2. **Jito-Only Invariant**: Trading swaps are routed exclusively through private atomic Jito MEV bundles (no public mempool transactions).
3. **Price Impact Limiter**: Maximum 1.2% price impact allowed per transaction. Orders above the threshold are automatically sliced into micro-orders.
4. **Anti-Bubblemaps Funding**: Master wallet never funds sub-wallets directly in a 1-to-N fanout. It cascades through 3 ephemeral transit wallets to sever on-chain graphs.
5. **Poisson & Pareto Distributions**: Volume amounts follow Pareto distributions with non-round numbers (no 0.1, 0.5, 1.0 SOL), and inter-transaction delays follow Poisson arrival pacing (3s to 120s).
6. **Kill Switch & Circuit Breaker**: Treasury loss is capped at `$40.00` per batch. If exceeded, the bot automatically triggers an emergency halt.

## API Control Contract

| Endpoint | Method | Purpose |
|---|---|---|
| `GET /api/status` | GET | Real-time status, balances, loss metrics, and Liquidity Gate |
| `POST /api/orchestrator/start` | POST | Starts autonomous volume loop |
| `POST /api/orchestrator/stop` | POST | Kill switch immediately halts trading |
| `POST /api/vault/generate` | POST | Generates encrypted sub-wallet pool |
| `POST /api/sweep` | POST | Sweeps all remaining SOL to cold destination |
| `WS /ws/telemetry` | WebSocket | 1-second live telemetry stream |
