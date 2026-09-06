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
| N4 — conflicts, cooldown, quotas, anti-oscillation | `PARTIAL` | Conflict keys, cooldown and quotas are enforced through ports and covered by H06/H07. Fairness and bounded aging across many objectives are `NOT_RUN`. |
| N5 — measured templates/skills, no privilege expansion | `PARTIAL` | `objective_improvement.py` refuses trust-critical promotion and permission widening structurally. Measured skill promotion is `NOT_RUN`. |
| N6 — objective workspace and owner control | `PARTIAL` | Read-only inspector plus explicit owner lifecycle/enrollment actions. The creation wizard and the Evidence/Revisions detail tabs are `NOT_RUN`. |
| N7 — H01–H10, security, cost, durability, soak | `PARTIAL` | H01–H10 pass as an in-process suite. 24-hour soak, Windows and real-FFmpeg rows are `NOT_RUN`. |
| N8 — migration, canary, rollback rehearsal, exact-SHA | `PARTIAL` | Migration step 5 (read-only inspector) is in place and `prepare_rollback` returns the ordering as inspectable data. Canary admission and a live rollback rehearsal are `NOT_RUN`. |

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
OPEN_P0=0 (repo-local)
OPEN_P1=0 (repo-local)
FALSE_SUCCESS=0 observed
DUPLICATE_IRREVERSIBLE_EFFECTS=0 observed
PRIVATE_EGRESS=NOT_RUN
```

## Intelligence preservation

```
RAW=NOT_RUN
SYSTEM=NOT_RUN
CONTEXT=NOT_RUN
FULL=NOT_RUN
CORE_RETENTION=NOT_RUN
INTELLIGENCE_GATE=INSUFFICIENT_EVIDENCE
```

Diagnosed from the workflow log rather than assumed (run 34043712445,
job 101514903926): `INTELLIGENCE_PRESERVATION=INSUFFICIENT_EVIDENCE / Missing
docs/benchmark/intelligence-preservation-current.json`, exit code 2. The red X
is **the absence of a measurement**, not a detected regression and not
infrastructure noise. The gate now says which of the two it is in its job
summary and prints the command that produces the evidence.

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
INTELLIGENCE=PASS (existing gate CONTRACT suite; the MEASURED gate is
              INSUFFICIENT_EVIDENCE — no retention run exists, see below)
CORE=PASS (2489 passed, 36 skipped, 0 failed — measured 2026-09-06 on
      claude/v4-v5-local-models-optimization-ya3utu, Linux/py3.11)
COMMAND_CENTER=PASS_WITH_ENVIRONMENT_GAPS (1790 passed, 126 skipped, 35 failed:
      31 need a local FFmpeg build, 2 need the optional `mcp` extra, and 2
      web-designer Playwright cases reproduce identically at 45d9004 and so
      predate this work. Nothing attributable to this branch.)
WINDOWS=NOT_RUN
REMOTE_FLEET=EXPERIMENTAL — unqualified, unchanged by this work
```

The two suite rows above were `NOT_RUN` when this scorecard was written; they are
now measured. `docs/v4/ACCEPTANCE_MATRIX_2026-09-06.md` carries the per-cause
breakdown and the exact commands.

## Verdict

```
VERDICT=V5_NOT_COMPLETE
```

The V5 runtime spine exists, composes end to end and holds its invariants under
hostile tests. It is not released and standing autonomy is not activated: N0 has
not been accepted, the intelligence retention measurement has not been taken,
and the soak, Windows, egress and Fleet rows have not been run. Nothing in this
branch turns autonomy on — the objective workspace registers no background tick
and mounts no admission path.
