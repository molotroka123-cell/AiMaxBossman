# Bossman 1.9 map: CMD / Terminal Run, Browser, Computer Use, Agentic Rave

This was a read-only pass on `claude/bossman-1.9-owner-bugtest-20260930` at HEAD `8cfa0481`. I did not run any tests. In this report:
- **EXISTS** means the code is present and wired in.
- **WIRED** means it is reachable from the API, UI or CLI.
- **STUB** means it is intentionally not implemented.
- **UNPROVEN** means there is no real-run evidence for it in the repo.

Features in `command-center/bcc/features/*.py` are picked up automatically by `features/__init__.py::load_features()`. Each module's `FEATURE` router is mounted under `/api` with token or cookie auth.

## 0. Summary

| Surface | Backend | UI | CLI | Real connection | Global STOP | Restart recovery |
|---|---|---|---|---|---|---|
| CMD (`bossman` 1.2 client) | client only; talks to the one backend | n/a | `bossman` → `bcc.terminal_cli` | WIRED; Windows owner run UNPROVEN | `bossman stop --all` → `/api/control-plane/stop-all` | task replay by cursor; chat sessions stored as JSON files |
| Terminal page (shell sessions) | `features/terminal.py` + `v2/terminal_control.py` | `pages/terminal.js` | none | WIRED; sandbox mode needs Docker | kills live sessions only | none; DB rows stay `running` |
| Browser | `features/browser.py`, `features/tools_browser.py`, `v2/browser_control.py` | `pages/browser.js` | none (chat tasks only) | WIRED (Playwright, headless only) | stops live sessions | none; dead sessions are flagged `live:false` |
| Jev browser fast path | `jev/browser_fastpath.py` | none | offline tool `tools/jev_shadow_owner.py` | library only, not in the agent loop; execution is a STUB | n/a | n/a |
| Computer Use | `features/tools_computer.py` + `bossman-core/bossman/computer_operator/*` | a panel inside `pages/control.js` only | `/computer`, `bossman run automation` | Windows only; Notepad/Calculator only; live PASS recorded 2026-09-26 | durable STOP file | STOP, unknown-outcome flag and used-approvals journal all survive restart |
| Agentic Rave | `rave/{engine,connectors,spec,workspace,cli}.py` + `features/rave.py` | `pages/rave.js` | `bossman rave …`, `/rave` | mock and local work; Claude/Codex CLIs UNPROVEN against the real binaries | listens for the bus event `owner.stop_all` | agents come back `paused`, driven by a step journal |

---

## 1. CMD / Terminal Run

### 1a. Bossman CMD: the terminal client (version 1.2.0)

**Entry points**
- Console script `bossman = "bossman.cli:main"` in `bossman-core/pyproject.toml:47`.
- `bossman-core/bossman/cli.py:24`: `TERMINAL_COMMANDS` covers chat, exec, status, events, result, resume, approve, deny, pause, stop, continue, list, keys, code, evolution, repair, run, evolve, start, version, approvals, tasks, market, rave, autonomy. These go to `bcc.terminal_cli.main`.
- `python -m bcc.terminal_cli` does the same.
- `command-center/pyproject.toml:71-75` defines only `bcc`, `bcc-desktop`, `bcc-open` and `bossman-telegram`. The root `pyproject.toml` defines no scripts.
- The "Bossman CMD" desktop shortcut is created by `tools/owner_one_bossman.ps1:338` and points at `Bossman-Terminal.cmd`.

**Routing** (`bcc/terminal_cli/cli.py:310`)
- `rave` goes to `bcc.rave.cli.main`.
- `autonomy` goes to `bcc.autonomy.cli.main`.
- Everything else goes to a `cmd_*` handler.
- `run automation` is handled by `screens.py:357 run_automation`. It picks an agent that has `computer.*` tools, refuses if `/api/computer/status.stopped`, then runs `run_headless`.

