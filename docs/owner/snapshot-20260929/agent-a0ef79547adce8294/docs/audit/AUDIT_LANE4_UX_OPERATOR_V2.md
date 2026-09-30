# BOSSMAN V1 RC — INDEPENDENT RE-AUDIT LANE-4 UX OPERATOR V2

**BASE_HEAD:** `9a0db65075e5d5f24a347c20b16947d71d3ef854` (feat profiles)
**REMOTE_HEAD:** `9a0db65075e5d5f24a347c20b16947d71d3ef854` (origin/claude/bossman-control-v03-43igbk)
**AUDIT_HEAD:** `9a0db65` (checked 2026-08-30, branch claude/audit-lane4-ux-v2)
**VERDICT:** `NEEDS_ATTENTION` (no P0, but 3 P1 RC blockers)
**P0:** 0  **P1:** 3  **P2:** 5
**OLD HEAD REJECTED:** `4fb8b6f` (Pythia integration) irrelevant for UX audit

---

## 1. OPERATOR_PATH_MATRIX

| Stage | Status | Evidence | API Source | UI Render | Operator Action | Dead End | Failure State |
|-------|--------|----------|------------|-----------|-----------------|----------|---------------|
| **Task** | PASS | `command-center/bcc/db.py:68 tasks,83 task_runs` + `bcc/api.py:554 list,569 create,598 get,611 action` + `bcc/engine.py:78 enqueue,229 claim` | `GET/POST /api/tasks, GET /api/tasks/{id}, POST /api/tasks/{id}/{run,pause,resume,stop,retry}, GET /api/runs/{id}/events` (real) | `ui/pages.js:896 TasksPage` composer+chips+board+drawer, `ui/pages/home.js:188` + `overview.js:95` Quick Command | Create → queue → run → pause/resume → stop | None | `failed`/`stopped` rendered with error |
| **Session** | PARTIAL | 4 parallel realities: `bcc/sessions.py:21 auth session` REAL, `bcc/coding_session.py:72 CodingWorktreeManager` REAL but orphaned, `bcc/features/opencode.py:61 OpenCodeBridge` REAL via 127.0.0.1:4096, `bcc/features/terminal.py:27 + browser.py:32` REAL, `bcc/features/forks.py:42 fork` REAL | `POST /api/opencode/sessions, /terminal/run, /browser/sessions, POST /runs/{id}/fork` but **NO** `/api/coding-sessions/*` | `terminal.js:26, browser.js:17, forks.js:19` wired; **NO** `#/coding-sessions` page for CodingWorktreeManager | CodingWorktreeManager unreachable via HTTP (library only) | `opencode:502 when serve down → fallback snapshot` else `offline` |
| **Activity** | PASS | `bcc/events.py:20 EventBus` → `events` table `bcc/db.py:154` + `api.py:435 GET /api/activity` + `WS /api/events` | `GET /api/activity, WS /api/events` | `pages.js:159 home, overview.js:53, app.js:343 subscribe` `activityRow` | View only | None | Transient events filtered correctly |
| **Diff** | PARTIAL | `bcc/coding_session.py:141 diff` (`git diff --stat/patch vs base_sha` argv-only, 400k cap) REAL; `bcc/v2/opencode_bridge.py:203 diff` + `bcc/features/opencode.py:187 GET /opencode/sessions/{id}/diff` REAL; `tools_opencode.py:355 opencode.diff` tool | `GET /api/opencode/sessions/{id}/diff` (live or snapshot), `tool opencode.diff` | **NO** top-level diff page; diff only via tool result or `/opencode/.../diff` explicit call | Task→Diff click missing | `source:"snapshot"` when serve down honest |
| **Diagnostics/LSP** | PARTIAL | `bcc/lsp_bridge.py:36 LSPClient` argv-only, bounded, `features/code_intel.py:48 _run` via `LSP_SERVERS` env, `GET /code-intel` | `GET /code-intel, tool code:diagnostics/definition` | **NO** diagnostics page; model tool only | Call tool `code:diagnostics` | `no LSP server configured` honest error |
| **Tests** | PARTIAL | No `/api/tests`; tests via `terminal.run pytest` (AUTO) + `features/benchlab.py:24 bench` via `adapter.chat` | `POST /benchmarks, GET /benchmarks/compare` (model bench) | `pages/benchmarks.js:17` shows model bench, **no repo test page** | Run via terminal | bench `not_tested` for tool_calling |
| **Review** | PARTIAL | `v2/reviewer_gate.py:9 ReviewGate` + `features/review_gate.py:69 gate_completion` hook, deterministic or `adapter.chat PASS/FAIL` + `evaluations` table | `POST /review/enable, GET /review/status` | **NO** review page; `diff_aware_review:223` not wired to gate | Enable via API only | `waiting_approval` escalation after 3 fails |
| **Approval** | PASS | `bcc/db.py:129 approvals + 350 tool_calls`, `bcc/approvals.py:20 create,41 decide` idempotent, `bcc/api.py:682-698` | `GET /approvals?status=pending, POST /approvals, POST /approvals/{id}` | `pages.js:1419 ApprovalsPage` cards+decide, `app.js:338 badge` | Approve/Reject | Stale pending forever (no TTL) |
| **Merge/Action** | PARTIAL | `coding_session.py:150 merge_preview git merge-tree, 168 merge serialized _MERGE_LOCK` REAL | **NO** `/api/merge` | **NO** merge button; via `terminal git merge` | Manual | `conflicts → STOP` correct |
| **Audit** | PASS (scattered) | `events 154, interventions 324, recovery 335, tool_calls 350, facts 383` + `GET /api/activity, /governor/interventions` | Multiple endpoints, no `/api/audit` | Fragmented `healing.js, governor.js, resources.js` | View slices | No unified trail (P2) |

