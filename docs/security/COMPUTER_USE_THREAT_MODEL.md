# Computer Use — threat model (Bossman 1.9 RC, workstream B)

Scope: the **installed product path** — Command Center tools `computer.observe` /
`computer.act` (`command-center/bcc/features/tools_computer.py`) on top of the
bossman-core adapters (`bossman/computer_operator/adapters/windows.py`,
`app_launch.py`, `applist.py`, `policy.py`). Audited on PR #84 HEAD `762e96d2`;
fixes on branch `rc19/b-cu`. The bossman-core task manager
(`computer_operator/manager.py`, `/computer/tasks`) is **not mounted** in the
Command Center backend and is out of the installed path (see R8).

Tags: **proven-defect** (reproduced by a failing test on 762e96d2, fixed on
rc19/b-cu), **ok** (verified, no change needed), **risk** (open, accepted or
backlog).

## 1. How a CU action is requested

| Surface | Path | Gate |
|---|---|---|
| Model tool call (web chat, CLI `bossman`, Telegram «Пульт» task) | engine tool loop → `decide_effect` → ASK → `approvals` row → owner decision → `_authorization_at_effect_time` (CAS approved→consumed) → handler with `ToolContext.approval_id` | engine + handler (`claim_approval`) |
| Owner HTTP panel | `POST /api/computer/observe`, `/stop`, `/resume`, `GET /status` (owner token/session+CSRF) | owner auth; there is **no** HTTP "act" route |
| Telegram «Пульт» (owner control bot, `bcc/telegram_companion`) | `/approvals` → one-time nonce gate (180 s, bound to chat/user/kind/digest) → fresh `/api/computer/observe` → `POST /api/approvals/{id}`; `/stop`, `/resume` → `/api/computer/*` | owner-only console, nonce gate, then the same engine + handler gates |
| Jeff / PIT participant bot (`bcc/pit`) | none | structurally absent (see §9) |

The model has no tool that reaches `/api/computer/resume` or the approvals API.

## 2. Findings

