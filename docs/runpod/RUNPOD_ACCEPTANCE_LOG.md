# RUNPOD ACCEPTANCE LOG
## [preflight] 2026-09-01
- connected: ssh root@213.173.111.21 -p 47591
- GPU RTX 5090 32607MiB, driver 570.133.20, CUDA 12.8, torch 2.8.0+cu128 cuda_avail=True
- CPU 32 vCPU, RAM 187GiB (155 avail), container disk 30G, /workspace network volume (mfs euro-3)
- persistent dirs created: AiMaxBossman models hf-cache ollama benchmarks artifacts
- env persisted: /etc/profile.d/bossman_env.sh (HF_HOME, HUGGINGFACE_HUB_CACHE, OLLAMA_MODELS)
- repo cloned, branch claude/bossman-control-v03-43igbk, LOCAL==REMOTE==d1f218d, tree clean
- NOT installed yet: PostgreSQL, Ollama, Bossman python deps (planned Phase 2, no models until then)
## [preflight-canonical] 2026-09-01
- python3 tools/runpod_preflight.py: RUNPOD_READY=NO, единственный blocker: gateway import fail (No module named fastapi) = python-зависимости Bossman не установлены
- Ollama binary=NO reachable=NO; POSTGRES reachable=NO; MODEL_CACHE пусты — ожидаемо на свежем поде, установка = Phase 2 setup
- Железо/repo/сеть подтверждены; известные deployment-заметки: config.py дефолты docker-compose имён требуют .env; bind 127.0.0.1 by design
- Параллельная сессия (d1f218d): headless Chromium P1 исправлен, A/B-харнесс Linux-фиксы, bossman-core 1274 passed / cc 634 passed (на их хосте с PG 16.13)
## [setup-complete + phase2] 2026-09-01T18:20Z
- deps: pip -e bossman-core[dev,resource] + command-center[dev,mcp] (--break-system-packages, system python 3.12.3)
- PostgreSQL 16 @5432 running, role/db bossman, PG_AUTH_OK; Redis PONG; Ollama 0.33.2 @11434; RUNPOD_READY=YES
- cgroup caps: cpu.max/memory.max absent → host limits = 32 vCPU / 187.8 GiB (independent verification)
- Gateway E2E LIVE_PROVEN: bossman-fast → qwen2.5:7b → "OK", HTTP 200, fallbacks=0, cold TTFT 10.6s
- A/B SMALL: Direct 12/18 = Bossman 12/18, Retention 1.0, cloud_calls=0, VRAM peak 6894 MiB — GATE PASS
- qwen2.5:14b (MEDIUM) pull started; 7b выгружать перед tier-переключением
