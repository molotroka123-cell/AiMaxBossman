# AiMaxBossman — Local Model Candidate Matrix

Date: 2026-09-07
Target hardware: AMD Ryzen AI Max+ 395 / Radeon 8060S / 128 GB unified memory (Strix Halo)

> Status: research/candidate matrix. Treat external benchmark numbers as provisional until reproduced on the owner machine with the same runtime, quant, context and prompt suite.

## Current baseline candidates

| Model | Primary role | Code / agents | Vision | Context | Approx. local memory | Strix Halo notes |
|---|---|---|---|---:|---:|---|
| Qwen3.8-27B ROCmFP4 | main multimodal local brain | strong coding + agent baseline | yes | ~262K | ~13.55 GiB weights | reported ~30–36 tok/s with MTP-class tuning; verify locally |
| Qwen3.6-35B-A3B | fast worker / tool executor | strong fast MoE agent | model-dependent multimodal build | ~262K | ~18–25 GiB quant-dependent | attractive resident worker due to small active params; verify exact runtime |
| gpt-oss-120B MXFP4 | hard reasoning / verifier | strong reasoning + code review | no | ~128K-class | ~59–70 GiB | heavy but practical inside 128 GB; use selectively |
| K2-Horizon-MoVA-36B-A4B | long-context tool/agent candidate | promising tool + terminal profile | no | ~512K | ~18.6 GiB FP4-class candidate | reported ~41 tok/s decode on Strix Halo; must reproduce before promotion |
| Qwen3.8-Flash-Next tuned | heavy multimodal / long-context candidate | strong agent + coding candidate | yes | 262K native / larger extensions | large; runtime/quant dependent | only promote if tuned build remains stable at long context and memory headroom is acceptable |

## Tool calling + structured outputs

Models must be evaluated separately from generic intelligence. Required local suite:

- correct tool selection;
- correct argument names/types/values;
- JSON / JSON Schema compliance;
- multi-tool and multi-step reliability;
- context/state preservation across tool calls;
- failure recovery and retry behavior;
- performance when 10 / 25 / 50+ tools are exposed;
- hallucinated-tool rate;
- malformed-call rate;
- schema-repair success rate.

Do not assume the smartest coding model is automatically the best tool router.

## Image generation track

Keep a separate benchmark lane for local image models. Compare:

- visual quality and prompt adherence;
- text rendering;
- edit/inpaint/outpaint support;
- ControlNet/reference/LoRA support;
- peak unified-memory use;
- cold start and warm generation time;
- ROCm/ComfyUI stability on gfx1151.

Current practical candidates remain Qwen-Image-class, Z-Image/FLUX-class and any newer Strix-Halo-optimized releases that beat them on quality-per-minute or memory-per-image.

## Video generation track

Keep a separate benchmark lane for local video models. Compare:

- text-to-video and image-to-video quality;
- motion consistency and faces/hands;
- native audio if supported;
- duration, resolution and frame count;
- VRAM/unified-memory footprint;
- generation minutes per output second;
- ROCm stability, tiled VAE behavior and crash recovery.

Current practical families to keep testing: LTX-class and Wan-class models, including animate/motion-transfer variants. Promote only when a new release materially improves quality, speed, duration or local stability on Strix Halo.

## Promotion rule

A new model replaces an existing resident model only if it wins a reproducible local A/B on at least one important axis without an unacceptable regression elsewhere:

1. coding success rate;
2. autonomous-agent task completion;
3. tool-call / schema reliability;
4. vision quality;
5. long-context stability;
6. memory footprint;
7. real end-to-end speed on Ryzen AI Max+ 395.

All benchmark records must include: model hash, quant, runtime/backend, driver/ROCm version, context length, prompt suite version, memory peak, prefill tok/s, decode tok/s, task score and failures.