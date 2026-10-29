# Codex Harness Review — Hole #4

**Date:** 2026-10-29  
**Review status:** Static review complete; tests and owner-PC live proof not run.

## Scope and revision identity

- Read `C:\Users\asd\Bossman\handoff\HARNESS_AGENT_TRIO_TASK_20261029_RU.md`.
- Audited Codex worktree `wt-codex-exec-20261028`, branch `codex/exec-swarm-20261028`, HEAD `ee06a8e2e82a360b33758b9fb4a3612a571929cd` (clean at inspection).
- Wrote this report in Argon worktree `wt-bossman-20-argon`, HEAD `6977fab21da4d780a7af2c0afaca5539d8e80768`.
- The two requested files differ between worktrees. I checked the destination versions of the spend path and `pc.run_command` call site as well: the destination adds shared cancellation-audit handling in `tools_cloud.py` and a PowerShell AST guard in `pc_agent_policy_cmds.py`; neither change adds spend reconciliation or Job Object handling. The relevant `terminal_control.py`, `test_run_command_job_object.py`, and `test_win_job.py` blobs match across the two SHAs. This is a focused review of the checked paths, not a blanket equivalence claim for the two trees.

Line references use Codex-source line numbers unless the section names a destination-only path.

## Findings

### H4-A — No owner reconciliation for unknown paid spend

**Severity:** Medium (paid-cloud availability)  
**Confidence:** High; code-path review.  
**Status:** Confirmed missing recovery path; tests not run.

`tools_cloud.py:_t_ask` records paid cancellations, provider exceptions, and missing actual usage as `status="billing_unknown"` with `cost_usd=0` (`tools_cloud.py:510–527`). `budget_state` blocks paid budget for the current UTC day when it sees such a row (`tools_cloud.py:183–185, 234–247`). No `reconcile_unknown_spend_by_owner` handler or equivalent owner resolution for this cloud-audit state was found in the reviewed `bcc` and test paths.

The lockout duration needs a distinction: a well-formed `billing_unknown` row blocks paid calls for that UTC day and then falls out of `today_rows` the next UTC day. A corrupt or unreadable audit is more persistent: `_read_audit` parses the complete journal before date filtering (`tools_cloud.py:187–207`), and `budget_state` returns zero remaining on any read/validation error (`:236–242`), so one bad historical row can keep paid spend blocked on later days until the journal is repaired. A free ready route may still be selected when paid budget is zero.

Existing coverage asserts the fail-closed behavior for an unknown charge, malformed/unreadable audit, and missing first-use audit (`test_exec_swarm_regressions.py:61–86`). It does not cover owner reconciliation or a negative control proving that a non-owner or unsupported “not charged” decision cannot unlock spend.

**Recommendation:** Keep the fail-closed default. Add an owner-authenticated, append-only reconciliation event that identifies the original audit record (stable record ID or hash), records the owner’s decision and supporting provider usage evidence, and is idempotent. Do not erase or rewrite the original event. Keep the budget locked until the unknown amount is resolved; give corrupted-journal recovery its own auditable repair path. Add tests for owner success, non-owner denial, invalid/missing evidence, duplicate reconciliation, next-UTC-day behavior, and corrupt-journal recovery.

### H4-B — `pc.run_command` can leave descendants after normal completion

**Severity:** High (process lifecycle containment)  
**Confidence:** High for the source path; Windows runtime reproduction not run.  
**Status:** Confirmed cleanup gap by source/API semantics.

`pc_agent_policy_cmds.py:_classify_run_command` classifies argv and policy; it does not create or clean up a Job Object. The execution path is in `pc_agent.py:2323–2376`: it starts the Windows process suspended, binds/resumes it through `terminal_control._win_bind_and_resume`, and calls `_reap_child` on timeout or cancellation (`:2340–2371`). On normal completion, the `finally` block only closes the job handle (`:2373–2376`).

The actual helper `terminal_control._win_job_attach` creates and assigns a Job Object (`terminal_control.py:280–299`) but does not set `JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE`; `_win_job_close` only calls `CloseHandle` (`:308–312`). Microsoft documents that closing the last Job Object handle terminates associated processes only when `JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE` was specified ([Job Objects — Microsoft Learn](https://learn.microsoft.com/en-us/windows/win32/procthread/job-objects)). Therefore, if a command exits successfully after starting a child that closes or redirects the inherited output handles, `communicate()` can finish, the job handle is closed, and that child can remain alive after the step has been removed from `rt.active_procs`.

Coverage exists for explicit tree termination on timeout/cancellation (`test_run_command_job_object.py:26–43`) and for failed Job Object attachment not resuming the root (`test_exec_swarm_regressions.py:27–35`). `test_win_job.py:59–65` verifies kill-on-close for the separate `bcc.security.win_job.JobObject` implementation, which configures that flag; `pc.run_command` uses the distinct `terminal_control` helper. There is no integration test for a successful parent exit with a detached child, so the existing tests do not cover this completion path.

**Recommendation:** Configure the Job Object used by `terminal_control` with `JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE`, or explicitly terminate the job during normal-step cleanup after the root exits. Preserve the existing fail-closed behavior if assignment/resume fails. Add a Windows-only `pc.run_command` integration test whose parent spawns a child with stdout/stderr redirected and exits successfully; assert the child is gone and an unrelated process remains alive.

## Verification limits and product status

- Tests inspected but **NOT_RUN**; no Windows process reproduction or owner-PC live proof was performed.
- Hole #4 is **not closed**: owner spend reconciliation is absent, and the normal-success Job Object cleanup path is uncovered and can leave descendants.
- This report does not claim harness acceptance or advance a North Star ladder level. Terminal Run 1.2 remains an additional control surface for the same Bossman runtime, not a separate product.

