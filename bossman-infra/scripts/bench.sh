#!/usr/bin/env bash
# Грубый замер tok/s через API (без llama-bench): ./scripts/bench.sh bossman-fast
set -euo pipefail
cd "$(dirname "$0")/.."
set -a; source .env; set +a
MODEL="${1:-bossman-fast}"
# прогрев (загрузка модели)
curl -s http://127.0.0.1:4000/v1/chat/completions -H "Authorization: Bearer $LITELLM_MASTER_KEY" -H "Content-Type: application/json" \
  -d "{\"model\":\"$MODEL\",\"messages\":[{\"role\":\"user\",\"content\":\"привет\"}],\"max_tokens\":5}" >/dev/null
start=$(date +%s.%N)
resp=$(curl -s http://127.0.0.1:4000/v1/chat/completions -H "Authorization: Bearer $LITELLM_MASTER_KEY" -H "Content-Type: application/json" \
  -d "{\"model\":\"$MODEL\",\"messages\":[{\"role\":\"user\",\"content\":\"Напиши подробный рассказ на 500 слов о Праге.\"}],\"max_tokens\":400,\"temperature\":0.7}")
end=$(date +%s.%N)
tokens=$(echo "$resp" | jq -r '.usage.completion_tokens')
echo "$MODEL: $tokens токенов за $(echo "$end - $start" | bc) с = $(echo "scale=1; $tokens / ($end - $start)" | bc) ток/с (включая prefill)"
