# Bossman 1.8 — Evolution Engine

Status: THEORETICAL DESIGN. Do not implement before 1.5 + 1.6 + 1.7 freeze.

## North Star
**Every verified task makes the next similar task cheaper, faster or better.**

When foreground owner work is absent, Bossman may enter bounded `IDLE_EVOLUTION_MODE`:
`observe weakness → rank opportunity → hypothesis → isolated candidate → benchmark → independent verifier → unseen transfer → PROMOTE/REJECT → measure long-term effect`.

Foreground owner work always preempts self-improvement.

## Practical objective
Optimize:
`VALUE = RevenueGain + OwnerTimeSaved + ReliabilityGain + QualityGain - Cost - Risk - Complexity`.

Priority:
1. make money;
2. save owner time;
3. reduce manual intervention;
4. improve reliability;
5. reduce model/context cost.

Do not optimize vanity metrics.

## Reuse, do not rebuild
1.8 must not create a second backend, task engine, memory authority, provider registry, Jev, Studio, Telegram stack or Agent Society. Reuse 1.5–1.7 Scientific Self-Improvement, Society, Skill Compiler, Operating Graph, Resource Manager, Economy Orchestrator, BossNet, Distributed Brain, mathematical memory retrieval, Business Growth Engine, Jeff/PIT, Studio, OpenHands, Computer Use, Jev, approvals, evidence and STOP.

# Twelve core systems

## 1. Task Compiler / Execution Graph
Compile owner goals into a DAG over existing Bossman tasks:
`goal → subtasks → dependencies → workers → verification → outcome`.
Parallelize independent nodes; survive restart; never blindly repeat verified external effects.
Borrow-first references: LangGraph durable graphs; Temporal durable-workflow semantics. Do not replace Bossman's task engine without evidence.

## 2. Jev Router 2.0 / Model Market
Route empirically using per task class/model: verified quality, latency, context/tokens, cost, retries, tool reliability, hardware pressure and fallback history. Unknown task classes may use bounded cheap shadow/calibration competition.
Borrow-first: RouteLLM concepts; LiteLLM routing/fallback/cost concepts. Keep Bossman gateway canonical.

## 3. Context Economy / Memory Compiler
Treat context as a budget. Optimize `VERIFIED_OUTCOME / CONTEXT_TOKEN`.
Worker packet: objective + constraints + current state + minimal evidence + relevant skill + UNKNOWNs.
Idle compaction may deduplicate, supersede and surface contradictions.
Borrow-first: Graphiti, Mem0, Letta patterns.

## 4. Rollout Lab 3.0
Core self-improvement loop:
`BASELINE ENV A vs CANDIDATE ENV B → deterministic metrics → independent verifier → security regression → unseen task`.
Candidate never receives holdout answers.
Low-risk auto-optimization: prompts, retrieval/routing weights, context packing, skill selection, retries, caching, execution ordering.
Security/authority/money/credentials/destructive actions never auto-promote.
Borrow-first: existing OpenHands sandbox/execution path; DSPy/GEPA optimization concepts.

## 5. Skill Compiler 2.0
Verified workflows become versioned executable skills with id/version, task class, inputs, graph, models/tools, permissions, expected evidence, benchmark, failure cases, cost profile and success history.
Compile only after successful execution + independent verification + reuse.
Borrow-first: Anthropic Agent Skills and Letta concepts.

## 6. Computer World Model
Persistent semantic map:
`Windows → apps → screens → controls → actions → states → verified transitions`.
Successful UI actions become candidate reusable computer skills. Goal: fewer vision calls, fewer random clicks, faster reliable UI operation.
Borrow-first: Microsoft UFO Windows AgentOS concepts; Browser Use self-healing browser patterns.