**Endpoints it uses**
- `POST /api/tasks` (with optional `client_request_id`), `/api/tasks/preflight`, `/api/tasks/{id}/run`.
- `GET /api/tasks/{id}`.
- `POST /api/tasks/{id}/stop|pause|resume`.
- `GET /api/tasks/{id}/events?after=N&limit` returns `{task_id, status, events[], cursor, more}` (`api.py:1200`).
- **SSE** `GET /api/events/stream?task_id|run_id&after` (`api.py:1216`). Frames are `id: <seq>` plus `data: <json>`. Synthetic kinds are `stream.open`, `stream.replayed` and `stream.lagged` (the client should reconnect with `after=cursor`). Keepalive is a comment line. It is scoped to a single task or run; there is no SSE for rave, browser or computer.
- Other endpoints: `/api/approvals[/{id}]`, `/api/computer/status|stop|resume`, `/api/coding-tasks`, `/api/memory/*`, `/api/models`, `/api/agents`, `/api/providers`, `/api/control-plane/stop-all`.
- The web UI uses a separate channel: WebSocket `/api/events` (`api.py:728`).

**Exit codes** (`records.py:44-56`): 0 OK, 1 FAIL, 2 USAGE, 3 DISCONNECTED, 4 WAIT_APPROVAL, 5 BLOCKED, 6 STOPPED, 7 TIMEOUT, 8 PARTIAL, 9 NOT_FOUND, 10 NOT_SUPPORTED, 11 CONFLICT, 130 INTERRUPTED.

**Environment variables**: `BCC_PORT` (default 8800), `BCC_HOST`, `BOSSMAN_URL`, `BCC_DATA_DIR`, `BOSSMAN_TERMINAL_NO_APPROVE`, `BCC_TOKEN_STDOUT` (set to 0 by `launch.py`).

**Connecting and starting**
- `launch.py:start_backend` starts a detached `python -m bcc.app`, but only if no Command Center answers for this data root.
- It refuses a mismatched build or data root (`api_client.backend_mismatch`).
- It enforces one backend per data root through `bcc/backend_lock.py`.

**Persistence**
- Task state lives in the backend DB (`tasks`, `task_runs`, `events`).
- Chat sessions are written by the client directly to `<data>/terminal/sessions/<id>.json` (`chat.py:127`).
- Exports go to `<data>/terminal/exports/`.

**STOP**
- Per task: `bossman stop <id>` or Ctrl+C (`follow.py:127 request_stop`).
- Global: `bossman stop --all` calls `cli.py:818 global_stop`, which calls `POST /api/control-plane/stop-all`.

**Approvals**: inline y/n in chat, `approve`/`deny`, and `--approval-mode fail` (exit 4, nothing is approved).

**Still unfinished according to the docs** (`docs/owner/TERMINAL.md:176-189`, `docs/terminal/PARITY_MATRIX.md`, `docs/terminal/ACCEPTANCE_AND_OWNER_RUN.md`)
- Windows ConHost / Windows Terminal is not verified: Cyrillic, paste and Ctrl+C. These are TR-01, TR-02 and TR-04.
- There is **no TR-01…22 evidence report** anywhere; `ACCEPTANCE_AND_OWNER_RUN.md:3` still says "NOT_RUN".
- There is no token-by-token streaming; text arrives once per model step.
- `repair --self` and `/evolve` return exit 10 when `/api/evolution` is missing. `run automation` and `evolve --lab` are minimal.
- `/api/identity` has no instance ID.
- `--cwd` does not change the working folder of a normal task.
- Browser, Studio, Web Designer, Telegram, Fleet and diagnostics have no CLI operations (marked UNAVAILABLE in the parity matrix). Computer, Models, Skills, Tools, Coding and Global STOP are only "WIRED", with no end-to-end test.
- Two doc items are stale:
  - The prompt_toolkit item is already fixed: `tools/windows_bundle_lock.txt:354` has `prompt-toolkit==3.0.52`.
  - The Global STOP row in the parity matrix describes the old path. The code now uses `/api/control-plane/stop-all`.

### 1b. Terminal page: HTTP shell sessions

**Endpoints** (`features/terminal.py`)

