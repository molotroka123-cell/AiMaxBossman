# AiMaxBossman V7 — Adaptive Reality OS

Status: **DESIGN + MULTI-MODEL AUDIT INTAKE**  
Base: `v6/velocity-phase0-baseline-20260907` @ `5f75dc55ff0376ef7774526cbed88b50efd638ff`  
V7 branch: `v7/adaptive-reality-os-audit-20260907`

## Why V7 exists

V6 is the velocity/reliability pass: startup, lazy UI, single-flight, phase timing, resource truth, owner-session bug closure and exact-SHA evidence.

V7 must **not** become another feature pile or dashboard redesign. The next architectural step is to turn Bossman from a task executor into an adaptive decision system:

`intent → current world state → desired state → constraints → strategy candidates → simulation → execution → verification → learning`

The owner should specify the goal. Bossman should choose the execution strategy, prove the result, recover when the first strategy fails and learn only from verified outcomes.

## V7 thesis

**Task Executor → Adaptive Reality OS**

The system should treat models, agents and tools as resources. The durable intelligence lives in orchestration, state truth, policy, evidence and learning—not in assuming one model is always the brain.

## Core V7 workstreams

1. **Reality Compiler 2.0** — compile human goals into a versioned Mission IR with current state, desired state, effect obligations, permissions, privacy, budget, deadline, success evidence and rollback.
2. **World State Graph** — typed objects + facts + provenance + freshness + confidence + ownership + invalidation.
3. **Strategy Search** — generate multiple execution strategies and select by expected utility rather than first-plan wins.
4. **Adaptive Model Orchestration** — route by capability, quality, latency, cost, memory pressure and observed reliability.
5. **Dynamic Agent Teams** — create the minimum temporary team needed for a mission and dissolve it afterward.
6. **Counterfactual Verification** — cheaply simulate likely effects and failure modes before irreversible actions.
7. **Autonomous Recovery** — change strategy, not merely retry the same broken path.
8. **Attention / QoS Brain** — owner-visible work outranks background work; priorities adapt to deadlines, blockers and resource pressure.
9. **Verified Skill Factory** — successful missions may become reusable skills only through replay → shadow → adversarial tests → benchmark → canary → promotion.
10. **Local Cognitive Fabric** — orchestrate multiple local models on unified memory as one resource pool rather than forcing the owner to select models manually.

## Non-goals

V7 is not:
- 50 more dashboard pages;
- another generic memory rewrite;
- dozens of permanently running agents;
- a model zoo without routing evidence;
- self-modification without verification;
- speculative AGI branding;
- weakening V4/V5/V6 safety or effect boundaries for autonomy.

## Safety inheritance

V7 inherits and must not weaken:
- effect-obligation verification;
- fresh pre-effect observations where required;
- approvals and authorization;
- budget/cost limits;
- fencing and lease ownership;
- journal/recovery consistency;
- post-state evidence;
- canary/rollback;
- fail-closed behavior when state/resource evidence is missing.

V7 adds autonomy **above** these boundaries, never by bypassing them.

## Documentation map

- `ARCHITECTURE.md` — proposed runtime architecture and contracts.
- `SOL_AUDIT.md` — independent GPT-5.6 Sol architecture audit and critique.
- `FABLE5_CORRECTION_PROMPT.md` — narrow V6 completion prompt before implementation starts.
- `INDEPENDENT_MODEL_AUDIT_PROMPT.md` — prompt for another frontier model to add its own signed audit/vision in this branch.
- `MASTER_MULTI_MODEL_AUDIT.md` — synthesis contract for reconciling all model audits without erasing disagreement.

## Entry gate

Do **not** start broad V7 implementation until V6 is at least `REPO_COMPLETE_EXTERNAL_VALIDATION_PENDING` with no known repository-fixable P0/P1 and no unresolved exact-SHA CI regression.

Owner-machine Windows/local-model evidence may remain external, but V7 design must preserve those evidence gaps rather than restamping them as passes.
