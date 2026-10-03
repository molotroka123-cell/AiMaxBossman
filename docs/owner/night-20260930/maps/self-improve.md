# Bossman autonomy and self-improvement loop: what exists and what is missing (branch `claude/bossman-1.9-owner-bugtest-20260930`)

This was a read-only mapping. I changed no files and ran no tests. "Tests exist" below means test files are present; I did not run them.

**Summary.** The repo has two separate self-improvement engines and several memory layers:
- **Engine 1: `bcc.autonomy`**, driven by the Claude and Codex CLIs. It has a constitution, goals, a lease, a policy layer (Policy/HandBroker), dual review, staging, a metrics gate and a release panel. It is the closest match to the owner's loop. It **has never run for real** on this branch: `--real` is marked OWNER_REQUIRED.
- **Engine 2: the `bossman_v3.self_improvement.loop` evolution loop.** It is exposed at `/api/evolution/*` and runs OBSERVE→…→LEARN. It writes only candidate refs in its own bare repo, but it **automatically publishes VERIFIED coding recipes into retrieval memory**.

Neither engine honours a single shared kill switch and budget. `bcc.autonomy` honours **neither** the global STOP nor any cost ledger.

---

## 1. `command-center/bcc/autonomy/*`: data model, state machine, storage

### 1.1 Types (`types.py`, contract `docs/autonomy/AUTONOMY_CONTRACT.md`)
- `Budget(max_minutes, max_agent_turns, max_cost_usd=0.0)`
- `Goal(goal_id, problem, desired_result, constraints, acceptance_tests, budget, risk_tier, target_metric, protected_metrics)`
  - Goal scope and rollback live inside `constraints` as `path:<glob>` and `rollback:<text>`. They are read by `workers.goal_scope()` (line 136) and `goal_rollback()` (line 142).
- `HandRequest(goal_id, requested_by∈{claude,codex,jev,jeff}, action, target, arguments, expected_evidence, risk_class, timeout_s, rollback)`
- `HandResult(request_hash, ok, exit_code, started_at, finished_at, artifacts{name:sha256}, refused_reason)`
- `Review(goal_id, reviewer, sha, diff_sha256, verdict∈{APPROVE,REQUEST_CHANGES,REJECT}, notes, evidence_sha256)`
- `RiskTier`: `docs_tests | prompts_models | memory_keys_telegram_services | critical_runtime`
- Wire schemas are in `schemas/autonomy/{task,action,review,result}.schema.json` and are validated by `schemas.validate()`.

### 1.2 State machine (`goals.py` `GoalStore`, `TRANSITIONS` at line 28)
```
PROPOSED→PLANNED→BUILDING→TESTING→CLAUDE_REVIEW→CODEX_REVIEW→STAGING→USER_APPROVAL→DEPLOYED→MONITORING→COMPLETE|ROLLED_BACK
any→BLOCKED; BLOCKED→blocked_from|PLANNED; ROLLED_BACK→PLANNED; revision (→BUILDING) invalidates approvals/staging/tests
```
Guards in `_guard()` (line 194):
- TESTING needs a candidate.
- CLAUDE_REVIEW needs tests that passed and are bound to the current (sha, diff).
- CODEX_REVIEW needs Claude's APPROVE.
- STAGING needs both approvals.
- USER_APPROVAL needs approvals plus `staging_passed`.
- DEPLOYED from STAGING only when `risk_tier=="docs_tests"` and `auto_tier:true`. Otherwise it needs `apply_decided` and `owner_confirmed:true`.
- COMPLETE needs gate `"ACCEPT"`, or `outcome=="rejected_by_user"` when coming from USER_APPROVAL.

Other `GoalStore` behaviour:
- `record_review` (line 366) refuses a reviewer approving its own unreviewed candidate.
- `charge()` (line 437), `sweep_budgets()` (line 448) and `budget_exceeded()` (line 458) exist but are **never called in production code**.
- Storage: `<root>/goals/<ID>.json`, written atomically with a `version` counter, under `goals/.lock`.

### 1.3 Cycle driver (`cycle.py`)
- `AutonomyCycle.run_goal` / `resume` / `user_decision` / `run_queue`.
- `_drive()` (line 290) dispatches `_s_<state>`.
- Context is persisted after each step to `<work_root>/<GOAL>/cycle.json`.

