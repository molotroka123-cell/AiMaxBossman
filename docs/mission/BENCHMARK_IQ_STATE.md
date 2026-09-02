# BENCHMARK_SYSTEM_IQ-001 — FINAL AUDIT (updated)

START_SHA=d5480b3 · FINAL_REMOTE_SHA=1377c9e+ (freeze addendum commit) · branch claude/bossman-control-v03-43igbk

## Commits
- 9afed95 honest release gate: 18 required capability IDs, strict tiers, SystemIQ/PureCodingIQ from measured REAL/LIVE telemetry, CLI exit 1 on NO-GO, manifest v1.2.0 (capability bindings, release_requires_live)
- 1377c9e HARD-$3 budget: cross-process transactional lock (msvcrt/fcntl), mission/owner/request binding, RECONCILING hold on failure (survives restart), trusted_reconcile-only settlement, actual>reserved refused, double commit refused, unknown price REFUSED, separate cache rates, 3.0 chars/token upper bound
- freeze addendum: Fable-originated HMAC manifest MAC pinning (opt-in, OFF default) + review evidence + this audit

## API budget ledger (cap $3.00; stop at $2.70; reserve $0.30)
| # | provider/model | purpose | request_id | in/out | cost |
|---|---|---|---|---|---|
| 1 | anthropic-direct/claude-sonnet-4-5 | IQ-review of implementer diff | req_011CefFwJRBc2Y7r2HEVpuHC | 5203/1200 | $0.033609 |
Total actual: $0.033609 (reserved $0.90 worst-case per call, released/committed atomically). All prices known → all calls permitted.

## Fable independent verification of BENCHMARK_SYSTEM_IQ_IMPLEMENTER
Findings triaged against evidence:
1. "lock not implemented" — FALSE POSITIVE: `_CrossProcessFileLock` implemented above excerpt; 5-process concurrency test passes (exactly 3×$1.0 granted).
2. "manifest forgery vector" — PARTIALLY VALID → accepted: HMAC MAC pinning implemented (opt-in secret, forgery → NO-GO), tested valid/invalid/OFF.
3. "child can claim evidence_class=LIVE" — FALSE POSITIVE: runner assigns class from manifest (engine comment + test_bench_mode_002 lying-child FAIL).

## Test totals (targeted, this mission)
benchmark gate+truth 11 passed · budget adversarial 9 passed (incl. cross-process 5×reserve) · UCA/adversarial/durable/outreach/teacher regression 47 passed 1 honest skip

## Capability coverage matrix (honest)
measured REAL/LIVE: persistence, recovery (2/18). Remaining 16 (model_selection, router, fast_heavy_policy, working_state, context_selection, raw_context_fallback, dag_compiler, adaptive_reasoning, verifier, prompt_cache, local_cognitive_reuse, budget_router, approval, idempotency, prompt_injection_defence, universal_computer_apprentice) — coverage REQUIRES new REAL_SANDBOX/LIVE cases; until then release gate = NO-GO (by design, the false-READY fix).

## Scores at HEAD
RegressionScore 1.0 (n=21) · RealCapabilityScore 1.0 (n=4, REAL_SANDBOX) · LiveCapabilityScore INSUFFICIENT (n=0) · SystemIQ MEASURED on real rows (release tier) · PureCodingIQ MEASURED (repair+sandbox rows) · smoke/pr READY, release/nightly NO-GO (honest).

## Three scenarios
Higgsfield: BLOCKED_BY_ENVIRONMENT (real attempt, signup wall) · Bug repair: LIVE PASS (1 teacher call; Bug B reuse 0 calls) · Maps/outreach: discovery REAL; send technically impossible without issued owner approval.

## Remaining threats / rollback / verdict
Threats: 16 unmeasured capabilities (work items, not defects); MAC secret management is owner-side; CLI/relay billing ambiguity stays out of direct transport.
Rollback: git revert 9afed95 / 1377c9e / freeze-addendum independently.
**READY=NO for release gate (honest, capability coverage pending) — false READY eliminated. Mission objective (truthful measurement system) = DONE.**
