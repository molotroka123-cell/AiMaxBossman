# ASTRA salvage — delta of the 35 findings against HEAD (SALVAGE-004)

START_SHA (this mission): `2955d3d912533789f4149075900e42334d49e608` · package base `35390ec` · package observed remote `87bf7c7` · audited `7b1377a`.
Integration commit: `2077bbf` (feat(astra): integrate ASTRA salvage package). Follow-ups: `0fa1317` V2-HOOK-CANCEL-01, `d114e30` CSRF re-login, `ffd7d25` Windows UTF-8 + SCA tooling pin.
Statuses are against the code and tests at HEAD, not against the package's own claims. FIXED = targeted test present and green in the full Core/CC/root regression on the integrated tree. PARTIALLY_FIXED/BLOCKED = external acceptance (exact-SHA CI, Windows, branch protection) still open.

| # | Finding | Sev | Status at 35390ec | Status at HEAD | Evidence at HEAD | Fix SHA |
|---|---|---|---|---|---|---|
| 1 | ASTRA-001 | P1 | OPEN → remote OPEN | FIXED | bossman-core/tests/test_astra_remediation.py::test_astra001_final_text_does_not_complete_an_action, ::test_astra001_actual_worker_cannot_mark_no_tool_action_done; command-center/tests/test_finalize_gate.py | 2077bbf |
| 2 | ASTRA-002 | P1 | OPEN → remote PARTIALLY_FIXED | FIXED | test_astra_remediation.py::test_astra002_tampered_completion_cannot_be_resumed; test_v3_astra_p1.py::test_astra_002_unsigned_finished_flags_do_not_skip_work (load refuses unsigned completion) | 2077bbf (interim c36dd5b / TRUTH-003 d6260ad…87bf7c7) |
| 3 | ASTRA-003 | P1 | OPEN → remote PARTIALLY_FIXED | FIXED | test_astra_remediation.py::test_astra003_resume_binds_entire_action; test_v3_astra_p1.py::test_astra_003_changed_plan_under_same_ids_is_blocked_not_resumed (immutable contract) | 2077bbf (interim c36dd5b / TRUTH-003 d6260ad…87bf7c7) |
| 4 | ASTRA-004 | P1 | OPEN → remote OPEN | FIXED | test_astra_remediation.py::test_astra004_wrong_execution_binding_fails, ::test_astra004_old_and_future_signed_observations_fail; test_v3_astra_p1.py::test_astra_004_evidence_from_another_work_does_not_satisfy_contract | 2077bbf (interim c36dd5b / TRUTH-003 d6260ad…87bf7c7) |
| 5 | ASTRA-005 | P1 | OPEN → remote PARTIALLY_FIXED | FIXED | test_astra_remediation.py::test_astra005_crash_after_effect_before_receipt_never_replays; test_v3_fence_receipts.py (zombie fence 41 vs 42) | 2077bbf (interim c36dd5b / TRUTH-003 d6260ad…87bf7c7) |
| 6 | ASTRA-006 | P2 | OPEN → remote OPEN | FIXED | test_astra_remediation.py::test_astra006_absent_measurement_gets_no_perfect_score | 2077bbf |
| 7 | ASTRA-SEC-101 | P1 | OPEN → remote OPEN | FIXED | test_astra_remediation.py::test_sec101_pinned_backend_never_resolves_original_host, ::test_reaudit_shared_address_space_is_not_public_egress, ::test_reaudit_dns_does_not_block_deadline_or_grant_late_pins | 2077bbf |
| 8 | ASTRA-SEC-102 | P2 | OPEN → remote OPEN | FIXED | test_astra_remediation.py::test_sec102_streamed_gzip_bomb_is_bounded, ::test_sec102_http_saved_logs_remove_secret_values | 2077bbf |
| 9 | ASTRA-SEC-103 | P1 | OPEN → remote PARTIALLY_FIXED | FIXED | tests/test_ci_secret_scan.py (nested/corrupt ZIP fail closed); root-ci secret scan PASS at ffd7d25 (local) | 2077bbf (interim c36dd5b / TRUTH-003 d6260ad…87bf7c7) |
| 10 | ASTRA-SEC-104 | P2 | OPEN → remote OPEN | FIXED | command-center/tests/test_astra_remediation_cc.py::test_sec104_token_never_prints_to_redirected_output | 2077bbf |
| 11 | ASTRA-CI-101 | P1 | OPEN → remote OPEN | BLOCKED (owner: branch protection API) | tools/astra_branch_protection.py present; branch protection NOT applied (last API observation: protected=false, rulesets=[]) — owner action | 2077bbf + ffd7d25 |
| 12 | ASTRA-CI-102 | P2 | OPEN → remote OPEN | PARTIALLY_FIXED (exact-SHA CI pending) | tools/astra_security_gate.py blocking in bossman-core-ci/command-center-ci; local gate PASS/PASS after pip>=26.2/setuptools>=83 (ffd7d25); exact-SHA CI pending | 2077bbf + ffd7d25 |
| 13 | ASTRA-CI-103 | P2 | OPEN → remote OPEN | PARTIALLY_FIXED (Windows run on ffd7d25 pending) | astra-acceptance.yml portable matrix ubuntu/windows; windows job FAILED at f0051ee (cp1252 read in test) → fixed ffd7d25; result on ffd7d25 pending | 2077bbf + ffd7d25 |
| 14 | ASTRA-CI-104 | P2 | OPEN → remote OPEN | PARTIALLY_FIXED (real sandbox NOT_RUN) | astra-acceptance.yml runner job PASS at f0051ee (ASTRA runner recovery); real sandbox job opt-in, NOT_RUN | 2077bbf + ffd7d25 |
| 15 | F001 | P1 | OPEN → remote OPEN | FIXED | test_astra_remediation.py::test_f001_shared_exclusive_and_renewal_capabilities | 2077bbf |
| 16 | F002 | P1 | OPEN → remote PARTIALLY_FIXED | FIXED | test_astra_remediation.py::test_f002_expired_capability_refuses_effect | 2077bbf (interim c36dd5b / TRUTH-003 d6260ad…87bf7c7) |
| 17 | F003 | P1 | OPEN → remote OPEN | FIXED | test_astra_remediation.py::test_f003_f004_queue_claim_fencing_and_durable_waits | 2077bbf |
| 18 | F004 | P1 | OPEN → remote OPEN | FIXED | same test (backoff/WAIT_HUMAN survive restart) | 2077bbf |
| 19 | F005 | P1 | OPEN → remote OPEN | FIXED | test_astra_remediation.py::test_f005_atomic_unified_memory_admission, ::test_f005_fake_128gb_topology_and_residency (fake topology, not hardware attestation) | 2077bbf |
| 20 | F006 | P1 | OPEN → remote PARTIALLY_FIXED | FIXED | bossman-core/tests/test_v3_fleet_e2e.py (MINIMIZED context; fleet_dispatch is the only extra key) | 2077bbf (interim c36dd5b / TRUTH-003 d6260ad…87bf7c7) |
| 21 | O001 | P1 | OPEN → remote OPEN | FIXED | test_astra_remediation.py::test_o001_private_cloud_marketplace_blocked; test_astra_remediation_cc.py::test_o001_prod002_every_actual_provider_call_checks_privacy_and_price, ::test_o001_direct_openrouter_probe_refuses_private_context | 2077bbf (interim c36dd5b / TRUTH-003 d6260ad…87bf7c7) |
| 22 | O002 | P1 | OPEN → remote OPEN | FIXED | test_astra_remediation.py::test_o002_conflicting_intake_rolls_back_all_rows; test_v3_astra_p1.py::test_o002_work_id_collision_across_missions_is_rejected | 2077bbf (interim c36dd5b / TRUTH-003 d6260ad…87bf7c7) |
| 23 | O003 | P1 | OPEN → remote OPEN | FIXED | test_astra_remediation.py::test_o003_invalid_resource_values_rejected; test_v3_astra_p1.py::test_o003_negative_or_non_finite_budget_is_a_contract_problem | 2077bbf (interim c36dd5b / TRUTH-003 d6260ad…87bf7c7) |
| 24 | O004 | P1 | OPEN → remote OPEN | FIXED | test_astra_remediation.py::test_o004_mandatory_risk_reviewer_veto_is_not_skipped | 2077bbf |
| 25 | O005 | P1 | OPEN → remote OPEN | FIXED | test_astra_remediation.py::test_o005_o006_atomic_budget_recovery_and_capacity_release | 2077bbf |
| 26 | O006 | P2 | OPEN → remote OPEN | FIXED | same test | 2077bbf |
| 27 | O007 | P1 | OPEN → remote OPEN | FIXED | test_astra_remediation.py::test_o007_astra009_stored_scope_ownership_required | 2077bbf |
| 28 | PROD-001 | P2 | OPEN → remote OPEN | FIXED | test_astra_remediation_cc.py::test_prod001_budget_includes_source_labels_and_separators | 2077bbf |
| 29 | PROD-002 | P1 | OPEN → remote OPEN | FIXED | test_astra_remediation_cc.py::test_prod002_* (5 tests: unknown stays unknown, explicit free, legacy revalidation, old zero not free) | 2077bbf |
| 30 | PROD-003 | P2 | OPEN → remote OPEN | FIXED | test_astra_remediation_cc.py::test_prod003_failed_process_query_is_unknown, ::test_prod003_successful_empty_process_list_really_is_zero | 2077bbf |
| 31 | PROD-004 | P2 | OPEN → remote OPEN | FIXED | test_astra_remediation_cc.py::test_prod004_mission_tasks_are_filtered_before_limit_and_paginated | 2077bbf |
| 32 | ASTRA-007 | P1 | OPEN → remote PARTIALLY_FIXED | FIXED | test_astra_remediation.py::test_astra007_journal_path_is_confined; test_v3_memory_kernel.py::test_journal_task_id_cannot_escape_root | 2077bbf (interim c36dd5b / TRUTH-003 d6260ad…87bf7c7) |
| 33 | ASTRA-008 | P1 | OPEN → remote OPEN | FIXED | test_astra_remediation.py::test_astra008_pem_and_structured_secrets_are_removed | 2077bbf |
| 34 | ASTRA-009 | P1 | OPEN → remote OPEN | FIXED | test_astra_remediation.py::test_o007_astra009_stored_scope_ownership_required | 2077bbf |
| 35 | ASTRA-010 | P2 | OPEN → remote OPEN | FIXED | test_astra_remediation.py::test_astra010_serialized_context_including_header_fits | 2077bbf |

