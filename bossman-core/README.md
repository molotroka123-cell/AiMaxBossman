# bossman-core — Bossman Control v0.3

Личные агенты на домашнем сервере. Реализация ТЗ v0.3
([`../bossman-infra/docs/BOSSMAN_CONTROL_TZ.md`](../bossman-infra/docs/BOSSMAN_CONTROL_TZ.md)):
одна петля выполнения, агент = папка, проекты с файловым состоянием,
жёсткая дисциплина контекста. Никаких фреймворков-оркестраторов — всё читается за вечер.

## Структура

```
bossman/
  api.py          FastAPI: API раздела 11, WS /events, статика UI
  runner.py       петля выполнения — одна на всех агентов (раздел 5)
  context.py      бюджет окна, порядок блоков, схлопывание, уплотнение (раздел 10)
  agents.py       загрузка папок агентов, политика облака, права инструментов
  llm.py          вызовы через LiteLLM ключом агента; проверка политики ДО отправки
  approvals.py    очередь подтверждений: необратимое — только с кнопки
  telegram.py     уведомления и кнопки да/нет (запасной канал)
  toolkit/        инструменты: декларация + обрезка в коде (лимиты из 10.4)
  projects/       планировщик, маршрутизатор, раннер проектов (раздел 9)
agents/           analyst · coder · fresh-vibes  (agent.yaml + prompt.md + memory.md)
tools/registry.yaml   реестр инструментов проектов (9.3) + реальные потолки моделей (10.7)
ui/               одна страница, 7 панелей, WS-обновления; ставится как PWA
db/schema.sql     tasks, runs, model_calls, tool_calls, approvals, cloud_calls,
                  agent_memory_index, projects, project_tasks, artifacts, qa_results
sandbox/          образ для run/tests: контейнер, запускаемый с --network none
compose.core.yaml надстройка над bossman-infra: Core + сеть bossman-internal (internal: true)
```

## Запуск

**На сервере** (поверх развёрнутого `bossman-infra`):

```bash
git clone <repo> /opt/bossman
cd /opt/bossman/bossman-core && docker build -t bossman-sandbox:latest sandbox/
cd /opt/bossman/bossman-infra
# в .env добавить: COMPOSE_FILE=compose.yaml:compose.server.yaml:../bossman-core/compose.core.yaml
#                  CORE_DIR=../bossman-core
docker compose up -d --build
sudo tailscale serve --bg --https=9443 http://127.0.0.1:8700   # Bossman Control с телефона
```

**Разработка на ноутбуке** (без GPU, инфраструктура — profiles=пусто или CPU-тест):

```bash
cd bossman-core
pip install -e ".[dev]"
cp .env.example .env            # адреса 127.0.0.1, SANDBOX_MODE=local
set -a; source .env; set +a
bossman serve                   # http://127.0.0.1:8700
```

Тесты (без железа и без инфраструктуры): `python -m pytest`.

CLI: `bossman task "…" [--agent coder]` ·
`bossman project plan <slug> <brief.md>` · `bossman project run <slug>` ·
`bossman project state <slug>`.

## Ключи per-агент

Каждому агенту — свой ключ LiteLLM с белым списком моделей
(`../bossman-infra/scripts/create-agent-key.sh`), в `.env` как
`LITELLM_KEY_<ИМЯ>` (например `LITELLM_KEY_FRESH_VIBES`). У агента с
`cloud_policy: never` в белом списке нет облачных алиасов — запрос в облако
отклоняется шлюзом до отправки, независимо от кода Core.

## Три уровня приватности (раздел 6)

1. **Сеть** — `compose.core.yaml` держит Core, агентов и sandbox в
   `bossman-internal` (`internal: true`); в интернет смотрит только LiteLLM.
   Sandbox для `run`/`tests` запускается с `--network none`.
2. **Ключи** — белые списки моделей per-агент в LiteLLM.
3. **Интерфейс** — `llm.py` проверяет политику до отправки: `never` → отказ,
   `ask` → предпросмотр в «Подтверждениях», каждый облачный вызов — в `cloud_calls`.

## Что отложено (по этапам ТЗ)

- Обработчики `gmail.*` / `crm.*` / `docs.read` — декларации есть, коннекторы v0.4.
- Голос (Whisper + Piper из JARVIS) — v0.4; кнопка в Пульте — заглушка.
- PII-фильтр перед облаком — открытый вопрос 3 ТЗ.
- RAG по pgvector — таблица и точка входа (`search_journal`) готовы,
  индексация подключается при поднятом `bossman-embed`.
- Эндпоинт выгрузки модели в llama-swap — сверить с актуальным README llama-swap.
