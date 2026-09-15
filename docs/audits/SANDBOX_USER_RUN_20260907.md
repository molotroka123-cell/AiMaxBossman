# AiMaxBossman — unprivileged sandbox user-run audit (2026-09-07)

## Scope and identity

This was an actual local Command Center run in an isolated Linux sandbox as the
unprivileged `nobody` user, not a static-only review and not owner-Windows
acceptance. The source archive came from the GitHub Actions artifact for PR #37
candidate `0413aa5228dcadaf74a9d2ca41657a06f6b11b8f`. Before publishing fixes the
three modified production blobs (`command-center/bcc/engine.py`, `api.py`,
`providers.py`) were rechecked against the then-current PR #37 line and were
unchanged through `0ad86d172c39fe5e4cb40deae2b0b26abcfe1c29`, so the
reproductions still apply to the current integration line.

The sandbox had no owner credentials, no cloud keys and no real external side
effects. A local fake OpenAI-compatible endpoint was used to control timing and
malformed responses. The app itself ran as a normal user with a separate HOME,
SQLite database and data directory.

## Confirmed runtime findings

### LIVE-P0-01 — Stop was not sticky across Resume

**Reproduction:** stop an actively running task, wait until both task and run are
`stopped`, then `POST /api/tasks/{id}/resume`.

**Before:** HTTP 200, a fresh run was created and the stopped task returned to
`running`.

**Root cause:** `TaskEngine.resume()` did not require `task.status == paused` and
reconstructed a run from the last checkpoint whenever no active run existed.

**Fix:** lifecycle transitions are state checked; Resume accepts only `paused`.
Stopped/cancelled/completed/failed tasks return `409 TASK_STATE_CONFLICT`.

### LIVE-P1-02 — Pause→Resume could duplicate an in-flight model call

**Reproduction:** pause while `adapter.chat()` is blocked, then resume before the
first call returns.

**Before:** the live run was rewritten to `queued`; another worker could claim it
and a second identical inference reached the provider. Fencing prevented the
second tool dispatch in the tested path, but provider cost and compute were
already duplicated and only one inference was reflected in normal run usage.

**Fix:** if the paused run is still `leased/running`, Resume changes only the task
projection back to `running`; it never rewrites the run or lease. If pause has
already parked the run as `queued`, the same run is resumed. No second run is
created.

### LIVE-P1-03 — Retry and Run had single-flight TOCTOU races

**Reproduction A:** Retry while a model call is still running.

**Before:** a second run was created.

**Reproduction B:** two concurrent `POST /run` requests for the same draft task.

**Before:** both callers could observe "no active run" and both insert a run.

**Fix:** `enqueue(... only_if_idle=True)` performs the active-run check beside the
INSERT under the same SQLite `BEGIN IMMEDIATE` / row lock. Manual Run and Retry
use this engine-level gate. One caller succeeds; the loser receives HTTP 409.

### LIVE-P1-04 — owner Stop/Pause could be undone by crash recovery

**Reproduction:** construct the real crash window: task projection already
`stopped` or `paused`, associated run still `running` with an expired lease, then
run `recover()`.

**Before:** generic lease recovery set both run and task back to `queued`.

**Fix:** persisted owner state outranks lease recovery. Stopped/cancelled parent
finalizes the stale run as stopped. Paused parent parks the stale run as queued
but leaves task paused until an explicit Resume. Existing terminal parent states
are never reactivated.

### LIVE-P1-05 — late Stop rewrote completed history

**Reproduction:** complete a task normally, then send Stop.

**Before:** task status became `stopped` while its only run stayed `completed`.

**Fix:** Stop is idempotent for stopped/cancelled, but completed/failed outcomes
are immutable and reject late Stop with HTTP 409.

### LIVE-P1-06 — terminal review veto left an orphan queued run

**Reproduction:** a `gate_completion` result of `FAIL`, `requeue=false`,
`status=failed` (the Action Contract uses this shape).

**Before:** task became `failed`, but run stayed `queued` with no `finished_at`.
The worker would never claim it because the parent task was no longer runnable.
Retry then accumulated more run rows behind the orphan.

