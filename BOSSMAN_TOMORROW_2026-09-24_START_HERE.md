# BOSSMAN — 24.09.2026 START HERE

Status: **OWNER HANDOFF / IMPLEMENTATION SPEC / NOT A READINESS CERTIFICATE**.

This file is the single entrypoint for tomorrow's work.

## Branch truth

- This documentation is intentionally stored in `main` by owner request.
- The `main` runtime is an older historical line and **must not be merged or reset over the current product**.
- The current product implementation line is `release/bossman-owner` unless fresh remote evidence says otherwise.
- Tomorrow: `git fetch --all --prune`, read this package from `main`, then implement on the fresh product line.
- No force-push. No "final-final" branch. No replacement of working Bossman subsystems with duplicates.

## Tomorrow's three tracks

1. **MODEL FLEET GREEN**
   - Download, register and expose the approved local models in Bossman UX/CLI.
   - Every supported model ends as real GREEN or with a reproduced hardware/runtime blocker.
   - See [MODEL_FLEET_GREEN.md](docs/tomorrow-2026-09-24/MODEL_FLEET_GREEN.md).

2. **BOSSMAN WEBDESIGNER**
   - Build a render-aware website agent: brief -> design system -> code -> Chromium render -> visual critique -> patch -> functional QA -> verified artifact.
   - Use community systems as reviewed donors/adapters, not blind vendoring.
   - See [WEBDESIGNER_PRODUCT_TZ.md](docs/tomorrow-2026-09-24/WEBDESIGNER_PRODUCT_TZ.md).

3. **8xH200 TRAINING**
   - Do not pretrain a foundation model from scratch.
   - First establish baseline + golden dataset + holdout.
   - Then LoRA/SFT -> preference stage only if it wins blind evaluation.
   - See [H200_TRAINING_PLAN.md](docs/tomorrow-2026-09-24/H200_TRAINING_PLAN.md).

Game Studio remains the next reuse target for the same generate -> render/play -> critique -> repair loop. The WebDesigner pipeline is deliberately built so its evaluator/training machinery can later be reused by Game Studio.

## Definition of tomorrow's useful progress

A good day is not "downloaded many weights".

A good day produces:

- a working Models/Fleet UX with truthful states;
- reproducible baseline scores for Qwen3.8 / Xing4 and selected challengers;
- one complete WebDesigner render/critique/repair loop;
- a frozen unseen holdout;
- a license/provenance-clean dataset manifest;
- a reproducible H200 container/config even if GPUs are not rented yet;
- zero fake PASS;
- one pushed implementation SHA plus evidence.

## Package

Read in order:

1. [README.md](docs/tomorrow-2026-09-24/README.md)
2. [CLAUDE_MASTER_PROMPT.md](docs/tomorrow-2026-09-24/CLAUDE_MASTER_PROMPT.md)
3. [MODEL_FLEET_GREEN.md](docs/tomorrow-2026-09-24/MODEL_FLEET_GREEN.md)
4. [WEBDESIGNER_PRODUCT_TZ.md](docs/tomorrow-2026-09-24/WEBDESIGNER_PRODUCT_TZ.md)
5. [OPEN_SOURCE_SOURCE_LEDGER.md](docs/tomorrow-2026-09-24/OPEN_SOURCE_SOURCE_LEDGER.md)
6. [DATASET_FACTORY.md](docs/tomorrow-2026-09-24/DATASET_FACTORY.md)
7. [H200_TRAINING_PLAN.md](docs/tomorrow-2026-09-24/H200_TRAINING_PLAN.md)
8. [EVALUATION_AND_HOLDOUT.md](docs/tomorrow-2026-09-24/EVALUATION_AND_HOLDOUT.md)
9. [BOSSMAN_RUNTIME_INTEGRATION.md](docs/tomorrow-2026-09-24/BOSSMAN_RUNTIME_INTEGRATION.md)
10. [TOMORROW_RUNBOOK.md](docs/tomorrow-2026-09-24/TOMORROW_RUNBOOK.md)
11. [GAME_STUDIO_NEXT.md](docs/tomorrow-2026-09-24/GAME_STUDIO_NEXT.md)
12. [TODAY_HANDOFF_2026-09-23.md](docs/tomorrow-2026-09-24/TODAY_HANDOFF_2026-09-23.md)

Do not call a model "Claude-level" from training loss, a screenshot cherry-pick, or an internet benchmark. That claim is allowed only from the frozen blind evaluation described in this package.
