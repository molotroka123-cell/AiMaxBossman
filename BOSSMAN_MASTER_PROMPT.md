# BOSSMAN Master Prompt — Hard Reasoning V2

This document is the high-level master prompt / design contract for Bossman hard reasoning. It is intended to be used by orchestration code, planners, routing logic, and verifier layers. It does **not** override safety policy, approval gates, perimeter rules, or deterministic checks.

---

## 0. Continuity

Before applying the new reasoning logic, include prior shipped upgrades in the operating assumptions:

- security hardening PR with all 10 fixes was already pushed earlier in this repo;
- context engine / decision memory / failure memory already exist and must be reused rather than replaced;
- hard reasoning must be additive and must not break the main code path;
- no direct action is allowed to bypass policy, scope, approval, or sandbox constraints.

The controller must treat previous pushes as baseline platform capabilities, not as optional ideas.

---

## 1. Core objective

Bossman should no longer merely "think longer". It should mathematically decide:

1. what to verify next,
2. how much more evidence is needed,
3. which model or tool to use,
4. which branches deserve more budget,
5. when uncertainty is low enough to stop,
6. when risk requires escalation or approval.

The goal is efficient reasoning under uncertainty, not verbose reasoning.

---

## 2. Operating law

Every task must be framed as:

- hidden world state,
- observations,
- candidate hypotheses,
- possible actions,
- risk class,
- utility / loss / cost,
- stopping criteria.

Bossman must maintain a belief state, update it when new evidence arrives, and choose the next action that provides the highest expected decision value relative to cost and risk.

---

## 3. Hard reasoning stack

Hard Reasoning Controller
│
├── Belief State
├── Bayesian Hypothesis Tracker
├── Entropy Estimator
├── Value-of-Information Planner
├── Reasoning Budget Allocator
├── Branch Generator
├── Dynamic Beam Search
├── Evidence Matrix
├── Causal Analyzer
├── Counterfactual Evaluator
├── Confidence Calibrator
├── Multi-Model Consensus
├── Model Bandit Router
├── Saturation Detector
└── Stop / Verify / Escalate

Every component must degrade gracefully. If one estimator is unavailable, the controller falls back to simpler heuristics rather than blocking the entire agent.

---

## 4. Sequential stopping

Use Sequential Probability Ratio Test when the question is effectively binary:

- H1 = solution is correct
- H0 = solution is incorrect

Update the likelihood ratio after each new evidence item:

Λ_t = ∏ P(E_i | H1) / P(E_i | H0)

Decision rule:

- if Λ_t > A, accept H1;
- if Λ_t < B, reject H1 and escalate or repair;
- otherwise continue gathering evidence.

This is preferred over fixed "always run N checks" logic for:

- coding fixes,
- debugging,
- browser / PC automation,
- architecture validation,
- multi-step execution with expensive tools.

The stopping rule must prevent both premature stopping and endless re-checking.

---

## 5. Value of information

For each possible next action a, estimate:

VOI(a) = E[U | observe(a)] - U(current) - Cost(a)

Bossman should prefer the action with the highest positive VOI subject to policy and risk.

Example pattern:

- ask the LLM again → often medium information, non-trivial cost;
- open traceback → high information, low cost;
- grep target function → medium-high information, low cost;
- run full suite → medium information, high cost.

The planner should generally choose traceback first in this scenario.

---

## 6. Expected utility

Every action must have expected utility:

EU(a) = P(success | a)·Reward - P(failure | a)·Loss - Cost(a)

Loss must increase with risk.

Typical ordering:

- read file → near-zero loss;
- edit one test → low loss;
- modify production code → higher loss;
- delete directory → high loss;
- send payment / external side effect → extreme loss and approval.

Reasoning depth and verification depth must increase with action risk.

---

## 7. Belief state and POMDP framing

Bossman should reason over partial observability.

Do not store only a binary statement like:

- "Windows focus is the issue"

Instead maintain a probability distribution, for example:

- host focus issue = 0.55
- planning failure = 0.25
- policy issue = 0.10
- unknown = 0.10

Every new observation updates the belief state. This belief state is the core state passed between planner, verifier, and router.

---

## 8. Bayesian hypothesis tracking

When multiple explanations compete, update:

P(H_i | E) ∝ P(E | H_i) P(H_i)

Examples:

- if executor succeeds and policy is ALLOW, policy-bug probability should fall;
- if foreground or host-state evidence is wrong, host-focus probability should rise;
- if changing the model does not change failure, model-specific explanation should weaken.

Bossman should not restart investigation from zero after each observation.

---

## 9. Entropy reduction

The planner should prefer actions that reduce uncertainty fastest.

Current uncertainty:

H(S) = - Σ p_i log p_i

Expected entropy after action a:

ExpectedEntropy(a) = Σ P(o | a) H(S | o)

Choose action maximizing entropy reduction:

a* = argmax [ H(S) - ExpectedEntropy(a) ]

This is especially useful for debugging, diagnosis, and branch prioritization.

---

## 10. Causal reasoning

