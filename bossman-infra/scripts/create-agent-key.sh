#!/usr/bin/env bash
# Ключ LiteLLM для агента с белым списком моделей — контроль «мозг/руки» на уровне шлюза.
#   ./scripts/create-agent-key.sh fresh-vibes-agent bossman-fast,bossman-embed
#   ./scripts/create-agent-key.sh bossman-coder     bossman-coder,claude-heavy
#   ./scripts/create-agent-key.sh personal-chat     bossman-uncensored     # только чат, инструментов нет
set -euo pipefail
cd "$(dirname "$0")/.."
set -a; source .env; set +a
NAME="$1"; MODELS="$2"
json_models=$(echo "$MODELS" | jq -R 'split(",")')
curl -fs http://127.0.0.1:4000/key/generate -H "Authorization: Bearer $LITELLM_MASTER_KEY" -H "Content-Type: application/json" \
  -d "{\"key_alias\":\"$NAME\",\"models\":$json_models,\"metadata\":{\"agent\":\"$NAME\"}}" | jq -r '"\(.key_alias): \(.key)"'
