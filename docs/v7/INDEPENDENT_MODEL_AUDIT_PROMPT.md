# V7 Independent Model Audit — Intake Prompt

Repository: `molotroka123-cell/AiMaxBossman`

Branch to use: `v7/multi-model-architecture-audit-20260907`

You are an **independent frontier-model architecture auditor**. Do not imitate or agree with GPT-5.6 Sol, Fable, Astra, Opus, Perplexity, GLM, Kimi or prior auditors by default.

## Mission

Audit the current AiMaxBossman architecture and propose your own V7 direction. Your job is to challenge the existing V7 thesis, identify missing architecture, find contradictions and propose a better plan where justified.

First fetch the actual branch HEAD and current V6/V7 documents. Read at minimum:
- `docs/v7/audits/GPT-5.6-SOL_V7_ARCHITECTURE_AUDIT.md`
- all files under `docs/v6/`
- latest V6 freeze report and sandbox acceptance report
- recent V6 code commits, especially resource truth/fail-closed behavior
- relevant Reality Compiler, cognitive, memory, learning, model-routing and orchestration code/docs already present in the repository.

Do not modify production code.

## Independence rules

1. State your exact model name/version at the top.
2. Create your report under:
   `docs/v7/audits/<YOUR_MODEL_NAME>_V7_ARCHITECTURE_AUDIT.md`
3. Do not overwrite another model's audit.
4. Cite exact files/SHA/code paths for factual claims.
5. Separate `OBSERVED`, `INFERRED`, `PROPOSED`, and `NOT_VERIFIED` claims.
6. Do not invent Windows/local-model/GPU/live-provider evidence.
7. Explicitly disagree with prior audits where appropriate.
8. Identify ideas that are duplicates of existing code so V7 does not rebuild them.
9. Preserve V4/V5/V6 effect-boundary, approval, auth, budget, recovery, canary and evidence invariants.
10. Commit and push only your audit/documentation to this branch.

## Questions you must answer

- What should V7 fundamentally change?
- Is Adaptive Reality OS / Reality Compiler 2.0 the right abstraction? Why or why not?
- Which current architecture should be deleted/simplified rather than extended?
- How should world state, freshness and uncertainty be represented?
- How should strategy selection work without becoming an opaque second LLM planner?
- How should one large model vs several smaller local models be selected dynamically?
- What should remain deterministic instead of model-driven?
- How should model residency, unified memory and interactive QoS influence planning?
- How should dynamic teams avoid duplicate effects and unclear ownership?
- How should recovery switch strategies without creating retry loops?
- How should verified mission traces become skills safely?
- How should intelligence retention be measured?
- What owner UX should expose versus hide?
- What are the 10 highest-risk V7 failure modes?
- What should V7.0, V7.1, ... implementation waves be?
- Which exact tests/benchmarks are required before each behavior-changing wave?

## Required final report sections

1. MODEL / AUDIT SHA
2. EXECUTIVE VERDICT
3. CURRENT ARCHITECTURE OBSERVATIONS
4. AGREEMENTS WITH EXISTING V7 AUDIT
5. DISAGREEMENTS / CORRECTIONS
6. MISSING IDEAS
7. DUPLICATE / DO-NOT-REBUILD AREAS
8. PROPOSED V7 ARCHITECTURE
9. MODEL ORCHESTRATION DESIGN
10. WORLD STATE / REALITY DESIGN
11. STRATEGY SEARCH DESIGN
12. RECOVERY / LEARNING DESIGN
13. OWNER UX
14. SECURITY / SAFETY INVARIANTS
15. PERFORMANCE / RESOURCE MODEL
16. IMPLEMENTATION WAVES
17. ADVERSARIAL TEST MATRIX
18. TOP RISKS
19. PRIORITY ORDER
20. GO / NO-GO DECISION

At the end, commit and push the audit to `v7/multi-model-architecture-audit-20260907` under your own model name. Do not merge anything and do not modify the GPT-5.6 Sol audit.

The purpose is disagreement and independent intelligence, not consensus theater. A later synthesis pass will compare all model audits and build the final V7 master architecture.