## 7. AI Max Resource Brain
Treat Ryzen AI Max+ 395 / Radeon 8060S / 128 GB as unified-memory compute.
Track available/committed memory, model residency, CPU/GPU telemetry where available, Studio, ASR/VLM/LLM workloads and foreground priority.
Heavy local workload may route foreground Jeff to verified free cloud; return local when resources recover.
Do not assume NVIDIA VRAM semantics.
Borrow-first: Ray scheduling ideas; llama.cpp/local runtime concepts. Do not add a mandatory second scheduler without evidence.

## 8. Multimodal Brain
Automatically classify inputs TEXT/PHOTO/MULTI-PHOTO/VIDEO/AUDIO/DOCUMENT and intents UNDERSTAND/GENERATE/EDIT/COMPARE/EXTRACT/TRANSFORM.
Studio remains canonical media execution plane.
Borrow-first: ComfyUI workflow ideas; stable-diffusion.cpp local media runtime.

### AI Video Editing Workspace — Kadr
Canonical candidate:
- `HelpFreedom/kadr`
- Usefulness: 9.8/10
- Architecture fit: 10/10
- Owner-hardware fit: high; Electron/WebGL2/ffmpeg path does not require CUDA
- Windows integration readiness: medium-high, but its embedded Claude session currently contains Linux-specific process/session assumptions that must not be treated as production-ready on Windows without adaptation
- License: GPL-3.0

Bossman should use Kadr as an **external video-editing workspace/process through MCP/API-style integration**, not copy GPL-3.0 source into Bossman core unless the licensing consequences are explicitly accepted.

Preferred architecture:
`Bossman → Jev media intent → Studio/media assets → Kadr MCP/adapter → live timeline → preview/snapshot → visual verifier → export`.

Division of responsibilities:
- Qwen Image / Wan / other Studio models generate media assets;
- Kadr performs timeline editing, subtitles, beat alignment, Remotion fragments, TTS/voice-over workflows and export;
- Bossman acts as director/orchestrator;
- Jev chooses generation/editing route;
- vision verifier checks preview/final frames;
- Aster audits UX and workflow reliability.

Useful Kadr capabilities to reuse via adapter:
- live project state;
- timeline edits with undo;
- snapshot;
- export;
- transcription;
- Remotion fragment creation;
- beat-aligned editing;
- captions;
- ffmpeg export;
- autosave/recovery;
- storage/cache visibility;
- AI-accessible project controls.

Do not expose unrestricted Kadr eval/file/PTY authority to participant Jeff. Kadr is an owner/control-plane capability only unless a narrower sandboxed media tool is explicitly approved.

## 9. Agent Society 2.0
Agents become measured specialists. Track speciality, verified quality, context efficiency, latency, tool success, skills, cost and failures. Task Compiler assembles temporary teams based on measured history.
Borrow-first: AutoGen team/bench concepts; CAMEL society/critic concepts.

## 10. Bossman Observatory
One justified major UI surface: a brain X-ray.
Show `owner task → compiler → Jev → models → tools → verifier → outcome → learned delta`.
Per-model: calls, verified success, tokens/context, p50/p95 latency, retries, tool success, data processed, cost and fallback reason.
Also measure owner time saved, frontier calls avoided, business impact and before/after improvements.
Borrow-first: Langfuse and Arize Phoenix schemas/UX. Prefer extending Bossman telemetry to deploying redundant stacks.

### Live cognitive-state UI — Thinking Orbs
Canonical UI candidate:
- `Jakubantalik/thinking-orbs`
- License: MIT
- Usefulness for Bossman UI: 9.5/10
- Integration stance: lightweight visual state layer only; never a source of truth.

The package provides nine tuned states rendered with plain Canvas 2D and no WebGL/filter dependency. Use it to make Bossman/Jev activity legible without adding another backend or telemetry path.

Map orb states only from **real canonical task/telemetry state**:
- `working` → executing a task/tool chain;
- `searching` → Internet Radar/web/repository retrieval;
- `solving` → planning/reasoning/rollout evaluation;
- `listening` → voice/owner input capture;
- `connecting` → provider/MCP/remote-node connection;
- `weaving` → multi-agent/team/task-graph assembly;
- `composing` → writing/code/media/artifact generation;
- `breathing` → healthy idle/background wait;
- `shaping` → skill compilation/distillation/evolution candidate formation.

