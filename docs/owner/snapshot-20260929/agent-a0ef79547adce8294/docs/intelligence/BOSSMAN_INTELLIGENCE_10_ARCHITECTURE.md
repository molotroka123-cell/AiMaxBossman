# Bossman Intelligence — 10 mechanisms, one substrate

Status: architecture decision record, 2026-09-02. Evidence base: this session's 16 VERIFIED
learning cases, the SECREM test suites, and the existing V2.6 modules. Everything marked
ESTIMATE is expert judgment, not measurement.

## The core claim

The owner's ten mechanisms (Experience Retrieval, Reasoning Compiler, Hypothesis Tournament,
Verifier-First, Context Compiler, Model Router, Adaptive Compute, Counterexample Generator,
Skill Factory, Self-Improvement Lab) are not ten subsystems. After deduplicating the 200
candidate ideas (`docs/learning/intelligence_ideas/INDEX.md`) they reduce to **seven primitives**,
five of which already exist in some form in the repository:

| Primitive | What it is | Exists today |
|---|---|---|
| P1 Evidence ledger | typed observations with provenance, freshness, hash; text can veto, never approve | `command-center/bcc/v2/verification.py` (F-012), `bossman/flight_recorder.py`, `bossman/deep_fix.py` gates |
| P2 Learning corpus | VERIFIED/failed split, retrieval by tags/predicate/finding, supersession | `learning/trace.py`, `data/learning/*.jsonl` |
| P3 Outcome table | per-(model, task class, failure pattern) → verified success, cost, help level | partial: router class stats (n≥5), `failure_patterns.py`; **not populated** |
| P4 Budgeted decision loop | expected gain vs cost, early stop, verification reserve, escalation ladder | partial: `uncertainty.py`, adaptive compute (V2.6 B/C), Governor |
| P5 Promotion pipeline | hidden holdout, independent verifier, shadow, owner promotion, rollback | `learning_guard/`, holdout exclusion in runner |
| P6 Mutator library | deterministic counterexample generators over boundary predicates | piecemeal in `tests/test_secrem_*`, `.agents/redteam/*` |
| P7 Context slots | budgeted, hashed, provenance-tagged slices with a cache-stable prefix | `bossman/context.py` block shares; `bcc/v2/code_index.py` |

Each mechanism is a *policy* over these primitives:

- **Experience Retrieval** = P2 queries keyed by (boundary, violated predicate, failure pattern),
  budgeted by P4, rendered into a P7 slot with provenance.
- **Reasoning Compiler** = protocol fragments whose gates are P1 predicates (gate-as-code), sized
  by the difficulty estimator (P4), scaffolding steps run without a model.
- **Hypothesis Tournament** = P1 ledger + discriminating observations + elimination; bounded by P4.
- **Verifier-First** = P1 with the plan hashed before the patch, verifier isolated from the coder
  transcript, negative controls from P6.
- **Context Compiler** = P7: failing-test-first slice, manifest hashes, untrusted slot framed.
- **Model Router** = P3 posteriors + resource residency + capability probes (P1), fail-closed.
- **Adaptive Compute** = P4 with verification reserved first and escalation on evidence gap.
- **Counterexample Generator** = P6 + sibling sweep across components sharing a boundary tag.
- **Skill Factory** = P2 clusters → contracts (verification recipe + negative example + scope) → P5.
- **Self-Improvement Lab** = P5 over P2 records; security veto; honest power notes.

## What is deliberately *not* built

- A second verifier, second approval store, second scheduler or second memory: every mechanism
  reads/writes the existing engines (Verification, Approval, Flight Recorder, Learning Guard,
  Resource Brain, EventBus).
- LLM-driven "hypothesis merging", "protocol mutation" and Bayesian priors from prose: rejected
  or deferred until P3 contains real rows (fake precision is worse than none).
- Per-model skill variants, Pareto archives, contextual bandits: deferred until there are ≥2
  candidates and ≥20 tasks per class to compare.

## Security posture of the intelligence layer

- Retrieved cases, skills, and protocol outputs are **data** (F-006 marker), never authority.
- Nothing is promoted because the same agent says it improved (P5); security must-deny tests are a
  veto, not a score term.
- Cloud escalation inherits the fail-closed policy (F-008/F-016); the escalation packet is a
  failing test + ledger, not a transcript.
- Learning records never contain hidden reasoning or secrets (schema-enforced).

## Where this session's fixes fit

| Fix | Primitive exercised |
|---|---|
| F-012 fresh-evidence gate | P1 |
| F-013 approval identity digest | P1 (plan hash bound to implementation) |
| Learning layer + 16 cases | P2 |
| Deep Fix Mode gates | P1 + P4 (states, reserve) |
| SECREM variants (paths, URLs, approvals, MCP metadata) | P6 |
| Terminal/approval/browser session ownership | P1 provenance (who may observe/act) |
