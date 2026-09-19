# AiMaxBossman — PERFORMANCE OPTIMIZATION MASTER TZ

**Статус:** APPROVED PLAN / NOT IMPLEMENTED  
**Волна:** post-freeze optimization  
**Цель:** сделать Bossman заметно быстрее и плавнее без ослабления execution truth, verification, permissions, owner control, security и recovery.

## 0. Gate перед реализацией

Нельзя начинать широкую оптимизацию, пока текущая release-линия не имеет понятного frozen candidate SHA и нет открытых P0/P1 correctness blockers. До этого разрешены только measurement/instrumentation и очевидные low-risk fixes, если они нужны для приёмки.

## 1. Definition of Done

Optimization Wave завершена только если на одном и том же candidate SHA:

- cold/warm startup измерены до и после;
- UI/API p50/p95 улучшены или не ухудшены;
- idle CPU/RAM и process-tree HWM измерены;
- model residency/reload count измерен;
- Computer Use быстрее без потери stale/approval/postcondition гарантий;
- Video Studio остаётся интерактивным во время preview/export;
- full regression / relevant CI зелёные;
- нет новых P0/P1;
- no fake-green: SKIP/NOT_RUN/INSUFFICIENT_EVIDENCE не превращаются в PASS.

## 2. Baseline contract

Перед первым production change собрать baseline минимум из 5 повторов каждого сценария. Записывать median, p95 и worst. Каждая запись содержит:

`source_sha`, `tree_sha`, OS/build, Python, browser, FFmpeg, DB mode, model/provider, quantization, context, hardware, warm/cold flag, process tree, model resident state, test scenario id.

### Сценарии baseline

1. cold launch → UI_READY;
2. warm launch → UI_READY;
3. idle 5 minutes;
4. submit normal chat → first useful token;
5. safe computer-use 5-step mission;
6. Command Center navigation across primary pages;
7. Video preview;
8. Video export while interacting with UI;
9. Web Studio edit/save/reopen;
10. restart/recovery;
11. 3–5 concurrent safe tasks;
12. model switch and repeated requests.

## 3. Метрики и acceptance thresholds

| Metric | Acceptance |
|---|---|
| cold_start_to_ui_ready_ms | >=30% better vs frozen baseline |
| warm_start_to_ui_ready_ms | >=25% better |
| local_api_p95_ms | no regression; target <250 ms for non-model operations |
| ui_interaction_p95_ms | no long jank; target <100 ms where practical |
| ttfr_ms | >=20% better on same model/config or documented external limit |
| verified_action_p50_ms | >=20% better without weaker verification |
| observations_per_verified_action | improve only if stale/freshness tests remain identical |
| idle_cpu_median_pct | meaningful reduction; target <5% on owner reference machine if feasible |
| bossman_process_tree_ram_hwm_mb | baseline first; target >=15–20% reduction only where safe |
| model_reload_count | 0 avoidable reloads in 30-min scripted workload |
| preview_click_to_playable_ms | p95 <5 s on reference short 1080p fixture if hardware permits |
| interactive_api_p95_during_export_ms | <2x idle p95 |
| recovery_to_safe_state_ms | no regression >10% |

## 4. Workstream A — Startup / Time-to-UI

### A1. Instrument startup phases

Add monotonic timestamps around:

- config load;
- DB init/migrations/check;
- vault/identity readiness;
- API bind;
- Command Center static/UI readiness;
- optional capability discovery;
- browser runtime discovery;
- UIA observer initialization;
- FFmpeg/ffprobe probe;
- Fleet loops;
- model provider health/model load.

### A2. Separate readiness states

Introduce explicit readiness, without fake-ready:

- `CORE_READY` — policy/identity/DB/truth path ready;
- `UI_READY` — owner can navigate Command Center;
- `MODEL_READY` — selected model actually usable;
- `COMPUTER_USE_READY` — observer/operator dependencies confirmed;
- `MEDIA_READY` — FFmpeg/capabilities confirmed;
- `FLEET_READY` — relevant scheduler/worker state confirmed.

UI can become usable before optional subsystems, but must show truthful LOADING/WARN/BLOCKED state for them.

### A3. Lazy initialization candidates

Profile first, then defer only optional work:

- UIA/pywinauto/pyautogui/Pillow until Computer Use opens/runs;
- FFmpeg heavy probe until Video Studio first use;
- Fleet background discovery after UI_READY;
- cloud provider catalog refresh after UI is responsive;
- Studio-specific modules/assets by route.

### Regressions

- first request cannot race before policy/identity/DB readiness;
- opening Computer Use immediately after launch works or shows explicit readiness wait;
- no capability falsely displayed as READY;
- restart still restores durable state.

## 5. Workstream B — Command Center responsiveness

### B1. Polling inventory

Find every `setInterval`, recursive poll, task/job refresh loop and broad `ctx.refresh()`.
Record request/min, response bytes/min, scripting time, DOM nodes changed and page CPU.

### B2. Adaptive polling before event architecture

Low-risk precondition:

- active job/task: 0.5–1.5 s freshness;
- idle page: 3–10 s or no polling;
- hidden tab/page: back off strongly;
- Stop/Pause/approval endpoints remain immediate and never wait for polling.

### B3. Narrow updates

Where measured waste is high:

- update one task/job row rather than full page;
- use ETag/revision/since cursor for lists;
- event/delta channel may be added post-freeze with periodic reconciliation.

### B4. DOM/render discipline

- do not rebuild large Studio/page trees for one status counter;
- debounce high-frequency visual telemetry;
- virtualize only lists proven large enough to justify it;
- keep long logs/history outside synchronous render path.

## 6. Workstream C — Backend/API hot paths

### C1. HTTP client reuse

Do not implement generic connection pooling blindly: current GatewayClient already owns reusable `httpx.AsyncClient`. Audit every other provider/service path and fix only paths that create per-request clients.

### C2. Blocking I/O audit

Find blocking file/database/subprocess work inside async request handlers. Candidates:

- large journal/history rewrites;
- sync hash/ffprobe on request path;
- filesystem scans;
- JSON serialization of full histories;
- subprocess waits;
- external DNS/network calls.

Move only non-authoritative work off the critical loop. Truth/effect commits stay durable.

### C3. DB/query profiling

Collect top 20 local queries by cumulative time and p95. Verify query plans before adding indexes. Prioritize task/job/event/provider catalog lists.

### C4. Response size

Paginate and filter large collections server-side; return honest `total/returned/has_more`. Do not trade correctness for silent truncation.

## 7. Workstream D — Computer Use speed

### D1. Phase timing

Measure separately:

`observe → summarize/context → plan/model → policy → approval wait → dispatch → post-observe → verify → persist`.

### D2. Observation reuse

Already-safe post-action observation reuse remains. Further reduction only if:

- effect boundary gets fresh/applicable observation where required;
- changed foreground/ui_tree invalidates stale approval;
- completion proof still requires actual verified effect;
- ambiguous timeout never triggers blind retry.

### D3. UIA-first strategy

Test whether cheap structure/focus probe can avoid full screenshot when no visual information is needed. Full visual observation remains mandatory when semantics depend on pixels/image state.

### D4. Model-call efficiency

- compact structured planner schema;
- no duplicate system/context blocks;
- only relevant tools/skills;
- avoid model call for deterministic transitions/reconciliation;
- preserve error observability so 401/404/parse failures do not burn replan budget.

## 8. Workstream E — Local model / unified memory

### E1. Residency-aware routing

Measure model load time and resident memory. Prefer keeping the primary compatible model warm across bounded idle windows when memory headroom permits.

### E2. Tuning matrix

A/B test, never assume:

- keep_alive;
- max_loaded_models;
- parallel requests;
- context length;
- Flash Attention;
- KV cache type/quantization;
- prompt caching where supported.

Each matrix cell records TTFT, tokens/s, quality, RAM/unified-memory HWM, queue time and OOM.

### E3. Target Ryzen AI Max+ 395

On unified-memory hardware do not report model memory twice as «RAM + VRAM». Record:

- OS process working set/private bytes;
- runtime-reported model allocation;
- dedicated GPU memory if exposed;
- shared GPU memory/unified allocation;
- total committed memory;
- headroom before paging/OOM.

NPU is not assumed useful for arbitrary LLM inference. Use only if a supported workload/kernel shows end-to-end gain.

## 9. Workstream F — Video Studio

### F1. Resource classes

Classify:

- interactive preview;
- thumbnail/waveform/proxy;
- analysis/transcription;
- full export.