ASTRA_FINDINGS_RECHECKED=35 · ASTRA_FIXED=31 · ASTRA_ALREADY_FIXED_OR_PARTIAL_BEFORE_PACKAGE=11 · ASTRA_STILL_OPEN=4 (all four are CI/acceptance findings: P1=1 ASTRA-CI-101, P2=3).

## Structured traces (fixes made in this mission, not in the package)

### V2-HOOK-CANCEL-01
- problem: Governor stop of an error loop was overwritten by the engine's retry write.
- observable evidence: test_governor_stops_error_loop failed 4/5 runs on Python 3.11 at f657f12; run stayed `queued`, 7 `stopped` interventions recorded.
- root cause: `asyncio.wait_for` on 3.11 runs the hook in another task and swallows the outer cancellation when the hook finishes in the same loop step (CPython gh-86296).
- fix: `asyncio.timeout` in `Engine._call_hooks` (same task). V2 freeze exception stated in the commit.
- tests: tests/test_pass3_hooks_fail_closed.py::test_hook_stop_cancellation_reaches_the_run (fails at f657f12, passes now); governor test 6/6.
- result: PASS. lesson: a hook that cancels its own run must run in the run's task. SHA: 0fa1317.

### PROCESS-OBSERVER-01 (reported blocker)
- problem: process verifier said running=false for a live PID in the package author's container; on Windows the fallback `os.kill(pid, 0)` would terminate the observed process.
- evidence: REAUDIT_RU.md; code reading of `_observe_process`.
- root cause: single observation source trusted blindly; POSIX-only probe used on all platforms.
- fix: `observe_pid` cross-checks psutil/procfs/signal-0; disagreement or no source → UNVERIFIED; no signal probe on Windows.
- tests: test_v2_poststate_verifiers.py::test_observe_pid_windows_without_psutil_is_unobservable, ::test_observe_pid_disagreeing_sources_are_not_proof; both reported tests pass on Linux (9/9).
- result: PASS. lesson: unavailable observation is UNVERIFIED, never a guessed boolean. SHA: 2077bbf.