| Method and path | Line | Request / response |
|---|---|---|
| `GET /api/terminal/roots` | 113 | returns `{roots}` |
| `POST /api/terminal/roots` | 119 | body `{roots:[abs dirs]}`; validated by `_validate_roots`, rejects a filesystem root; stored encrypted in `settings_kv` under key `terminal.roots` |
| `POST /api/terminal/preview` | 134 | body `{mode, command, cwd}`; returns `{decision: auto\|ask\|deny, mode, cwd}` |
| `POST /api/terminal/run` | 147 | body `{mode, command, cwd, network, approval_id?}`; returns `{session_id, pid, mode}`. Deny gives 403. Ask gives **HTTP 202** `{approval_id}`, and the preview string is `"[mode] cmd\ncwd: …"`. The call is then retried with `approval_id` and consumed once. |
| `GET /api/terminal/sessions` | 193 | last 100 rows of DB table `terminal_sessions` (`v2/tables.py:99`) |
| `GET /api/terminal/sessions/{id}` | 202 | `{id, cwd, cmd, mode, pid, finished, exit_code, output_tail[≤200]}` |
| `POST /api/terminal/sessions/{id}/stdin` | 219 | body `{text}` |
| `POST /api/terminal/sessions/{id}/kill` | 230 | 503 if the stop is not confirmed |

**Runtime** (`v2/terminal_control.py`)
- `TerminalPolicy.decision` (line 145):
  - The only DENY patterns are format/diskpart/mkfs/fdisk, `rm -rf /`, `git push --force` and `git reset --hard`.
  - ASK covers git push, pip/npm install, docker compose and sudo/runas.
  - `system_admin` mode always asks.
- `TerminalManager.start` (line 192): sandbox mode runs `docker run --rm --network none|bridge -v cwd:/work python:3.12-slim sh -lc`. Host modes use a shell.
- `kill` (line 316) runs `taskkill /T /F` on Windows and `killpg` on POSIX.
- Output is kept **in memory only**: at most 5000 lines, and the API returns a tail of 200.

**UI** (`pages/terminal.js`)
- Mode selector with sandbox as the default, cwd, command, network checkbox.
- Preview, then run, with an approval dialog wired through `api.decideApproval`.
- Session modal that polls every 1.5 s with a kill button.
- There is **no stdin box**, although the endpoint exists.
- `pages/mobile.js` also lists sessions and can kill them.

**Gaps**, highest priority first:
1. **P1: no recovery after restart.**
   - No `setup()` in `features/terminal.py`, and nothing in `api.py` shutdown kills live sessions.
   - Rows in `terminal_sessions` stay `status='running'` forever. Host processes, started with `start_new_session`, outlive the backend untracked, and global STOP cannot see them because it inventories live handles only.
   - Fix: add `features/terminal.py:setup` to reconcile `running` rows to `lost`/`unknown`, and kill `svc.terminal.sessions` in `api.Services.close` (around `api.py:255-270`).
2. **P1: stopping a sandbox command probably leaves the container running.**
   - `kill` signals the docker CLI process, and the container has no `--name`. A SIGKILL is not proxied to the container, so it likely keeps running. (Plausible, not verified.)
   - Fix: in `TerminalManager.start`, add `--name bcc-<sid>` (or `--cidfile`); in `kill`, run `docker kill`.
3. **P1: on the owner's Windows PC, sandbox (the UI default) fails with 503 if Docker is missing.**
   - UI fix: `pages/terminal.js termState.mode` should default based on a Docker probe.
   - Agent-tool fix: `tools_terminal._tool_run` currently lets every command fall back to `project_host`, which always asks.
4. **P2: full output is never persisted.** The DB has no output column. After the session is dropped from memory (200 kept, `RETAIN_FINISHED`) or after a restart, the call returns 404.
5. **P2: two different default roots for the same key.** `features/terminal.py:_allowed_roots` defaults to `data_dir`, which contains `bcc.db` and the token file. `tools_terminal._roots` defaults to the scratch folder (the AP-001 fix).
6. **P2: per-task STOP does not kill that task's terminal sessions.**
   - `terminal.run` returns while the command is still running when it hits its timeout (`tools_terminal.py:287-377`). The command keeps running.
   - Nothing listens for `task.stopped`.
   - Fix: kill sessions whose `owner == task_id` from `engine.stop`, or subscribe to `task.stopped`.
