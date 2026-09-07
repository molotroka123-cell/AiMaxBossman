# AiMaxBossman V7 — Adaptive Reality OS

Status: **DESIGN + MULTI-MODEL AUDIT INTAKE ONLY**  
Base: `v6/velocity-phase0-baseline-20260907` @ `5f75dc55ff0376ef7774526cbed88b50efd638ff`  
V7 branch: `v7/adaptive-reality-os-audit-20260907`

> ## ⛔ MANDATORY V6 FREEZE BARRIER — EVERY MODEL/AGENT MUST READ
>
> **DO NOT START V7 PRODUCTION IMPLEMENTATION YET.**
>
> Claude/Fable is currently finishing the V6 freeze. Before writing any V7 production code, every model/agent must re-fetch the active V6 branch and independently verify:
>
> - exact current V6 source SHA + tree SHA;
> - `OPEN_REPO_P0 = 0`;
> - `OPEN_REPO_P1 = 0`;
> - no reproducible exact-SHA CI regression remains;
> - current V6 freeze report points to the actual tested code SHA;
> - V6 verdict is `PASS` or `REPO_COMPLETE_EXTERNAL_VALIDATION_PENDING`.
>
> Owner Windows/local-model/real-provider/real-media evidence may remain explicitly external. Missing external evidence must **not** be restamped as PASS.
>
> Until this barrier is satisfied, V7 work is limited to **audit, architecture, specifications, hostile-test design and migration planning**.
>
> No model may treat this README's historical base SHA as proof that V6 is frozen. Repository truth at execution time wins.

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

## Documentation map — mandatory reading order

Every model working on V7 must read in this order:

1. `README.md` — this freeze barrier and V7 thesis.
2. `IMPLEMENTATION_TZ.md` — full phased implementation specification and release gates.
3. `ARCHITECTURE.md` — proposed runtime contracts.
4. `SOL_AUDIT.md` — GPT-5.6 Sol independent architecture audit and critique.
5. `MASTER_MULTI_MODEL_AUDIT.md` — convergence rules; disagreements must not be erased.
6. `INDEPENDENT_MODEL_AUDIT_PROMPT.md` — mandatory protocol for every additional frontier-model audit.
7. `FABLE5_CORRECTION_PROMPT.md` — narrow V6 completion handoff; not a V7 implementation prompt.

## Multi-model audit rule

No additional model may overwrite another model's audit. Each independent model must publish under:

`docs/v7/audits/<MODEL_SLUG>_AUDIT.md`

with its exact model identity and inspected SHA. After enough independent views exist, they are reconciled into `MASTER_MULTI_MODEL_AUDIT.md` with explicit consensus, disagreements, rejected ideas and evidence requirements.

## Entry gate

Broad V7 implementation begins only after the Mandatory V6 Freeze Barrier at the top of this document is satisfied and the owner explicitly starts implementation.