### UI-CSRF-403-01 (test-period log 51307af16b90)
- problem: mutating requests failed with 403 in 0 ms; owner saw dead buttons.
- evidence: 4 http.error rows (POST /api/providers, POST /api/browser/sessions/2/act) + ui.refused.
- root cause: valid session cookie, stale/missing per-tab CSRF token; UI treated 403 as policy denial.
- fix: ApiError.code='csrf' from require_token; ui/api.js clears the token and raises the unauthorized event → login form. Policy 403 unchanged.
- tests: test_browser_navigation_ui.py::test_lost_csrf_token_requires_login_instead_of_dead_403 (Playwright, ran, not skipped).
- result: PASS. lesson: give the client a machine-readable reason for auth-class refusals. SHA: d114e30.

### WIN-UTF8-01
- problem: ASTRA acceptance windows-latest failed at f0051ee (UnicodeDecodeError cp1252).
- root cause: test read the journal with the platform default encoding.
- fix: encoding='utf-8' in the Windows-run tests. tests: same files, 91 passed locally. result: pending exact-SHA Windows run on ffd7d25. SHA: ffd7d25.

### SCA-TOOLING-01
- problem: security gate audits the whole interpreter; pip 24.0 / setuptools 79.0.1 advisories would fail CI unrelated to project deps.
- fix: CI security step upgrades pip>=26.2 and setuptools>=83 before auditing; local gate PASS/PASS (bandit 0, pip-audit 0) for both components. SHA: ffd7d25.

