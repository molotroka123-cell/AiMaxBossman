# BOSSMAN — NIGHT V7 CONVERGENCE MASTER RUN

**Repository:** `molotroka123-cell/AiMaxBossman`  
**Working branch:** `night/v7-convergence-20260908`  
**Base:** `28c0aa1f039bb3b4f717aa87bab28e8ca349b9bd`

## Mission

One autonomous overnight convergence run that:

1. closes the five concrete defects from the latest cloud-QA run;
2. closes the P0 review/escalation deadlock and approval/token storm exposed by the 202-event acceptance trace;
3. finishes OpenHands to genuine end-to-end usable status where the current environment permits it;
4. performs the agreed UX consolidation/cosmetic pass without breaking the proven 34/34 UI behavior;
5. advances V7 from Phase-1 contracts into a materially more complete adaptive-reality execution path;
6. proactively improves adjacent weak areas when evidence shows a safe, high-value fix.

This is NOT a passive audit. The run must implement, test, verify, push, and leave an honest final status.

---

## Operating rule: improve beyond the prompt, but never destabilize core

You are explicitly allowed and expected to improve the system beyond the checklist when you discover adjacent defects, duplicated logic, unsafe defaults, unnecessary approvals, wasteful retries, stale contracts, or obvious UX/runtime problems.

However:

- preserve V4-V6 safety/evidence/permission/budget invariants;
- prefer additive/refactoring-compatible changes over destructive rewrites;
- do not weaken approval, evidence, secret, sandbox, or execution boundaries to make tests pass;
- do not replace core architecture wholesale unless a failing invariant proves it necessary;
- keep backward-compatible call paths where practical;
- every non-trivial autonomous improvement must have tests or direct acceptance evidence;
- if a proactive improvement creates regression risk, isolate it behind a feature flag/shadow mode and document it;
- never fabricate live evidence.

Think like an owner-engineer, not a ticket bot.

---

# 0. Establish current truth before touching code

Fetch all relevant branches/commits and compare them before implementation:

- `night/v7-convergence-20260908`
- `v6/velocity-phase0-baseline-20260907`
- `v7/phase1-reality-core-20260908` (`17f1131287774a969e1c68ab2606c51bb9055113`)
- `acceptance/total-local-20260906` (`9c97c73202b149d95e13a3a4595c9bac09175597`)
- `audit/cloud-qa-20260908`
- OpenHands status base `28c0aa1f039bb3b4f717aa87bab28e8ca349b9bd`

Read and use as evidence:

- `ACCEPTANCE_FINAL_20260908.md`
- `tasks-trace.jsonl` (202 events)
- `ui-walkthrough.json`
- `AUDIT_CLOUD_QA_20260908.md`
- relevant OpenHands docs/tests/runtime
- V7 Phase-1 Mission IR / World State / Strategy code

Do not trust report claims if code/tests disagree.

---

# 1. Close B3 — Apps control must work without manual startup magic

Current finding: all Apps enable/disable operations return 409 unless `BOSSMAN_APPS_CONTROL_ENABLED=1` is manually injected and the service restarted.

Required result:

- Apps control behavior is deterministic and owner-visible;
- no hidden environment-variable ritual is required for normal supported operation;
- safe default must be chosen deliberately, not accidentally;
- if policy requires control to be disabled by default, expose an explicit persistent setting/config with clear UX and restart semantics;
- API/UI must distinguish policy-disabled vs runtime error;
- no blanket 409 for valid owner-authorized control once feature is configured;
- add regression tests for enable/disable, disabled policy, invalid app, repeated idempotent request, and restart persistence.

Do not weaken authorization.

---

# 2. Close B4 — unify streaming compatibility across OpenRouter/GLM paths

Current finding: streaming probes fail, including GLM 5.3.

Build one normalized streaming layer for supported OpenRouter-compatible providers/models.

Required:

- one canonical parser/adapter for streaming responses;
- support normal SSE/data frames and provider variants already observed in repository traces;
- correct finish/usage/error handling;
- graceful fallback to non-streaming only when policy allows and model capability says it is supported;
- explicit classification: stream_supported / stream_degraded / stream_failed / provider_failed;
- no infinite retry loop;
- no double token accounting;
- preserve provider/model evidence in run telemetry;
- tests with deterministic recorded fixtures for GLM and at least one Claude/OpenRouter stream.