7. **P3: the mode setting has no writer.** `tools_terminal._mode` reads `settings_kv['terminal.default_mode']`, but no endpoint writes it. `_run_effect` assumes sandbox when `mode` is omitted, so the approval judgment and the actual execution mode could diverge if that key is ever set.

**Agent tool `terminal.run` gating** (`tools_terminal.py:432 _run_effect`): hard deny covers ClickFix patterns and unrecoverable deletes. `network=True`, `project_host` and `system_admin` all ask. Sandbox is AUTO only for the read/build list. This is sound.

---

## 2. Browser

**Endpoints** (`features/browser.py`)

| Method and path | Line | Request / response |
|---|---|---|
| `GET /api/browser/health` | 50 | `{available, detail, active_sessions}` |
| `POST /api/browser/sessions` | 61 | body `{agent_id?, task_id?, policy?}`; returns `{session_id, url, title, takeover, paused, …}`. Always `headless=True`. 503/403/500 set the row to `failed`. |
| `GET /api/browser/sessions` | 96 | last 50 rows with a `live` flag |
| `GET /api/browser/sessions/{id}/state` | 109 | 404 if not live |
| `POST /api/browser/sessions/{id}/act` | 118 | body `{action: navigate\|click\|download\|type\|snapshot\|back\|reload, url?, selector?, text?, actor: agent\|human, approval_id?}`. 202 means approval is needed (preview string `"browser {action}: {subject}"`), 403 deny, 409 takeover, 422, 502 for a Playwright error. |
| `GET /api/browser/sessions/{id}/downloads` | 196 | download log |
| `GET /api/browser/sessions/{id}/screenshot` | 205 | PNG |
| `POST /api/browser/sessions/{id}/takeover` | 219 | owner takes control |
| `POST /api/browser/sessions/{id}/resume` | 228 | also re-queues a task parked with `WAITING_FOR_OWNER_CHALLENGE` |
| `POST /api/browser/sessions/{id}/stop` | 270 | stops the session |
| login receipts and credentials routes | `tools_browser.py:832-896` | `GET /api/browser/login-receipts`, `…/{id}/screenshot`, `POST …/{id}/consumed`, `GET\|POST /api/browser/credentials`, `DELETE /api/browser/credentials/{id}` |

**DB**: table `browser_sessions` (`v2/tables.py:83`); credentials in `settings_kv` under `browser.credentials`. **Files**: `<data>/browser/`, `profiles/`, `downloads/session-N/`, `login-receipts.json`. **Environment**: `BCC_BROWSER_ALLOW_PRIVATE`, `PLAYWRIGHT_BROWSERS_PATH`.

**Policy** (`v2/browser_control.py:38 DEFAULT_RULES`)
- AUTO: navigate, back, reload, read, click, type, select.
- ASK: download, upload, submit, login.
- DENY: purchase, payment, wallet, bank_transfer.
- A private or metadata target is refused (F-010).
- A captcha blocks agent interaction.
- `_guard` is at line 1006.

**Agent tools** (`tools_browser.py:707 SPECS`)
- AUTO: `browser.open`, `read_dom`, `screenshot`, `click`, `type`, `select`, `back`, `reload`, `request_owner_fields`, `fill_owner_fields`, `verify_owner_input_success`.
- ASK, and the ASK cannot be lowered: `download`, `submit`, `login`.
- For `login`, the vault supplies the password; the model never sees it (`fill_secret`, `redact_secrets`).

**Jev fast path** (`jev/browser_fastpath.py`)
- `execute_step` (line 404) always raises `Escalate("phase_not_enabled")`.
- `config.BrowserConfig.may_execute` always returns False.
- `shadow_step` is called only from `tools/jev_shadow_owner.py` and tests. It is **not wired into the agent browser loop**.
- The flags `BOSSMAN_JEV_BROWSER_ENABLED` and `BOSSMAN_JEV_BROWSER_SHADOW` only appear in `GET /api/jev/status`.