## Verification on the integrated tree (local, Python 3.11 venv, Linux)

| Check | Result |
|---|---|
| Core suite (bossman-core/tests) | 2047 passed, 36 skipped, 2 xfailed, 5 failed → the 5 legacy tests adapted, 6/6 green (commit 2077bbf) |
| Command Center suite | 1471 passed, 4 skipped, 0 failed |
| Root suite | 152 passed |
| Package VERIFY.py | core PASS, command-center PASS, ci-gate PASS, secrets PASS, runner PASS |
| Secret scan | PASS (recursive ZIP, both uploaded archives scanned) |
| SAST/SCA gate | bandit PASS (0), pip-audit PASS (0) after tooling upgrade; before: 9 advisories in pip/setuptools only |
| Skips registry | 89 entries, 0 without reason |
| Scorecard --check | PASS |
| Windows | astra-acceptance windows-latest: FAIL at f0051ee (fixed), result at ffd7d25 pending; Windows ACL of key files NOT_RUN |
| Hardware / sandbox | NOT_RUN (no self-hosted runner) |
| OpenRouter live | NOT_RUN in this mission; spend 0.00 USD |

Counters measured by deterministic tests at HEAD: DUPLICATE_SIDE_EFFECT_COUNT=0 (test_v3_organization_e2e restart, test_v3_fence_receipts zombie), STALE_EVIDENCE_ACCEPTED=0 (test_astra004_old_and_future_signed_observations_fail, ActionReceipt.fresh), CROSS_MISSION_EVIDENCE_REPLAY=0 accepted (test_astra004_wrong_execution_binding_fails), UNKNOWN_EFFECT_RETRY_COUNT=0 (test_astra005_crash_after_effect_before_receipt_never_replays). Live-run values: UNPROVEN.
