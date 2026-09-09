# Owner task admission and truthful health

Baseline: `c7e75cc3c0a636d61f6fc8fb3e476a210d1320d3`.
This is a scoped repair record, not final release acceptance.

| Finding | Reproduction / negative control | Change / positive control |
|---|---|---|
| MF-031, P2 | Fresh DB: `POST /api/tasks` with `run_now=true, agent_id=null` created a blocked task without a run. Missing, disabled, deleted-model and policy-denied agents were predictable failures. | Creation-time selection under the insertion transaction. Unavailable -> actionable 409 before task/run creation; configured automatic/explicit selection queues with unchanged permissions. Intentional drafts remain drafts. Runtime/effect-time admission is unchanged. |
| MF-032, P2 | `/health` returned 404; an HTTP 200 from `/api/system` made Overview claim normal operation even when components were not configured. | `/health/live` proves process liveness; `/health`, `/healthz`, and authenticated `/api/health` report actual readiness with distinct component states. Overview uses component states. |
| ASTRA-HEALTH-01, P2, new | Unmeasured models looked healthy; stale success was not distinguished. | Only fresh measured model success reads HEALTHY. Unknown, stale, missing, broken and partially available provider registries are distinct. A working model plus degraded optional models can remain ready, with DEGRADED status. |
| ASTRA-HEALTH-02, P2, new | Worker/scheduler/metrics stamped heartbeats before work and swallowed loop exceptions, so repeated failures could read healthy. A completed loop also retained a recent heartbeat. | Last failure tracked until a successful iteration; terminated tasks override recent heartbeats. Failing loop negative controls and ordinary loop health retained. |
| ASTRA-HEALTH-03, P2, new | Importable Playwright was called healthy when Chromium was absent. | System health reports UNKNOWN until an actual connected context is observed. This does not make browser use a requirement for core task readiness. |
| ASTRA-UI-01, P2, new | Publish refusal/status message was hidden by CSS at mobile width. | Message remains visible on mobile. Busy-state/refusal/no-retry controls added for desktop and mobile; no live GitHub publish is performed by these tests. |

Test integrity: the old assertion that task creation returns a blocked row was
replaced with the stronger zero-created-tasks/409 assertion. Draft/run/retry and
removed-executor runtime refusals retain coverage. The old home-page selection
fixture created agents without any model; it now supplies a provider/model
configuration, since queuing that prior fixture is precisely MF-031. Workers
remain disabled there and no model success is claimed.

Scoped verification before this commit: 160 passed, 7 browser cases skipped
because this host has no Chromium executable. Includes task preflight,
executor admission, health, API, completion truth, action contracts,
authorization-at-effect-time, scheduler and GPU metrics. JavaScript syntax and
`git diff --check` passed. Browser cases must run in browser CI before closing
their acceptance status.

Source Services boot (not clean-install proof): fresh temporary data directory,
real worker/scheduler/metrics and all 14 feature loops started successfully.
Models/providers were correctly NOT_CONFIGURED. No real provider credentials or
local model were available; genuine task execution remains OWNER_LIVE_REQUIRED.

## One-command genuine owner core acceptance

Run using the Python interpreter from the clean installed release environment:

```sh
python -m bcc.owner_acceptance --data-dir <configured-bcc-data-directory> --output bossman-owner-task-acceptance.json
```

Optional `--agent-id N` pins the owner's configured agent/model. Otherwise the
freshest best-ranked enabled configured agent is chosen. This is a real small
model call, with no fixture fallback: one arithmetic task, max 256 output tokens,
no tools and no model fallback. It requires exact answer `391`, one completed run,
and the same completed run/result after restarting an owned installed server.

The command reads the original registry without writing it, selects only one
provider credential, and re-encrypts that credential under a fresh key in a
private temporary acceptance directory. It never copies old tasks, schedules,
the owner's master key, other provider credentials, or the original agent's
effectful permissions. Only its own server processes are stopped/restarted.

Reports bind to the installed wheel's `bcc/_build.json` full `source_sha` and
build-manifest hash. An editable/source checkout is refused. Exit 0/PASS covers
only installed core API/model/restart acceptance; full browser UI and the other
product flows remain separate. Exit 2/OWNER_REQUIRED means unavailable installed
artifact/model/credential configuration. Exit 1/FAIL means observed acceptance
failure. Secret response bodies, tokens, vault keys and server logs are never
copied into the shareable report.

Harness integrity tests use isolated SQLite fixtures only; those tests are not
evidence that a real owner model has run. Intelligence Preservation is untouched.

## Fable authorization fixture after MF-031

The first integrated Fable CI attempt exposed an obsolete setup in
`test_revoked_approval_cannot_be_consumed_or_used_for_override`: POST `/api/tasks`
with prompt `p` and no configured agent previously created an unusable task. MF-031
correctly refuses that request before insertion, so indexing `response["task"]`
failed before any revoked-approval assertion ran.

The test now uses the existing `make_stack` provider/model/agent fixture, retaining
prompt `p`. Runtime admission and every authorization assertion are unchanged:
the revoked terminal approval cannot be consumed, revoked review escalation
cannot authorize `finalize_override`, and the task remains `waiting_approval`.
The positive/negative revocation controls and effect-time/finalize suites remain
mandatory. This is a setup correction to reach the original safety boundary;
creating an executor-less task is not the assertion this test is meant to prove.