**Gaps**, highest priority first:
1. **P0/P1: clicks are not gated for paid actions or publishing.**
   - `browser.click` is AUTO, and nothing classifies what is being clicked. A click on "Pay", "Buy", "Publish" or "Submit" is plain `click`. The `purchase`/`payment` deny rules are labels that no tool ever sends.
   - So `fill_owner_fields` (AUTO) followed by `click` on the submit button (AUTO) sends owner data without approval, bypassing the ASK on `browser.submit`.
   - Computer Use already has a lexicon for this (`ComputerPolicy.ask_consequence`, `bossman-core/.../policy.py:172`). The browser has nothing equivalent.
   - Fix:
     - Add an `effect_hook` on the `browser.click` ToolSpec (`tools_browser.py:729`) that classifies the target's label or ref from the last snapshot with a consequence lexicon, plus `type=submit` / form-submit detection.
     - Add a boundary re-check inside `BrowserManager.click` (`browser_control.py:1209`) / `_guard`.
2. **P1: screenshots pile up on disk.**
   - `GET /screenshot` (`browser.py:212`) and `tools_browser._screenshot` (line 695) write a new `shot-<sid>-<rand>.png` on every call and never delete them.
   - The UI live panel polls every 3 s (`browser.js openLivePanel`), which is about 1200 files per hour per open panel.
   - Fix: return bytes with `Response(content=png)` or keep a ring buffer.
3. **P1: after a restart, an agent task's browser tools all fail.**
   - `tools_browser._session_for` (line 57) reuses the newest `status='running'` row without checking `mgr.is_live`. Every browser tool call then fails with `LookupError`.
   - No startup reconciliation of `browser_sessions` exists.
   - Fix: in `_session_for`, check `is_live` and otherwise mark the row and open a new session. Add a feature `setup` that reconciles rows.
4. **P1: sessions leak when a task ends.** No session is closed on task completion or per-task stop; only global stop-all and backend shutdown close sessions.
   - `BrowserPolicy.max_runtime_minutes`, `max_tabs` and `screenshots` are parsed but **never enforced**.
   - Fix: subscribe to `task.stopped`, `task.completed` and `task.failed`, and close the sessions whose `task_id` matches.
5. **P1: Human Take Over is limited.** The browser is always headless (`browser.py:74`, `tools_browser.py:~83`). The owner gets a screenshot plus a URL bar only; there is no forwarding of clicks or typing. The owner cannot solve a captcha or log in through this UI. The owner-input path over the Telegram form bridge exists as a partial substitute.
6. **P2: `takeover` and `resume` return 500 on a dead session.** They do not catch `LookupError`, and `mobile.js` offers these buttons for dead sessions too.
7. **P2: one shared profile for every agent.** `persistent_profile=True` always uses profile `"default"` (`BrowserManager.start` defaults `profile_name`). Any agent whose policy enables persistence reuses the owner's logged-in cookies, so there is no per-account or per-agent isolation. Two concurrent persistent sessions will also collide on the Chromium profile lock.
8. **P2: `browser.type` into a password field is not blocked.** Only the value shown in snapshots is redacted.

---

## 3. Computer Use

**Code**
- `features/tools_computer.py` (1506 lines), built on the bossman-core adapters `WindowsDesktop` (pywinauto UIA + pyautogui), `LocalScreenshotProvider`, `AppLaunchAdapter` and `ComputerPolicy`.
- `availability()` (line 178) returns False on anything other than Windows, or when the core or its dependencies are missing. pywinauto, pyautogui and psutil are in the bundle lock.

**Tools**
- `computer.observe` has default effect **ask** (line 1397).
- `computer.act` has default effect **ask** (line 1402), with `effect_hook=_act_effect` (line 1378). Deny covers `hard_refusal`: pay, purchase, transfer, secret entry, Win-key combinations and credential targets. Ask covers declared or lexicon consequences, and every action except `wait` ("CU-ONESHOT").
- Approvals come from `claim_approval` (line 941): one approval per action, TTL `BCC_COMPUTER_APPROVAL_TTL_S`, default 300 s.
- The launch allowlist is **Notepad and Calculator only** (`bossman-core/bossman/computer_operator/applist.py APP_ALLOWLIST`).
- Input is accepted only into windows of allowlisted processes (`window_allowlisted`, line 829).