If multiple duplicate streaming implementations exist, consolidate them.

---

# 3. Close B5 — silent/free model health must be classified honestly

Current finding: `cohere/north-mini-code:free` can return no useful probe response and still pollute routing/health assumptions.

Implement robust model health classification:

- timeout budget;
- first-byte timeout where relevant;
- empty-response detection;
- malformed response detection;
- provider 4xx/5xx distinction;
- transient vs persistent failure state;
- cooldown/backoff;
- fallback candidate selection;
- health confidence and last-success timestamp;
- never call a silent model healthy merely because endpoint discovery succeeded.

Free models may remain usable, but only after evidence-backed health.

Add negative controls for silent, partial, slow, malformed, quota, and recovered models.

---

# 4. Close B2 — build a secure local bridge/relay for cloud QA

Do NOT try to make cloud agents directly access `127.0.0.1`.

Build a controlled relay where:

`cloud QA agent -> authenticated relay contract -> local Bossman bridge -> local action -> sanitized evidence -> cloud QA`

Requirements:

- outbound/local-initiated connection preferred;
- explicit allowlisted QA capabilities;
- short-lived task IDs/nonces;
- replay protection;
- no arbitrary local shell from cloud;
- no direct secret/file-system exfiltration;
- redact credentials/tokens/private paths from evidence;
- rate/size/time limits;
- structured evidence only;
- local owner can disable bridge immediately;
- clear audit trail;
- tests for auth failure, replay, expired task, forbidden action, oversized evidence, secret redaction, disconnect/reconnect.

First supported QA actions should cover Video Studio, Web Designer and Apps smoke/acceptance interactions.

If a full network relay cannot be safely completed in one run, implement the repository-complete local endpoint + signed queue/evidence contract and leave only genuine external transport provisioning as external.

---

# 5. Re-run 3-system QA after B2-B5 fixes

Re-run independent QA for:

- Video Studio
- Web Designer
- Apps

Target behavior:

- cloud QA can request local execution through relay;
- local Bossman performs allowed actions;
- sanitized evidence returns;
- agents finish tasks without manual owner intervention;
- no fake success based only on API 200;
- actual post-state is verified.

Record exact task IDs, model, route, cost, retries, approvals, interventions and final evidence.

---

# 6. P0 — eliminate review/escalation deadlock

Acceptance corpus reproduced `waiting_approval` deadlock four times while the approval queue was empty; only `/stop` escaped.

This is the highest-priority repository-fixable blocker.

Required invariants:

- `waiting_approval` may exist only if a concrete unresolved approval object exists;
- task cannot wait forever on a missing/stale/consumed approval;
- review escalation must have a terminating state machine;
- duplicate/redundant reviews collapse;
- stale approval IDs cannot block mission completion;
- transition to failure/replan/human escalation must be explicit and bounded;
- `/stop` is not the normal recovery mechanism.

Add direct replay tests from the four failing patterns in `tasks-trace.jsonl`.

`ReviewDeadlockRate = 0` is mandatory.

---

# 7. Kill approval storm / approval amplification

Observed: roughly 121 confirmations for a documentation edit.

Implement:

- approval deduplication;
- approval coalescing by authority/effect scope;
- reusable approval lease only where safe and explicitly scoped;
- approval budget per mission;
- repeated identical approval request suppression;
- do not ask again if the exact authorized effect is unchanged and approval remains valid;
- do ask again if effect scope/target/risk materially changes.

Acceptance target for a normal safe documentation edit: **0-1 owner approvals**, unless existing policy requires more for a specific real effect.

Never reduce approval count by silently expanding authority.

---

# 8. Token / loop efficiency guardrails

Observed: ~1.28M tokens for a simple documentation correction.

Add mission-level controls:

- token budget;
- review token budget;
- max review cycles;
- max replan cycles;
- repeated-context detection;
- repeated-identical-call detection;
- cheap deterministic check before expensive model review;
- prefer local/small/cheap route where measured quality is sufficient;
- structured reason when escalating to an expensive model.

