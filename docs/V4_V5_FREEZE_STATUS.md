# V4/V5 Feature Freeze — Certification Status

**Document status:** LIVE certification record. This is the authoritative freeze ledger.
**Branch under certification:** `claude/v4-v5-freeze-p0-gates-l56exm`
**FEATURE_FREEZE_READY:** **NO**
**OPEN_P0:** **3**

> This document records gate state honestly. A gate is never marked PASS without naming
> its evidence. Anything not independently verifiable from this repo is marked
> `UNVERIFIED` rather than assumed.

---

## 1. SHA Discipline

Three distinct SHAs exist in this program. They are **never** interchangeable and must
**never** be conflated in any evidence claim.

| Field | Value | Meaning |
|---|---|---|
| `FINAL_SOURCE_SHA` | `83a2a77be66a7fe80d201279b9b8c92e5ffd8926` | The **real source head** being certified. PR #37, branch `claude/v5-closure-at-reconcile-xdh12f`. This is the only SHA that exact-source certification may reference. |
| `TESTED_PR_MERGE_SHA` | `7de858ddb9bd18e349f0f02593591ce5501c1a72` | GitHub's **synthetic merge commit** — a throwaway merge of the PR head with its base, created by GitHub for CI. **Not the source.** |
| `TESTED_SHA` | Per-run value | Whatever a given CI run **actually checked out**. Must be captured verbatim from that run, not inferred. |

### Verification of the synthetic merge object

`TESTED_PR_MERGE_SHA` **does not exist in the local clone**. Verified:

```
$ git cat-file -t 7de858ddb9bd18e349f0f02593591ce5501c1a72
fatal: git cat-file: could not get object info
```

This is expected and confirms the object is a GitHub-side synthetic merge, not a commit
on any real branch of this repository. It carries a tree that no developer ever authored
and that no branch points at.

### THE RULE

> **Exact-source certification must NEVER be claimed from a synthetic PR merge checkout.**
>
> Any evidence claim — CI run, report, scorecard, gate row — **must record both SHAs side
> by side**: the `FINAL_SOURCE_SHA` being certified and the `TESTED_SHA` the run actually
> checked out. If a run checked out `TESTED_PR_MERGE_SHA`, it is **portability / integration
> evidence against the merge result**, not exact-source certification of `83a2a77`.
>
> Editing, copying, or backfilling an `evaluated_sha` field in any report so that it appears
> to match `FINAL_SOURCE_SHA` is **forbidden** and invalidates the certification record.

---

## 2. Gate Table

**State vocabulary:** `PASS` · `FAIL_CLOSED` · `IN_PROGRESS` · `NOT_RUN` · `INSUFFICIENT_EVIDENCE` · `CLOSED_DO_NOT_REDO`

