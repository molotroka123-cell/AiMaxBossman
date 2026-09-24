# Pillar 2 — Specialist Model Foundry

Goal: convert verified Bossman experience into smaller specialist models instead
of repeatedly paying large general models.

Pipeline:
`verified traces -> dataset compiler -> contamination/dedup checks -> frozen
train/holdout manifests -> LoRA/distillation -> benchmark -> independent
verifier -> shadow -> promote/reject`.

Initial specialists:
- Bossman-Coder;
- Bossman-Vision-Chart;
- Bossman-Market;
- Bossman-Workflow-Router;
- Bossman-Verifier.

## Hard rules
- failed/unverified trajectories never become positive training labels;
- teacher output is not truth without outcome/evidence;
- holdout is physically excluded from training manifest;
- model promotion requires improvement on frozen owner benchmarks;
- training loss alone is meaningless;
- previous production model remains rollback target;
- dataset/model lineage is immutable and hashed.

## Distillation
Use expensive/cloud models only on difficult examples where they add measurable
quality. Distil verified capability into local specialists. Re-run old benchmark
to detect catastrophic forgetting.

## RunPod
Model Foundry may request rented compute through Provider Fleet, but cannot
increase budget or keep pods alive itself.