**Summary:** Task→Activity→Approval chain is end-to-end REAL. Session→Diff→Diagnostics→Tests→Review→Merge chain is **fragmented**: backend REAL but not exposed via unified operator pages. Severity ≤P1 per rules (backend exists → not P0).

---

## 2. DEGRADED_STATE_MATRIX

| Subsystem | Backend Health Code | UI Display | Expected `BACK→UI` | Actual | Fake-Green? | Source of Truth | Severity |
|-----------|---------------------|------------|-------------------|--------|-------------|-----------------|----------|
| **Ollama** | `providers.py:160 health() GET /models 6s`, `discovery.py:139 probe 2.5s`, `gateway/backends.py:133 probe 4s` strict 2xx only | `pages.js:333 ModelsPage` per-model `statusBadge` (honest), `overview.js:60 count` | `offline → UI offline` | **System page `ok` while all models `offline`** because `bcc/api.py:713 _health` only `db,queue_worker,scheduler,metrics` | YES MEDIUM | `Registry DB models.status` + Gateway probe | P2 (visibility) |
| **Browser** | `v2/browser_control.py:394 available, 402 SDK check, 470 _guard deny/ask, 486 status redact` | `browser.js:22 list empty→blank` | `unavailable → UI degraded` | blank `«Агент сам открывает»` not error; System `ok` | YES HIGH | `BrowserManager._sessions` + `browser_sessions` table, `GET /api/browser/sessions` | P1 (dead end not signalled) |
| **Providers** | `registry.py:125 check_model, providers.py:208` | `pages.js:333 per-card offline/idle/online` | `offline → UI offline` | `unknown→idle grey` not `err`; System `ok` | YES (partial) | `DB providers/models` | P2 |
| **Pythia** | `world_intelligence/subsystem.py:54 critical=False, 88 _get fail-soft, routes.py:27 health` | **NO page** | `degraded → UI degraded` | No dot anywhere; System omits | YES HIGH (observability) | `_state + GET /world_intelligence/health` | P2 (by design fail-soft, but gap) |
| **Plugins** | `features/plugins.py:28 MANIFEST 13 caps, 365 GET /plugins` `health:idle` not live | `skills.js` blank | `missing cred → UI idle` | `unl-` vs `missing` distinction exists but not health | YES MEDIUM | `GET /api/plugins` config, not live probe | P2 |
| **Gateway** | `gateway/backends.py:133, router.py:56, app.py:211 /health any healthy→ok` | **No BCC page** | `degraded → UI degraded` | BCC System never queries Gateway `/health` | PARTIAL (Gateway honest, BCC gap) | `GET /health + /metrics` | P2 |
| **Stage13** | `computer_operator/subsystem.py:26 cloud_policy never, 54 critical=False` | none | `unavailable on Linux → UI offline` | System `ok` even on Linux where dispatch impossible | YES HIGH (platform) | `tasks.json + ControlLease` | P1 (core dispatch assumed ready) |
| **Cost Governor** | `cost_control/enforcer.py:20 reserve, gateway/app.py:66 cost_reserve, 264 pre-cloud hook` | `governor.js:13 rules + interventions` | `budget deny → UI warn` | `has_enabled_policies==False → silent unlimited spend`, no `health.spend` | YES MEDIUM | `cost_control.db + GET /api/governor/rules` | P2 (fail-open invisible) |
| **Telegram** | `notifications/telegram_transport.py:20 enabled,53 hmac, runtime.py:18 queue` | none | `disabled → UI idle` | System `ok` (by design best-effort) | YES LOW (expected) | `GET /notifications/queue` | P2 |
| **MCP** | `v2/mcp_runtime.py:75 health,325 sdk check,372 probe` | none | `unhealthy → UI degraded` | System `ok` | YES MEDIUM | `GET /api/mcp/servers` | P2 |

