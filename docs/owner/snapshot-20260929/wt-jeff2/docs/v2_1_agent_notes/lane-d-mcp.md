# Lane D — MCP runtime (фаза D). Статус: DONE

MCP реально исполняет протокол на официальном SDK и подключён в канонический
tool-loop. Хэндроллинга JSON-RPC НЕТ.

## Изменённые/созданные файлы

| Файл | Что |
|---|---|
| `command-center/bcc/v2/mcp_runtime.py` (new, ~330) | клиентский рантайм: SDK-обёртка, соединения, health, discovery, call |
| `command-center/bcc/features/tools_mcp.py` (new, ~300) | Feature: регистрация MCP-инструментов в `bcc.tools.REGISTRY`, HTTP `/api/mcp/runtime/*`, tick-health |
| `command-center/tests/test_v21_mcp.py` (new) | 15 тестов на живом сервере |
| `command-center/tests/fixtures/mcp_echo_server.py` (new) | реальный MCP-сервер (`mcp.server.mcpserver.MCPServer`, stdio) |
| `docs/v2_1_agent_notes/lane-d-mcp.md` | этот файл |

Запрещённые файлы (`engine.py`, `api.py`, `db.py`, `tools.py`, `providers.py`,
`approvals.py`, `pyproject.toml`, `ui/pages/index.js`) НЕ трогались.

## SDK

`pip install mcp` → **mcp 2.1.1** (реально установлен в среде). Фолбэка на
самописный JSON-RPC НЕТ и не понадобился.

Важно про 2.x API (v1-гайды не подходят):
- `FastMCP` переименован в `MCPServer` (`mcp.server.mcpserver.MCPServer`);
- клиент высокого уровня — `mcp.client.client.Client(StdioServerParameters(...))`,
  async-context-manager;
- поля моделей — snake_case: `tool.input_schema`, `result.is_error`,
  `result.structured_content` (не `inputSchema`/`isError`);
- `send_ping` объявлен удалённым → health-проба сделана через
  `list_tools(cache_mode="refresh")`.

Импорт ленивый (`load_sdk()` внутри функции), модуль фичи импортирует только
`bcc.v2.mcp_runtime` — приложение стартует и без пакета `mcp`
(проверено запуском с заблокированным импортом: старт OK, `connect` отдаёт
честный `MCPUnavailable`, эндпоинты — 503, не 500).

## Архитектура соединения (почему так)

SDK построен на anyio-таскгруппах: входить и выходить из контекста обязана ОДНА
и та же задача. Поэтому на сервер заводится одна задача-водитель
(`_Connection._run`), которая владеет `async with Client(...)`, а `connect/
list_tools/call_tool/disconnect` кладут запрос в `asyncio.Queue` и получают
ответ через future. Значит, вызывать рантайм можно откуда угодно (HTTP-хэндлер,
хэндлер инструмента внутри воркера, tick фичи) без нарушения контракта anyio.

Реализовано: connect, disconnect, health (`probe` — живой `tools/list` мимо
кэша), list tools / resources / prompts, refresh discovery, call tool,
per-call timeout (`asyncio.wait_for`), обработка ошибок. Discovery пишется в
существующую таблицу `mcp_tools` (схему не менял).

## Подключение к канонике

- Имя: `bcc.v2.mcp_hub.namespaced_tool` → `mcp:<server>:<tool>`;
  имя для модели — `mcp_echo_echo` (через `ToolSpec.api_name`).
- `ToolSpec(source="mcp", external_output=True, category="exec",
  idempotent=False)`, `input_schema` = `properties` схемы сервера,
  `required` — оттуда же. Результат приходит с шапкой «внешние данные».
- Права — ТОЛЬКО канонический слой: `decide_effect` + `agents.permissions.
  tool_rules`. Своей системы согласований нет.
- `default_effect` инструмента берётся из УЖЕ существующего ключа настроек
  `mcp.policy` (его пишет `POST /api/mcp/policy` из `features/skills.py`),
  по умолчанию `ask`. Так политика хаба и канонический слой — одно целое.
- ASK идёт обычным путём движка: approval → `waiting_approval` → после
  одобрения исполняется ровно один раз (доказано файлом-счётчиком на стороне
  сервера, не изнутри процесса).

### Контекст-эффективность

Реестр — это КАТАЛОГ; фильтрация выдачи делает существующий
`allowed_tools_for(task, agent)` → `REGISTRY.resolve(...)`. Поэтому
`mcp:echo:secret` зарегистрирован, но в схемы, уходящие провайдеру, НЕ попадает
(тест `test_unassigned_mcp_tool_never_reaches_model` проверяет и то, что
секретный инструмент в реестре есть, и что в `tools`-payload его нет).

