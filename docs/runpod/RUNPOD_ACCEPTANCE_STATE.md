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
LAST_COMPLETED_PHASE=FIRST_REAL_BOSSMAN_TASK + MEDIUM_A/B + MEMORY_RESTART
NEXT_PHASE=ROUTER_REAL (SMALL/MEDIUM) → CONTEXT_STRESS → FILES/ARTIFACTS → MCP/BROWSER → SECURITY → RECOVERY
RUNPOD_PREFLIGHT=PASS
RUNPOD_READY=YES

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