Do not confuse correlation with cause.

Use a causal graph like:

Model Output
↓
Planner
↓
Policy
↓
Executor
↓
Host State
↓
Observation

When failure appears downstream, inspect the causal path rather than blaming the earliest upstream node by default.

Use intervention logic conceptually:

P(Y | do(X))

If changing X does not change Y, confidence in X as root cause should decrease.

---

## 11. Counterfactual reasoning

After failure, explicitly compare the observed path against alternative next actions:

CF(a') = ExpectedOutcome(a') - ObservedOutcome(a)

Use this for self-repair decisions such as:

- retry same action,
- switch tool,
- retrieve more context,
- escalate to stronger model,
- request approval,
- abandon a dead branch.

Counterfactual analysis must feed decision memory and failure memory.

---

## 12. Multi-model consensus

Consensus must not be naive majority voting.

Weighted consensus should use:

- historical reliability for this task class,
- current confidence,
- support strength for the hypothesis,
- optionally diversity bonus.

A coding-specialized model should usually carry more weight than a general model for code-local fixes.

---

## 13. Diversity bonus

Three nearly identical models do not provide much extra value.

Ensemble value should rise with both:

- accuracy,
- diversity.

If two models fail in highly correlated ways, adding a third similar one has little value. A different architecture or tool-based verifier may be more useful.

---

## 14. Regret minimization

Bossman should learn strategy quality over time.

Per strategy a:

Regret_t(a) = Reward(best) - Reward(a)

Minimize cumulative regret over many tasks.

This supports adaptive routing such as:

- small local model for grep/local edit,
- stronger reasoning model for architecture,
- deterministic parser instead of LLM for JSON extraction.

---

## 15. Model routing with bandits

Treat each eligible model as an arm.

Reward may combine:

Reward = Success - λ Cost - μ Latency - ν Retries

Support both:

- UCB for explicit exploration / exploitation balance,
- Thompson Sampling for probabilistic routing under uncertainty.

All routing must remain inside cloud/security policy. A mathematically attractive model is still forbidden if policy disallows it.

---

## 16. Difficulty-normalized evaluation

Raw success rate is misleading.

Model scorecards must be normalized by at least some notion of task difficulty or grouped statistics such as:

- task type,
- difficulty,
- reasoning level,
- risk,
- tool count.

Otherwise the router learns the wrong lesson from easy tasks.

---

## 17. Search budget allocation

For reasoning trees, budget should not be uniform.

If branches have scores s_i and total budget B:

Budget_i = B · s_i^γ / Σ s_j^γ

For γ > 1, stronger branches get more budget.

This is useful for tree-of-thought style branching and keeps deep search economically bounded.

---

## 18. Dynamic beam and temperature

Beam width must depend on uncertainty and complexity.

Example policy:

- low uncertainty → beam = 1;
- high uncertainty and high complexity → beam = 3 or 4.

Reasoning temperature should also adapt:

- deterministic coding fix → low temperature;
- architecture exploration → higher generation temperature;
- final verification → low temperature again.

---

## 19. Final pass rule

Use a unified decision score:

DecisionScore = w1·Evidence + w2·Confidence + w3·Verifier + w4·Consistency - w5·Risk - w6·Contradictions - w7·Uncertainty

A task passes only if:

- DecisionScore ≥ θ(task, risk)
- policy allows the action
- required approvals are present
- verifier does not veto

Threshold must rise with risk.

Illustrative policy:

- text or low-risk plan → around 0.65
- production code change → around 0.85
- dangerous physical or external irreversible action → around 0.95 plus approval

No formula may override approval logic.

---

## 20. Memory integration

The controller must write reusable outcomes into the existing memory stack:

- decision_memory for validated choices,
- failure_memory for failed strategies,
- context_engine memory for evidence summaries and provenance.

Do not create a second parallel memory universe. Reuse what is already in the repo.

The controller should persist:

- winning hypothesis,
- rejected alternatives,
- decisive evidence,
- counterfactual note,
- risk level,
- model/tool route that worked,
- stopping reason.

---

## 21. Implementation rules

- Additive only: no breaking rewrites of current Bossman flow.
- Safety first: policy / perimeter / approval outrank reasoning score.
- Deterministic verifiers outrank free-form model opinion when available.
- Expensive reasoning must justify itself by VOI, entropy reduction, or risk.
- The controller must stop when evidence is sufficient, not when token budget merely remains.
- When uncertainty remains high at high risk, escalate rather than guess.

---

## 22. Minimal implementation path

Implement in phases:

Phase 1:
- SPRT
- VOI scoring
- expected utility
- decision score
- adaptive threshold by risk

Phase 2:
- belief state
- Bayesian tracker
- entropy reduction planner
- dynamic beam / temperature
- memory writeback

Phase 3:
- bandit routing
- Thompson sampling
- difficulty-normalized scorecards
- causal / counterfactual analyzers
- branch budget allocator

The first production goal is not theoretical completeness. It is better stop/continue/escalate decisions under uncertainty without breaking the main code path.
