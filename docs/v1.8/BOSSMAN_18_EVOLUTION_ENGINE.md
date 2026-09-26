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

## 9. Agent Society 2.0
Agents become measured specialists. Track speciality, verified quality, context efficiency, latency, tool success, skills, cost and failures. Task Compiler assembles temporary teams based on measured history.
Borrow-first: AutoGen team/bench concepts; CAMEL society/critic concepts.

## 10. Bossman Observatory
One justified major UI surface: a brain X-ray.
Show `owner task → compiler → Jev → models → tools → verifier → outcome → learned delta`.
Per-model: calls, verified success, tokens/context, p50/p95 latency, retries, tool success, data processed, cost and fallback reason.
Also measure owner time saved, frontier calls avoided, business impact and before/after improvements.
Borrow-first: Langfuse and Arize Phoenix schemas/UX. Prefer extending Bossman telemetry to deploying redundant stacks.

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