What each state does:
- **`_s_proposed`** (line 340): checks `constitution_verify()`, the budget, acceptance tests plus target metric, the scope and the rollback. It then calls `route_writer` and records a metrics baseline.
- **`_s_building`**: runs a `WriterSession`.
- **`_s_testing`**: sends `run_tests` suites `acceptance` and `protected` through the HandBroker, then computes `evidence_sha256`.
- **`_review`**: runs a `ReviewerSession`, then `parse_review` and `ReviewGate`.
- **`_s_staging`**: runs `StagingRunner.run(sha, ("health","ready",*STAGING_PROBES))`. It auto-deploys only if `risk_tier=="docs_tests"` and `level>=3`; otherwise it moves to USER_APPROVAL.
- **`_s_monitoring`**: calls `metrics_gate.decide`, then either finishes COMPLETE or sends a `rollback` hand.
- Budget check `_check_budget` (line 332) enforces **turns and minutes only**, not cost.
- `_capture_trace` writes `trace.json` with a plain `write_text` (not atomic). Only its hash goes to the journal (`trace_captured`).
- CLI: `python -m bcc.autonomy.cycle --goal JEFF-0042 [--real] [--level N]`. Without `--real` it is a dry run that prints the goal and route. With `--real` it uses `_real_deps()` and **clamps the level: `level = min(args.level, 2)` (line 711)**.

### 1.4 Workers (`workers.py`), review (`review.py`), policy (`policy.py`), hands (`hands.py`)
**`WriterSession.run`** (line 489):
- Acquires `EngineeringLease` and creates an isolated clone with `rws.create_workspace` on branch `autonomy/<goal>/w<turn>-<agent>`.
- Checks the tamper fingerprint, lets Bossman commit, then checks changed paths against `goal_scope` via `path_allowed`.
- Hand requests are parsed strictly (`parse_hand_requests`, at most 8 per turn).
- Claude writer flags: `--permission-mode acceptEdits`, tools `Read,Edit,Write,Glob,Grep`, deny `Bash,WebFetch,WebSearch`. Codex writer flags: `--sandbox workspace-write`.
- **Gap:** changed paths are **not** checked against `policy.PROTECTED_GLOBS`, and the path-derived tier (`classify_path`) is **not** compared with `goal.risk_tier`. Both checks exist only inside `Policy.check` for `write_file` hands, and CLI writers edit files directly.

**`ReviewGate`** (`review.py`): reviews are bound to the (sha, diff_sha256, evidence_sha256) tuple. Any change invalidates both approvals. REJECT, a malformed verdict or a timeout leads to BLOCKED. Reaching `max_disagreements` leads to BLOCKED.

**`Policy.check`** (`policy.py`, line 270): evaluation order is constitution → schema → goal → `ALWAYS_USER` → unknown action (fail closed) → level (`ACTIONS`: `apply_candidate`=L3, `rollback`=L4) → Jeff read-only → high risk → budget → protected paths and tier → argv allowlist.

`PROTECTED_GLOBS` (line 92) covers only:
- `docs/constitution/*`
- `bcc/autonomy/{constitution,policy,goals,hands,journal,types}.py`
- `schemas/autonomy/*`
- `.github/workflows/*`
- `.git`

It does **not** cover:
- `cycle.py`, `review.py`, `workers.py`, `lease.py`, `staging.py`, `probes.py`, `metrics_gate.py`, `service.py`, `skills.py`, `planner.py`
- `features/autonomy.py`, `features/control_plane.py`, `ui/pages/autonomy.js`
- `learning/lessons.py` (the poison filter), `pit/runtime.py` (`claim_origin`)
- `bossman_shared/fable_budget.py`

**`HandBroker.execute`** (`hands.py`, line 194): journals the request, calls `policy.prepare/check`, requires the lease for writing kinds, runs the executor, hashes the artifacts and journals the result.
- `build_default_broker()` (line 361) **caps the level at `MAX_LEVEL="L2"`** (line 253).
- **`SubprocessExecutor` does not implement `apply_candidate` or `rollback`**: it returns `"the default executor does not perform …"`. Automatic apply and rollback are therefore a stub even above L2.

### 1.5 Other modules
- **`staging.py`**: `StagingRunner.run` uses a free non-live port (not 8800/8801), a temp data dir that must not overlap the owner data, and always tears down. `CloneLauncher` starts `python -m bcc` and waits on `/health/live`.
- **`probes.py`**: `STAGING_PROBES = telegram_fake, memory, jeff_identity, voice_status, model_routing, acceptance`. Each runs inside the staged checkout and fails closed.
- **`metrics_gate.decide`**: returns ACCEPT, REJECT or ROLLBACK. Missing values fail closed.
- **`journal.py`**: `<root>/journal.jsonl` plus `journal.head` plus `journal.lock`. It is hash-chained and secret-redacted before hashing, and has `verify()`.
- **`manifest.py`**: freeze manifest linked to the journal head.
- **`lease.py`**: one global writer via `<root>/engineering.lease` and its lock. Stale leases are taken over and orphan processes are killed.
- **`planner.py`**:
  - `rule_candidates` builds candidates from metrics, `FAILED …::test` log lines and a backlog.
  - `Planner.plan` asks remote Nemotron (only after a live 0/0 price and ≥10B-parameter check), then Jev, then a local model; the rule planner is the fallback.
  - Web research is passed as `UNTRUSTED_WEB_RESEARCH`.
  - **Not wired**: `Planner(` is only instantiated in tests.
