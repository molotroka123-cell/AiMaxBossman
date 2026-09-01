#!/usr/bin/env bash
echo "=== PIP DEPS ==="
cd /workspace/AiMaxBossman
pip install -q --break-system-packages -e 'bossman-core[dev,resource]' 2>&1 | tail -1
echo CORE_PIP=$?
pip install -q --break-system-packages -e 'command-center[dev,mcp]' 2>&1 | tail -1
echo CC_PIP=$?
echo "=== APT PG+REDIS ==="
DEBIAN_FRONTEND=noninteractive apt-get install -y -qq postgresql redis-server > /tmp/apt.log 2>&1
echo APT_EXIT=$?
(service postgresql start || pg_ctlcluster 16 main start) > /dev/null 2>&1; sleep 2
(service redis-server start || redis-server --daemonize yes) > /dev/null 2>&1; sleep 1
echo PG_READY=$(pg_isready -h 127.0.0.1 -p 5432 2>/dev/null)
echo "=== PG ROLE/DB ==="
PW=$(openssl rand -hex 12)
su postgres -c "psql -tAc \"SELECT 1 FROM pg_roles WHERE rolname='bossman'\"" | grep -q 1 \
  && su postgres -c "psql -q -c \"ALTER ROLE bossman WITH LOGIN PASSWORD '$PW';\"" \
  || su postgres -c "psql -q -c \"CREATE ROLE bossman LOGIN PASSWORD '$PW';\""
su postgres -c "createdb -O bossman bossman" 2>/dev/null || echo db_exists
su postgres -c "psql -tAc 'SELECT datname FROM pg_database'" | grep -q bossman && echo DB_OK
echo "=== ENV PERSIST ==="
cat > /etc/profile.d/bossman_env.sh <<EOF
export HF_HOME=/workspace/hf-cache
export HUGGINGFACE_HUB_CACHE=/workspace/hf-cache
export OLLAMA_MODELS=/workspace/ollama
export BOSSMAN_DATABASE_URL=postgresql://bossman:$PW@127.0.0.1:5432/bossman
export REDIS_URL=redis://127.0.0.1:6379/0
EOF
chmod 600 /etc/profile.d/bossman_env.sh
cat > /workspace/AiMaxBossman/.env <<EOF
BOSSMAN_DATABASE_URL=postgresql://bossman:$PW@127.0.0.1:5432/bossman
REDIS_URL=redis://127.0.0.1:6379/0
EOF
chmod 600 /workspace/AiMaxBossman/.env
git check-ignore .env && echo ENV_GITIGNORED || echo ENV_NOT_IGNORED_CHECK
export BOSSMAN_DATABASE_URL=postgresql://bossman:$PW@127.0.0.1:5432/bossman
PGPASSWORD=$PW psql -h 127.0.0.1 -U bossman -d bossman -tAc 'select 1;' && echo PG_AUTH_OK
echo "=== OLLAMA ==="
which ollama > /dev/null 2>&1 || curl -fsSL https://ollama.com/install.sh | sh > /tmp/ollama_install.log 2>&1
echo OLLAMA_INSTALL=$?
pgrep -x ollama > /dev/null || (OLLAMA_MODELS=/workspace/ollama nohup ollama serve > /workspace/ollama/serve.log 2>&1 &)
sleep 3
echo OLLAMA_VERSION=$(ollama --version 2>/dev/null)
curl -s 127.0.0.1:11434/api/version && echo
echo "=== PREFLIGHT ==="
python3 tools/runpod_preflight.py 2>&1 | tail -22