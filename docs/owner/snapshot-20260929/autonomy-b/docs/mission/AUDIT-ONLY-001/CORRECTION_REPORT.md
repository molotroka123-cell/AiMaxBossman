# AUDIT-ONLY-001 — correction report

The Fable audit log (`fable_consolidated.json`) is left **unmodified**. This file
records where independent reproduction disagreed with it.

Method: every finding was reproduced locally before any production code changed.
RED tests live in `bossman-core/tests/audit001/`. Fable was treated as a source of
hypotheses; severity, exploit and fix plan were re-derived from the code and from
actual production call sites.

## Corrections

**F1 — severity P0 → P3.** The code claim is true and reproducible (a second
`complete_side_effect` overwrote the receipt, across processes and across a
restart). Both supporting arguments are false:

- *"Two processes claim same side_effect_id (race on claim check)"* — impossible.
  `claim_side_effect` runs its SELECT+INSERT inside `_tx`, which issues
  `BEGIN IMMEDIATE`, and `effects.id` is PRIMARY KEY. A multiprocess test confirms
  the second claim always loses.
- *"Can cause budget double-spend if first receipt had refund"* — there is no
  refund concept anywhere in the repository (`rg refund` has zero production hits)
  and nothing reads `effects.receipt` for accounting.

Both production callers complete only on their own successful claim, so the gap
was unreachable. Fixed regardless, as defence in depth.

**F2 — severity P1 → P2, and a different defect.** Fable's mechanism ("orchestrator
timeout triggers abandon while executor thread still running") has zero production
triggers: no watchdog, no timeout thread, and all three callers are same-thread.
The genuine risk is that `DELETE` re-opened the `side_effect_id`, allowing a retry
to repeat an external effect that may already have happened.

**F3 — severity P0 → P1, and the exploit is disproved.** Fable's P0 rested entirely
on a partial write bypassing the nonce-once guarantee. A genuine `SQLITE_FULL` and
a genuine constraint violation both roll back atomically, leaving
`integrity_check == ok` and no partial row — SQLite owns atomicity because every
`_tx` body runs inside a real transaction. The real defect is narrower: the
rollback failure was masked, leaving the caller contract undefined.

**F4 — severity P1 → P3, and the fix plan was rejected.** The code claim is true and
in fact worse than described: a half-supplied pair carrying a *measured breach* in
`after` was silently discarded. But no production caller can reach it — every route
into `advance` passes both snapshots as mandatory keyword arguments, behind an
OFF-by-default flag. Fable's plan ("remove Optional, make both mandatory, raise")
was not applied verbatim because it also breaks two transitions that confer no
authority, for zero security gain. The gate is enforced where authority is granted.

**F5 — severity P1 → P2, premise half wrong.** Fable asserts "no corpus_id/domain_id
field in Candidate or SecuritySnapshot". The binding already exists as
`AutonomyCandidate.scope`, populated by both production callers. No redundant
`corpus_id` field was added. The real gap is that the scope was decorative —
nothing compared it. Two sub-expectations remain open as `xfail(strict=True)`:
`AUDIT001-F5-REPLAY` and `AUDIT001-F5-PROVENANCE`.

**F6 — confirmed, but the location is wrong.** Fable's answer was truncated by
`max_tokens` (`stop_reason: "max_tokens"`), so its evidence was incomplete.
`bossman_shared/cache_intelligence.py`, the module it names, computes no money at
all — `cache_advice` already refuses to report savings without provider evidence,
and `cache_observation.py` is honest too. Neither was modified. The fabricated
dollar figure is produced on a path that never calls `cost_pair`:
`gateway/app.py::_cache_economics` → `telemetry.py::end_cache` → `/metrics` →
`api.py` → `ui/index.html`. Four sub-defects were reproduced and fixed there.

## Summary

| finding | Fable | reproduced | verdict |
|---|---|---|---|
| F1 | P0 | P3 | code claim true, exploit false, fixed |
| F2 | P1 | P2 | different mechanism, fixed |
| F3 | P0 | P1 | error masking, not partial write, fixed |
| F4 | P1 | P3 | true but unreachable, fixed narrowly |
| F5 | P1 | P2 | premise half wrong, partially fixed |
| F6 | P2 | P2 | right defect, wrong location, fixed |

Zero findings were accepted at Fable's stated severity. Zero were dismissed
outright: all six describe something real.

## Cost

No paid API call was made during this verification. Fable was not re-invoked.
The AUDIT-ONLY-001 ledger is unchanged at `$0.035529`.