Requirements:
- orb state must be derived from Observatory events, not invented by the frontend;
- STOP/failure/OWNER_REQUIRED must override decorative animation with explicit textual status;
- respect reduced-motion/accessibility behavior;
- suspend/offscreen animation when hidden;
- do not use the orb as evidence that work is actually progressing;
- Command Center may use the 64px/inline variants; future phone/React-Native UI may reuse the project ports if they pass parity/UX tests.

## 11. Distillation Foundry
Purpose: convert strong/expensive verified teacher behavior into cheaper local specialist capability.

Pipeline:
`authorized teacher → real task → observable actions/tool calls + artifact → independent verifier → privacy/license filter → dataset → local student → frozen benchmark → unseen transfer → Model Market promotion`.

Do not store hidden chain-of-thought. Store only allowed outputs, tool/action traces, evidence, artifacts, scores and authorized summaries. Do not violate provider terms/licensing or put private participant data/secrets into shared distillation corpora.

Possible students:
- bossman-coder
- bossman-browser
- bossman-business
- bossman-jeff
- bossman-research
- bossman-verifier

Distillation acceptance:
- teacher baseline frozen;
- student evaluated on unseen tasks;
- independent verifier;
- no privacy/license violations;
- measurable cost/latency reduction;
- target >=95% of teacher verified quality before promotion unless task-specific policy sets a stricter gate.

The goal is not blind imitation. Frontier/giant models become occasional teachers; verified capability migrates into fast local specialists.

## 12. Streaming Giant Runtime
Reference project: `FareedKhan-dev/kimi-k3-in-c`.

Purpose: add a SLOW/IDLE giant-model tier for models too large to reside fully in RAM, using SSD streaming/expert caching where technically appropriate.

Do NOT replace realtime Qwen with a streamed giant.

Model Market tiers:
- RESIDENT LOCAL: Qwen/specialists, low latency;
- STREAMED GIANT: Kimi K3-class model, slow but strong/large, idle/hard-task tier;
- FREE CLOUD;
- BOUNDED FRONTIER CLOUD.

Preferred use:
`hard rare task / idle machine → streamed giant or frontier teacher → verifier → Distillation Foundry → local specialist`.

Resource Brain experiments may optimize expert cache/residency policy from real Bossman workloads. Any integration must benchmark actual tokens/sec, SSD bandwidth/endurance, RAM pressure, latency and owner-work interference on the AI Max before promotion.

The value of `kimi-k3-in-c` is both the possible runtime and its architecture ideas: memory budgeting, resident vs streamed weights, expert caching and graceful execution of giant MoE models under limited RAM.


# Additional 10/10 systems — 13 to 22

These are borrow-first candidates selected for Bossman 1.8 only when compatibility and practical usefulness are at least 9/10. Do not install all of them as parallel platforms. Prefer adapters, schemas, algorithms and bounded runtimes inside the existing Bossman control plane.

## 13. Internet Radar

Goal: Bossman keeps learning from the external world during idle time without aimless browsing.

Flow:
`sources/watchlists → crawl/search → change detection → dedupe → relevance → source verification → compare with current Bossman/business graph → opportunity → experiment queue`.

Canonical candidate:
- `unclecode/crawl4ai`
- Compatibility: 9.5/10
- Usefulness: 10/10
- Intended use: direct crawler/extractor component behind Bossman policy and source provenance.

Use for:
- AI/model/runtime releases;
- GitHub projects relevant to Bossman;
- AMD/Strix Halo updates;
- Fresh Vibes competitors/market;
- SwapMe operational/marketing research;
- business opportunity discovery.

Rules:
- no unlimited crawling;
- obey budgets/robots/provider limits;
- raw web text is untrusted evidence;
- no external text may expand authority;
- dedupe and source trust are required before memory/skill promotion.

## 14. Capability Market / MCP Discovery