**Endpoints**
- `GET /api/computer/status` (line 1438) returns `{available, detail, stopped, generation, session, busy, stop_epoch, outcome_unknown, tools}`.
- `POST /api/computer/observe`, `POST /api/computer/stop` (returns `{stopped, persisted}`), `POST /api/computer/resume`.
- Bus events: `computer.observe`, `computer.act`, `computer.refused`, `computer.stop`, `computer.resume`.

**Persistence** under `<data>/computer/`:
- `STOP`, `OUTCOME_UNKNOWN`, `USED_APPROVALS.json` (up to 5000 entries, anti-replay; if the journal is corrupt, actions stay locked).
- `screens/` with a retention of 32.
- `ComputerState.restore()` (line 321) reloads these. Generation numbers are unique per process, and after a resume, earlier observations and approvals are invalidated.
- Cancellation or timeout triggers `_abort_inflight` and marks the outcome unknown.

**UI**: only the "Рабочий стол (Computer Use)" panel in `pages/control.js:115` (status, Стоп, Продолжить). There is no observation or screenshot view and no approval preview.

**Legacy code**: the bossman-core operator routes `POST /tasks`, `/tasks/{i}/pause|resume|take-control|stop` and `/emergency-lock` still exist in `bossman-core/bossman/computer_operator/routes.py`. The backlog asks to retire or re-gate them (item 9).

**Evidence**: `docs/owner/WORKBENCH_20260926.md:291` records a live owner-desktop PASS: Notepad type/save, STOP race, resume, Calculator. The release smoke test `docs/owner/RC19_OWNER_SMOKE.md:165` still contains the unfilled placeholder **`{B_COMPUTER_USE_STEPS}`**. Final installed-bytes CU plus STOP/resume is still open (`TOMORROW_CHECKLIST.md:18`, `TOMORROW_OPERATOR_RUNBOOK.md:77`).

**Unfinished**, from `docs/owner/DECISIONS_BACKLOG.md:25-35`:

| # | Item | Status |
|---|---|---|
| 4 | window crop attached to each approval question | not implemented (no "crop" in code) |
| 5 | crop observation screenshots to the observed window | not implemented |
| 6 | refuse shift+F10 in file dialogs | not implemented |
| 7 | restrict `file_exists` to owner-approved folders | partial: only a freshness check exists, so it is still an existence oracle for any absolute path |
| 8 | Studio-only token for Jeff | open |
| 9 | retire the legacy `/computer/tasks` routes | open |
| 10 | lease refusal returns 409 | done (`550bb136`) |
| 11 | prefer a button when target and label match | open |

**Gaps**, highest priority first:
1. **P1: the release smoke test has no Computer Use steps.** Fill `{B_COMPUTER_USE_STEPS}` in `RC19_OWNER_SMOKE.md`.
2. **P1: the owner approves blind.** No approval preview or crop exists (backlog 4). Target: `tools_computer._act_effect` / `claim_approval`, plus the approval payload.
3. **P2: no per-task Computer Use STOP.** Only the global STOP file exists; a per-task stop cancels the worker, which leads to `_abort_inflight`.
4. **P2: no UI page.** There is no observation view and no queue of pending desktop actions; the Terminal parity matrix lists this surface as "WIRED (minimal view)".
5. **P3: the allowlist is intentionally Notepad and Calculator only.** Widening it is an owner decision.

---

## 4. Agentic Rave

**Design doc**: `docs/v1.9/AGENTIC_RAVE.md`. It was merged into this branch through `24140668`, `rc19/p-green`, with the owner's GREEN LIGHT recorded in `RC19_SESSION_AUDIT_20260928.md:88`.