**Root cause systemic:** `bcc/api.py:713 _health()` aggregates only 4 liveness signals; `ui/pages.js:1500 normalizeSystem` does `!health.length ? 'ok'` (empty → ok) and `app.js:587 loadTopStats` maps unknown strings → `ok`; `overview.js:61` checks HTTP 200 not degraded content. Result: `BACKEND_DOWN → UI_OFFLINE` fails for 6/10 subsystems. Required evidence `BACKEND_DEGRADED → UI_DEGRADED` missing.

**Severity rule:** Most are **P2 visibility** (does not block task completion). Browser empty + Stage13 platform mismatch → **P1** because operator cannot complete browser/dispatch task and sees green.

---

## 3. DIFF WORKFLOW

**Repo-wide search `rg -n "diff|patch|git diff"`:** ~70 hits, functional filtered:

- **Backend methods REAL:**
  - `bcc/coding_session.py:141 diff` (`git diff --stat` + `patch` vs pinned `base_sha` `119`, `max_bytes 400_000`)
  - `bcc/v2/opencode_bridge.py:203 diff` (`GET /session/{id}/diff`, `diff_summary 239`, `render_diff 250` 8k limit)
  - `bcc/features/opencode.py:187 GET /opencode/sessions/{id}/diff` (live → `source:live`, fallback → `source:snapshot` from `run_events opencode.diff`)
  - `bcc/features/tools_opencode.py:189 persist_diff` stores diff in `run_events`, `355 opencode.diff` tool
- **API under different name:** No `/api/diff` or `/api/git/diff`. Canonical is `/api/opencode/sessions/{id}/diff` + tool `opencode.diff`. `CodingWorktreeManager.diff` has **no HTTP router** (`coding_session.py` has no `APIRouter`, `bcc/api.py:314 load_features` only discovers `features/*`).
- **Session diff in response:** Not auto-embedded in `GET /api/tasks/{id}` or `/runs/{id}`; persisted via `run_events kind=opencode.diff` and returned via snapshot fallback.
- **Old audit claim “нет `/api/diff`”:** **FALSE POSITIVE**. Backend diff exists but under `opencode` namespace; UI missing is ≤P1 (backend exists), not P0.

**Verdict:** Diff **PARTIAL** — backend REAL, fragmented across two managers, no unified operator page. Impacts merge/review but not data loss → **P1**.

**Minimal fix:** Wire `CodingWorktreeManager` behind `POST /api/coding-sessions/{id}/diff|merge_preview|merge|discard` and add `#/diff` page consuming `/opencode/.../diff` + `/review/status`.

---

## 4. APPROVAL INBOX

**API:** `GET /approvals?status=pending` `api.py:682`, `POST /approvals` `686`, `POST /approvals/{id} {"approve":bool,"by":"ui"}` `691`, `WS approval.decided` → `engine.py:733 approval_watcher` + `recover() 257` sweep.

**Payload actual vs required:**

| Required | Actual | Evidence |
|----------|--------|----------|
| action | `approvals.kind` + `tool_calls.tool` | `db.py:129 kind, 350 tool` |
| target | `task_id/run_id/call_id` | `db.py:129,350` |
| arguments | `tool_calls.args JSON + args_hash` | `db.py:350` |
| effect | `tool_calls.effect auto/ask/deny` | `engine.py:560 decide_effect` |
| reason | `preview` substring `причина политики:` + `args` | `engine.py:572 preview = "хочет выполнить {tool} причина: {reason} args:{json}"` |
| risk | **Not stored**; inferred via `category read/write/send` + `destructive` | Missing explicit risk band |
| consequence preview | `preview` (500 chars) + `tool_calls.result_preview` | truncated |
| requesting agent | `task.agent_id` via `join` not in approval row directly | need fetch task |
| created_at | `db.py:129 created_at` | yes |

