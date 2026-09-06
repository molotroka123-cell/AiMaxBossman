# Steward objective contract foundation

Scope: pure, opt-in imports in `bossman_shared/objective_spec.py`. No runtime,
observer, database migration, account enrollment, scheduler, network call or
policy authority is added. V5 remains `V5_NOT_COMPLETE`; N0 stays blocked.

Implemented:

- Immutable canonical ObjectiveSpec with exact schema, finite nonnegative caps,
  owner/scope identity, trusted predecessor requirement and digest round trips.
- Typed deterministic predicates and explicit enrolled-source references;
  stale, missing, duplicate and wrongly scoped/revisioned evidence is UNKNOWN.
- Pure lifecycle transition validation: expiry is inclusive, revocation sticky,
  terminal state cannot reactivate, and owner/scope must match. Authentication
  and atomic persistence are intentionally required at the calling service.
- Deterministic proposal projection: content-bound observation fingerprints and
  objective revision dedup key, freshness/expiry horizon, cumulative usage and
  cooldown checks. Reordering events or changing trigger does not change the
  proposal key for the same evidence. Mutation of source content does.
- Projection constructors cannot request admission authority. Import is DRAFT;
  a proposal is never an execution grant or a verified healthy condition.

Targeted verification:

```sh
PYTHONPATH=$PWD:$PWD/bossman-core:$PWD/command-center \
  /tmp/bossman-epoch4-venv/bin/python -m pytest -q \
  tests/test_epoch5_objective_spec.py tests/test_epoch4_mission_ir.py
```

Result: **226 passed** (156 objective tests, 70 Mission IR compatibility tests).
This is fixture/contract evidence, not product acceptance or performance proof.

Red-team cases cover cross-owner/scope substitution, old spec and source
revisions, future/stale observation time, duplicate IDs across different
sources, ambiguous source events, invalid numeric types/nonfinite/unbounded
integers, terminal resurrection, expired activation, JSON duplicate fields,
unknown-dominant aggregation, cumulative caps, cooldown boundary, revocation,
stop flags, changed evidence, repeated triggers, malformed ledger snapshots,
and attempts to construct an admission grant from a projection.

Integration still required: authenticate observations and caller identity using
existing services; resolve fresh canonical lifecycle/enrollment and stop flags;
retain cumulative/reserved usage across revisions; atomically dedup and reserve
missions/cost/conflicts; revalidate lifecycle, freshness, revisions and current
grants at admission and effect boundaries; convert into ordinary Mission IR;
independently verify effects and reobserve health; migration/rollback, H01–H10,
Windows attestations, soak and performance benchmarks. A zero remaining cost
budget may yield a local zero-cost proposal but never permits spending without
an authoritative reservation. The projection itself mutates no ledger.
