# Fable — 10 Organization Layer innovations

## Mandate
The items below are directions, not ceilings. Fable may improve, redesign, extend, merge, split or replace the proposed implementation when repository evidence supports a better solution. Do not ask for approval for ordinary engineering improvements inside this stage.

Non-negotiable: preserve verified-action truthfulness, permissions/security, restart/idempotency, project isolation and test integrity. Never store private chain-of-thought or fabricate execution evidence.

For each item: **Proposal → Foundation → Explanation → Fable discretion**.

### 1. Dynamic Organization Graph
**Proposal:** Form temporary cross-department teams per mission instead of relying only on a rigid tree.
**Foundation:** Organization registry + durable mission/task state + role/capability metadata + Executive OS bridge.
**Explanation:** A release can require Engineering, Research and QA simultaneously; Bossman should form, own and dissolve such teams automatically.
**Fable discretion:** Redesign graph schemas, lifecycle, APIs and scheduling freely if the repository supports a cleaner production design.

### 2. Agent Capability Marketplace
**Proposal:** Match work to agents/models by capability, quality, cost, latency, load and historical success.
**Foundation:** Router + agent registry + KPI store + budget controls.
**Explanation:** Managers request capabilities rather than hard-code a model; cheap/local workers win when reliable, with evidence-driven escalation.
**Fable discretion:** Improve scoring, bidding, fallback and deterministic routing as useful.

### 3. Adaptive Team Formation
**Proposal:** Select team size and roles from task complexity and risk.
**Foundation:** Mission decomposition + organization graph + delegation contracts + capability marketplace.
**Explanation:** Small tasks may need one executor; releases may need lead, coder, reviewer and QA. Avoid permanent agent swarms.
**Fable discretion:** Improve complexity/risk heuristics, templates, role merging and escalation.

### 4. Organizational Memory & Knowledge Flow
**Proposal:** Department-scoped knowledge with explicit controlled sharing.
**Foundation:** V3 typed memory/context + scope/provenance/access policy.
**Explanation:** Engineering keeps engineering lessons; business projects keep their own context. Cross-team knowledge is deliberately exported/imported to prevent contamination.
**Fable discretion:** Improve retrieval, promotion/demotion, conflict and stale-memory handling while preserving isolation.

### 5. Delegation Contract 2.0
**Proposal:** Typed delegation contracts containing goal, inputs, constraints, deliverables, evidence, budget, risk and escalation.
**Foundation:** Existing delegation contract + Executive task state + V2 verification receipts.
**Explanation:** Child agents receive an objective definition of done; parent completion depends on verified deliverables, never prose claims.
**Fable discretion:** Extend formats and validators whenever correctness improves.

### 6. Independent Review & Adversarial QA
**Proposal:** Separate producer and verifier for important work.
**Foundation:** Roles + routing + evidence contracts + quality/KPI governor.
**Explanation:** Coder/reviewer, researcher/verifier and high risk executor/risk reviewer pairs reduce correlated false success without wasting agents on trivial tasks.
**Fable discretion:** Design risk tiers, sampling, quorum and reviewer selection from measured value.

### 7. Organizational Learning Loop
**Proposal:** Improve routing/team formation from mission outcomes without recording hidden reasoning.
**Foundation:** KPI + failed-approach memory + receipts + mission history.
**Explanation:** Learn from success rate, cost, retries, failures and verification results so future teams become cheaper and more reliable.
**Fable discretion:** Add bounded confidence/decay/experimentation/bandit methods if auditable and tested.

### 8. Budget Economy & Resource Treasury
**Proposal:** Department/mission budgets for local-context units, cloud money, local compute and time.
**Foundation:** Executive Governor + router + cost/KPI telemetry.
**Explanation:** Prefer local compute, justify expensive escalation, reserve resources and stop runaway agents.
**Fable discretion:** Improve accounting, quotas, reservations and forecasting. Never claim hard provider cost enforcement if unavailable.

### 9. Event-Driven Organization
**Proposal:** Verified events can create organizational work.
**Foundation:** Durable task state + workflows/events + permission gates + routing.
**Explanation:** Failing CI can create Engineering triage; verified changes can trigger QA; schedules can trigger reports. Events create bounded tasks, not uncontrolled side effects.
**Fable discretion:** Add dedup, replay protection, priority and backpressure as appropriate.

### 10. CEO Control Plane & Organization Digital Twin
**Proposal:** One authoritative organizational state for departments, agents, missions, ownership, dependencies, budgets, blockers, risk, KPI and verified completion.
**Foundation:** Organization store + mission state + Executive bridge + telemetry.
**Explanation:** “What is the company doing?” should have a truthful machine-readable answer and become the foundation for later Fleet/Distributed Bossman.
**Fable discretion:** Improve control-plane models, APIs and UI projections freely; observable truth beats decorative dashboards.

## Global autonomy clause
Fable is explicitly authorized to discover adjacent Organization Layer weaknesses; refactor supplied code; add missing production primitives/tests; improve schemas/APIs, local-first economics, portability, observability, recovery and performance; and introduce additional Organization Layer innovations on its own judgment.

Boundaries:
1. Never weaken V2/V3 evidence, permission, idempotency or security invariants.
2. Do not silently expand into Fleet Mode except for small forward-compatible interfaces.
3. Do not replace working architecture with fashionable infrastructure without evidence.
4. Validate changed behavior with targeted tests and broader regression when practical.
5. Record material deviations and why the new design is better.
6. Never claim tests/integration not actually run.
7. Prefer a coherent production-ready layer over many half-built features.

Success: Bossman behaves like a small auditable AI organization whose teams can form, delegate, execute, verify, learn, budget resources and report truthful state.
