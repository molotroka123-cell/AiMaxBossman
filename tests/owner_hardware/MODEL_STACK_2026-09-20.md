# Bossman Owner-Hardware Model Stack — 2026-09-20

This document defines the **target model fleet and the hardware acceptance matrix** for the owner's Ryzen AI Max+ 395 / Radeon 8060S / 128 GB machine.

It is an acceptance target, not a claim that every benchmark below has already been reproduced by Bossman on the owner's machine.

The purpose of HW testing is to answer two questions:

1. Which models actually work well on this exact machine?
2. Does Bossman route each class of task to the right model and improve completion rate versus using a single local LLM directly?

## Operational lanes

Bossman must treat the local AI stack as separate capability lanes rather than one undifferentiated model pool.

| Lane | Primary target | Secondary / fallback | Acceptance focus |
| --- | --- | --- | --- |
| Main LLM / coding brain | Qwen3.8-27B | Qwen3.6-35B-A3B | coding quality, context, general reasoning, repo work |
| Heavy local brain | Qwen3.8-Flash-Next | Qwen3.8-27B | hard reasoning, long context, multi-tool chains |
| Agent / computer use | Nex-N2.5-mini | Occamy-1.0 | screenshot→action→verify, tool reliability, GUI control |
| Autonomous tool / repo agent | Occamy-1.0 | Qwen3.8-Flash-Next | long-horizon agent work, repo automation, recovery |
| Fast generic workers | Qwen3.6-35B-A3B / Nemotron 3.5 Lightning | smaller approved local worker | throughput, low memory cost, parallel worker fleet |
| Verifier | gpt-oss-120B | Qwen3.8-Flash-Next | independent verification, contradiction finding, final review |
| Tools / structured output | Qwen3.8-Flash-Next + Occamy-1.0 | main model fallback | correct tool choice, exact args, schema compliance, multi-step tool use |
| Image quality | Qwen-Image-2512 | HunyuanImage 3.0 candidate | quality, text rendering, edit fidelity |
| Image fast/edit | FLUX.2 Klein 4B | Z-Image Turbo | latency, edit throughput, low-memory batch work |
| Video default | LTX-2.5 | HunyuanVideo 1.5 | T2V/I2V/video edit, audio+video when supported |
| Video quality | Wan 2.2 A14B | LTX-2.5 | motion quality, I2V/T2V quality |
| Vision / desktop | Nex-N2.5-mini | main multimodal model | UI grounding, state recognition, action verification |

## Important runtime rule

Strict JSON and JSON Schema compliance must not depend on model goodwill alone.

Bossman should use constrained decoding / grammar / schema enforcement at runtime where supported.

Acceptance therefore tests both:

- whether the model selected the correct tool and arguments;
- whether the runtime enforced the declared schema and rejected malformed output.

## HW-01 — Local AI: expanded model acceptance

The owner-hardware run must test, when locally runnable and available:

### Qwen3.8-27B
- conversation
- coding task
- repo edit + tests
- long-context retrieval
- structured output
- tool call
- multi-turn context
- restart persistence through Bossman

### Qwen3.8-Flash-Next
- hard reasoning
- multi-tool chain
- planner role
- structured output
- long context
- fallback/retry behavior
- memory footprint and sustained throughput

### Nex-N2.5-mini
- screenshot understanding
- target grounding
- semantic target first
- visual fallback
- coordinate fallback only when required
- action verification after each effect
- 10+ step desktop sequence without loop drift

### Occamy-1.0
- autonomous multi-step tool workflow
- repo task
- long-horizon recovery
- tool selection
- tool argument accuracy
- verifier handoff

### Qwen3.6-35B-A3B / Nemotron 3.5 Lightning
- fast worker throughput
- parallel worker load
- low-latency simple tool work
- memory pressure behavior

### gpt-oss-120B
- verifier-only test
- detect intentionally planted contradiction
- compare worker answer against evidence
- reject unsupported completion
- measure usable speed on this machine

A model that cannot run stably on the target machine is not marked failed as a model in general; it is marked **NOT SUITABLE FOR THIS LOCAL HARDWARE PROFILE** and removed from the default local route.

## HW-02 — Computer Use / GUI

Primary target: Nex-N2.5-mini.

Required sequence:

observation
→ identify app/window
→ semantic target
→ action
→ re-observe
→ verify effect
→ continue.

The run must include:

