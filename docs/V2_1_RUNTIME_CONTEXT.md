# V2.1 — Runtime Context (рабочая память сессии)

Это компактный контекст для V2.1. **Основной источник правды для агентов этой
волны.** Большие файлы повторно не открывать — сверяться отсюда, читать точечно
по указанным символам/строкам.

- Ветка: `claude/bossman-control-v03-43igbk`
- База: коммит `d2bc5f7` (Workflow Builder), тесты **115 passed**
- Дата среза: 28.08.2026

## 1. Канонические сущности (что уже есть в БД)

`bcc/db.py` (единственный владелец схемы, 431 строка):

| Таблица | Ключевое |
|---|---|
| `providers` / `models` | `alias`, `kind` local\|cloud, `caps` JSON, `bench`, `status` |
| `agents` | `system_prompt`, `model_id`, `fallback_model_id`, `tools` JSON (**пусто — не используется**), `permissions` JSON, `max_steps`, `max_tokens`, `workspace` |
| `tasks` | `status`, `kind`, `mission_id`, `orchestra_id`, `skill_version_id`, `parent_task_id`, `workspace_path`, `meta` JSON |
| `task_runs` | `attempt`, `status`, `worker_lease_until`, `checkpoint` JSON, `result`, `error`, `model_alias`, `tokens_in/out`, `cost_usd`, `route` JSON, `reservation_id` |
| `run_events` | лог run'а (`level`, `kind`, `message`, `data`) |
| `approvals` | `kind`, `preview`, `status` pending\|approved\|rejected, `task_id`, `run_id` |
| `checkpoints` | `run_id`, `step`, `messages` JSON — история для Replay/Fork |
| `missions`, `kpi_history`, `orchestras`, `orchestra_members`, `skills`, `skill_versions`, `benchmarks`, `session_forks`, `resource_reservations`, `interventions`, `recovery_attempts` | V2 |
| `bcc/v2/tables.py` | 9 пак-таблиц на той же `metadata`: `terminal_sessions`, `browser_sessions`, `mcp_servers`, `mcp_tools`, `opencode_sessions`, `evaluations`, `openrouter_models`, `model_capabilities`, `nl_orchestrations` |

Новые колонки существующих таблиц — только через `V2_NEW_COLUMNS` +
идемпотентный `_migrate()`. **Владелец схемы один — Integration Lead.**

## 2. Текущий путь вызова модели (ГЛАВНЫЙ ПРОБЕЛ)

`bcc/engine.py::_run` (строки 346–383):

```
messages = [system, user]
while step < max_steps:
    result, model = await self._call_model(...)   # ChatResult(text, tokens, finish)
    messages.append({"role":"assistant","content": result.text})
    checkpoint + on_step hook
    # строка 381: «у MVP-агента нет инструментов»
break на следующем витке (messages[-1].role == assistant)
```

`bcc/providers.py::OpenAICompatAdapter.chat` (стр. 106–126) **выбрасывает
`message.tool_calls`** — берёт только `content`. `AnthropicAdapter.chat`
(стр. 152–181) склеивает только `type=="text"` блоки. `ChatResult` не имеет поля
для tool-calls.

**Уже готово, но не подключено:** `bcc/v2/tool_messages.py` — `ToolCall`,
`AssistantTurn`, `parse_openai_chat_response()`.

## 3. Путь approvals

`bcc/approvals.py`: `create(kind, preview, task_id, run_id)` → строка +
`approval.created`; `decide(id, approve, by)` — идемпотентен (второй вызов
ничего не меняет). **Нет `wait()`** — в отличие от `bossman-core`.

Ограничение движка: воркер не должен блокироваться ожиданием человека
(пул из N слотов). Поэтому ASK реализуется не блокировкой, а
**сохранением состояния**: checkpoint с `pending_tool_call` → задача
`waiting_approval` → решение человека возвращает run в очередь.

`bcc/permissions.py` — список прав агента (`agent_allowed`, `needs_approval`).
`bcc/v2/permissions.py` — `PermissionPolicy` (fnmatch, последнее правило
побеждает, `safe_default()` с deny на wallet/`.env`/id_rsa).

## 4. Terminal

- `bcc/v2/terminal_control.py`: `TerminalPolicy.decision(cmd, cwd)` →
  auto/ask/deny (DANGEROUS/ASK/AUTO регэкспы), `TerminalManager.start/status/
  write_stdin/kill`. sandbox = `docker run --network none -v cwd:/work`.
