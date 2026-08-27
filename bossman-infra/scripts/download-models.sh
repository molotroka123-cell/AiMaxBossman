#!/usr/bin/env bash
# Загрузка GGUF-моделей в $MODELS_DIR. Имена репозиториев на Hugging Face меняются каждый месяц:
# перед запуском проверить строки с TODO на huggingface.co (поиск по имени модели + "GGUF").
set -euo pipefail
MODELS_DIR="${MODELS_DIR:-/opt/bossman/models}"
mkdir -p "$MODELS_DIR"
command -v hf >/dev/null 2>&1 || pip install -U "huggingface_hub[cli]"

# формат: "repo|include-pattern"
MODELS=(
  "ggml-org/gpt-oss-120b-GGUF|*mxfp4*"                          # bossman-smart, ~61 ГБ
  "Qwen/Qwen3-Embedding-0.6B-GGUF|*Q8_0*"                       # bossman-embed
  "unsloth/Qwen3.6-35B-A3B-GGUF|*Q8_0*"                          # bossman-fast   TODO уточнить репозиторий
  "unsloth/Qwen3-Coder-30B-A3B-Instruct-GGUF|*Q8_0*"            # bossman-coder  TODO уточнить актуальную версию Coder
  "unsloth/Qwen3.8-27B-GGUF|*Q6_K*"                              # bossman-writer TODO уточнить репозиторий
  # "Youssofal/Qwen3.6-35B-A3B-Uncensored-GGUF|*Q8_0*"           # bossman-uncensored TODO уточнить (личный чат)
)

# для CPU-теста на ноутбуке: MODELS=("Qwen/Qwen3-0.6B-GGUF|*Q8_0*" "Qwen/Qwen3-Embedding-0.6B-GGUF|*Q8_0*")
[ "${CPU_TEST:-0}" = "1" ] && MODELS=("Qwen/Qwen3-0.6B-GGUF|*Q8_0*" "Qwen/Qwen3-Embedding-0.6B-GGUF|*Q8_0*")

for entry in "${MODELS[@]}"; do
  repo="${entry%%|*}"; pat="${entry##*|}"
  echo "=== $repo ($pat) ==="
  hf download "$repo" --include "$pat" --local-dir "$MODELS_DIR/$(basename "$repo")"
done
echo "Готово. Проверь имена файлов и поправь пути в llama-swap/config.yaml:"
find "$MODELS_DIR" -name '*.gguf' | sort