- semantic target success;
- accessibility/structured target when available;
- vision target;
- coordinate fallback;
- stale screenshot;
- moved target;
- modal interruption;
- wrong foreground app;
- retry with bounded loop count;
- approval-gated consequential action.

Bossman must not treat raw x/y as a normal primary control path.

## Tool-calling + structured-output benchmark

This is a separate acceptance lane.

For Qwen3.8-Flash-Next, Occamy-1.0, Qwen3.8-27B and the current fallback worker:

run the same tool suite.

Measure:

- correct tool chosen;
- correct tool not chosen when unnecessary;
- argument names;
- argument types;
- enum/schema compliance;
- required-field coverage;
- no hallucinated fields;
- multi-tool ordering;
- recovery after one tool failure;
- final state verification;
- number of owner interventions.

Record both raw-model validity and post-constrained-decoding validity.

## HW-07 — Image stack

### Quality mode
Qwen-Image-2512.

Test:
- text-to-image;
- image edit;
- text rendering inside image;
- multi-step edit;
- preserve subject/identity where required by task;
- reopen/export through Bossman;
- VRAM/unified-memory peak;
- generation time.

### Fast/edit mode
FLUX.2 Klein 4B.

Test:
- fast generation;
- edit;
- multi-reference edit where supported;
- batch throughput;
- output persistence.

### Ultra-fast mode
Z-Image Turbo.

Test:
- low-latency generation;
- quality/latency tradeoff.

### Candidate quality-top
HunyuanImage 3.0.

Do not make it default merely because upstream benchmarks are strong. Promote only after the same owner-hardware comparison on this machine.

## HW-08 — Video stack

### Default
LTX-2.5.

Test:
- text-to-video;
- image-to-video;
- video-to-video/edit where supported;
- synchronized audio+video when supported;
- restart-safe project;
- final render;
- ffprobe;
- full decode;
- memory peak;
- time to first result;
- total render time.

If a W4A8/quantized build is used, record quality and audio differences versus the full/default path.

### Quality
Wan 2.2 A14B.

Test:
- T2V;
- I2V;
- motion;
- temporal stability;
- quality versus LTX-2.5;
- memory and runtime cost.

### Light fallback
HunyuanVideo 1.5.

Test:
- ability to run under tighter memory budget;
- acceptable quality floor;
- fallback routing when larger model cannot be resident.

## Routing acceptance

The router must not simply use the largest model.

Test automatic routing for:

- simple chat;
- code patch;
- hard coding;
- GUI control;
- multi-tool automation;
- image generation;
- image edit;
- video generation;
- verifier pass;
- long-running autonomous mission.

For every route record:

- requested task class;
- selected model;
- why selected;
- fallback model;
- actual completion;
- latency;
- memory;
- owner intervention;
- cloud escalation if any.

## Resident fleet test

Because 128 GB unified memory enables multiple useful local roles, test at least one resident-fleet configuration rather than loading one model per task.

Candidate experiment:

- resident heavy/main brain;
- resident GUI/agent worker;
- optional fast worker;
- verifier loaded on demand if needed.

Measure:

- total memory;
- swap/page pressure;
- first-token latency;
- concurrent task behavior;
- model-switch overhead;
- system responsiveness.

A dual-Qwen3.8-Flash-Next resident setup may be evaluated as an experiment, but it is not the default until measured on the owner's exact machine.

## A/B proof: bare local LLM vs Bossman

Use the same underlying local model.

A = direct model/runtime.
B = same model through Bossman.

Use unseen tasks.

Measure:

- tasks completed;
- correctness;
- tool success;
- context retention;
- restart survival;
- number of owner interventions;
- total time;
- memory;
- cloud cost;
- recovery after failure.

Bossman is considered to add real value only if orchestration improves task completion/reliability enough to justify its complexity.

## Promotion rule

No model becomes a Bossman default because of internet benchmarks alone.

Promotion requires:

1. owner-hardware run;
2. same-task comparison;
3. stable runtime;
4. acceptable memory;
5. acceptable latency;
6. no regression in tool/schema reliability;
7. evidence recorded in the owner-hardware certificate.

## EVO 1.0 relationship

The current release may use these results to make recommendations only.

EVO 1.0 may later learn from accumulated benchmark and mission outcomes and propose model-routing improvements.

It must not silently change the stable model fleet or self-modify the production core.

Any routing/model promotion in EVO remains:

stable
→ isolated candidate
→ benchmark
→ verifier
→ owner approval
→ promotion
→ rollback if regression.
