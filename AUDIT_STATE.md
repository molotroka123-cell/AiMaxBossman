# AUDIT_STATE — AUDIT-ONLY-001 (independent verification complete)

BASE_SHA=9613e459b7ee28428a34829b63350acd4bb327b5
CODE_COMPLETE_SHA=0e8960ae8443e2247d78b1c5216e3d2cdaaa6df4 (GitHub CI 4/4 SUCCESS)
Branch claude/bossman-control-v03-43igbk

## Verdicts (ours, after local reproduction — NOT Fable's)

| finding | Fable | reproduced | status |
|---|---|---|---|
| F1 receipt lost update | P0 | P3 | CONFIRMED (code), exploit REFUTED — FIXED |
| F2 abandon race | P1 | P2 | CONFIRMED as a different defect — FIXED |
| F3 silent rollback | P0 | P1 | error masking; partial-write story DISPROVED — FIXED |
| F4 no security baseline | P1 | P3 | CONFIRMED but unreachable — FIXED narrowly |
| F5 cross-corpus | P1 | P2 | PARTIAL (premise half wrong) — 9/11 fixed, 2 xfail(strict) |
| F6 cache false savings | P2 | P2 | CONFIRMED at a DIFFERENT location — FIXED |

Details and every disagreement with Fable: `docs/mission/AUDIT-ONLY-001/CORRECTION_REPORT.md`.
The Fable audit log itself is unmodified.

## Commits

| commit | content |
|---|---|
| cafe4c8 | benchmark: 16 REAL_SANDBOX capability cases + IQ v2 math |
| ea2d03b | durable: immutable completion, abandon tombstone, poisoned store (F1/F2/F3) |
| 99e3a88 | learning: mandatory and scope-bound promotion evidence (F4/F5) |
| d033128 | cache: savings only from provider evidence (F6) |
| 8520bf0 | docs: correction report, state, GLM red-team handoff |
| 0e8960a | benchmark: decoy credentials assembled at runtime (secret-scan hygiene) |

Each independently revertable.

## Capability coverage

BEFORE 2/18 measured REAL_SANDBOX (persistence, recovery).
AFTER **18/18** — every required capability has an executable case that imports a
real production class, drives its real call path, records observed facts, and is
judged by an external verifier that refuses any case lacking both a positive and a
negative check.

nightly gate: **READY** (SystemIQ MEASURED at full component weight).
release gate: **NO-GO** — honestly. LIVE evidence still needs an owner-approved
paid run; no such call was made.

## Tests

- bossman-core full suite: **1640 passed, 7 failed, 57 skipped, 2 xfailed**.
- All 7 failures are host-specific and proven so:
  - 6 × `OSError WinError 1314` (creating a symlink needs Developer Mode/admin on
    this Windows host) in test_pass3_deep_fix_p0 and test_secrem_sibling_sweep;
  - 1 × `OSError WinError 193` in test_teacher_iso_001, reproduced **identically at
    the untouched base commit 9613e459** in a clean worktree.
- `tests/audit001`: all green (2 documented xfail(strict=True)).
- compileall clean; secret scan clean; `git diff --check` clean.

## Budget

No paid API call in this session. Fable not re-invoked. Ledger unchanged at $0.035529.

## Open

Nothing from this audit. The three items below were closed on
`claude/v4-v5-local-models-optimization-ya3utu`; `tests/audit001` now runs
**71 passed, 0 xfail** (it was 69 passed with 2 `xfail(strict=True)`).

| item | closed by | evidence |
|---|---|---|
| AUDIT001-F5-REPLAY (P2) | `bossman/learning_guard/evidence_ledger.py` — a measurement is single-use, keyed on the evidence alone and consumed by candidate id + version; idempotent for a retry of the same version; `DurableEvidenceLedger` keeps the refusal across restarts and processes (`BOSSMAN_EVIDENCE_LEDGER_PATH`) | `tests/audit001/test_f5_cross_corpus.py::test_ab_evidence_cannot_be_replayed_for_a_newer_candidate_version` (xfail marker removed), `tests/test_learning_evidence_ledger.py` (12) |
| AUDIT001-F5-PROVENANCE (P2) | `autonomy_trainer.corpus_fingerprint()` always yields a corpus identity and both SecuritySnapshots must carry it; the measurement producers emit it and `SkillPromoter.corpus_ref` gives callers the canonical value | `tests/audit001/test_f5_cross_corpus.py::test_incomparable_security_snapshots_do_not_pass_the_gate` (xfail marker removed) |
| `ObservationLog.record` idempotency | optional opaque `CacheObservation.observation_id`; a repeat of a recorded id is counted once and the dedup window equals the retention window. An observation without an id is still counted rather than merged by field similarity | `tests/test_cache_observation.py` (5 new) |

`POST_FREEZE_BACKLOG` item "DirectApiBudget multi-reservation bookkeeping" was
re-checked and is already closed by `269d123`: `_hold_total()` sums
`worst_case_usd` per RESERVED/RECONCILING record, which is the per-record hold
map the entry asked for (`bossman-core/tests/test_fable_budget.py`).

READY_FOR_GLM_RED_TEAM = YES — see `RED_TEAM_HANDOFF.md`.
READY_FOR_PRODUCTION = NO-GO (release tier still lacks LIVE evidence).
