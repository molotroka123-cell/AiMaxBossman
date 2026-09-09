# Required PR60 fixes: isolated semantic integration

This is a bounded convergence workstream report, **not the final release verdict**.
Base: `d23acf385f77ab5d16eebbf2112361ed271d34ce`.
Context reviewed: full PR60 head `e5ccd54c8fea94940f2ebced41bcb583076ac264`
and `CONVERGENCE_LEDGER_20260910.md`.

Runtime validation SHA: `0e7193e0ac8338b8bde23a522ab462d8e3af216d`.
The canonical release owner must rerun acceptance on the final convergence SHA.
No push, tag, merge, live model run, installed UI acceptance or final CI success
is asserted by this report.

## Input disposition

| Input | Disposition | Integrated as |
| --- | --- | --- |
| `355a582d3274ef29da345a177f0380c3b8fd4cdd` | REQUIRED. Exact matching Trader base; reproduced cross-provider/cross-instrument LONG_CANDIDATE before integration. | `c63e38014d4b44d83e0a0ca557cc0e1d87aef7d1`, then stronger identity guards `f1d310b560635be334cbc97bb92433cb67a65988` |
| `9fcf9ab5cbcea46574202a52fa3ca69de294a3f8` | REQUIRED, semantically corrected. Do not import silent capture failure or unfenced writes. | `2f9e79c94f20f9cee4f8712297e00255a4d503d4` |
| `cf020daa1ec72af90ae069b4118544ff7fe6a12d` | REQUIRED regression intent; corrected experimentally weak shutdown harness before relying on results. | `0e7193e0ac8338b8bde23a522ab462d8e3af216d` plus container-positive-control test in this report commit |
| PR60 File Intelligence feature commits | Not imported by this workstream. No FI endpoint/setup code or optional feature was added. | Deferred under the parent ledger's disposition. |
| PR60 FI router leak fix `a1e633c` | No FI router exists in the integrated changes; no weaker global router implementation was carried. | Re-evaluate only if FI is integrated. |
| Historical skip registry from `cf020da` | Not copied: it describes an older tree. | Parent must regenerate final inventory for its final SHA. |

## Findings and controls

| ID | Severity | Reproduction and root cause | Fix commit | Negative / positive controls | Status |
| --- | --- | --- | --- | --- | --- |
| PR60-IDENTITY | P1 | Baseline `analyze(Snapshot(100,100,100,source="source-a",instrument="ETH"), Snapshot(110,110,110,source="source-b",instrument="BTC"))` returned LONG_CANDIDATE, confidence0.95. Provider/metric identity and timestamp were not checked. | `c63e380` | Cross-provider, cross-instrument, absent identity, changed normalization/aggregation and stale timestamps refuse. Same identified forward series preserves existing regimes/money results. | FIXED |
| ASTRA-PR60-IDENTITY-BYPASS | P2, NEW | Upstream355a582 still advertised reclaimed levels on incompatible rows; `accepted_above`/`sweep_and_reclaim` combined different or stale observations; malformed typed fields raised AttributeError. | `f1d310b` | Eleven additional cases failed before repair. Same-source ordered history and valid level reclaim still succeed; incompatible/malformed history fails closed. | FIXED |
| PR60-PROVENANCE | P1 | Only mutable agent rows explained historical prompts, permissions and configured models; editing/deleting agent changed or erased the explanation. | `2f9e79c` | Run-start configuration survives real DB agent rename/prompt/permissions/budget edits and deletion; SQL rewriting or clearing captured JSON fails. | FIXED |
| ASTRA-PR60-PROVENANCE-FENCE | P1, NEW | Upstream9fcf9ab wrote provenance without a fence; stale worker wrote after takeover. Capture exceptions were logged with raw details, then provider dispatched and run completed. | `2f9e79c` | Real SQLite takeover rejects stale writer; injected capture failure produces failed run before any adapter call and does not leak exception canary. Valid current worker captures and proceeds. | FIXED |
| ASTRA-PR60-PROVENANCE-IDENTITY | P1, NEW | Upstream lookup ignored installed `_build.json`, accepted arbitrary BCC_BUILD_SHA, cached stale source revisions, and reader accepted bool/negative schema versions. | `2f9e79c` | Valid embedded40hex SHA wins over environment; malformed/missing manifest is NOT_CAPTURED. Dirty checkout not attributed to clean HEAD; new clean HEAD changes record. Invalid schema rejected. | FIXED |
| ASTRA-PR60-PROVENANCE-BACKFILL | P1, NEW | First NULL-to-value update could invent historical completed-run configuration. Original guard implemented only SQLite. | `2f9e79c` | SQLite terminal backfill refused; old row API stays NOT_CAPTURED. Already captured record is idempotent. Explicit RESUMED_RUN_CONFIGURATION avoids pretending resumed legacy config was original. PostgreSQL implementation has isolated live test; no server proof here. | SQLite FIXED; PostgreSQL OWNER_REQUIRED |
| ASTRA-PR60-MISSION-LIFECYCLE | P2, NEW | Mission KPI consumer never unsubscribed on cancellation. One malformed KPI delta terminated all later updates silently. Stronger stop test found one retained queue. | `0e7193e` | Real service subscribers present before stop, zero after stop; repeated same-Service start/stop does not accumulate queues. Malformed event reports refusal; next valid event changes real KPI row. | FIXED |