### F2. Bounded admission

Do not hardcode concurrency=1 globally without measurement. Create resource envelopes and prioritize interactive preview/owner controls over batch export.

### F3. Process priority / threads

Measure FFmpeg thread count, CPU/GPU/disk saturation and Windows process priority. Lower batch priority where safe; keep deterministic export correctness.

### F4. Preview path

Measure button click → queue → ffmpeg → file verify → browser metadata → first frame/playable. Production UI must choose/fallback to a decodable container/codec; a test-only API workaround is not acceptance.

## 10. Workstream G — Telemetry, journals, persistence

### G1. Split authoritative vs observational

**Authoritative:** journal intent, effect receipt, evidence, owner control, budget settlement — durable/fail-closed semantics stay.

**Observational:** performance telemetry, noncritical counters, UI analytics — may batch asynchronously if loss is visible and does not alter mission truth.

### G2. Write amplification

Measure append/rewrite costs by history size. Introduce append/batch/index only after reproducing scaling problem and preserving restart/tamper semantics.

## 11. Workstream H — Skills / prompt / context

- load 1–2 relevant skills, not whole library;
- cache immutable skill metadata/tool schemas;
- summarize old context only with provenance;
- keep policy, owner constraints and evidence obligations always present;
- measure prompt tokens, TTFT and held-out task quality before/after.

Acceptance: >=30% prompt-token reduction in long workflows only if verified success and safety do not decline.

## 12. Concurrency and scheduler

Post-freeze candidate:

- one shared resource scheduler aware of CPU, GPU/unified memory, model residency, media jobs and interactive priority;
- admission before starting expensive work;
- no oversubscription that causes paging and makes every task slower;
- owner Stop/Pause and verification work receive priority over batch work.

## 13. RAM / VRAM budget plan

Current exact Bossman footprint is **UNKNOWN** until measured. Required components to measure separately:

1. Core Python;
2. Gateway;
3. Command Center;
4. DB/container/Redis if running;
5. Chromium/Playwright/browser helpers;
6. UIA helper processes;
7. FFmpeg workers;
8. Fleet workers;
9. local model runtime/model/KV cache.

Report both idle and high-water mark per scenario. On the previous owner acceptance machine, limited system RAM makes contention especially visible; on the future 128 GB unified-memory target, the problem shifts from fit to bandwidth/residency/admission.

## 14. Rollout sequence

### Phase 0 — Measurement only
Instrumentation, baseline scripts, resource snapshot. No behavior optimization.

### Phase 1 — Low-risk quick wins
Startup lazy init after proof, adaptive idle polling, remove duplicate work, reuse already-safe connections, model keep-alive tuning, bounded media admission.

### Phase 2 — Medium
Narrow UI updates, query/index optimization, context/skill selection, non-authoritative telemetry batching.

### Phase 3 — Architectural
Unified resource scheduler, event-driven state propagation with reconciliation, residency-aware model router.

## 15. Required performance tests

- cold/warm launch benchmark;
- idle 5-min request/CPU/RAM test;
- API latency benchmark;
- Command Center navigation benchmark;
- owner Stop/Pause latency during busy work;
- Computer Use phase timing benchmark;
- stale/approval/effect truth negative regression unchanged;
- Video export + simultaneous UI responsiveness test;
- model residency/reload 30-min scenario;
- RAM/unified-memory pressure/concurrency test;
- recovery after crash during resource-heavy operation.

## 16. Rejection rules

Reject optimization if it:

- turns verified observation into heuristic completion;
- drops durable effect/journal commits;
- retries ambiguous irreversible effects;
- hides errors as empty state;
- disables tests/raises timeouts without root cause;
- relies on synthetic speedup as live claim;
- changes model/quant/context between baseline and candidate without labelling it;
- improves average but creates severe p95/p99 stalls;
- increases OOM/paging or owner-control latency.

## 17. Final artifacts after implementation

- `PERFORMANCE_BASELINE_<SHA>.json`
- `PERFORMANCE_CANDIDATE_<SHA>.json`
- `PERFORMANCE_DELTA_<BASE>_<CANDIDATE>.md`
- profiler traces/artifacts linked to tested SHA;
- updated audit index;
- README status from PLANNED → IN_PROGRESS → VERIFIED only when evidence exists.

**Current implementation status: NOT STARTED.**