- **`skills.py`**:
  - `capture_trace` redacts immediately.
  - `compile_skill` enforces 5 conditions plus an origin check that rejects web, chat, quoted and model_output origins.
  - `SkillStore` keeps `<root>/skills/<name>/v<N>.json`, with revoke, confidence and expiry, and `retrieve`.
  - **Not wired**: the cycle never calls `compile_skill`, `add` or `retrieve`. Skill writes are not journaled and not atomic (tmp plus replace, no fsync). There is no poison filter on text fields.
- **`savings.py`**: `<root>/savings/ledger.jsonl`, a record of writer turns and tokens. It is not a spend cap.
- **`identity_task.py`**: JEFF-0042 goal (`jeff_0042_goal()` at line 113), 20 RU+EN prompts, `run_redteam`, `detect_leak`.
- **`service.py`**:
  - `AutonomyService` provides `status()`, `list_goals()`, `goal_view()`, `apply()` (records the decision and returns commands; it **never merges**), `confirm_released()`, `reject()` and `revise()`.
  - `DEFAULT_LEVEL="L2"` and `DEFAULT_TARGET_BRANCH="release/bossman-owner"`.
  - `level()` reads `<root>/level.json`. **Nothing writes that file, and nothing except `status()` uses it.**
- **`cli.py`** (`bossman autonomy …`, routed from `terminal_cli/cli.py:316`): `status`, `goals [--state]`, `journal verify`, `constitution status|pin`. `pin` requires a TTY, no agent environment markers (`AGENT_ENV_MARKERS`), a pin path outside the repo, and the user typing the first 12 hex characters.

### 1.6 Files and environment variables
**Root**: `BOSSMAN_AUTONOMY_ROOT` or `<BCC_DATA_DIR>/autonomy`. It contains:
- `journal.*`
- `goals/`
- `engineering.lease*`
- `level.json`
- `cycles/<GOAL>/{cycle.json,trace.json,w*-a*/{worktree,manifest.json,…},r*-<agent>-a*/}`
- `reports/<GOAL>/<suite>-<sha12>.xml`
- `skills/`
- `savings/`

**Pin**: `%LOCALAPPDATA%\Bossman\autonomy\constitution.sha256`, or `$XDG_DATA_HOME/Bossman/autonomy/…`.

**Environment variables**:
- `BOSSMAN_AUTONOMY_JEFF_MODEL`: without it, the real metrics probe returns None and the goal is BLOCKED at PROPOSED.
- `BOSSMAN_AUTONOMY_JEFF_ENDPOINT` (default `http://127.0.0.1:11434/v1`)
- `OPENROUTER_API_KEY` (Nemotron writer route)

### 1.7 API (`features/autonomy.py`, auto-discovered, owner token)
| Method + path | Body / query | Response |
|---|---|---|
| GET `/api/autonomy/status` | – | `{loop:"READY"\|"BLOCKED", reason, constitution{status,ok,sha,pinned_sha,reason,path,pin_path}, level, lease(no token), goals{state:n}, goals_total, journal{ok,entries,head,reason}}` |
| GET `/api/autonomy/goals` | – | `{items:[{goal_id,problem,state,risk_tier,target_metric,blocked_reason,outcome,sha,updated_at,approvals[]}]}` |
| GET `/api/autonomy/goals/{id}` | – | summary + `record`, `evidence`(≤300 journal entries), `actions{apply,confirm,reject,revise:{allowed,reason}}`, `approvals_valid`, `staging_passed`, `commands?` |
| POST `/api/autonomy/goals/{id}/apply` | `{sha:^[0-9a-f]{40}([0-9a-f]{24})?$, diff_sha256:64hex, note≤2000}` | `{goal, commands{release[],rollback[],expect_head,target_branch}, next}` |
| POST `…/confirm` | same | `{goal}` (moves to DEPLOYED) |
| POST `…/reject`, `…/revise` | `{note}` | `{goal}` |
| GET `/api/autonomy/journal?limit=1..1000&goal_id=` | – | `{items}` |
| GET `/api/autonomy/journal/verify` | – | `{ok,entries,head,reason,bad_seq}` |

Errors: 404 for GoalNotFound, 409 for ReleaseRefused/TransitionError, 400 for GoalError.

**Missing endpoints**: create goal, plan, run or resume cycle, stop, and level.

**SSE**: `ui/pages/autonomy.js:149` listens for `autonomy.*` events, but **nothing emits any `autonomy.*` event**. That subscription is dead.

**UI** (`ui/pages/autonomy.js`): status pills, goal table, release panel (Apply, Confirm release, Revise, Reject), evidence table.

