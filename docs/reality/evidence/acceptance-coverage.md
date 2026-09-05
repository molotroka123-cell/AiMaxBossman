# Reality acceptance coverage and rollout limits

Review date: 2026-09-05. This maps the supplied [ACCEPTANCE.md](../ACCEPTANCE.md)
and [FABLE_CONNECT.md](../FABLE_CONNECT.md), including step 10, to actual code
and tests. It does not grant permissions from archive instructions.

`PASS` below is limited to the stated test boundary. `BLOCKED` means the full
requested deployment scenario is not demonstrated, even when its local subset
passes. Injected model answers, mock adapters, SQLite and two logical nodes on
one computer are not live provider or distributed-backend proof.

Exact source revisions, commands, source digests and latest rerun outcomes are
recorded in [engine evidence](final-engine-tests.json) and
[installed-wheel evidence](final-package-tests.json). A source change requires a
new recorded run; older artifacts must not be called final evidence for newer code.

## Test files used by the matrix

- **H**: [host integration tests](../../../tests/reality/test_host.py).
- **K**: [package tests](../../../tests/reality/test_package.py), classes `KernelTests` and `SupportTests`.
- **C**: [Core/Compound/Fleet integration](../../../bossman-core/tests/test_reality_integration.py).
- **B**: [BCC integration](../../../command-center/tests/test_reality_integration.py).
- **F**: [existing Fleet safety proofs](../../../bossman-core/tests/test_v3_fleet_safety_proofs.py).

## Supplied acceptance scenarios

| # | Status and boundary | Exact test/evidence references and remaining gap |
| --- | --- | --- |
| 1 | PASS, controlled engine boundaries | C `test_core_loop_final_text_requires_real_file_receipt[False]`; B `test_done_without_receipts_is_parked_and_not_reclaimed`. Core DB/model are fixtures; BCC uses real temporary SQLite and injected checkpoint text. No paid model is called. |
| 2 | PASS, real local files | C `test_core_loop_final_text_requires_real_file_receipt[True]`; B `test_real_file_dispatch_and_all_completion_gates`; H `test_real_file_dispatch_and_restart_proof`. File writes and independent reads are real; adapters and host contracts are controlled. |
| 3 | PASS, immutable local resume boundary | C `test_compound_resume_rejects_changed_ir[args/target/expected/actor/plan]`; H `test_fail_closed_before_io[run]`. Compound uses the journal task ID as its fixed run ID; the separate run-substitution test exercises the common guard. |
| 4 | PASS, actual child process kill | H `test_killed_process_recovery_without_duplicate[True]`: child writes a real file, process is killed and joined, restored host observes it, original fence confirms, counter remains one. |
| 5 | PASS, actual child process kill | H `test_killed_process_recovery_without_duplicate[False]` and `test_unknown_attempt_is_not_retryable`: absence plus known terminal child status is required; unknown attempt remains manual review. These are local process outcomes, not remote-provider cancellation proof. |
| 6 | BLOCKED for distributed deployment | C `test_real_local_fleet_file_with_reality`; H `test_lease_lost_after_write_retains_escrow`, `test_completion_hook_keeps_fleet_fence`; F `test_expired_lease_cannot_regain_authority`. Existing Fleet proofs use local shared SQLite; F `test_remote_transport_is_not_production_ready` explicitly rejects remote transport. No shared production PostgreSQL/remote-node proof. |
| 7 | PASS for signed local bindings; BLOCKED for exhaustive code-tree attack matrix | K `test_cross_mission_replay`, `test_cross_run_replay`, `test_expected_value_replay`, `test_independent_effective_identity`, `test_stale_and_future_evidence`; H `test_stable_signing_key_across_restart`. The git mission binds separate immutable code trees and observations. It does not exhaustively inject every wrong SHA/tree through each application finalizer. |
| 8 | BLOCKED for Reality paid mode | H `test_paid_and_cloud_paths_blocked_before_call`; B `test_provider_and_fallback_not_called_for_participant`. Existing regressions include Core `test_fable_budget_hard.py::test_cross_process_concurrent_reserve_never_exceeds_cap` and BCC `test_fable_hard_cap.py::test_two_workers_cannot_reserve_more_than_the_cap_together`, `test_both_paths_share_one_ledger`, `test_a_broken_call_keeps_its_money_held`. Existing owner ledgers remain protected; enrolled Reality paid reservation and real paid egress are not enabled or tested. |
| 9 | PASS for blanket blocked egress; BLOCKED for selective provider admission | K `test_memory_dependencies_and_privacy` rejects a PUBLIC root depending on LOCAL data; B `test_provider_and_fallback_not_called_for_participant` proves the real engine never constructs a provider for an enrolled run. Fact slicing is not wired into provider context assembly. No selective PUBLIC cloud allowance is claimed. |
| 10 | PASS for tested fail-closed paths; BLOCKED for exhaustive cross-product | H `test_fail_closed_before_io[module/profile/store/payload/run]`, `test_disable_flag_keeps_existing_run_gated`, `test_unrelated_tasks_do_not_need_optional_module`; B `test_direct_finalization_cannot_bypass_gate[off/missing_profile/wrong_run/missing_ir]`. The module/flag corruption matrix has not been independently expanded across every Core/Compound/Fleet finalization branch. |
| 11 | PASS for fixed local action admission; BLOCKED for provider fallback/remote reassignment | H `test_quarantine_blocks_real_dispatch`, `test_configured_learning_route_respects_quarantine_before_io`. Provider fallback is blocked wholesale. No automatic alternate tool route or remote Fleet reassignment is admitted or demonstrated. |
| 12 | PASS, support-kernel contract | K `test_benchmark_no_changed_goalposts` rejects changed suite/case sets and regressions. No automatic benchmark promotion service is enabled; this is not live promotion proof. |
| 13 | PASS, durable local restriction | H `test_poststate_divergence_restricts_after_restart`, `test_learning_divergence_contains_hashes_and_fixed_text_only`: fresh independent mismatch persists level zero, escrow remains unresolved, restart respects it. |
| 14 | PASS, real controlled git and bare remote | [git evidence](git-acceptance.json), produced by [reality_git_acceptance.py](../../../scripts/reality_git_acceptance.py). Separate base/fixed trees, failing/passing subprocess tests, commit and independently reopened remote SHA/tree/exact patch. The remote is a local bare repository; no live hosting-provider API claim. |
| 15 | PASS for isolated installed-wheel imports; production installation NOT_RUN | [installed-wheel evidence](final-package-tests.json) records Python 3.11/3.12 isolated imports from installed wheels and shared module identity; [engine evidence](final-engine-tests.json) records per-project regression commands and tested source revision. These fresh verification environments do not replace/restart the user's currently running Bossman installation. |