Goal: if Bossman lacks a capability, it may discover a candidate tool instead of asking the owner to manually search for one.

Canonical candidate:
- `modelcontextprotocol/registry` — official MCP Registry
- Compatibility: 10/10
- Usefulness: 9.5/10
- Intended use: discovery API/catalog, not automatic trust.

Flow:
`missing capability → registry search → candidate metadata → security/license scan → isolated install/test → capability verifier → register or reject`.

Never:
`discover → auto-install → production authority`.

A discovered MCP server inherits no secrets, network scope, budget or approval authority unless existing Bossman policy explicitly grants it.

## 15. Counterfactual Replay + Chaos Lab

Goal: test new Bossman candidates against historical workflows and synthetic failures without spending real model money or repeating real side effects.

Canonical candidate:
- `mockagents/mockagents` (canonical project; legacy mirror existed under `anandtopu/mock-agents`)
- Compatibility: 9.5/10
- Usefulness: 9.5/10
- Intended use: deterministic offline model/tool/MCP mock + record/replay + fault injection.

Replay classes:
- historical task traces;
- timeouts;
- 429/rate limits;
- malformed JSON;
- truncated SSE/streaming;
- provider disconnect;
- tool failure;
- slow TTFT;
- restart mid-task.

External effects are simulated/reconciled, never replayed blindly.

## 16. Agent Self-Optimization

Goal: optimize prompts/configs/agent behavior from measured outcomes rather than manual prompt tweaking.

Canonical candidate:
- `gepa-ai/gepa`
- Compatibility: 9.5/10
- Usefulness: 10/10
- Intended use: evaluator-driven candidate generation/optimization inside Rollout Lab.

Initial scope:
- prompts;
- task decomposition templates;
- routing policies;
- context packing;
- retry/fallback configs.

Do not start with security policy, authority or money-related optimization.

Full RL systems that require CUDA-centric trainers remain optional later experiments; they are not 1.8 hard dependencies on the Windows AI Max.

## 17. External Agent Gym

Goal: prevent Bossman from inventing its own benchmark and then self-certifying.

Canonical candidate:
- `harbor-framework/harbor`
- Compatibility: 9.2/10
- Usefulness: 9.5/10
- Intended use: external eval harness for agents/models, standard environments and rollout generation.

Use:
`Internal BossmanBench + Harbor external benchmark`.

A promoted coding/agent candidate should pass both internal real-task regression and at least one suitable external benchmark where relevant.

Harbor may run locally or via a bounded external environment, but external paid environments require normal Bossman budget policy.

## 18. NPU Reflex Brain

Goal: use the AI Max XDNA2 NPU as an always-on low-power System-0 layer while keeping Radeon resources free for larger models/media.

Canonical candidate:
- `ROCm/FastFlowLM`
- Compatibility: 10/10 for Ryzen AI XDNA2/Strix Halo class hardware; owner-machine benchmark still required.
- Usefulness: 10/10
- Intended use: OpenAI-compatible NPU inference runtime for small/fast LLM/VLM/embedding/reflex tasks.

Possible workloads:
- intent classification;
- lightweight Jev pre-routing;
- memory tagging;
- relevance scoring for Internet Radar;
- spam/abuse filtering;
- small background agents;
- cheap embeddings/structured extraction where supported.

Architecture target:
`NPU = always-on reflex`
`GPU = main intelligence/media`
`CPU/RAM/SSD = data/giant streamed tier`
`cloud = overflow/frontier`.

Do not move a task to NPU merely to use the NPU; benchmark quality/latency first.

## 19. MTP / Speculative Local Accelerator

Goal: materially accelerate the main local Qwen path before buying more compute.

Canonical candidate:
- `olliehm/qwen-flash-next-windows`
- Compatibility: 10/10 target match for Windows + Strix Halo/Ryzen AI Max class workflows.
- Usefulness: 10/10
- Intended use: reference/build/benchmark path for Qwen Flash-Next and speculative/MTP-style acceleration.