**Related surfaces**:
- `features/v15_autonomy.py`: `/api/v15/autonomy/status|plan`, read-only, `authoritative:false`.
- `features/objectives.py:41`: `STANDING_AUTONOMY_ENABLED = False` (a code constant).

---

## 2. The other learning and self-improvement code

### 2.1 Evolution loop
Implemented in `bossman-core/bossman_v3/self_improvement/loop.py` and exposed by `command-center/bcc/features/evolution.py`.

**Phases**: `OBSERVE, SELECT, ATTEMPT, VERIFY, ACCEPT, LEARN, CHECKPOINT`.

**Controls**: `<data>/evolution/campaign/{STOP,PAUSE,loop.lease.json,loop-state.json}`.

**Budgets** (`LoopConfig`, line 79):
- `max_cycles=3`
- `attempt_minutes=40`
- `total_hours=8`
- `max_usd=2.0`
- `attempt_usd=0.5`
- disk, RSS and RAM limits
- `max_consecutive_failures=3`, which triggers AUTO_PAUSE

**ACCEPT** (`create_candidate`, line 915) only creates `refs/heads/evo/candidate-<cycle>` in `candidates.git`. It never touches stable code. Its `report()` includes `"weights":"WEIGHTS_UNCHANGED"`, `"production_promoted":False` and `"stable_written":False` (around line 1179).

**LEARN** (`phase_learn`, line 950) writes a `LearningStore` record (PARTIAL or FAILED_EXPERIMENT) and, **by default** (`publish_recipes=True`, when `model_kind=="REAL_MODEL"`), calls `POST /api/coding-recipes`. That route (`coding_recipes.save_verified_recipe`) stores the recipe as a **VERIFIED** lesson, with `verifier.independence_class="external_tool"`. The recipe is then retrievable by future tasks with **no owner approval**.

**API**:
- GET `/api/evolution/status`, `/report`
- POST `/start` `{backend:"bossman_coding", source_repo?, cycles 1..200, model?}`; the suite must be `config/evolution/owner-v1.1.json`
- POST `/pause`, `/stop`, `/resume`

No feature flag gates `/start`; the owner token is enough.

### 2.2 `learning/` (shared distribution `bossman-shared`)
- **`trace.py` `LearningStore`**: append-only `journal.jsonl` is the authority. `fix_cases.jsonl` (VERIFIED only), `failed_experiments.jsonl` and `history.jsonl` are derived snapshots. Records are **versioned** (`version`, `supersedes_version`, tombstones) and support CAS (`expected_version`). Secrets are redacted and hidden-reasoning fields are forbidden. VERIFIED requires an independent verifier (`INDEPENDENT_CLASSES`), with no self-certification. Retrieval returns VERIFIED only.
- **`lessons.py` `LessonBook`**:
  - `dedup_key()` (line 189) plus an `occurrences` count
  - `Provenance(who, what, when, evidence_refs)`
  - project isolation
  - statuses candidate→verified→withdrawn, quarantined, superseded, expired, degraded
  - **poison filter** `poison_reasons()` (line 143): denylist for override-control, raise-budget, grant-permission, auto-approve, owner-authority, prompt-injection and env-flag-flip patterns; a structural-keys check; and normalised views (NFKC, zero-width stripping, homoglyphs, base64). It runs at write time and again at read time.
- **`lesson_format.py`**: field validation. It states that no field may carry a permission, approval or budget.
- **`retrieval.py`**: `UnifiedRetriever` over notes, facts and lessons (exact match plus BM25). Everything retrieved is labelled DATA, and results are deduplicated.
- **`lifecycle.py`**: BOOT, TASK_START, RESUME, AFTER_VERIFIED_RESULT, CHECKPOINT. Its rule is "memory is evidence, never authority".
- **`backup.py`**: clean or merge restore that never resurrects retired records.