| Gate | State | Evidence | Blocking? |
|---|---|---|---|
| `FINAL_SOURCE_SHA` | PASS | `83a2a77be66a7fe80d201279b9b8c92e5ffd8926` confirmed as local head of `claude/v4-v5-freeze-p0-gates-l56exm`; source head of PR #37 / `claude/v5-closure-at-reconcile-xdh12f`. | No (identity record) |
| `TESTED_PR_MERGE_SHA` | INSUFFICIENT_EVIDENCE | `7de858ddb9bd18e349f0f02593591ce5501c1a72` — synthetic GitHub merge; `git cat-file -t` fails locally (object absent). Valid as merge-result evidence only; **cannot** stand as exact-source certification of `83a2a77`. | Yes — blocks any exact-source PASS claim sourced from it |
| `OPEN_P0` | FAIL_CLOSED | Count = **3**. See §3. `FEATURE_FREEZE_READY = NO`. | **Yes** |
| `AT-01` | IN_PROGRESS | UnknownEffect handling — P0 #3, actively being implemented on this branch. No completed acceptance evidence yet. | **Yes** |
| `AT-03` | CLOSED_DO_NOT_REDO | Effect-boundary freshness. Closed; **no new counterexamples received**. Reopen only on a reproduced counterexample. | No |
| `CANARY_PRODUCTION_CALLER` | IN_PROGRESS | P0 #1 — production caller wiring under implementation on this branch. No end-to-end production-path evidence yet. | **Yes** |
| `SATISFIED_EVIDENCE_GATE` | IN_PROGRESS | P0 #2 — real resolver evidence for `SATISFIED` under implementation on this branch. Synthetic/placeholder evidence does not satisfy this gate. | **Yes** |
| `N4` | INSUFFICIENT_EVIDENCE | Strong repo-local tests exist; **no actual scheduler acceptance** on a real machine. Repo-local tests are not acceptance. | **Yes** |
| `N5` | INSUFFICIENT_EVIDENCE | Strong repo-local tests exist; **no actual model acceptance** run. | **Yes** |
| `N6` | INSUFFICIENT_EVIDENCE | Strong repo-local tests exist; **no actual owner-browser acceptance** run. | **Yes** |
| `N8` | INSUFFICIENT_EVIDENCE | Strong repo-local tests exist; **no actual owner-rollback acceptance** run. | **Yes** |
| `VIDEO_CFR` | CLOSED_DO_NOT_REDO | Earlier failures were a **test-harness defect**, not a product bug. Fixed by `51825b2665bcbdf056adfbaec748d9838ddd5af7`. Full media step: **335 passed / 11 skipped**. | No |
| `VIDEO_PLAYBACK` | CLOSED_DO_NOT_REDO | Same fix: the old stand measured the library `/media/...` **source** video instead of the rendered `/exports/.../file` **preview**. Corrected stand waits for the real render output and carries a **negative control**. Reopen **only** on a NEW product-level failure produced by the corrected harness. | No |
| `HUMAN_SPEED` | CLOSED_DO_NOT_REDO | LATENCY_CONTRACT. `83a2a77` is **diagnostic only** — it prints the complete verdict pytest was eliding; it changes **no thresholds** and filters **no samples**. 10 ms target stands. Contract distinguishes genuine storage-distribution degradation from a single scheduler stall, and carries negative controls that must reject a genuinely slowed storage path. Reopen only on a reproduced counterexample that breaks the contract's stated invariants. | No |
| `INTELLIGENCE_PRESERVATION` | FAIL_CLOSED | **Expected fail-closed blocker, NOT a regression.** The FULL measurement method was fixed, but **no real model result exists** and **no current same-model measured evidence exists**. The only top-level red in the current CI wave. | **Yes** |
| `WINDOWS_ACCEPTANCE` | NOT_RUN | No owner-machine Windows acceptance has been performed. GitHub Windows-path checks are **PORTABILITY evidence, not owner-machine acceptance**. | **Yes** |
| `LOCAL_MODEL_ACCEPTANCE` | NOT_RUN | No local-model acceptance run exists. | **Yes** |
| `CANARY_ROLLBACK` | INSUFFICIENT_EVIDENCE | CAN-001 / CAN-003 are closed, but owner-rollback acceptance (see `N8`) has not been executed on a real machine. | **Yes** |
| `REMAINING_RELEASE_BLOCKERS` | FAIL_CLOSED | 3 open P0s + `INTELLIGENCE_PRESERVATION` fail-closed + soak / real same-model retention / Windows owner acceptance / local-model acceptance all NOT_RUN or INSUFFICIENT_EVIDENCE. | **Yes** |

---

## 3. The Three Open P0s

All three are **IN_PROGRESS on this branch**. None is closed.

1. **Canary production caller** — `CANARY_PRODUCTION_CALLER`
2. **Real resolver evidence for `SATISFIED`** — `SATISFIED_EVIDENCE_GATE`
3. **AT-01 `UnknownEffect`** — `AT-01`

`OPEN_P0 = 3` → `FEATURE_FREEZE_READY = NO`.

---

## 4. CI State — Current Wave

All of the following **PASSED** on the current wave:

- Core
- Command Center
- root
- Editors
- Fable media / Fleet
- ASTRA
- V2 Auto-Repair
- internal benchmark
- Solana

**The only top-level red is `Intelligence Preservation`.**

> Recording discipline: each of the above is evidence against the `TESTED_SHA` that run
> actually checked out. Where that was the synthetic merge, it is merge-result evidence —
> record both SHAs before citing it in any certification claim.

---

## 5. Intelligence Preservation — Fail-Closed Detail

- State: **FAIL_CLOSED**. This is the **expected** behavior, **not a regression**.
- No current **same-model measured evidence** exists.
- The **FULL measurement method was fixed**, but **no real model result exists** against it.
- A **mathematically capable PASS** requires **≥ 189 paired samples per required metric**.
  Anything below that threshold cannot produce a defensible PASS and must not be reported as one.

### Forbidden

> **Fabricating, copying, or editing a report's `evaluated_sha` to make this gate green is
> FORBIDDEN.** Doing so is falsification of the certification record, not a shortcut.
> The only route to PASS is a real, same-model, ≥ 189-paired-sample measurement whose
> `evaluated_sha` was produced by the run itself.

---

## 6. Video — Harness Defect, Now Closed

- The three earlier playback failures were a **TEST-HARNESS defect**, not a product bug.
- Fix commit: `51825b2665bcbdf056adfbaec748d9838ddd5af7`.
- Defect: the stand measured the library `/media/...` **source** video instead of the
  rendered `/exports/.../file` **preview**.
- Correction: the stand now **waits for the real render output** and carries a
  **negative control**.
- Full media step result: **335 passed / 11 skipped**.
- Therefore `VIDEO_CFR` and `VIDEO_PLAYBACK` are **CLOSED_DO_NOT_REDO**.
  **Reopen only on a NEW product-level failure produced by the corrected harness.**

