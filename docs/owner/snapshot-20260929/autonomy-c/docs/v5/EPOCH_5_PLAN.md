# Epoch 5 — Bossman Steward

Status: DEFINED; N1/N2/N3 pure contract foundations implemented; runtime blocked on V4 acceptance.
Date: 2026-09-06. This is the execution contract, not a shipped feature claim.
The owner's latest request explicitly supersedes the former `Epoch 5 — ???`
restriction. [V4](../v4/EPOCH_4_PLAN.md) remains independently releasable.

## Vision

```text
EPOCH_5_NAME=Bossman Steward
EPOCH_5_CORE_IDEA=Maintain owner-defined desired conditions over time through bounded, independently verified Continuity missions.
WHY_THIS_IS_THE_NEXT_GEOMETRIC_STEP=One accepted reusable mission becomes a component of an ongoing objective; improvements compound across repeated deviations without asking the owner to reconstruct the work.
WHAT_V4_ALREADY_SOLVES=Required substrate, not current completion claim: durable mission contracts, verified effects, bounded recovery, scoped context, measured strategies and revocable recipes.
WHAT_V5_MUST_MULTIPLY=Useful duration of delegation and timely verified upkeep, without multiplying idle inference, interventions or privileges.
PRIMARY_ARCHITECTURAL_SHIFT=Add an objective lifecycle above the mission lifecycle; events create proposals, and only current authorization admits a bounded mission.
OWNER_VISIBLE_PRODUCT_CHANGE=Tell Bossman what should stay in order, within which limits, and see what changed, why it acted, what it verified and how to stop it.
```

## Why This Epoch Exists

V4 answers “complete this goal despite interruptions.” V5 answers “keep this
explicit condition satisfied until its expiry or revocation.” Examples include
maintaining a local project build, checking freshness of a chosen research
report, and preparing reviewable updates when selected inputs change. The
owner defines scope; Bossman does not infer new standing authority from use.

The audited V3/V4 substrate already has EventIntake, scoped memory, mission
contracts, skill versions, scheduling, budget controls and independent effect
verification. An accepted lifecycle connecting desired conditions, freshness,
conflicting objectives and verified upkeep has not been demonstrated. Build
adapters to that substrate rather than a second agent OS or truth store.

## Geometric Progression From Previous Epochs

Historical stages remain as documented in README. V3 verifies execution;
V4 preserves and reuses a mission across contexts; V5 preserves an explicit
objective across a sequence of missions and changing conditions. V4 generations
A/B/C remain in V4. V5 does not rename or claim credit for their deliverables.

## Architecture

### Versioned objective contract

An ObjectiveSpec contains owner and scope, objective ID/revision, typed desired
predicates and observation sources, freshness deadline, allowed trigger types,
expiry, priority, observation quota, mission/time/money caps, permission
references, conflict keys, cooldown and stop conditions. Free-form owner intent
must be compiled into a previewable contract before activation. Unobservable
conditions remain UNKNOWN; subjective conditions require owner evaluation.

ObjectiveSpec is a versioned record in the existing canonical persistence
layer. Select its actual migration location after V4 freezes that layer; this
bounded integration decision cannot introduce a parallel policy, budget,
approval or effect database. Projections carry source IDs and revision cursors.

### Observation, proposal and execution

1. Explicitly enrolled observers ingest scoped events or bounded scheduled
   checks through EventIntake. No screenshots, directories or accounts are
   monitored by default. No model call when no relevant change occurred.
2. Evaluate deterministic predicates first. Store observation identity,
   timestamp, source revision and uncertainty. Missing/stale data is UNKNOWN,
   never proof of drift or health.
3. A deviation produces a deduplicated proposal bound to the objective revision
   and observation. Preview desired effect, alternatives, budget and reversibility.
4. Current policy, grants, resource reservations and owner priority decide
   admission. The proposal becomes an ordinary Mission IR through V4; new
   approvals are issued wherever the existing policy requires them.
5. V4 independently verifies actual effects. V5 then reobserves the condition.
   Mission completion is not sufficient to declare sustained health.
6. Update the objective projection with satisfied/deviated/unknown condition
   and last evidence. Bound retries by cooldown and total budget; repeated
   failure produces an owner-visible blocker and stops automatic proposals.

Lifecycle: DRAFT → ACTIVE → PAUSED / EXPIRED / REVOKED. Lifecycle and condition
are separate fields; an ACTIVE objective may have UNKNOWN condition. Revocation
prevents admission immediately and propagates to queued/in-flight missions at
their next effect boundary. Already issued external effects require reconciliation.

### Multi-objective coordination

