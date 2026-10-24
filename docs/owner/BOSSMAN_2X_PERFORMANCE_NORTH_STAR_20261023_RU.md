# BOSSMAN NORTH STAR — PERFORMANCE 2X: ARGON/CODEX OWNER PLAN

Owner request: 22 Oct 2026. **Read before coding on 23 Oct 2026.** Canonical repo: molotroka123-cell/AiMaxBossman, main; hardware AMD Ryzen AI Max+ 395, Radeon 8060S, 128 GB unified memory, Windows 11.

GOAL: **>=2.00x median verified end-to-end speedup** in a pre-registered coding/agent workload on the owner's actual PC, same output quality and permissions. This is a TARGET, not a measured gain or promise that ALL workloads double. Never claim achieved without paired baseline/candidate runs, exact installed SHA, logs and independent review. This document is a design plan only; behavioural upgrades are NOT installed.

## Repo audit: known architecture and likely hotspots, not measured bottlenecks

- Mobile: apps/bossman-mobile/bossmobile/api.py → command-center/bcc/features/agent_opencode.py → request auth/session, _session_detail, build_model_router_catalog, model selection, /api/tasks/preflight, /api/tasks, _start, chat streaming. These serial stages must be TIMED before optimizing.
- agent_opencode.py::send_agent_message currently resolves session_detail and model router catalog each message. Catalog/readiness/key-vault calls may be measurable overhead; do not assume they are slow. Cache ONLY safe stable metadata by revision/short TTL, never authorization or API keys.
- agent_opencode.py::list_agent_sessions scans sessions and historic turns; check O(N) / N+1 costs when UI has many old chats. Profile before adding indexes or pagination.
- agent_opencode.py::list_agent_tools generates descriptions and schemas for all tools; filter only allowed relevant schema for each model prompt, without removing enforcement from policy engine.
- tools/resource_governor/governor.py already manages Ollama keep_alive, Jeff keep/hold, file preread, RAM limits and process prioritisation: OPTIMIZE EXISTING, DO NOT BUILD A SECOND GOVERNOR. Avoid GPU memory thrash between 70B coder, Gemma, Jeff and Wan.
- command-center/bcc/features/coding_tasks.py already caches some sidecar handshakes and runs coding verification/worktrees. Investigate repeated process startup and LLM tool loops, without skipping verifier, approval or rollback.
- Earlier owner's machine benchmark (docs/archive/2026-10/owner/BOSSMAN_DAY_20261020_RU.md) separately measured Qwen3.8-27B MTP up to ~2.09x DECODE versus baseline. That does NOT imply installed Bossman 2x end-to-end. Other file: docs/owner/MP21_LOCAL_MODELS_AUTONOMY_20261011.md.

## Metric contract: what EXACTLY means 2x

T_total = queue_wait + model_load + prefill + decode + tools + verification + UI_delivery + retries. For a completed task, measure from user pressing Send on iPhone to independently verified final result and artifact visible in Mobile Pult. In addition track TTFT, p50/p95/p99, quality, prompt/decode tokens/sec, model swap time, context tokens, cost USD, RAM peak, GPU utilization, failed work, number of agent calls, tool schema errors and interventions.

Speedup = median(paired_baseline_ms) / median(paired_candidate_ms). Need >=2.00 for core coding/agent suite AND quality noninferiority. Also report p95 regression, throughput useful verified completions/hour, per-lane speedups. Do not pool media generation and chat into one misleading statistic.

PRE-REGISTER TEST MATRIX before changes: 30 short local chat+safe tool tasks; 30 multi-tool 4–8-step tasks; 20 code edit+pytest+independent review+diff tasks; 20 long-memory+skill tasks; 10 Windows computer-use tasks; 5 image/video generation plus 5 Jeff-under-media coexistence tests (separate lanes). Fix prompts, test fixtures, seed and decoding settings where supported, and preserve same baseline model/quant/hardware for direct kernel A/B. Repeat each task >=3 runs randomized A/B order, warm and cold measured separately. Keep errors/retries in statistics; store raw aggregate evidence in docs/owner/performance-2x/runs/. Pre-register quality threshold >=98% of baseline pass rate with zero new critical security violations (small-sample safety scenarios must ALL pass).

