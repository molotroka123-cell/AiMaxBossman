#!/usr/bin/env bash
cd /workspace/AiMaxBossman
mkdir -p /workspace/artifacts /workspace/benchmarks
source /etc/profile.d/bossman_env.sh
echo "=== CLOUD_KEYS_PRESENT (presence only) ==="
env | grep -oE '^(OPENROUTER|OPENAI|ANTHROPIC|GEMINI|GOOGLE|DEEPSEEK|TOGETHER|GROQ)[A-Z_]*' | sort -u || echo NONE
echo "=== OLLAMA SANITY ==="
pgrep -x ollama > /dev/null || (OLLAMA_MODELS=/workspace/ollama nohup ollama serve > /workspace/ollama/serve.log 2>&1 &)
sleep 2
curl -s 127.0.0.1:11434/api/version && echo
echo "=== START GATEWAY (port 8877) ==="
export BOSSMAN_GATEWAY_CORE_KEY=$(openssl rand -hex 16)
export BOSSMAN_GATEWAY_CONFIG=/workspace/AiMaxBossman/bossman-core/config/gateway.local-hardware.yaml
nohup python3 -m bossman.gateway.main > /workspace/artifacts/gateway_e2e.log 2>&1 &
echo $! > /tmp/gw.pid
UP=NO
for i in $(seq 1 40); do
  CODE=$(curl -s -o /dev/null -w '%{http_code}' -H "Authorization: Bearer $BOSSMAN_GATEWAY_CORE_KEY" http://127.0.0.1:8877/v1/models 2>/dev/null)
  if [ "$CODE" != "000" ]; then UP=YES; break; fi
  sleep 1
done
echo GATEWAY_UP=$UP CODE=$CODE
echo "=== E2E CHAT ==="
cat > /tmp/req.json <<'EOF'
{"model":"bossman-fast","messages":[{"role":"user","content":"Reply with exactly: OK"}],"max_tokens":16,"temperature":0}
EOF
curl -s -X POST http://127.0.0.1:8877/v1/chat/completions \
  -H "Authorization: Bearer $BOSSMAN_GATEWAY_CORE_KEY" \
  -H "Content-Type: application/json" \
  -d @/tmp/req.json \
  -w '\nHTTP=%{http_code} TTFT=%{time_starttransfer}s TOTAL=%{time_total}s\n' \
  -o /workspace/artifacts/gateway_e2e_resp.json || echo CURL_FAIL
cat /workspace/artifacts/gateway_e2e_resp.json
echo
echo "=== GATEWAY LOG TAIL ==="
tail -8 /workspace/artifacts/gateway_e2e.log
kill $(cat /tmp/gw.pid) 2>/dev/null
echo GATEWAY_STOPPED