### 2.3 Other gates in `bcc/v2` and `bossman-core`
- **`bcc/v2/memory/facts.py`**: bi-temporal facts (`valid_at/invalid_at/created_at/expired_at/superseded_by`). Records are never UPDATEd in place.
- **`bcc/v2/skill_evaluation.py`**: verdicts PROMOTE, REJECT or HUMAN_REVIEW. PROMOTE switches `skills.current_version_id` behind a canary gate (`bossman_shared/objective_activation.py`). A candidate that widens permissions always goes to HUMAN_REVIEW. This is an **automatic promotion of SKILL.md versions** behind a canary, not behind the owner.
- **`bcc/v2/skill_catalog.py`**: imported skills carry `provenance.json` with a sha256. They start UNVERIFIED, lines that weaken gates are stripped, and critical instructions quarantine the whole skill.
- **`bossman-core/bossman/learning_guard/`**: `PromotionStage` goes CANDIDATE→VALIDATION→SHADOW→VERIFIED→OWNER_PROMOTED. `promote()` requires `owner_approved` plus `RollbackInfo` plus `security_proven`. `MIN_SHADOW_RUNS=20`. `evidence_ledger` makes evidence single-use. `autonomy_trainer` is gated by `BOSSMAN_AUTONOMY_TRAINER_SHADOW` (OFF).
- **`bossman-core/bossman/apprentice/flags.py`**: all flags default OFF, including `BOSSMAN_UNIVERSAL_COMPUTER_APPRENTICE`, `BOSSMAN_SKILL_RECORDING`, `BOSSMAN_SKILL_SHADOW_REPLAY` and `BOSSMAN_SKILL_PROMOTION`.
- **Learning 24/7**: `tools/owner_journeys/learning_supervisor.py` (docs `docs/owner/journeys/LEARNING_247.md`). Failures become `CANDIDATE_QUARANTINED` lessons and nothing is promoted. It honours `<state>/STOP` **and** the owner's `<owner-data-root>/computer/STOP`. The cloud cap `cloud_cap.json` defaults to $0.50/day.

### 2.4 Memory-poisoning gate (autonomy freeze, line C)
- Test: `command-center/tests/test_mandatory_memory_poisoning.py`. Cases cover quoted, negated, third-person, hypothetical, web and model-generated claims, plus opt-in and forwarded messages.
- Implementation: `command-center/bcc/pit/runtime.py`:
  - `claim_origin()` (line 254) returns `quoted | model_generated | web | third_person | hypothetical | negated | ''`.
  - `extract_candidates()` (line 273) mines only sentences where `claim_origin` is empty.
  - Writes go through `HighRecallCollector` → `PersonaVault.append_candidate`, and only when `ConsentState.memory_enabled` is set.
- **Scope:** this covers Jeff persona facts only. It is not applied to autonomy traces, skills or reviewer feedback.

---

## 3. JEFF-0042 plan (`docs/owner/BOSSMAN_JEV_TYPESCRIPT_AUTONOMY_ONE_RUN_PLAN.md`)
`docs/autonomy/OWNER_PLAN_20260929.md` is a copy of this file.

**Long-term goal**: `OOTB-001-700GB-AUTONOMOUS-BOSSMAN`. Milestones: M1 is one complete supervised cycle; M2 is ten bounded cycles with rollback; M3 to M6 cover pilot, benchmarks, grants and financing. Changes to that goal are owner-only.

**Loop**:
1. Jev plans; Nemotron is the planner when it is free and ≥10B.
2. Bossman takes the engineering lease and creates an isolated worktree.
3. One writer CLI edits and commits, then exits; the lease is released.
4. Deterministic tests and the security suite run.
5. Claude then Codex review read-only, with approvals bound to the SHA and diff.
6. The candidate is staged on a separate port with a temp data dir.
7. The user release gate decides.
8. Bossman monitors protected metrics and rolls back automatically on regression.

**Hand protocol**: a JSON envelope; Bossman is the only privileged executor.

**Traces and skills**: a trace becomes a skill only if all 5 conditions hold: acceptance passed, both approvals on the exact hash, redacted, parameters/preconditions/failure detection/rollback explicit, and one staging success. Skills are versioned, scoped, revocable, confidence-decayed and expiring. Chat, quoted, web or model text never becomes a fact or skill.

**Authority**: money, external communication, credentials, destructive actions, constitution changes and production release stay user-gated. Unknown state fails closed.

**Stop conditions**: budget exhausted, repeated disagreement, failing protected tests, ambiguous state, rollback not guaranteed, or a user-gated action is next. All of them lead to BLOCKED and resume from persisted state.

**First real task**: fix Jeff's identity leak. Scope is `bcc/pit/public_guard.py`, `pit/j2/safety.py` and `tests/test_identity_*.py`; tier `prompts_models`; budget 90 minutes, 10 turns, $0; metric `identity_redteam.leaks==0`.

**Status**: `docs/owner/CONTINUATION_PROMPT_20260929.md:32` says "первый реальный цикл JEFF-0042 … только по разрешению владельца". Not run.

**Defects found while mapping (by reading code, not by running it):**
1. **The cycle can never reach COMPLETE for JEFF-0042.** `_real_deps.probe` (`cycle.py:641`) returns only `{identity_redteam.leaks}`. The goal's protected metrics (`task_success, refusals_and_hallucinations, role_stability, latency_ms, red_team_pass_rate`) are therefore missing, and `metrics_gate._delta` treats missing values as a failure. After deploy, MONITORING rejects, the `rollback` hand needs L4 (refused at L2), and the goal ends BLOCKED with "rollback not guaranteed".
2. **Some acceptance tests are never executed.** The `cmd:` test and the measurable-statement test are ignored. `RoutedPolicy.prepare` runs only `pytest:` ids.
3. **The metrics gate never runs before the owner decides.** It is not applied to the staged candidate before USER_APPROVAL; it only runs after deploy.

