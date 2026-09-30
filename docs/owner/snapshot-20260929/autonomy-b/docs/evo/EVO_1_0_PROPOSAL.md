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

## Frontier Council and teacher-guided learning

Owner direction recorded 2026-09-21: authorized frontier models should independently audit Bossman, propose evidence-backed corrections, coach local students and prepare training curricula. The implementation and acceptance specification is [Frontier Council and Training](FRONTIER_COUNCIL_AND_TRAINING.md). It extends this proposal and [owner model behavior](OWNER_MODEL_BEHAVIOR.md); it does not create a second runtime, gateway, memory or promotion authority.

The intended loop is:

`verified owner episode -> sanitized audit pack -> independent frontier reviews -> reproduced finding -> teacher correction -> local student retry -> verified lesson -> unseen holdout -> owner-approved promotion -> monitoring/rollback`

Three distinct outcomes must be reported: memory/skill learning (weights unchanged), a curated curriculum/dataset, and optional later weight adaptation. LoRA/QLoRA requires separate authorization, compatible training infrastructure, permitted data use and evidence against forgetting; saving a lesson is not fine-tuning. Teacher patches and teacher-assisted completions do not count as unassisted student success.

Audits may be triggered by repeated failures, release candidates or an owner-approved cadence, with deduplication, budgets and rate limits. No schedule or paid call is enabled by this document. Frontier suggestions are untrusted proposals: executable verification and an independent holdout take precedence over model confidence or majority agreement.

Finish and accept stable 1.0 before automating the council. Already-authorized repair/coaching preparation can continue; the future council must not expand the current release scope or silently modify stable. All new council UI, orchestration and training requirements remain planned until implementation and exact-evidence acceptance are separately published.
