# Bossman Intelligence — decision mathematics

## Objectives tracked (never collapsed into one scalar for promotion)

| Symbol | Meaning | Source of truth |
|---|---|---|
| Q | VerifiedSuccess: fraction of tasks whose completion was VERIFIED by fresh observation (P1), not self-report | evaluations / learning records with status VERIFIED |
| I | IntelligenceRetention: Q on the hard subset (L3–L4) relative to the strongest available route | holdout runs |
| S | Security: must-deny tests passing; approval bypass = 0; cloud calls under deny = 0 | SECREM suites, route audit invariant |
| C | Cost: tokens, API $, per verified task | flight recorder |
| L | Latency per verified task | flight recorder |
| M | VRAM/RAM peak | resource sampler |
| R | Regression risk: failing existing tests / files touched outside scope | Deep Fix scope gate, regressions |
| F | False-success / tail risk: completions later found wrong | re-verification, FAILED_EXPERIMENT records |

## Promotion rule (constraints first, then Pareto)

A candidate mechanism/skill/protocol/route is promotable only if all hold on the hidden holdout:

```
S_new  == S_baseline            (every must-deny test still denies; veto, not a term)
Q_new  >= Q_baseline - 0.01     (1 pp tolerance)
I_new  >= 0.99 * I_baseline     (no silent loss on hard tasks)
F_new  <= F_baseline
n      >= 20 tasks per class    (else: INSUFFICIENT_EVIDENCE, never "improved")
```

Among survivors, choose on the Pareto frontier of (C, L, M, R) — do not sum them. Owner promotes.

## Ranking aid for *candidate ideas* (not for promotion)

The owner proposed `EVI = ΔVerifiedSuccess × Confidence × Reuse × Coverage` against a summed cost.
Kept as an **ordinal aid** with two corrections:

1. Risk is a penalty applied after the ratio, with security weighted double, because a security
   regression is not tradeable against benefit:
   `PriorityAid = EVI / (Cost + 0.5) − (2·SecurityRisk + GoodhartRisk)`.
2. All factors are ordinal (0..3 / L,M,H) and labelled ESTIMATE. When a measurement exists it
   replaces the estimate and the record says so. Precision is not invented.

## Escalation and stopping (P4)

Let `g` be the expected gain from one more attempt at the current rung and `c` its cost.
Attempt only if `g > c` **and** a new observation was obtained since the last attempt (evidence
gap rule, F7.2). Verification budget `v = max(0.2 · budget, cost(plan))` is reserved before any
generation; generation stops when the remaining budget would breach `v` (F7.1). Rungs are fixed
(local-fast → local-strong → cloud) and capped per task class; cloud additionally requires the
fail-closed policy signal.

Why not SPRT/CVaR now: both need base rates the outcome table (P3) does not yet contain. The rules
above are the degenerate, evidence-free-safe versions; SPRT is a PROTOTYPE once P3 has ≥20 rows
per class.

## Hypothesis tournament (P1)

Hypotheses `H1..Hk` (k ≤ 5) each declare a discriminating observation `o_i` such that the
predicted outcome differs across at least two hypotheses. Observations are executed cheapest first
(`cost(o)` ascending, ties by number of hypotheses split). An observation consistent with all
surviving hypotheses carries zero weight (symmetric-evidence rule). Stop when one hypothesis
survives all executed observations **and** forward reproduction from the cause succeeds; otherwise
after 3 rounds → OWNER_DECISION_REQUIRED with the ledger. Ordinal confidence (likely/possible/
unlikely) is recorded for later Brier scoring; no numeric posteriors until P3 exists.

## Verification sufficiency by risk class (P1)

| risk_class | required evidence |
|---|---|
| normal | 1 fresh observation of the declared effect |
| sensitive | 1 fresh observation + 1 negative control (mirrored must-deny) |
| irreversible | 2 independent observation kinds (e.g. file hash + DB row) + negative control + owner approval |

A verifier that shares the model family with the coder may only produce UNVERIFIED for
judgment-only effects (F4.8). Timeout ⇒ UNVERIFIED, never PASS.

## Token economy target

Primary metric: **verified progress per token** = Q-weighted tasks / tokens, logged per task
(F7.10). Levers, in expected order of effect (ESTIMATE): mechanical scaffolding without a model
(F2.8), failing-test-first slices (F5.1), cache-stable prefix layout (F5.9), escalation handoff
packet (F7.9), retrieval budget by uncertainty (F1.3). Gate: `Q_new ≥ Q_baseline − 1pp` and
`I ≥ 0.99` — a cheaper path that loses hard-task quality is not promoted.
