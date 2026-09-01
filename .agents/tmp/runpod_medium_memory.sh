#!/usr/bin/env bash
source /etc/profile.d/bossman_env.sh
source /workspace/AiMaxBossman/.env
echo "=== PHASE MEDIUM A/B (GPU) ==="
OLLAMA_MODELS=/workspace/ollama ollama stop qwen2.5:7b 2>/dev/null; echo UNLOADED_7B
sleep 2
nvidia-smi --query-gpu=memory.used --format=csv,noheader
cd /workspace/AiMaxBossman
export BOSSMAN_AB_MODEL=qwen2.5:14b
python3 -u tools/local_hardware_ab.py > /workspace/benchmarks/ab_medium_qwen25_14b.log 2>&1
echo AB_EXIT=$?
tail -c 700 /workspace/benchmarks/ab_medium_qwen25_14b.log
echo
echo "=== PHASE MEMORY RESTART (CPU/DB) ==="
BEFORE=$(psql "$BOSSMAN_DATABASE_URL" -tAc "SELECT count(*) FROM working_memory" | tr -d '[:space:]')
MAXID=$(psql "$BOSSMAN_DATABASE_URL" -tAc "SELECT coalesce(max(id),0) FROM working_memory" | tr -d '[:space:]')
echo WM_BEFORE=$BEFORE MAXID=$MAXID
service postgresql restart > /dev/null 2>&1; sleep 3
pg_isready -h 127.0.0.1 -p 5432
pkill -f "bossman.cli serve"; sleep 2
cd /workspace/AiMaxBossman/bossman-core
export BOSSMAN_GATEWAY_URL=http://127.0.0.1:8877/v1
export BOSSMAN_GATEWAY_CORE_KEY=$(cat /workspace/artifacts/gw.key)
export BOSSMAN_GATEWAY_CONFIG=/workspace/artifacts/gateway.runpod.yaml
setsid nohup python3 -m bossman.cli serve < /dev/null > /workspace/artifacts/bossman_serve.log 2>&1 &
sleep 6
python3 -m bossman.cli task "Ответь ровно: OK2" --agent analyst
sleep 2
TID=$(psql "$BOSSMAN_DATABASE_URL" -tAc "SELECT max(id) FROM tasks" | tr -d '[:space:]')
FINAL=TIMEOUT
for i in $(seq 1 90); do
  S=$(psql "$BOSSMAN_DATABASE_URL" -tAc "SELECT status FROM tasks WHERE id=$TID" 2>/dev/null | tr -d '[:space:]')
  if [ "$S" = "done" ] || [ "$S" = "failed" ]; then FINAL=$S; break; fi
  sleep 2
done
echo TASK_AFTER_RESTART=$FINAL
AFTER_TOTAL=$(psql "$BOSSMAN_DATABASE_URL" -tAc "SELECT count(*) FROM working_memory" | tr -d '[:space:]')
AFTER_OLD=$(psql "$BOSSMAN_DATABASE_URL" -tAc "SELECT count(*) FROM working_memory WHERE id <= $MAXID" | tr -d '[:space:]')
echo WM_TOTAL=$AFTER_TOTAL WM_PRE_RESTART_ROWS_STILL_PRESENT=$AFTER_OLD
psql "$BOSSMAN_DATABASE_URL" -c "SELECT id,status FROM tasks ORDER BY id DESC LIMIT 3"
grep -E "bossman.gateway request_id" /workspace/artifacts/gateway.log | tail -2