**Guarantees:**
- `APPROVE executes once`: `approvals.py:41 WHERE status='pending'` + `tool_calls UniqueConstraint(run_id,call_id)` + `engine.py:801 IntegrityError` update → **PASS**
- `REJECT does not execute`: `engine.py:677 pending_tool_call` remains, not resumed → **PASS**
- `duplicate callback does not double-execute`: second `POST /approvals/{id}` returns existing row, no second `approval.decided` event (only on `rowcount==1`) → **PASS**; Telegram `consume_callback` single-use also
- `stale approval rejected/handled`: **PARTIAL** — no TTL, pending forever inflates badge (`pages.js:338`), task stays `waiting_approval` indefinitely; `recover()` sweeps only non-pending. No auto-reject.

**Severity:** Stale forever → **P2** (badge leak, not bypass). Missing explicit risk band → **P1 UX** if destructive `gmail.send/telegram.send` preview doesn't surface destructive consequence clearly (current preview generic). No P0 (no bypass).

---

## 5. CODING SESSION ACTIONS

| Operation | Backend | API | UI | Auth/Policy |
|-----------|---------|-----|----|-------------|
| **create** | `coding_session.py:102 create` pin SHA, confinement | NO (library only) | NO page | — |
| **status/diff** | `coding_session.py:130 status,141 diff` | NO | NO | — |
| **merge_preview** | `coding_session.py:150 merge_preview merge-tree` | NO | NO | — |
| **merge** | `coding_session.py:168 merge _MERGE_LOCK, abort` | NO | NO | — |
| **discard** | `coding_session.py:194 discard` | NO | NO | — |
| **resume/fork** | `features/forks.py:42 fork, 33 checkpoints`, `features/opencode.py:97 send wait=false,169 fork` | `POST /runs/{id}/fork, POST /opencode/.../fork, POST /api/tasks/{id}/pause|resume|retry` | `pages/forks.js:19 full` + `mobile.js abort` | `require_token` + `allowlisted dir` |
| **review** | `review_gate.py:120 POST /review/enable, 135 GET /review/status` | YES | NO page | — |
| **stop/cancel** | `api.py:611 task_action stop|pause|resume` | YES | `tasks.js:970 chips` | — |

**Verdict:** `resume/fork/stop` via tasks/forks/opencode **PASS**; `merge/discard/review/diff` via CodingWorktreeManager **PARTIAL** (backend REAL, no API/UI). Not requiring new API for V1 if contract excludes native git worktree merge, but V1 operator flow **breaks** at `→Merge` → **P1**.

---

## 6. PLUGIN HEALTH

**Exists:** `GET /api/plugins` `features/plugins.py:365` returns `{plugins:[{plugin,enabled,health:idle,capabilities:[...],credential:configured|missing|n/a}],count}` for 13 caps (http, monitor, sql, obsidian ×2, mcp ×2, ollama, openrouter, github ×2, gmail ×2, calendar ×2, drive ×2, telegram ×2, n8n ×2, browser ×2) — **CONFIGURATION STATUS**, not live probe. `health` always `idle`, not `healthy/unhealthy` from runtime. Credential never exposes raw secret (only `configured/missing`).

**Distinction:** `CONFIGURATION STATUS` (credential present) vs `LIVE HEALTH` (probe). `_status_rows` does not call `probe()`; `mcp_runtime.probe()` exists but not aggregated. So **LIVE HEALTH missing**.

**Severity:** Not P0; `post-RC` Polish — add `runtime health` aggregation (`probe` + `browser available` + `gateway health`) into `/api/system` without new subsystem (reuse existing registry).

---

## 7. COST + LATENCY

- Task cost: via `cost_control` `reserve/commit/release` per `gateway/app.py:264` cloud only; local `cloud_policy=never` → 0 cloud calls proven (Stage13 `cloud_policy never`).
- Model cost: `providers.py:160` health not cost; `cost_control` stores `cost_usd` per run (not surfaced in task card).
- UI: `governor.js:13` fetches `GET /api/governor/rules` + interventions; no per-task cost/latency pill in `TasksPage`.
- **P2** by default (does not block safe completion).

