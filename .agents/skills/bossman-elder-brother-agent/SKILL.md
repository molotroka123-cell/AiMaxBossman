---
name: bossman-elder-brother-agent
description: Complete computer-agent methodology and 15-branch operational playbook for Bossman local Qwen models (Observe -> Plan & Gate -> Act -> Independently Verify -> Rollback). Covers all 15 capability tree branches (jeff, ux, apps, models, cloud, computer, media, memory, agents, skills, plugins, ops, oss, commerce, devices) with verified LessonBook recall and executable coding recipes.
compatibility: BOSSMAN, Qwen-Operator L1, PC-Agent, OpenHands local sidecar
metadata:
  owner: bossman
  version: "2.2"
  category: computer-agent-core
  learned_from: Elder Brother 15-Branch Training & Verification Run (2026-10-15)
---

# Bossman Elder Brother Computer-Agent Skill (15 Directions)

## 1. Core Computer-Agent Loop (`Observe -> Plan & Gate -> Act -> Verify -> Rollback`)

1. **OBSERVE (Never Guess)**:
   - Never answer repository facts, line counts, constants, file paths, or UI states from parametric memory.
   - Always call the smallest read tool first (`grep`, `read_file`, `list_dir`, `run_python` read-only).
   - Every number, constant, or identifier in the final answer must appear verbatim in a tool observation from the current session.

2. **PLAN & GATE (T0–T3 & 15-Branch Boundaries)**:
   - Classify the target zone among the 15 canonical directions (`jeff`, `ux`, `apps`, `models`, `cloud`, `computer`, `media`, `memory`, `agents`, `skills`, `plugins`, `ops`, `oss`, `commerce`, `devices`).
   - Check the owner's hard rules before executing any write or external action:
     - **Secrets & Vault**: Keys, tokens, DPAPI blobs, and private owner contact details are never printed, stored in memory/README, or exported.
     - **Zero Unapproved Spend**: Only local Ollama models (`127.0.0.1:11434`) and free cloud workers (`:free`, NVIDIA NIM) unless the owner explicitly names a model and daily USD cap. Never rent GPUs on RunPod.
     - **Live Poker RTA Refusal**: Never give real-time action advice at a live real-money poker table. Poker GTO (`apps/poker-botlab/botlab/gto.py`, `poker-train-host`) is strictly for offline study, simulation, and post-session debriefs.
     - **Media & Consent**: Never swap faces or clone voices of third parties without explicit consent; personal media stays on the owner's PC and goes only to the owner's Telegram Pult.
     - **Destructive Actions & Scope**: Deleting user folders, overwriting outside the lab worktree, or pushing/merging into `main` requires the owner's explicit confirmation and green CI on the exact SHA.
     - **Constitution & Gates**: Never disable, skip, or weaken Constitution v2, its pin, or `.github/workflows` gates.
     - **License Integrity**: Flag AGPL/non-commercial code before integration and never strip author attribution.
   - When a request violates a rule: **refuse clearly** (`не буду` / `нельзя`), **name the exact rule**, and **propose the safe alternative**.

3. **ACT (Scoped Execution)**:
   - Recall verified lessons (`LessonBook.retrieve`) and executable coding recipes (`coding_recipes.executable_recipes`) before editing.
   - Confine code edits to the allowed paths in an isolated worktree (`lab/*`).
   - On Windows, use atomic temp-file writes (`allow_nan=False`) with bounded `PermissionError` retry on `os.replace` and `finally` cleanup.
   - Guard all numeric inputs (scores, budgets, costs) with `math.isfinite(x)` and reject `None`/`bool`/`NaN`/`±inf`.

4. **INDEPENDENTLY VERIFY (Receipt Proof)**:
   - A tool call or `HTTP 200` is not proof of visual or functional completion.
   - Run `verify_tests` and hidden holdout checks (`tools/tree_holdout/*`) in an independent verification pass.
   - Report `PASS` only when backed by a measured receipt in this session; otherwise report `NOT_RUN` / `не проверено`.

5. **ROLLBACK & STOP**:
   - Check the global `STOP` switch before and after every step. If `STOP` is active, halt immediately and write zero further actions.
   - If verification fails, revert the worktree diff (`git apply -R`) and diagnose the root cause.

## 2. 15-Branch Operational Map

| # | Branch | Primary Surface | Required Verification Receipt |
|---|---|---|---|
| 1 | `jeff` | `command-center/bcc/pit/stars_ledger.py` | Cited source lines; verified Telegram Stars receipt (paper != real) |
| 2 | `ux` | `command-center/bcc/features/game_bootstrap_v16.py` | Real viewport/screenshot check; otherwise `NOT_RUN` |
| 3 | `apps` | `apps/poker-botlab/botlab/gto.py`, `tools/exchange_volume/report.py` | Post-session GTO EV math; `21–20 Europe/Prague` `report.md` + `disputed.csv` SHA-256 |
| 4 | `models` | `command-center/bcc/features/qwen_operator.py` | Local Ollama digest, `CTX_LADDER` (`64k->128k->256k`), held-out delta |
| 5 | `cloud` | `command-center/bcc/provider_governance.py` | `free_only_refusal`, `avoid_family='qwen'` cross-family verifier |
| 6 | `computer` | `command-center/bcc/features/pc_agent.py`, `pc_agent_policy.py` | Window/file before+after state, screenshot/hash, `STOP` compliance |
| 7 | `media` | `command-center/bcc/telegram_companion/gen_refine.py` | Immutable preset snapshot, model name, elapsed time, output hash |
| 8 | `memory` | `learning/lessons.py`, `learning/retrieval.py` | `LessonBook.verify` with independent `external_tool` + `head_sha` |
| 9 | `agents` | `command-center/bcc/autonomy/goals.py`, `qwen_operator.py` | `math.isfinite` guard, `lab/*` isolation, owner gate for `release_main` |
| 10 | `skills` | `command-center/bcc/features/coding_recipes.py`, `skills.py` | `validate_recipe` (`ALLOWED_TOOLS`), real pytest exit code `0` |
| 11 | `plugins` | `command-center/bcc/oss/blender_mcp.py`, `v2/mcp_hub.py` | `MCP_POLICY_KEY` (`auto/ask/deny`), sandbox receipt, Pult-only delivery |
| 12 | `ops` | `bossman-core/bossman_v3/self_improvement/runner.py` | `source_sha == build_sha == runtime_sha`, `atomic_json` retry |
| 13 | `oss` | `command-center/bcc/oss/vibe_trading.py`, `fincept_terminal.py` | Upstream URL + SHA, license check (flag AGPL), paper-mode boundary |
| 14 | `commerce` | `command-center/bcc/features/reports_runner.py` | Separate demo, buyer demand, order, payment, revenue, and expenses |
| 15 | `devices` | `command-center/bcc/autonomy/stop.py` | Explicit owner pairing, immediate revoke, local-only media, `NOT_RUN` if unplugged |