**Engine**: `rave/engine.py`, class `RaveService`, attached as `svc.rave`.
- `create` (line 210): up to 8 agents (`spec.MAX_AGENTS`) and a prompt of at most 8000 characters. Each agent gets a remote-less git clone at the base commit with branch `rave/<id>/<name>` (`workspace.create_workspace`). Without `--repo`, a scratch repo is created.
- `_run_agent` (line 287): preflight, then opt-in, then `conn.run`. Outcomes are `blocked`, `stopped`, `failed` or `done`. Finalisation runs as its own task through `await_shared`.
- `_finish` (line 347): Bossman commits the workspace, checks it for tampering, runs the owner's `--test` command, then refreshes conflicts (`workspace.detect_conflicts`, `git merge-file --diff3`).
- Controls: `pause`, `resume`, `stop`, `stop_all` (lines 456, 475, 502, 534). Child processes run in a `ProcessTree` from `bossman.apprentice.proc_tree`, a Windows Job object; pause and resume suspend them with psutil.
- `recover` (line 544): agents that were live in a previous boot become `paused` with reason `recovered_after_restart`, or `stopped` if a stop had been requested. An interrupted non-idempotent step becomes `interrupted` and is re-run only through an explicit per-agent resume.
- `apply` (line 614): a dry-run conflict check, then approval `kind=rave_apply` (HTTP 202 `WAIT_APPROVAL`), then `consume`, then write the working tree only (no commit, no push). Error codes are 409 `CONFLICT`, `NOT_ELIGIBLE`, `ALREADY_APPLIED` and 403 `APPROVAL_INVALID`.

**Connectors** (`rave/connectors.py`)
- `mock`: in-process and idempotent.
- `local`: `python -m bossman.apprentice.local_sidecar`; only a local endpoint (`BOSSMAN_RAVE_LOCAL_ENDPOINT`, default `http://127.0.0.1:11434`).
- `claude`: `claude -p`.
- `codex`: `codex exec --sandbox workspace-write`.
- The Claude and Codex connectors need the one-time opt-in approval `kind=rave_connector_optin`, stored in `<data>/rave/optin.json`.
- API-key variables are stripped from the child environment. `BOSSMAN_RAVE_ALLOW_API_KEY=1` plus `?auth=api_key` is required to use a key.

**Endpoints** (`features/rave.py`)

| Method and path | Line | Request / response |
|---|---|---|
| `POST /api/rave` | 66 | body `{prompt, agents[], repo?, allow[], test?}`; returns the rave view |
| `GET /api/rave` | 73 | returns `{items}` |
| `GET /api/rave/connectors` | 78 | connector login and opt-in state |
| `POST /api/rave/stop-all` | 93 | stops every rave |
| `GET /api/rave/{rid}` | 98 | rave view |
| `GET /api/rave/{rid}/events?after=` | 104 | returns `{events[{seq,…}], cursor}` |
| `POST /api/rave/{rid}/pause`, `/resume`, `/stop` | 109-119 | body `{agent?}` |
| `GET /api/rave/{rid}/agents/{name}/diff` | 124 | agent diff |
| `GET /api/rave/{rid}/conflicts` | 129 | conflict list |
| `POST /api/rave/{rid}/agents/{name}/apply` | 136 | body `{approval_id?}` |

- Bus events are `rave.<kind>`: created, agent_started, workspace_ready, step_started, step_done, agent_paused, agent_resumed, agent_rerun, suspended, agent_done/failed/stopped/blocked/interrupted, conflict, stop, recovered, apply_requested, apply_conflict, applied; plus `rave.recovered_after_restart`.
- `setup` (line 176) runs `recover()` and starts `_watch_owner_stop` (line 144), which listens for bus `owner.stop_all` and calls `stop_all`.

