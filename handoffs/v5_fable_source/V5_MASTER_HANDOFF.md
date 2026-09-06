# Bossman V5 Fable Master Handoff



---

## FILE: 00_START_HERE.md

# Bossman V5 — Fable Implementation Pack

Repository: `molotroka123-cell/AiMaxBossman`
Observed primary HEAD: `876e2434b94a84be6bb534fd53d02f5ddc2c4909`
Epoch 5: **Bossman Steward**

This pack is designed to save Fable context/model budget. Fetch current remote truth
first; then use this pack as an accelerator rather than re-reading the whole repo.

## Already present upstream

- V3 verified execution/finalizer/evidence foundations.
- V4 Continuity foundations.
- `bossman_shared/objective_spec.py`.
- V5 N1/N2/N3 pure contract foundations.
- Objective revision/lifecycle/predicate/proposal contracts.
- Fable PR #19 media/Web/Fleet hardening.
- exact-SHA certification tooling.
- Intelligence Preservation gate logic.

## This ZIP adds

- concrete V5 runtime reference architecture;
- ready-to-port Python reference modules;
- tests/fixtures/schemas/examples;
- security invariants;
- observer/admission/mission integration design;
- migration/rollback;
- Golden Missions H01–H10;
- anti-dumbness/context/tool-overload plan;
- Fleet/world-state/long-horizon notes;
- UX reference images;
- a mini prompt.

`reference_implementation/` is NOT production runtime. Port useful pieces into
canonical Bossman services only after checking current HEAD.

Permanent rules:

`MODEL_TEXT != PROOF`
`TOOL_SUCCESS != VERIFIED_EFFECT`
`APPROVAL != POST_STATE`
`MEMORY_DATA != POLICY_AUTHORITY`
`OLD_SHA_PASS != CURRENT_SHA_PASS`
`SKIPPED/CANCELLED != PASS`
`PROPOSAL != AUTHORIZATION`
`MISSION_COMPLETION != SUSTAINED_OBJECTIVE_HEALTH`


---

## FILE: 01_CURRENT_STATE_AND_GAPS.md

# Current V5 State and Gaps

## Implemented upstream

The canonical contract layer is `bossman_shared/objective_spec.py`.

It already gives immutable ObjectiveSpec, revision/digest binding, typed sources and
predicates, bounded limits, expiry/lifecycle validation, explicit enrollment,
UNKNOWN for stale/missing/ambiguous evidence and deterministic proposal projection.

## Still required

- **N0:** accepted V4 substrate before runtime activation.
- **N1 integration:** canonical persistence/CAS/migration.
- **N2 runtime:** scoped observers, freshness, quotas, provenance, idle model calls = 0.
- **N3 runtime:** proposal → current policy/grant/resource checks → ordinary Mission IR.
- **N4:** conflicts, priority, quotas, cooldown, fairness, anti-oscillation.
- **N5:** measured templates/skills with no privilege expansion.
- **N6:** objective workspace and owner control.
- **N7:** H01–H10, security, cost, durability, 24-hour soak.
- **N8:** migration, opt-in canary, rollback rehearsal, exact-SHA release.

V5 is event-driven upkeep, not continuous hidden agent thinking:

event/bounded check → deterministic observation → predicate evaluation →
proposal only on verified deviation → current authorization → ordinary V4 mission →
verified post-state → fresh re-observation.


---

## FILE: 02_V5_SYSTEM_ARCHITECTURE.md

# Bossman Steward — Architecture

V4 means: complete a goal despite interruptions.
V5 means: maintain an explicit owner-defined condition until expiry or revocation.

```text
OWNER
 ↓
OBJECTIVE SPEC
 ↓
SCOPED OBSERVERS
 ↓
VERIFIED WORLD STATE
 ↓
DETERMINISTIC PREDICATES
 ├─ SATISFIED → evidence/sleep
 ├─ UNKNOWN   → stale/blocker
 └─ DEVIATED
      ↓
   PROPOSAL
      ↓
 ADMISSION KERNEL
 lifecycle/revision/grants/budget/conflicts/cooldown/revocation
      ↓
   MISSION IR
      ↓
 V4 EXECUTION/RECOVERY
      ↓
 POST-STATE VERIFICATION
      ↓
 RE-OBSERVE OBJECTIVE
```