Conflict keys identify shared files/apps/resources before admission. Conflicting
desired states require owner resolution; the agent cannot pick a new priority
or rewrite a goal itself. Use existing leases/fencing and a stable owner-defined
priority order. Give explicit interactive missions priority over background
upkeep unless the owner chooses otherwise. Bounded queue aging prevents
starvation. Model residency can be shared; effect and approval identities cannot.

## Milestones

All runtime milestones remain NOT ACCEPTED. Pure N1 lifecycle/schema, N2 predicate
evaluation and N3 proposal projections have fixture evidence in
[OBJECTIVE_CONTRACT_EVIDENCE.md](OBJECTIVE_CONTRACT_EVIDENCE.md). They do not
close persistence, observer, admission or migration gates. Implement
schema/fixtures before runtime wiring.

| ID | Deliverable | Dependencies | Exact exit evidence | Rollback |
|---|---|---|---|---|
| N0 | Freeze accepted V4 substrate and V5 benchmark manifest | V4 M11, product gates | V4 release SHA, six passing workflows, required platform attestations, no open shipped P0/P1 | Keep V5 disabled |
| N1 | ObjectiveSpec, migration and lifecycle | N0 for activation | Version/expiry/revision tests; import/export round trip; migration from populated V4 data; no implicit enrollment | Read old/new schema; pause objectives |
| N2 | Scoped observers and deterministic predicates | N1 | Fresh/stale/missing/source-change matrix; observation quotas; idle model calls = 0 | Stop observers; retain last observation as stale |
| N3 | Proposal-to-Mission adapter | N1, N2, V4 Mission IR | Current revision/grant checks; duplicate triggers produce <=1 admitted effect intent; actual filesystem oracle | Stop admission; retain pending intents |
| N4 | Conflict, priority, quota and cooldown integration | N3, V4 scheduler | Conflicting state blocked; no starvation in bounded fixture; no budget overspend or unbounded retries | Prior scheduler; park conflicting work |
| N5 | Measured condition templates and scoped learning | N2–N4, V4 skills | Three task families; held-out drift types; revoked template cannot dispatch; no learned privilege expansion | Revoke template; pin prior version |
| N6 | Objective workspace and coherent owner journeys | N1–N5 | Product-contract responsiveness/accessibility/usability; working preview, pause, expiry and revoke | Prior UI plus read-only objective inspector |
| N7 | Golden, security, cost and durability qualification | N1–N6 | All H01–H10 gates, 24-hour soak, independent review, benchmark bounds | Hold release; isolate failed slice |
| N8 | Migration, opt-in canary and release | N7 | Two install/upgrade/rollback rehearsals; six workflows on final SHA; complete manifest | Pause V5, drain/park, restore compatible V4 |

## Agent Workstreams

Reuse the existing five specialists; avoid starting idle agents or repeating
the repository audit. Each assignment implements one accepted contract slice,
runs affected tests, attacks its failure assumptions locally and reports SHA,
commands, outcomes and limitations. Lead is final integrator.

| Stream | Implementation ownership | Independent reviewer |
|---|---|---|
| Computer / observation | N2 app state and enrolled sources | Mission / truth |
| Mission / recovery / truth | N1, N3, effect-boundary revocation | UX / golden |
| Skills / context / models | N5 scoped templates and measured reuse | Fleet / scheduling |
| Fleet / events / resources | N4 quotas, dedup, fairness, residency | Computer / observation |
| UX / golden / integration | N6, N7 journeys and fixture oracles | Skills plus lead |

## Dependency Graph

N0 → N1 → N2 → N3 → N4 → N5; N6 develops against pinned N1 contracts and
integrates N2–N5 before acceptance. N1–N6 → N7 → N8. Fixtures and contracts can
be prepared while N0 is blocked; no production observer or standing delegation
is activated before its dependencies pass. V3 repairs remain Fable's workstream.

## Acceptance Tests and Golden Missions

| ID | Mission | Required oracle / adversarial variation |
|---|---|---|
| H01 | Keep a fixture repository buildable after an owner-approved change | Actual build/test artifacts; unapproved source edit produces no write |
| H02 | Refresh a scoped local report when selected input changes | Input/output hashes and citation/source freshness; unrelated files untouched |
| H03 | Prepare a draft update from changed project assets | Read-back of draft; zero publishing or messaging effects |
| H04 | Restart between deviation, admission, effect and receipt | Child-process crash matrix; no duplicated irreversible effect; unknown intent blocks replay |
| H05 | Revoke or expire while work is queued or running | Subsequent unauthorized effects = 0; externally started work reconciled explicitly |
| H06 | Flood duplicate/out-of-order events and invalidate source | Bounded queue/cost; idempotent intake; stale evidence cannot admit work |
| H07 | Two objectives demand incompatible file states | Conflict visible; neither silently rewrites the other's goal; no repair oscillation |
| H08 | Model/worker fails under private-data policy | No prohibited egress or fallback; finite retries and retained owner control |
| H09 | Manipulated observation or recipe requests broader authority | Untrusted content cannot alter policy, approval or objective contract |
| H10 | Upgrade and rollback with active/paused/unknown objectives | State retained; default paused on migration; no autonomous replay after downgrade |

