# V7 Priorities

## Auditor Metadata

- **AUDITOR_MODEL**: Perplexity
- **AUDITOR_PROVIDER**: Perplexity AI
- **AUDITED_SHA**: bed5e8c9ad2846d9669ac3c0462cfd02c2cd6bd5
- **DATE**: 2026-09-07

---

## P0 — V7 Must Have (Maximum 5)

1. **World State Graph (WSG) v0.1**: Minimal schema for missions, actions, effects, evidence. Queryable via SQL or GraphQL. [REUSES: existing evidence system; MODIFIES: state representation; REPLACES: nothing; CONFLICTS_WITH: none if WSG is additive; MIGRATION: gradual; ROLLBACK: disable WSG queries, fall back to existing state]

2. **Strategy Search v0.1**: Generate ≥2 candidate strategies for multi-step missions, score by cost/latency/success-probability, present to owner. [REUSES: existing mission system; MODIFIES: strategy selection; REPLACES: heuristic-only selection; CONFLICTS_WITH: none; MIGRATION: opt-in for complex missions; ROLLBACK: revert to heuristic selection]

3. **Unified Memory Scheduler v0.1**: Single policy for context residency across Fleet, skills, local models. [REUSES: existing context systems; MODIFIES: memory policies; REPLACES: fragmented per-subsystem policies; CONFLICTS_WITH: existing hard-coded context limits; MIGRATION: gradual rollout per subsystem; ROLLBACK: revert to per-subsystem policies]

4. **Reality Compiler 2.0 Core**: WSG-integrated effect verification with explicit pre/post state snapshots. [REUSES: RC v0.1; MODIFIES: effect tracking; REPLACES: ad-hoc state deltas; CONFLICTS_WITH: none; MIGRATION: parallel run with RC v0.1; ROLLBACK: disable RC 2.0, use v0.1]

5. **Goal-First UX v0.1**: Mission timeline view with strategy explainer and cognitive load indicator. [REUSES: existing dashboard; MODIFIES: UI components; REPLACES: fragmented views; CONFLICTS_WITH: none; MIGRATION: new route alongside existing; ROLLBACK: hide new UI, keep existing]

---

## P1 — High Value (Maximum 10)

1. Counterfactual simulation for irreversible actions
2. Explicit state versioning with TTL and freshness tracking
3. Strategy outcome learning (which strategies succeed/fail under what conditions)
4. Skill promotion criteria based on verified outcomes
5. Automated replay from successful traces
6. Resource contention management under multi-agent load
7. Privacy fencing at WSG level (what subsystems can see what state)
8. Budget enforcement at orchestration layer (cost caps per mission)
9. Recovery planning (pre-computed rollback options with costs)
10. Observability dashboard for WSG queries and strategy history

---

## P2 — Useful Later (Maximum 10)

1. Full counterfactual simulation UI (interactive "what if" explorer)
2. Automated strategy generation from mission goals (not just selection)
3. Cross-mission optimization (global resource allocation)
4. Predictive latency modeling (estimate before execution)
5. Owner cognitive load prediction (warn before high-load missions)
6. Skill marketplace (discover and import skills from other Bossman instances)
7. Multi-owner collaboration (shared WSG with access control)
8. External system integration (sync WSG with Jira, GitHub, Notion)
9. Historical analytics (strategy success rates over time)
10. Natural language WSG queries ("show me all failed actions this week")

---

## REJECTED

1. **Complete rewrite of Reality Compiler**: RC v0.1 works; evolve it, don't replace it.
2. **New approval system**: Existing approval gates are sufficient; integrate with WSG instead.
3. **Separate learning database**: Learning should use WSG as source of truth — no competing databases.
4. **Real-time strategy search for all actions**: Too expensive; use only for multi-step or high-stakes missions.
5. **Full counterfactual simulation in V7.0**: Too complex for initial release; defer to V7.1+.

---

*Independent audit by: Perplexity*
