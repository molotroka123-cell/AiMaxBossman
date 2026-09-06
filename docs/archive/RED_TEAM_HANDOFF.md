# RED_TEAM_HANDOFF — for the next GLM cybersecurity red-team run

Produced by the independent verification of Fable audit **AUDIT-ONLY-001**.
Fable was treated as a source of hypotheses, not as a verifier. Every finding was
reproduced locally before any code was changed; every severity below is ours, not
Fable's.

## Base

- START_REMOTE_SHA `9613e459b7ee28428a34829b63350acd4bb327b5`
- CODE_COMPLETE_SHA `0e8960ae8443e2247d78b1c5216e3d2cdaaa6df4` — all 4 GitHub workflows green
  (root-ci, Bossman Core CI, Command Center CI, Bossman V2 Auto-Repair). This commit adds
  only this line, so it is the same tree plus documentation.
- Branch `claude/bossman-control-v03-43igbk`
- No paid API call was made in this session. No Fable re-invocation. Budget ledger untouched.

## Confirmed findings (all fixed unless noted)

| id | Fable severity | our severity | verdict | why the severity moved |
|---|---|---|---|---|
| F1 receipt lost update | P0 | **P3** | CONFIRMED (code), exploit REFUTED | unreachable: both callers complete only on their own successful claim; `claim` is atomic (`BEGIN IMMEDIATE` + PRIMARY KEY) so a double claim is impossible; the claimed "refund double-spend" channel does not exist anywhere in the repo |
| F2 abandon race | P1 | **P2** | CONFIRMED as a *different* defect | Fable's mechanism (a watchdog abandoning an in-flight claim) has zero production triggers; the real risk is that `DELETE` re-opened the id for a replay |
| F3 silent rollback | P0 | **P1** | CONFIRMED as error masking; partial-write story DISPROVED | a real `SQLITE_FULL` and a real constraint violation both roll back atomically with `integrity_check == ok`; the defect is the swallowed rollback failure and the undefined caller contract |
| F4 no security baseline | P1 | **P3** | CONFIRMED (code), unreachable | every production route into `advance` passes both snapshots as mandatory keyword args, behind an OFF-by-default flag |
| F5 cross-corpus promotion | P1 | **P2** | PARTIAL — premise half wrong | the binding already existed as `AutonomyCandidate.scope`; the real gap was that nothing compared it |
| F6 cache false savings | P2 | **P2** | CONFIRMED at a **different location** | `cache_intelligence.py` computes no money; the fabricated dollar figure came from `gateway/app.py` + `gateway/telemetry.py` |

Two F5 expectations remain open and are `xfail(strict=True)` with the reason inline:

- `AUDIT001-F5-REPLAY` (P2) — refusing replay of byte-identical A/B evidence to a
  newer candidate version needs a durable single-use evidence ledger. No stateless
  predicate can separate the two calls.
- `AUDIT001-F5-PROVENANCE` (P2) — refusing two *unidentified* snapshots requires
  provenance to become mandatory on every `SecuritySnapshot` producer.

`strict=True` means the day either is implemented, the run fails until the marker
is removed.

## Changed trust boundaries and new invariants

**Durable safety store** (`bossman/apprentice/durable.py`) — explicit state machine,
documented in the module docstring:

```
CLAIMED -> COMPLETE | ABANDONED | RECONCILING
RECONCILING -> COMPLETE | FAILED_FINAL
COMPLETE, ABANDONED, FAILED_FINAL are terminal
```

- every transition is a conditional atomic `UPDATE` naming its expected current
  state, followed by a rowcount check; last-write-wins is impossible;
- a stored receipt is **immutable** after COMPLETE;
- `abandon` no longer deletes: it writes an ABANDONED tombstone so the
  `side_effect_id` cannot be re-opened for a replay; deletion is a separate
  TTL-guarded GC;
- a failed rollback marks the store **POISONED**, closes the connection, chains
  both exceptions and refuses further operations until an explicit reopen with an
  integrity check. Fail-closed. No SQL values are logged.

**Promotion** (`bossman/learning_guard/`) — the security gate is enforced where
authority is actually granted: a half-supplied snapshot pair is refused at every
stage, `SHADOW -> VERIFIED` requires a complete pair, and `promote()` requires
`candidate.security_proven`, so `OWNER_PROMOTED` cannot be granted on a VERIFIED
label that was never earned. `MissingSecurityEvidence` subclasses the existing
`SecurityRegression`, so existing handlers already quarantine it. Scope comparison
is opt-in by declaration: a name-only scope behaves as before, a scope declaring
`dataset_hash` / `policy_version` / `corpus_ref` demands matching evidence.

