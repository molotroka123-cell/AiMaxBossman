# AiMaxBossman — AUTONOMY TRAINER FIX PASS (Kimi K3)

# MODE: IMPLEMENTATION + LIVE ACCEPTANCE + AUDIT
# PRIORITY: P0 > P1
# REPO: https://github.com/molotroka123-cell/AiMaxBossman
# BRANCH: claude/bossman-control-v03-43igbk
# BASE SHA: 00686399b1c0b0bf9215bbbadd237925e3194c83
#   (if a commit fixing jsonschema deps already exists on the branch, that is
#    the P0-CI item DONE — verify, do not redo)
#
# THIS IS NOT A PLANNING TASK.
# DO NOT ONLY WRITE DOCUMENTATION.
# IMPLEMENT, TEST, RUN, FIX, RE-RUN, COMMIT, PUSH.
#
# Existing architecture is valuable. DO NOT rewrite working subsystems unless
# a concrete failing test proves that a rewrite is required.

You are the implementation agent for the AiMaxBossman Autonomy Trainer stage.
Objective: convert the existing (partially simulated) Apprentice stack into one
integrated self-learning computer-agent runtime.

TARGET USER EXPERIENCE: owner gives an arbitrary computer task ->
1. Apprentice attempts it itself (semantic observe/act, NOT X/Y coordinates).
2. It independently verifies the result.
3. It records a sanitized factual episode (no chain-of-thought, no secrets).
4. Repeated verified episodes become a reusable skill (Learning Guard gates).
5. On a similar task later it retrieves and tries the learned skill first.
6. If it cannot solve a hard coding problem, it may use Claude Code as an
   EXTERNAL UNTRUSTED TEACHER; teacher output is independently verified.
7. Only a generalized VERIFIED method may enter Apprentice memory.
8. Dangerous external side effects remain owner-gated (approval + nonce).
9. Restart/crash must not erase critical safety state.

============================================================
0. REPO STATE AT BASE SHA (what already exists — extend, do not duplicate)
============================================================

