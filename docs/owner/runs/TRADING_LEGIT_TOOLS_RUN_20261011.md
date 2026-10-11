# Trading-support tools: run evidence (11.10.2026)

Branch `trade/legit-20261011` (worktree `wt-trading-legit-20261011`), base `origin/claude/bossman-final-convergence-hu2702` @ 6f6a3873 (wash-trading removal commit included).
Paper/synthetic only. No live orders, signing, keys, token creation, funds, paid requests, network calls.

## Items and files

| # | Item | Code | Tests |
|---|---|---|---|
| 1 | Paper strategy lab | `bossman-core/bossman/trading_learning/strategy_lab.py`; `metrics.py` (EV double count fixed) | `tests/test_trading_strategy_lab.py` (12) |
| 2 | MM paper analysis | `trading_learning/mm_sim.py`, fixture `tests/fixtures/trading_lab/recorded_book_sample.csv` (synthetic, in recorded format) | `tests/test_trading_mm_sim.py` (10) |
| 3 | Wallet/vault/control plane | `solana_volume_suite/dashboard/safety_app.py`, `core/key_vault/vault.py`, `scripts/generate_vault.py`, `core/wallet_policy.py` | `solana_volume_suite/tests/test_control_plane_hardening.py` (24) |
| 4 | Holder monitor | `trading_learning/holder_monitor.py`, fixtures `holders_*.json` | `tests/test_trading_holder_monitor.py` (10) |
| 5 | Live gate | `docs/owner/TRADING_LIVE_READINESS_GATE_20261011.md`, `trading_learning/live_gate.py` | `tests/test_trading_live_gate.py` (4) |

## Item 3: file:line before/after (audit MP21-D, paths on base e9ac78aa)

| Finding | Before | After |
|---|---|---|
| H1 vault deleted on failed decrypt (`orchestrator_loop.py:93-98`) and on short password (`safety_app.py:286-294`) | already removed by commit b9adda71 (loop no longer reads/creates/deletes vault; `/api/vault/generate` returns 403). Verified by grep: no `os.remove` in suite | regression tests: wrong password leaves vault bytes unchanged; create refuses overwrite (`vault.py` `FileExistsError`) |
| H1 6 vs 12 chars (`scripts/generate_vault.py:23` `len(password) < 6`) | script accepted 6, library required 12 | `MIN_PASSWORD_LENGTH = 12` in `core/key_vault/vault.py`, script uses it; script refuses an existing vault, writes a timestamped `.bak-` copy and exits 3; `--password` argv flag removed (getpass only) |
| H2 plaintext `VAULT_MASTER_PASSWORD` in `.env` (`setup_mainnet.py:127,133`) | already replaced by fail-closed stub (b9adda71) | test scans suite sources for any `VAULT_MASTER_PASSWORD=` write and that the stub writes nothing. No DPAPI facility exists in the repo (grep CryptProtectData/dpapi: none), so the policy is prompt-only (getpass) |
| H3 unauthenticated POSTs, non-empty-body bypass (`safety_app.py:183-200,219-271`) | any local process could POST; `{"x":1}` started the loop | `require_control` (token header `X-Control-Token`, `hmac.compare_digest`, foreign `Origin` rejected) on `/api/bot/start`, `/api/bot/sweep`, `/api/orchestrator/start`, `/api/sweep`, `/api/vault/generate`; token from `SVS_CONTROL_TOKEN` (>=16 chars) or random per process (printed to the local console only); `/api/bot/start` accepts exactly `{"paper_only": true}` (pydantic `extra=forbid`, `Literal[True]`); stop/kill-switch endpoints intentionally need no token |
| role-separated keys + per-wallet limits | role label only in `vault.get_sanitized_public_view` | `core/wallet_policy.py`: immutable roles, trade key cannot withdraw/transfer, treasury never trades, per-order and per-day caps, default deny |
| removed path | `core/funding_router.py` (cascade/rotation) removed by 6f6a3873; not touched | n/a |

Not changed (and why): PBKDF2 iterations stay 100k (changing breaks existing vault files unless iteration count is stored in the payload; recommended follow-up); H4 manual Pump.fun/Jupiter instruction assembly still unfixed (signing is already refused by the paper-only choke point).

## Commands and results

| Command | rc / result |
|---|---|
| `cd bossman-core && python -m pytest tests/test_trading_*.py -q -p no:cacheprovider` | 159 passed, 2 skipped (baseline before: 123 passed, 2 skipped) |
| `python -m pytest solana_volume_suite/tests tests/test_solana_safety.py -q -p no:cacheprovider` (isolated venv: pytest, pytest-asyncio, fastapi, httpx, cryptography, solana, solders, base58) | `solana_volume_suite/tests`: 77 passed (53 on base after the removal commit + 24 new); with `tests/test_solana_safety.py`: 99 passed |
| `python tools/ci_secret_scan.py` | PASS |

Known flake (pre-existing, not touched): `test_vault_security.py::test_pareto_distribution` failed once in 4 runs (random amount landing 0.2001, threshold 1e-4).

## Synthetic evidence outputs (NOT market evidence)

Negative control, 20 driftless random walks, random entries, realistic costs (fee 4 bp + half-spread 3 bp + slippage 5 bp per leg, partial fills, latency 1 bar):
mean net return per trade -0.235%; verdicts: LOSES_MONEY_AFTER_COSTS 18, NO_EDGE_PROVEN 2, EDGE_CANDIDATE 0.
Same strategy with zero costs: mean +0.008%; NO_EDGE_PROVEN 18, LOSES 1, EDGE_CANDIDATE 1 (a 1-in-20 false positive: multiple testing is real, and the gate requires G1-G11, not one CI).
Positive control (autocorrelated series): OOS 600 trades, net +0.403% per trade, CI [+0.245%, +0.555%], breaks even near 3x costs (x3 = -0.09%).

Market-making simulator (200 paths x 2000 steps), half-spread 0.05: informed fraction 0.15 -> mean +19.7 (spread +24.0, adverse -4.4); informed 0.5 -> +16.4 (spread +31.3, adverse -14.9). Tight half-spread 0.01 with informed 0.3 -> mean -10.4, P(loss)=100%. The sign is set by the assumed informed share: the report says so and carries the "NOT proof of live profit" disclaimer.
Recorded-format replay on the synthetic sample: 10 blocks, mean +0.44 per block, CI [+0.14, +0.73]; fills require a trade strictly through the quote (touch fills at 25% probability).

Holder monitor fixtures: concentrated -> VERY_HIGH (top10 94.2%, Gini 0.90, HHI 0.188, 55.6% held by <7d wallets, pool excluded); distributed -> LOW (top10 18.8%, Gini 0.33). Panel text is risk wording only; tests assert absence of evasion vocabulary.
RPC adapter: off by default (needs `enabled=True` AND `BOSSMAN_HOLDER_RPC=1`), read-only method allowlist, no keys, min interval 1 s, injectable transport (tests make no network calls).