---

## 4. How retrieval learning vs weight training is labelled
- `CLAIMS_NOT_PROVEN.md` has no learning or weights entry.
- `KNOWN_LIMITATIONS.md:17` says: "COACHING_PIPELINE_TESTED, LOCAL_LEARNING_GAIN_NOT_MEASURED, WEIGHTS_UNCHANGED. No EVO/self-modification is enabled."
- `WEIGHTS_UNCHANGED` / `weights_changed:False` markers appear in:
  - `bossman_v3/self_improvement/loop.py:1179`
  - `tools/self_improve_lab.py:85`
  - `tools/coaching_runner.py:67` ("no fine-tuning, no weight update")
  - `tools/coaching_exam.py`
  - `tools/owner_run_tomorrow.py`
  - `tools/bossman_15_self_improve.py:502`
  - `tools/model_bakeoff.py`
  - `bcc/economy_orchestrator.py:369`
  - `tools/owner_journeys/learning_lab.py:245` (`"fine_tuning":"NOT_ATTEMPTED"`)
- `coding_recipes.py` states that a fix travels "through CONTEXT … never through weights".
- **`bcc/autonomy` carries no such label.** Its skills, traces and outcome have no `weights` or `learning_kind` field.
- Real weight training exists only in offline tools outside every loop:
  - `tools/video_studio/train_adapter.py` (LoRA; "never automatically promoted")
  - `tools/motion_studio/finetune_lora.py` (NOT_TESTED)
  - The promotion predicate `bcc/features/model_foundry_v16.py:promotable()`, which requires separate train and holdout manifest hashes and a rollback model id.

---

## 5. Emergency stop and budget: what autonomy honours

**Global STOP exists**:
- `POST /api/control-plane/stop-all` (`features/control_plane.py:123`). It is also reachable via `bossman stop --all` (`terminal_cli/cli.py:818`), the palette and Telegram `/stop`.
- It first persists `<data_dir>/computer/STOP` (`tools_computer.http_stop`, line 1480; `STOP_FILE` at line 112), then stops these planes: `tasks, terminal, coding, command_bar, browser, evolution, v15_economy, v15_owner_run, pit (<pit_home>/stop.flag), studio`.
- It emits the bus event `owner.stop_all`. Rave subscribes to it.

**`bcc.autonomy` is not among those planes and reads no STOP file.** No code under `bcc/autonomy` checks STOP. The cycle runs as a separate CLI process, so a global STOP does not stop an in-flight writer or reviewer. Only the lease TTL and orphan killing on a later takeover would clean them up.

**Evolution loop**: control-plane writes its campaign `STOP`, which is honoured.

**Jev kill switch**: `BOSSMAN_JEV_KILL_FILE` (default `<data>/jev.disabled`). It affects Jev calls only.

**Budget ledgers**:
- `bossman_shared/fable_budget.py` is a hard $3 Anthropic cap with worst-case reservation. It is not used by autonomy.
- `bcc/mission_budget.py` covers task runs.
- The evolution loop has its own $ reservation.
- `bcc.autonomy` enforces per-goal turns and minutes only. Cost is not charged (`GoalStore.charge` is never called), `sweep_budgets` never runs, and there is no daily or global cap. `PlannerBudget` exists but the planner is not wired.

---

## 6. Stage-by-stage table

