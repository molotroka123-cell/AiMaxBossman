# bossman-infra

Инфраструктура локальных LLM для домашнего сервера Bossman (ACEMAGIC M1A PRO+, Ryzen AI MAX+ 395, 128 ГБ).
Один OpenAI-совместимый адрес для чата и будущих агентов; локальные модели через llama.cpp, облачные через тот же шлюз.

Полный чек-лист дня приезда и ТЗ: [`docs/STAGE0_AND_SPEC.md`](docs/STAGE0_AND_SPEC.md).

## Структура

```
compose.yaml              базовый compose (сервер и ноутбук)
compose.server.yaml       надстройка для сервера: GPU-образ, /dev/dri, /dev/kfd
.env.example              → скопировать в .env и заполнить
litellm/config.yaml       алиасы моделей, роутинг локально/облако, fallback
llama-swap/config.yaml    какая модель каким llama-server запускается, группы по памяти
llama-swap/config-cpu.yaml  конфиг для CPU-теста на ноутбуке
postgres/init/            БД bossman + расширение vector
scripts/host-setup-fedora.sh   настройка хоста (шаги 1.3–1.7 чек-листа)
scripts/download-models.sh     загрузка GGUF с Hugging Face
scripts/smoke-test.sh          проверка цепочки
scripts/bench.sh               замер tok/s
scripts/create-agent-key.sh    ключ LiteLLM для агента с белым списком моделей
```

## Быстрый старт — сервер

```bash
cp .env.example .env && nano .env      # COMPOSE_FILE=compose.yaml:compose.server.yaml, COMPOSE_PROFILES=local
./scripts/download-models.sh
nano llama-swap/config.yaml            # сверить имена .gguf
docker compose up -d
./scripts/smoke-test.sh
```

## Быстрый старт — ноутбук (до приезда железа)

Только облако — проверяем UI и шлюз:

```bash
cp .env.example .env
# в .env: COMPOSE_FILE=compose.yaml   COMPOSE_PROFILES=   DEFAULT_MODEL=claude-heavy   + ANTHROPIC_API_KEY
docker compose up -d
MODEL=claude-heavy ./scripts/smoke-test.sh
```

CPU-тест — проверяем llama-swap и llama-server без GPU:

```bash
# в .env: COMPOSE_PROFILES=local   LLAMA_SWAP_CONFIG=./llama-swap/config-cpu.yaml   MODELS_DIR=./models
CPU_TEST=1 MODELS_DIR=./models ./scripts/download-models.sh
docker compose up -d
./scripts/smoke-test.sh
```

## Адреса (только localhost; наружу — через `tailscale serve`)

| Сервис | Адрес |
|---|---|
| Open WebUI | http://127.0.0.1:3000 |
| LiteLLM API | http://127.0.0.1:4000/v1 |
| LiteLLM UI | http://127.0.0.1:4000/ui |
| llama-swap | http://127.0.0.1:8080 (список загруженных: /running) |
| Uptime Kuma | http://127.0.0.1:3001 |

## Ключи для агентов

```bash
./scripts/create-agent-key.sh fresh-vibes-agent bossman-fast,bossman-embed
./scripts/create-agent-key.sh bossman-coder     bossman-coder,claude-heavy
./scripts/create-agent-key.sh personal-chat     bossman-uncensored
```

## Бенчмарк (заполнить после приезда)

| Алиас | Модель | Квант | Контекст | ток/с |
|---|---|---|---|---|
| bossman-fast | | | | |
| bossman-smart | | | | |
| bossman-coder | | | | |
| bossman-writer | | | | |

## Ссылки

- llama-swap: https://github.com/mostlygeek/llama-swap
- LiteLLM proxy: https://docs.litellm.ai/docs/simple_proxy
- Open WebUI: https://github.com/open-webui/open-webui
- Strix Halo сборки: https://github.com/kyuz0/amd-strix-halo-toolboxes
