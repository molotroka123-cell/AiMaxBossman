#!/usr/bin/env bash
source /workspace/AiMaxBossman/.env
psql "$BOSSMAN_DATABASE_URL" -c "SELECT id,status,agent FROM tasks ORDER BY id"
psql "$BOSSMAN_DATABASE_URL" -c "SELECT id,task_id,agent,status,steps FROM runs ORDER BY id"
echo "=== RESULT TEXT ==="
psql "$BOSSMAN_DATABASE_URL" -tAc "SELECT left(result,200) FROM tasks WHERE id=2" 2>/dev/null
psql "$BOSSMAN_DATABASE_URL" -tAc "SELECT left(result,200) FROM tasks WHERE id=1"
echo "=== WORKING MEMORY ==="
psql "$BOSSMAN_DATABASE_URL" -tAc "SELECT count(*) FROM working_memory"
echo "=== GATEWAY ALL REQUESTS ==="
grep -cE "bossman.gateway request_id" /workspace/artifacts/gateway.log | xargs echo total_requests=
grep -E "bossman.gateway request_id" /workspace/artifacts/gateway.log | tail -6
echo "=== SERVE LOG ERRORS ==="
grep -ciE "error|exception" /workspace/artifacts/bossman_serve.log | xargs echo err_lines=