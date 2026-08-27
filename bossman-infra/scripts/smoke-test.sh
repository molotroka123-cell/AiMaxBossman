#!/usr/bin/env bash
# Проверка цепочки: LiteLLM → (llama-swap | облако). MODEL=claude-heavy ./scripts/smoke-test.sh
set -euo pipefail
cd "$(dirname "$0")/.."
set -a; source .env; set +a
API="http://127.0.0.1:4000/v1"
MODEL="${MODEL:-bossman-fast}"

echo "--- контейнеры ---"
docker compose ps --format "table {{.Service}}\t{{.Status}}"

echo "--- /v1/models ---"
curl -fs "$API/models" -H "Authorization: Bearer $LITELLM_MASTER_KEY" | jq -r '.data[].id'

echo "--- chat: $MODEL ---"
curl -fs "$API/chat/completions" -H "Authorization: Bearer $LITELLM_MASTER_KEY" -H "Content-Type: application/json" \
  -d "{\"model\":\"$MODEL\",\"messages\":[{\"role\":\"user\",\"content\":\"Ответь одним словом: работаешь?\"}],\"max_tokens\":20}" \
  | jq -r '.choices[0].message.content'

echo "--- embeddings ---"
curl -fs "$API/embeddings" -H "Authorization: Bearer $LITELLM_MASTER_KEY" -H "Content-Type: application/json" \
  -d '{"model":"bossman-embed","input":"тест"}' | jq -r '.data[0].embedding | length | "размерность: \(.)"' || echo "embed недоступен (ок на ноутбуке без local)"
echo "OK"
