#!/usr/bin/env bash
set -e
mkdir -p /workspace/AiMaxBossman/docs/runpod
cd /workspace/AiMaxBossman/docs/runpod
cat > RUNPOD_ACCEPTANCE_STATE.md <<'EOF'
# RUNPOD ACCEPTANCE STATE
REMOTE_SHA_SOURCE_OF_TRUTH=yes
CURRENT_SHA=d1f218d
LAST_VERIFIED_SHA=d1f218d
GPU=RTX_5090_32GB
CUDA=12.8
TORCH=2.8.0+cu128
PYTORCH_CUDA=True
RAM_GIB=187
CPU_VCPU=32
WORKSPACE_PERSISTENT=YES (network volume mfs euro-3)
REGION=EU
HF_HOME=/workspace/hf-cache
OLLAMA_MODELS=/workspace/ollama
POSTGRES=NOT_INSTALLED
MODEL_RUNTIME=NOT_INSTALLED
CLOUD_KEYS_PRESENT=NO (by design; CLOUD_CALLS=0 required)
LAST_COMPLETED_PHASE=0_PREFLIGHT
NEXT_PHASE=await_master_prompt
RUNPOD_PREFLIGHT=PASS
EOF
cat > RUNPOD_ACCEPTANCE_LOG.md <<'EOF'
# RUNPOD ACCEPTANCE LOG
## [preflight] 2026-09-01
- connected: ssh root@213.173.111.21 -p 47591
- GPU RTX 5090 32607MiB, driver 570.133.20, CUDA 12.8, torch 2.8.0+cu128 cuda_avail=True
- CPU 32 vCPU, RAM 187GiB (155 avail), container disk 30G, /workspace network volume (mfs euro-3)
- persistent dirs created: AiMaxBossman models hf-cache ollama benchmarks artifacts
- env persisted: /etc/profile.d/bossman_env.sh (HF_HOME, HUGGINGFACE_HUB_CACHE, OLLAMA_MODELS)
- repo cloned, branch claude/bossman-control-v03-43igbk, LOCAL==REMOTE==d1f218d, tree clean
- NOT installed yet: PostgreSQL, Ollama, Bossman python deps (planned Phase 2, no models until then)
EOF
cat > RUNPOD_LOCAL_HANDOFF.md <<'EOF'
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
IMPORTANT_DECISIONS=env persisted via /etc/profile.d/bossman_env.sh; repo at /workspace/AiMaxBossman
BENCHMARK_PATHS=/workspace/benchmarks (empty)
ARTIFACT_PATHS=/workspace/artifacts (empty)
CLOUD_CALLS=0
SAFE_TO_CONTINUE_LOCAL=YES (after preflight commit pushed)
FUTURE_MEMORY_EXPANSION_ITEM=none found yet (no hard-coded ceilings audited yet)
EOF
cat > RUNPOD_METRICS.json <<'EOF'
{"phase":"0_preflight","timestamp":"2026-09-01T18:00Z","gpu":"RTX 5090","vram_mib":32607,"cuda":"12.8","torch":"2.8.0+cu128","pytorch_cuda":true,"cpu_vcpus":32,"ram_gib":187,"workspace_persistent":true,"remote_sha":"d1f218d","local_sha":"d1f218d","cloud_calls":0,"postgres":false,"ollama":false,"preflight":"PASS"}
EOF
cd /workspace/AiMaxBossman
git add docs/runpod/
git -c user.name="bossman-acceptance" -c user.email="bossman-acceptance@runpod.local" commit -m "docs(runpod): preflight PASS checkpoint - RTX 5090 32GB, CUDA 12.8, torch cu128, repo synced d1f218d" | tail -1
git push 2>&1 | grep -E "->|error" | head -2
git rev-parse HEAD
git rev-parse origin/claude/bossman-control-v03-43igbk