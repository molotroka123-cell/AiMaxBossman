# EVO 1.0 Proposal — Bossman Improves Bossman

EVO 1.0 is documentation only for the current release. No autonomous self-modification is enabled.

## Goal

Future Bossman may observe its own performance, identify a weakness, research alternatives, create an engineering mission, assign AI engineers, build an isolated candidate, test it, obtain independent verification and red-team review, compare against stable, and then propose an upgrade.

## Mandatory promotion chain

`stable -> isolated candidate -> evaluation -> comparison -> owner approval -> promotion -> monitoring -> rollback if regression`

Stable production is never silently rewritten.

## Required controls

- immutable reference to current stable SHA and artifact hash;
- isolated worktree/container for every candidate;
- explicit capability and budget limits for engineering agents;
- reproducible evaluation set plus unseen holdout tasks;
- independent verifier that did not author the patch;
- red-team/security pass for changed trust boundaries;
- comparison against stable on correctness, completion, interventions, tool success, context retention, restart survival, latency and cloud cost;
- owner-visible diff, evidence and rollback point;
- explicit OWNER APPROVAL before promotion;
- canary rollout where feasible;
- automatic rollback on defined regression triggers;
- append-only promotion/audit ledger.

## Forbidden

- direct edits to the stable production checkout;
- self-approval;
- deleting or weakening tests to promote a candidate;
- hidden model/provider substitution;
- using demo or mock output as production evidence;
- promoting when the candidate cannot be rolled back.

## First EVO milestone

Recommendation-only mode: Bossman may produce a ranked engineering proposal and isolated patch candidate, but promotion remains manual and owner-approved. Autonomous promotion is out of scope until the owner separately authorizes it.

## Cloud/model routing recommendations

EVO 1.0 may recommend changing local/cloud model routing based on measured completion, latency, cost, privacy class and verifier corrections. It may not silently enable providers, transmit LOCAL_ONLY data, enable paid fallback, increase budgets, or turn on provider auto-recharge. Any provider/model promotion follows the same isolated benchmark → evidence → owner approval → rollout → rollback chain.
