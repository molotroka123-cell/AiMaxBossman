# Epoch 6 — Hardware, RAM/VRAM & Model Residency

## Current truth

Exact Bossman framework RAM/VRAM footprint is **UNKNOWN** until measured on the frozen candidate. Estimates such as `idle 3–4 GB`, `15–20% CPU`, or specific Strix Halo tokens/s remain hypotheses unless backed by an exact-SHA run.

## Measure components separately

1. Core Python process;
2. Gateway;
3. Command Center;
4. Postgres/Redis/container services if part of the selected deployment;
5. Chromium/Playwright/browser helpers;
6. UIA/helper processes;
7. FFmpeg workers;
8. Fleet/background workers;
9. Ollama/runtime/model/KV cache.

Record idle, scenario HWM and post-task retained memory.

## Dedicated GPU systems

Where GPU exposes dedicated VRAM:

- process working set/private bytes;
- dedicated VRAM by runtime/process where available;
- shared GPU/system memory;
- model/KV allocation reported by runtime;
- paging/OOM/headroom.

Do not call model allocation «Bossman framework memory».

## Ryzen AI Max+ / Strix Halo unified memory

CPU and iGPU share one physical memory budget. Therefore report:

- OS committed memory;
- Bossman process-tree working set/private bytes;
- runtime model allocation;
- shared/unified GPU allocation;
- total HWM;
- free/headroom before paging/OOM.

Avoid double counting the same pages as independent `RAM + VRAM`.

## Model residency experiments

For each candidate model/config record:

- load time;
- TTFT;
- tokens/s;
- context length;
- KV cache type/size;
- resident allocation;
- queue wait;
- reload count;
- quality/verified success;
- OOM/paging.

Test `keep_alive`, max loaded models, parallelism, Flash Attention and KV cache only through A/B. No universal tuning value is assumed correct.

## Routing policy goal

Prefer one compatible warm model when quality gates allow and memory headroom is sufficient. Avoid model churn that spends seconds loading/unloading for small task-class differences. Large specialist models may remain on-demand when their residency harms interactive work.

## Resource admission

Before expensive jobs:

- reserve expected memory envelope;
- account for currently resident model + KV + media jobs;
- leave safety headroom;
- prioritize owner controls/verification over batch work;
- defer rather than oversubscribe into system-wide paging.

## Hardware scaling conclusion rule

Single-host vs scale-up vs scale-out is decided from measured bottleneck:

- memory pressure only → benchmark scale-up;
- concurrent independent task pressure → benchmark scale-out;
- one model does not fit → only then evaluate distributed inference;
- unreliable software → fix software first.

Socket count alone is not a performance requirement.