**Storage**: files, not DB. `<BCC_DATA_DIR>/rave/<rv-xxxxxxxx>/` contains:
- `rave.json`, written atomically.
- `events.jsonl`.
- `agents/<n>/ws/` (the agent's clone), `exec.log`, `codex-last-message.txt`, `files-copy/` (created on tampering).
- `conflicts/`, and `base/` for a scratch rave.

**CLI** (`rave/cli.py`): start, list, status [--watch], show, diff, conflicts, log, pause, resume, stop [--all], apply, connectors, plus `--json`. Chat `/rave` runs the same commands in detached mode. Status is watched by polling the events endpoint every second.

**UI** (`pages/rave.js`, nav section "more/studio")
- Start form with prompt and agents only.
- Per-agent table with pause, resume, STOP and diff.
- Rave-wide pause, resume and STOP; "STOP всех рейвов".
- Refreshes on any `rave.*` event.

**Gaps**, highest priority first:
1. **P0: the Claude CLI flag is unverified.**
   - The code passes `--permission-prompts none` (`connectors.py:373`, copied into `autonomy/workers.py:346`). This flag is not in the design doc's invocation list (§7), and I do not recognise it as a documented Claude Code flag.
   - The test stub `tests/rave_stub_cli.py` ignores unknown flags, so tests cannot catch this.
   - If the real `claude` rejects the flag, every `claude:` agent fails.
   - There is no real-run evidence for Claude or Codex anywhere in `docs/`, `evidence/` or `artifacts/`.
   - Fix: check `claude --help` on the owner's machine and make the stub reject unknown flags.
2. **P1: global STOP does not confirm raves.**
   - `control_plane._active_owner_work` (line 33) does not include rave (nor autonomy). `stop_all_owner_work` reports `ok` before the asynchronous bus listener has stopped the rave agents, so `bossman stop --all` can print "полностью подтверждён" while agents are still running.
   - Fix: add a `rave` plane that calls `svc.rave.stop_all()` synchronously in `features/control_plane.py:stop_all_owner_work`, and inventory it through `RaveService.list()`.
3. **P1: the UI flow is incomplete.**
   - No `repo`, `allow` or `test` fields, so the UI can only create scratch raves.
   - No Apply button: the page only shows text telling the owner to use the CLI. Approving `rave_apply` in the Approvals page does not trigger the apply; the owner must re-run `bossman rave apply … --approval-id N`.
   - No connectors or opt-in status, no events timeline, and no full answer (text is truncated to 400 characters).
   - Targets: `pages/rave.js`, plus an approval-decided hook in `features/rave.py` that auto-applies the exact consumed approval.
4. **P2: doc and code disagree on timeouts.** The doc (§5) says "Paused time counts against the agent's own timeout". `AgentCtx.spawn` (lines 725-752) does not count suspended time.
5. **P2: nothing is ever cleaned up.** No command removes old raves, and workspaces are never deleted, so disk use grows.
6. **P2: approval lookups are capped.** `_require_optin` and the pending lookup in `apply` scan only the newest 100 approvals (`approvals.list` limit). An older approved opt-in can be missed, which creates a duplicate approval.
7. **P2: `AgentCtx.spawn` hard-depends on bossman-core** (`bossman.apprentice.proc_tree`). If core is missing, local, claude and codex agents and `--test` all fail as `failed`; only the local connector checks for this in preflight (`_core_path`).
8. **P3: wrong exit code on connection failure.** `rave/cli.py:main` returns 5 (BLOCKED) when it cannot connect, where the 1.2 contract uses 3 (DISCONNECTED).

---

## 5. Cross-cutting

**Global STOP** (`features/control_plane.py:123`)
- Stops, in order: Computer STOP file (first), tasks, terminal (live handles), coding (requested only), command_bar, browser (live), evolution, v15 economy and owner run, pit (Jeff) flag file, Studio.
- It then emits `owner.stop_all`, which rave and autonomy handle **asynchronously and outside the `ok` accounting**. The `market` collector is not in the inventory either.

**Mission console** (`pages/mission_console.js`): it can submit tasks (`POST /api/tasks`) and decide approvals, but it has **no per-task STOP or pause control**.

**Restart recovery**: Computer Use (STOP, unknown outcome, used approvals) and Rave (journaled recovery to paused) handle restarts well. Terminal and Browser only report `live:false` or 404; nothing reconciles their DB rows, and orphaned host processes are not tracked.