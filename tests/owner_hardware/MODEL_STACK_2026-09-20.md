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


## Search refresh — additional top candidates to benchmark

The following models are added by the 2026-09-20 search refresh. They are **candidate lanes**, not automatic defaults. Bossman must benchmark them against the existing targets on the owner's exact hardware before promotion.

### Interactive coding / repo work — Qwen AgentWorld 35B-A3B

Add as a direct challenger to Qwen3.8-27B for interactive coding and repo-agent work.

Why it is in the test pool:
- a published 128 GB Strix Halo coding matrix reported 8/10 executable coding tasks at about 7.2 s/task;
- in that same matrix Qwen3.8-27B Q6_K also scored 8/10 but took about 12.5 s/task;
- therefore AgentWorld is a strong candidate for the **interactive coding** route even if Qwen3.8-27B remains the more general main brain.

Required comparison:
- same repo-edit tasks;
- same tool contracts;
- same context;
- first useful patch latency;
- final tests passed;
- owner interventions;
- hallucinated edits / protocol failures.

### Accuracy / difficult-code verifier — gpt-oss-120B

Keep as a first-class accuracy/verifier candidate, not merely a text verifier.

A published Strix Halo coding matrix reported 9/10 executable coding tasks, the best result in that particular 23-deployment comparison. Another reproducible Strix Halo performance repository reports roughly 53 tok/s generation for a Q4_K_M-class deployment.

Required comparison:
- hard bug fix;
- independent review;
- contradiction detection;
- final diff verification;
- latency versus Qwen3.8-Flash-Next;
- whether its quality gain justifies loading cost.

### Heavy long-context candidate — DeepSeek V4 Flash

Add to the experimental heavy lane.

Why:
- current Strix Halo deployments show the 284B-class model can fit on 128 GB with aggressive quantization;
- reproducible Vulkan measurements report roughly mid-30 tok/s generation at a 122k-token test point and successful long-context retrieval;
- it is therefore interesting for large-context reasoning/retrieval even though it is too memory-heavy to assume as a resident default.

Required:
- long-document retrieval;
- coding;
- tool/schema compliance;
- 128K+ context stability;
- total resident memory;
- system responsiveness;
- compare against Flash-Next on the same task.

Do not make DeepSeek V4 Flash resident by default unless it beats the smaller fleet strongly enough to justify consuming most of the machine.

### Heavy frontier candidate — GLM-5.3-Flash

Add to discovery inventory, not default routing.

The current open-weight ecosystem describes it as a very large MoE with a small active fraction and very long context, but its smallest practical quant is around the limit of a 128 GB unified-memory workstation.

Acceptance:
- first prove it loads without destructive memory pressure;
- then compare coding, agent behavior and long-context retrieval;
- reject it from the production local route if system responsiveness or headroom is unacceptable.

### Fast worker — Nemotron 3.5 Lightning

Retain as a speed/specialist candidate rather than a main intelligence model.

The important question for Bossman is not whether it wins a general intelligence leaderboard. Test whether it is useful as a cheap resident worker for:
- extraction;
- classification;
- schema-bound transformation;
- short tool decisions;
- parallel worker jobs;
- long-context specialist tasks.

Promote only if it materially reduces latency/memory cost without increasing downstream verifier corrections.

### GUI / desktop — Nex-N2.5-mini remains the primary measured candidate

Current Strix Halo benchmark data directly supports keeping Nex-N2.5-mini at the front of the GUI-worker test pool:
- ROCmFP4 Strix quant around 17.32 GiB;
- measured decode around 76.9 tok/s Vulkan / 68.6 tok/s ROCm;
- vision projector available;
- very high prefill in the published ROCm measurement.

But it must still beat alternatives on **completed GUI tasks**, not tokens/sec.

### Autonomous tool agent — Occamy-1.0 remains a primary candidate

Its released benchmark table supports keeping it in the tool/autonomy pool: strong Claw-Eval, AutomationBench and BFCL v4 relative to its Qwen3.6 starting checkpoint.

Bossman acceptance must reproduce a smaller owner-specific suite:
- browser + files + terminal;
- 10–20 step tool chain;
- recovery after one failed tool;
- no duplicate consequential effect;
- exact schema arguments;
- final evidence verification.

### Image quality — HunyuanImage 3.0 promoted to serious challenger

Current open-source-filtered image arena data places HunyuanImage 3.0 above FLUX.2-dev and Qwen-Image-2512 in overall preference.

Therefore HW-07 must compare:
- HunyuanImage 3.0;
- Qwen-Image-2512;
- FLUX.2 Klein 4B;
- Z-Image Turbo.

Do not replace Qwen-Image-2512 automatically: licensing, local runtime maturity, edit workflow, text rendering, memory and speed matter in addition to arena preference.

### Video — add MiniMax H3 local as an experimental quality/audio challenger

MiniMax has released H3 weights and describes native audio+video generation. Add the **local H3 base path** to HW-08 candidate testing.

Important boundary:
- local/open-weight capability and the full hosted high-resolution pipeline must be recorded separately;
- do not claim hosted 2K functionality as local Bossman capability.

HW-08 candidate pool becomes:
- LTX-2.5 — default/audio+video workflow candidate;
- Wan 2.2 A14B — mature quality candidate;
- HunyuanVideo 1.5 — strong/light fallback candidate;
- MiniMax H3 local — experimental native-audio quality challenger.

Also record current open-source video arena results as discovery evidence only; they do not replace the owner-hardware comparison.

## Revised practical shortlist for the first owner run

To avoid spending the first day downloading every interesting model, test in waves.

### Wave 1 — must test
- Qwen3.8-27B
- Qwen3.8-Flash-Next
- Nex-N2.5-mini
- Occamy-1.0
- gpt-oss-120B
- Qwen-Image-2512
- FLUX.2 Klein 4B
- LTX-2.5
- Wan 2.2 A14B

### Wave 2 — challengers
- Qwen AgentWorld 35B-A3B
- DeepSeek V4 Flash
- Nemotron 3.5 Lightning
- Z-Image Turbo
- HunyuanImage 3.0
- HunyuanVideo 1.5
- MiniMax H3 local

### Wave 3 — only if practical
- GLM-5.3-Flash
- alternative large/heavy models discovered after the freeze

The purpose is not to collect models. The purpose is to end with the **smallest fleet that wins the owner's real tasks**.