## HTTP API (коллизий с `features/skills.py` нет)

В `skills.py` уже заняты `GET/POST /mcp/servers`, `DELETE /mcp/servers/{id}`,
`GET /mcp/tools`, `POST /mcp/policy` — поэтому всё моё под `/mcp/runtime/*`:

```
GET  /api/mcp/runtime                       статус SDK + серверов + список mcp:*-инструментов
POST /api/mcp/runtime/servers/{ref}/connect|disconnect|refresh|call
GET  /api/mcp/runtime/servers/{ref}/health|tools|resources|prompts
```
`{ref}` — числовой id или имя сервера. `refresh` = discovery + persist + перерегистрация.

## Падение сервера

Транспорт помер → соединение помечается `unhealthy`, в БД (`mcp_servers.status`,
`status_detail`, `last_check`) и в шину:
- `mcp.unhealthy` (виден в Activity, пишется в таблицу `events`);
- `mcp.call_failed` — сигнал Governor/Self-Healing;
- плюс обычный `tool.called ok=False` от движка и `tool_calls.status='error'`.
Для модели это ДАННЫЕ: run не падает. `tick` фичи (30 с) переопрашивает
подключённые серверы.

## Тесты

`cd command-center && timeout 300 python -u -m pytest tests/test_v21_mcp.py -q`
→ **15 passed** (~17 с). Полный прогон `timeout 400 python -u -m pytest -q`
→ **173 passed** (в дереве параллельно работают другие лейны; мои +15,
регрессий нет; базовые 129 на момент старта проходили).

Покрыто требуемое 1–9: старт/connect; discovery+persist (+ проверка схемы);
невыданный инструмент отсутствует в контексте модели; выданный присутствует;
AUTO исполняется и возвращает настоящий `эхо: привет`; ASK создаёт approval;
одобренный вызов исполняется РОВНО один раз (счётчик серверного процесса = 1);
DENY не доходит до сервера (счётчик = 0); падение сервера → unhealthy + события.
Плюс: disconnect, restore реестра после рестарта без запуска процессов,
404 на несуществующий сервер, 503 на неподнимающийся сервер, ручной вызов.

Тесты помечены `skipif(not sdk_available())` — без пакета `mcp` они
пропускаются, а не падают.

## Что нужно от Integration Lead

1. **`pyproject.toml`** — добавить в `dependencies` (я его не трогаю):
   `"mcp>=2.1,<3"`. Сейчас пакет установлен вручную (`pip install mcp
   --ignore-installed PyJWT`: системный debian-PyJWT 2.7 без RECORD мешал
   апгрейду). Установка подтянула/обновила транзитивно: `pydantic 2.13.4`,
   `starlette 1.6.0`, `anyio 4.14.2`, `httpx 2.12` (как `httpx2`),
   `jsonschema`, `sse-starlette`, `uvicorn 0.52`. Полный набор тестов после
   этого зелёный, но зафиксировать версии в pyproject стоит осознанно.
2. **UI**: страницы под `/api/mcp/runtime/*` я не делал (не мой файл
   `ui/pages/index.js`); данных для карточки сервера (status/detail/tools)
   достаточно в `GET /api/mcp/runtime`.
3. `POST /api/mcp/servers` (в `skills.py`) не умеет `cwd`/`env_keys` — рантайм
   их поддерживает. Мелкое расширение того эндпоинта пригодится реальным
   серверам с ключами API. Тест вставляет строку напрямую в БД.

## Ограничения / PARTIAL

- **Транспорт `http`/streamable-HTTP — PARTIAL (не реализован).** Рантайм
  поддерживает только `stdio`; при `transport="http"` соединение честно
  отвечает `MCPUnavailable("транспорт http пока не поддержан")`. Причина:
  в среде нет реального удалённого MCP-сервера, а доказывать HTTP-транспорт
  мок-сервером против правила «никаких моков своего кода» я не стал.
  Каркас (`_Connection._run`) расширяется одной веткой на
  `streamablehttp_client`.
- Sampling/elicitation/roots-колбэки клиента не подключены (сервер не может
  попросить модель) — вне рамок фазы D.
- Автоподключение серверов на старте отключено намеренно: `setup()` только
  поднимает инструменты из `mcp_tools` в реестр, процесс сервера стартует
  лениво при первом вызове (`MCPRuntime.ensure`), иначе рестарт CC порождал бы
  N дочерних процессов.
