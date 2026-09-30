# Agent 02 — execution truth

Mission: BOSS-FINAL-REALITY-CLOSURE-010. Base: `a14515d`.
Isolated branch: `audit/agent-02-truth`. Windows, Python 3.12, UTF-8 enabled.
No cloud inference, financial calls, or mainnet side effects. Cost: USD 0.

## Findings and changes

1. **P1 / FINAL-010-TRUTH-01, reproduced:** Command Center finalizer accepted
   denied/error mutation calls, `executed` terminal calls with exit_code=1,
   unfinished processes, and successful mutation attempts without a post-state
   contract. The historical live task 7 exposed this failure class. Intent parser
   improvements alone were insufficient: the finalizer trusted absence of FAIL.
   Finalizer now independently checks actual tool rows, refuses unresolved
   mutations and requires post-state expectations for action tasks/effectful tools.
   It never promotes a successful executor status to verified world state.
2. **P1 / FINAL-010-TRUTH-02, reproduced:** human review override marked a task
   completed while its required file was absent. Override now verifies stored
   approval identity, latest task run, pending tool approvals, mutation outcome,
   expectation integrity and fresh required post-state. Human judgement cannot
   waive missing effects. The normal finalizer also checks task/run binding.
3. **P1 / FINAL-010-TRUTH-03, reproduced:** unknown expectation kinds were silently
   removed by parsing, leaving NOT_REQUIRED. The finalizer rejects malformed
   obligation lists instead of treating an empty parsed result as permission.
4. **P1 / FINAL-010-TRUTH-04, reproduced (Agent 05 discovery):** mission aggregation
   included failed/stopped children in `done`, and then emitted mission.completed.
   A required failed/stopped/cancelled child now fails the mission. Only completed
   children count toward successful progress.
5. **Freshness correction:** naive SQLite UTC timestamps were converted using
   local Windows timezone. They are now interpreted as UTC for comparison with
   observer timestamps. Override uses the same freshness requirement.

Read-only failed diagnostics remain usable as information. An exact-action retry
can clear its earlier execution failure, but still needs independent readback.
Successful calls from an unrelated capability cannot provide its verifier.
Shell substitutions and write-capable git commands are conservatively effectful.

## Reproduction and validation

Interpreter:
`C:/AiMaxBossman-claude-bossman-control-v03-43igbk/.venv-ci312/Scripts/python.exe`.
`PYTHONUTF8=1`, explicit PYTHONPATH to this worktree's component directories and
shared package. Each pytest command ran in the relevant component directory.

- Historical reproduction loaded `git show a14515d:command-center/bcc/finalize.py`
  and `git show a14515d:command-center/bcc/features/missions.py` into their normal
  modules in an isolated pytest process. No historical files were modified.
  Original 12 new tests: **9 failed, 3 passed**. Failures correspond to the four
  concrete defect classes above, not import failures.
- Final focused Command Center run:
  `pytest tests/test_mission010_execution_truth.py tests/test_finalize_gate.py
  tests/test_fence_fl01.py tests/test_gate_contract_requeue.py
  tests/test_no_direct_completed_writes.py -q --tb=short --disable-warnings`:
  **26 passed**. This includes actual terminal handler allowed-root denial with
  a scripted provider's honest failure response; SQLite task does not complete.
  Also real temporary file fresh readback, parent aggregation, malformed effect,
  missing effect override, exact-action recovery, and optional read diagnostics.
- Core:
  `pytest tests/test_astra_remediation.py tests/test_v3_astra_p1.py
  tests/test_v3_fence_receipts.py tests/test_v3_evidence_signing.py
  tests/test_v3_compound_resume.py -q --tb=short`: **104 passed**.
  Covers ASTRA-001..005, stale/future/wrong binding evidence, immutable plans,
  signed journal tampering, crash-after-effect no replay, and zombie fences.
- Root `pytest tests/test_action_receipt.py -q --tb=short`: see final agent result.
- `git diff --check`: PASS.

## Explicit compatibility failures / integration work

Scoped legacy run (before the final extra capability/intent backstops):
`pytest tests/test_action_contract.py tests/test_action_gate.py
tests/test_feat_governor_review.py tests/test_feat_missions.py -q --tb=short
--disable-warnings`: **71 passed, 8 failed**.

Failures, preserved rather than weakening the invariant:

- `test_terminal_real_execution_with_matching_file_completes`: Windows project
  host test times out, consistent with the historical executor blocker; needs
  Windows specialist confirmation after argument transport repair.
- `test_memory_real_write_completes`: no post-state contract attached.
- `test_family_matrix_executed_call_is_not_applicable` for apps, openclaw,
  opencode and plugin: fake successful handler does not prove external state;
  resulting waiting_approval is the new intended fail-closed behavior.
- `test_code_action_satisfied_by_a_real_edit`: successful tool call lacks explicit
  post-state contract.
- `test_restart_does_not_repeat_a_completed_side_effect`: legacy test expects
  completion without an independently verifiable mutation contract.

The later added capability matching and action-without-expectation refusal may
identify additional legacy unsupported-success assumptions in the lead's full
regression. No broad suite is claimed green.

Pytest emitted intermittent aiosqlite Event loop is closed thread warnings,
including existing finalizer/fence tests. These are recorded as a runtime teardown
limitation, not hidden or classified as product success.

## Remaining boundaries / verdict

The 104 Core tests reprove the historical safeguards in their tested boundaries;
they are not evidence of live provider hot-swap or remote transport. Current BCC
contracts are task-level expectations, not a general per-action target binding
system. Same-kind unrelated postconditions, intent outside the deterministic
classifier and tools misdeclared as read remain red-team audit targets. No claim
of exhaustive false-success elimination is made. Independent Agent 10 review
and final integrated regression are required before production acceptance.

STATUS=FIXES_IMPLEMENTED_FOCUSED_VERIFIED_WITH_EXPLICIT_INTEGRATION_BLOCKERS
LIVE_HISTORICAL_FALSE_SUCCESS_COUNT=1 (retained; not rewritten to zero)
NEW_FOCUSED_FALSE_SUCCESS_COUNT=0
CORE_STALE_OR_WRONG_EVIDENCE_ACCEPTED=0_IN_TESTED_CASES
CORE_CRASH_REPLAY_DUPLICATES=0_IN_TESTED_CASES
LIVE_PROVIDER_CALLS=0