## Step 10: actual support-layer wiring

`LocalHost` supports an optional **host-authored** `route_bids` mapping. Every bid
must be typed, local, zero-cost, and name an already permitted concrete action.
The configured `learning_redactor` is mandatory when this mapping is nonempty;
bootstrap supplies the application's existing redactor. No model-authored
probabilities or cost estimates are accepted as authorization.

- `route_allowed` checks durable quarantine and calls `choose` for the one already compiled action. It cannot change action, arguments, target, or permissions. This is fixed-route admission, not a multi-provider broker.
- Independent observation calls `compare_world`; mismatch persists an autonomy restriction before recording a hash-only delta lesson.
- After durable `store.confirm`, `record_confirmed` reloads and freshly verifies the persisted signed receipt, owner, fence and dispatch binding. Only then does it record a lesson and settle verified success under a mission/effect/fence key.
- Lesson text consists only of fixed host strings passed through the injected redactor. Delta payloads contain hashes and the fixed key `poststate`, never observer contents, prompts, exception text, target paths or clinical records. Causal knowledge remains `INFERRED` / `cause_not_assessed`.
- Without bid configuration, no settlements or lessons are written. Existing quarantine lookup still works. Transport errors never manufacture success or quarantine; host-confirmed hard-failure settlement remains an explicit owner operation.
- Audit failure after confirmation retains `CONFIRMED`; it cannot replay the effect. The host repairs the ledger and retries `record_confirmed`, then reconciles application progress. This is not an automatic recovery policy.

H tests beginning `test_learning_` and
`test_configured_learning_route_respects_quarantine_before_io` verify durable
outcomes, restart idempotency, invalid/stale proofs, redactor calls, privacy,
missing configuration and audit/transport failure behavior. These tests use a
redactor spy; the controlled git mission supplies the real existing Core redactor
and checks its durable learning rows.

**Not wired:** `MemoryCompiler.slice` at model context assembly; actual
multi-provider `choose/settle`; model fallback and cloud privacy lineage across
new provider admission. All provider calls are blocked for enrolled runs, so
adding a disconnected helper would not establish these integrations.

## Remaining rollout blockers

**P0 before enabling broader autonomy or provider/remote execution:** authenticated
host bootstrap and filesystem ACL isolation must be demonstrated on the deployed
host; agent mounts must not expose participation latches, owner IR, signing keys
or learning databases. Paid routing must reserve the existing global owner cap
and local mission accounting before every real egress path, including retry and
fallback. Selective cloud routing requires end-to-end dependency privacy checks.
Distributed Fleet requires authenticated remote nodes and shared transactional
owner/fence/escrow enforcement. These are rollout conditions, not claims of a
currently exploitable remote vulnerability in the disabled mode.

**P1 to complete the package contract:** wire dependency slicing to the actual
context assembler once provider admission is implemented; connect measured
provider candidate selection/outcome settlement; finish the exhaustive
module/flag/finalization and wrong-code-tree negative matrices; provide supported
owner recovery that reconciles confirmed effects with pending application
journals after an audit failure. Runtime tests of fresh installed wheels must be
repeated at the final source revision.

The Windows evidence-key binary-write defect discovered during this review is
fixed separately with a deterministic control-byte restart regression. No
existing production key was inspected, silently rotated or rewritten. Earlier
keys/proofs affected by the old text-mode write require explicit host assessment.
Windows POSIX-mode `0600` assertions and symlink-creation privilege failures are
environmental limits, not proof that Windows ACL isolation has passed.
