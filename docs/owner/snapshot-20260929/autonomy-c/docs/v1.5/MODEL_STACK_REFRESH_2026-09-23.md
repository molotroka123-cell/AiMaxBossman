# Bossman 1.5 — Model Stack Refresh — 2026-09-23

**RESEARCH / BENCHMARK PLAN. NOT A CLAIM THAT EVERY MODEL IS INSTALLED OR OWNER-HARDWARE CERTIFIED.**

Target hardware: Ryzen AI Max+ 395 / Radeon 8060S / 128 GB unified memory.

This refresh preserves the existing four required lanes:
1. LLM / coding / agents;
2. image generation;
3. video generation;
4. tool-calling + structured output.

Voice/OCR/GUI remain specialist lanes.

## Bottom line

The current Bossman model inventory is strong, but it must **not** be described as permanently "the best stack".

A better claim is:

> Bossman has a high-quality candidate fleet for this hardware, and defaults are promoted only by same-task, same-runtime measurements on the owner's machine.

The major new challenger on 2026-09-23 is **Xing4.0-29B-A4B**.

## Xing4.0-29B-A4B

Publisher: XingChen-AGI / China Telecom AI.
License: Apache-2.0.
Architecture: ~29B total, ~4B active MoE.
Context: 256K native, publisher says extensible to 512K.
Official ecosystem: Transformers, vLLM, SGLang, KTransformers; official GGUF exists.

Publisher-reported results include:
- SWE-bench Verified: 75.0;
- Terminal-Bench 2.1: 57.5;
- SWE-bench Multilingual: 66.0;
- Claw-Eval: 76.55;
- DeepresearchBII: 60.8.

The publisher compares it closely with Qwen3.6-35B-A3B, with Xing ahead on Terminal-Bench 2.1 / Claw-Eval / DeepresearchBII and Qwen ahead on several reasoning/software benchmarks.

### Bossman decision

**ADD AS FIRST-CLASS CHALLENGER, DO NOT BLINDLY REPLACE MAIN.**

Best initial roles:
- fast coding worker;
- terminal/repo agent;
- tool-using sub-agent;
- Game Studio coding worker;
- parallel swarm worker.

Why:
- only ~4B active per token;
- agent-oriented post-training;
- long context;
- official GGUF path;
- permissive license;
- size is practical for the 128 GB machine.

Unknown until owner test:
- actual Radeon 8060S throughput;
- best llama.cpp/Vulkan/ROCm route;
- prompt-template/tool-parser reliability in Bossman;
- structured-output failure rate;
- long-context prefill cost;
- performance under concurrent workers.

Official PC tutorial demonstrates an int4 GGUF around 19 GB on an RTX 3090-class setup, but that is not AMD owner-hardware evidence.

## Recommended local routing candidates

### MAIN — Qwen3.8-27B
Keep as main until Xing wins the same Bossman tasks.

Reason:
- already targeted/tested in the Bossman owner stack;
- dense multimodal generalist;
- 262K native context;
- public Strix Halo measurements exist around the same Ryzen AI Max+ 395 / Radeon 8060S class.

Published third-party Strix Halo measurements vary strongly by runtime/quant. One reproducible llama.cpp/Vulkan setup reports roughly 26 tok/s for Q4-class generation; therefore runtime configuration matters as much as model name.

### FAST / AGENT — Xing4.0-29B-A4B vs Qwen3.6-35B-A3B
Run head-to-head.

Promote Xing only if it improves:
- verified completion;
- tool choice;
- argument/schema correctness;
- time-to-verified-result;
- owner interventions;
- RAM/throughput under swarm load.

### LONG-HORIZON AGENT — Occamy-1.0
Keep in the pool.

Occamy is explicitly trained for stateful co-work across code, tools, files and long trajectories. It is based on Qwen3.6-35B-A3B and remains a distinct specialist from a general MAIN model.

### GUI / COMPUTER USE — Nex-N2.5-mini
Keep as specialist candidate rather than forcing a text coding model to do GUI grounding.

Native desktop/browser visual interaction must be measured through the exact Bossman computer-use loop.

### HEAVY LOCAL / EXPERIMENTAL — Qwen3.8-Flash-Next
Keep experimental, not resident default.

It is a 125B-total / 6B-active multimodal MoE plus a very large n-gram table and has unusual offload/runtime requirements. It can be attractive on 128 GB unified-memory systems, but storage/offload/runtime details dominate usability.

### VERIFIER — gpt-oss-120B
Keep as on-demand independent verifier/reasoner.

OpenAI documents 117B total / 5.1B active, Apache-2.0, 131K context, function calling and structured outputs. Do not keep it resident if that harms interactive fleet responsiveness.

## Cloud / non-local frontier route

### DeepSeek-V4.1-Flash
Refresh old references to DeepSeek V4 Flash for cloud use.

DeepSeek released V4.1-Flash on 2026-09-10 and reports major agent/coding improvements, including a much higher Terminal-Bench 2.1 score than the previous V4 Flash.

Treat it as a cloud route, not a practical resident local model for the 128 GB owner machine: the new model is a much larger MoE. LOCAL_ONLY data must never silently use it.

### GLM-5.3-Flash
Keep in discovery/cloud/heavy-lab inventory.

Its 320B-total / 18B-active footprint and serving requirements make it a poor default resident choice for this machine even if aggressive quantization experiments become possible.

