# AI Gateway (ЭТАП 3) — аудит интеграции

**Дата:** 2026-08-29
**Ветка:** claude/bossman-control-v03-43igbk
**База интеграции:** `bossman-core` (ТЗ v0.3), НЕ `command-center`.
**Предыдущий этап:** ComputerUse V1.2 (`e98132a`).

## Решение о размещении

В репозитории два ядра. `command-center/bcc` (дашборд V2.2) не тронут. ZIP
написан под реестр и LLM-слой `bossman-core`: пакет ложится в
`bossman-core/bossman/gateway/`, а адаптер встраивается в существующий
`bossman/llm.py` — единственное место, где ядро ходит к моделям. Второй
сетевой архитектуры рядом не построено: Gateway стал штатной точкой выхода
за тем же OpenAI-совместимым `/chat/completions`, за которым раньше стоял
только LiteLLM.

Принцип REUSE → ADAPT → WRITE NEW: пакет `gateway/` (app, auth, backends,
client, config, router, telemetry, main) взят из ZIP как есть; адаптирован
`llm.py` (ветка выбора транспорта); написан новый тест адаптера.

## Файлы

| Файл | Что сделано |
|---|---|
| `bossman-core/bossman/gateway/` | новый пакет из ZIP: FastAPI-шлюз, роутер моделей, backends, auth, client, telemetry, main (не менялся) |
| `bossman-core/bossman/llm.py` | адаптер: `chat()` и `vision_caption()` идут через Gateway при заданном `BOSSMAN_GATEWAY_URL`; ленивый `GatewayClient`, `aclose_gateway()` |
| `bossman-core/bossman/config.py` | настройки `gateway_url` / `gateway_core_key` (из окружения, по умолчанию пусто) |
| `bossman-core/bossman/api.py` | `aclose_gateway()` подключён к shutdown сервиса — без осиротевших HTTP-клиентов |
| `bossman-core/pyproject.toml` | console-script `bossman-gateway`, опциональный extra `resource=[psutil]` |
| `bossman-core/.gitignore` | боевой `config/gateway.yaml` / `gateway.yaml` исключены |
| `bossman-core/.env.example` | `BOSSMAN_GATEWAY_URL` / `BOSSMAN_GATEWAY_CORE_KEY` (без значений) |
| `bossman-core/config/gateway.example.yaml` | пример из ZIP: только плейсхолдеры и `*_env` |
| `bossman-core/tests/test_gateway*_stage3.py` | штатные тесты ZIP (9) |
| `bossman-core/tests/test_llm_gateway_stage3.py` | новый тест адаптера (2) |
| `bossman-core/docs/stage-3/` | доки ЭТАПА 3 из ZIP |

## Точка интеграции в реальном llm.py

Ветка выбора транспорта — `bossman-core/bossman/llm.py:99` внутри `chat()`
(объявлена на `:79`):

```python
if settings.gateway_url:                         # bossman/llm.py:99
    data = await _gateway_client().chat(         # :100  → приватный Gateway
        model=alias, messages=messages, tools=tools, max_tokens=max_tokens)
else:
    key = agent.api_key or settings.litellm_master_key   # прежний путь к LiteLLM
    ...
```

Облачная политика (`never`/`ask`) проверяется ВЫШЕ этой ветки (`:88-92`) —
до любой сети. Учёт `model_calls` / `cloud_calls` идёт после и не изменён:
ответ Gateway OpenAI-совместим. `vision_caption()` через Gateway запрашивает
capability-алиас `bossman-vision` (`bossman/llm.py:154-156`). Клиент Gateway —
переиспользуемый singleton (`_gateway_client`, `:28`), закрывается в
`api.py:58-63` (shutdown).

Флаг активации: пустой `BOSSMAN_GATEWAY_URL` => прежнее поведение (напрямую
к LiteLLM ключом агента). Значит все существующие тесты и боевой путь до
явного включения Gateway не меняются.

## Security

Раздельные границы доверия и обращение с ключами.

- **GW-SEC-001 — ядро аутентифицируется в Gateway ключом ядра, не ключом
  провайдера/агента.** Адаптер шлёт `Authorization: Bearer
  {BOSSMAN_GATEWAY_CORE_KEY}` (`gateway/client.py:20`); идентичность и права
  агента остаются в Core. Провайдерские ключи живут только на стороне
  backend'ов Gateway (`backends.py:38-40`, из окружения). Статус: реализовано,
  покрыто тестом (`test_chat_routes_through_gateway_when_configured`
  проверяет, что уходит именно ключ ядра, а не `agent.api_key`).
- **GW-RT-001 — Gateway НЕ обходит облачную политику агента.** Generic-fallback
  Gateway провайдер-агностичен, но `is_cloud(alias)`-гейт Core срабатывает до
  маршрутизации: агенту `cloud_policy=never` облачный алиас отбивается
  `CloudDenied` до сети (`test_gateway_does_not_bypass_cloud_policy` — в mock
  ничего не уходит). Безопасный паттерн из `docs/stage-3/BOSSMAN_LLM_ADAPTER.md`
  соблюдён: облако остаётся за политикой/подтверждением Core, OpenRouter не
  подставлен молча под `bossman-smart`.