| Owner stage | Status | Where |
|---|---|---|
| Task (bounded, measurable, budgeted) | EXISTS / partial intake | `types.Goal`, `goals.validate_goal`, `GoalStore.create`. Only JEFF-0042 is wired in `cycle.main`. No create-goal API or CLI. |
| Execution (isolated, single writer) | EXISTS (OWNER_REQUIRED real CLIs) | `workers.WriterSession.run/_run_locked`, `lease.EngineeringLease`, `hands.HandBroker.execute` |
| Verifiable result | PARTIAL | `cycle._s_testing` (pytest acceptance and protected, JUnit, `evidence_sha256`). `cmd:` and statement tests are not executed. The JEFF-0042 probe lacks the protected metrics. |
| Experience record | PARTIAL | `journal.Journal` (hash chain), `cycle.json`, `skills.capture_trace` → `trace.json` (not atomic). Not written to `LearningStore` or `LessonBook`, so there is no cross-goal memory. |
| Improvement proposal | PARTIAL (not wired) | `planner.rule_candidates` / `Planner.plan` are used in tests only. Backlog and log inputs have no source. |
| Isolated candidate | EXISTS / gap | clone and branch, tamper check, scope check. **No PROTECTED_GLOBS or tier check on changed paths**, and goal scope may name protected files. |
| Evaluation | PARTIAL | `review.ReviewGate` (dual review), `staging.StagingRunner` plus `probes`. The metrics gate runs **only after deploy**, not on the staged candidate. |
| Approval | EXISTS | USER_APPROVAL guards, `service.apply/confirm_released/reject/revise`, `/api/autonomy/goals/{id}/*`, UI release panel |
| Apply | EXISTS as manual only | `service.release_commands` (the owner runs git). The automatic `apply_candidate` executor is **missing**, and the level cap makes it unreachable anyway. |
| Rollback | PARTIAL | Manual `git revert` command list. The automatic `rollback` hand needs L4, which is capped at L2, and has no executor, so the goal ends BLOCKED. |
| Skill / memory from experience | PARTIAL (not wired) | `skills.compile_skill/SkillStore` are not called by the cycle. There is no poison filter or journal on skills. |
| Journal | EXISTS | `autonomy/journal.py`; `LearningStore` journal |
| Memory versions | EXISTS (learning) / partial (autonomy skills) | `LearningStore` version/supersedes; `facts.py` bi-temporal; `SkillStore v<N>.json` (revoke is an in-place rewrite) |
| Dedup | EXISTS (lessons) / MISSING (autonomy skills) | `lessons.dedup_key`, retrieval dedup |
| Knowledge provenance | EXISTS | `lessons.Provenance`, `trace` `verified_by`/evidence, `skill_catalog` `provenance.json`, `SkillSpec.source_trace_hash` |
| Context-poisoning protection | EXISTS in pieces | `pit.runtime.claim_origin`, `lessons.poison_reasons`, `skills.compile_skill` origin check, planner `UNTRUSTED_WEB_RESEARCH`, the TaskManifest feedback banner. Not applied to skill text or reviewer notes. |
| Budget | PARTIAL | turns and minutes only; cost and global caps missing |
| Emergency stop | MISSING for `bcc.autonomy` | global STOP exists but is not consulted |
| Self-change protection (rules, permissions, limits, branch) | PARTIAL | Constitution pin plus `PROTECTED_GLOBS` (incomplete list). `release_commands` accepts any `candidate.target_branch` that matches a regex; canonical branch `release/bossman-owner` is only a default. Level promotion (25 cycles plus the user) is not implemented. |

---

## 7. Flags that currently enable or disable autonomous changes

| Control | Where | Default | Effect |
|---|---|---|---|
| Constitution pin | `constitution.verify()`, pin outside repo | **absent** → BLOCKED | `Policy.check` refuses everything; `_s_proposed` blocks |
| Hard level cap | `hands.MAX_LEVEL="L2"` (line 253), `build_default_broker` | L2 | `apply_candidate` (L3) and `rollback` (L4) refused |
| CLI clamp | `cycle.main`: `min(args.level, 2)` (line 711) | 2 | auto-deploy requires ≥3, so it never happens |
| `CycleConfig.auto_deploy_min_level` | `cycle.py:150` | 3 | only the `docs_tests` tier is eligible |
| `level.json` | `service.level()` | L2 (display only) | not used by the cycle or broker |
| Auto apply executor | `hands.SubprocessExecutor` | not implemented | automatic apply/rollback cannot run |
| `STANDING_AUTONOMY_ENABLED` | `features/objectives.py:41` | False | V5 objectives read-only |
| `BOSSMAN_AUTONOMY_TRAINER_SHADOW` | `learning_guard/autonomy_trainer.py:29` | OFF | trainer records nothing |
| `BOSSMAN_SKILL_PROMOTION` and other apprentice flags | `apprentice/flags.py` | OFF | |
| Evolution loop | `/api/evolution/start` | **no flag** (owner token) | writes `evo/candidate-*` refs only; **publishes VERIFIED recipes** (`LoopConfig.publish_recipes=True`) |
| Canary skill PROMOTE | `v2/skill_evaluation._apply_promotion` | active behind canary | automatic switch of SKILL.md version |

Net result: autonomous **code** apply is OFF: pin required, L2 cap, no executor. Autonomous **memory** promotion is ON in two places: evolution recipes and canary skill PROMOTE.

---

## 8. Minimal design to close the gaps by extending existing modules (autonomous apply stays OFF)

1. **Kill switch.** Add to `bcc/autonomy/service.py`: `stop_reason(data_dir) -> str`, which reads `<data>/computer/STOP` and a new `<data>/autonomy/STOP`, plus `request_stop()` and `clear_stop()`.
   - Add `CycleDeps.stop_check: Callable[[],str]`. `AutonomyCycle._drive` checks it every loop iteration and raises `Blocked("owner STOP")`.
   - `HandBroker.execute` refuses after the request is journaled (`hand.refused`, reason "owner STOP").
   - `WriterSession` and `ReviewerSession` poll during the run and kill the process tree through the lease's registered processes.
   - `features/control_plane._active_owner_work` adds an `"autonomy"` plane (`EngineeringLease.peek()`). `stop_all` writes `autonomy/STOP` and kills the lease processes.
   - `features/autonomy.py` adds POST `/autonomy/stop` and `/autonomy/resume`. `cli.py` adds `bossman autonomy stop|resume`.
