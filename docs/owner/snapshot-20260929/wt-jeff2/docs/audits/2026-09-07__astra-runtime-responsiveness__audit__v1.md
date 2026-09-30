# AiMaxBossman — Runtime Responsiveness & Resource Efficiency Audit

**Auditor:** GPT-5.6 Sol / Astra lane  
**Date:** 2026-09-07  
**Status:** UNEXECUTED PERFORMANCE PLAN — evidence-backed where noted  
**Base SHA:** `ddea21112f89c978df50aae8948c5955d7dada2e`  
**Scope:** speed, responsiveness, startup, UI/API latency, computer-use loop, model residency, media responsiveness, memory pressure.

> This file intentionally separates **measured facts**, **code-evidenced risks**, and **hypotheses requiring measurement**. It is not a claim that the proposed gains have already been achieved.

## 1. Executive Summary

Bossman is now large enough that perceived speed is dominated less by raw model tokens/s and more by orchestration overhead: repeated observations, polling/re-render loops, startup fan-out, model load/reload, media jobs competing with interactive work, and synchronous durable writes on hot paths. Some meaningful speed work has already landed (for example observation reuse in the computer operator and better local-model path handling), so the next optimization pass should be surgical, benchmarked, and must not weaken execution truth, owner control, permissions, evidence or crash recovery.

**Current status:** 🟡 performance not yet freeze-certified.  
**Primary recommendation:** first create a trustworthy baseline harness, then apply low-risk changes only; postpone architectural scheduling changes until after V4/V5 freeze.

## 2. What is already real vs not yet real

### Already evidenced in repo / owner runs

- Computer operator observation reuse reduced redundant observation work in the operator loop; this is an actual optimization direction already present in the current line.
- Windows owner run exposed real configuration/dependency latency traps (`/v1`, observer dependencies, planner token/schema issues). These are reliability/perceived-speed problems, not only correctness problems.
- Command Center and editor suites already exercise real browser and FFmpeg paths; exact source SHA still matters.
- Gateway client code already keeps a reusable `httpx.AsyncClient`; any audit claiming "new client per request" must be verified against current code before implementing a fix.

### Not yet measured honestly

The following numbers **do not have authoritative current baseline evidence on the final candidate SHA** and must not be treated as facts yet:

- cold startup seconds;
- warm startup seconds;
- idle CPU percentage;
- idle RAM of the whole Bossman process tree;
- exact VRAM usage by Bossman itself;
- p50/p95 Command Center route/API latency;
- exact Video Studio preview latency;
- exact local-model tokens/s on the future Ryzen AI Max+ 395 machine;
- exact savings from lazy loading, polling changes, batching or KV-cache tuning.

## 3. Required baseline before optimization

Create one repeatable `performance baseline` command that records at minimum:

| Metric | Definition | Target after optimization |
|---|---|---|
| Cold start | process start → Command Center first usable interaction | -30% from measured baseline, no functionality loss |
| Warm start | restart with caches/models warm → first usable interaction | -25% |
| UI p50/p95 | navigation + core interactive API calls | p95 < 250 ms for local non-model operations |
| TTFR | task submit → first useful streamed model content | -20% vs baseline on same model/config |
| Verified action latency | observe → plan → dispatch → verify | -20% p50 without fewer verification gates |
| Observations/action | total expensive observations / verified mutating action | <= current proven ratio, improve only if evidence-safe |
| Idle CPU | 5-minute median after startup, no active tasks | < 5% on reference owner machine if feasible |
| Core+CC RAM HWM | process-tree private/working-set high-water mark without model | baseline first; target -20% if hot-path evidence supports it |
| Model reload count | loads/unloads per 30-min scripted session | 0 unnecessary reloads |
| Video preview latency | click Preview → playable frame | p95 < 5 s for reference 1080p short fixture on reference machine |
| UI responsiveness during export | p95 interactive API latency while export runs | < 2× idle p95 |
| Crash/recovery overhead | resume-to-safe-state time | no regression >10% |

