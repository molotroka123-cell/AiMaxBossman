# Learning Case: BUG-004-asyncpg-pool-loop

## Metadata
MODEL: claude-fable-5-1
AGENT: agent-D+lead
START_SHA: 3ec4c81d72b4930e1ac9006541ac7ebd8036ab6a
END_SHA: c10d36a
LEARNING_STATUS: VERIFIED
OUTCOME: FIXED
VERIFIED_BY: pytest:bossman-core/tests/test_stage13_auth_redteam.py, pytest:bossman-core/tests/test_secrem_f018_wiring.py
CONFIDENCE: 0.85
TAGS: {"bug_class": "event_loop_affinity", "component": "bossman.db", "domain": "reliability", "security_boundary": "none", "severity": "LOW"}
FINDINGS: BUG-004

## Task
auth red-team b3/b4/b7: 'Task got Future attached to a different loop'

## Symptom
pytest-asyncio function-scoped loops reused a process-global asyncpg pool created on an earlier loop.

## Reproduction
- bossman-core/tests/test_secrem_f018_wiring.py::test_bug004_pool_is_rebound_per_event_loop (asyncio.run twice)

## Evidence
- db._pool singleton had no loop identity (pre-fix); prior attempt c1c44df awaited close() on the foreign loop and was reverted
- post-fix: second asyncio.run gets a new pool bound to the new loop; close() on a foreign loop discards instead of awaiting; stage13 auth red-team 22 tests green

## Hypotheses considered
- pool/loop affinity (root cause)
- fixture scope
- asyncpg version

## Rejected hypotheses + why
- fixture scope alone: module-scoped fixture hides it in one file but breaks any CLI/thread scenario
- asyncpg version: reproduced across versions

## Root cause
asyncpg connections/futures are bound to the creating event loop; the singleton ignored that.

## Relevant code paths
- bossman-core/bossman/db.py:pool
- bossman-core/bossman/db.py:_discard_stale_pool
- bossman-core/bossman/db.py:close

## Fix strategy
Remember the creating loop; on a foreign loop terminate() (sync) and recreate; graceful close only on the owning loop; per-test fixture closes on its own loop.

## Alternatives considered
- await close() on the stale loop (the reverted attempt — impossible from another loop)

## Why this fix was chosen
Correct for tests AND for production patterns (asyncio.run in helper threads).

## Files changed
- bossman-core/bossman/db.py
- bossman-core/tests/test_stage13_auth_redteam.py

## Tests added
- bossman-core/tests/test_secrem_f018_wiring.py::test_bug004_pool_is_rebound_per_event_loop

## Original reproduction after fix
green on Linux/PG16

## Adversarial variants
- close() from foreign loop does not raise

## Regression
stage13 auth red-team + 247 focused passed

## Fresh external verification
pytest against live Postgres 16 on this host.

## Generalizable lessons
- Any singleton holding loop-bound resources must record and check its loop.

## Teach local model
- Recognize: 'attached to a different loop' + global pool
- Avoid: awaiting close() of a foreign-loop resource
- Prefer: terminate + recreate

## Limitations / follow-up
- RunPod/Windows re-run pending (GLM observed the failure there; fix verified on Linux only).