---

## 8. JS / UI STATIC CHECK

```bash
node --check command-center/ui/api.js .......... OK
node --check command-center/ui/app.js .......... OK
node --check command-center/ui/components.js ... OK
node --check command-center/ui/pages.js ........ OK
node --check command-center/ui/pages/agentmap.js OK
node --check command-center/ui/pages/apps.js ... OK
...
(27 files) — all OK, 0 fail
```

No build tooling lint beyond `node --check`; no new framework added.

---

## 9. LIVE/API SMOKE

- `GET /api/system` → `401 Unauthorized` (server running on `127.0.0.1:8800`, `require_token` enforced) — **evidence: auth perimeter alive**, not 502. Need `POST /api/login` with `BCC_TOKEN` to get `bcc_session` + CSRF, not attempted in this audit (would require cred). Honest `401` not fake 200.
- `GET /api/health` (gateway 8765) → timeout (no gateway on this host) → `SKIP_HOST` honest (not claimed healthy).
- No full sweep without cred; payload linkage not verified live → `NOT_TESTED_LIVE` for live payload, static evidence used.

**HTTP 200 alone not PASS** — checked payload via code, not live.

---

## 10. FINDINGS

### FALSE_POSITIVES_FROM_OLD_AUDIT

| # | Old Claim | Reality | Evidence | Impact |
|---|-----------|---------|----------|--------|
| F-1 | “нет `/api/diff`” | Exists as `GET /api/opencode/sessions/{id}/diff` + tool `opencode.diff` + `CodingWorktreeManager.diff` (no HTTP) | `features/opencode.py:187`, `coding_session.py:141`, `tools_opencode.py:355` | Downgrade P0→P1; backend exists, UI missing |
| F-2 | “plugin health endpoint отсутствует” | Exists `GET /api/plugins` | `features/plugins.py:365` returns 13 plugins with `configured/missing` | Not P0; live probe missing is P2 |
| F-3 | “approval bypass” | No bypass; idempotent `WHERE status='pending'` + `UniqueConstraint` | `approvals.py:41`, `engine.py:801` | Rejected |

### CONFIRMED_FINDINGS (from old audit, still valid)

| ID | Title | Severity | Status |
|----|-------|----------|--------|
| C-1 | CodingWorktreeManager orphaned (no API/UI) | P1 | Confirmed — `coding_session.py` has no router, `load_features` only `features/*` |
| C-2 | Review has no operator page | P1 | Confirmed — `review_gate.py:120` API exists, no page |

### NEW_FINDINGS

#### P1-1: Session→Diff→Merge operator chain broken (dead end)
- **REPRO:** Create task → `POST /runs/{id}/fork` OK, `GET /api/opencode/sessions/{id}/diff` OK via API, but `TasksPage` drawer `loadTaskDetail` does not link to diff; `coding_session diff/merge` unreachable.
- **ROOT CAUSE:** `coding_session.py` is library, never mounted as `Feature` router; `opencode diff` is under `opencode` namespace not task flow.
- **IMPACT:** Operator cannot complete `Review → Merge` via UI; must use `terminal git merge`.
- **SEVERITY:** P1 (core V1 workflow cannot be completed via UI)
- **EVIDENCE:** `coding_session.py:141` diff REAL, `features/opencode.py:187` diff REAL, `ui/pages.js:1063` no `diff` fetch
- **MINIMAL FIX:** Wire `CodingWorktreeManager` behind `POST /api/coding-sessions/{id}/diff|merge_preview|merge|discard` reusing existing `allowed_roots` + `safe_name`, add `#/diff` page (reuse `render_diff` from `opencode_bridge`).
- **REGRESSION:** `LSP_ALLOWED_ROOT` style tests + `test_polish_lsp_and_coding.py` merge tests

#### P1-2: System fake-green (empty → ok, many subsystems invisible)
- **REPRO:** `GET /api/system` returns `health:{db:ok,queue_worker:ok,scheduler:ok,metrics:ok}` while `BrowserManager.available==False`, `models offline`, `Pythia offline`, `Stage13 unavailable` → UI `pages.js:1500 overall = !health.length ? 'ok'` + `app.js:587 unknown→ok` shows `в норме`.
- **ROOT CAUSE:** `_health()` only 4 signals; `normalizeSystem` empty→ok
- **IMPACT:** Operator sees green while dispatch/browser offline, may start task expecting browser
- **SEVERITY:** P1 (fake-green misleads operator; browser/Stage13 are V1 core)
- **EVIDENCE:** `bcc/api.py:713`, `ui/pages.js:1500`, `overview.js:61` healthOk only HTTP 200
- **MINIMAL FIX:** Extend `_health` to include `browser, registry (models), world_intelligence (if configured), stage13, mcp` using existing `validate()/probe()` without new subsystem; fix `normalizeSystem` to `!health.length ? 'degraded' : ...` and `loadTopStats` unknown→`warn`.
- **REGRESSION:** `BACKEND_DOWN → UI_OFFLINE` for each subsystem

