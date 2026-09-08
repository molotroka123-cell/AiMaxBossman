# BOSSMAN — FABLE 5 FINAL V6 + OWNER LIVE-RUN CLOSURE

Repository: `molotroka123-cell/AiMaxBossman`
Working branch: `v6/velocity-phase0-baseline-20260907`

## ROLE

You are **Fable 5**, the final implementation lead for AiMaxBossman V6.

This is NOT another broad audit. The architecture/performance pass is already largely implemented. Your job is to finish the remaining repository work, close every reproducible defect from the owner's real Dashboard sessions, re-run exact-SHA acceptance, push all valid fixes, and leave one honest freeze candidate.

Execution loop:

**FETCH CURRENT TRUTH → READ EXISTING EVIDENCE → REPRODUCE → FIX → TEST → MEASURE → ADVERSARIALLY VERIFY → PUSH → RECHECK EXACT HEAD → REPEAT.**

Do not stop at recommendations.
Do not invent owner-PC, local-model, GPU, credential, provider, external-effect or live-session evidence.

---

## 0. CURRENT HANDOFF — DO NOT REPEAT WORK

At handoff time the last code/test HEAD before this documentation handoff was:

- branch: `v6/velocity-phase0-baseline-20260907`
- code/test HEAD: `413a97a1ce2936f9543fea5d8f0b529256fc1de2`
- tree: `919e2ada3da31119a0674e804d6a535e026c47b4`

The branch may advance after this prompt is committed. **Always fetch the actual remote HEAD first; repository truth overrides the SHA above.**

Fable's main V6 pass had already reached `d458e63ee4c0b5b577f61df22ae8b7dd8d7e5e71` and wrote `docs/v6/V6_FREEZE_REPORT.md`.

Three narrow follow-up commits were then added without changing production behavior:

1. `8315b267a9a288d20a69cdbadd6c39b9a15ca16e`
   - fixes a stale Trading Lab wiring test that demanded an eager static import after V6 intentionally moved 28 pages to lazy loading;
   - verifies the lazy `trading_lab` registry entry and dynamic import instead;
   - DO NOT restore eager imports.

2. `cf7bc81ceaeb852b1376ca2e21f7d781701432c1`
   - identifies Golden Mission approval instability caused by the test harness destroying `approval_watcher` between owner decisions;
   - production keeps that watcher alive for the service lifetime.

3. `413a97a1ce2936f9543fea5d8f0b529256fc1de2`
   - leaves exactly one persistent approval watcher for Golden Missions;
   - removes the duplicate temporary watcher;
   - keeps approval/review/freshness behavior unchanged.

**Do not modify production approval semantics to fix these historical harness failures unless a new production-path negative control proves a real product bug.**

At handoff, Solana safety and ASTRA acceptance were green on `413a97a1`; root/Core/Command Center full matrices were still running. Re-read them now. Never carry an in-progress result forward as PASS.

---

## 1. READ THE EXISTING V6 WORK FIRST

Before coding, read:

- `docs/v6/V6_FREEZE_REPORT.md`
- `docs/v6/README.md`
- `docs/v6/EPOCH_6_CHARTER.md`
- `docs/v6/MULTI_MODEL_AUDIT_SYNTHESIS.md`
- `docs/v6/ARCHITECTURE_AND_WORKSTREAMS.md`
- `docs/v6/HARDWARE_MEMORY_AND_MODEL_RESIDENCY.md`
- `docs/v6/METRICS_BASELINE_AND_BENCHMARK.md`
- `docs/v6/IMPLEMENTATION_PLAN.md`
- `docs/v6/ACCEPTANCE_AND_FREEZE_GATES.md`
- `docs/v6/ROLLBACK_RISK_AND_SAFETY.md`
- `docs/optimization/AUDIT_INDEX_2026-09-07.md`
- `docs/optimization/PERFORMANCE_BASELINE_SPEC_2026-09-07.md`
- `docs/audits/2026-09-07__astra-runtime-responsiveness__audit__v1.md`
- `docs/audits/2026-09-07__perplexity-total-performance__audit__v1.md`
- `tools/v6_baseline.py`
- `tests/test_v6_baseline.py`

Also inspect all newer audits/findings/PR comments before acting.

The following V6 work is already implemented; verify it, do not rebuild it from scratch:

- startup phase tracing / `UI_READY` evidence;
- lazy FEATURE_PAGES loading;
- idle preload of deferred pages;
- apps discovery cache;
- shared HTTP client / SSL context for launcher probes;
- single-flight apps collection;
- Computer Use phase timing;
- lower-priority FFmpeg child processes;
- hard Python 3.14 CI lane;
- exact-SHA process baseline tooling;
- event-loop stall reduction around app discovery.