Critical Amdahl example (ILLUSTRATION NOT MEASUREMENT): if 60% of elapsed time is decode, a 2.09x decode boost alone makes E2E only 1/(0.4+0.6/2.09)=1.46x. Halving the other 40% too yields 1/(0.2+0.6/2.09)=2.05x. Hence need BOTH faster inference AND reduced orchestration/context overhead if actual profile resembles this hypothetical split.

## P0: TRACE FIRST (highest priority)

Add optional sanitized OpenTelemetry spans or integrate existing tracing at: mobile.request, session.auth, session.load, model.catalog, model.route, preflight, task.create, queue.wait, llm.load, llm.prefill, llm.decode, tool.dispatch, tool.run, verify, memory.retrieve, receipt.persist, ws.deliver. Propagate trace ID into tools/background workers/restarts. Use monotonic clock; never export prompts, credentials, private file paths/window titles or Telegram content. Set sampling, benchmark overhead <3%; replay correctness independent of telemetry. Output top 10 time-consuming spans per workload. If model inference is 85% of time, do not spend a day optimizing tiny DB calls.
Reference: https://opentelemetry.io/docs/languages/python/instrumentation/
FastAPI instrumentation: https://opentelemetry-python-contrib.readthedocs.io/en/latest/instrumentation/fastapi/fastapi.html

## P1: VULKAN + MTP A/B ON REAL 8060S

Experiment with existing matching GGUF model bundled with a compatible MTP head using pinned llama.cpp Vulkan/llama-server on secondary localhost port. Compare SAME model and quantization, baseline llama server MTP off vs draft-mtp at --spec-draft-n-max 1,2,3. If current production uses Ollama, separately compare its current backend; no apples-to-oranges attribution. Tests: short and 4k/8k contexts, 128/256/512 output tokens, Python coding eval and tool-call/JSON schema reliability. Measure prefill AND decode speed, MTP accepted draft tokens, TTFT, warm-up and peak shared RAM. Accept only faster correct output with stability; pin binary/model/driver hashes and keep rollback. Vulkan vs ROCm only if exact Windows runtime compatible. No guaranteed 2x.

Upstream verified feature: https://github.com/ggml-org/llama.cpp/blob/master/docs/speculative.md

## P2: PROMPT CACHE + PREFILL + CONTEXT DISCIPLINE

llama.cpp supports prompt caching, reusable slots, KV save/restore and metrics; evaluate --cache-prompt, --cache-reuse, batch/ubatch sizes, flash attention **where supported**. Preserve prompt prefix stability across turns, isolate confidential contexts by owner/session, invalidate on model version, tool schema, policy, or secret-scope change. Reuse OpenCode's version-matched compaction system rather than implementing a second loss-prone summariser; keep task decisions/permissions/approval status verbatim. Test old-context/critical-instruction retention and tool correctness.

Upstream: https://github.com/ggml-org/llama.cpp/blob/master/tools/server/README.md
OpenCode compaction: https://opencode.ai/v2/docs/compaction
AMD long-prompt performance example, NOT same Bossman benchmark: https://www.amd.com/en/developer/resources/technical-articles/2026/how-to-run-a-one-trillion-parameter-llm-locally-an-amd.html

## P3: CUT WASTED AGENT TURNS (potentially more important than GPU speed)

- For a verified deterministic skill, route straight from intent classification to schema-validated tool, not 70B to re-explain a fixed known workflow. Unknown/ambiguous tasks go to existing lightweight decider, then verified coder when confidence insufficient.
- Only provide model the allowed and task-relevant tool schemas; policy checks remain server-side and NEVER cached.
- Retrieval: bounded relevant memory, deduplicate snippets, compress huge tool output while preserving audit pointer and task state. Never drop owner constraints.
- Avoid repeated model catalogs, key-vault probes and repeated shell process handshakes if traces prove expensive. TTL/version-key stable metadata, revalidate provider readiness and permission on every mutating call.
- No output/result caching for dynamic tenders, financial rates, private-changing data or side-effecting tools. All idempotency protections remain. Benchmark exact content correctness on unseen tasks, not just lower tokens.