Measure:
- tokens/sec;
- TTFT;
- context-length sensitivity;
- unified-memory footprint;
- correctness parity;
- tool/JSON reliability;
- stability after restart.

No acceleration is promoted if quality/tool reliability regresses.

## 20. Durable Agent Kernel

Goal: long-running Bossman tasks resume from durable checkpoints after process/Windows failure rather than restart from zero.

Canonical candidate:
- `dbos-inc/dbos-transact-py`
- Compatibility: 9.5/10
- Usefulness: 9.5/10
- Intended use: borrow/adapt durable-step/checkpoint/recovery semantics; selectively integrate where it improves existing Bossman task durability.

Bossman already has a task engine. Do NOT install DBOS as a second canonical task engine by default.

Borrow:
- step checkpointing;
- durable queues;
- crash recovery;
- workflow IDs/deduplication;
- rewind/retry semantics where useful.

## 21. Adaptive Business Experiment Engine

Goal: choose the next business experiment mathematically instead of brute-forcing all variants.

Canonical candidate:
- `facebook/Ax`
- Compatibility: 9.3/10
- Usefulness: 9.5/10
- Intended use: optimization engine for bounded business experiments, not business authority.

Optimize real objectives such as:
`verified gross profit - spend - owner time - risk`.

Possible parameters:
- offer;
- creative;
- CTA;
- audience;
- response template;
- landing variant;
- scheduling/time slot.

Use constraints, noisy outcomes, parallel suggestions and early stopping. External spend/publication remains inside existing approval/budget policy.

## 22. Standardized GenAI Telemetry

Goal: stop inventing ad-hoc schemas for every new model/tool/runtime.

Canonical candidate:
- `open-telemetry/semantic-conventions-genai`
- Compatibility: 10/10
- Usefulness: 9.5/10
- Intended use: canonical semantic vocabulary for GenAI model/agent/tool/MCP traces, metrics and evaluation events inside Bossman Observatory.

Bossman should map existing telemetry into this schema where sensible rather than deploy a separate telemetry platform solely for standards compliance.

Privacy rule:
raw participant prompts/responses remain opt-in/private; observability defaults to metadata, IDs, counts, timings, route/provider, costs, tool results and verifier outcomes without personal message content.


# Mandatory 1.8 media smoke — 5-second advertisement intro

After the 1.7 freeze and after the Kadr adapter is available, run one real owner-machine end-to-end media test before calling the Multimodal Brain foundation complete.

## Goal
Produce a **5-second vertical advertisement intro** for Fresh Vibes using Bossman orchestration.

Default target:
- duration: exactly 5.0 s ± 1 frame;
- format: 1080×1920, 9:16;
- fps: 30 unless the selected generation path requires another fixed rate;
- final container: MP4/H.264 or the current verified Studio/Kadr export preset;
- no paid external generation unless owner policy explicitly allows it.

## Workflow
1. Bossman receives: `create a 5-second premium Fresh Vibes intro ad`.
2. Jev classifies it as VIDEO_GENERATE + VIDEO_EDIT.
3. Studio generates or selects the base visual asset using the current best verified local/free route (prefer latest supported Wan path when available).
4. Verify the generated source asset exists and is readable.
5. Import the asset into Kadr through the adapter/MCP path.
6. Assemble a 5-second timeline.
7. Add a simple brand-safe intro treatment:
   - Fresh Vibes name/logo if approved asset exists;
   - short premium visual motion;
   - optional beat-aligned sound only from approved/licensed local library;
   - no fabricated medical claims.
8. Create a live preview.
9. Take at least one fresh preview snapshot.
10. Run visual verifier for:
   - readable branding;
   - no obvious generation artifacts;
   - no black frames;
   - no broken alpha/composition;
   - correct orientation;
   - correct duration.
11. Export final video.
12. Independently verify with ffprobe:
   - duration;
   - resolution;
   - fps;
   - codec/container;
   - audio presence/absence as expected.
