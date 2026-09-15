# CHECKPOINT-1 — freeze-20260907-130104 — OpenRouter + Total Audit

RUN_ID: freeze-20260907-130104
TESTED_RUNNING_SHA: 931584d72983ba205a5bf9f0b04c71a1b3326cf9 (branch feat/web-designer-live-panel, worktree = running app on 127.0.0.1:8800)
START_REMOTE_PRIMARY_SHA: ddea21112f89c978df50aae8948c5955d7dada2e (origin/claude/bossman-control-v03-43igbk)
ACCEPTANCE_BRANCH_SHA: bab5ac6a7c2ea538534ee586abb49653d7835734
V5_SOURCE_BRANCH_SHA: 67905ee2078c6aab6587f677960dd800d354bb61 (NOT merged locally — verified)
PUSH_CONFIRMED: yes (this commit)

HOST: Windows 11, Python 3.14.3 (command-center venv), Docker daemon DOWN (core/Postgres stack NOT started — ENVIRONMENT_BLOCKER, core:8700 not running)
NOTE: bossman-core requires Postgres (asyncpg only, bossman/db.py:1-9) → ACTOR=BOSSMAN_AGENT core flows = NOT_RUN this checkpoint.

Mode: AUDIT-ONLY per owner instruction (findings, no fixes). Dashboard: Command Center launched via `python -m bcc` (owner path `bcc`/`bcc-desktop`).

## OpenRouter — LIVE (owner key, stored in temp file OUTSIDE repo; never printed/committed)

