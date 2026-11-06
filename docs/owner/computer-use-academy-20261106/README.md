# AiMaxBossman Computer Use Academy — 500 cases (06.11.2026)

**Status: SCENARIO_DESIGNS_ONLY / NOT_RUN.** The 500 lines in `SCENARIOS_500.jsonl` are scenario specifications, NOT 500 action trajectories, not screenshots, not completed tests and not evidence of actual fine-tuning. No owner PC was accessed in this GitHub contribution.

## Counts and holdout
- Windows approved desktop apps: **125** (25 families × five conditions)
- Roblox Studio sandbox: **125** (25 × five)
- Offline synthetic browser: **100** (20 × five)
- Isolated cross-app project workflows: **75** (15 × five)
- Recovery/safety negative controls: **75** (15 × five)
- Total **100 families, 500 cases**; **300 train, 100 validation, 100 sealed test**. Split is by whole family, so sibling variants never leak between training and holdout.
- Five environment conditions: standard, 150% UI scaling, distractor window, partially completed dark-theme task, app restart with re-acquisition. These are *fixture requirements*, not proof fixtures currently exist.

## Real executor boundaries (read root AGENTS.md)
- Current Bossman `computer.act` desktop target list is restricted to **Notepad, Calculator, Character Map, Paint**, and owner approval is **per action**. `computer.act` must NEVER be extended to Roblox or browsers simply to satisfy these scenarios.
- Roblox scenarios require a **separately approved project-bound executor**, a disposable place and real F5 playtest receipts. If no such executor is authorized: **BLOCKED**, not PASS.
- Browser scenarios require existing approved browser tooling and a **synthetic offline/loopback mock website**, never a real logged-in browser session.
- Cross-app and recovery cases require **isolated fixtures**, exact permissions, negative-control assertions, independent result verification and rollback.
- No private files, screenshots, keys, passwords, real customer data, production apps or owner chats in public GitHub or cloud models. Cloud reviewers can receive only sanitized synthetic text/code within the existing allowed project scope, and cannot control live Windows.
- STOP, approvals, immutable core, budgets and external-action restrictions are not training variables. RunPod / GPU rental is not authorized.

## Files
- `SCENARIOS_500.jsonl`: each case contains `id`, `family`, `category`, `split`, `variant`, `execution_lane`, `fixture`, `task`, `success_oracle`, `negative_control`, `cleanup`, `status=NOT_RUN`.
- `ARGON_MASTER_PROMPT_RU.md`: supervisor prompt for Argon with cheap cloud routing, staged PC training and then defensive cybersec.
- `verify_catalog.py`: static corpus integrity validation. Not a GUI benchmark and not a live training run.

## Required workflow before real training
1. Read root `AGENTS.md`, `CLAUDE.md`, `bossman-core/docs/COMPUTER_USE_OPERATIONS.md`, `docs/security/COMPUTER_USE_THREAT_MODEL.md`, and existing owner LoRA reports/model manifests. Observe actual branch SHA and never reset other agents' work.
2. Run `python docs/owner/computer-use-academy-20261106/verify_catalog.py`.
3. Implement **10 sandbox fixtures and independent machine-readable oracles** from *train-only* families. Show RED/negative control before implementing a passing harness. Do not force unsupported app access.
4. Baseline base Qwen3-VL vs today's Bossman LoRA through *real allowed local executor*, owner approvals and full receipt. Capture action outcome using fresh UIA/screenshot after each step. If local PC is unavailable: **NOT_RUN**.
5. Record verified teacher trajectories and failure corrections privately. Confirm multimodal vision tensors and gradients before attempting new LoRA. Baseline first, train only on validated examples, keep existing adapter immutable.
6. At acceptance, compare new vs current vs base on a sealed holdout exactly once. Never change holdout from train outcomes; add new independent external transfer tests for robust claims.
7. When Computer Use is adequately verified, perform **separate** cybersec static audit, then optional defensive synthetic simulation only under existing triple sandbox gate and owner permission.

## Evidence required for claims
`case_id, git_sha, checkpoint_sha, fixture_hash, windows_hwnd_pid, approvals, sanitized_private_trace, oracle_expected, oracle_actual, negative_control_status, stop_status, latency_ms, token_usage, cost_usd, verdict, independent_verifier`.

Keep actual sensitive traces only on local encrypted owner-owned storage. Public audit publishes aggregate scores and hashes, not images or machine secrets.

A catalog-integrity PASS does NOT imply GUI control, fine-tuning, cybersec, or live acceptance success.