13. Open/play the exported file on the owner machine and verify first/middle/last frames.
14. Record telemetry:
   - generation wall time;
   - Kadr edit/render wall time;
   - peak unified memory;
   - model/provider route;
   - local/free/paid calls;
   - total cost;
   - output bytes;
   - verifier verdict.
15. Deliver the preview/final artifact to the OWNER AI CONTROL CHANNEL, not Jeff.

## PASS
`KADR_5S_INTRO=PASS` only if:
- real generated/edited artifact exists;
- exact-ish 5-second duration is independently measured;
- output is playable;
- branding is readable;
- no black/corrupt frames;
- export survives reopen;
- no secrets/private paths are exposed;
- owner/control-plane permissions remain intact.

This test proves the chain:
`Bossman → Jev → Studio/Wan → Kadr → verifier → export`.

# Hardware / media research decisions

## ROCm / PyTorch experimental lane
ROCm/PyTorch is strategically useful for the Ryzen AI Max but must be introduced side-by-side after 1.5–1.7 freeze.

Keep working production paths:
- Ollama;
- llama.cpp;
- stable-diffusion.cpp.

Then benchmark ROCm/PyTorch for:
- Qwen;
- vision;
- distillation/training;
- media;
- ComfyUI/Wan where supported.

Do not remove a stable runtime until replacement wins real owner-machine benchmarks.

## Wan video
For local video, prefer evaluating the latest supported Wan line (currently Wan 2.2 rather than treating Wan 2.1 as the long-term target) through existing Studio/Model Market.

Video model selection is dynamic:
`quality + latency + memory + task type + cost`.

## FreeToken
FreeToken remains a strategic Resource Brain / MoE-runtime reference, but it is NOT a current production dependency until AMD/Windows/Strix Halo support proves >=9/10 compatibility on the owner machine.

Borrow its useful ideas:
- expert caching;
- hybrid residency;
- memory budgeting;
- semantic-aware cache;
- large-MoE scheduling.

# Borrow-first implementation matrix

| System | Canonical reference | Compatibility | Usefulness | Integration stance |
|---|---|---:|---:|---|
| Internet Radar | `unclecode/crawl4ai` | 9.5/10 | 10/10 | use behind Bossman policy |
| Capability Market | `modelcontextprotocol/registry` | 10/10 | 9.5/10 | registry API + security gate |
| Replay/Chaos | `mockagents/mockagents` | 9.5/10 | 9.5/10 | deterministic test harness |
| Agent optimization | `gepa-ai/gepa` | 9.5/10 | 10/10 | Rollout Lab optimizer |
| External Gym | `harbor-framework/harbor` | 9.2/10 | 9.5/10 | independent benchmark |
| NPU Reflex Brain | `ROCm/FastFlowLM` | 10/10 | 10/10 | owner-machine benchmark then adapter |
| Local accelerator | `olliehm/qwen-flash-next-windows` | 10/10 | 10/10 | benchmark/adopt winning path |
| Durable semantics | `dbos-inc/dbos-transact-py` | 9.5/10 | 9.5/10 | borrow/integrate, no second engine |
| Business optimizer | `facebook/Ax` | 9.3/10 | 9.5/10 | bounded optimizer adapter |
| GenAI telemetry | `open-telemetry/semantic-conventions-genai` | 10/10 | 9.5/10 | canonical telemetry semantics |
| AI video editor | `HelpFreedom/kadr` | 9.0/10 overall (higher after Windows adapter) | 9.8/10 | external MCP/adapter; keep GPL boundary |
| Cognitive-state UI | `Jakubantalik/thinking-orbs` | 10/10 | 9.5/10 | presentation layer driven by real Observatory state |

# Consolidated 1.8 architecture

Do not implement 22 independent subsystems. Group them into six coherent planes:

## A. Evolution Plane
Rollout Lab + Replay/Chaos + GEPA + Harbor + Distillation Foundry.

## B. Intelligence Market
Jev + Model Market + NPU reflex + resident Qwen + speculative/MTP acceleration + streamed giant + free/frontier cloud.