**Fix:** a terminal gate verdict closes both projections through `_finish()`.
Only genuine human-wait statuses remain parked as queued checkpoints.

### LIVE-P1-07 — failed run text leaked into aggregate task.result

**Reproduction:** terminal veto after the model produced an answer.

**Before:** `/api/tasks/{id}` selected the last non-empty `run.result`, regardless
of run status, so a failed/refused answer could appear as the task's successful
aggregate result.

**Fix:** aggregate `task.result` is derived only from `completed` runs. Failed run
text remains available in raw run history for forensics.

### LIVE-P1-08 — malformed HTTP-200 provider JSON escaped the provider boundary

**Reproduction:** OpenAI-compatible endpoint returns HTTP 200 with HTML/truncated
JSON.

**Before:** `resp.json()` raised outside `ProviderError`; the worker emitted a
generic error but the run could remain live until lease recovery, hiding the
actual protocol failure.

**Fix:** provider response decoding is typed. Invalid JSON or wrong top-level
shape becomes `ProviderError(kind="protocol")`; model-list payloads must contain
a list. The same fail-closed decoder is used for OpenAI-compatible and Anthropic
JSON boundaries.

### LIVE-P1-09 — deleting an executing agent breaks provenance (OPEN)

**Reproduction:** delete the agent while its task is running.

**Observed:** the run can still finish, but `tasks.agent_id` is nulled by the
foreign-key policy. The run retains the model alias but not an immutable snapshot
of agent identity/system prompt/tool grants/permissions used for execution.

**Why not patched here:** a correct fix needs a durable run-level execution
identity snapshot (and migration/compatibility tests), not a superficial "refuse
delete forever" rule. This remains an explicit release/audit finding for the
integrator.

## Validation of the prepared patch

Sandbox-only dependency note: the container image lacked `aiosqlite`; official
MIT `aiosqlite 0.22.1` source was placed only on temporary `PYTHONPATH`. It is
not part of this patch.

Focused results after the fixes:

- `tests/test_sandbox_user_run_regressions.py`: **10 passed**
- engine Stop / queue retry / persistence / worker pool / providers: **20 passed**
- fencing regressions: **5 passed**
- API regressions: **7 passed**
- selected Action Contract terminal/restart regressions: **2 passed**
- `py_compile` for changed Python files: **PASS**

Total completed focused assertions: **44 passed, 0 failed, 0 skipped**.
A broader Action Contract file emitted no assertion failure before the temporary
sandbox dependency left the process hanging during teardown; that incomplete
run is NOT counted as PASS. GitHub CI with canonical dependencies remains the
source of truth for full regression.

## Patch pack

Ready-to-apply diffs are under `patches/sandbox-runtime-hardening/`:

- `engine.patch`
- `api.patch`
- `providers.patch`
- `tests.patch`

They were generated from the exact production preimages used by the live
reproductions. Apply them semantically on the current PR #37 head and run the
included regression patch plus canonical Command Center CI.

## Existing freeze blockers not superseded by this audit

This sandbox audit does not claim to close the current V4/V5 handoff blockers.
The current integration line still tracks at least:

- P0-A: canary has no production caller / broad-activation integration path;
- P0-B: SATISFIED evidence must be resolved/attested, not a non-empty string;
- P0-C: UnknownEffect cannot be closed by an unrelated verified mutation;
- N4 real scheduler wiring and acceptance;
- N5 real-model promotion/retention path;
- N6 owner-browser acceptance;
- N8 production canary + actual rollback path;
- exact-SHA owner Windows/local-model acceptance;
- intelligence preservation measurement (`INSUFFICIENT_EVIDENCE` until real data);
- PRIVATE egress and required long-horizon/soak evidence.

## Verdict

`SANDBOX_USER_RUN = COMPLETED`

`SANDBOX_RUNTIME_PATCH = VERIFIED_FOCUSED`

`FEATURE_FREEZE_READY = NO`

`RELEASE_CERTIFICATION_COMPLETE = NO`
