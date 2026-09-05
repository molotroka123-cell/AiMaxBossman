# Fable: integrate the supplied Reality Compiler package

You are connecting an already implemented package to AiMaxBossman. Do the wiring,
run adversarial integration tests, and report exact results. Do not replace the
package with an architecture essay or start rewriting existing memory/Fleet.

## Source and first steps

Repository: molotroka123-cell/AiMaxBossman
Inspected branch: claude/bossman-control-v03-43igbk
Reference HEAD observed during preparation: 836d34023f9264992ab6b86b06060cae3912e5df.
This is context, NOT permission to reset or pin current work to this older HEAD.
Read current HEAD and all applicable AGENTS.md first. Preserve uncommitted work.
The archive contains no patch to existing runtime files: you must connect at the
current call sites. Read README.md and docs/SECURITY_MODEL.md before integration.

1. Run the supplied 43-test suite and local example from the archive root.
2. Copy `bossman_shared/reality/` without replacing shared package initialization.
   Put tests under a dedicated repository test directory; ensure CI discovers them.
3. Confirm both Core and Command Center can import the SAME shared module. Find
   actual Core dispatch/finalization, Compound resume and Fleet completion paths.
   Do not guess names. Record each file and function in REALITY_INTEGRATION_REPORT.md.
4. Persist full Mission IR by actual mission/run identity BEFORE execution. Planner
   output is a proposal, not authority. Use RealityCompiler then Constitution.admit.
   Bind actor, action classification, policy and concrete target using trusted host
   metadata; model output cannot downgrade action risk or label clinical data PUBLIC.
5. Connect all participating tool dispatches through RealityRuntime or equivalent
   existing async boundary using store.claim BEFORE IO and store.confirm AFTER an
   independently observed receipt. Keep current permission checks, global budget
   reservations, operation ordering, cancellation and lease/fence checks intact.
   Avoid holding a DB transaction open during network IO. Do not call synchronous
   adapters directly on the async event loop: preserve the project's async model.
6. Register make_completion_hook for BCC's gate_completion. Inspected signature:
   (task, run_id, answer). It returns PASS or FAIL with explicit requeue=False.
   Missing IR for a participating run is FAIL, never NOT_APPLICABLE. Connect an
   equivalent hard gate to Core, Compound and Fleet finalization so none can bypass.
   Do not overwrite other gates or downgrade CriticalHookFailure.
7. Provide fresh, host-owned observers. Keep HMAC keys stable across restart and
   outside agent-readable memory, logs and tools. Reuse existing verifier identity
   and signer infrastructure if it provides equal or stronger binding guarantees.
   Independent reviewer means a distinct effective principal, not a renamed agent.
8. Recovery: reload exact immutable IR and escrow. Query the real target. For
   confirmed effects, issue a fresh dispatch-bound receipt using the stored fence
   and confirm. SAFE_TO_RETRY requires authoritative absence AND terminal old
   attempt status. Unknown/manual state must not be automatically requeued.
   Do not create a new mission/run ID merely to bypass unresolved escrow.
9. Use existing unified budget ledger before every paid call. The package's local
   reservation is additional per-mission accounting, never a second global ledger.
   Do not automatically refund ambiguous paid calls or rely on model cost estimates.
10. Wire support layers to existing services: dependency slicing at context assembly,
    choose/settle at router, compare_world after observation, divergence to persisted
    autonomy restriction, quarantine at skill/route admission, record_lesson after
    verified delta. Filter all learning text through existing privacy/redaction.
    Quarantine writes and success settlement are host-only. No auto skill promotion.
11. Use a feature flag default OFF for existing tasks until integration tests pass.
    For an opted-in run, persist participation before first effect; missing/erroring
    module must FAIL CLOSED. Never let flag changes bypass an already opted-in run.
12. Run the integration scenarios in docs/ACCEPTANCE.md. Then run relevant existing
    regression suites. Report environmental blockers separately from failures.
    Do not mark mock/fixture PASS as live PASS or say production-ready without proof.

## First concrete mission

Use a throwaway local git repository and controlled bare remote: reproduce a small
bug, capture the failure at base SHA, apply the fix, run the targeted test, commit,
push to the controlled remote, independently read remote SHA and exact tree/patch.
Compile explicit obligations for each piece of evidence. Do not use a single generic
"success=true" observer. Before-patch reproduction and after-patch tests must bind
to their respective immutable code trees. Scope regression claims to the actual
suite; "no unrelated regressions anywhere" is not a provable finite test claim.

## Finish

Create REALITY_INTEGRATION_REPORT.md with:
BASE_SHA, FINAL_SHA, changed files, Core/BCC/Compound/Fleet wiring table,
UNIT_TESTS, INTEGRATION_TESTS, EXISTING_REGRESSIONS, LIVE_CHECKS,
KNOWN_LIMITATIONS, OPEN_P0, OPEN_P1, ENABLED_MODES, rollback instructions.
Record commands and evidence references, not prompts/secrets/clinical payloads.
Do not claim remote push unless independently checked and already authorized in
this session. Preserve current work; no destructive reset, force push or policy bypass.
