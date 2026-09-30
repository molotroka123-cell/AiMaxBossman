# Agent 05: Organization / Treasury / executor admission

Mission: BOSS-FINAL-REALITY-CLOSURE-010. Base: a14515d. Windows, Python 3.12,
original repository `.venv-ci312`, explicit worktree PYTHONPATH. No cloud calls,
mainnet calls, or spend. Findings here are scoped evidence, not final-SHA attestation.

## Live failure and reproduction

Latest authoritative session 0ded40f7389f has 2788 events (one export, not multiple runs).
At 2026-09-05T17:34:55.583Z task.created task_id=10 has agent_id=null and title
`Доложи состояние миссии`; at .591Z task.queued run_id=9; at .797Z run.failed;
at .802Z task.failed reports no selected agent. No task.started event occurs for run 9.

Root cause: `ui/pages/mission_console.js::commandBlock` computes canRun from any
enabled agent plus presence of models, but POST /api/tasks omits agent_id.
`api.py::create_task` accepts null and `TaskEngine.enqueue` previously inserted
a queued run unconditionally. `_run` discovered missing agent only after claim
and used `_fail_now`. Organization, marketplace and Model Broker were never
reached on this direct V2 path; they cannot repair an omitted executor binding.
Organization's separate `_agent_factory/_bind` resolves its selected profile
to a V2 row before constructing execution bindings.

Six fresh API/SQLite regressions failed before implementation: owner command,
manual run/retry/resume, disabled executor, and executor removal after enqueue.

## Change

Central engine admission checks selected executor existence/enabled state.
Unavailable executor persists task status `blocked`, meta.reason_code
`BLOCKED_CAPABILITY_UNAVAILABLE`, and human-readable meta.blocked_reason;
no new run is created. API run/retry/resume returns ok=false, status=blocked,
run_id=null, code and reason. Task GET exposes the persisted reason. Existing
queued work whose executor disappears blocks before task.started/model call;
the run update preserves fencing. Re-enabled executor can be retried and stale
admission metadata clears. No automatic agent/cloud escalation was introduced.
UI binding and response presentation are assigned to agent09, not modified here.

## Fresh checks

* `command-center/tests/test_executor_admission.py`: 6 passed. Exact owner text
  blocked without run; valid re-enabled executor completes with one deterministic
  adapter call; removed executor makes zero calls. Initial isolated pass emitted
  one aiosqlite worker-thread event-loop-closed warning, not counted as clean runtime.
* CC targeted batch: executor admission, API, queue/retry, scheduler, engine stop,
  organization feature, Astra remediation CC: 49 passed, 1 failed in 16.04s.
* Failure `test_engine_stop::test_stop_leaves_no_connection_behind`: cancellation
  leaves one checked-out SQLite connection. Differential run against original
  repository (without this fix) also FAILED identically; isolated agent05 rerun
  PASSED. Classification: PRE_EXISTING_PLATFORM_FAILURE, intermittent. Remains
  OPEN; a passing rerun does not close it. Lead notified for tooling specialist.
* Core organization core/E2E/planner and v3 Astra P1: 51 passed in 8.53s.
* Core Astra remediation `-k test_o00`: 29 passed, 51 deselected in 1.53s.
  Rechecks O001 private/cloud, O002 collision transaction rollback, O003 negative/
  NaN/infinite resources, O004 mandatory risk/reviewer veto, O005/O006 atomic
  reserve/restart/capacity release, O007 scope ownership. These remain fixed in
  tested scenarios; no reopening from stale TZ descriptions.

The E2E cases include independent reviewer, unavailable lead, budget exhaustion,
restart without duplicate effect, and false success forcing parent failure.
No full suite or live provider attestation is claimed. Adapter calls are test
substitutes; API, SQLite admission and state reads are real runtime paths.

## Handoff / open boundaries

Lead notified of separate `features/missions.py::_tick` parent truth issue:
failed/stopped children counted as done, then parent completion requested.
Agent02 owns this finalization boundary; no conflicting edit made here.
Agent09 has exact omitted-agent UI root cause and blocked payload contract.
Lead owns final integration, broad regression, final-SHA evidence and push.