- **GW-SEC-002 — ключи и токены не логируются и не отдаются наружу.** В пакете
  Gateway нет `print`/`logger`/`logging` (единственный `log_level="info"` —
  уровень uvicorn). Клиентские ключи хранятся как sha256-хэши и сравниваются
  `hmac.compare_digest` (`auth.py:44-49,62-67`) — плейнтекст не хранится.
  Наружу ответы несут только `x-bossman-backend` и `x-bossman-route-model`
  (имя backend'а и модели маршрута), не учётные данные.
- **GW-SEC-003 — секретов в git и в ZIP нет.** Скан ZIP и добавленных файлов
  по `sk-…/AKIA…/ghp_…/xox…/AIza…/PRIVATE KEY` — 0 совпадений.
  `gateway.example.yaml` использует только `key_env`/`api_key_env` и
  `REPLACE_WITH_*`. Боевой `config/gateway.yaml` — в `.gitignore`
  (проверено `git check-ignore`); пример остаётся под версией.
  **Скомпрометированных секретов не обнаружено.**
- **GW-SEC-004 — Gateway слушает loopback, наружу не публикуется.** `bind_host`
  по умолчанию `127.0.0.1` (`gateway/config.py:67`), docker-пример биндит
  порт на `127.0.0.1`. Телефонный клиент — позже, за приватной сетью +
  device-enrollment + короткоживущими сессиями; в ЭТАПЕ 3 это НЕ строится.
- **GW-RT-002 — capability-маршрутизация и алиас-изоляция.** Клиент просит
  алиас (`bossman-vision` требует `vision`), не сырую модель провайдера; роутер
  отдаёт цели по приоритету и здоровью с упорядоченным fallback; наружу
  выставляется алиас, а не backend-модель. Покрыто
  `test_vision_requires_vision_capability`, `test_priority_and_capabilities`,
  `test_alias_is_exposed_not_backend_model_and_fallback_works`,
  `test_unknown_alias_rejected`.

**Рекомендация (низкая):** при HTTP≥400 backend Gateway отдаёт клиенту тело
апстрима, усечённое до 1000 символов (`backends.py:61`, попадает в 502
`attempts`). Тела апстримов Ollama/LM Studio/OpenRouter учётных данных не
содержат, но если апстрим сконфигурирован эхом заголовков — риск утечки.
Санитайзинг ответов апстрима — кандидат в ЭТАП 3.1.

## Тесты

`cd bossman-core && python -m pytest -q` с настоящим Chromium
(`BOSSMAN_TEST_CHROMIUM=/opt/pw-browsers/chromium-1194/chrome-linux/chrome`):
**48 passed** (было 37).

Прибавка +11: 9 штатных тестов Gateway (`test_gateway_stage3.py` — auth,
alias-изоляция, fallback, vision-capability, метрики/токены, body-limit,
streaming; `test_gateway_router_stage3.py` — приоритет/способности;
`test_gateway_client_stage3.py` — chat/embeddings клиента) + 2 теста адаптера
`llm.py`.

Без `BOSSMAN_TEST_CHROMIUM` два браузерных E2E ЭТАПА 1
(`test_browser_emulator_e2e.py`) не запускаются — бинарь Chromium в этой среде
не скачать (прокси блокирует `cdn.playwright.dev`, 403). С указанием на уже
установленный `/opt/pw-browsers/chromium-1194` они зелёные; это ограничение
среды, не кода, и кода ЭТАПА 1 правка не касалась.

Тесты в сеть не ходят: апстримы — `httpx.MockTransport`, реальные вызовы к
платным провайдерам за флагом окружения и без ключей пропускаются, а не падают.

## Ограничения / NOT RUN

- **Context Engine embeddings через `bossman-embed` — НЕ подключены (граница).**
  ЭТАП 2.222 (`context_engine/`) делает другой агент и в этой ветке ещё не
  появился; его каталог трогать/создавать нельзя. Алиас `bossman-embed` и
  `GatewayClient.embeddings()` готовы; проводка — когда появится context_engine.
- **Concurrency-лимит и queue-timeout** реализованы (семафор `max_concurrency`
  на backend + `asyncio.wait_for(queue_timeout_seconds)` в `app.py`), но
  отдельным нагрузочным тестом на превышение лимита НЕ проверялись (NOT RUN).
- **Streaming mid-flight fallback:** логика «после первых байт не подменять
  модель, а завершить ошибкой» присутствует (`app.py:136-139`, флаг `emitted`);
  passthrough покрыт тестом, а отдельный тест на обрыв апстрима в середине
  стрима НЕ добавлен (NOT RUN).
- **Развёртывание:** в `compose.core.yaml` Core сидит в `bossman-internal` без
  интернета (наружу смотрит только LiteLLM). Gateway с облачным fallback
  (OpenRouter) потребует доступа в интернет — это решение сети/деплоя для
  ЭТАПА 3.1, в код ядра не вносилось. Пример сервиса — `docs/stage-3/
  DOCKER_SERVICE_EXAMPLE.md`.
- **Реальный vision-инференс и живой прогон против Ollama/LM Studio** — за
  флагом и ключами, NOT RUN (нет локальных рантаймов в среде).

## Вердикт

**AI GATEWAY STAGE 3 — PASS (фундамент).** Пакет встроен в штатный LLM-путь
ядра, облачная политика не обойдена, ключи ядра и провайдеров разделены и не
логируются, секретов в git/ZIP нет, боевой конфиг игнорируется. Полный набор
тестов зелёный (48). Телефонная модель доверия и Resource Brain — следующие
этапы; здесь заложены точки расширения, не заявлено их завершение.
