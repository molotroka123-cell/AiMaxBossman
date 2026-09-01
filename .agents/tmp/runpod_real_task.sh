#!/usr/bin/env bash
cd /workspace/AiMaxBossman
mkdir -p /workspace/artifacts /workspace/benchmarks
source /etc/profile.d/bossman_env.sh
source /workspace/AiMaxBossman/.env

echo "=== GATEWAY CONFIG (runpod: fast+smart -> SMALL) ==="
cat > /workspace/artifacts/gateway.runpod.yaml <<'EOF'
server:
  host: 127.0.0.1
  port: 8877
  allow_unauthenticated_loopback: false
backends:
  ollama:
    base_url: http://127.0.0.1:11434
    health_path: /v1/models
    timeout_seconds: 120
aliases:
  bossman-fast:
    targets:
      - backend: ollama
        model: qwen2.5:7b
        capabilities: [text, tools]
  bossman-smart:
    targets:
      - backend: ollama
        model: qwen2.5:7b
        capabilities: [text, tools]
clients:
  bossman-core:
    key_env: BOSSMAN_GATEWAY_CORE_KEY
    allowed_aliases: [bossman-fast, bossman-smart]
EOF
echo config_written

pkill -f "bossman.gateway.main" 2>/dev/null; sleep 1
export BOSSMAN_GATEWAY_CORE_KEY=$(openssl rand -hex 16)
echo "$BOSSMAN_GATEWAY_CORE_KEY" > /workspace/artifacts/gw.key; chmod 600 /workspace/artifacts/gw.key
export BOSSMAN_GATEWAY_CONFIG=/workspace/artifacts/gateway.runpod.yaml
nohup python3 -m bossman.gateway.main > /workspace/artifacts/gateway.log 2>&1 &
echo $! >> /workspace/artifacts/pids.txt
UP=NO
for i in $(seq 1 40); do
  CODE=$(curl -s -o /dev/null -w '%{http_code}' -H "Authorization: Bearer $BOSSMAN_GATEWAY_CORE_KEY" http://127.0.0.1:8877/v1/models 2>/dev/null)
  if [ "$CODE" != "000" ]; then UP=YES; break; fi
  sleep 1
done
echo GATEWAY_UP=$UP

echo "=== START BOSSMAN CORE (serve + worker) ==="
pkill -f "bossman.cli serve" 2>/dev/null; pkill -f "bossman serve" 2>/dev/null; sleep 1
export BOSSMAN_GATEWAY_URL=http://127.0.0.1:8877/v1
cd /workspace/AiMaxBossman/bossman-core
nohup python3 -m bossman.cli serve > /workspace/artifacts/bossman_serve.log 2>&1 &
echo $! >> /workspace/artifacts/pids.txt
sleep 5
tail -5 /workspace/artifacts/bossman_serve.log

echo "=== SUBMIT REAL TASK ==="
cd /workspace/AiMaxBossman/bossman-core
python3 -m bossman.cli task "Ответь ровно: OK" --agent analyst

echo "=== POLL TASK STATUS ==="
TID=$(PGPASSWORD=$BOSSMAN_DATABASE_URL pg_isready >/dev/null 2>&1; psql "$BOSSMAN_DATABASE_URL" -tAc "SELECT max(id) FROM tasks")
echo TASK_ID=$TID
FINAL=TIMEOUT
for i in $(seq 1 90); do
  S=$(psql "$BOSSMAN_DATABASE_URL" -tAc "SELECT status FROM tasks WHERE id=$TID" 2>/dev/null | tr -d '[:space:]')
  if [ "$S" = "done" ] || [ "$S" = "failed" ]; then FINAL=$S; break; fi
  sleep 2
done
echo TASK_FINAL=$FINAL
psql "$BOSSMAN_DATABASE_URL" -c "SELECT id,status,agent,source FROM tasks WHERE id=$TID"
psql "$BOSSMAN_DATABASE_URL" -c "SELECT id,task_id,agent,status,steps FROM runs WHERE task_id=$TID" 2>/dev/null || true

echo "=== FLIGHT RECORDER / MEMORY TRACES ==="
psql "$BOSSMAN_DATABASE_URL" -tAc "SELECT count(*) FROM events" 2>/dev/null | xargs echo events_rows=
psql "$BOSSMAN_DATABASE_URL" -tAc "SELECT count(*) FROM working_memory" 2>/dev/null | xargs echo working_memory_rows=
psql "$BOSSMAN_DATABASE_URL" -c "\dt" 2>/dev/null | tail -20

echo "=== GATEWAY LOG (bossman-core requests) ==="
grep -E "bossman.gateway request_id" /workspace/artifacts/gateway.log | tail -5

echo "=== SERVE LOG TAIL ==="
tail -8 /workspace/artifacts/bossman_serve.log