# RUNPOD LOCAL HANDOFF (machine-readable)
CURRENT_SHA=d1f218d
LAST_VERIFIED_SHA=d1f218d
RUNPOD_GPU=RTX 5090 32GB, driver 570.133.20
MODEL_RUNTIME=NONE_YET (Ollama planned at /workspace/ollama, env OLLAMA_MODELS set)
MODELS_DOWNLOADED=none
MODEL_PATHS=none
MODEL_REVISIONS=none
QUANTIZATIONS=none
CACHE_PATHS=/workspace/hf-cache (HF_HOME, HUGGINGFACE_HUB_CACHE)
POSTGRES_STATE=not installed; canonical Bossman PostgreSQL path required (Phase 7)
GATEWAY_STATE=not started
ROUTER_STATE=not tested
MEMORY_STATE=not tested
CONTEXT_STATE=not tested
SECURITY_STATE=P1 project_host ASK enforced at commit 82e5099-era code (see repo tools_terminal.py)
LAST_COMPLETED_PHASE=0_PREFLIGHT
NEXT_PHASE=await GPU Acceptance Master Prompt (then Phase 2 fast baseline small model)
KNOWN_FAILURES=none yet
KNOWN_WORKAROUNDS=none needed on Linux so far (Windows teardown-hang workaround not applicable)
SETUP_PENDING=pip install Bossman deps (bossman-core editable + deps), install Ollama binary, install/start PostgreSQL (canonical path), then re-run python3 tools/runpod_preflight.py expecting RUNPOD_READY=YES
IMPORTANT_DECISIONS=env persisted via /etc/profile.d/bossman_env.sh; repo at /workspace/AiMaxBossman
BENCHMARK_PATHS=/workspace/benchmarks (empty)
ARTIFACT_PATHS=/workspace/artifacts (empty)
CLOUD_CALLS=0
SAFE_TO_CONTINUE_LOCAL=YES (after preflight commit pushed)
FUTURE_MEMORY_EXPANSION_ITEM=none found yet (no hard-coded ceilings audited yet)
