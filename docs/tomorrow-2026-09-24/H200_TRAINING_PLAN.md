# 8xH200 WebDesigner Training Plan

## Objective

Use rented 8xNVIDIA H200 to specialize an already strong open model for high-quality website planning/coding/repair.

Do **not** train a foundation model from scratch.

NVIDIA H200 reference:
- 141 GB HBM3e per GPU;
- 4.8 TB/s memory bandwidth per GPU;
- Hopper architecture;
- 8-GPU HGX systems are standard configurations.

Reference: https://www.nvidia.com/en-gb/data-center/h200/

## What the GPUs are for

Good use:
- LoRA/PEFT SFT sweeps;
- larger context/batch;
- 30B/70B-scale specialization;
- preference tuning after SFT;
- parallel evaluation/inference.

Bad use:
- discovering dataset parsing bugs;
- scraping sources;
- manually fixing CSS;
- full pretraining;
- running an unvalidated reward signal for days.

## Candidate bases

Initial:
1. Qwen3.8-27B.
2. Xing4.0-29B-A4B.
3. Qwen3.6-35B-A3B as comparison.

Occamy can be evaluated as an agent specialist, but do not assume its architecture/training recipe is ideal for a design SFT.

### Selection before training

Run the frozen baseline.

Choose:
- one primary base;
- optionally one challenger for a small adapter run.

Do not train all candidates at full scale.

## Training strategy

### Stage 0 — no rental
On local hardware:
- dataset parser;
- tokenizer/template validation;
- 50-example overfit test;
- render/evaluator loop;
- baseline benchmark;
- checkpoint save/load;
- adapter merge/export dry run.

Only rent when Stage 0 is green.

### Stage 1 — SFT LoRA smoke
8xH200, BF16 where supported.

Recommended first sweep:
- 5k–20k curated examples;
- context 4k/8k depending code length;
- LoRA rank 32 vs 64;
- all-linear or architecture-appropriate target modules;
- learning rate around 1e-4 vs 2e-4 as a sweep, not doctrine;
- one short epoch / step-capped run.

Goal: prove training + eval pipeline and find obvious regressions.

Because 8xH200 has ample memory, prefer normal BF16 LoRA for 30B-class models before QLoRA. Quantized training is a tool for larger models or cost constraints, not automatically better.

### Stage 2 — production SFT candidate
Dataset:
- broad curated layout/code subset;
- golden production examples upsampled;
- repair examples strongly represented.

Train 1–2 candidates from the best Stage-1 configuration.

Checkpoint frequently enough to evaluate intermediate quality.

### Stage 3 — preference tuning
Only after SFT shows blind gain.

Options:
- DPO/ORPO-style preference training;
- other TRL-supported preference methods after compatibility review.

Preference data must come from blind pairs with functional gates.

Do not use an LLM judge as the sole preference source until its bias is measured against human labels.

### Stage 4 — visual-repair specialization
Train compact repair trajectories:
`render critique + context -> minimal patch`.

This may deliver more product value than another full-code epoch.

### Stage 5 — optional multimodal specialist
Only if product data shows the separate visual critic is the bottleneck.

Possible experiment:
- screenshot -> structured critique;
- screenshot + design context -> issue localization;
- screenshot -> code is a separate track.

Do not force the main coder to become a VLM if a two-model system performs better.

## Framework

Preferred first path:
- current PyTorch CUDA;
- Transformers;
- PEFT;
- TRL;
- Accelerate/FSDP2 or a measured DeepSpeed/NeMo alternative;
- bf16;
- flash-attention/SDPA where compatible.

Pin every package/container revision.

HF references:
- https://huggingface.co/docs/trl/peft_integration
- https://huggingface.co/docs/peft/en/package_reference/lora

NVIDIA reference performance shows 8xH100 LoRA configurations can process 30B/70B-class models at useful throughput; use those numbers only for capacity planning. H200 has more memory/bandwidth but the exact Bossman model/runtime/dataset must be measured.

Reference:
https://docs.nvidia.com/nemo/automodel/performance/performance-summary

## Rental budget gates

Do not start an uncapped rental.

Before launch define:
- provider;
- 8xH200 hourly price;
- max hours;
- storage/egress;
- checkpoint storage;
- auto-stop condition.

Suggested first rental envelope: enough for **6–12 hours**, not a week.

Extend only if:
- pipeline stable;
- at least one candidate improves frozen validation;
- holdout is not being touched for tuning;
- no runaway checkpoint/storage issue.

## Stop conditions

Stop training if:
- loss becomes non-finite;
- throughput drops >30% without explained context/batch change;
- validation functional correctness regresses materially;
- visual preference gain is absent across two checkpoints;
- data contamination is detected;
- holdout leak is found;
- wrong template/tokenizer is discovered.

Do not "use the remaining paid hours" after a broken run.

## Artifacts per run

`runs/webdesigner/<run_id>/`:
- RUN.md;
- config.yaml;
- base model ID/revision;
- container/package lock;
- dataset hashes;
- git SHA;
- GPU topology;
- stdout/stderr;
- throughput;
- checkpoints;
- adapter;
- eval.json;
- renders;
- cost.json.

## Promotion

An adapter is promoted only if:
1. build/functional benchmark is not worse;
2. visual blind preference improves against base;
3. tool/schema behavior does not regress;
4. general coding sanity does not collapse;
5. local inference on the 128 GB owner machine is viable;
6. owner can disable/rollback it.

The output of H200 training is a candidate, not an automatic new default.