## Image lane

Current choices remain sensible:

- Quality/default: Qwen-Image-2512.
- Heavy quality research: HunyuanImage-3.0 family.
- Fast/edit: FLUX-family lightweight route and Z-Image Turbo where local runtime is proven.

Important hardware note:
HunyuanImage-3.0 official full model is extremely large; upstream documentation recommends multi-80GB GPU-class hardware for the main checkpoints. It should not be assumed to be a practical owner-machine default.

Qwen-Image-2512 is a much more realistic local quality baseline and has standard Diffusers/ComfyUI ecosystem support.

## Video lane

Current choices remain reasonable:
- default/local research: LTX-2.5;
- quality challenger: Wan 2.2 A14B;
- lighter fallback: HunyuanVideo 1.5.

But "best video model" is task-specific. Measure:
- T2V;
- I2V;
- identity consistency;
- motion;
- text/logo handling;
- accepted seconds / generated seconds;
- generation time;
- peak memory;
- restart/recovery.

Note: LTX-2.5 uses a community/commercial license rather than Apache/MIT. Commercial usage terms must be checked before treating it as a universal free production default.

## Tool / structured-output lane

This lane is separate from raw coding score.

Mandatory challengers:
- Xing4.0-29B-A4B;
- Qwen3.8-27B;
- Qwen3.6-35B-A3B;
- Occamy-1.0;
- Qwen3.8-Flash-Next when available.

Measure raw and constrained-decoding results:
- correct tool chosen;
- no unnecessary tool;
- required arguments;
- types/enums;
- no hallucinated fields;
- multi-tool ordering;
- retry/recovery;
- final-state verification;
- owner interventions.

Schema enforcement stays in runtime. Never rely on model goodwill.

## 2026-09-24 owner benchmark matrix

Use unseen tasks. Same inputs and permissions.

### Coding / repo
- 5 small fixes;
- 3 multi-file fixes;
- 2 long-context repo tasks.

### Agent / tools
- 5 multi-tool workflows;
- one forced tool failure;
- one restart;
- one stale-state/unknown-outcome case.

### Game Studio
- Unreal project create/edit;
- compile/build failure repair;
- Blueprint/Python tool call;
- one autonomous playtest bugfix.

### Structured output
- 50 JSON/tool cases with hidden schema mutations.

### Resource test
For each candidate:
- load time;
- RAM after load;
- prompt processing;
- generation tok/s;
- p50/p95 verified-result latency;
- concurrent 2/4-worker behavior;
- unload/reload;
- system responsiveness.

## Promotion rule

Do not ask "which benchmark winner is the best model?"

Ask:

> Which route gives Bossman the highest verified task success per unit of latency, memory and owner intervention on this exact machine?

A model becomes default only after:
1. exact checkpoint/revision/quant recorded;
2. stable runtime;
3. same-task A/B;
4. no tool/schema regression;
5. acceptable RAM and latency;
6. restart/recovery success;
7. independent verifier;
8. owner-hardware evidence.

## Expected fleet after testing

Most likely architecture, pending measurements:

```
MAIN            Qwen3.8-27B
FAST/AGENT      Xing4.0-29B-A4B  <-> Qwen3.6-35B-A3B
LONG AGENT      Occamy-1.0
GUI             Nex-N2.5-mini
HEAVY           Qwen3.8-Flash-Next (on demand)
VERIFIER        gpt-oss-120B (on demand)
IMAGE           Qwen-Image-2512 + fast edit route
VIDEO           LTX-2.5 / Wan 2.2 / HunyuanVideo 1.5 by task
CLOUD FRONTIER  DeepSeek-V4.1-Flash + approved premium routes
```

This is a benchmark target, not a declaration that every item is currently installed.

---

## SAME-RUN HANDOFF: 1.5 → 1.6

После закрытия 1.5 или достижения `V1_5_NEAR_COMPLETE` этот же owner-run сразу переходит в отдельную ветку:
[feat/bossman-1.6-secret-intake-20260925](https://github.com/molotroka123-cell/AiMaxBossman/tree/feat/bossman-1.6-secret-intake-20260925)

`V1_5_NEAR_COMPLETE`: software P0 = 0, release-blocking P1 = 0, основные targeted/regression/hidden tests зелёные, canonical SHA зафиксирован; остаток только owner-live/soak/внешняя среда.

Не ждать отдельного следующего дня. Цель одного owner-run:
`BOSSMAN_1_5_CLOSED -> BOSSMAN_1_6_CLOSED`.

Evidence 1.5 и 1.6 сохраняются раздельно по своим SHA.

---

## Immediate handoff to Bossman 1.6

As soon as Bossman 1.5 reaches OWNER_READY closure — or only explicitly non-blocking P2 / optional OWNER_REQUIRED items remain — continue the SAME owner run in:

[Bossman 1.6 self-evolution branch](https://github.com/molotroka123-cell/AiMaxBossman/tree/feat/bossman-1.6-self-evolution-20260925)

Start with `BOSSMAN_1_6_START_HERE.md`.

One-run objective:

`BOSSMAN_1_5_OWNER_READY -> 1.6 HANDOFF -> BOSSMAN_SELF_EVOLUTION_RUNNING`

Do not use this handoff to hide an open 1.5 P0 or release-blocking P1. The goal is to close both stages in one continuous owner run whenever the critical 1.5 gates allow it.