All applications inherit one shared spine:

`MISSION → CONTEXT → POLICY → CAPABILITY → EXECUTION → WORLD STATE → VERIFICATION → RECOVERY → LEARNING`

Do not create parallel policy, budget, finalizer, evidence or mission runtimes.


---

## FILE: 03_IMPLEMENTATION_ORDER.md

# Implementation Order

## A — Map canonical integration points once
Find current persistence/store, Mission IR, policy/grants, Treasury/resource reserve,
event intake, finalizer/verifier, Fleet leases/fencing and UI page registry.

## B — Persistence + lifecycle
Persist spec/digest/lifecycle/cumulative usage/last observation/last proposal/stop/CAS.
Test restart, stale revision, concurrent activation, revoke, expiry, migration.

## C — Observers
Start with two local deterministic observers:
1. project/file state fixture;
2. bounded scheduled local check.
No cloud accounts; no continuous screenshot capture.
Test fresh/stale/missing/wrong source/wrong revision/duplicates/out-of-order/quota.

## D — Admission
Atomic:
current objective → ACTIVE/current revision → fresh/current observation → stop/cooldown
→ dedup → conflicts → grants/policy → reserve cost/resources → mission intent.
Any failure means no execution.

## E — Mission adapter + reconciliation
Convert admitted proposal into canonical Mission IR.
Use canonical executor/finalizer.
After completion, re-observe; never infer health from mission status.

## F — Conflicts/fairness
Stable owner priority, conflict keys, cooldown and bounded aging.

## G — UX
One objective workspace inside Command Center.

## H — Certification
H01–H10, crash matrix, 24h soak, Windows, exact-SHA CI, paired intelligence measurement.


---

## FILE: 04_SECURITY_AND_TRUST_INVARIANTS.md

# V5 Security / Trust Invariants

1. Activation is explicit; import is DRAFT/PAUSED.
2. Model text/memory/skills cannot activate or expand scope.
3. Revision cannot silently change owner/scope identity.
4. Revocation is sticky; expiry cannot resurrect.
5. Unenrolled sources cannot affect condition.
6. Stale/missing/ambiguous evidence = UNKNOWN.
7. UNKNOWN is never SATISFIED or DEVIATED.
8. Proposal is never authorization.
9. Recheck objective revision, grants and stop state at admission.
10. Recheck authorization again at effect boundary.
11. Reserve budget/resources atomically.
12. Unknown cost cannot become free.
13. Conflict keys block incompatible objectives.
14. Tool success is not verified effect.
15. Approval is not post-state.
16. Bookkeeping tables are not world-state proof.
17. Evidence binds mission/task/run/expected value/objective revision.
18. Crash ambiguity never authorizes irreversible replay.
19. PRIVATE/LOCAL_ONLY never silently falls back to cloud.
20. No implicit account/directory enrollment.
21. Learning may propose route/context/skill changes but cannot rewrite trust kernel.
22. Promotion requires benchmark + red-team + anti-dumbness + rollback.


---

## FILE: 05_OBJECTIVE_RUNTIME_DESIGN.md

# Objective Runtime Design

Lifecycle:
`DRAFT | ACTIVE | PAUSED | EXPIRED | REVOKED`

Condition:
`SATISFIED | DEVIATED | UNKNOWN`

They are separate. ACTIVE+UNKNOWN must not produce effects.

Suggested durable projection:

```json
{
  "objective_id": "...",
  "owner_id": "...",
  "scope_id": "...",
  "spec_digest": "...",
  "revision": 3,
  "lifecycle": "ACTIVE",
  "condition": "SATISFIED",
  "observations_used": 18,
  "missions_used": 2,
  "wall_seconds_used": 143.2,
  "cost_usd_used": 0.07,
  "last_observation_at": 1780000000,
  "last_proposal_at": 1780000000,
  "last_verified_evidence_ref": "...",
  "version": 19
}
```