#### P1-3: Browser dead end not signalled
- **REPRO:** `GET /api/browser/sessions` empty list → UI blank `«Агент сам открывает…»` not `degraded`; System remains `ok`
- **ROOT CAUSE:** `BrowserManager` degraded not aggregated into system health; `browser.js:22` treats empty as blank not error
- **IMPACT:** Operator cannot know browser unavailable until `POST /api/browser/sessions` 502
- **SEVERITY:** P1 (core browser action dead end)
- **EVIDENCE:** `v2/browser_control.py:394`, `pages/browser.js:22`, `api.py:713`
- **MINIMAL FIX:** Add `browser: available ? 'ok' : 'offline'` to `_health` and banner in `browser.js` when `probe` fails.

### P2s

| ID | Title | Impact | Fix |
|----|-------|--------|-----|
| P2-1 | `Pythia` no UI health dot (by design `critical=False` but invisible) | Observability | Add `world_intelligence` to `_health` if `base_url` configured, else omit (not P1) |
| P2-2 | Approval preview missing explicit `risk` band | UX | Add `risk` field derived from `capability.risk` + `destructive` to preview |
| P2-3 | Stale approvals forever (no TTL) | Badge leak | Add nightly sweep `>7d pending → warn` or UI filter |
| P2-4 | No unified audit trail page | Visibility | Add `#/audit` aggregating `events`+`interventions` (reuse existing tables) |
| P2-5 | Cost Governor `has_enabled_policies==False` invisible unlimited spend | Visibility | Add `health.cost` = `limited|unlimited` to system |

---

## 11. RC_BLOCKERS

**Confirmed RC blockers (must-fix before V1):** P1-1, P1-2, P1-3.

**Not blockers:** All P2s above + old P0 claims rejected. Cost/latency, richer diagnostics, optional controls are post-RC.

---

## 12. POST_RC

- Unified `#/diff` + `#/review` pages
- Real Pythia health dot + cost per-task pill
- Stale approval TTL + unified audit
- Streaming `eval_scorecard.load_jsonl` (lane2) already fixed via `code_intel` hardening in lane2 branch
- LSP workspace confinement already fixed in `claude/audit-lane2-coding`

---

## 13. STATIC_TESTS / LIVE_TESTS / NODE_CHECK

- **STATIC:** `rg` found 70 diff hits, 13 plugin caps, 4 health signals, 27 frontend routes
- **NODE_CHECK:** 27/27 `node --check` PASS (api.js, app.js, pages.js, 24 sub-pages)
- **LIVE:** `GET /api/system → 401` (auth perimeter PASS, not 200), `WS /api/events` not tested without login → `NOT_TESTED_LIVE` for payload; `SKIP_HOST` for gateway live

---

## 14. VERDICT

**LANE4_V2:** `NEEDS_ATTENTION`

```
BASE_HEAD: 9a0db65075e5d5f24a347c20b16947d71d3ef854
FINAL_AUDIT_HEAD: 9a0db65 (branch claude/audit-lane4-ux-v2)
P0: 0  P1: 3  P2: 5
CONFIRMED_RC_BLOCKERS: P1-1 Diff/Merge chain, P1-2 System fake-green, P1-3 Browser dead end
OLD_FINDINGS_REJECTED: 3 (diff endpoint, plugin health, approval bypass)
TESTS: static 27 JS node-check PASS, live smoke 401 honest, no pytest claims without evidence
NODE_CHECK: PASS (27/27)
LIVE: 401 honest, SKIP_HOST for gateway, NOT_TESTED_LIVE for full sweep
VERDICT: NEEDS_ATTENTION (3 P1 fixable with small wiring, no P0)
```

---

*Generated for independent re-audit, no trust in old labels/SHA, runtime evidence where possible, static proof otherwise.*

