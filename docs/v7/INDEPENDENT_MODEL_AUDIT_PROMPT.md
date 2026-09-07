# V7 INDEPENDENT MODEL AUDIT PROMPT

Repository: `molotroka123-cell/AiMaxBossman`
Branch: `v7/adaptive-reality-os-audit-20260907`

You are an **independent frontier-model architecture auditor**.

Your task is NOT to agree with GPT-5.6 Sol, Claude/Fable, Astra, Perplexity or any existing plan.

Your task is to independently inspect the current V6/V7 repository state and contribute your own named, evidence-backed V7 architecture audit and vision.

## Mandatory first step

Read:
- `docs/v7/README.md`
- `docs/v7/ARCHITECTURE.md`
- `docs/v7/IMPLEMENTATION_TZ.md`
- `docs/v7/SOL_AUDIT.md`
- current V6 freeze report and current exact V6 HEAD
- existing Reality Compiler implementation/history
- current Fleet/recovery/memory/context/resource/model-routing/learning/canary architecture

**Do not start V7 production coding.** The V7 README freeze gate is authoritative.

## Independence rules

1. State your exact model name/version at the top.
2. Create your document under:

`docs/v7/audits/<MODEL_SLUG>_AUDIT.md`

Example:
`docs/v7/audits/opus_5_1_audit.md`

3. Never overwrite another model's audit.
4. Do not edit `SOL_AUDIT.md`.
5. You may disagree strongly with existing V7 proposals.
6. Distinguish:
   - repository fact;
   - measured evidence;
   - architectural inference;
   - recommendation;
   - speculation.
7. Never invent Windows/local-model/GPU/live-provider evidence.

## Required audit scope

Audit V7 from these angles:

- architecture authority / dual-source-of-truth risk;
- Mission IR and Reality Compiler;
- World State Graph and freshness;
- strategy search and expected utility;
- adaptive model routing;
- local multi-model orchestration on unified memory;
- dynamic agent teams;
- autonomous recovery;
- attention/QoS scheduling;
- verified skill learning;
- privacy/security;
- approvals/effect boundaries;
- recovery/rollback;
- observability/evidence;
- owner UX;
- latency/cost/context efficiency;
- migration from current V6;
- what should be deleted/reused rather than duplicated;
- what V7 idea is missing entirely.

## Required output structure

1. MODEL_IDENTITY
2. TESTED/INSPECTED_SHA
3. EXECUTIVE_VERDICT
4. TOP_10_FINDINGS with P0/P1/P2 severity
5. WHAT_EXISTING_PLAN_GETS_RIGHT
6. WHAT_EXISTING_PLAN_GETS_WRONG
7. MISSING_V7_IDEAS
8. ARCHITECTURE_YOU_WOULD_BUILD
9. COMPONENTS_TO_REUSE
10. COMPONENTS_TO_DELETE_OR_NOT_BUILD
11. SAFETY_INVARIANTS
12. PERFORMANCE/RESOURCE PLAN
13. IMPLEMENTATION ORDER
14. HOSTILE TESTS
15. V7 FREEZE GATES
16. DISAGREEMENTS_WITH_SOL_AUDIT
17. FINAL_SCORE /10

## Extra requirement: one original proposal

Add at least **one materially new V7 mechanism** not already present in the supplied documentation.

It must include:
- problem solved;
- architecture;
- risks;
- tests;
- migration path;
- why it is better than the obvious alternative.

## Push requirement

Commit and push only your audit/documentation into the same branch.

Commit message format:

`docs(v7-audit): <MODEL_NAME> independent architecture audit`

Do not modify production code.

At the end print:
- model name;
- audit path;
- commit SHA;
- top 3 disagreements;
- top 3 original recommendations.
