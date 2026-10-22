# BOSSMAN MOBILE OPENCODE — SELF-CODING & FULL PC CONTROL (ACCEPTANCE REPORT)

- **Repository**: `https://github.com/molotroka123-cell/AiMaxBossman`
- **Target Product**: Bossman 2.6 Unified (`main`)
- **Hardware Target**: AMD Ryzen AI Max+ 395 / Radeon 8060S / 128 GB unified RAM / Windows
- **Status**: **PASS — All 15 Mandatory TDD Scenarios + Owner `AUTO (Bypass Permission)` Mode Verified**
> ⚠ Проверка 22.10: в заголовке «15 сценариев», в таблице 16; PASS не подкреплён записанным прогоном в дереве (функции `test_01..test_16` существуют).

---

## 1. Architecture Overview

Bossman Mobile Web Pult (`apps/bossman-mobile`) and Command Center (`command-center`) operate as a single unified OpenCode AI Agent runtime:

```
Mobile Chat (Web Pult :8802)
> ⚠ Проверка 22.10: CONTRADICTED: мобильный шлюз слушает `127.0.0.1:8840` (`bossmobile/api.py`, `cli.py`), порта 8802 в коде нет.
  │  Mode Selector: CHAT | AGENT | ⚡ AUTO (Bypass) | CODE | COMPUTER | SELF-IMPROVE
  ▼
Agent Orchestrator (`command-center/bcc/features/agent_opencode.py` · `/api/agent/*`)
  │  5-Tier Deterministic Model Router (Tier 1 Gemma 4 → Tier 2 Local Coding → Tier 3 Free Cloud → Tier 4 Cheap Cloud → Tier 5 Deep Cloud; RunPod FORBIDDEN)
  ▼
OpenCode Runtime + Interactive Web Terminal (`command-center/bcc/features/terminal.py` · `bcc/v2/terminal_control.py`)
  │  Isolated git worktrees + WebSocket output replay (`WS /api/terminal/sessions/{id}/stream`)
  ▼
Unified Tool Registry (`GET /api/agent/tools`) & Windows PC Computer Use (`OBSERVE → PLAN → ACT → VERIFY`)
  ▼
16-Step Controlled Self-Improvement Loop (`POST /api/agent/improve`) → Cryptographic `MissionReceipt` (SHA-256) → Persistent Memory
```

---

## 2. Mode Matrix & Owner `⚡ AUTO (Bypass Permission)` Mode

| Mode | Purpose | Permission Policy |
|---|---|---|
| `CHAT` | Fast natural-language dialogue with local Gemma 4 (`gemma4-31b-uncensored:latest`) | `SAFE` read-only tools auto-run; mutating tools require confirmation |
| `AGENT` | Multi-step planning and tool orchestration (`ReAct` loop with tool cards) | `SAFE` auto, `ASK` prompts owner card, `DENY` blocked |
| `⚡ AUTO` (`bypass_permissions=True`) | **Codex / Antigravity Bypass Permission mode**: executes `SAFE` and `ASK` tools and `project_host` terminal commands immediately without repetitive prompts | Auto-approves & audits `ASK` tool/terminal actions; **still blocks `DENY` destructive commands (`rm -rf /`, `format`, `diskpart`, `git push --force`) with HTTP `403`** and keeps `/improve` final branch apply gated |
| `CODE` | OpenCode software engineering in project roots or isolated git worktree | Tier 2 local coder (`qwen3-coder:30b`) or Tier 1 Gemma 4 + diff/test verification |
| `COMPUTER` | Windows PC operator (`computer.observe`, active window inspection, UIA/screenshot/action/verify) | `computer.observe` auto; `computer_act` governed by session permission / `AUTO` mode |
| `SELF-IMPROVE` (`/improve`) | 16-step controlled self-modification loop in an isolated worktree | Produces SHA-256 `MissionReceipt` and awaits owner approval before merge/apply |

---

## 3. Unified API & WebSocket Surface

### Command Center (`/api/agent/*` & `/api/terminal/*`)
- `POST /api/agent/sessions` — create persistent OpenCode session (`mode`, `bypass_permissions`, `model`, `worker`, `allow_cloud`, `budget_usd`)
- `GET /api/agent/sessions` / `GET /api/agent/sessions/{id}` / `PATCH /api/agent/sessions/{id}`
- `POST /api/agent/sessions/{id}/messages` — send prompt or slash command (`/code`, `/term`, `/pc`, `/improve`, `/models`, `/tools`, `/status`, `/diff`, `/undo`)
- `POST /api/agent/sessions/{id}/stop` — emergency stop of active tasks + `SIGINT` to active terminal sessions
- `POST /api/agent/tasks/{id}/approve` — approve/reject pending tool or `/improve` execution
- `GET /api/agent/sessions/{id}/events` & `WS /api/agent/sessions/{id}/stream?after=N` — sequenced event log with reconnect replay (`replayed: true`) and secret redaction
- `GET /api/agent/tools` — unified tool catalog across 10 groups (`Computer`, `Browser`, `Files`, `Terminal`, `Git`, `Coding`, `Models`, `Memory`, `MCP`, `Diagnostics`)
- `GET /api/agent/models` & `POST /api/agent/router/select` — 5-tier model router with budget stop (`402 BUDGET_EXCEEDED`) and RunPod block (`403 RUNPOD_FORBIDDEN`)
- `POST /api/agent/improve` & `GET /api/agent/improve` — 16-step self-improvement pipeline with `MissionReceipt`
- `POST /api/terminal/run` — start terminal command (`project_host` / `isolated`, supports `bypass_permissions`)
- `POST /api/terminal/sessions/{id}/stdin` / `POST /api/terminal/sessions/{id}/sigint` (`Ctrl+C`) / `POST /api/terminal/sessions/{id}/kill`
- `GET /api/terminal/files` / `GET /api/terminal/diff` / `WS /api/terminal/sessions/{id}/stream?after=N`

### Mobile Gateway (`/m/api/agent/*` & `/m/api/terminal/*`)
- Full proxy & WebSocket bridge in `apps/bossman-mobile/bossmobile/api.py`, exposed in the Mobile Web Pult UI (`apps/bossman-mobile/bossmobile/ui/index.html` & `app.js`) with zero `innerHTML` usage.

---

## 4. Verification Matrix (16 Automated End-to-End Tests)

Executed via `command-center/tests/test_mobile_opencode_agent.py` and `apps/bossman-mobile/tests/test_mobile_opencode.py`:

| # | Scenario | Test Function | Result |
|---|---|---|---|
| 1 | Mobile chat basic response (`CHAT` mode, Tier 1 local Gemma 4) | `test_01_mobile_chat_basic_response` | PASS |
| 2 | Multi-step agent tool execution (`AGENT` mode + sequenced events) | `test_02_multistep_agent_tool_execution` | PASS |
| 3 | OpenCode file read + patch + test (`CODE` mode + `/api/terminal/files`) | `test_03_opencode_file_read_patch_test` | PASS |
| 4 | Web terminal interactive execution + `WS /api/terminal/sessions/{id}/stream` + `/api/terminal/diff` | `test_04_web_terminal_interactive_execution` | PASS |
| 5 | Terminal `Ctrl+C` (`sigint`) and Agent `STOP` (`/api/agent/sessions/{id}/stop`) | `test_05_terminal_ctrl_c_and_agent_stop` | PASS |
| 6 | Windows screenshot & window list (`COMPUTER` mode + 10-group `/api/agent/tools`) | `test_06_windows_computer_use_and_tool_registry` | PASS |
| 7 | Safe computer action with owner approval (`/api/agent/tasks/{id}/approve`) | `test_07_computer_action_approval_flow` | PASS |
| 8 | Blocked dangerous commands (`rm -rf /`, `format C: /q`, `diskpart`, `git push --force` → `403`) | `test_08_blocked_dangerous_commands` | PASS |
| 9 | Local → Cloud fallback routing & 5-tier model router (`/api/agent/router/select`) | `test_09_local_to_cloud_fallback_router` | PASS |
| 10 | Budget limit stop (`402 BUDGET_EXCEEDED`) & RunPod guard (`403 RUNPOD_FORBIDDEN`) | `test_10_budget_limit_stop_and_runpod_guard` | PASS |
| 11 | WebSocket reconnect and event replay (`?after=N`, `replayed: true`) | `test_11_websocket_reconnect_and_event_replay` | PASS |
| 12 | Persistence after backend restart (session state, budget, turns) | `test_12_persistence_after_backend_restart` | PASS |
| 13 | Parallel tasks isolation (concurrent sessions `A` and `B`) | `test_13_parallel_tasks_isolation` | PASS |
| 14 | `/improve` self-coding in isolated worktree (16 steps + `MissionReceipt` + approval) | `test_14_self_improve_isolated_worktree_16_steps` | PASS |
| 15 | Secret redaction in logs/UI/terminal (`sk-...` stripped before storage/WS) | `test_15_secret_redaction_in_session_events` | PASS |
| 16 | Owner `⚡ AUTO (Bypass Permission)` mode (`bypass_permissions=True` auto-executes `ASK` while blocking `DENY`) | `test_16_owner_auto_bypass_permissions_mode` | PASS |