Observers return deterministic data and provenance, not model opinions.
Admission resolves authority from current stores, not copied proposal metadata.
Final V5 health is set only after fresh re-observation following verified effects.


---

## FILE: 06_INTELLIGENCE_PRESERVATION_AND_CONTEXT.md

# Intelligence Preservation in V5

V5 risks context pollution from objectives/history/observations/skills/tools.

Same-model lanes:
RAW → SYSTEM → CONTEXT → FULL BOSSMAN.

Minimum gate:
`CORE_INTELLIGENCE_RETENTION >= 98%`

A V5 execution call should receive only:
current mission, relevant objective predicates, relevant latest observations,
applicable policy, selected skill if confidence passes, selected capabilities/tools,
and current verified world state.

Never dump all objectives, memory, Fleet, skills, logs or tools.

Capability flow:
`Objective → Mission IR → capability graph → relevant capabilities → tools`.

Memory stays data, never objective authority.

Governed improvement:
observe → hypothesis → sandbox → A/B → red-team → Intelligence Preservation →
canary → monitor → rollback.


---

## FILE: 07_FLEET_WORLD_STATE_LONG_HORIZON.md

# Fleet, World State and Long Horizon

Remote Fleet stays EXPERIMENTAL until independently qualified.

Before production claim verify node identity, mTLS/equivalent transport identity,
revocation, replay denial, lease/fence binding, mission/objective binding,
capability attestation, node/controller restart, partition, stale worker,
queue-completion ownership, PRIVATE placement and bounded retries.

World state is verified fact storage, not model belief. Facts need source, scope,
observed_at, freshness, provenance and explicit UNKNOWN semantics.

Long-horizon acceptance should cover restart, 50–200 tool actions, model reload,
changing app state and queued approvals. Resume from `LAST_VERIFIED_STATE`.

Crash matrix:
before dispatch / after dispatch / after external effect / before journal /
after journal / after approval / during verification / during finalization.


---

## FILE: 08_GOLDEN_MISSIONS_AND_ACCEPTANCE.md

# V5 Golden Missions

H01 Keep a fixture repository buildable after an approved change.
H02 Refresh a scoped local report when selected input changes.
H03 Prepare a draft update from changed project assets; never publish.
H04 Crash between deviation/admission/effect/receipt; no duplicate irreversible effect.
H05 Revoke/expire while queued/running; subsequent unauthorized effects = 0.
H06 Flood duplicate/out-of-order events; bounded queue/cost and <=1 admitted intent.
H07 Two objectives demand incompatible file states; visible conflict, no oscillation.
H08 Model/worker fails under PRIVATE; no prohibited egress/fallback.
H09 Malicious observation/recipe requests broader authority; authority unchanged.
H10 Upgrade/rollback with active/paused/unknown objectives; no autonomous replay.

Also require 24h soak, exact-SHA workflows, Windows, real FFmpeg where relevant,
paired model intelligence measurements, migration rehearsal and rollback rehearsal.


---

## FILE: 09_UX_PRODUCT_SPEC.md

# V5 Owner UX

Show objectives, not "agents running forever".

Objective card:
name, lifecycle, condition, last verified observation, next eligible check,
budget/cap, blocker, Pause, Revoke, Evidence.

Creation wizard:
desired condition → sources → freshness → allowed response → approvals →
budgets/caps → expiry → conflict scope → exact ObjectiveSpec preview → Activate.
Default is DRAFT.

Detail tabs:
Overview / World State / Timeline / Missions / Evidence / Budget / Permissions / Revisions.

Status:
green = fresh verified SATISFIED only;
amber = UNKNOWN/stale/pending owner;
red = verified DEVIATED/blocked;
grey = PAUSED/EXPIRED/REVOKED.

No model prose can directly set green.


---

## FILE: 10_AGENT_WORKSTREAMS.md

# Five Efficient Workstreams

