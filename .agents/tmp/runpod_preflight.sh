#!/usr/bin/env bash
echo "---PY---"
python3 --version 2>&1
pip3 --version 2>&1 | head -1
echo "---GIT---"
git --version 2>&1
echo "---TORCH---"
python3 -c "import torch; print('torch', torch.__version__); print('cuda_avail', torch.cuda.is_available()); print('gpu', torch.cuda.get_device_name(0) if torch.cuda.is_available() else 'NONE'); print('vram_gib', round(torch.cuda.get_device_properties(0).total_memory/1024**3, 2) if torch.cuda.is_available() else 0)" 2>&1
echo "---NET---"
curl -s -o /dev/null -w "github:%{http_code}\n" https://github.com
echo "---PG---"
which psql 2>/dev/null || echo NO_PSQL
which postgres 2>/dev/null || echo NO_POSTGRES_BIN
ls /usr/lib/postgresql/ 2>/dev/null || echo NO_PG_LIBS
echo "---OLLAMA---"
which ollama 2>/dev/null || echo NO_OLLAMA
echo "---VENV---"
ls /workspace/venv 2>/dev/null || echo NO_VENV_YET
echo "---ENVPERSIST---"
mkdir -p /workspace/AiMaxBossman /workspace/models /workspace/hf-cache /workspace/ollama /workspace/benchmarks /workspace/artifacts
cat > /etc/profile.d/bossman_env.sh <<'EOF'
export HF_HOME=/workspace/hf-cache
export HUGGINGFACE_HUB_CACHE=/workspace/hf-cache
export OLLAMA_MODELS=/workspace/ollama
EOF
chmod 644 /etc/profile.d/bossman_env.sh
echo env_persisted:/etc/profile.d/bossman_env.sh
echo "---CLONE---"
if [ ! -d /workspace/AiMaxBossman/.git ]; then
  git clone https://github.com/molotroka123-cell/AiMaxBossman.git /workspace/AiMaxBossman 2>&1 | tail -2
fi
cd /workspace/AiMaxBossman
git fetch --all --prune 2>&1 | tail -1
git checkout claude/bossman-control-v03-43igbk 2>&1 | tail -1
git pull --ff-only 2>&1 | tail -1
echo "LOCAL_SHA=$(git rev-parse HEAD)"
echo "REMOTE_SHA=$(git rev-parse origin/claude/bossman-control-v03-43igbk)"
git log --oneline -3
echo "TREE_DIRTY=$(git status --porcelain | wc -l)"