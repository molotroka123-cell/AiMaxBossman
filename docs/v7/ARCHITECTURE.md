# V7 Architecture — Adaptive Reality OS

## 1. Architectural objective

V7 must make Bossman capable of choosing **how** to achieve a goal instead of only executing a supplied workflow.

The architecture is:

`OWNER INTENT`
→ `REALITY COMPILER`
→ `MISSION IR`
→ `WORLD STATE GRAPH`
→ `STRATEGY GENERATOR`
→ `COUNTERFACTUAL / COST / RISK EVALUATION`
→ `STRATEGY SELECTOR`
→ `DYNAMIC TEAM + MODEL ROUTER`
→ `EXECUTION GRAPH`
→ `FRESH EFFECT VERIFICATION`
→ `TOOLS / COMPUTER / APPS / CODE / MEDIA`
→ `POST-STATE OBSERVATION`
→ `PROOF / ROLLBACK`
→ `VERIFIED LEARNING`

## 2. Reality Compiler 2.0

Every mission becomes a versioned `MissionIR`.

Minimum fields:

- `mission_id`
- `owner_intent`
- `current_state_refs[]`
- `desired_state[]`
- `constraints[]`
- `effect_obligations[]`
- `forbidden_effects[]`
- `permission_scope`
- `privacy_scope`
- `budget`
- `deadline`
- `success_evidence[]`
- `rollback_plan`
- `risk_class`
- `freshness_requirements[]`
- `strategy_policy`
- `model_policy`
- `human_escalation_policy`
- `compiler_version`

Mission compilation must be deterministic enough to diff, replay and audit.

No mission can reach an effectful tool if its required obligations are missing or unresolved.

## 3. World State Graph

V7 needs a typed reality layer rather than more free-form memory.

Each fact must include:

- subject/object identity;
- typed relation/value;
- provenance;
- observed_at;
- valid_until / freshness class;
- confidence;
- authority/ownership;
- privacy class;
- invalidation dependency;
- evidence reference.

Examples:

- PR #49 `state=open`, observed 12 s ago via GitHub.
- local model X `resident=true`, measured 2 s ago from runtime.
- file Y `sha256=...`, observed after write.
- browser session Z `url=...`, fresh screenshot generation 123.

### Required rule

A stale or low-confidence fact may support planning, but may not satisfy a fresh effect-boundary obligation.

## 4. Strategy Engine

For non-trivial tasks, generate candidate strategies, for example:

- deterministic tool path;
- one local large model;
- several smaller specialists;
- local first + cloud escalation;
- browser path;
- direct API path;
- code generation + tests;
- human-assisted path.

Each strategy receives an evidence-backed estimate:

`Utility = success_probability × mission_value - latency_penalty - monetary_cost - resource_cost - risk_penalty - irreversibility_penalty`

Do not expose fake precision. Confidence intervals / qualitative bands are acceptable when evidence is weak.

## 5. Adaptive Model Router

Models are resources, not permanent roles.

Routing dimensions:

- capability match;
- tool/schema reliability;
- structured-output reliability;
- historical success on this task class;
- expected latency;
- context size;
- token cost;
- local memory requirement;
- current residency;
- queue pressure;
- privacy requirements;
- fallback/escalation cost.

The router should prefer the cheapest/smallest model that clears the quality threshold.

Escalation should be evidence-driven, e.g.:

small local → medium local → large local → cloud frontier → human.

## 6. Local Cognitive Fabric

On unified-memory hardware, model scheduling must share one memory truth.

Components:

- residency manager;
- model load single-flight;
- load/reload counters;
- hysteresis to prevent thrashing;
- predicted working-set estimator;
- measured headroom only;
- priority-aware eviction;
- owner-interactive reserve;
- background-job throttling.

Never assume 128 GB merely because target hardware is expected. Missing measurement remains `UNMEASURED` and admission fails closed where required.

## 7. Dynamic Agent Teams

Teams are mission-scoped and ephemeral.

Examples:

- research: Scout + Synthesizer + Verifier;
- coding: Planner + Coder + Test/Review;
- browser task: Operator + State Verifier;
- simple deterministic action: zero additional agents.

Team creation must have a measurable benefit. Avoid agent inflation.

Each member receives:

- role contract;
- allowed tools;
- context budget;
- effect permissions;
- result schema;
- stop condition.

## 8. Counterfactual layer

Before risky/irreversible work, simulate:

- likely state diff;
- cost/resource effect;
- blast radius;
- permission implications;
- recovery path;
- evidence required after execution.

Counterfactual simulation never replaces real fresh verification.

## 9. Attention + QoS Brain

Priority classes:

1. owner-interactive;
2. effect-bound verification/recovery;
3. active mission critical-path;
4. normal mission background;
5. export/index/training/maintenance.

Priority adapts based on:

- owner waiting;
- deadline proximity;
- blocked dependency;
- resource pressure;
- failure/retry loop;
- safety/recovery urgency.

Background throughput must yield before owner-visible latency becomes unacceptable.

## 10. Recovery as strategy change

V7 recovery is not unlimited retry.

Failure policy:

1. classify failure;
2. decide whether retry is rational;
3. if not, choose alternate strategy;
4. preserve evidence of failed attempts;
5. avoid duplicate irreversible effects;
6. escalate only when alternatives are exhausted or policy requires it.

Examples:

browser selector fails → accessibility tree → app/API route → alternate browser session → human.

## 11. Verified Skill Factory

A successful execution path is only a candidate skill.

Promotion pipeline:

`candidate`
→ `replay`
→ `shadow`
→ `adversarial tests`
→ `benchmark`
→ `canary`
→ `promotion`

Rollback must be immediate and durable.

No self-generated skill receives production authority merely because one mission succeeded.

## 12. Mission UX

The owner-facing center of V7 should show:

- Goal
- Current reality
- Desired reality
- Selected strategy
- Why this strategy
- Agents/models/tools currently allocated
- Budget/time/resource envelope
- What requires approval
- What has been proven
- What remains uncertain
- Rollback state

Specialized editors remain workspaces, not the organizing principle of the OS.

## 13. Required invariants

V7 implementation must preserve:

- no effect without satisfied obligations;
- no stale evidence satisfying a fresh obligation;
- no model/agent self-promotion to additional authority;
- no invented resource state;
- no duplicated irreversible effect due to retries;
- terminal evidence immutability where inherited;
- durable rollback identity;
- human approval cannot rewrite factual failure into success;
- missing evidence remains missing.
