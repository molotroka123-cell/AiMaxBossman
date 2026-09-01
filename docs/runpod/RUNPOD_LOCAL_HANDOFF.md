# RUNPOD LOCAL HANDOFF (machine-readable)
CURRENT_SHA=см. последний push (docs/runpod)
LAST_VERIFIED_SHA=60f328d+ (этот цикл документов)
RUNPOD_GPU=RTX 5090 32GB (32607 MiB), driver 570.133.20, CUDA 12.8
VRAM=32607 MiB; пики: 6894 (7B) / 15178 (14B) / 27524 (32B) / 2 MiB после auto-unload
MODEL_RUNTIME=Ollama 0.33.2 @127.0.0.1:11434, env OLLAMA_MODELS=/workspace/ollama
MODELS_DOWNLOADED=qwen2.5:7b, qwen2.5:14b, qwen2.5:32b (все Q4_K_M)
MODEL_PATHS=/workspace/ollama (blobs)
MODEL_REVISIONS=7b:845dbda0ea48; 14b:7cdf5a0187d5; 32b:9f13ba1299af
QUANTIZATIONS=Q4_K_M (default tags)
CACHE_PATHS=/workspace/hf-cache (HF_HOME, HUGGINGFACE_HUB_CACHE), /workspace/ollama
POSTGRES_STATE=PostgreSQL 16 + pgvector/pg_trgm/pgcrypto @127.0.0.1:5432, role/db bossman, DSN в /workspace/AiMaxBossman/.env (600) и /etc/profile.d/bossman_env.sh (600)
GATEWAY_STATE=WORKING: BOSSMAN_GATEWAY_CONFIG=/workspace/artifacts/gateway.runpod.yaml (порт 8877, aliases fast/smart/coder, client rate 6000rpm/200burst после BUG-003), key в /workspace/artifacts/gw.key; core env BOSSMAN_GATEWAY_URL=http://127.0.0.1:8877/v1
ROUTER_STATE=bcc/v2/model_router.route() INTEGRATION_PROVEN на живых метриках; BCC engine HTTP path pending
MEMORY_STATE=LIVE_PROVEN: PG write → PG restart + serve restart → restore; WM 1 row/task (108 задач)
CONTEXT_STATE=8k/16k 4/4 обе руки; 32k 3/4 обе руки (потолок 7b); деградация Bossman-пути 0.0
SECURITY_STATE=egress redaction ✓, auth negative ✓, sandbox secrets grants ✓, cloud_policy=never enforced, CyberSec layer GATED_NOT_ENABLED (by design)
LIVE_PROVEN_FUNCTIONS=gateway-inference, task-engine, memory-restart, files-csv/json/md/zip, artifact-engine, MCP-stdio, browser-chromium, recovery-cycle, scheduler-cron, flight-recorder-explain, concurrency-4, long-run-50, router-logic
PARTIAL_FUNCTIONS=router (BCC engine HTTP pending), computer (контракты только; GUI = HOST_NOT_APPLICABLE на Linux)
GATED_FUNCTIONS=CyberSec V1 (BOSSMAN_CYBERSEC_V1_ENABLED), V3: UCA/Visual State/Self-Healing/Skill Factory/Recovery Kernel/SIL/Context Guardian
HOST_NOT_APPLICABLE=Windows GUI computer control, Windows-only тесты
LAST_COMPLETED_PHASE=все 10 приоритетных фаз владельца
NEXT_PHASE=BCC-engine router HTTP live; research engine live; command-center full regression на pod; long-run 100; runner 429-backoff (FUTURE_LOCAL)
KNOWN_FAILURES=RUNPOD_FAILURES.md: BUG-002 pgvector env, BUG-003 gateway client rate default, GAP-001 RSS sampler, DISC-001 alias fail-fast, NOTE-002 scheduler wiring
KNOWN_WORKAROUNDS=setsid+/dev/null для фоновых процессов под ssh; pgvector pre-create суперпользователем; execve limit ~128KB (длинные тексты — через db.insert+enqueue, не argv)
FUTURE_LOCAL_BLOCKERS=не обнаружено; жёстких аппаратных потолков в коде не найдено (порты/пути/модели — из конфига)
FUTURE_MEMORY_EXPANSION_ITEMS=лимиты конфигурируемы; каноническая память PostgreSQL; кэши bounded
BENCHMARK_PATHS=/workspace/benchmarks: ab_small_qwen25_7b.log, ab_medium_qwen25_14b.log, ab_large_qwen25_32b.log, router_real.json, final_regression_core.log
ARTIFACT_PATHS=/workspace/artifacts: gateway_e2e_resp.json, stress_direct.json, stress_bossman.json, stress_bossman_32k.json, files/files_real.json, gateway.runpod.yaml, gw.key (600)
CLOUD_CALLS=0
SAFE_TO_CONTINUE_LOCAL=YES
SETUP_PENDING=нет
