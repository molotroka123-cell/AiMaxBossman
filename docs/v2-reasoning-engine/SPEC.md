# Bossman V2 — LLM Runtime & Reasoning Engine

> **Status:** Draft — pending audit of `bcc/v2` before implementation begins  
> **Branch:** `claude/v2-reasoning-engine`  
> **Scope:** LLM execution quality, routing, verification, repair — no memory layer changes

---

## Principles

1. Runtime never trusts model prose as evidence — only external observation counts.
2. Every action is confirmed by real execution result.
3. The cheapest sufficient model handles each task.
4. All inter-stage contracts are schema-validated.
5. High-risk actions require a policy/approval gate.
6. Retry, replan, escalation, and abort are always bounded and observable.
7. Multi-agent reasoning is enabled only when there is measurable benefit.

---

## Execution Graph

```
TASK
  ↓
TaskClassifier          → task_class, risk_level, reasoning_level, budget
  ↓
ReasoningPolicy         → fast | standard | deep | multi_pass
  ↓
CapabilityRouter        → model_profile (primary + fallback)
  ↓
Planner                 → typed Plan + TypedAction list
  ↓
CandidateGenerator      → 1–3 candidates (conditional)
  ↓
Critic                  → risks, gaps, counterarguments (conditional)
  ↓
PolicyApprovalGate      → allow | deny | escalate
  ↓
Executor                → tool call / code patch / browser action
  ↓
FreshObserver           → actual result (diff / DOM / API response / stdout)
  ↓
Verifier                → pass | fail | low_confidence
  ↓
ConfidenceGate
  ├─ PASS              → DONE
  ├─ RETRY             → Executor (bounded)
  ├─ REPLAN            → Planner (bounded)
  ├─ ESCALATE          → CapabilityRouter (stronger profile)
  └─ ABORT             → FAILED_WITH_EVIDENCE
```

---

## Reasoning Levels

### fast
For: classification, extraction, short JSON, low-risk single tool calls.
- 1 LLM call
- No multi-candidate, no debate
- Schema validation required
- Escalate only on schema failure or confidence below threshold
- Max latency budget: 8 000 ms

### standard
For: typical coding tasks, tool-use chains, medium-complexity planning.
- Planner → Executor → Verifier
- Up to 2 targeted repair attempts
- 1 candidate by default
- Critic activated on medium risk or failed verification
- Max LLM calls: 4, max latency: 30 000 ms

### deep
For: architecture decisions, multi-file changes, complex debugging, long tool chains.
- Planner required
- Up to 3 candidates on critical steps
- Independent Critic pass required
- Fresh observation after every external action
- Max 2 replans, max 3 repair loops
- Max LLM calls: 10, max latency: 120 000 ms

### multi_pass
For: critical/security-sensitive tasks, or after 2 failed repairs.
- Two independent model profiles produce outputs
- Judge/Verifier selects using typed evidence criteria (not majority vote)
- Approval required before any destructive action
- Full trace + rollback plan required
- Max LLM calls: 16

---

## Adaptive Reasoning Priority Order

1. Adaptive reasoning depth — reduce latency/cost on simple tasks
2. Model capability routing — right model for task class
3. Planner / Critic / Verifier split — no single model does everything
4. Confidence escalation — evidence-based, not self-assessment
5. Real-world model scorecards — data-driven routing
6. Multi-candidate + judge — only when needed
7. Speculative local-first — cheap draft, strong verify

---

See `CONTRACTS.md` for typed schemas, `ROUTING.md` for capability matrix,
`REPAIR.md` for self-repair policy, `SCORECARD.md` for model benchmarking,
and `ROLLOUT.md` for phased delivery.