Provenance scope is **configuration at initial start**, or explicitly configuration
on a resumed legacy run. Configured default/fallback identities are not a claim
that every later dispatch used that model. Existing actual-call usage/model
journals remain separate. The record binds task ID, run ID, fence and prompt
digest. No plaintext provider key or system prompt is copied. Completion truth,
authorization at effect time, executor admission and finalize_task were not relaxed.

## Test integrity corrections

- Two pre-existing Trader history fixtures acquired explicit same-series identity
  and increasing timestamps. Prices and expected outcomes stayed unchanged; new
  tests require refusal for incompatible/unstated observations.
- cf020da's SlowDbLoop was not in `svc._graceful`, so never tested STOP_GRACE.
  Its fast loop never observed the stop event, and its assertion did not require
  cooperative exit. Both preconditions are now explicit.
- cf020da read `engine.pool` after dispose (a new empty pool), and its helper
  returned zero on any exception. Tests now retain and inspect the original pool.
- Real SQLite execution blocked inside a bounded user function is tested separately
  from merely holding a session across asyncio.sleep. STOP_GRACE remains2seconds;
  no production timeout was increased.
- cf020da manually unsubscribed its test queue before calling stop. The corrected
  test checks actual service-owned queues and exposed the mission bug above.
- Its test named “stopping mid run does not duplicate effect” had never dispatched
  an effect. It is accurately named stopped-queued-run restart/claim test, with
  stronger persisted-row equality and claim refusal. Existing actual post-effect
  crash/reconciliation regressions are retained and ran in the validation set.
- Docker discovery uses bounded argv, not unbounded `os.system`. The container
  containment test requires a successful inside-container canary read before
  claiming that a prohibited absolute path was denied. A missing image or dead
  container cannot pass as containment.
- No failing assertion was weakened; no regression was deleted, xfailed or skipped.
  New PostgreSQL live proof and existing Docker boundaries explicitly remain unrun.

## Validation

On the clean runtime validation SHA above: Linux, Python3.12, **160 passed,
3 skipped in34.28seconds**. Explicit existing Command Center pytest config is
required when mixing its tests with root tests:

```bash
PYTHONPATH=.:bossman-core:command-center python -m pytest -c command-center/pyproject.toml \
  tests/test_trader_apprentice.py \
  command-center/tests/test_run_provenance.py \
  command-center/tests/test_run_provenance_postgres.py \
  command-center/tests/test_executor_admission.py \
  command-center/tests/test_p0_completion_truth.py \
  command-center/tests/test_p0_review_deadlock.py \
  command-center/tests/test_fence_fl01.py \
  command-center/tests/test_finalize_gate.py \
  command-center/tests/test_stop_grace_lifecycle.py \
  command-center/tests/test_secrem_f009_terminal.py \
  command-center/tests/test_feat_missions.py \
  command-center/tests/test_services_lifecycle.py \
  command-center/tests/test_engine_lifecycle_drain.py \
  command-center/tests/test_fable_crash_after_effect.py \
  command-center/tests/test_engine_stop.py -q
```

An earlier mixed invocation omitted `-c`, selected the common root, and failed
because Command Center's `asyncio_mode="auto"` was absent. It is not counted as
passing; the corrected command above reran the unchanged tests. No pytest config
was changed. Compile and whitespace checks passed.

## External boundaries

| Boundary | Status | Deterministic owner action |
| --- | --- | --- |
| Live PostgreSQL immutable guard | OWNER_REQUIRED | Set `BCC_TEST_POSTGRES_URL` to a permitted asyncpg test DSN, run `python -m pytest command-center/tests/test_run_provenance_postgres.py -q`. Test creates/drops one random schema; no existing owner table is modified. PASS requires first capture, identical update, edit/erase refusal, old-run backfill refusal and independent reread. |
| Docker absolute-path containment | OWNER_REQUIRED | With Docker daemon and `python:3.12-slim` available, run `python -m pytest command-center/tests/test_secrem_f009_terminal.py -q`. PASS requires genuine container in-scope read and failed outside read with no token returned. |
| Windows cancellation / file locks | OWNER_REQUIRED | Run the same lifecycle/AP001 suites on the supported Windows CI/owner host. Linux timings do not prove Windows driver behavior. |
| Genuine model / installed browser owner workflow | Not measured by this workstream | Parent installed acceptance and `python -m bcc.owner_acceptance` remain required. Fixture adapter regressions are not model-live success. |

The pre-existing PostgreSQL **terminal-status** guard (separate from provenance)
was also reported to the parent: it logs a full database URL and does not install
an equivalent non-SQLite trigger. This report does not claim that independent
boundary fixed by the provenance implementation.
