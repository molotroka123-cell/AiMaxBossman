# RUNPOD LOCAL HANDOFF (machine-readable)
CURRENT_SHA=4983eb7
LAST_VERIFIED_SHA=4983eb7
RUNPOD_GPU=RTX 5090 32GB (32607 MiB), driver 570.133.20, CUDA 12.8
VRAM=32607 MiB (peak used so far 6894 MiB with 7B Q4_K_M + 32k ctx)
MODEL_RUNTIME=Ollama 0.33.2 @127.0.0.1:11434, env OLLAMA_MODELS=/workspace/ollama
MODELS_DOWNLOADED=qwen2.5:7b (SMALL), qwen2.5:14b (MEDIUM)
MODEL_PATHS=/workspace/ollama (blobs)
MODEL_REVISIONS=qwen2.5:7b id=845dbda0ea48; qwen2.5:14b id=<see ollama list>
QUANTIZATIONS=Q4_K_M (default tags)
CACHE_PATHS=/workspace/hf-cache (HF_HOME, HUGGINGFACE_HUB_CACHE), /workspace/ollama
POSTGRES_STATE=PostgreSQL 16 running @127.0.0.1:5432, role/db bossman, DSN in /workspace/AiMaxBossman/.env (chmod 600, gitignored) + /etc/profile.d/bossman_env.sh
GATEWAY_STATE=WORKING (LIVE_PROVEN): BOSSMAN_GATEWAY_CONFIG=/workspace/artifacts/gateway.runpod.yaml, port 8877, aliases bossman-fast/bossman-smart/bossman-coder (все → qwen2.5:7b пока), client key в /workspace/artifacts/gw.key (600); core env BOSSMAN_GATEWAY_URL=http://127.0.0.1:8877/v1
ROUTER_STATE=static alias-per-agent (validated fail-fast); динамический роутер command-center/bcc — не тестирован
MEMORY_STATE=LIVE_PROVEN: PostgreSQL write → PG service restart + serve restart → restore (WM rows intact, новая задача done)
CONTEXT_STATE=not tested yet
SECURITY_STATE=P1 project_host ASK enforced (from repo history); cloud keys absent; sandbox/policy tests pending
LIVE_PROVEN_FUNCTIONS=gateway inference path (local provider), ollama backend, gateway auth (client key), model alias resolution
PARTIAL_FUNCTIONS=
GATED_FUNCTIONS=
HOST_NOT_APPLICABLE=Windows GUI computer control
LAST_COMPLETED_PHASE=FIRST_REAL_TASK + MEDIUM_A/B + MEMORY_RESTART
NEXT_PHASE=ROUTER_REAL (SMALL/MEDIUM) → CONTEXT_STRESS → FILES/ARTIFACTS → MCP/BROWSER → SECURITY → RECOVERY
KNOWN_FAILURES=see RUNPOD_FAILURES.md (GAP-001 RSS sampler, BUG-002 pgvector env, DISC-001 alias fail-fast)
KNOWN_WORKAROUNDS=long-lived background procs under ssh: setsid + </dev/null (stdin наследуется иначе); pgvector pre-created superuser'ом
FUTURE_LOCAL_BLOCKERS=none found yet
FUTURE_MEMORY_EXPANSION_ITEMS=none audited yet
BENCHMARK_PATHS=/workspace/benchmarks (ab_small_qwen25_7b.log JSON, pull logs)
ARTIFACT_PATHS=/workspace/artifacts (gateway_e2e_resp.json, gateway_e2e.log)
CLOUD_CALLS=0
SAFE_TO_CONTINUE_LOCAL=YES
SETUP_PENDING=none (all RUNPOD_READY gates green)