**Cache savings** (`bossman/gateway/{app,telemetry}.py`, `bossman/api.py`, both UIs) —
a savings figure may only be claimed from provider-reported cache tokens on an
eligible request. An unpriced cache bucket is charged at the ordinary input price
(an upper bound on cost, never an overstated saving). Both UIs fail closed and
render "not claimable" without `savings_basis == "provider-observed"`. The
conservative ratio fallback is deliberately retained for budget *reservation*,
where assuming a write costs more is the fail-closed choice.

**Benchmark** (`bossman/benchmark/`) — capability coverage is now 18/18 measured
REAL_SANDBOX. A case records observed `(actual, expected)` facts and never sets
`verified`; `sandbox_row.verify_row` computes the verdict and refuses any case
lacking BOTH a positive and a negative check, and the verdict is recomputed at the
runtime process boundary. Capability and evidence class are assigned by the runner
from the manifest; a child claiming a different capability fails the case.

## Migration / schema

- `effects` gains the ABANDONED/RECONCILING/FAILED_FINAL states and the columns
  they need, added with defaults inside a single transaction. An existing sqlite
  file written by the previous code still opens; `claimed` rows keep their meaning.
- Three learning-guard dataclass fields added with defaults
  (`Candidate.security_proven`, `ABResult.scope_ref`, `SecuritySnapshot.scope_ref`);
  no schema file changed, nothing persisted, nothing deleted.
- `/metrics prompt_cache` and the command-center economics payload are purely
  additive; no key removed or renamed.
- Rollback for any of the above is a plain `git revert` of the single commit.

## What to attack next (suggested for the GLM run)

1. **Multiprocess races** on the new conditional updates — especially
   `RECONCILING -> COMPLETE` and the GC vs a late receipt.
2. **Crash consistency** — kill a process between the actuator's external effect
   and `complete_side_effect`; confirm the tombstone prevents a replay.
3. **SQLite corruption / poisoning** — can a poisoned store be resurrected without
   the integrity check? Is any caller catching the poison exception too broadly?
4. **Replay after restart** — nonce-once, approval nonces, and the new ABANDONED
   tombstone across a real restart.
5. **Forged promotion evidence** — `security_proven` is an in-process bool; can a
   hand-built or deserialised Candidate set it?
6. **Cross-corpus poisoning** — the two open xfails are the known holes; look for
   others (scope declared but evidence scope-less).
7. **Cache savings manipulation** — can a hostile upstream body inflate
   `cached_tokens` and thereby the published saving?
8. **Manifest / dataset replacement** — MAC pinning is opt-in and OFF by default.
9. **Approval and receipt binding** — receipt `action_id` / `action_type` /
   `observed_at` skew, and approvals bound to another task.
10. **Fail-open recovery** — every `except` added in this change set; confirm none
    of them turns a refusal into a pass.

## Commands

```bash
cd bossman-core && python -m pytest --basetemp=C:/tmp/rt -p no:cacheprovider -q tests/audit001
```

```bash
cd bossman-core && python -m bossman.benchmark run --tier nightly --output /tmp/bench
```

```bash
cd bossman-core && python -c "from bossman.benchmark.sandbox_runtime import CASES; [print(c, CASES[c](20260902)['verified']) for c in sorted(CASES)]"
```

## Rollback

| commit | reverts |
|---|---|
| `cafe4c8` | benchmark capability harness + IQ v2 math |
| `ea2d03b` | durable store F1/F2/F3 |
| `99e3a88` | promotion F4/F5 |
| `d033128` | cache savings F6 |
| `0e8960a` | decoy credentials assembled at runtime (secret-scan hygiene) |

Each is independently revertable.

## Known host-specific failure (not a defect)

`tests/test_teacher_isolation.py::test_teacher_iso_001_teacher_sees_only_the_bundle`
fails on this Windows host with `OSError: [WinError 193] %1 is not a valid Win32
application` when it shells out. Verified to fail **identically at the untouched
base commit `9613e459`** in a clean worktree, so it is environmental and
pre-existing. It passes in CI.
