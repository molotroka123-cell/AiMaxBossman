# V7 IMPLEMENTATION TZ — Adaptive Reality OS

## Mandatory freeze gate before implementation

**STOP. DO NOT START V7 CODE YET.**

V7 implementation is forbidden until the active V6 line has a current, exact-SHA verdict proving:

- no repository-fixable P0;
- no repository-fixable P1;
- no reproducible exact-SHA CI regression;
- current freeze report updated to the actual tested SHA;
- V6 decision is `PASS` or `REPO_COMPLETE_EXTERNAL_VALIDATION_PENDING`.

Owner Windows/local-model/real-provider evidence may remain external, but it must stay explicitly unproven.

Every model/agent must re-check this gate before coding V7. Do not rely on an old handoff.

---

## Phase 0 — Audit convergence and contracts

Deliverables:

- independent audits by multiple named models;
- `MASTER_MULTI_MODEL_AUDIT.md` with consensus, disagreements and rejected proposals;
- versioned Mission IR schema;
- World State Graph schema;
- strategy scoring contract;
- model-routing contract;
- attention/QoS contract;
- skill-promotion contract;
- V7 acceptance gates.

No broad production code in Phase 0.

Exit criteria:
- all safety inheritance mapped;
- no duplicate subsystem with existing Reality Compiler, memory, Fleet, recovery or V6 resource scheduling unless replacement is justified;
- migration plan exists;
- rollback boundaries exist.

## Phase 1 — World State + Mission IR

Implement the smallest vertical slice:

1. compile a user goal into versioned Mission IR;
2. reference typed world-state facts with provenance/freshness;
3. invalidate stale facts;
4. require fresh facts for specified effect obligations;
5. record current/desired state and post-state evidence.

Initial domains:
- filesystem;
- GitHub repository/PR state;
- process/runtime state;
- browser state.

Tests:
- stale fact cannot satisfy fresh obligation;
- foreign mission evidence rejected;
- wrong object identity rejected;
- provenance loss fails closed;
- replay preserves immutable source identity;
- post-state evidence cannot be backfilled from unrelated historical success.

## Phase 2 — Strategy Search

Implement multiple candidate paths for a bounded mission class.

Required:
- candidate generation;
- expected success band;
- latency/cost/resource/risk estimates;
- strategy selection explanation;
- deterministic fallback;
- strategy-switch evidence.

Do not use fake decimal probabilities if data is insufficient.

Tests:
- deterministic tool strategy beats model path when clearly cheaper/safer;
- privacy-restricted mission cannot choose disallowed cloud path;
- unavailable local model triggers rational fallback;
- repeated failure changes strategy instead of infinite retry;
- irreversible path receives appropriate risk penalty.

## Phase 3 — Adaptive Model Router + Cognitive Fabric

Required:
- capability registry;
- empirical task-class reliability;
- context/token estimate;
- latency estimate;
- cost estimate;
- residency/headroom input;
- model load single-flight;
- hysteresis;
- escalation policy;
- local-first privacy rules.

Target hardware optimization must support Ryzen AI Max+ 395 / Radeon 8060S / 128 GB unified memory, but runtime must never assume that hardware is present.

Tests:
- missing hardware metrics => no invented headroom;
- parallel callers do not reload same model N times;
- pressure causes safe eviction;
- owner-critical task preempts optional background warmup;
- low-quality small model escalates after measurable failure threshold;
- cloud route denied when privacy policy forbids it.

## Phase 4 — Dynamic Agent Teams

Implement mission-scoped temporary teams.

Rules:
- minimum team size;
- one-agent/deterministic path preferred where sufficient;
- each member has bounded context/tools/permissions;
- no agent can grant itself additional authority;
- team dissolves at mission end;
- results merge through explicit schema/evidence.

Tests:
- duplicate agents not created for identical role/work;
- conflicting results surface disagreement;
- verifier cannot silently rewrite executor evidence;
- team crash/restart preserves mission identity.

## Phase 5 — Counterfactual + Recovery Engine

Required:
- predicted state diff;
- blast radius;
- reversibility classification;
- rollback prerequisites;
- failure classification;
- alternate strategy selection.

Tests:
- effect attempted once across retry/recovery;
- ambiguous side-effect outcome does not auto-repeat;
- alternate path chosen after deterministic failure;
- rollback identity bound to the exact applied revision/effect;
- counterfactual never substitutes for fresh effect verification.

## Phase 6 — Attention / QoS Brain

Required:
- owner-interactive priority;
- deadline/blocker-aware priority;
- resource-pressure awareness;
- background yielding;
- queue wait telemetry;
- starvation prevention.

Tests:
- owner request preempts background export within bounded delay;
- background work resumes after idle;
- safety/recovery jobs cannot be starved by normal owner tasks;
- priority inversion is detected;
- cancellation does not corrupt held resources.

## Phase 7 — Verified Skill Factory

Only after Phase 1-6 are stable.

Pipeline:
`candidate → replay → shadow → red-team → benchmark → canary → promotion → rollback`

Promotion evidence must bind:
- skill version;
- model/runtime version where relevant;
- expected effects;
- benchmark cohort;
- exact code SHA;
- signer/authority;
- rollback target.

Tests:
- historical evidence cannot promote new revision;
- silent/failed canary member denies promotion;
- terminal failed promotion cannot be rewritten successful;
- rollback survives restart;
- skill cannot expand its own tool permissions.

## Phase 8 — Mission UX

Only after contracts work headlessly.

Primary view must communicate:
- intent;
- current state;
- desired state;
- strategy;
- confidence/uncertainty;
- resource allocation;
- approvals;
- proof;
- rollback.

Do not build UX first and invent backend semantics afterward.

---

## V7 performance requirements

Measure at minimum:
- Mission compile latency;
- strategy generation/selection latency;
- model routing latency;
- World State query/update latency;
- queue wait;
- first useful response;
- verified action latency;
- recovery time;
- model reload rate;
- process-tree RSS/CPU;
- real GPU/unified-memory metrics where measurable.

V7 must not regress V6 owner-visible latency without documented value and acceptance.

## V7 release gates

A V7 freeze-candidate requires:

- `OPEN_REPO_P0=0`;
- `OPEN_REPO_P1=0`;
- exact-SHA CI green or explicitly external-blocked;
- World State freshness hostile tests green;
- Mission IR replay/identity tests green;
- strategy fallback tests green;
- adaptive router negative controls green;
- effect-boundary inheritance green;
- recovery/no-double-effect tests green;
- canary/rollback for learned skills green;
- owner acceptance on representative missions;
- local-model validation on owner hardware when hardware-dependent claims are made;
- no fabricated performance/resource numbers.

Final verdict enum:
- `PASS`
- `BLOCKED`
- `REPO_COMPLETE_EXTERNAL_VALIDATION_PENDING`