2. **Self-change protection.**
   - Extend `policy.PROTECTED_GLOBS` to cover `command-center/bcc/autonomy/**`, `features/{autonomy,control_plane,evolution,coding_recipes}.py`, `ui/pages/autonomy.js`, `learning/{lessons,lesson_format,trace}.py`, `bcc/pit/runtime.py`, `bossman_shared/fable_budget.py` and `config/evolution/*`.
   - In `goals.validate_goal`, reject `path:` globs that overlap the protected list.
   - In `workers.WriterSession._run_locked`, after computing `changed`, flag `violation "protected:"` for protected paths and `"tier:"` when `classify_path(p)` ranks above `goal.risk_tier`.
   - In `service.release_commands` and `goals.set_candidate`, refuse any `target_branch != DEFAULT_TARGET_BRANCH`.
3. **Budget.**
   - In `_s_building` and `_review`, call `self.d.goals.charge(gid, agent_turns=…, cost_usd=…)`.
   - Call `goals.sweep_budgets()` at the top of `_drive`.
   - Add a global daily cap (`<root>/budget.json`: cycles/day, turns/day, usd/day), checked in `_s_proposed` and journaled as `budget.*`.
   - Any paid call reserves through `bossman_shared.fable_budget` before it is made.
4. **Evaluation before approval.**
   - In `_s_staging`, run `metrics_probe` against the staged candidate and call `metrics_gate.decide(before, after, …, deployed=False)`. REJECT leads to BLOCKED. Put the verdict in the USER_APPROVAL evidence and in the release panel view.
   - Fix JEFF-0042: either make `_real_deps.probe` return every protected metric (e.g. `red_team_pass_rate = 1 - leaks/total`, `latency_ms`), or narrow `jeff_0042_goal().protected_metrics` to what is measured.
   - Record `cmd:` and statement acceptance tests as `NOT_EXECUTED` in the evidence instead of silently ignoring them.
5. **Experience → retrieval memory.**
   - `cycle._capture_trace`: write `trace.json` with `journal.atomic_write_bytes`.
   - At terminal states, write a `learning.lessons.LessonBook.save(CoachingEpisode(...))` record with `Provenance(who=writer, evidence_refs=[trace_hash, sha, journal head])`, status `candidate` (UNVERIFIED), and `dedup_key`.
   - Verification (to VERIFIED) happens only through an owner action: new `bossman autonomy lesson verify|withdraw`.
   - Add `"weights":"WEIGHTS_UNCHANGED","learning_kind":"retrieval_context"` to `CycleOutcome.evidence`, `trace.json`, `SkillSpec` and `service.status()`.
6. **Skills.**
   - In `compile_skill`, run `learning.lessons.poison_reasons` over the description, steps, preconditions and rollback.
   - `SkillStore.add` skips when the artifact hash already equals the latest version (dedup).
   - Journal `skill.add/revoke/outcome` to the autonomy journal.
   - Wire compilation only as a *proposal*: the cycle writes `skills/proposed/<hash>.json`, and `add()` requires an owner CLI confirmation.
7. **Proposal intake.** Add `bossman autonomy plan` in `cli.py`.
   - It builds `PlanInputs` from the `identity_task.run_redteam` metric, JUnit failures under `<root>/reports`, `<root>/backlog.json`, and `LessonBook.retrieve` (VERIFIED only).
   - It calls `Planner.plan` and then `GoalStore.create`, leaving the goal in PROPOSED. It never runs the goal.
8. **Keep apply OFF.**
   - Leave `hands.MAX_LEVEL="L2"` and the `cycle.main` clamp unchanged.
   - Do not add an `apply_candidate` executor.
   - Level promotion: add a read-only `service.promotion_eligibility()` that counts COMPLETE goals without ROLLED_BACK or policy refusals (default 25). The actual level change is an interactive TTY command like `pin`, stored outside the repo.
9. **Evolution parity.**
   - Default `LoopConfig.publish_recipes=False`, or post recipes as candidates.
   - Make `EvolutionLoop.stop_requested()` also honour `<data>/computer/STOP`.
10. **UI and events.** `features/autonomy.py` emits `autonomy.goal.decision` and `autonomy.stop` on the bus so the `autonomy.*` subscription in `autonomy.js` actually receives events. The page adds pills for the STOP state, "autonomous apply: OFF (L2 cap)" and `WEIGHTS_UNCHANGED`.