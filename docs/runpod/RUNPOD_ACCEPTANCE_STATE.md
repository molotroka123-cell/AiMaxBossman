# RUNPOD ACCEPTANCE STATE
UPDATED=2026-09-01T18:20Z
REMOTE_SHA_SOURCE_OF_TRUTH=yes
CURRENT_SHA=4983eb7
GPU=RTX_5090_32GB
CUDA=12.8
TORCH=2.8.0+cu128
RAM_GIB=187.8 (no cgroup caps found: cpu.max/memory.max absent)
CPU_VCPU=32
STACK=PostgreSQL 16 @5432 (bossman/bossman, .env 600) + Redis PONG + Ollama 0.33.2 @11434 (OLLAMA_MODELS=/workspace/ollama)
CLOUD_KEYS_PRESENT=NONE
LAST_COMPLETED_PHASE=FILES_ARTIFACTS_MCP
NEXT_PHASE=BROWSER_REAL → SECURITY → RECOVERY → SCHEDULER → CONCURRENCY/LONG-RUN
RUNPOD_PREFLIGHT=PASS
RUNPOD_READY=YES

## Files/artifacts/MCP REAL
- file_intel: csv/json/md/zip реально распарсены (kind, sections, sha256 в render); corrupted zip → честная ошибка; unsupported → honest unavailable; file_intel тесты 11 passed
- ARTIFACT ENGINE: LIVE_PROVEN — register_artifact x2 → версии 1→2, ids distinct, reopen hash match, creator_task записан, registry_rows=2
- MCP: 15/15 PASSED на Linux (реальный SDK-путь FastMCP через stdio; mcp пакет доставлен)
- LARGE tier A/B (qwen2.5:32b): Direct 15/18 = Bossman 15/18, Retention 1.0, VRAM 27524 MiB; coding 0/3 в ОБОИХ руках (32b вербозность ломает exact-match; 7b/14b дают 3/3 → 14b sweet spot для coder)
- Bandit -lll: 0 findings; secret scan: только 2 известные фейковые тестовые константы

## Context stress REAL (7b, DIRECT vs BOSSMAN, needles P0-start/supplier-end/contradiction/security)
- 8k: обе руки 4/4 (P0+supplier+contradiction+security) — задачи #4
- 16k: обе руки 4/4 — задача #5
- 32k: обе руки 3/4 — P0-игла В НАЧАЛЕ теряется ОДИНАКОВО (DIRECT и BOSSMAN; модель честно ответила «P0-код отсутствует»); end-игла и противоречие сохранены; security-констрейнт не нарушен
- ВЕРДИКТ: контекст-путь Bossman БЕЗ деградации vs direct на всех тирах (scores идентичны); 32k start-loss = потолок 7b (на 14b long_context в A/B = 1.0)
- NOTE: execve limit ~128KB — 32k текст нельзя передать argv CLI; использован продакшн-путь db.insert+runner.enqueue (то же, что CLI)
- Evidence: /workspace/artifacts/stress_direct.json, stress_bossman.json, stress_bossman_32k.json; tasks 4-6

## Router REAL (a146d0b-era evidence)
- bcc/v2/model_router.route() driven with LIVE-MEASURED inputs: tps/latency from ollama generate (7b 420.7 tok/s / 146ms; 14b 263.2 tok/s / 52ms warm), VRAM peaks from our A/B (6894/15178 MiB), success rates from our A/B (7b 0.667/reasoning 0.0; 14b 1.0)
- 6 сценариев корректны: A simple→14b (оправданная эскалация: measured success 1.0 vs 0.667); B reasoning→14b; C memory_cap 12GB→7b (14b честно отброшен по реальному VRAM); D/E cloud denied/allowed→локальная 14b (облако не навязывается); F falsified tools→7b (проба важнее рекламы каталога)
- Status: INTEGRATION_PROVEN (production logic + live inputs; BCC engine HTTP path pending)
- Evidence: /workspace/benchmarks/router_real.json

## Phase results so far
- FIRST REAL BOSSMAN TASK: LIVE_PROVEN — `bossman task` → runner → analyst → gateway (bossman-smart) → ollama qwen2.5:7b → result "OK", tasks 1-3 done, WM rows persisted, warm 64ms
- MEMORY RESTART: LIVE_PROVEN — PostgreSQL service restart + serve process restart: pre-restart WM rows intact, new task done after restart (write→restart→restore)
- MEDIUM A/B (qwen2.5:14b Q4_K_M): Direct 18/18 (1.0) = Bossman 18/18 (1.0); ALL classes 1.0 including reasoning и long_context (на 7b были 0/3 — потолок модели подтверждён); Retention 1.0; cloud 0; VRAM 15178 MiB
- Gateway E2E: LIVE_PROVEN — POST /v1/chat/completions alias bossman-fast → ollama qwen2.5:7b → "OK", HTTP 200, log outcome=ok fallbacks=0. Evidence /workspace/artifacts/gateway_e2e_resp.json + gateway_e2e.log
- CLOUD_CALLS=0: proven structurally (zero cloud keys in env) + gateway log fallbacks=0 backend=ollama
- A/B SMALL (qwen2.5:7b Q4_K_M, digest 845dbda0ea48, 6 classes x 3): Direct 12/18 (0.667) = Bossman 12/18 (0.667); IntelligenceRetention=1.0; simple/coding/tool_use/memory 3/3 both arms; reasoning 0/3, long_context 0/3 BOTH arms (model capability ceiling, identical); retries=0; cloud_calls=0; peak VRAM 6894 MiB. GATE PASS
- Fail-fast agent validation: run_task падает если алиас агента не разрешается в Gateway — REAL behavior, config fix (bossman-coder alias добавлен)
- identical per-class outcomes AND token counts vs local Windows campaign (same model digest) — cross-host reproducibility signal
- Metrics: /workspace/benchmarks/ab_small_qwen25_7b.log, ab_medium_qwen25_14b.log (JSON)

## Known metric gaps (not product bugs)
- peak_ollama_rss_mib=50.4: A/B RSS sampler does not see the ollama runner subprocess holding the model; honest footprint metric = VRAM (6894 MiB). Recorded in RUNPOD_FAILURES.md
- cold-load TTFT (first request after pull) ~10.6s; warm TTFT ~0.04s
- qwen2.5:7b kept loaded in VRAM after A/B (ollama keep_alive); unload before tier switch per GPU policy