## C. World / Capability Plane
Internet Radar + MCP Capability Market + Computer World Model + Multimodal Brain.

## D. Durability / Memory Plane
Context Economy + Skill Compiler 2 + durable execution semantics + Operating Graph.

## E. Money Plane
Business Growth Engine + Revenue Lab + Ax-style adaptive experiments + attribution.

## F. Observatory
OpenTelemetry GenAI semantics + existing Bossman telemetry + business/owner-time/model-economics views.


# Business / Revenue Lab

Self-improvement must connect to real owner outcomes.

## Fresh Vibes
Optimize:
`lead → booking → completed treatment → verified gross profit`.
Study SEO/social/creative/offers/response speed/conversion and run bounded experiments with required owner approvals for external effects.

## SwapMe / exchange
Optimize operational efficiency, response speed, lead conversion, reporting, marketing, compliance assistance and profitability analytics. No self-improvement may grant financial-transfer/trading authority.

## Market/trading research
Use evidence → hypothesis → historical replay → verifier → skill. Research quality only; no autonomous live trading authority.

# Personal Autopilot
Track repeated owner workflows. Candidate automation requires replay/evidence before becoming a skill. Optimize `OWNER_HOURS_SAVED`.

# Idle evolution levels
- Level A zero-risk: context/retrieval/routing/cache/prompt candidates may auto-promote after strong gates + rollback.
- Level B internal code: isolated worktree → local/free coder → tests → independent verifier; promotion follows configured policy.
- Level C product behavior/UI: canary/Aster/owner evidence.
- Level D authority/security/money/credentials: never autonomous promotion; owner gate required.

# Night loop example
No foreground task → telemetry ranks opportunities → run bounded experiments → promote measurable winner, reject regression, hold owner-live candidate. Morning report should show experiments, promotions/rejections, cost, quality/latency/context change, owner time saved and business opportunities.

# Acceptance
1.8 is not PASS until:
- IDLE_EVOLUTION PASS
- AUTONOMOUS_HYPOTHESIS PASS
- ISOLATED_CANDIDATE PASS
- BASELINE_VS_CANDIDATE PASS
- INDEPENDENT_VERIFIER PASS
- AUTOMATIC_REJECT PASS
- AUTOMATIC_ROLLBACK PASS
- UNSEEN_TRANSFER PASS
- SKILL_COMPILER_2 PASS
- MODEL_MARKET PASS
- CONTEXT_ECONOMY PASS
- RESOURCE_BRAIN PASS
- BUSINESS_VALUE_TRACKING PASS
- DISTILLATION_FOUNDRY PASS
- STREAMING_GIANT_BENCHMARKED
- KADR_5S_INTRO PASS

Minimum: >=10 autonomous improvement cycles, >=3 promoted, 0 critical regressions.

Before/after benchmark uses the same 10 real task classes plus unseen variants. Aggregate quality must be >= baseline and improve at least two of latency, owner intervention, context, cost or verified success.

# Path to 2.0

## 1.8 — Evolution Engine
Bossman learns how to improve Bossman. External strong models remain fallback/teacher/reviewer.

## 1.9 — Autonomous Maintainer / Business Operator
Bossman maintains/develops itself, manages skill/provider lifecycle and runs controlled real revenue experiments. Frontier models cease being normal dependency.

## 2.0 — Autonomous Evolution OS
Foreground: SERVE OWNER.
Background: IMPROVE SYSTEM + RESEARCH INTERNET + DISCOVER BUSINESS OPPORTUNITIES + OPTIMIZE BUSINESSES.

Owner controls goals, budgets, permissions and irreversible actions. Bossman controls models, agents, tools, workflows, experiments, routing and learning.

# End-state principle
Use proven open-source components/patterns wherever possible. Build custom code only where Bossman-specific authority, unified memory, business logic, owner policy or differentiated learning requires it.

Do not start implementation until `READY_FOR_1_8=YES`.