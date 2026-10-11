---
name: solana-volume-suite
description: QUARANTINED (MP21-D audit 2026-10-11; volume-faking code removed by owner decision 2026-10-11). Paper-only simulation remains. Do not invoke for artificial volume, market making or wallet funding; agents must not run it live.
compatibility: BOSSMAN, OpenCode, Claude-compatible agent skills
metadata:
  owner: bossman
  version: "1.0"
  category: finance-execution
---
> **QUARANTINED 2026-10-11.** Paper-only. Do not use this skill to generate volume or fund wallets through transit hops;
> the volume-faking modules (funding router, KOTH/FLOOR_DEFENSE) were removed by owner decision (commit 6f6a3873).
> See `docs/owner/MP21_D_TRADING_TOKEN_AUDIT_20261011.md`.


# Solana Volume Suite (paper-only simulation)

Use this skill for the paper-only simulation of Solana trading: hypothetical BUY/SELL records, price-impact and order-size checks, and encrypted key-vault handling without exposing private keys. Artificial volume generation, price support (floor defense, KOTH pushes) and wallet-link obfuscation were removed by owner decision 2026-10-11 and must not be re-added.

## When to Call This Skill

Invoke this skill when the prompt or task mentions:
- "Solana paper trading" / "бумажная симуляция на солане"
- "Pump.fun bonding curve" / "Raydium AMM" price-impact checks
- "Zero-Knowledge vault"
- "Запусти дашборд / command center solana volume"

## Quick Actions & CLI Commands

### 1. Launch Interactive Prototype (One-Command Start)
Starts the Command Center web dashboard on `http://localhost:8501`, auto-generates test sub-wallets, and opens the browser:
```bash
python solana_volume_suite/start_prototype.py
```

### 2. Mainnet Easy Connection Setup Wizard
Connects to Solana Mainnet-Beta (Helius, QuickNode, Triton, or public RPC), is a fail-closed stub in paper-only mode (no RPC calls, no key creation):
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
2. **No transaction building**: Pump.fun instruction/bundle builders, Jupiter swap/sign helpers and Jito compilation were removed (audit H4, 2026-10-11). Only paper plans remain (`core/paper_plan.py`); `core/paper_guard.py::refuse_live_signing` is the single refusal point.
3. **Price Impact Limiter**: Maximum 1.2% price impact allowed per transaction. Orders above the threshold are automatically sliced into micro-orders.
4. **No volume faking / link obfuscation**: no wash trading, no KOTH/floor-defense modes, no cascaded funding or wallet rotation to hide links (removed 2026-10-11; `funding_router.py` deleted, history keeps it).
5. **Kill Switch & Circuit Breaker**: Treasury loss is capped at `$40.00` per batch. If exceeded, the bot automatically triggers an emergency halt.

## API Control Contract

| Endpoint | Method | Purpose |
|---|---|---|
| `GET /api/status` | GET | Real-time status, balances, loss metrics, and Liquidity Gate |
| `POST /api/orchestrator/start` | POST | Starts the paper simulation loop |
| `POST /api/orchestrator/stop` | POST | Kill switch immediately halts trading |
| `POST /api/vault/generate` | POST | Generates encrypted sub-wallet pool |
| `POST /api/sweep` | POST | Sweeps all remaining SOL to cold destination |
| `WS /ws/telemetry` | WebSocket | 1-second live telemetry stream |
