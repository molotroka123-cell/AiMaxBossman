# Fable — recommendations beyond the supplied design

These are the mechanisms I would build first if this were my own Agent OS under finite compute and
API limits. Each is traceable to an evidence point from this session; none widens authority.

## A. Limit / token economy

1. **Discovery is mechanical; pay for it once per commit.** `repo map`, test discovery, diff stat
   and symbol slices are produced by tools and cached per HEAD (F5.10, F2.8). The strong model
   receives results, never performs discovery. Evidence: the dominant cost of this session (and the
   four sub-agents that died at the limit) was re-reading code, not reasoning.
2. **Escalation handoff packet** (F7.9): the cloud/strong model starts from a failing test, the
   ledger and the surviving hypotheses. It never re-derives. Combined with fail-closed cloud policy
   this makes cloud spend proportional to *uncertainty*, not to task size.
3. **Verification reserve first** (F7.1/7.4): budget for checking and committing is set aside
   before generation. Checkpoint-or-stop (F7.3) makes partial work durable — the failure mode of
   "hours of unverified local work" becomes structurally impossible.
4. **Prompt-cache-aware layout** (F5.9): stable prefix (policies, invariants, tool schemas), volatile
   slices last. Zero intelligence cost, measurable latency/cost gain.
5. **Retrieval budget by uncertainty level** (F1.3): L0 tasks get no cases; L4 get up to 8, evidence
   first (F1.4).

## B. Hard reasoning

6. **Gate-as-code** (F2.1): protocols are state machines over the evidence ledger (Deep Fix Mode is
   the first instance). A model can ignore a prompted step; it cannot skip a predicate.
7. **Discriminating-observation requirement** (F3.1) with **forward reproduction** (F3.7): a root
   cause is accepted only when the bug can be reproduced *from* the cause. This is the single
   strongest anti-plausibility device I know for code debugging.
8. **Wrong-layer detector** (F3.3): force one hypothesis one layer up/down. Three of this session's
   root causes (F-005 second executor path, F-009 sandbox-as-authorization, F-015 approval-as-flag)
   were architectural, not local.
9. **Two-phase plan commit** (F2.5) + **effect diff** (F4.7): declare expected effects, execute, diff
   observed vs declared; unexpected writes fail verification even when tests pass.

## C. Self-correction / false-success reduction

10. **Verifier isolation** (F4.2): the verifier never sees the coder transcript; it gets the plan
    hash, read tools and the environment. Stronger than filtering echoes (F-012 fix) because the
    echo channel does not exist.
11. **Counterfactual-pair retrieval** (F1.2) and **negative skills** (F9.3): show the tempting wrong
    fix with the evidence that killed it. Local models copy the first plausible pattern; the
    corpus already stores `rejected_hypotheses` and `Avoid:` lines.
12. **Sibling sweep** (F8.4): a boundary fix in one component triggers the same counterexamples
    against every component sharing the boundary tag. Evidence: SSRF appeared in `net.py`, the
    browser policy and discovery; approval-by-flag in terminal and browser routes.
13. **Route audit invariant** (F6.7): nightly self-check that no `model_calls.is_cloud` row exists
    for a deny-policy task — continuous verification of the fail-closed fixes.
14. **Promotion honesty** (F10.3): below n=20 per class the lab reports INSUFFICIENT_EVIDENCE.
    Improvement claims are learning records with a status (F10.1); rollback is a corpus operation
    (F10.7).

## What I would reject or delay from the owner set (with reasons)

- Automatic hypothesis merging by LLM (3.6): token cost without measurable gain at k ≤ 5.
- Prose-calibrated Bayesian posteriors (3.2, 3.8), per-model context/skill profiles (5.9, 9.9),
  cross-model usefulness scoring (1.8, 1.10): all need the outcome table (P3); implementing them
  before data exists produces confident nonsense. First collect rows for free (F1.6, F2.9, F6.3).
- Context attribution telemetry (5.10) and protocol mutation on hidden evals (2.9): expensive and
  Goodhart-prone; only inside the lab with ablations (F10.6).
- Concurrency schedule perturbation (8.5): nondeterministic; the observed reliability bug
  (BUG-004) was loop affinity, caught by a deterministic probe.

## Teacher → Student

Transfer only: failure pattern, decisive evidence, root cause, failed strategies, successful
strategy, verification recipe, invariant. Help levels L0–L5 (task only → verified reference case)
are recorded per (model, task class, failure pattern) in P3 to build the competence-gap map; the
map drives retrieval depth (F1.3), routing (F6.1) and, later, optional fine-tuning on the VERIFIED
corpus only (`learning.LearningStore.export_sanitized`). Benchmark leakage is prevented by
git-tracked holdout hashes absent from candidate workspaces (F10.2).
