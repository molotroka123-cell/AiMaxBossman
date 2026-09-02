# BUG_MAP — LONGHORIZON-FREEZE-001

| ID | P | Reproduction | Expected | Actual (found) | Root cause | Fix | Test | Status |
|---|---|---|---|---|---|---|---|---|
| CI-HISTORY-001 | P0 | CI rest group on shallow checkout | historical SHA resolves | `ShaMismatch: unknown commit 8a13f1d` | fetch-depth:1 in 3 workflows | fetch-depth:0 (provenance checks untouched) | test_bench_sha_001_002/003 | CLOSED (5fad426, f240ddc) |
| CI-AUTOREPAIR-REPORT-001 | P0 | repair PR created on failure() | honest status | body claimed "Verified all tests pass" | template lie | truthful body + contract test | test_autorepair_workflow_contracts | CLOSED (a21512f) |
| CI-AUTOREPAIR-REF-001 | P0 | pull_request event | candidate merge commit tested | hard-coded base branch ref | checkout ref pinned | ref removed + fetch-depth:0 | same contract tests | CLOSED (a21512f, f240ddc) |
| BUG-OBS-001 | P1 | live Chromium observer | distinct fresh obs ids | all ids obs_1 → freshness violations | itertools.count created per observe() | class-level counter | test_e2e_real_gui | CLOSED (80cedc4) |
| BUG-RECEIPT-001 | P1 | engine act() call | adapter matches protocol | TypeError action_id kwarg | engine evolved to EffectReceipt protocol | adapter updated (receipt identity/freshness) | test_e2e_real_gui | CLOSED (80cedc4) |
| BUG-TEACHER-001 | P1 | untrusted teacher returns list-shaped test_results | typed observation | ValueError crash in verify pipeline | dict() coercion on arbitrary shape | degrade to {"raw": ...} | test_apprentice_teacher 19 PASS | CLOSED (b6fd5e1) |
| P0-FINISH-BUDGET-001 | P0 | reserve→commit cycle; restart | durable, conservative accounting | in-memory; failed call leaked reservation; double-commit possible | placeholder bookkeeping | durable atomic records (RESERVED/COMMITTED/RELEASED), reload, release-on-failure, fail-closed corruption | test_fable_budget 4 PASS | CLOSED (269d123) |

Open (honest, non-blocking freeze): Higgsfield auth wall (P1 external); outreach live WAIT_APPROVAL not reached in bounded sweep (P1 data-dependent; invariant mechanics proven offline); branch protection (P1 owner action); budget multi-reservation placeholder (P2); symlink-privilege skip (P2 environment).
