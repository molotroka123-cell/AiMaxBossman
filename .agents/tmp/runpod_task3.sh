#!/usr/bin/env bash
source /etc/profile.d/bossman_env.sh
source /workspace/AiMaxBossman/.env
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
  bossman-coder:
    targets:
      - backend: ollama
        model: qwen2.5:7b
        capabilities: [text, tools]
clients:
  bossman-core:
    key_env: BOSSMAN_GATEWAY_CORE_KEY
    allowed_aliases: [bossman-fast, bossman-smart, bossman-coder]
EOF
pkill -f "bossman.gateway.main" 2>/dev/null; pkill -f "bossman.cli serve" 2>/dev/null; sleep 1
export BOSSMAN_GATEWAY_CORE_KEY=$(cat /workspace/artifacts/gw.key)
export BOSSMAN_GATEWAY_CONFIG=/workspace/artifacts/gateway.runpod.yaml
export BOSSMAN_GATEWAY_URL=http://127.0.0.1:8877/v1
cd /workspace/AiMaxBossman && nohup python3 -m bossman.gateway.main > /workspace/artifacts/gateway.log 2>&1 &
sleep 4
cd /workspace/AiMaxBossman/bossman-core && nohup python3 -m bossman.cli serve > /workspace/artifacts/bossman_serve.log 2>&1 &
sleep 6
echo "=== SUBMIT TASKS ==="
python3 -m bossman.cli task "Ответь ровно: OK" --agent analyst
TID=$(psql "$BOSSMAN_DATABASE_URL" -tAc "SELECT max(id) FROM tasks" | tr -d '[:space:]')
echo WATCH_ID=$TID
FINAL=TIMEOUT
for i in $(seq 1 90); do
  S=$(psql "$BOSSMAN_DATABASE_URL" -tAc "SELECT status FROM tasks WHERE id=$TID" 2>/dev/null | tr -d '[:space:]')
  if [ "$S" = "done" ] || [ "$S" = "failed" ]; then FINAL=$S; break; fi
  sleep 2
done
echo TASK_FINAL=$FINAL
psql "$BOSSMAN_DATABASE_URL" -c "SELECT id,status,agent FROM tasks ORDER BY id"
psql "$BOSSMAN_DATABASE_URL" -c "SELECT id,task_id,agent,status,steps FROM runs WHERE task_id=$TID" 2>/dev/null
echo "=== TASK RESULT ==="
psql "$BOSSMAN_DATABASE_URL" -tAc "SELECT coalesce(result,'<null>') FROM tasks WHERE id=$TID" | head -3
echo "=== GATEWAY REQUESTS ==="
grep -E "bossman.gateway request_id" /workspace/artifacts/gateway.log | tail -6
echo "=== SERVE LOG TAIL ==="
tail -4 /workspace/artifacts/bossman_serve.log