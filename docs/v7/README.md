# AiMaxBossman V7 — Multi-Model Audit Convergence

Canonical working branch: `v7/audit-convergence-20260907`

Base V6 SHA: `5f75dc55ff0376ef7774526cbed88b50efd638ff`

## Goal

V7 starts as an evidence-driven architecture/audit convergence phase, not a feature race. Multiple frontier models work on the SAME branch, but each model owns a namespaced audit area under its exact model identity. Their independent findings are later merged into one evidence-ranked mega audit and one implementation roadmap.

## Required model identity

Every participating model must record:
- provider/vendor if known;
- exact model name/version as exposed to the model;
- session/tooling identity if available;
- starting SHA and final SHA;
- timestamp/date;
- whether it inspected other models' audits before completing its RAW audit.

Never invent a model identity. If exact version is unavailable, say `UNKNOWN_VERSION`.

## Directory contract

Each model writes only under:

`docs/v7/audits/<sanitized-model-id>/`

Minimum files:
- `IDENTITY.md`
- `RAW_AUDIT.md`
- `VISION.md`
- `FINDINGS.json`
- `TEST_EVIDENCE.md`
- `RESPONSE_TO_OTHER_AUDITS.md` (only after RAW audit is frozen)

Shared synthesis artifacts are written only during the convergence phase:
- `docs/v7/MEGA_AUDIT.md`
- `docs/v7/CONFLICT_MATRIX.md`
- `docs/v7/V7_ROADMAP.md`
- `docs/v7/OPEN_FINDINGS.json`

## Same-branch concurrency rules

1. `git fetch origin` and checkout `v7/audit-convergence-20260907`.
2. Pull/rebase before every push.
3. Never force-push.
4. Never rewrite, delete, rename, or silently edit another model's audit files.
5. Use model-namespaced paths to avoid conflicts.
6. Small implementation fixes are allowed only when they are well-scoped, directly supported by a reproducible finding, have tests, and do not change V4/V5 freeze/canary/budget/evidence semantics without a new P0 proof.
7. If branch HEAD changed while you worked, rebase and rerun all tests materially affected by the new commits.
8. Treat disagreement as data. Do not resolve by majority vote.

## Audit convergence sequence

### Phase A — independent audits
Each model inspects code, tests, CI, runtime evidence, packaging, UI/UX contracts, local-model execution, browser/computer use, media/video, safety, resource admission, memory/context, agent orchestration, observability, and release truth. It must finish and commit its `RAW_AUDIT.md` before reading peer audit conclusions when practical.

### Phase B — adversarial cross-review
Each model may then read peers and write `RESPONSE_TO_OTHER_AUDITS.md` with confirmations, rebuttals, missing evidence, and newly discovered interactions.

### Phase C — mega audit
A synthesizer merges underlying defect signatures rather than prose. Every finding is classified as one of:
- `PROVEN`
- `CORROBORATED`
- `DISPUTED`
- `SPECULATIVE`
- `OWNER_HARDWARE_REQUIRED`
- `CLOSED_BY_EVIDENCE`

Each finding gets severity P0/P1/P2/P3, affected surfaces, exact evidence, reproducer/test, remediation, regression risk, and owner.

## Truth constraints

- Repo-level green CI is not owner-Windows acceptance.
- Repo-local rollback/canary proof is not owner-hardware rollback acceptance.
- A model response is not evidence of an external effect.
- Do not fabricate local GPU/model/browser/media evidence.
- Preserve fail-closed intelligence-retention semantics when same-model evidence is absent.
- Do not resurrect already-closed historical findings without a new reproducer on current HEAD.

## Prompt files

- `FABLE_MINI_PROMPT.md` — focused Claude Fable continuation.
- `INDEPENDENT_MODEL_MASTER_PROMPT.md` — independent second-model lane.
- `MEGA_AUDIT_SYNTHESIS_PROMPT.md` — final convergence/synthesis lane.
- `PROMPT_PACK.md` — copy/paste bundle of all prompts.