| ID | Finding | Tag | Fix / evidence |
|---|---|---|---|
| D1 | **One-shot approval not enforced.** An agent granted `computer.control`, or an owner `tool_rules` AUTO rule, made every non-"consequential" `computer.act` (type, hotkey, click, launch, focus_window) run with **no** owner decision (`decide_effect` → AUTO; a test even asserted `auto`). An approval **lease** (≤200 uses / 4 h) also drove `computer.act` with `approval_id=None`. | proven-defect | `effect_hook` is now an ASK floor for every mutation (`hook_is_floor` → rules/grants cannot lower it); handler `claim_approval` refuses any mutation without a real approval row; `approval_scope` neither offers nor grants a lease for `computer.act`. Tests: `test_every_mutation_is_an_owner_question_even_for_a_granted_agent`, `test_handler_refuses_a_mutation_without_an_approval_row`, `test_computer_act_is_not_leasable`, `test_engine_lease_cannot_drive_the_desktop`. |
| D2 | **No window allowlist.** Launch was allowlisted (notepad/calculator), but `focus_window` accepted any top-level window by title and input went to any foreground window that was not Bossman/UAC/credential UI — e.g. a PowerShell / cmd / Windows Terminal window → arbitrary shell command; `hotkey win+r` opened Run. | proven-defect | Input kinds and `focus_window` only reach windows whose **process** (GetWindowThreadProcessId → image name, not the title) is in the app allowlist (`notepad.exe`; Calculator only via verified `ApplicationFrameHost` + CalculatorApp package check); Win/`ctrl+esc`/`ctrl+shift+esc`/`ctrl+alt+del` hotkeys refused (engine DENY + handler); launch outside allowlist is an engine DENY (never asked). Tests: `test_input_into_a_shell_window_is_refused`, `test_focus_window_only_reaches_allowlisted_windows`, `test_shell_hotkeys_and_launch_outside_allowlist_are_denied_by_the_engine`, `test_allowlist_decision_uses_the_process_not_the_title`. |
| D3 | **Payment / credential actions were approvable; password fields undetected.** `pay`/`transfer`/`secret_entry` only raised an ASK; typing into a UIA password box was not detected at all. | proven-defect | `pay, purchase, transfer, secret_entry, account_change, security_change` are refused with or without approval (engine DENY from semantic/target; handler refusal from observed evidence, e.g. a "Checkout" window title). `type` is refused into credential-named targets, elements with UIA `IsPassword`, and — at the effect boundary — when the OS says the **focused** element is a password box or its type cannot be read (fail-closed). Tests: `test_payment_is_refused_even_with_an_owner_approval`, `test_credential_entry_is_refused`. |
| D4 | **Approval flow unusable, no expiry.** The parked call carried the model's `generation`; the handler required `generation == current` and ≤45 s. Any observation while the owner decided — the «Пульт» approve button itself calls `/api/computer/observe` — or >45 s of owner latency made **every** approved action refuse. Conversely `launch` (no generation) had no expiry at all. | proven-defect | Approval TTL 300 s from the approval question to the effect (env `BCC_COMPUTER_APPROVAL_TTL_S` can only shorten, min 5 s). An approved call is rebound to the observation the owner was asked about: that generation must still be in the in-memory history (cleared by resume, unknown outcome, restart), the fresh re-read must show the **same window handle**, and the target is re-resolved on the fresh screen. Tests: `test_approved_action_survives_an_observation_during_the_decision`, `test_approved_action_refused_after_resume_restart_or_window_change`, `test_expired_approval_never_executes`. |
| D5 | **Handler trusted `ctx.approval_id` blindly** (no existence/status/task/replay check). | proven-defect | `claim_approval`: row exists, kind `tool`/`effect_reconciliation`, status `consumed` (engine CAS happened), same task, within TTL, not in the persisted used-approval journal (`data_dir/computer/USED_APPROVALS.json`, written before the effect; corrupted journal → outcome-unknown lock). Tests: `test_approval_must_be_real_consumed_fresh_and_unused`, `test_approval_of_another_task_is_not_transferable`. |
| D6 | **Content oracle.** `expect.file_contains` read any absolute file even if the action never wrote it; `wait` needs no approval, so `wait + expect` could probe e.g. key files. | proven-defect | File content (`file_contains`, new `file_sha256`) is only read when the file's mtime is after the action started. Test: `test_file_content_is_not_read_unless_the_action_wrote_it`. |
| O1 | Model claims (`semantic`, `_approved_*`, `approved:true`) are never authority; only the engine's approval row is. | ok | pre-existing tests `test_model_claim_is_not_approval`, RT-P2/RT-P5. |
| O2 | Approval bound to exact arguments: `approval_digest` (tool + implementation fingerprint + normalized args + capability + agent/task); mismatch at resume → rejected. Owner decision replay returns the existing row; `accept_for_execution` is a single CAS. | ok | RT-P2/RT-P3, `test_secrem_f013_approval_identity`, live scenario e_replay. |
| O3 | STOP propagation: `/api/computer/stop` (panel, CLI, «Пульт» `/stop`, control-plane full stop) sets a threading.Event checked between typing chunks (16 chars typed / 64 chars via clipboard), bumps a stop epoch re-checked before every desktop sub-step, persists `data_dir/computer/STOP` across restarts; resume invalidates all previous observations (and now approved-but-unexecuted calls). | ok | `test_computer_stop_race.py`, live scenario f. |
| O4 | Crash / restart: engine write-ahead `started` row before a non-idempotent effect; after a crash the interrupted dispatch is never re-run silently (owner reconciliation or rejection); a hung adapter marks `OUTCOME_UNKNOWN` (persisted) until a fresh observation. New: the approved generation does not survive a restart, so an approved-but-unexecuted desktop step is refused after restart. | ok | `test_owner_stop_lifecycle`, live scenario g. |
| O5 | Postcondition verification is deterministic: `contains_text` / `absent_text` read UIA element values of a fresh observation; `file_exists` / `file_contains` / `file_sha256` read the disk; no expectation → `verified=null`, unknown key → `false`. Screenshot/model judgement is never the verdict. | ok (+ `file_sha256` added) | `test_computer_verify_and_approval.py`, `test_file_sha256_postcondition`. |
| O6 | Protected surfaces: input into Bossman / Command Center / UAC / Windows Security / credential manager windows refused; sensitive targets ("Approve", "Продолжить", "Emergency") refused when the window identity is unknown. | ok | existing CU-PATH tests, RT-P6. |
| R1 | Every `computer.observe` stores a **full-screen screenshot** in `data_dir/computer/screens` (retention 32). Other windows on screen (e.g. an open browser with API keys) are captured. Local only, but broader than the acted-on window. | risk | backlog B-2 (crop to the observed window / opt-in full screen). |
| R2 | Inside an allowlisted app the owner can still approve steps that open a common file dialog; a file dialog can navigate the file system and (via context menu) open files with their default handler. Each such step is a separate owner question showing its arguments. | risk | accepted for RC; backlog B-3 (refuse context-menu key / shift+F10 / `apps` key — `apps` already refused). |
| R3 | The clipboard path temporarily replaces the owner's clipboard text (restored afterwards); a crash mid-paste leaves test text on the clipboard. | risk | documented. |
| R4 | `expect.file_exists` still reveals whether an arbitrary absolute path exists (no content). | risk | low; backlog B-4 (restrict to owner roots). |
| R5 | Approval preview shows the arguments (action/target/text/generation), not the screen; the owner relies on the handler's fresh-screen rebinding. «Пульт» shows a fresh screen before deciding. | risk | backlog B-1 (attach a cropped screenshot to the question). |
| R6 | A refused lease request returns HTTP 500 (`PermissionError` from `approval_scope.grant` inside the decision transaction; the decision is rolled back, nothing is granted). | risk (UX) | `api.py` owner: map PermissionError from `_lease_from_parked_call` to 409. |
| R7 | PIT (Jeff) holds the core backend token for `/api/studio/*`; the token is full-authority. No PIT code references `/api/computer`, `/api/approvals` or `/api/tasks` (asserted by test), but the credential itself is not least-privilege. | risk | backlog B-5 (scoped studio token). |
| R8 | bossman-core `ComputerOperatorManager` (/computer/tasks in the bossman-core API) keeps the older semantics (pay/transfer approvable, no process allowlist for input). Not mounted in the Command Center backend. | risk | backlog B-6: either retire it or route it through the same `tools_computer` gates. |
| R10 | Task outcome vs. desktop evidence: the action-contract finalizer classifies "open Notepad" as an `apps` action and fails the task (`action_contract/no_verified_action`) although `computer.act launch` was deterministically verified; tasks whose script contains deliberately refused CU calls end `failed` ("effectful tool did not succeed"). The desktop effect/evidence is correct; only the task-level status is conservative. After a backend kill with `max_retries=0` the run fails ("lease expired") and the dispatched call's journal row stays `started` (effect unobserved) instead of being marked `interrupted` — never re-executed. | risk (reporting) | owners of `action_contract.py` / `engine.py`: accept a ПРОВЕРЕНО `computer.act` as evidence for the apps capability when the agent owns `computer.*`. |
| R9 | Smart App Control: `pywin32` / `pyautogui` native parts must be allowed on the owner machine; they worked on 2026-09-26/28 via the system Python. The bundle lock pins them but the bundle's `verify_runtime` does not import them. | risk | workstream A (see report). |

