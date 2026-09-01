# RUNPOD ACCEPTANCE LOG
## [preflight] 2026-09-01
- connected: ssh root@213.173.111.21 -p 47591
- GPU RTX 5090 32607MiB, driver 570.133.20, CUDA 12.8, torch 2.8.0+cu128 cuda_avail=True
- CPU 32 vCPU, RAM 187GiB (155 avail), container disk 30G, /workspace network volume (mfs euro-3)
- persistent dirs created: AiMaxBossman models hf-cache ollama benchmarks artifacts
- env persisted: /etc/profile.d/bossman_env.sh (HF_HOME, HUGGINGFACE_HUB_CACHE, OLLAMA_MODELS)
- repo cloned, branch claude/bossman-control-v03-43igbk, LOCAL==REMOTE==d1f218d, tree clean
- NOT installed yet: PostgreSQL, Ollama, Bossman python deps (planned Phase 2, no models until then)
