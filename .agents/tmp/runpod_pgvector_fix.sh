#!/usr/bin/env bash
source /etc/profile.d/bossman_env.sh
source /workspace/AiMaxBossman/.env
echo "=== PGVECTOR ==="
DEBIAN_FRONTEND=noninteractive apt-get install -y -qq postgresql-16-pgvector > /tmp/pgv.log 2>&1; echo APT_EXIT=$?
ls /usr/share/postgresql/16/extension/vector.control && echo PGVECTOR_OK
echo "=== RESTART CORE ==="
pkill -f "bossman.cli serve" 2>/dev/null; sleep 1
export BOSSMAN_GATEWAY_URL=http://127.0.0.1:8877/v1
export BOSSMAN_GATEWAY_CORE_KEY=$(cat /workspace/artifacts/gw.key)
export BOSSMAN_GATEWAY_CONFIG=/workspace/artifacts/gateway.runpod.yaml
pgrep -f "bossman.gateway.main" > /dev/null || (cd /workspace/AiMaxBossman && nohup python3 -m bossman.gateway.main > /workspace/artifacts/gateway.log 2>&1 &)
cd /workspace/AiMaxBossman/bossman-core
nohup python3 -m bossman.cli serve > /workspace/artifacts/bossman_serve.log 2>&1 &
echo CORE_PID=$!
sleep 6
tail -3 /workspace/artifacts/bossman_serve.log
echo "=== SCHEMA CHECK ==="
psql "$BOSSMAN_DATABASE_URL" -tAc "SELECT extname FROM pg_extension" | tr '\n' ' '; echo
psql "$BOSSMAN_DATABASE_URL" -tAc "\dt" | wc -l | xargs echo TABLES=
echo "=== SUBMIT REAL TASK ==="
python3 -m bossman.cli task "Ответь ровно: OK" --agent analyst
sleep 2
TID=$(psql "$BOSSMAN_DATABASE_URL" -tAc "SELECT max(id) FROM tasks" | tr -d '[:space:]')
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
echo "=== MEMORY/EVENTS ==="
psql "$BOSSMAN_DATABASE_URL" -tAc "SELECT count(*) FROM events" 2>/dev/null | xargs echo events_rows=
psql "$BOSSMAN_DATABASE_URL" -tAc "SELECT count(*) FROM working_memory" 2>/dev/null | xargs echo working_memory_rows=
psql "$BOSSMAN_DATABASE_URL" -tAc "SELECT count(*) FROM decisions" 2>/dev/null | xargs echo decisions_rows=
echo "=== GATEWAY REQUESTS ==="
grep -E "bossman.gateway request_id" /workspace/artifacts/gateway.log | tail -5
echo "=== SERVE LOG TAIL ==="
tail -6 /workspace/artifacts/bossman_serve.log