Reference runs must record SHA/tree, OS, Python, browser, FFmpeg, model, quantization, context, provider, hardware and whether the run is Windows owner / CI Linux / synthetic.

## 4. High-priority hot paths

### P1 — Command Center refresh / polling / repaint

**Risk:** background polling and broad page refreshes can consume CPU and trigger unnecessary DOM rebuilding, especially with task/job lists and Studio status polling.

**What to inspect:**
- `command-center/ui/` page timers, `setInterval`, recursive poll loops;
- API endpoints returning full collections where delta/event updates would suffice;
- components/pages that call global `ctx.refresh()` after narrow state changes.

**Do not assume WebSocket is automatically faster.** First profile request rate, response size, DOM mutation count and scripting time. A simple change from 500 ms to 1500–3000 ms adaptive polling may be lower risk before freeze.

**Acceptance:** background request count and renderer CPU fall >=30% while task/status freshness remains <=1.5 s for active jobs and owner Stop/Pause remains immediate.

### P1 — Startup critical path

**Risk:** import-time initialization, discovery, capability probes, browser/media checks and optional subsystem initialization can delay first-useful UI.

**Measure first:** Python import profile + timestamped startup phases. Identify >100 ms phases. Lazy-load only optional modules that are proven off the critical path.

**Candidates:**
- Windows observer dependencies only on computer-use activation;
- media/FFmpeg capability probing only when Video Studio opens;
- Fleet discovery loops after Command Center is usable;
- nonessential catalog sync after UI becomes responsive.

**Acceptance:** cold start -30% and warm start -25%; no missing capability state, no race where first action occurs before policy/identity/DB readiness.

### P1 — Model residency and unnecessary reloads

**Risk:** model load/reload dwarfs framework overhead. The scheduler must know whether keeping one model resident is better than switching models for small tasks.

**Measure:** per model load ms, resident memory, context/KV allocation, reload count, request queue time, tokens/s and TTFT. Do not import generic Ollama tuning values as universal defaults.

**Quick win:** preserve the selected primary local model across a bounded idle window and route compatible tasks to it when quality gates allow. Use hardware-aware admission and explicit memory headroom.

**Acceptance:** scripted 30-minute mixed workload has zero avoidable reloads and no OOM / degraded verification rate.

### P1 — Computer-use observation pipeline

**Risk:** screenshot/UIA capture, summarization, model planning and post-action verification dominate interactive automation latency.

**Already done:** avoid re-observing when a verified post-action observation is safely reusable.

**Next:** instrument every phase separately before changing logic. Consider UIA-first delta checks for unchanged regions and full screenshot only when needed; preserve fresh-observation requirements at effect boundaries.

**Acceptance:** >=20% lower p50 verified-action latency with identical stale-state/approval/postcondition tests and no increase in false completion.

### P1 — Video Studio interactive vs batch resource contention

**Risk:** FFmpeg preview/export/analysis can saturate CPU/GPU/disk and make UI/API feel frozen.

**Plan:** classify media jobs into interactive preview vs batch export; add bounded concurrency/admission and lower batch priority where OS allows. Do not simply force concurrency=1 globally; measure CPU/GPU/disk utilization and queue wait.

**Acceptance:** while a long export runs, Command Center p95 local API latency <2× idle, owner Stop/Pause remains responsive, preview jobs are not starved, export correctness unchanged.

## 5. Medium changes after baseline

### P2 — Narrow event/delta updates
Replace full-list refreshes with delta/event updates where profiling proves DOM/network waste. Keep periodic reconciliation to recover missed events.

### P2 — Database query/index audit
Profile top 20 slow local queries in Command Center/task/job/event lists. Add indexes only from query-plan evidence. Avoid write-amplifying indexes on journal/event tables without measurement.

### P2 — Telemetry batching outside truth boundary
Telemetry may batch asynchronously only if mission truth/evidence durability remains synchronous where required. Benchmark telemetry separately; never move authoritative journal commits onto a lossy queue.

