# Intelligence benchmark plan

Purpose: decide, not decorate. Every benchmark below changes a concrete decision; anything that
cannot change a decision is not run.

## Datasets

| Set | Source | Size now | Hidden |
|---|---|---|---|
| SECREM must-deny | `tests/test_secrem_*` (core + cc) | ~120 tests | no (regression) |
| Fix cases | `data/learning/fix_cases.jsonl` (VERIFIED) | 16 | no |
| Holdout | git-tracked `(test id, expected verdict, sha256)`; files absent from candidate workspace | to create at ≥20 cases | yes |
| Adversarial holdout | variants authored by an agent ≠ the fixer (F8.1/F8.4 mutators) | grows with fixes | yes |

## Experiments (each = learning record with status)

1. **Direct vs Deep Fix (same local model)** — Q, I, C, L on holdout fix tasks. Decision:
   enable `BOSSMAN_DEEP_FIX_ENABLED` for that model/class. Threshold: Q +5 pp or F −50 % with
   `Q ≥ baseline − 1 pp`; n ≥ 20 else INSUFFICIENT_EVIDENCE.
2. **Retrieval on/off** — inject 0 vs up to 8 compact VERIFIED cases (F1.3 budget). Decision:
   retrieval depth per uncertainty level. Measure tokens per verified task.
3. **Gate ablation in the lab** (F10.6) — disable one Deep Fix gate at a time. Decision: which gates
   are ceremonial. Production always full.
4. **Help-level ladder** (L0–L5) per model × failure pattern — builds the competence-gap map (P3).
   Decision: routing and escalation rungs.
5. **Route audit** (F6.7) — invariant, run nightly: zero cloud rows under deny. Decision: none if
   green; a red result is a security incident.
6. **Token economy** — verified progress per token before/after scaffolding cache (F2.8/F5.10) and
   prefix layout (F5.9). Decision: keep or revert.

## Environment honesty

GPU/RunPod-only runs (large local models, VRAM peaks) are reported as NOT_TESTED_ON_THIS_HOST
from this container; the harness (`tools/local_hardware_ab.py`, `tools/runpod_preflight.py`) is
ready. Docker-dependent proofs (F-009 container mount) likewise.

## Reporting

Each experiment produces one learning record (`learning_status` UNVERIFIED until an independent
re-run) and one row in `data/learning/experiments.jsonl` (same validator). Results with n < 20 per
class are reported as INSUFFICIENT_EVIDENCE — never as "improved".