## P4: API/WORKER CRITICAL PATH

Investigate redundant session read, catalog resolve, preflight, task create and start round-trips in agent_opencode. Safe independent READS may run concurrently; task creation, authorization and side effects MUST remain atomic and ordered. Profile N+1 in list sessions and repeated DB queries, then optimize only measured bottlenecks. Consider embedded OpenCode SDK if and only if pinned installed version supports it and measured HTTP/process overhead justifies migration. Avoid adding parallel orchestration or weakening task identity/STOP/reconnect.

Reference OpenCode embedded SDK (verify installed V1 vs V2 API; NOT drop-in replacement): https://dev.opencode.ai/v2/docs/build/sdk/

## P5: UMA SCHEDULER / OLLAMA LIFECYCLE

Existing Governor already uses keep_alive, model prewarming and Jeff coexistence. Tune after capturing model load, cache hits, swap, memory usage and GPU/CPU occupancy. Keep a measured free RAM floor and owner Jeff policy; don't preload heavyweight 70B during media jobs. Use adaptive model-residency by workload lane; stop experiments immediately on page thrash, media failure, Jeff latency regression or runaway memory. On Windows do not count UMA as extra discrete VRAM. Reference Ollama official keep_alive semantics: https://github.com/ollama/ollama/blob/main/docs/faq.mdx

## OSS decision table (upstream available ≠ installed or proven)

1. llama.cpp draft-mtp — real feature, highest-priority local inference PoC, Bossman deployed status NOT_RUN.
2. llama.cpp prompt cache/slots/metrics — real upstream interfaces, Bossman impact NOT_RUN.
3. OpenCode V2 context compaction and embedded SDK — real upstream, compatibility with installed build NOT_RUN.
4. OpenTelemetry Python / FastAPI — real observability, candidate for opt-in timing, overhead NOT_RUN.
5. Ollama keep_alive — already implemented in Bossman Governor, tune existing; no duplicate component.
Fresh other releases audited at docs/owner/CHECKPOINT_20261022_02_FRESH_OSS_RU.md. Reject innovations that cost complexity without demonstrated end-to-end gain, and check licenses, dependency vulnerabilities, OS/Vulkan compatibility.

## ARGON TOMORROW — CHRONOLOGICAL CHECKPOINTS

CP-PERF-00 baseline: fetch exact main and installed SHA; verify last CI; baseline all workloads; push timings+quality+hardware.
CP-PERF-01 trace: RED test then GREEN telemetry; confirm <3% overhead and no prompt/secret export.
CP-PERF-02 inference: controlled MTP/cache/batch sweeps; commit failures too; compare same model/quant, no live daemon replacement without approval.
CP-PERF-03 control: context minimization, deterministic skill fast path and catalog cache only if trace warrants; benchmark tool/schema correctness.
CP-PERF-04 system: reduce verified API/DB/worker overhead, minimize model churn; no unchecked asynchronous side effects.
CP-PERF-05 integrated: exact SHA CI; owner iPhone+Windows 15-min smoke; STOP, Jeff outbound guard, media and North Star self-written skill.
CP-PERF-06 result: final before/after metrics, CI URLs, logs, rollbacks, achieved ratio, remaining bottlenecks and PASS only if >=2.00x AND quality guard met.

Push each CP as an evidence-bearing commit to dedicated integration branch. Owner authorizes publishing THIS documentation to main, not merging new behavioral optimizations directly into main. No RunPod or external paid calls without separate explicit owner approval, no self-escalation through AUTO, no fake tests and no weakening independent verification. If 2x not reached, print measured value and next bottleneck; no false victory.

READ FIRST tomorrow: AGENTS.md, this file, docs/owner/runs/north-star-20261023/FINAL_ACCEPTANCE_RU.md, README.md. Do real baseline BEFORE rewriting architecture.