Metrics to emit:

- `tokens_per_verified_effect`
- `approvals_per_successful_mission`
- `interventions_per_mission`
- `review_cycles`
- `replans`
- `model_cost_usd`

Do not hard-code arbitrary tiny limits that destroy complex missions; budgets must be configurable and evidence-backed.

---

# 9. Promote the 202-event trace into a golden regression corpus

Do NOT treat `tasks-trace.jsonl` only as fine-tuning data.

Build replay/evaluation tooling around it.

At minimum extract scenarios for:

- 4 approval deadlocks;
- approval storm;
- provider-down negative control;
- successful cloud routed tasks;
- user intervention points;
- repeated review loops.

Create deterministic regressions that fail if these pathologies return.

Keep raw evidence immutable; derived fixtures can be sanitized/minimized.

---

# 10. Finish OpenHands genuinely

Current honest status at `28c0aa1f` explicitly says the sidecar's actual agent execution is still a STUB and live execution is NOT_RUN.

Close this gap.

Required:

- replace `run_openhands_agent()` STUB with real supported OpenHands runtime invocation when package is present;
- keep isolated worktree;
- preserve allowed/protected path enforcement;
- preserve independent Git evidence;
- provider credentials only through secure env/secret path;
- integrate existing OpenRouter configuration where practical;
- timeout/cancel/max retry/budget controls;
- no OpenHands direct push/deploy/mission-complete authority;
- real client E2E test without mocks using deterministic sidecar path;
- if runtime/package and valid provider are present, perform one harmless live coding acceptance;
- if runtime truly cannot be executed in environment, leave `LIVE_OPENHANDS_ACCEPTANCE=NOT_RUN` with exact reason, but do not leave repository-fixable STUB code.

OpenHands is a coding worker, not a second sovereign orchestrator.

---

# 11. UX consolidation / cosmetics — use the agreed 2/6/1 direction

The current UI passed functional sweep but visually became an engineering control panel.

Do NOT remove functionality.

Consolidate information architecture and visuals while preserving behavior.

Goals:

- premium Windows-12 / modern AI-OS feel;
- acrylic/glass depth, calmer hierarchy, less visual noise;
- reduce top-level navigation to roughly 5-7 primary spaces;
- move specialist surfaces under Apps/Workspaces instead of exposing ~30 top-level items;
- remove duplicate entries such as duplicate Control/Pult concepts;
- one primary command input, not several competing command surfaces;
- Home should focus on: intent, current work, decisions needed, active apps, concise system health;
- deep telemetry stays available but moves under System/Resources/Developer areas;
- Video Studio, Web Designer, Trading Lab, Browser, Coding open as first-class workspaces/apps;
- no hidden or deleted capability;
- responsive behavior must remain functional;
- preserve keyboard navigation and existing 34/34 functional routes.

Use the previous 2/6/1 design direction as inspiration, but improve it if you can produce a cleaner, more coherent result.

Run visual/DOM smoke checks after changes.

---

# 12. Continue V7 Phase 1 into Phase 2

Bring forward the existing V7 Phase-1 contracts from `v7/phase1-reality-core-20260908` without losing latest V6/OpenHands/runtime fixes.

Integrate or reimplement safely:

- typed Mission IR;
- World State Graph with freshness/provenance;
- deterministic strategy candidates;
- shadow utility router.

Then advance V7 with:

### Reality Compiler
Owner intent -> validated Mission IR.
Do not grant authority during compilation.

### Observation adapters
Feed fresh evidence into World State Graph from at least:
- Git/repository state;
- process/system state;
- task/mission state;
- provider/model health;
- app/QA post-state.

### Strategy-changing recovery
Implement bounded strategy switching rather than blind retry.
Example ladder:
`direct tool/API -> alternate compatible path -> local relay/UI -> alternate provider/model -> human escalation`

### Shadow decision telemetry
Record recommendation, alternatives, expected utility terms, chosen production path, actual outcome and regret signal.
Shadow mode remains non-authoritative until evidence is sufficient.