Agent 1: persistence/lifecycle/migration.
Agent 2: observers/world state/freshness/provenance.
Agent 3: admission/current grants/Treasury/Mission IR adapter.
Agent 4: conflicts/Fleet/recovery/long-horizon.
Agent 5: UX/H01–H10/Intelligence Preservation.

Each agent receives only relevant files.
Return only:
FILES_READ / FILES_CHANGED / INVARIANT / TESTS / RESULT / OPEN_RISK / SHA.

Fable is final integrator and treats agent prose as untrusted until tests/evidence.


---

## FILE: 10_UX_REFERENCE_GUIDE.md

# Visual Reference Guide

`visual_references/01_objectives_overview.png` — objective workspace.
`02_objective_detail.png` — evidence-first detail.
`03_world_state_fleet.png` — verified world state and honest Fleet readiness.
`04_self_improvement_lab.png` — governed improvement, never blind self-modification.
`05_recovery_verification.png` — authorization/effect/evidence/recovery chain.

These are product references, not literal screenshots. Reuse current Command Center
design system; do not create a second frontend framework.


---

## FILE: 11_MIGRATION_AND_ROLLBACK.md

# Migration / Rollback

Existing missions do not become standing objectives.
Import is DRAFT/PAUSED.
No implicit account/directory enrollment.
Owner explicitly selects sources, response, permissions, budgets and expiry.

Sequence:
1. versioned records behind disabled flag;
2. migrate populated copy;
3. old runtime still works;
4. fixtures imported DRAFT;
5. read-only inspector;
6. explicit local observers;
7. proposal generation without admission;
8. canary admission;
9. rollback rehearsal;
10. expand opt-in.

Rollback:
pause observers → stop admissions → fence effects → drain/park missions →
snapshot objective state → restore compatible V4 → preserve V5 read-only.

Rehearse rollback during observation, proposal, admission, queued mission,
external effect and post-effect verification.


---

## FILE: 12_DO_NOT_DUPLICATE.md

# Do Not Duplicate

Search current HEAD before creating anything.

Do not create a second Mission IR, finalizer, verifier, evidence signer, policy engine,
approval engine, Treasury ledger, Fleet lease system, memory database, model router,
tool registry, Video Studio, Web Designer or objective schema.

V5 is a thin objective lifecycle above existing V4 missions.
If reference code conflicts with a stronger current repository invariant, preserve
the repository invariant and adapt the reference.


---

## FILE: 13_FABLE_TOKEN_BUDGET_PROTOCOL.md

# Fable Token Budget Protocol

Read once:
current V5 plan, ObjectiveSpec, this pack, exact canonical integration files.

Do not repeatedly read old superseded audits or whole repo.

Rhythm:
map → implement one boundary → narrow tests → hostile test → commit → fetch → continue.

Push small coherent commits before session budget gets low.

Report only PASS / FAIL / NOT_RUN / INSUFFICIENT_EVIDENCE.


---

## FILE: 14_RELEASE_SCORECARD_TEMPLATE.md

# V5 Release Scorecard

FINAL_SHA=

N0= N1= N2= N3= N4= N5= N6= N7= N8=

H01= H02= H03= H04= H05= H06= H07= H08= H09= H10=

OPEN_P0=
OPEN_P1=
FALSE_SUCCESS=
DUPLICATE_IRREVERSIBLE_EFFECTS=
PRIVATE_EGRESS=

RAW=
SYSTEM=
CONTEXT=
FULL=
CORE_RETENTION=
TOOL_SELECTION_DELTA=
SCHEMA_DELTA=
INTELLIGENCE_GATE=

SOAK_HOURS=
MAX_TOOL_ACTIONS=
RSS_GROWTH=
STUCK_APPROVALS=
UNBOUNDED_RETRIES=

ROOT=
CORE=
COMMAND_CENTER=
V2_REPAIR=
ASTRA=
SOLANA=
INTELLIGENCE=
V5_GOLDEN=

WINDOWS=
AI_MAX=
REMOTE_FLEET=

VERDICT=V5_NOT_COMPLETE
