# Lane G/H — OpenRouter certification + Smart Router по verified-возможностям

Ветка `claude/bossman-control-v03-43igbk`. Ничего не коммитил, ничего не пушил.

## Изменённые файлы

| Файл | Что сделано |
|---|---|
| `bcc/v2/model_router.py` | `unsupported_capabilities` (проба провалена), `cap_state()`, `disqualify()`, `shortlist()`, `candidate_digest()`, `RouteRequest.max_candidates/require_verified`, `RouteDecision.considered/total` |
| `bcc/v2/capability_probe.py` | **фикс бага** (`c.__dict__` на `slots=True` → любая tools-проба была `verified=False`), `probe_vision`, `probe_streaming`, `probe_model()`, `ProbeResult.skipped/.verified(None)` |
| `bcc/v2/openrouter_ext.py` | `OpenRouterClient.stream_raw()` — SSE-стрим → список дельт (для streaming-пробы) |
| `bcc/v2/openrouter_catalog_service.py` | `sync()` возвращает реальный счётчик `stale` + `remote_ids` |
| `bcc/features/openrouter.py` | probe-эндпоинт: advertised берётся из каталога (`advertised_caps` + модальности), гоняются только заявленные пробы, незаявленное пишется `verified=NULL`; событие `model.capabilities_probed` |
| `bcc/features/router.py` | `_verified_caps()` читает `model_capability_checks` (последняя проба на способность) → `verified/unsupported` кандидата; `POST /api/router/candidates` (ограниченный digest); `preview` больше не отдаёт `-inf` (был бы 500 при JSON-сериализации) |
| `tests/test_v21_openrouter_router.py` | новый, 13 тестов |
| `tests/test_feat_openrouter.py` | расширен (не переписан): tools/structured verified=True, vision=None |

Файлы Lead'а (`engine.py`, `api.py`, `db.py`, `providers.py`, `tools.py`,
`approvals.py`, `pyproject.toml`, `ui/pages/index.js`) не трогал.

## Что работает (доказано тестами)

1. **Каталог**: sync сохраняет context_length, цены (нормализованы в USD/1M),
   модальности, `supported_parameters`, `architecture`, сырой JSON, `advertised_caps`.
2. **Stale**: исчезнувшая из remote модель остаётся строкой со `stale=True`;
   `/catalog` её скрывает, `?include_stale=true` показывает.
3. **Алиасы и история переживают refresh**: pinned-модель и все строки
   `model_capability_checks` целы после повторного sync; повторный pin идемпотентен.
4. **tool_calls переживают адаптер**: MockTransport-ответ OpenRouter с
   `message.tool_calls` → `ChatResult.tool_calls` с id/name/arguments/raw_arguments,
   `finish="tool_calls"`, `raw_message`, `provider_meta`. Второй виток собран
   ХЕЛПЕРАМИ ДВИЖКА (`_assistant_tool_message`/`_tool_message`) и уходит на провайдера
   с assistant.tool_calls и `role:tool` с тем же `tool_call_id`.
5. **Пробы**: chat / tools / structured_output / vision / streaming. Незаявленная
   способность → `skipped`, `verified=NULL` («не знаем»), а не `False`.
6. **advertised ≠ verified** хранятся раздельно в `model_capability_checks`.
7. **ГЛАВНОЕ ПРАВИЛО**: `advertised_tools=true` + `verified_tools=false` →
   модель ОТВЕРГНУТА для задачи с `requires={"tools"}` (причина
   `verified NOT supported: tools`), выбирается проверенная — даже если она дороже.
   Проверено и юнитом, и сквозняком через БД (`/api/router/preview`).
8. **Shortlist ограничен**: 400 кандидатов → `MAX_CANDIDATES=12` доходит до скоринга,
   `rejected` ≤ `MAX_REJECTED=24`; `POST /api/router/candidates` отдаёт compact digest
   (11 полей, без raw-метаданных) длиной ≤ `max_candidates`.

## Найденный и исправленный баг

`capability_probe.probe_tools` делал `json.dumps([c.__dict__ ...])` по
`ToolCall(slots=True)` → `AttributeError` ловился общим `except` → **любая**
tools-проба записывалась как `verified=False`. С новым правилом роутера это
заблокировало бы все tool-задачи. Старый тест ловил только факт наличия пробы
(`"tools" in caps`), не её результат — теперь проверяется значение.

## Семантика способностей (контракт для остальных лейнов)

`ModelCandidate.cap_state(cap)` → `verified` (проба OK) | `falsified` (проба FAIL,
жёсткий отказ) | `advertised` (заявлено, не проверено — пропускаем, если
`require_verified=False`) | `unknown` (отказ). В БД: `verified=True/False/NULL`.
`models.caps` остаётся ЗАЯВЛЕННЫМ; правда о проверке живёт только в
`model_capability_checks` — probe больше не перезаписывает `models.caps`.

## Тесты

- `timeout 300 pytest tests/test_v21_openrouter_router.py -q` → **13 passed**
- `timeout 500 pytest -q` → **211 passed, 1 skipped** (весь набор ветки на момент
  прогона, вместе с тестами других лейнов, которые доливались параллельно;
  регрессий нет — база 143 выросла за счёт чужих лейнов, не сломалась)

## Допущения

- Имена способностей едины для проб, каталога и роутера: `tools`, `vision`,
  `structured_output`, `streaming`, `chat`.
- `advertised_caps["streaming"]` у OpenRouter всегда `True` (каталог этого не
  различает) — streaming-проба поэтому гоняется всегда.
- «Последняя проба» = максимальный `id` в `model_capability_checks` (строки только
  добавляются, не апдейтятся).
- `require_verified` по умолчанию **False**: иначе свежедобавленная непроверенная
  модель была бы недоступна. Включается через `PATCH /api/router/rules`.

## Крючки для Integration Lead

1. **Схема не менялась** — `model_capability_checks` и `provider_catalog_models`
   уже подходят. Но `verified` должен допускать NULL (сейчас `sa.Boolean` без
   `nullable=False` — ок). Если появится Alembic: NULL здесь значим, не заменять на 0.
2. **`bcc/features/router.py` читает `bcc/v2/tables.py`** — если таблицы переедут в
   `db.py`, поправить импорт `model_capability_checks`.
3. **Правила роутера** (`settings_kv["router.rules"]`) получили два новых ключа:
   `max_candidates` (int) и `require_verified` (bool).
4. **Новый эндпоинт** `POST /api/router/candidates` — годится как источник для UI
   и как единственный безопасный вход в промпт (весь каталог туда не уедет).
5. `before_run`/`after_run` ресурс-брейна не трогал; `pick_model` возвращает тот же
   контракт `{"model_id", "route"}`, в `route` добавлены `considered`/`total_candidates`.
6. Для реальной сертификации нужен живой ключ OpenRouter: здесь всё доказано на
   `httpx.MockTransport`, сети в тестах нет (см. PARTIAL ниже).

## PARTIAL / не доказано

- **Живой OpenRouter не дёргался** (нет ключа и запрещена сеть). Все пути
  сертифицированы на MockTransport с реалистичными телами ответов.
- **Streaming в основном рантайме не используется**: `stream_raw` существует только
  для пробы; движок по-прежнему ходит нестримом через `bcc/providers.py`.
- **Vision-проба проверяет только «модель приняла image_url и ответила текстом»**, а
  не правильность распознавания (это требовало бы эталонной картинки и модели).
- **`models.caps` для pinned-модели** заполняется лишь `tools`/`vision` (как и было);
  `structured_output`/`streaming` в реестр не переносятся — роутер берёт их из проб.