Use the current P0/approval/token failures as V7 benchmark cases.

---

# 13. Proactive improvements allowed

After mandatory items are green, spend remaining effort on the highest-value repository-fixable defects you encounter.

Good candidates:

- duplicated router/provider code;
- ambiguous task state transitions;
- weak telemetry provenance;
- stale settings/config semantics;
- avoidable owner friction;
- excessive model calls;
- fragile Windows paths;
- obvious Video/Web/Apps post-state verification gaps;
- resource/memory admission inconsistencies;
- missing tests around recently changed boundaries.

Do not add random features simply to increase commit count.

Every proactive change must answer: what user-visible/reliability problem does this solve?

---

# 14. Testing / acceptance

At minimum run:

- focused tests for every fix;
- approval/review state-machine tests;
- golden trace replay;
- provider streaming/model-health tests;
- Apps control tests;
- local relay security tests;
- OpenHands worktree/client/E2E tests;
- V7 reality core tests;
- relevant Bossman Core regression suite;
- relevant Command Center/UI tests;
- secret scan;
- exact-head CI where available.

Do not call P0 fixed from unit tests alone if a deterministic replay exists.

---

# 15. Success metrics

Mandatory targets:

- `ReviewDeadlockRate = 0`
- documentation-edit approval target = `0-1` unless policy proves otherwise
- no uncontrolled million-token loop for simple tasks
- Apps control requires no undocumented manual startup ritual
- silent free model is not classified healthy
- GLM/OpenRouter streaming has a canonical working/fallback path
- cloud QA can invoke allowed local QA through sanitized relay contract
- Video/Web/Apps 3-system QA completes without manual owner rescue
- OpenHands has no repository-fixable STUB execution gap
- V7 Reality Compiler + observation + recovery path exists and is tested
- core safety/evidence invariants remain intact
- visual hierarchy is substantially cleaner without route/function loss

---

# 16. Git discipline

Work only on:

`night/v7-convergence-20260908`

Commit in logical slices. Do not force-push unrelated branches. Preserve acceptance evidence branches.

Before final push:

- run secret scan;
- ensure no API key/token appears in code, fixtures, logs or reports;
- verify branch HEAD contains all intended integration work.

---

# 17. Final report format

Return:

```text
FINAL_SHA =
COMMITS_CREATED =

B2_LOCAL_RELAY = PASS/FAIL/PARTIAL
B3_APPS_CONTROL = PASS/FAIL
B4_STREAMING = PASS/FAIL
B5_MODEL_HEALTH = PASS/FAIL
THREE_SYSTEM_QA = PASS/FAIL/NOT_RUN

REVIEW_DEADLOCK = PASS/FAIL
APPROVAL_COALESCING = PASS/FAIL
TOKEN_LOOP_GUARDS = PASS/FAIL
GOLDEN_TRACE_REPLAY = PASS/FAIL

OPENHANDS_REPO = PASS/FAIL
OPENHANDS_REAL_E2E = PASS/FAIL/NOT_RUN
OPENHANDS_LIVE = PASS/FAIL/NOT_RUN

V7_REALITY_COMPILER = PASS/FAIL
V7_WORLD_STATE = PASS/FAIL
V7_STRATEGY_RECOVERY = PASS/FAIL
V7_SHADOW_TELEMETRY = PASS/FAIL

UX_CONSOLIDATION = PASS/FAIL
FUNCTIONAL_UI_REGRESSION = PASS/FAIL

APPROVALS_DOC_EDIT =
TOKENS_DOC_EDIT =
REVIEW_DEADLOCK_RATE =
MANUAL_INTERVENTIONS_3_SYSTEM_QA =

CORE_REGRESSION = PASS/FAIL
CI = PASS/FAIL/PENDING
SECRET_SCAN = PASS/FAIL

PROACTIVE_IMPROVEMENTS =
OPEN_REPO_P0 =
OPEN_REPO_P1 =
EXTERNAL_ONLY_BLOCKERS =

FINAL_VERDICT = READY / READY_WITH_EXTERNAL_EVIDENCE_PENDING / NOT_READY
```

Do not claim READY while any repository-fixable P0 remains.
