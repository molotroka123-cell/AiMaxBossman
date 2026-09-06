# V4 Continuity integration patch — actual run audit

## Scope and provenance

Repository: `molotroka123-cell/AiMaxBossman`.
Tested upstream snapshot: `3803301ebdbc2a2f98299cb586ff05f4891354dd`.
Source tree: `c61c31b9d4f176a9ff03c428de7ce0dc898bf164`, matching GitHub.
Offline snapshot commit: `b59d0a1fb5b37d09ed74653077075a930c58957b`.
The local snapshot commit is NOT the upstream commit and must not replace its history.
Apply the accompanying format-patch using `git am --3way` on current upstream.

Last separately observed Fable head: `9d3f82ac0d26c5b88b408f0c470a3acfae700f98`.
The 4 commits after the tested base change 17 files; none matches this patch's
13 code/test paths. This is a path-level conflict check, NOT combined acceptance.
Do not replace Fable's recent memory, recovery, Fleet or media hardening.

## Implemented in this patch

1. Bind persisted mission plans to child node IDs and dependency edges, using
   existing mission/task tables and the existing TaskGraph. No second DB/kernel.
   An unfinished or failed dependency cannot release downstream execution.
2. Create parent, children and binding in one transaction; reject malformed,
   nonfinite or oversized plans. Repeated materialization is idempotent.
3. Enforce dependency readiness at enqueue, execution and shared tool boundaries.
   Atomic mission worker-cap checks protect concurrent enqueue across engines.
4. Make lifecycle transitions conditional and terminal states sticky. Repeated
   Start and pause/resume do not extend the mission deadline. A stale ticker
   cannot overwrite an owner stop. Pause blocks new dispatch; already executing
   external effects still require reconciliation, not an impossible cancellation.
5. Snapshot nested typed-action arguments. Recheck policy inside the execution
   guard. Changed approved action content, new ASK policy and revoked authority
   fail closed before dispatch. Compound receipts use the actually executed copy.
6. Bind BCC approval identity to full action, expectation, task, run, agent and
   tool implementation. Do not authorize by the truncated human preview.
   Consume the canonical approval at the effect boundary, not during lookup.
7. Expose dependency/block reason state in the existing mission UI. Projection
   is scheduling data, NOT post-state evidence; canonical finalizer stays intact.

## Tests actually observed

| Suite | Result | Qualification |
|---|---|---|
| Command Center targeted integration | 88 passed, 0 failed, 0 skipped; 31.45s | exit 0 recorded |
| Mission UI Node contract | 5 passed, 0 failed, 0 skipped | contract, not browser visual acceptance |
| Core affected suite | 249 passed, 0 failed, 0 skipped; 40.46s in JUnit | normal process exit NOT certified; teardown/exit remains a blocker |
| Same Core subset on unmodified base (without 16 added tests) | 233 passed; 36.79s in JUnit | no saved exit-code proof; not a baseline release certificate |
| Secret-pattern scanner | PASS | exit 0 recorded |
| Patch whitespace | PASS | git diff --check |

JUnit/log files are included in the delivery archive. A summary reporting 249
passed does NOT override an unverified or hanging process exit. No full-suite,
coverage, PostgreSQL-concurrency, Windows, actual desktop, live provider,
24-hour soak or real-model intelligence acceptance was completed in this patch.
No test/coverage/verification gate was lowered to claim success.

## Remaining risk / release boundaries

- Reconcile against current Fable HEAD, then repeat affected and full suites.
- Diagnose Core shutdown/aiosqlite/event-loop lifecycle before accepting the run.
- Exercise PostgreSQL row-lock behavior and shared worker admission explicitly.
- Old unbound DAG missions fail closed and need explicit migration/replanning;
  old flat missions retain compatibility. No destructive auto-migration.
- Recheck task reassignment, active mission plan mutation, concurrent stop and
  actual irreversible effects independently at dispatch/effect boundaries.
- New digest-bound approval previews intentionally do not reuse legacy previews;
  verify owner resume/reapproval UX on existing installations.
- A dispatcher cannot unsend an external effect already accepted elsewhere.
  Preserve ambiguous-state blocking and use authoritative reconciliation.
- This patch advances V4 foundations, not all M0–M11 or A/B/C acceptance.
- V5 standing autonomy remains gated on V4/N0. No performance multiplier or
  intelligence-preservation improvement has been measured here.

## Delivery state

This is a local committed patch, not proof of a GitHub push. The GitHub write
operation returned Resource not found; rediscovery currently exposes read tools.
The local CLI cannot resolve github.com and has no usable configured remote auth.
The GLM operator with its existing GitHub setup must apply, test and push.
Do not ask the owner to paste a token into chat or include credentials in logs.

VERDICT=V4_PATCH_IMPLEMENTED_NOT_RELEASE_CERTIFIED
V4_COMPLETE=NO
EXACT_SHA_CI=NOT_RUN_FOR_THIS_PATCH