---

## 7. Human Speed / LATENCY_CONTRACT

- Commit `83a2a77` is **DIAGNOSTIC ONLY**: it prints the complete `LATENCY_CONTRACT`
  verdict that pytest was eliding. It **changes no thresholds** and **filters no samples**.
- The **10 ms target stands**.
- Raw metrics that **must be preserved** and never aggregated away:
  `p50`, `p95`, `p100`, `max`, `over_limit`, `stalls`, `floor`.
- **Prohibited:** retry-until-green, `skip`, `xfail`.
- The contract distinguishes **genuine storage-distribution degradation** from a
  **single scheduler stall**, and carries **negative controls** that must reject a
  genuinely slowed storage path.
- State: **CLOSED** unless a **reproduced counterexample** breaks its stated invariants.

---

## 8. Closed — DO NOT REDO

These are settled. Do not re-run, re-litigate, or reopen without a reproduced counterexample.

| Item | Note |
|---|---|
| `AT-03` | Effect-boundary freshness. No new counterexamples received. |
| `AF-03` | OpenRouter provider isolation. |
| `AF-04` | Production FULL intelligence methodology. |
| `CAN-001` | Closed. |
| `CAN-003` | Closed. |
| `PROM-001` | Closed. |
| `PROM-002` | Closed. |
| `PROM-003` | Closed. |
| `VIDEO_CFR` / `VIDEO_PLAYBACK` | See §6. |
| `HUMAN_SPEED` | See §7. |

---

## 9. Not Run / Insufficient Evidence

| Item | State | Why |
|---|---|---|
| `N4` scheduler acceptance | INSUFFICIENT_EVIDENCE | Repo-local tests only |
| `N5` model acceptance | INSUFFICIENT_EVIDENCE | Repo-local tests only |
| `N6` owner-browser acceptance | INSUFFICIENT_EVIDENCE | Repo-local tests only |
| `N8` owner-rollback acceptance | INSUFFICIENT_EVIDENCE | Repo-local tests only |
| Windows **owner** acceptance | NOT_RUN | GitHub Windows-path checks are **portability** evidence only |
| Local-model acceptance | NOT_RUN | — |
| Soak | NOT_RUN | — |
| Real same-model retention | NOT_RUN / INSUFFICIENT_EVIDENCE | Ties to `INTELLIGENCE_PRESERVATION` |

> N4/N5/N6/N8 have **strong repo-local tests**. Repo-local tests are **not** acceptance.
> Acceptance requires the real scheduler / real model / real owner browser / real owner
> rollback on an owner machine.

---

## 10. Overlap / Conflict Register

| PR | Classification | Action |
|---|---|---|
| **#26** | Closed **historical evidence only** | **Do not merge.** Cite as history if needed. |
| **#36** | **Superseded**, non-mergeable draft | **Do not merge wholesale.** |
| **#42** | Historical **independent negative probes** | Reuse **evidence/tests selectively**; do not adopt wholesale. |
| **#46** | Audit / performance **docs** | **Post-freeze.** Head `bf8c606`. |
| **#47** | Optimization **plan**, implementation **0%** | Explicitly **post-freeze**. |

> **Rule:** PR #46 / PR #47 optimization work **must NOT touch the freeze candidate.**
> The freeze candidate is `FINAL_SOURCE_SHA` = `83a2a77be66a7fe80d201279b9b8c92e5ffd8926`
> and its P0 fixes only.

---

## 11. EXIT CRITERIA

**V4/V5 MUST NOT be declared complete until BOTH conditions hold:**

1. **All three P0s are CLOSED**, with named evidence:
   - `CANARY_PRODUCTION_CALLER` — production caller proven on the production path
   - `SATISFIED_EVIDENCE_GATE` — real resolver evidence, not synthetic
   - `AT-01` — `UnknownEffect` handled, with acceptance evidence

2. **The required live / evidence gates have ACTUALLY BEEN RUN**, not merely planned:
   - `INTELLIGENCE_PRESERVATION` — real same-model measurement, **≥ 189 paired samples per
     required metric**, `evaluated_sha` produced by the run itself
   - `N4` / `N5` / `N6` / `N8` — real scheduler, model, owner-browser and owner-rollback acceptance
   - `WINDOWS_ACCEPTANCE` — on an **owner machine** (GitHub Windows-path checks do not count)
   - `LOCAL_MODEL_ACCEPTANCE`
   - Soak and real same-model retention

**And in every case:** the evidence must record `FINAL_SOURCE_SHA` and the run's actual
`TESTED_SHA` side by side. **No exact-source certification may be claimed from a synthetic
PR merge checkout.**

Until then: `FEATURE_FREEZE_READY = NO`.