## 3. Allowlists (current, minimal)

* Apps (launch): `notepad`, `calculator` (logical names only; resolved inside `%SystemRoot%`; no paths/args/shell metacharacters).
* Windows (input / focus_window): processes `notepad.exe`, `calc.exe`, or `ApplicationFrameHost.exe` hosting the verified `Microsoft.WindowsCalculator_*` package. Nothing else — no browser, terminal, messenger, Explorer, Bossman.
* URLs: `computer.act` has no navigation; browser work goes through the browser toolkit with its own domain policy. `launch "https://…"` is an engine DENY.
* 2026-10-10 (task 83 follow-up): per-task owner grant from the catalog (`notepad`, `calculator`, `charmap`, `paint`) via `PUT /api/computer/tasks/{id}/apps`; with a grant, input/focus/targeted observation reach only windows launched by that task (Win11 Notepad shares one process across the owner's private windows, so process identity alone is not enough). `applist.HARD_DENIED_PROCESSES` (shells, terminals, browsers, messengers, password managers, UAC, credential UI, Windows Security, Explorer, Bossman) can never be granted or targeted. Every approved input/`focus_window` carries the exact `window` (hwnd) + `pid` of a fresh observation; the effect re-reads that hwnd and refuses on a changed pid/title/tabs/content or when it is not in front — the server does not restore focus on its own. Secret-like text and irreversible external actions (`send`, upload, deploy, push, merge, release, uninstall) are refused like payments. Tests: `command-center/tests/test_cu_task_apps_binding.py`.
* Paths: no path input to launch; file postconditions must be absolute; content read only for files the action wrote.

## 4. Approval scope

One owner decision = one `computer.act` call: bound to the exact arguments
(digest), consumed once (CAS), valid for 300 s from the question, bound to the
task, journaled as used before the effect, rebound to the same window on a fresh
screen. `wait` (no desktop effect) is the only approval-free action; its
postcondition cannot read content of files it did not write.

## 5. Live acceptance (2026-09-28, rc19/b-cu @ 9c608997)

`tools/cu_acceptance/run_cu_acceptance.py`: own backend (:8840, fresh data dir,
started via explorer.exe at owner integrity), scripted OpenAI-compatible model
(:8841) choosing tool calls, every owner decision / STOP / resume through the
product CLI client, real Notepad. Verification by the product verifier plus
independent read-only UIA readback and SHA-256 of the saved file. Scenarios
a, b, c, d, d2, e, e_replay, f, g, g2: REAL_PASS; h: MOCK_ONLY (no Telegram
allowed; PIT has no CU path). Evidence: `evidence/rc19/b/cu_acceptance_20260928T093405Z.json`
(cleanup: `cu_acceptance_20260928T094127Z.json`; first attempt with harness
defects: `run1_cu_acceptance_20260928T092646Z.json`).

## 9. Jeff / participants

Jeff (`bcc/pit`) builds its model calls without any tool schema, filters tool
names through `TelegramToolPolicy` (denies the `computer` prefix — asserted
against the real registered names), refuses owner-console commands (`/screen`,
`/approve`, `/resume`, `/stop`, `/open`, `/task`, `/pc`, `/shell`, …) before
dispatch, marks `computer_control` as `NEVER_PARTICIPANT`, and imports neither
the engine, the tool registry, the CU feature nor the companion `Core` client.
Test: `command-center/tests/test_cu_participant_perimeter.py`. The owner's
«Пульт» bot is the only Telegram surface that can approve or STOP CU, and every
effect it approves still passes `claim_approval` in the handler.