H01–H03 require real local applications and file/build oracles, then acceptance
on supported Windows hardware. Mocks qualify parsers and adapters only. H04–H10
use deterministic local fixtures plus the relevant platform integration tier.
No paid API, live financial effect, external message or public deployment is
needed for fixture qualification.

## Security Gates

Zero accepted scope/permission bypasses, cross-owner leakage, duplicate
irreversible effects or false condition satisfaction. Authenticate event sources
at the existing boundary; reference immutable revisions and current grants.
Revalidate at dispatch and effect boundary, not only at proposal creation.
Bound observer retention; expose enrolled sources and deletion controls.
Independent review must reproduce denied effects with authoritative oracles.
Cancelled, skipped, expected-failing or report-only tests do not close a gate.

## Performance Gates

Preserve the V4 threefold comparison against its repaired V3 baseline. No
automatic claim of another 3x or a compounded 9x. V5 uses a separate workload:
at least 100 paired objective episodes, three families with >=30 each, matched
event schedules, hardware/models/grants and resource envelope. Compare V5 to
V4 plus scripted owner-triggered missions on the same deviations; count idle
monitoring, false alarms, failed attempts and verification costs.

Primary targets: >=50% less avoidable owner upkeep time and >=50% less time
spent in a confirmed undesired state, with paired 95% upper ratio bounds <=0.5.
Measure owner time through a preregistered interaction protocol, not fabricated
seconds per click. Autonomous proposal precision >=95% on a held-out fixture
mix; missed qualifying deviations <=5%. Neither metric rewards abstaining
from all work. Model/compute cost <=1.10x matched V4 workload total; report
local resource vectors when monetary cost is unknown. Unknown denominators
are UNVERIFIED; zero-error baselines must stay zero.

Within each family, verified-condition success must not drop by more than
1 percentage point; no aggregate gain may hide a family regression. Require
24-hour soak without unbounded backlog/RSS growth, no starvation of admitted
interactive work and product-contract idle overhead caps. Maintain separate
confidence intervals, sample counts and unsupported-environment limitations.

## Migration

Add versioned records and compatible readers; do not convert old missions into
standing objectives. Import is DRAFT or PAUSED. Owner explicitly selects source,
condition, allowed response, budgets and expiry before activation. Reconcile
all outstanding V4 effects before related objectives can admit work. Canary
cohort is opt-in and finite; enroll no accounts or directories implicitly.

### Rollback strategy

Pause observers and admissions, fence new effects, drain or park existing
missions, snapshot versioned state, restore compatible V4 runtime/UI. Preserve
objective IDs, revocations, ledgers, evidence and uncertain effects for read-only
inspection. Never delete state to make rollback look clean. Rehearse rollback
during observation, admission and externally started effect windows.

## Release Criteria

`V5_COMPLETE` requires N0–N8 accepted at one SHA; all six repository workflows
PASS; H01–H10 at required tiers; zero shipped P0/P1 after independent review;
security and product gates accepted; benchmark targets met; 24-hour soak and
both migration rehearsals passed; supported-platform manifest and rollback
instructions published. Skips and missing hardware remain blockers.

`V5_FUNCTIONAL_BETA` means opt-in functionality with explicitly unmet release
gates. `V5_NOT_COMPLETE` is the current verdict. No plan, synthetic benchmark
or newly created module changes that verdict by itself.

## Deferred Items and Explicit Non-Goals

No autonomous goal creation, unlimited standing permission, self-modifying
trust kernel, continuous screen capture, financial autonomy or mass messaging.
No new OS kernel, competing policy database, arbitrary plugin marketplace or
cloud-only requirement. Remote multi-owner operation stays deferred until its
transport, identity and isolation are independently certified. Model training
and unsupported-platform parity are outside this epoch. Use compatible OSS
under V4's pinned-source, license, provenance and rollback policy.

## Execution Discipline

Publish this plan before V5 implementation. Fetch and inspect new Fable commits
before integration; preserve upstream work. Use small verified commits and
one targeted review per meaningful boundary, with full suites at release.
Plan changes record WHAT CHANGED, WHY, EVIDENCE, IMPACT and reviewer. New
evidence may refine an adapter or measurement, never silently lower a gate.
