# Production chains and the missing evidence

## N4 — actual queue service, not ordering

Inspected: `objective_fairness.rank/select/candidate_from_store`; part of
`AdmissionKernel.admit/settle/resolve_pending_releases`. The newly added settle
path records conflict releases durably; the old audit's missing-release defect
must not be copied forward as an independently re-reproduced current finding.

This run executes six fairness ordering controls. It does NOT serve a real
objective queue. No service entry -> production scheduler -> canonical policy /
Treasury / conflict lease -> actual effect -> independent verifier chain was run.
The later scorecard at f38ac74 explicitly reports that no production caller of
rank exists. That documentary acknowledgement is not an independent whole-tree
call-graph search; GitHub search results were incomplete.

### Conditions needed for a starvation guarantee

A rank() property alone gives no scheduling guarantee. The production caller must
select from every actually eligible contender, preserve truthful eligibility and
last-service times across restart, enforce quota and cooldown atomically, grant
at most one current lease to competing workers, and release/settle leases after
service. A permanently held resource or unresolved irreversible reservation
blocks progress regardless of age. Such uncertainty must not be cleared to make
a fairness metric look better.

A conditional bound can be derived only with a finite number K of older/equally
waiting eligible contenders, a bounded service-plus-scheduling delay B, a finite
starvation threshold D, eventual resource availability, no forged old arrival
times, and no priority change that resets age. If the caller always serves the
oldest starved candidate and resets age after service, a conservative wait bound
is D + K*B. This is a proposed bound under those assumptions, NOT a measurement
or an unconditional guarantee of this implementation. Quota limits alone do not
prove fairness when load exceeds service capacity or windows reset before the
waiting goal gets a turn.

Required next test: an actual scheduler loop with a continuously replenished
high-priority source, a waiting low-priority goal, competing workers and a real
restart. Assert observed service and the declared bound, not the rank array.
Keep standing autonomy disabled; use the existing disabled/manual test entry.

## N5 — eligibility is not actual promotion

Executed component chain: real fixture baseline/candidate calculations ->
independent CSV/arithmetic oracle -> measure -> authorize -> canonical durable
ledger. Tests use true output checks, not model-supplied scores. The policy-like
flags and retention reference are labelled fixtures. There is no actual model,
stored paired retention artifact, promoted skill, canonical deployment entry or
post-deployment verifier in this run.

Required next trace: existing service entry -> independently evaluated baseline /
candidate -> nonoverlapping holdout -> actual resolved retention report -> current
policy/Treasury/evidence -> canary activation -> separately verified behavior.
No broad activation or privilege expansion may follow just from authorize=True.

## N6 — actual entry present, execution intentionally absent

Static source at the tested SHA provides POST /objectives/preview (no writes),
POST /objectives (owner check -> ObjectiveStore.create -> DRAFT), and read/control
surfaces. GET /objectives/status explicitly returns autonomy=False,
observers_running=False, admission_enabled=False. The workspace is documented as
an inspector with owner actions, not a mission dispatcher.

No HTTP or browser test was run here. This is an inspected entry-to-store chain,
not a tested user-to-effect path and not a reason to turn autonomy on.

## N8 — pure canary and a store rehearsal, not production activation

Executed: pure evaluator and its malformed/reordered inputs. Inspected:
`test_a_rollback_rehearsal_with_an_objective_in_flight` uses real ObjectiveStore /
AdmissionKernel but fake policy/Treasury/conflict ports. It explicitly asserts
`not rollback.executed` and reopens ObjectiveStore in the same Python process.
No observed operating-system process restart is present in that function, and
no irreversible external effect is dispatched through an application caller.

Preserve that useful component test, but do not use it as evidence that a running
activation/rollback loop survived a crash. Required: actual subprocess boundaries,
revision-bound attestation, failure latching, crash between eligibility check and
activation, persisted decisions, real rollback/fencing/drain caller and an
independent fixture effect ledger showing no replay.

## AT-01 — prepared, not executed

`bossman-core/tests/test_v5_independent_at01.py` adds four negative cases through
make_manager/create_task/run with fixture adapters: missing unique-content file,
wrong fresh content, stale previous file, and only one of two obligations done.
An unrelated mutation really writes a decoy temporary file. Positive controls
cover read-only completion and correct requested bytes. Syntax is checked; all
six cases are NOT_RUN because the full Core source/environment was unavailable.

The inspected COMPLETE branch checks a fresh planner-chosen postcondition plus
at least one verified mutating step. That is a reason to run these tests, NOT an
independently reproduced production false-completion finding in this report.

## Owner recovery — still NOT_RUN

The seven passing ledger tests are not Pause/Take Control/approval recovery.
Extend existing owner-control and canary-rollback harnesses with actual separate
processes. Park via the actual owner/control path, terminate only that test
process, restart and verify the parked state with zero new dispatches. For an
in-flight irreversible fixture effect, crash after an independent effect receipt
but before the mission journal; Stop/unknown outcome must remain on reconciliation
and never replay the effect. Do not self-approve ASK requests. Use only temporary
fixtures, and do not compete for the owner's active desktop.

## Other deliberately unexecuted gates

Full root/Core/Command Center suites; owner Windows/UI; actual paired retention;
24h soak; human-speed measurements; real FFmpeg/video/Web Designer acceptance;
actual policy/Treasury/mission integration; populated migration; actual queue
fairness; production canary activation/restart; privacy transport/egress; Fleet
worker replacement; canonical repository secret scanner. No row borrows PASS
from an old SHA or from a lower evidence tier.