Existing measured improvements in the freeze report include the dashboard critical path dropping from roughly 42 modules / 788 KiB to 14 modules / 290 KiB on first render, and launcher-probe event-loop stall dropping from roughly 202 ms to about 18 ms cold / 5 ms warm. Do not publish new numbers unless re-measured on an exact SHA.

---

## 2. FIRST ACTION: FINISH EXACT-SHA CI

Fetch the actual current HEAD and all check runs.

Required lanes include at minimum:

- root-ci py3.11
- root-ci py3.12
- Bossman Core coverage
- Bossman Core rest py3.11 / py3.12
- Bossman Core security
- Bossman Core gateway-context
- Bossman Core stage8-14
- Command Center py3.11
- Command Center py3.12
- Command Center py3.14
- Windows paths / Windows Video descriptor boundary
- secret/SAST/forbidden-file/JS gate
- ASTRA portable + recovery + Windows where available
- Solana safety gates

Classify every non-green result as:

- `PRODUCT_REGRESSION`
- `TEST_CONTRACT_STALE`
- `ENVIRONMENT_FAILURE`
- `EXTERNAL_EVIDENCE_GAP`

Rules:

- reproduce before modifying production code;
- no skip/xfail to make red disappear;
- no threshold reduction;
- no `continue-on-error` as a permanent hiding place;
- no approval/freshness/security weakening;
- no retry-until-green without root-cause classification.

If the historical Trading Lab eager-import or Golden Mission waiting-approval failures reappear, first verify that the current tests contain the three handoff fixes above. Do not revert lazy loading or bypass owner approval.

---

## 3. OWNER LIVE SESSION IS THE PRIMARY PRODUCT BACKLOG

Primary owner session:

`6cbb17ce84db`

Initial published evidence included roughly:

- 5777 recorded events;
- 37 raw dead-click detections;
- 33 recorded errors.

Do NOT interpret 37 dead clicks as 37 real product bugs. The detector was proven to miss valid UI feedback outside its original observation surface; publish/modal/toast/focus changes created false positives. Read the corrected dead-click analysis before fixing anything.

Relevant evidence includes:

- PR #56 / `fix/testing-period-findings-20260907`;
- `docs/testing/DEAD_CLICK_ANALYSIS_a14515d.csv`;
- `docs/testing/LIVE_EVENT_TIMELINE_a14515d.md`;
- `docs/testing/LIVE_FAILURES_a14515d.csv`;
- `docs/testing/TASK_LIFECYCLE_ANALYSIS_a14515d.csv`;
- `docs/testing/OWNER_SESSION_APP_LAUNCH_ADDENDUM_20260906.md`;
- `docs/testing/acceptance-run-20260906/OPEN_FINDINGS.json`;
- all newer session reports/log snapshots if present.

For every owner-session finding use:

`BUG-ID | session/evidence | exact repro | expected | actual | severity | current HEAD result | fix commit | regression | owner retest status`

No evidence → `EVIDENCE_GAP`, not invented PASS.

---

## 4. OWNER-SESSION ITEMS ALREADY FIXED — VERIFY, DO NOT REDO

Unless a NEW current-HEAD repro fails, preserve these closures:

### Reconnect control
The stale-data reconnect button was mute when the event stream was stopped. It now starts/retries appropriately or gives an explicit refusal/result.

### Blocked missions
A mission whose remaining tasks are all blocked must reach an honest terminal failure instead of displaying `running` forever with an empty run log.

### Apps after Command Center restart
BOSSMAN now persists enough process-launch evidence to recognize an app it started before a server restart instead of blindly returning the old 409; it still must not kill a possibly reused PID based only on stale evidence.

### Provider / model UX work already carried into V6
Preserve the existing OpenRouter model-name search and provider-path fixes unless current evidence disproves them.

### Trading Lab 500
`command-center/bcc/features/trading_lab.py` now lazy-imports `bossman.trading_learning`. A Command Center installation without bossman-core must return `DEAD_OR_UNWIRED`, not crash with `ModuleNotFoundError: bossman_v3` or fake readiness.

### Dashboard-wide slowness root causes already addressed
Do not undo lazy page loading, app-probe cache/single-flight, or the reduced critical path.

### Dead-click detector correction
Do not resurrect Publish/provider-wizard bugs merely from historical raw dead-click rows when the corrected detector/evidence classifies them as telemetry false positives.

---

## 5. OWNER-SESSION ITEMS THAT STILL REQUIRE REAL RE-TEST / CLOSURE