bossman-core/bossman/apprentice/:
  engine.py            UniversalComputerApprentice state machine + DefaultVerifier
  models.py            ApprenticeTask/State, PlanStep, SemanticTarget, RiskClass, TRANSITIONS
  recording.py         EpisodeRecorder (sanitized factual episodes -> schemas/*.schema.json)
  skills.py            skill recording/retrieval (semantic anchors, no coordinates)
  teacher.py           ProblemBundle, untrusted teacher observation, PatchVerifier,
                       acceptance-test binding, sanctions, strategy extraction
  sanctions.py         reliability score, technical sanctions, circuit breaker
  outreach.py          OutreachPackage, ApprovalDecision, side-effect idempotency
  guards.py            ApprovalRegistry, SideEffectLedger, step_digest
  flags.py             feature flags — ALL live capabilities OFF by default
  errors.py            typed errors (ApprenticeDisabled, FlagDisabled,
                       CoordinateTargetForbidden, ...)

Tests: bossman-core/tests/test_apprentice_{core,learning,recording,sanctions,teacher,outreach}.py
Fixtures: bossman-core/tests/fixtures/apprentice/{sim.py,teacher_sim.py} (deterministic sims)
Schemas: schemas/apprentice_action_record.schema.json, schemas/apprentice_skill.schema.json,
         schemas/autonomy_candidate.schema.json
CI: .github/workflows/bossman-core-ci.yml (matrix py3.11+3.12, groups security /
    gateway-context / stage8-14 / rest), .github/workflows/root-ci.yml,
    .github/workflows/command-center-ci.yml. Job "compile" runs compileall +
    tools/ci_secret_scan.py + advisory pip-audit/bandit.

Known environment notes (Windows dev machine):
- system python is 3.14; Python 3.11/3.12 NOT preinstalled — install via
  `winget install astral-sh.uv` then `uv python install 3.11 3.12`, create venvs
  from those interpreters, `pip install -e "./bossman-core[dev,resource]"`.
- CI runs the "rest" group as:
  `python -m pytest -q --timeout=120 --timeout-method=signal <paths+ignores per workflow>`

============================================================
1. P0 — FIX CI FIRST
============================================================

Audited failure: bossman-core/tests/test_apprentice_core.py imports
`import jsonschema`, but bossman-core/pyproject.toml did not declare it.
The usage is TEST-ONLY (validates records against schemas/*.schema.json;
runtime code deliberately avoids a schema validator).

Fix: add `jsonschema>=4.20` to `[project.optional-dependencies].dev` ONLY
(keep the explanatory comment). Do NOT add to runtime dependencies. Do not
add any other packages.

Then:
- run the FULL rest group under Python 3.11 and 3.12 exactly as CI does;
- run the other three groups (security, gateway-context, stage8-14) — zero new
  failures allowed;
- `python -m compileall -q bossman-core/bossman` = PASS;
- `python tools/ci_secret_scan.py` = PASS;
- Apprentice tests must actually EXECUTE (not collected-then-skipped).

Acceptance:
  P0-CI-001 py3.11 rest suite = PASS        P0-CI-002 py3.12 rest suite = PASS
  P0-CI-003 apprentice tests executed       P0-CI-004 compileall = PASS
  P0-CI-005 secret scan = PASS

Do not continue to final completion while CI is red.

============================================================
2. P0 — REAL CLAUDE CODE TEACHER BRIDGE
============================================================

Existing teacher architecture (bossman-core/bossman/apprentice/teacher.py) is
good: ProblemBundle, untrusted observation, independent PatchVerifier,
acceptance-test binding, sanctions, strategy extraction. THE MISSING PART IS
LIVE EXECUTION.

Implement (names may differ if a better location exists):
  bossman-core/bossman/apprentice/claude_code_client.py
  bossman-core/bossman/apprentice/live_workspace.py

A. Concrete Claude Code client — capable of invoking the real locally
installed Claude Code process when enabled. It must:
  - receive ONLY the sanitized bounded ProblemBundle;
  - run Claude Code in an explicitly scoped workspace;
  - capture typed visible artifacts: opened_files, symbols, commands,
    root_cause, patch/diff, test claims, attempt errors, artifact refs;
  - NEVER request or persist hidden reasoning;
  - treat all returned data as UNTRUSTED_TEACHER_OUTPUT;
  - enforce timeout, process kill, cloud/budget governor, max attempts;
  - pass stdout/stderr through sanitization only; never leak secrets into
    the ProblemBundle; never allow push/deploy permissions.

B. Real workspace compatible with PatchVerifier:
  snapshot() / restore(token) / read(path) / write(path, text) / apply(patch) /
  run_tests(ids)
  - support real unified diff (the format the Claude adapter emits), not only
    full-file replacement;
  - security: allowed path scope; reject ../ traversal; reject symlink escape;
    acceptance tests immutable/hash-bound; reject protected security paths per
    existing policy; rollback failed teacher changes; HEAD/environment
    evidence binding.

C. Acceptance:
  CLAUDE-LIVE-001 fixture repo with a genuine bug: apprentice fails first ->
    typed fallback reason -> real Claude Code invocation -> teacher patch ->
    PatchVerifier applies/verifies independently -> acceptance+regression+secret
    tests pass -> strategy stored UNVERIFIED/SHADOW per existing rules.
  CLAUDE-LIVE-002 second analogous bug of the same class: apprentice retrieves
    the learned strategy and tries the method ITSELF BEFORE calling Claude.
    teacher_calls(second) < teacher_calls(first) (ideal: == 0).
  CLAUDE-LIVE-003 deliberately invalid patch -> rejected, workspace rolled
    back, evidence retained, reliability penalty, max ONE corrective retry,
    no learned strategy.
  CLAUDE-LIVE-004 teacher modifies an acceptance test -> ACCEPTANCE_TAMPERING
    -> reject, restore tests, learning blocked, reliability penalty,
    adversarial regression record, stop.
  CLAUDE-LIVE-005 teacher attempts security weakening -> QUARANTINED -> patch
    not accepted, no learning, reliability penalty, explicit report.

Integration tests for the client/workspace must run against REAL subprocess /
filesystem boundaries without credentials (e.g. a stub `claude` executable on
PATH or an injectable command runner producing a canned valid/invalid diff).
Mark true live-Claude runs `@pytest.mark.live` (opt-in).

============================================================
3. P0 — DURABLE SAFETY STATE
============================================================

Process-local state is NOT acceptable for live external effects. Persist at
minimum: consumed approval nonces; side_effect_ids; in-progress side-effect
claims; completed effect receipts; outreach recipient cooldowns; blocked
recipients; sent history; teacher reliability scores; adversarial teacher
records; circuit-breaker history; pending WAIT_APPROVAL state.

Use the existing project persistence layer where possible (Postgres/Redis
already present). SQLite is acceptable as local fail-safe fallback if cleanly
abstracted. DO NOT build five persistence implementations. One small
interface, e.g. DurableSafetyStore, with atomic ops:
  claim_side_effect() / complete_side_effect() / abandon_side_effect()
  consume_nonce_once() / get_cooldown() / set_cooldown()
  block_recipient() / record_teacher_outcome()
  save_pending_approval() / resume_pending_approval()
Requirements: atomic; concurrency-safe; restart-safe; process-safe; idempotent;
no secret values stored; corruption/failure must fail CLOSED for external
effects.

Acceptance:
  DURABLE-001 side effect claimed in process A; kill; process B: same
    side_effect_id MUST remain blocked.
  DURABLE-002 nonce consumed; restart; replay of same nonce MUST fail.
  DURABLE-003 recipient contacted; restart; cooldown MUST remain active.
  DURABLE-004 teacher reliability penalty; restart; MUST remain.
  DURABLE-005 WAIT_APPROVAL; restart; resume with matching fresh valid
    approval; continue exactly once.
Mark restart tests `@pytest.mark.restart`; they must prove behavior by
destroying and recreating the store/process, not by re-reading memory.

============================================================
4. P0 — THREE INDEPENDENT END-TO-END ACCEPTANCE CASES
============================================================

These are THREE DIFFERENT test cases. DO NOT combine into one fake mega-demo.

CASE A — REAL COMPUTER APPRENTICE (Higgsfield-type flow)
  If Higgsfield credentials/session exist in env, use Higgsfield. If not,
  DO NOT fake it: build the real browser/computer adapter, use a real local
  browser target, and mark the Higgsfield live run BLOCKED_BY_ENVIRONMENT with
  an exact one-command manual trigger for the owner machine.
  Flow: open -> observe -> semantic UI controls -> act -> wait -> re-observe ->
  independently verify -> sanitized episode -> generalize candidate skill ->
  shadow/verify/promote via Learning Guard -> similar task reuses learned
  semantic workflow before replanning from zero. Semantic anchors only.
  Acceptance: APP-LIVE-001, APP-LIVE-002, APP-LIVE-003 (003 succeeds only if
  the second run demonstrably reuses learned state; record action counts/times
  for both runs).

CASE B — CLAUDE CODE BUG LEARNING: the CLAUDE-LIVE sequence above.

CASE C — GOOGLE MAPS -> SITE AUDIT -> DEMO -> OWNER APPROVAL
  1. open/search Google Maps or an equivalent public business discovery source;
  2-4. pick a legitimate public business, use PUBLIC info only, inspect its
     public website;
  5-6. identify ONE verifiable website problem and verify it independently;
  7. create a simple demo/improvement artifact;
  8-10. build OutreachPackage, show owner (business, reason, evidence, current
     site, demo, proposed message, recipient) and STOP at WAIT_APPROVAL;
  11. only after owner approval may transport.send() be called.
  NO automated mass sending. NO repeated unsolicited messages. NO scraping
  private personal data. Default live acceptance may terminate at WAIT_APPROVAL.
  Acceptance:
    OUTREACH-LIVE-001 discovery with real public source
    OUTREACH-LIVE-002 issue independently verified
    OUTREACH-LIVE-003 demo created
    OUTREACH-LIVE-004 package shown, WAIT_APPROVAL reached
    OUTREACH-LIVE-005 wrong/replayed/expired/mismatched approval DENIED
    OUTREACH-LIVE-006 restart preserves pending package + replay protection

============================================================
5. P1 — ONE TOP-LEVEL LEARNING LOOP
============================================================

One mandatory integration path (facade, e.g. ApprenticeRuntime, or extend an
existing top-level runtime; keep existing DI for tests):
  TASK -> retrieve VERIFIED/READY skill -> try skill first, else plan ->
  OBSERVE -> ACT -> VERIFY -> EpisodeRecorder -> factual episode / negative
  lesson -> independent verification -> generalization -> shadow/A-B/promotion
  gates -> READY skill -> future tasks retrieve it; on repeated failure ->
  recovery; on typed coding fallback -> Claude teacher -> independent
  verification -> generalized verified strategy -> future self-attempt first.

Acceptance:
  LEARNING-LOOP-001 success auto-creates factual episode
  LEARNING-LOOP-002 failure auto-creates failure evidence / negative lesson
  LEARNING-LOOP-003 READY compatible skill attempted before generic planning
  LEARNING-LOOP-004 DEGRADED skill not blindly replayed
  LEARNING-LOOP-005 teacher strategy attempted on analogous bug before another
    teacher call

============================================================
6. P1 — OWNER APPROVAL AUTHENTICATION
============================================================

ApprovalDecision already checks digest/scope/TTL/nonce — insufficient alone.
Do NOT trust approver="human:owner" as a string from a model. Bind approval to
an authenticated owner session / trusted local control channel, reusing
existing auth/perimeter primitives:
  server creates approval challenge -> owner acts via authenticated owner
  interface -> server issues signed or server-stored token binding owner
  identity + task id + action digest + scope + expiry + nonce -> models cannot
  self-issue -> token consumed once -> restart safe.

Acceptance:
  OWNER-AUTH-001 model-generated fake "human:owner" approval -> DENY
  OWNER-AUTH-002 real authenticated owner approval -> ALLOW
  OWNER-AUTH-003 recipient/content modified after approval -> DENY
  OWNER-AUTH-004 replayed approval -> DENY

============================================================
7. P1 — SECURITY / RELEASE HARDENING
============================================================

A. HIGH/CRITICAL dependency/SAST finding on relevant production code must not
   silently allow a release candidate to be called green (review the
   continue-on-error jobs; make blocking where practical or document why not).
B. Branch protection: if repo API permissions allow, configure required checks
   for the default branch. Otherwise do NOT pretend it was done — create exact
   owner instructions + required check names, record status
   EXTERNAL_OWNER_ACTION_REQUIRED.
C. Feature flags: all risky new live capabilities OFF by default.
D. Re-run: repo secret scan; learning-record secret scan; teacher-bundle
   secret tests; logs; evidence export.

============================================================
8. REQUIRED BENCHMARK
============================================================

For at least TWO learned task classes record FIRST vs SECOND run: planning
calls, actions, recoveries, teacher calls, wall time, tokens/cost where
measurable, verified success. Second run must beat the first on at least one
metric with NO degradation in verified success or security.
Create docs/autonomy/AUTONOMY_LEARNING_BENCHMARK.md.

============================================================
9. TEST STRUCTURE
============================================================

Categories: unit, integration, live_optional, restart, security, teacher,
skill, outreach. Suggested markers @pytest.mark.live / .restart / .security.
Ordinary CI stays deterministic; real external-service tests opt-in. Adapters
MUST have integration tests against real subprocess/filesystem/browser
boundaries where no credentials are required. No "function exists" tests —
tests must prove behavior.

============================================================
10. FAILURE REPORTING
============================================================

For every acceptance result:
{ "acceptance_id": "...", "status": "PASS | FAIL | BLOCKED_BY_ENVIRONMENT",
  "reason": "...", "evidence": [...], "head_sha": "...", "environment": "...",
  "next_action": "..." }
BLOCKED_BY_ENVIRONMENT is acceptable ONLY for genuinely unavailable external
credentials/app sessions/hardware — NOT for missing adapter code, broken local
tests, missing persistence/integration/restart handling.

============================================================
11. FINAL REPORT
============================================================

Create AUTONOMY_TRAINER_FINAL_COMPLETION_REPORT.md with: START_SHA, FINAL_SHA,
commits created, files changed, architecture changes, exact test totals,
Python 3.11 result, Python 3.12 result, security results, secret scan result,
live acceptance table, restart acceptance table, Claude teacher acceptance
table, Higgsfield/real-browser acceptance table, outreach acceptance table,
learned-skill benchmark, remaining environment-blocked items, known risks,
rollback procedure, flags that remain OFF by default.

Then print the final terminal summary:
START_SHA= / FINAL_SHA= / CORE_PY311= / CORE_PY312= / ROOT_TESTS= / SECURITY= /
SECRET_SCAN= / APP_LIVE= / CLAUDE_LIVE= / OUTREACH_LIVE= / DURABLE_RESTART= /
LEARNING_REUSE= / NEW_FAILURES= / BLOCKED_BY_ENVIRONMENT= /
READY_FOR_OWNER_AUDIT=YES|NO
If ANY P0 item is failing: READY_FOR_OWNER_AUDIT=NO

============================================================
12. NON-NEGOTIABLE RULES
============================================================

DO NOT: replace working Apprentice architecture with a new framework; weaken
existing security checks; disable tests; xfail/skip failing acceptance tests;
make acceptance tests easier; let Claude Code verify its own work; trust
teacher claims such as "VERIFIED"; store chain-of-thought/hidden reasoning;
store secrets/credentials in learning records; use hard-coded screen
coordinates as the primary learned UI mechanism; silently claim live
functionality when only a simulator was used; claim SUCCESS without fresh
external evidence; learn from self-reported success; let a restart reset
approval/replay protection; send external outreach without explicit owner
approval; mass-message businesses; change protected security code merely to
make teacher patches easier.

Prefer extending bossman/apprentice/*, bossman/learning_guard/*,
bossman/computer_operator/*, existing gateway/persistence/security primitives
over creating parallel duplicate architectures. All new high-risk
functionality stays behind feature flags until acceptance passes.

DO NOT answer with a plan. START BY:
1. inspect current HEAD; 2. reproduce failing CI; 3. fix P0-CI; 4. run tests;
5. continue through remaining P0 items; 6. only then P1; 7. run the full audit
at the end. If an implementation choice is ambiguous: choose the smallest
change that preserves existing architecture and maximizes real verifiable
behavior.
