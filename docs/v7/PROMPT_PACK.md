# AiMaxBossman V7 — Prompt Pack

Canonical branch: `v7/audit-convergence-20260907`

This file defines the launch order. Full prompts live beside it.

## 1. Fable lane

Use:
`docs/v7/FABLE_MINI_PROMPT.md`

Purpose:
- continue from current branch truth;
- produce a namespaced Fable audit/vision;
- fix only proven, bounded regressions;
- push to the same branch.

## 2. Independent frontier-model lane

Use:
`docs/v7/INDEPENDENT_MODEL_MASTER_PROMPT.md`

Purpose:
- produce an independent RAW audit before reading Fable conclusions when practical;
- publish under the model's real identity;
- explicitly challenge Fable and V7 architecture hypotheses;
- push to the same branch.

## 3. Mega-audit convergence

Run only after at least two model audit directories exist and their RAW audits are committed.

Use:
`docs/v7/MEGA_AUDIT_SYNTHESIS_PROMPT.md`

Purpose:
- deduplicate by defect signature;
- resolve disagreements by current-HEAD evidence rather than vote;
- produce `MEGA_AUDIT.md`, `CONFLICT_MATRIX.md`, `V7_ROADMAP.md`, `OPEN_FINDINGS.json`.

## Shared prerequisites

All models must first read:
- `docs/v7/README.md`
- `docs/v7/CURRENT_AUDIT_2026-09-07.md`
- `docs/v7/V7_ARCHITECTURE_CHARTER.md`

All models work in:
`v7/audit-convergence-20260907`

Rules:
- pull/rebase before push;
- no force push;
- model-owned namespaced audit paths;
- no editing other models' RAW audit files;
- no fake Windows/GPU/local-model/provider/media evidence;
- no mass architecture implementation before convergence;
- current-HEAD evidence beats historical prose.

## Final execution sequence

Fable RAW audit + vision
→ independent model RAW audit + vision
→ both cross-review
→ mega-audit synthesis
→ evidence-ranked roadmap
→ implementation from proven/corroborated findings only.