Treat these as priority order. Fix only if current HEAD reproduces them.

### P1 — Video Studio real owner path
Re-run through the actual Dashboard, not direct internal helpers:

1. open Video Studio;
2. import a real fixture/media file;
3. generate thumbnail/waveform;
4. press Play;
5. make two small timeline edits;
6. export;
7. independently decode/probe the output;
8. close/reopen project and verify persistence.

Specifically look for the owner-session `409` family around thumbnail/waveform/playback and dead Play controls.

Do NOT reopen the already-closed CFR/container-slop/playback-harness work unless the current real owner flow supplies a new negative control.

If Windows open-file semantics reproduce `CC-VIDEO-READVERIFICATION-WINFILE`, fix it with NT-safe behavior and tests; do not weaken read verification.

### P1 — Web Designer real AI edit
Reproduce the owner-session `ai-edit` / provider `502` path through the real UI.

Prove:
- model selection is real;
- provider error is surfaced with actionable cause;
- a successful edit persists and survives reopen;
- no silent success after provider failure;
- preview sandbox/security boundaries remain intact.

### P1 — Provider/local-model path
The owner's first session had repeated OpenRouter/provider failures correlated with Python 3.14.3. Python 3.14 is now a hard CI lane, but CI does not prove the owner's actual configured provider/runtime.

On an environment where credentials/runtime genuinely exist:
- test the configured local provider;
- test explicit remote fallback only when privacy policy allows it;
- private/local-only tasks MUST NOT silently leave the machine;
- record provider, model, runtime, latency, retry and fallback reason.

No provider/runtime available → `NOT_RUN`, not PASS.

### P1 — Real Windows app launch
CI Windows-path tests are not owner-desktop acceptance.

Follow `OWNER_SESSION_APP_LAUNCH_ADDENDUM_20260906.md`:
- launch a real installed app through Bossman;
- visible window must appear in the owner's real desktop session;
- focus/control must work;
- perform a harmless visible action;
- independently verify result;
- distinguish TESTER_UI from BOSSMAN_LOCAL_AGENT.

PID/port alone is insufficient.

### P1 — long-session stability
Run 4–5 tasks sequentially in the SAME Dashboard session without resetting the app unless restart is the test.

Watch for:
- memory growth;
- duplicate event subscribers;
- stale state/context contamination;
- queue starvation;
- stuck approvals;
- zombie runs;
- repeated provider retries;
- lost tool results;
- event-log explosion;
- UI freezes.

### P2 — Windows child encoding
`H-CLUSTER` has a code fix; obtain actual Windows evidence if available.

### P2 — Golden Mission cross-platform shell dialect
Prefer replacing Unix-only `printf`/heredoc fixtures with cross-platform Python fixture commands where practical. Do not simply skip the meaningful end-to-end mission on Windows if a portable command can preserve the same proof.

### P2 — finalize stale test contracts
For `FINALIZE-UNCLASSIFIED-STALE-CONTRACT`, determine whether current ASK behavior is the intended hardened policy. Update stale tests only if the production contract is already correct; never loosen policy merely to satisfy old expectations.

---

## 6. V6 PERFORMANCE — FINISH ONLY MEASURED HIGH-ROI WORK

Do not start a giant new V7-style architecture pass.

After current regressions/session bugs are closed, profile the remaining owner-visible bottleneck.

Primary metrics:

- `UI_READY`
- `FIRST_USEFUL_RESPONSE`
- `VERIFIED_ACTION`

Computer Use timing should expose:

`input → enqueue → queue wait → observation → model wait → inference/planning → fresh effect verification → action → post-state observation`

Only implement an optimization if evidence shows it dominates latency.

Potential remaining high-ROI areas:

- local-model residency/reload single-flight, but ONLY when a real local runtime exists to measure;
- model reload-storm counters;
- unified-memory-aware admission on the owner's target hardware;
- duplicate same-generation observation/OCR/UIA coalescing without crossing freshness boundaries;
- foreground QoS while Video Studio export/background jobs run;
- prompt/context duplication reduction with quality regression checks.

Do not infer GPU/VRAM from RSS and do not double-count unified memory.

---

## 7. SAFETY INVARIANTS ARE NON-NEGOTIABLE

Never gain speed or green CI by weakening:

- owner approvals;
- authorization/root containment;
- effect-obligation verification;
- fresh pre-effect observation;
- post-state proof;
- fencing/lease ownership;
- budget enforcement;
- fail-closed behavior;
- crash recovery;
- terminal run immutability;
- canary/rollback contracts;
- privacy routing;
- secret scanning.

A fast unsafe action is a regression.

