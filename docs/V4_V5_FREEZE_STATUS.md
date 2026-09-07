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
| `FINAL_SOURCE_SHA` | `333616c` (freeze candidate, branch `claude/v4-v5-freeze-p0-gates-l56exm`) | The **real source head** being certified. Descends from `83a2a77` (PR #37 head at the time this run started). This is the only SHA that exact-source certification may reference. |
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

---

## 12. Measured run — freeze candidate `333616c`

Every number below was observed locally on the stated SHA. Nothing is estimated.

### SHAs for this run

| Field | Value |
|---|---|
| `FINAL_SOURCE_SHA` | `333616c` (branch `claude/v4-v5-freeze-p0-gates-l56exm`) |
| Base source head at start | `83a2a77be66a7fe80d201279b9b8c92e5ffd8926` |
| Upstream PR #37 head, later in the run | `3b210209100b8fa9b6a255c8e5f09cbc504008c5` |
| `TESTED_PR_MERGE_SHA` (PR #48, synthetic) | `7de858ddb9bd18e349f0f02593591ce5501c1a72` — absent from the clone |

### Baselines on clean `83a2a77` (separate worktree)

| Suite | Result |
|---|---|
| root `tests/` | 1077 passed, 10 skipped |
| V5 objective/canary subset | 215 passed |
| `bossman-core` AT-01 file | 12 passed |
| `bossman-core` operator set (4 files) | 98 passed, 1 skipped |

### Measured on the freeze candidate `333616c`

| Suite | Result |
|---|---|
| root `tests/` | **1131 passed, 2 skipped** |
| `bossman-core` full `tests/` | **2781 passed, 41 skipped** |
| operator set + AT-01 + upstream freeze test | **119 passed, 1 skipped** |

### Upstream regression found and fixed

Upstream `3b21020` ("fail closed unverifiable AT-01 effects") introduces
`UnverifiableEffect` so the extraction fallback routes *around* the broken drop
at `manager.py:660`, leaving that branch — and the unrelated-mutation rule at
`manager.py:675-678`, the no-probe drop at `:663-664` and the bare-`except`
swallow at `:682-687` — open. Reproduced on a clean checkout of `3b21020`:

```
9 failed, 89 passed, 1 skipped
```

all nine reading `TaskState.FAILED is TaskState.COMPLETED`, including
`test_c10_positive_control_approved_action_executes`. The same four files on
the freeze candidate: **119 passed, 1 skipped**. Upstream's `UnverifiableEffect`
and its new test file are kept; the manager is closed as well as routed around.

### CI reds on PR #48 at `bbfc4bb`, adjudicated

| Check | Verdict |
|---|---|
| `pytest rest (py3.11)` — 9 operator failures | **Not this PR's.** `bbfc4bb` is docs-only (2 files, +592). PR #48's base is `claude/v5-closure-at-reconcile-xdh12f` @ `3b21020`, so the synthetic merge carried upstream's regression. Fixed on this branch. |
| `root pytest + hygiene (py3.11)` — `test_objective_cas_under_10ms_and_stale_write_is_denied` | **Not this PR's; runner noise.** `FAIL / excess_spread_across_the_distribution`, `p50 1.389 ms`, `over_limit 2`, `stalls [26.60, 18.59]` against `max_isolated_stalls 1`. Docs-only diff cannot cause it, and the same test passes locally on the candidate. The 10 ms target is NOT relaxed, no sample filtered, no retry/skip/xfail added. |
| `measured intelligence retention` | **Expected fail-closed.** No current same-model measured evidence exists. Not made green. |

### Gate movement this run

| Gate | Was | Now | Evidence |
|---|---|---|---|
| `SATISFIED_EVIDENCE_GATE` | IN_PROGRESS | **PASS** | `5c6ad54`. Resolver binds objective, condition, spec digest + revision, applicability, producing run, freshness, single-use. ~15 negative controls; reproduced the pre-fix defect on a database written by `83a2a77` and verified the retirement path. |
| `AT-01` | IN_PROGRESS | **PASS** | `973ce94` + `333616c`. C1-C4 closed at the manager; six required cases present, including an invisible effect completing on a real bound receipt with a foreign-receipt negative control. |
| `CANARY_PRODUCTION_CALLER` | IN_PROGRESS | **FAIL_CLOSED (open)** | Parked on `claude/v4-v5-p0-1-canary-production-caller`. Registration bypass closed; gate wired over the durable canary tables; 13 passed / 3 failed. The three failures are left failing and unmodified. |

`OPEN_P0` is therefore **1**, and `FEATURE_FREEZE_READY` remains **NO**.

### P0-1 remaining work, stated exactly

`canary_decision` re-derives cohort reports from Command Center `task_runs`
facts on every call, so a durable canary run that is lost gets silently rebuilt
from the same facts and decided healthy — a restart inherits success instead of
finding silence. Reports must be written when a run reaches a terminal state
and only read at the decision point. The positive control promotes on a single
`refresh` with no prior pass, so relocating that write is a design decision, not
a mechanical fix. Still failing, deliberately:

- `test_another_service_identity_cannot_decide_this_run`
- `test_a_human_approval_does_not_bypass_the_canary`
- `test_a_restart_without_the_durable_run_denies`

`tests/test_v5_canary_production_caller.py` additionally targets an
objective-fleet rollout (`activate_broadly`, `apply_broad_revision`, `rollback`,
`revision_digest`) that does not exist in production; `ObjectiveStore.revise` is
test-only. That surface was deliberately not invented.

### Still NOT_RUN / INSUFFICIENT_EVIDENCE

`N4`, `N5`, `N6`, `N8`, `WINDOWS_ACCEPTANCE`, `LOCAL_MODEL_ACCEPTANCE`,
`CANARY_ROLLBACK`, `INTELLIGENCE_PRESERVATION`, soak, real same-model
retention. No owner-machine acceptance artifact exists anywhere in the tree;
the Windows checks that pass in CI are portability evidence only.

### CI outcome on the freeze candidate `5b461d3` (PR #48)

All 23 reported checks **succeeded**; `ASTRA real sandbox` skipped honestly (no
KVM/hardware on a standard runner, the fixture declines rather than pretends).
Green includes both `root pytest + hygiene` matrices, both `pytest` matrices,
`pytest rest`, `pytest security`, `pytest stage8-14`, `pytest gateway-context`,
`покрытие (неснижаемый порог)`, `compile + секреты`, `секреты, JS,
запрещённые файлы`, `windows paths (py3.12)`, `safety` 3.11/3.12,
`ASTRA portable` on ubuntu and windows, `ASTRA runner recovery`, and
`bossman-core container ships bossman-shared`.

**The human-speed red is now proven to have been runner noise, not a defect.**
`root pytest + hygiene (py3.11)` carried
`test_objective_cas_under_10ms_and_stale_write_is_denied` failing at `bbfc4bb`
with `excess_spread_across_the_distribution` (`stalls [26.60, 18.59]` against
`max_isolated_stalls 1`). It passes here with **no change to the contract**: the
10 ms target stands, no sample was filtered, and no retry, skip or xfail was
added. The LATENCY_CONTRACT invariants were never touched, so this is a
re-observation of the same gate, not a weakened one.

`measured intelligence retention` did **not** report on this head. It is not
claimed as passing. Its gate is a file-existence check —
`INTELLIGENCE_PRESERVATION=INSUFFICIENT_EVIDENCE / Missing
docs/benchmark/intelligence-preservation-current.json`, exit 2 — so the only
way to make it green is to commit that report, which is exactly the
fabrication this ledger forbids. It stays FAIL_CLOSED until a real same-model
measurement is run.

SHA discipline for this run, recorded from CI's own checkout line: PR #49's job
checked out `refs/remotes/pull/49/merge` = `43bef94`, logged as
"Merge 9c38a3e into 5b461d3". `SOURCE_HEAD=9c38a3e`, `TESTED_SHA=43bef94`
(synthetic). Only the former may back an exact-source claim.

---

## 13. Sandbox runtime hardening integrated (candidate `19180f4`)

### The patch pack was NOT already applied

Verified before touching anything: PR #37's head was still `67905ee` with no
apply-commit, all four patches passed `git apply --check` against this source,
and `command-center/tests/test_sandbox_user_run_regressions.py` did not exist.
The one-shot workflow never fired — it triggers only on a push to
`claude/v5-closure-at-reconcile-xdh12f` touching nothing but itself, and its
lineage guard pins an exact parent SHA. So `SANDBOX_PATCH_APPLIED` was **NO**,
and applying it here was the integration step, not a duplicate.

### Invariants, each mapped to a passing named test

| Invariant | Test |
|---|---|
| STOP is sticky | `test_stop_then_resume_never_resurrects_task` |
| RESUME accepts only PAUSED | `test_recovery_parks_paused_state_until_explicit_resume` |
| Pause→Resume cannot launch a second inference | `test_pause_resume_during_model_call_keeps_one_inference_and_one_run` |
| Run/Retry atomic single-flight | `test_retry_while_active_is_409_and_does_not_create_second_run`, `test_two_concurrent_run_clicks_are_single_flight` |
| Recovery cannot undo owner Stop/Pause | `test_recovery_cannot_resurrect_stopped_owner_state` |
| Completed/failed history immutable | `test_late_stop_cannot_rewrite_completed_history` |
| Terminal veto closes both task and run | `test_terminal_gate_veto_closes_run_and_never_becomes_aggregate_result` |
| Only COMPLETED output becomes `task.result` | (same test) |
| Malformed provider response → typed `ProviderError` | `test_openai_compat_malformed_200_is_protocol_error_not_unhandled_json`, `test_openai_model_list_wrong_json_shape_is_protocol_error` |

Measured: sandbox regressions **10 passed**; engine_stop + queue_retry +
persistence + worker_pool + providers + fence_fl01 + api **32 passed**;
action_contract + gate_contract_requeue **66 passed**.

Full Command Center suite here: **2041 passed, 142 skipped, 9 failed**. All nine
failures are browser-driven UI tests (video studio playback, web designer,
editors acceptance). They are **not** caused by this patch: the same tests fail
identically on the pre-patch commit `f6369b8`, and the cause is a Playwright
`Page.wait_for_function` 30 s timeout in this sandbox, not a product defect. CI
passes these same tests (`pytest (py3.12)` 2178 passed), so CI is authoritative
for them and no Video code was touched.

### Finding 10 — agent execution provenance — NOT closed

`tasks.agent_id` is `ON DELETE SET NULL` (`command-center/bcc/db.py:82`) and
`task_runs` carries no identity beyond `model_alias` (`db.py:104`). Deleting or
editing an agent therefore destroys who executed a historical run, under which
system prompt, tool grants and permission revision. Closing it needs immutable
per-run snapshot columns written once at run start. Not claimed as done.

## 14. P0-A root cause — corrected

The earlier hypothesis (a durability gap in `canary_decision`) was **wrong**, and
is superseded. All three failing tests fail identically with `promote` where
`human_review` is required, and the real cause is the canary window itself:

```
CANARY_WINDOW = MIN_RUNS = 5
... .order_by(runs_t.c.id).limit(CANARY_WINDOW)
```

The window is the **first five terminal runs in id order**. In
`test_a_human_approval_does_not_bypass_the_canary` the candidate is six
completed followed by four failed — a 40 % failure rate — but the window sees
only the healthy prefix, every cohort member reports healthy, the canary passes,
and the version promotes to the whole fleet. A candidate that degrades after its
first few runs is invisible to the gate.

This is a genuine security defect, not a flaky or unsatisfiable test.

**Why it is not fixed here.** Making the window representative (sampling the
cohort across all terminal runs) collides with `evaluate_canary`'s zero-tolerance
rule — "одно падение закрывает выпуск". The positive control
`test_the_promotion_path_now_goes_through_the_canary_door` uses nine completed
and one failed and REQUIRES promotion, so under a representative sample plus
zero tolerance it would deny whenever the digest happens to sample that one
failure. Resolving this needs an owner decision on canary semantics — a
representative sample with a failure budget, or a deterministic early window with
an explicit rate check alongside it — and guessing would either re-open the hole
or make promotion nondeterministic.

`OPEN_P0` stays **1**. P0-A work remains on
`claude/v4-v5-p0-1-canary-production-caller` (PR #49) and is deliberately NOT
merged into the freeze candidate, so the candidate carries no failing test.

---

## 15. P0-0 — diagnosis of PR #37's red CI at `67905ee`

Every failure below was read from its job log and reproduced, not inferred.

### Source truth first

`67905ee` is still PR #37's head — **no successor commit exists**. The sandbox
hardening is therefore NOT in source there, proven by blob identity rather than
by the workflow's presence:

| File | `67905ee` | `0ad86d1` (pre-patch) | |
|---|---|---|---|
| `command-center/bcc/engine.py` | `0bd2495f` | `0bd2495f` | UNCHANGED |
| `command-center/bcc/api.py` | `fa35f67a` | `fa35f67a` | UNCHANGED |
| `command-center/bcc/providers.py` | `54269fc4` | `54269fc4` | UNCHANGED |

`command-center/tests/test_sandbox_user_run_regressions.py` is absent at
`67905ee`. A workflow carrying patches is not the patches being in source.

### Why the one-shot workflow could never apply itself

The `apply` job **failed**, and not on the patch step. It applied the pack, then
its push was rejected by a repository ruleset:

```
remote:   Found 1 violation:
remote:   e45806a6cab8f811192bf39229097751f628a591
 ! [remote rejected] HEAD -> claude/v5-closure-at-reconcile-xdh12f
   (push declined due to repository rule violations)
```

So the mechanism is structurally incapable of producing the successor commit.
Materializing the pack by hand (`19180f4`) was the only path, and it is done.

### Classification of every red

| Check | Exact failure | Class | Status on `a6510db` |
|---|---|---|---|
| `root pytest + hygiene` py3.11 **and** py3.12 | `test_v5_satisfied_evidence_gate_freeze.py::test_arbitrary_nonempty_string_cannot_purchase_satisfied` — `DID NOT RAISE ObjectiveStoreError`; 1 failed, 1077 passed, 10 skipped | **PRODUCT_REGRESSION** — upstream's own P0-2 freeze test failing because the SATISFIED resolver is not in source at `67905ee` | **FIXED** — 1 passed |
| `pytest rest` py3.11 **and** py3.12 (Bossman Core) | 9 × `AssertionError: TaskState.FAILED is TaskState.COMPLETED` across `test_operator_at01_at03_regression`, `test_computer_operator_owner_control`, `test_stage13_wiring_notepad`, `test_stage13_auth_redteam::test_c10_positive_control_approved_action_executes`; 9 failed, 2286 passed | **PRODUCT_REGRESSION** — upstream's partial AT-01 fix (`3b84887`) routes around `manager.py:660` instead of closing it, leaving no way for a legitimately invisible effect to complete | **FIXED** — 98 passed, 1 skipped |
| `покрытие (неснижаемый порог)` | Same Core suite; the coverage job runs the failing tests | **PRODUCT_REGRESSION**, same root cause as the row above | **FIXED** (follows Core) |
| `repair-and-test` (V2 Auto-Repair) | Runs the same suites on `8fa9f69 = Merge 67905ee into ddea211`; `pull-request-operation = none` | **PRODUCT_REGRESSION**, same two root causes | **FIXED** (follows root + Core) |
| `apply` (one-shot sandbox workflow) | Push rejected by repository ruleset, above | **ENVIRONMENT** — cannot self-apply by design of the ruleset | Superseded by `19180f4` |
| `measured intelligence retention` | `INTELLIGENCE_PRESERVATION=INSUFFICIENT_EVIDENCE` / `Missing docs/benchmark/intelligence-preservation-current.json`, exit 2 | **EXPECTED_EVIDENCE_BLOCKER** | Unchanged — not fabricated |
| Command Center CI | **cancelled** | Not a PASS and not counted as one | Green locally except browser-env tests |

Nothing was classified as runner noise. Both product regressions were
reproduced on a clean checkout and both are fixed on this line; PR #48 targets
`claude/v5-closure-at-reconcile-xdh12f`, so it is the vehicle that lands them.

### P0-0 confirmed by CI on `e8671c0`

The diagnosis is no longer local-only. Every suite that is RED on PR #37 at
`67905ee` is GREEN on the freeze candidate:

| Suite | PR #37 `67905ee` | PR #48 `e8671c0` |
|---|---|---|
| `root pytest + hygiene` py3.11 | FAIL | **success** |
| `root pytest + hygiene` py3.12 | FAIL | **success** |
| `pytest rest` py3.11 (Core) | FAIL | **success** |
| `pytest rest` py3.12 (Core) | FAIL | **success** |
| `покрытие (неснижаемый порог)` | FAIL | **success** |
| `measured intelligence retention` | FAIL | FAIL — expected evidence blocker |

Also green on `e8671c0`: `pytest security`, `pytest stage8-14`,
`pytest gateway-context`, `compile + секреты`, `секреты, JS, запрещённые файлы`,
`Real media, Web and Fleet` (3.11 and 3.12), `browser-user-paths`,
`ASTRA portable` on ubuntu-latest and windows-latest, `ASTRA runner recovery`,
`windows paths (py3.12)`, `safety` 3.11/3.12, `deterministic-benchmark`,
`anti-dumbness gate contract`, `bossman-core container ships bossman-shared`.

The V2 Auto-Repair red followed the same two root causes, both fixed here.

`measured intelligence retention` remains the single genuine red and is not
touched: its gate is a file-existence check whose only "fix" is committing the
report that must not be fabricated.
