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
LAST_COMPLETED_PHASE=5_SMALL_MODEL_PATH (Phase 2 guide) + DIRECT_VS_BOSSMAN_SMALL
NEXT_PHASE=6_TIERS_MEDIUM_LARGE, 4_FUNCTION_MATRIX
RUNPOD_PREFLIGHT=PASS
RUNPOD_READY=YES

## Phase results so far
- Gateway E2E: LIVE_PROVEN — POST /v1/chat/completions alias bossman-fast → ollama qwen2.5:7b → "OK", HTTP 200, log outcome=ok fallbacks=0. Evidence /workspace/artifacts/gateway_e2e_resp.json + gateway_e2e.log
- CLOUD_CALLS=0: proven structurally (zero cloud keys in env) + gateway log fallbacks=0 backend=ollama
- A/B SMALL (qwen2.5:7b Q4_K_M, digest 845dbda0ea48, 6 classes x 3): Direct 12/18 (0.667) = Bossman 12/18 (0.667); IntelligenceRetention=1.0; simple/coding/tool_use/memory 3/3 both arms; reasoning 0/3, long_context 0/3 BOTH arms (model capability ceiling, identical); retries=0; cloud_calls=0; peak VRAM 6894 MiB. GATE PASS: Bossman >= Direct - 1pp, Retention >= 0.99
- identical per-class outcomes AND token counts vs local Windows campaign (same model digest) — cross-host reproducibility signal
- Metrics file: /workspace/benchmarks/ab_small_qwen25_7b.log (JSON)

## Known metric gaps (not product bugs)
- peak_ollama_rss_mib=50.4: A/B RSS sampler does not see the ollama runner subprocess holding the model; honest footprint metric = VRAM (6894 MiB). Recorded in RUNPOD_FAILURES.md
- cold-load TTFT (first request after pull) ~10.6s; warm TTFT ~0.04s
- qwen2.5:7b kept loaded in VRAM after A/B (ollama keep_alive); unload before tier switch per GPU policy
