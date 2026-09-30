# Bossman 1.6 — Foundation Freeze

Branch: `feat/bossman-1.6-bossnet-foundation-20260925`

Base: current Bossman 1.5 learning branch.

This branch is the dedicated 1.6 design/implementation lane. Do not merge it
back into 1.5 merely to keep branches synchronized. 1.5 must remain independently
closable/testable.

## Five pillars

1. Distributed Brain.
2. Specialist Model Foundry.
3. Temporal Knowledge Fabric.
4. Simulation World / Digital Twin.
5. Provider Fleet + Economic Scheduler.

## Absolute compute hierarchy

RunPod/rented GPU is **NOT a primary provider**.

Default order:

1. already available legitimate free capacity across approved providers;
2. already funded/prepaid primary provider capacity;
3. AI Max local compute when capability/privacy/time fit;
4. cheap paid API capacity;
5. approved secondary/subaccount capacity only where provider terms permit;
6. **rented GPU only after explicit owner approval for that rental/budget**.

A deadline or model-size constraint may make rental technically preferable, but
Bossman still cannot rent without owner spend authorization.

## RunPod hard rule

No implicit rental.

Jev/Bossman may:
- estimate;
- compare GPUs;
- prepare the workflow;
- present cost/time benefit;
- prepare exact commands/config.

Jev/Bossman may NOT:
- create/start paid GPU capacity without current owner authorization;
- raise budget;
- extend runtime beyond approved cap;
- leave idle compute running.

Owner approval binds:
`provider + max USD + max runtime + GPU class/count + workload id`.

Any mismatch requires new approval.

## If rental is approved: maximize the burst

Because rented compute is expensive, do not rent an H100/H200 merely for one
small inference if the same paid window can safely execute a prepared batch.

Before pod creation, Bossman builds a **Rental Work Pack** containing compatible
queued jobs, for example:
- K1M6A video VLM/ASR batch;
- embeddings/index rebuild;
- frozen LoRA/distillation experiment;
- model benchmarks;
- synthetic hard-negative generation;
- offline verifier batch;
- quantization/conversion;
- large replay/evaluation suite.

Jobs must be independent enough that one failure does not corrupt the others.

Schedule by GPU utilization/memory fit and deadline. Never mix a training job
with an evaluation holdout in a way that contaminates the benchmark.

## Rental efficiency metrics

Every approved rental reports:
- useful GPU-seconds / billed GPU-seconds;
- idle percentage;
- jobs completed;
- source video hours processed;
- training examples processed;
- benchmarks completed;
- checkpoints produced;
- verified artifacts produced;
- dollars per verified artifact;
- dollars per source-video hour;
- dollars per training million tokens/examples where applicable;
- startup/bootstrap overhead;
- teardown confirmation.

Target is high utilization, not merely "GPU was busy". Useless training that
fails verification has zero promoted-value output.

## Pre-stage before billing starts

Where platform semantics permit, prepare before rental:
- frozen Git SHA;
- container/image;
- dataset manifest;
- model weights/cache plan;
- task graph;
- benchmark manifests;
- expected output paths;
- cleanup script;
- max runtime/budget.

Do as much CPU/network preparation as possible before expensive GPU time begins.

## Training burst

One approved training rental may train/evaluate several specialists sequentially
or in safe parallel:
- Bossman-Coder;
- Bossman-Vision-Chart;
- Bossman-Market;
- Bossman-Router;
- Bossman-Verifier.

But each has separate:
- dataset hash;
- holdout;
- experiment id;
- checkpoint;
- benchmark;
- promotion decision.

"Train everything" never means sharing holdout answers or blindly mixing
datasets.

## Stop conditions

Immediately stop paid compute on:
- owner STOP;
- budget cap;
- max runtime;
- workload completion;
- bootstrap failure;
- no-progress watchdog;
- verifier-blocking failure;
- unsafe configuration.

Cleanup/termination proof is part of success.

## 1.6 foundation rule

Build contracts, simulators and tests before connecting paid side effects.

Provider adapters start read-only/status-first.
RunPod lifecycle first gets mock/sandbox tests.
The first real rental is a separate owner-approved acceptance event.
