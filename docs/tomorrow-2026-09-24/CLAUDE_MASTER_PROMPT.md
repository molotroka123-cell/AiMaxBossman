# CLAUDE MASTER — BOSSMAN 24.09: MODEL FLEET + WEBDESIGNER + H200

Repository: `molotroka123-cell/AiMaxBossman`.

Owner wants execution, not another roadmap.

## Branch rule

The documentation package is in `main`.

**Do not use old main runtime as the implementation base.**

First:
```
git status --short
git worktree list
git fetch --all --prune
git rev-parse origin/main
git rev-parse origin/release/bossman-owner
```

Read:
- `BOSSMAN_TOMORROW_2026-09-24_START_HERE.md` from main;
- all `docs/tomorrow-2026-09-24/*.md`.

Implement against the fresh current product line, expected `release/bossman-owner`, preserving all newer fixes.

No force push. No second Bossman. No fake evidence.

## Parallel swarm

Use parallel agents where resources do not conflict.

### AGENT 1 — MODEL FLEET
Build truthful Models/Fleet UX + CLI.
Download/connect/verify Qwen3.8-27B, Xing4.0-29B-A4B, Qwen3.6-35B-A3B first, then approved specialists.
Real GREEN only after load + real task + restart.
Record exact model/revision/quant/backend/RAM/speed.

### AGENT 2 — WEBDESIGNER RUNTIME
Implement:
brief -> design context -> code -> build -> Chromium desktop/mobile render -> visual critique -> patch -> rerender -> Playwright -> final evidence.

Use existing Bossman tasks, browser, memory, approvals and cost governor.

### AGENT 3 — OSS / DESIGN SYSTEM
Audit exact remote SHAs/licenses for Onlook, Open Design, Open CoDesign, Layout and Build Beautiful Sites.
Reuse only legally/technically appropriate pieces.
AGPL/source-available boundaries stay explicit.

### AGENT 4 — DATASET FACTORY
Implement manifest/schema/render/provenance/dedup.
Generate small verified smoke dataset and freeze micro-holdout.
Never leak holdout into memory/skills/train.

### AGENT 5 — TRAINING HARNESS
Prepare pinned 8xH200 training environment:
Transformers + PEFT + TRL + multi-GPU.
Run local/small smoke before rental.
Create LoRA SFT configs and artifact/evidence layout.

### AGENT 6 — EVALUATOR / RED TEAM
Own holdout and negative controls.
Do not write the candidate fixes it certifies.
Compare base vs candidate blindly from actual renders and functional tests.

Coordinator is the only writer to shared integration state. GPU/model/desktop leases are explicit.

## Priorities

1. Do not break current Bossman.
2. Fleet UX truthful.
3. WebDesigner runtime loop works BEFORE training.
4. Frozen evaluator/holdout.
5. Dataset factory.
6. H200 smoke.
7. Training.
8. Candidate local integration.
9. Only then optimization/polish.

## H200 policy

Do not pretrain from scratch.

First paid experiment:
- 8xH200;
- BF16 LoRA for 30B-class candidate;
- 5k–20k curated examples;
- short bounded run;
- evaluate immediately.

Only extend if blind benchmark improves.

Record provider, hourly price, start/end, GPU topology, code SHA, base revision, dataset hashes, config, throughput, checkpoints and total cost.

Auto-shutdown/cost ceiling required.

## "Claude-level" policy

Never infer parity from loss or selected screenshots.

Use the frozen blind holdout in `EVALUATION_AND_HOLDOUT.md`.

Keep:
- functional correctness;
- visual preference;
- efficiency;
- tool correctness
as separate measurements.

Only use the term `CLAUDE_PARITY_CANDIDATE` if the pre-registered blind test supports it.

## Required negative controls

- broken CSS/build;
- mobile CTA offscreen;
- inert form;
- console exception;
- stale screenshot;
- visual critic false-positive;
- wrong model identity;
- corrupt adapter;
- restart mid-iteration;
- holdout leakage detector.

## Do not

- download everything before testing the fleet;
- silently cloud-fallback LOCAL_ONLY data;
- disable Windows/security controls for convenience;
- vendor AGPL/custom-license code into Bossman core without explicit decision;
- train on unknown-license sources;
- let a model certify its own patch;
- change evaluation after seeing results;
- call a mock/render fixture a real model result.

## End condition

Push real work and create `docs/tomorrow-2026-09-24/IMPLEMENTATION_RESULT.md`.

Report:

```
START_PRODUCT_SHA=
FINAL_WORK_SHA=
MODEL_FLEET=
QWEN38=
XING4=
QWEN36=
WEBDESIGNER_LOOP=
VISUAL_NEGATIVE_CONTROL=
FUNCTIONAL_NEGATIVE_CONTROL=
DATASET_V1=
HOLDOUT_FROZEN=
H200_ENV=
H200_TRAINING=
CANDIDATE=
BLIND_GAIN=
CLAUDE_PARITY=
TOTAL_GPU_COST=
OPEN_P0=
OPEN_P1=
BLOCKERS=
NEXT_ACTION=
```

If H200 is not rented yet, finish everything that can be proven without it and leave a one-command/reproducible launch package. Do not mark H200 training PASS.
