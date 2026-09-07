# V5 Release Scorecard — Bossman Steward

Reported honestly against the template in the Fable V5 implementation pack.
Every row is `PASS` / `FAIL` / `NOT_RUN` / `INSUFFICIENT_EVIDENCE`. A row that
was never measured says `NOT_RUN`; it does not borrow the colour of a row that
was.

`FINAL_SHA=` (unset: this branch is not a release candidate)

## Nodes

| Node | State | Basis |
|---|---|---|
| N0 — accepted V4 substrate | `NOT_RUN` | Acceptance is a separate decision. Standing autonomy stays off until it lands. |
| N1 — canonical persistence/CAS/migration | `PASS` | `bossman_shared/objective_store.py`, 99 hostile tests, verified no lost update under 8 threads. |
| N2 — scoped observers, freshness, provenance | `PASS` | `objective_observer.py` / `objective_world_state.py`, 25 tests. Idle model calls are 0 by construction: `should_observe` is deterministic and the value digest excludes time. |
| N3 — proposal → current authorization → Mission IR | `PASS` | `objective_admission.py` / `objective_mission.py`, 18 tests, emitting real canonical `MissionIR`. |
| N4 — conflicts, cooldown, quotas, anti-oscillation | `PASS` (repo-local) | `objective_fairness.py` (33 tests): quota per window, bounded continuous aging, starvation deadline, deterministic total order. A prior defect is closed with it — a successful admission never released its conflict key, so a second objective was refused `conflict_held` forever; `AdmissionKernel.settle` now ends an admission, with durable release bookkeeping for the gap the DB and an external port cannot share a transaction across. Eight hostile settle cases covered. Fairness under a REAL scheduler loop is still `NOT_RUN`: nothing calls `rank()` in production yet. |
| N5 — measured templates/skills, no privilege expansion | `PASS` (repo-local) | `objective_promotion.py` (29 root tests + 5 against the canonical durable ledger): deterministic holdout, applicability-version binding checked at authorization, evidence spent exactly once with the refusal surviving a restart, evidence spent LAST so a malformed request does not burn it. Structural refusals still unbuyable. A promotion measured against a REAL model is `NOT_RUN` — the runner exists (`tools/intelligence_preservation_run.py`) but has not been run on the owner's model. |
| N6 — objective workspace and owner control | `PASS` (repo-local) | Creation wizard with a real server-side approval preview (`POST /objectives/preview` writes nothing), Evidence and Revisions tabs, Pause/Resume/Revoke. 15 backend tests + 7 node tests. `v5_spec_history` keeps superseded specs so Revisions shows WHAT changed. Owner-visible acceptance in a real browser on the owner's machine is `NOT_RUN`. |
| N7 — H01–H10, security, cost, durability, soak | `PARTIAL` | H01–H10 pass as an in-process suite. 24-hour soak, Windows and real-FFmpeg rows are `NOT_RUN`. |
| N8 — migration, canary, rollback rehearsal, exact-SHA | `PASS` (repo-local) | `objective_canary.py` (18 tests): deterministic cohort bounded at both ends, silence is PENDING not PASS, one unhealthy member fails the whole canary. The rehearsal is real — a live objective admitted through the actual `AdmissionKernel`, an irreversible effect in flight, a revision landing under it, the canary failing, and all four promises asserted across a process restart. A rollback rehearsal on the owner's machine is `NOT_RUN`. |

## Golden Missions

All ten run in `tests/test_v5_golden_missions.py` against the real store, real
files, real observers, the real Mission IR contract and the canonical evidence
signer. Only the policy, Treasury and conflict *ports* are faked — they are
canonical services elsewhere and are adapted, not reimplemented.

| Mission | State | What it actually proves |
|---|---|---|
| H01 buildable after an approved change | `PASS` | The full chain reaches SATISFIED only via a fresh post-effect re-observation with bound, signed evidence. |
| H02 refresh a scoped report on input change | `PASS` | An unchanged world yields no proposal, so an idle objective costs no model call. |
| H03 draft from changed assets, never publish | `PASS` | An ungranted capability is refused with the Treasury never touched. |
| H04 crash between deviation and receipt | `PASS` | An irreversible effect of unknown outcome parks across a real restart; it never replays. |
| H05 revoke/expire while queued | `PASS` | The effect-boundary recheck denies both, and revocation is sticky. |
| H06 duplicate/out-of-order event flood | `PASS` | 25 replays collapse to one proposal identity; five admissions yield one intent and one charge. |
| H07 incompatible objectives | `PASS` | The conflict key is visible and the loser stays refused rather than oscillating. |
| H08 failure under PRIVATE | `PARTIAL` | The compensation path is proved: a refusal downstream of a claim releases it. The egress floor itself is a transport property this suite cannot observe — `NOT_RUN`. |
| H09 malicious observation seeking authority | `PASS` | An observation asserting permissions changes nothing; the stored spec still defines authority. |
| H10 upgrade/rollback with active objectives | `PASS` | A revision mid-flight invalidates queued work, inherits no verdict and keeps cumulative usage. |