- Key validity: VALID (GET /api/v1/key → label "sk-or-v1-e50...c83", usage=0, free_tier=false)
- Live catalog: TOTAL=430 models; exact GLM ids confirmed live: `z-ai/glm-5.3`, `z-ai/glm-5.3-flash`, `z-ai/glm-5.2`, `z-ai/glm-4.7` … (pricing.prompt/completion = null for GLM family)
- Provider onboarding via API (same flow UI wizard uses): POST /api/providers → id=2 "OpenRouter" (kind=openai_compat, base_url=https://openrouter.ai/api/v1) → PATCH /openrouter/2/key → has_key=true → POST /openrouter/2/connect → **ok=true, models=430, cached=false** (fresh sync, last_synced_at 2026-09-07T11:26:55Z)
- Pre-existing state: only provider id=1 "Ollama (локальная сеть)" base_url=127.0.0.1:11435/v1, 1 local model qwen2.5:7b

## LIVE REPRODUCTION of historical bugs

- OR-004 CONFIRMED LIVE: only non-OpenRouter provider existed → `ui/pages/openrouter.js:44` binds page to `providers[0]` (Ollama); key PATCH would be saved into the Ollama row. Workaround used: separate provider creation (what a real owner would not know to do).
- /health endpoint MISSING: GET /health, /api/health, /healthz → all 404 on running app (health only embedded in authed /api/system). App manifests default-probe /health (bcc/features/apps.py:88) — Command Center itself fails its own convention.

## FINDINGS MATRIX (8 parallel audit agents, all at SHA 931584d)

### P0
| ID | AREA | PROBLEM |
|---|---|---|
| SEC-001 | secrets | Vault master password "SuperSecretMasterPass123!" in pushed git history (solana_volume_suite, docs/v3) + SAME value live in solana_volume_suite/.env:5 (mainnet configured). Public remote. History rewrite + rotation needed. |
| DO-001 | desktop executor | Windows executor real but mutating half (TYPE/HOTKEY/CLICK) never validated end-to-end; pywinauto/pyautogui in NO requirements file → fresh install lacks deps (windows.py:67 "pyautogui missing") |
| DO-002 | desktop | APP_CLOSE declared (models.py:21, capabilities.py:32) but NO backend supports it (windows.py:43-45 omits it) → scenario A "close only that window" impossible; Alt+F4 fallback can close wrong window |
| DO-009 | AT-01 | Production manager accepts planner-claimed COMPLETE unconditionally (manager.py:100-103, verifier.py:11 rubber-stamp); honest FalseCompletion gate exists only in default-OFF unwired apprentice engine |
| V5-001 | V5 objectives | Entire V5 objective layer (objectives/steward/canary/fairness) DOES NOT EXIST in worktree — only on unmerged branch origin/claude/v5-closure-at-reconcile-xdh12f; bossman/core/ = stale bytecode only |
| HM-001 | Hermes | Hermes DOES NOT EXIST anywhere (repo-wide grep incl. zips = 0) — claimed module absent |
| FU-001 | Fusion | Multi-model Fusion DOES NOT EXIST (only unrelated visual_state/fusion.py, 0% coverage, flag-gated OFF, 0 prod imports); per-participant telemetry absent |

### P1
| ID | AREA | PROBLEM |
|---|---|---|
| OR-002 | catalog | CONFIRMED STILL PRESENT live: features/openrouter.py:148 sort by remote_id + hard cap 200, response has NO total/has_more, UI shows no "Showing X of N"; z-ai/* unreachable in default view of 430-model catalog (live check in progress for CHECKPOINT-2) |
| OR-004 | onboarding | Live-reproduced above: wrong-provider fallthrough binds OpenRouter page to providers[0]; key saved to arbitrary provider row; sync imports wrong base_url catalog with misleading "OpenRouter ответил…" errors |
| OR-003 | credentials | 3 divergent credential paths: vault via UI vs env BOSSMAN_OPENROUTER_API_KEY (features/openrouter.py:271) vs env OPENROUTER_API_KEY (plugins.py:98-99 reads ONLY env, never vault) → "saved here, read there" class |
| OR-005 | providers | No uniqueness on provider name/base_url (registry.py:44-57, db.py:31-39); non-atomic wizard (create provider → create model) leaves orphan provider on 409 retry |
| OR-006 | sync errors | Catalog sync classifies 401/403 as "OpenRouter недоступен" (503, same as network outage) — key rejection never named on Refresh path |
| OR-007 | dead code | openrouter_catalog_service.list_catalog/pin dead (never called); live feature has different sort/caps logic — drift breeding ground |
| DO-007 | vision | NO OCR/vision screen reading anywhere in computer_operator; screenshots write-only (screenshot.py:8-14); if UIA tree unavailable agent is pixel-blind (Calculator scenario) |
| DO-008 | verification | Postcondition contains_text checks match only window titles (observer summary has no UI tree, subsystem.py:136) — "Calculator shows 5480" unverifiable by construction |
| DO-010 | AT-03 | Post-approval action executes on PRE-approval observation (manager.py:113-140); hours-long approval window → desktop drift invisible; generation bumps only on human pause |
| DO-011 | AT-03 | No coordinate grounding; UI tree has no bounding rects; `source:"vision"` is model-self-claimed, policy.py:42 trusts self-reported confidence |
| DO-014 | idempotency | Production manager has NO side-effect ledger (idempotency_key only copied to approval payload, manager.py:116-118); durable ledger exists but only in unwired apprentice (guards.py:114-137) |
| AP-001 | security | Default terminal root = data dir (tools_terminal.py:76) where token file + bcc.db live → agent with terminal.run can `cat token` → self-grant permissions (PATCH /agents/{id} needs no approval, api.py:714-724) + self-decide approvals |
| AP-002 | crash safety | Crash between external effect and receipt write (engine.py:1024-1027) → on restart `_resume_pending_tool` re-executes approved non-idempotent tool (no claim-before-effect in bcc; apprentice/V3 have it) |
| LG-005 | baseline | bossman/cognitive/ = EMPTY package (stale .pyc only), documented source of prior ModuleNotFoundError startup failure; landmine for any re-introduced import |
| VS-001 | video | Video Studio app NOT in this branch (only on codex/video-studio, diverged at debee69); e2e cycle never executed |
| WD-001 | web designer | CONFIRMED historical bug live in code: `+ Проект` does state.id=null → refresh() → render() repopulates id from localStorage/projects[0] (web_designer.js:567-572) → create form unreachable when ≥1 project exists |
| WD-002 | web designer | DOM round-trip lowercases SVG case-sensitive attrs/tags (web_designer_dom.py:74-76): viewBox→viewbox, clipPath→clippath → inline SVG breaks after ANY edit; every edit reparses whole doc |
| RH-001 | repo | 19 root zip/png binary packs (~6+MB incl. duplicates) TRACKED in git despite ignore rules — freeze blocker |

### P2 (condensed)
- OR-001 partially fixed: blank-state button → Models page wizard; but no in-page creation, no OpenRouter preset kind (only openai_compat|anthropic), fragile multi-hop
- DO-003: APP_LAUNCH accepts no args → cannot reopen saved file by path (app_launch.py:53-55 by design)
- DO-004: no right-click/context-menu kind; folder creation only via untested Save-dialog UI_INVOKE
- DO-005: pyautogui.write() cannot reliably type Cyrillic (windows.py:71), no clipboard fallback
- DO-012: desktop operator has NO path-scoped AUTO/ASK policy (policy.py:60 default allow)
- DO-013: degrade-open re-offers unbacked kinds incl. APP_CLOSE (planner.py:31-42)
- DO-015: manager approvals never `consumed` (approvals.py has no consume call in agent path); server-nonce machinery unwired for desktop
- DO-016: owner desktop screenshots to %TEMP% unencrypted, sensitive always False, no cleanup
- AP-003: bcc agent-path approvals never consumed (only owner-direct browser/terminal features call consume)
- AP-004: apprentice approval gate trusts forgeable model-supplied `_approved_digest` (engine.py:391-395; sha256 of plan-visible fields, no server secret)
- AP-005: approver identity = client-supplied string (api.py:385-388)
- AP-006: on_approval_decided/resume() resurrect stopped/paused task (engine.py:1142-1156, 290-305)
- AP-007: mission pause vs tick race → new tasks enqueued after pause (missions.py:137-159)
- AP-008: bossman-core V2 runner has NO stop/pause for running tasks (runner.py:368-439)
- AP-009: apprentice WAIT_APPROVAL memory-only → restart loses waiting task + stranded nonce
- AP-010: bcc resume = model transcript replay, not LAST_VERIFIED_STATE (V3 journal stack does meet criterion)
- AP-011: finalize gate verifies only DECLARED effects; task w/o required_effects completes as NOT_REQUIRED (finalize.py:70-72)
- AP-012: agent permission changes emit NO audit event (api.py:714-724)
- VS-002: no BLOCKED_FFMPEG state; ffmpeg absent → jobs accepted, persisted `queued`, stuck forever (video_factory)
- VS-003/004/005: in-memory job queue vs durable job.json → restart orphans queued jobs; QueueFull leaves zombie; no API resume/retry path
- VS-006: export verification shallow (probe only, no decode/duration-vs-request); no final export stage in this tree
- WD-003/004: PI/CDATA dropped; naive style parser corrupts values containing ";"
- WD-005/006/007/008/010: non-atomic saves; project id reuse after delete leaks viewport state; 2000-char sanity check; 900ms debounce loses edits on tab close; no delete-project UI button
- V5-002: fleet_memory_reservations NEVER deleted (no DELETE anywhere) → unbounded leak
- V5-003: lease release() not ownership/fence-checked (leases.py:95-96)
- V5-004: promotion trusts caller-supplied owner_approved/rollback_tested booleans; holdout_isolated defaults True (cache_intelligence.py:196-198)
- V5-005: only production promotion path (skill_evaluation) has NO holdout/canary, N=5
- V5-006: rollback declared, never executed/rehearsed
- V5-007: canary absent from worktree (branch-only)
- V5-008..V5-011: evidence TTL dead code; signed evidence replayable (no nonce registry); mission status transitions not CAS-guarded; evidence validation first-error-only
- LG-001: learning_guard promotion pipeline UNWIRED in production (only holdout exclusion wired)
- LG-002: no rollback-on-degradation executor; LG-003: holdout "salt" is constant-prefix hash; LG-004: trainer writes bypass store lock
- SEC-002: plaintext mainnet vault password in untracked .env (history-leaked value); SEC-003: committed masked key label + user id + spend data (docs/acceptance/HAILUO_RESCUE_api_log.jsonl); SEC-004: CI secret scanner blind spots (untracked .env unscanned, unquoted-password pattern miss, DICT_HINT self-skip)
- CI-001..005: duplicated full core suite; missing permissions:/concurrency: in 4-5 workflows; raw-SHA fetch pin; solana-safety overlap
- RH-002: 14 root status .md files tracked; RH-003: untracked stale full-repo copies (.audit-work, .checkpoint-push-b7aaf43) re-materialize SEC-001 password on disk
- DEP-001: solana_volume_suite deps unpinned, outside dependabot/pip-audit
- PERF-001: /api/apps p95 9577ms (cache-expiry probe of 9 neighbor apps sync in request path, apps.py:175-196); /api/testing/events p50 276ms (sync 32MB-max file read, testing_period.py:198); /api/system tail 396ms (sync nvidia-smi subprocess blocks event loop, metrics.py:57,115 + api.py:588); /api/tasks N+1 (api.py:751-754); static assets no Cache-Control (ETag only)
- PERF-002 (info): no /health endpoint (see live finding above)

## DASHBOARD RESPONSIVENESS (V6 perf, live N=10/endpoint, 0 errors, authed GETs)
p50/p95 ms: identity 13/43 · system 45/396 · activity 54/66 · agents 21/269 · tasks 73/135 · agentmap 76/85 · missions 64/244 · resources 70/140 · models 60/87 · providers 55/314 · **apps 78/9578** · skills 65/143 · terminal/sessions 55/264 · testing/events **276/495** · snapshots 53/58 · capabilities 148/377
Frontend total JS+CSS ≈107KB — excellent. Targets (UI feedback p95≤100ms) met for 14/16 endpoints; failures concentrated in sync-subprocess/sync-IO in async routes.

## POSITIVE CONFIRMATIONS (evidence-backed)
- Key security solid: Fernet vault, mask-only exposure, password UX, key never in URL/logs/GET (registry.py:34-37), connect error paths classify 401/422/502 correctly, tests assert no key echo
- Catalog cache semantics correct (TTL, stale markers, dedupe, outage keeps cache + 503)
- Pinned model → engine routing proven in tests (name=remote_id, adapter receives exact remote id; fallback visible via events)
- bcc DENY-before-approval order correct (engine.py:941-951); approval digest re-verified on resume; bcc approvals one-shot + consumed for owner-direct flows
- bcc Stop real gate: queued runs zeroed, claim() filters stopped tasks, tested (test_engine_stop.py)
- finalize gate server-side + structural test forbids other completed writes
- learning_guard promotion gates fail-closed (security snapshot pairs, holdout quarantine, scope binding); LearningStore CAS + file-lock + forged-history rejection
- Benchmark engine provenance hashing + honest NO-GO release gate
- fs containment, terminal hard-deny list, host-shell always ASK, redaction on 4 surfaces (logs/exceptions/events/episodes)
- CI: timeouts everywhere, non-overlapping suites, browser-skip=red, fail-closed security gate, secret scan inside zips

## STATUS
- OPENROUTER_CONNECT=PASS (live, via API-flow; UI click-path blocked by OR-004 trap for owner)
- MODEL_IDENTITY_PROOF=IN_PROGRESS (pin + route test → CHECKPOINT-2)
- SCENARIOS A/B/C=NOT_RUN (core/Postgres down: Docker daemon off — environment blocker)
- VIDEO_STUDIO=NOT_RUN (not in branch, VS-001); WEB_DESIGNER=static-audited (WD-001/002 live-code confirmed)
- V5_OBJECTIVES=NOT_PROVEN (V5-001); HERMES=DOES_NOT_EXIST; FUSION=DOES_NOT_EXIST
- OPEN_P0=7 OPEN_P1=18 OPEN_P2=40+
- FREEZE_STATUS=NOT_READY_FOR_FREEZE (SEC-001 alone is disqualifying)

Next: CHECKPOINT-2 = OR-002 live catalog verification, GLM 5.3 pin, route-test message, model identity proof.
