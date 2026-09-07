# V7 Multi-Model Synthesis Protocol

This branch is an **audit convergence lane**, not a production implementation lane.

## Goal

Collect independent architecture audits from multiple frontier models, preserve disagreements, then produce one evidence-weighted V7 master architecture rather than averaging prose.

## Audit namespace

Every auditor writes exactly one primary file:

`docs/v7/audits/<MODEL>_V7_ARCHITECTURE_AUDIT.md`

Never overwrite another auditor's report.

## Claim taxonomy

Each synthesis item should be classified as:
- `OBSERVED_CURRENT_CODE`
- `OBSERVED_TEST_EVIDENCE`
- `INFERRED_BOTTLENECK`
- `PROPOSED_ARCHITECTURE`
- `EXTERNAL_EVIDENCE_GAP`
- `REJECTED_DUPLICATE`
- `REJECTED_SAFETY_CONFLICT`

## Scoring proposals

Do not count votes. Score each proposal on:
1. expected owner value;
2. evidence strength;
3. architectural leverage;
4. implementation complexity;
5. latency/resource effect;
6. correctness risk;
7. security/effect-boundary risk;
8. rollback quality;
9. testability;
10. duplication with existing Bossman capabilities.

A minority proposal with strong evidence can beat a majority opinion.

## Conflict resolution

For every material disagreement record:
- Model A claim;
- Model B claim;
- current code/evidence;
- experiment or code inspection that could falsify each claim;
- synthesis decision;
- confidence;
- whether decision is reversible.

If evidence cannot resolve the conflict, keep it open. Do not manufacture consensus.

## Final synthesis outputs

After at least two independent audits exist, create:

- `docs/v7/V7_MASTER_ARCHITECTURE_AUDIT.md`
- `docs/v7/V7_IMPLEMENTATION_PLAN.md`
- `docs/v7/V7_ACCEPTANCE_GATES.md`
- `docs/v7/V7_RISK_AND_ROLLBACK.md`
- `docs/v7/V7_MODEL_FABRIC_SPEC.md`
- `docs/v7/V7_WORLD_STATE_SPEC.md`
- `docs/v7/V7_MISSION_IR_SPEC.md`

The master audit must identify which model originated each material idea and whether it was accepted, modified, deferred or rejected.

## Activation boundary

No V7 audit consensus may directly expand effect authority. Production activation requires normal code review, exact-SHA tests, safety invariants, shadow evidence, canary and rollback.