## Open items

```
OPEN_P0=3 (repo-local)
OPEN_P1=0 (repo-local)
FALSE_SUCCESS=0 observed
DUPLICATE_IRREVERSIBLE_EFFECTS=0 observed
PRIVATE_EGRESS=NOT_RUN
```

The three open P0 items, named rather than counted:

| Id | Hole | Why it is P0 |
|---|---|---|
| P0-A | `bossman_shared/objective_canary.py` has **zero production importers** | The canary attestation and its seven bindings are exercised only by tests. Nothing on the real broad-activation path calls `authorize_broad_activation`, so N8's guarantee does not hold outside the suite. |
| P0-B | `objective_store.set_condition` admits `SATISFIED` on any non-empty `evidence_ref` string | The gate is `type(evidence_ref) is str and evidence_ref.strip()`. It never resolves the reference to a real signed evidence record, so a plausible string satisfies a condition. Scaffolding for the resolved check is in the tree; the check itself is not switched over. |
| P0-C | AT-01 obligations consisting only of `UnknownEffect` are dropped | A step whose only obligation is unnamed is currently allowed to complete. Requiring a screen change here is wrong — a legitimate effect can be invisible (a background write, an API call) — and no other discriminating signal exists yet, so the honest state is "not finished", not "closed". |

Until all three are closed, `FEATURE_FREEZE_READY=NO`.

## Intelligence preservation

```
RAW=NOT_RUN
SYSTEM=NOT_RUN
CONTEXT=NOT_RUN
FULL=NOT_RUN
CORE_RETENTION=NOT_RUN
INTELLIGENCE_GATE=INSUFFICIENT_EVIDENCE
```

The measurer now exists (`tools/intelligence_preservation_run.py`, 220-task
held-out set, 19 tests). It did not before: the gate had no producer, which is
why `docs/benchmark/intelligence-preservation-current.json` was missing and the
CI job failed on the missing file rather than on any measurement.

One number belongs here, because it changes what "measured" costs. `--min-samples 20`
in CI is a floor, not sufficiency. Measured by running the real gate: a PERFECT
paired run still answers `INSUFFICIENT_EVIDENCE` at 20, 100 and 150 items per
metric, and only reaches `PASS` at **189**; with one lost item per metric no
sample size up to 4000 passes. The shipped set is 20 per metric — an honest
smoke of all four lanes, and explicitly not a `PASS`. See
`docs/benchmark/INTELLIGENCE_MEASUREMENT.md`.

The context boundary is implemented and tested (`objective_context.py`, 50
tests): the slice refuses foreign objectives, unscoped memory and every
forbidden dump key, and memory can never be read as policy. That is a property
of the code, not a measurement of the model. **Evaluator unit tests are not
retention proof.** A real paired same-model measurement across the RAW → SYSTEM
→ CONTEXT → FULL lanes is required before this section may report anything but
`NOT_RUN`, and `objective_improvement.may_promote` refuses a retention figure
that is not bound to such a report.

## Durability and long horizon

```
SOAK_HOURS=0 (NOT_RUN)
MAX_TOOL_ACTIONS=NOT_RUN
RSS_GROWTH=NOT_RUN
STUCK_APPROVALS=0 observed in H04/H05
UNBOUNDED_RETRIES=0 (bounded_retry is the only retry primitive)
```

## Suites

```
ROOT=PASS (root pytest, this branch)
V5_GOLDEN=PASS (17 tests)
INTELLIGENCE=PASS (existing gate suite, untouched)
CORE=NOT_RUN here
COMMAND_CENTER=NOT_RUN here
WINDOWS=NOT_RUN
REMOTE_FLEET=EXPERIMENTAL — unqualified, unchanged by this work
```

## Verdict

```
VERDICT=V5_NOT_COMPLETE
IMPLEMENTATION_COMPLETE=NO (3 open P0: see "Open items")
LOCAL_ACCEPTANCE_COMPLETE=NO
RELEASE_CERTIFICATION_COMPLETE=NO
FEATURE_FREEZE_READY=NO
```

The V5 runtime spine exists, composes end to end and holds its invariants under
hostile tests, and N4/N5/N6/N8 are now implemented rather than partial. It is
still NOT released and standing autonomy is still off. Implementation is not
complete either: this line previously read `IMPLEMENTATION_COMPLETE=YES` with
`OPEN_P0=0` while three P0 holes were open in the tree, which was wrong and is
corrected above. What separates
implementation from release is unchanged and is not a formality: N0 has not been
accepted, no retention figure has been measured on a real model, and the soak,
Windows, egress and Fleet rows have not been run. Nothing in this branch turns
autonomy on — the objective workspace registers no background tick and mounts no
admission path, and the fairness scheduler is a pure function that production
does not yet call.

`PASS (repo-local)` above means exactly what it says: the property is
implemented and covered by hostile tests in this repository. It is not a claim
about the owner's machine, a live model, or a released system.