- `bcc/features/terminal.py`: HTTP `/terminal/preview|run|roots|sessions/*`.
  ASK → 202 + `approval_id`. Корни — `settings_kv["terminal.roots"]`.
- **Пробел:** это только HTTP для человека. Модель вызвать терминал не может.
- Среда: `docker` В НАЛИЧИИ (`/usr/bin/docker`), но в контейнере разработки
  запуск контейнеров может быть недоступен — проверять, не выдавать желаемое.

## 5. Browser

- `bcc/v2/browser_control.py` (388 строк): Playwright DOM-first,
  `BrowserManager`, политика AUTO/ASK/DENY, Human Take Over.
- `bcc/features/browser.py`: HTTP `/browser/*`, `_patch_executable()` →
  `/opt/pw-browsers/chromium`, 409 при takeover, 202+approval на ASK.
- **Пробел:** модель не может вызвать браузер (нет tool-схем).

## 6. MCP

- `bcc/v2/mcp_hub.py` — **только контракт имён** (`mcp:<server>:<tool>`,
  `MCPServerSpec`, `MCPToolView`) + прямой запрет хэндроллить JSON-RPC.
- `bcc/features/skills.py` — реестр серверов и политика в БД (`mcp_servers`,
  `mcp_tools`), без исполнения протокола.
- **Пробел:** connect/discover/call отсутствуют. SDK `mcp` не установлен, но
  ставится (`pip download mcp` → 2.1.1 OK, сеть есть).

## 7. OpenCode

- `bcc/v2/opencode_bridge.py` (67 строк) + `bcc/features/opencode.py`:
  health/attach/abort/diff, честный `unavailable` вместо 500.
- Бинаря `opencode` в среде НЕТ (`which opencode` пусто) → реальный E2E тут
  невозможен; нужен детерминированный fake-сервер + отдельная host-smoke метка.

## 8. Память / Obsidian

- Аддон лежит НЕраспакованным: `BOSSMAN_MEMORY_RAG_OBSIDIAN_ADDON.zip`
  (16 файлов: `bcc/v2/memory/{obsidian,memsearch_bridge,context_pack,reranker,
  service}.py`, 3 скилла, тест).
- `MemSearchBridge` — CLI-обёртка над внешним `memsearch` (Milvus/ONNX).
  **В среде его нет и не будет по умолчанию** → нужен встроенный локальный
  backend (BM25 + опциональные dense-эмбеддинги), а memsearch остаётся
  опциональным. `ObsidianVault` уже безопасен (write только в
  `BOSSMAN Memory/`, excludes `.obsidian/.trash/.git`).
- `numpy`, `sentence-transformers`, `rank_bm25` НЕ установлены.

## 9. OpenRouter

- `bcc/v2/openrouter_ext.py`, `openrouter_catalog_service.py`,
  `capability_probe.py`; `bcc/features/openrouter.py` — sync/catalog/pin/probe.
- Таблицы `openrouter_models`, `model_capabilities` (advertised vs verified).
- **Пробел:** роутер не обязан смотреть на verified; tool-calls через адаптер не
  сохранялись вовсе (см. §2).

## 10. Роутер и ресурсы

- `bcc/v2/model_router.py` (94) + `bcc/features/router.py`: хук `pick_model`,
  кандидаты из моделей+health+bench+историч. успеха, пишет `task_runs.route`.
- `bcc/v2/resource_brain.py` + `features/resources.py`: `before_run`/`after_run`,
  `enforce=False` по умолчанию (иначе душит обычные задачи).

## 11. Тесты (сейчас 115)

`command-center/tests/`: `test_api, test_discovery, test_engine_stop,
test_feat_bench_opencode, test_feat_browser, test_feat_governor_review,
test_feat_missions, test_feat_nl_orchestra, test_feat_openrouter,
test_feat_res_fork_heal, test_feat_router, test_feat_skills,
test_feat_terminal_map, test_feat_workflow, test_persistence, test_providers,
test_queue_retry, test_scheduler, test_v2_core, test_worker_pool`.

Запуск: `cd command-center && timeout 400 python -u -m pytest -q`
(харнесс иногда даёт exit 144 без timeout — всегда через `timeout`).