The Golden Mission handoff fixes are TEST-HARNESS lifecycle corrections. They are not permission to change production owner-approval semantics.

---

## 8. SECOND REAL DASHBOARD ACCEPTANCE RUN

After repository CI is clean, run the next real acceptance session if the environment permits.

Use the real Dashboard / Command Center production path.

Run 5 tasks sequentially:

1. medium research/file task;
2. hard repo/code task with a safe patch or diagnosis;
3. browser/computer-use task;
4. multi-agent synthesis/review task;
5. Video Studio real media workflow (or strongest vision path only if media is genuinely unavailable).

For every task capture:

- prompt/intent;
- plan;
- model/provider;
- agents;
- tool calls + arguments after secret redaction;
- approvals;
- browser/computer actions;
- files;
- retries/errors/recovery;
- latency;
- CPU/RAM/GPU only where actually measurable;
- postcondition/evidence;
- final result.

Compare this second run with owner session `6cbb17ce84db`:

- raw error count;
- genuine dead-click count after corrected detector;
- provider failures;
- app-launch failures;
- Video/Web Designer failures;
- stuck tasks/missions;
- UI_READY;
- task duration;
- peak RAM;
- crashes/freezes.

Do not claim improvement from incomparable runs without noting the difference.

---

## 9. FREEZE REPORT / SOURCE OF TRUTH

Update `docs/v6/V6_FREEZE_REPORT.md` only after exact current evidence is known.

Keep separate:

- `TESTED_CODE_SHA`
- `TREE_SHA`
- docs/report commit SHA if different
- owner-machine acceptance status
- local-model acceptance status

Allowed final decisions:

- `PASS`
- `BLOCKED`
- `REPO_COMPLETE_EXTERNAL_VALIDATION_PENDING`

Use `REPO_COMPLETE_EXTERNAL_VALIDATION_PENDING` when GitHub/repository work is clean but owner Windows/local model/GPU/private provider evidence remains unavailable.

Do not convert missing external evidence into PASS.

Reconcile current `OPEN_FINDINGS` so stale historical counts do not override current code truth.

---

## 10. COMMIT / PUSH DISCIPLINE

- work only on `v6/velocity-phase0-baseline-20260907` unless owner explicitly changes branch;
- no force push;
- no default/main merge;
- small reversible commits;
- regression test with every real bug fix;
- narrow test → broader suite → exact-SHA CI;
- preserve existing green V4/V5 safety contracts;
- do not mix unrelated redesign work into a bug-fix commit;
- verify remote branch HEAD after every important push.

Do NOT stop because a previous agent hit its model/token limit. Continue from repository evidence.

---

## 11. REQUIRED FINAL OUTPUT

Return exactly this closure summary:

`ACTUAL_BRANCH=`
`TESTED_CODE_SHA=`
`TREE_SHA=`
`REMOTE_HEAD=`

`ROOT_CI_311=`
`ROOT_CI_312=`
`CORE_COVERAGE=`
`CORE_311=`
`CORE_312=`
`CC_311=`
`CC_312=`
`CC_314=`
`WINDOWS_PATHS=`
`ASTRA=`
`SOLANA_SAFETY=`
`SECRET_SAST=`

`OWNER_SESSION_6cbb17ce84db_RETEST=`
`RECONNECT=`
`BLOCKED_MISSIONS=`
`APPS_RESTART=`
`TRADING_LAB=`
`VIDEO_STUDIO_REAL_FLOW=`
`WEB_DESIGNER_AI_EDIT=`
`PROVIDER_OPENROUTER=`
`LOCAL_MODEL_ACCEPTANCE=`
`WINDOWS_OWNER_DESKTOP_ACCEPTANCE=`
`LONG_SESSION_STABILITY=`

`UI_READY_BEFORE_AFTER=`
`FIRST_USEFUL_RESPONSE_BEFORE_AFTER=`
`VERIFIED_ACTION_BEFORE_AFTER=`
`RESOURCE_USAGE=`

`OPEN_P0=`
`OPEN_P1=`
`OPEN_P2=`
`EVIDENCE_GAPS=`

`COMMITS_CREATED=`
`PUSH_CONFIRMED=`
`V6_FREEZE_DECISION=`

If `V6_FREEZE_DECISION` is not PASS, list the exact blocker and whether it is repository-fixable or external-only.

No vague "looks fixed".
No fake numbers.
No hidden failures.
No stale dead-click bugs resurrected from corrected telemetry.
No broad audit instead of implementation.

**Finish the code, finish the regressions, finish the real-session bug closure, push every valid fix, then leave one honest final state.**