### P2 — Skill/context selection
Load only task-relevant skills/context. Measure prompt tokens and quality before/after. Do not reduce evidence context or policy text. Target >=30% prompt-token reduction in long sessions with no held-out quality loss.

### P2 — Static asset / page-module loading
Lazy-load heavy Studio modules and optional page assets after route selection if bundle/network profiling shows meaningful startup impact. Avoid framework migration before freeze.

## 6. Post-freeze architectural optimizations

- event-driven state propagation with reconciliation instead of many independent pollers;
- unified scheduler for CPU/GPU/media/model contention using measured resource envelopes;
- local model router with residency cost in the objective function;
- cache hierarchy for immutable tool schemas, skill summaries and stable system context;
- zero-copy / shared-memory handling only after profiling proves serialization/copy cost is material;
- NPU use only for workloads with supported kernels and measured end-to-end benefit; do not assume NPU acceleration for arbitrary LLM inference.

## 7. RAM / VRAM accounting — what we can say now

### Bossman framework without local model

There is currently **no authoritative exact measurement** in the reviewed evidence for total Bossman process-tree RAM on the final candidate. Claims such as "3–4 GB idle" in other audits should be treated as estimates until the new baseline harness measures working set/private bytes across Core, Gateway, Command Center, browser helpers, DB/container and optional workers.

### Local model memory

Model memory is not the same as Bossman framework memory. On a unified-memory Ryzen AI Max+ platform, reporting separate "RAM" and "VRAM" can be misleading because GPU allocations consume the shared physical memory pool. Report:

1. system used memory;
2. process-tree working set/private bytes;
3. model weights resident bytes;
4. KV/cache bytes;
5. GPU/unified allocation as exposed by the runtime;
6. remaining admission headroom.

For the current Windows owner laptop with discrete RTX 4060 8 GB, GPU VRAM and system RAM are separate and should be measured separately. For the planned Ryzen AI Max+ 395 machine, use unified-memory high-water mark and admission headroom instead of pretending a fixed VRAM partition is independent RAM.

## 8. What I reject from generic performance advice

Do **not** implement these without current-code evidence:

- "Gateway creates a new HTTP client each request" — current client already owns a reusable `AsyncClient`; verify real provider paths before changing.
- "Bossman definitely uses 15–20% idle CPU" — unmeasured on final SHA.
- "Lazy imports will save 2–3 GB" — plausible only after import/object residency profiling.
- fixed Ollama values (`num_parallel`, KV q8, context, loaded models) as universal defaults — must be benchmarked per model/hardware/workload.
- moving authoritative journal/evidence writes to an eventually consistent queue — unacceptable if it weakens crash truth.
- replacing polling with WebSocket everywhere — may add complexity without reducing actual render cost.
- using smaller/faster model automatically — speed gain is irrelevant if verified task success falls.

## 9. Implementation order after freeze blockers are closed

1. **Instrumentation only** — startup spans, API/UI timings, process-tree memory, job queue, model load/reload, observation phases.
2. **Quick wins** — adaptive polling, defer optional probes, media admission, resident-model policy; one change at a time.
3. **Replay owner workload** — same 10–30 tasks, compare verified completion, p50/p95, interventions, memory.
4. **Medium optimizations** — query/index, delta UI, context trimming, cache hierarchy.
5. **Architecture** — unified scheduler/event model only if metrics justify it.

## 10. Freeze rule

Performance work starts only after remaining correctness/security freeze blockers are closed or the change is purely observational and cannot change behavior. Every optimization must prove:

- no P0/P1 correctness regression;
- no weaker permissions/owner control;
- no weaker verification/evidence/recovery;
- same or better verified task success;
- measured speed/resource win on the same workload;
- rollback is trivial.

**Final status:** `UNEXECUTED_PLAN`. This audit should be merged with the other independent audits into one Epoch 6 optimization specification after the current V4/V5 freeze line is stable.