Фикстура `env` (tests/conftest.py): приложение на временной SQLite,
`start_workers=False`, `FakeAdapter`, `wait_for()`.

## 12. Точные пробелы V2.1

| # | Пробел | Владелец |
|---|---|---|
| G1 | Нет канонического tool-loop в движке | Lead |
| G2 | Адаптеры теряют `tool_calls` | Lead |
| G3 | Нет канонического Tool Registry | Lead |
| G4 | ASK-approval не связан с конкретным вызовом (нет anti-replay) | Lead |
| G5 | Terminal не вызывается моделью | Lane 2 |
| G6 | Browser не вызывается моделью | Lane 2 |
| G7 | MCP не исполняет протокол | Lane 2 |
| G8 | Memory/Obsidian не интегрирован, нужен локальный backend | Lane 3 |
| G9 | Skills не отдают модели только свои инструменты | Lane 3 |
| G10 | OpenCode E2E не доказан (нет бинаря) | Lane 4 |
| G11 | Роутер не учитывает verified-возможности | Lane 5 |
| G12 | Auth: токен в localStorage + в query WS | Lane 6 |
| G13 | Нет снапшота/отката | Lane 6 |
| G14 | Нет миграций (Alembic) | Lead |
| G15 | Документация противоречива (13/14/15 из 15) | Lane 6 |

## 13. Владение файлами (чтобы не конфликтовать)

**Только Integration Lead:** `bcc/engine.py`, `bcc/api.py`, `bcc/db.py`,
`bcc/providers.py`, `bcc/tools.py`, `bcc/approvals.py`, миграции,
`ui/pages/index.js`, `pyproject.toml`.

**Лейны:** свой модуль в `bcc/features/<name>.py`, свой в `bcc/v2/…`, свой
тест `tests/test_v21_<name>.py`, своя страница `ui/pages/<name>.js`.

Заметки лейнов: `docs/v2_1_agent_notes/<agent>.md` (≤150 строк).

## 14. Целевой контракт tool-loop (фиксируется Lead'ом)

```python
# bcc/tools.py
ToolSpec(name, description, input_schema, category, permission, source,
         handler, timeout_seconds, idempotent, dangerous)
ToolContext(svc, task, run_id, agent, workspace, step)
ToolResult(content, one_line, truncated, more, error, data)
ToolRegistry.register(spec) / get(name) / schemas_for(names) / decide(...)
```

- Каноническое имя: `terminal.run`, `browser.open`, `memory.search`,
  `mcp:<server>:<tool>`, `opencode.session.start`.
- Имя для модели: точки → `__` (OpenAI не любит точки), обратное отображение
  в реестре.
- Решение: `AUTO` → выполнить; `ASK` → approval + `waiting_approval`;
  `DENY` → результат-отказ модели (не падение run'а).
- Anti-replay: `tool_calls.args_hash` + статус; одобренный вызов исполняется
  РОВНО один раз.
- Модель НИКОГДА не может выставить `approved=true` — поле берётся только из
  строки approvals.

## 15. Прогресс

Статус обновляется по мере выполнения. **Пока фаза не проверена тестом —
она TODO, а не DONE.**

| Фаза | Статус |
|---|---|
| 0 Контекст | DONE (этот файл) |
| A Канонический tool-loop | TODO |
| B Terminal-инструменты | TODO |
| C Browser-инструменты | TODO |
| D MCP runtime | TODO |
| E Memory/Obsidian | TODO |
| F OpenCode E2E | TODO |
| G OpenRouter certification | TODO |
| H Router + Resource Brain | TODO |
| I Governor/Healing на реальных инструментах | TODO |
| J Reviewer Gate на реальной кодовой задаче | TODO |
| K Skills → реальные инструменты | TODO |
| L NL-компилятор прав | TODO |
| M Mobile/операторский UX | TODO |
| N Auth hardening | TODO |
| O Snapshot/Rollback | TODO |
| P n8n-мост | PARTIAL (Workflow Builder + `/api/workflow/*` уже есть, коммит d2bc5f7) |

Итоги пишутся в: `docs/V2_1_FINAL_SCORECARD.md`, `docs/V2_1_E2E_PROOF.md`,
`docs/V2_1_SECURITY_REPORT.md`, `docs/V2_1_IMPLEMENTATION_REPORT.md`.
