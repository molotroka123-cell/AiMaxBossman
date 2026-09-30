# PLUGINS ADAPTER INTEGRATION REPORT

Дата: 2026-08-30 · Ветвь: `claude/bossman-control-v03-43igbk`
START_HEAD: `3d04e66`

## Принцип: адаптеры поверх существующей authority, без второго фреймворка

Коннекторы подключены как capability-адаптеры в СУЩЕСТВУЮЩИЕ подсистемы Command
Center — второго реестра/политики/approval/secret-store/event-bus/браузера/
Telegram/Gateway НЕ создавалось:

| Обязанность | Что переиспользовано (существующее) |
|---|---|
| Реестр инструментов | `bcc.tools.REGISTRY` (ToolSpec) — те же, что у всех фич |
| Политика ALLOW/ASK/DENY | `bcc.tools.decide_effect` (default_effect + права агента + хук) |
| Approvals / anti-replay | движок задач: ASK → очередь подтверждений, `idempotent=False` → не переигрывается |
| Секреты | `svc.vault` (Fernet) + env; наружу только `configured/missing`, маска `…last4` |
| Audit | существующая шина `svc.bus` через движок инструментов |
| MCP | существующий `bcc.v2.mcp_runtime` |
| Браузер | существующая browser-подсистема |
| Telegram | существующий канал (`channel.send`) |
| Облачный LLM | существующий провайдер-путь + Cost Governor (authority ядра) |

Новые файлы (3, command-center):
- `bcc/plugin_security.py` — hardened SSRF/path/redaction (закрывает F1/F2 аудита).
- `bcc/features/plugins.py` — манифест 13 коннекторов + регистрация в REGISTRY + `/api/plugins`.
- `tests/test_plugins_adapter.py` — 48 targeted-тестов.

## Типизированные контракты

`Capability(plugin_id, capability, scope, risk, destructive, permission,
credential_ref, network_targets, input_schema, required)`. Имя инструмента —
`plugin:<id>.<capability>`. risk=deny-капабилити (напр. `sql.write`) НЕ
регистрируются вовсе → resolve их не вернёт → DENY по умолчанию.

## Матрица коннекторов

| PLUGIN | CAPABILITY | POLICY | PERMISSION | CREDENTIAL | NOTES |
|---|---|---|---|---|---|
| github | repo_read | auto | — | GITHUB_TOKEN | read-only |
| github | issue_create | ask | — | GITHUB_TOKEN | внешняя запись |
| gmail | search | auto | — | GMAIL_OAUTH | read |
| gmail | send | ask | email.send | GMAIL_OAUTH | destructive, не переигрывается |
| calendar | search | auto | — | GOOGLE_OAUTH | read |
| calendar | create | ask | — | GOOGLE_OAUTH | внешняя запись |
| drive | search | auto | — | GOOGLE_OAUTH | read |
| drive | write | ask | — | GOOGLE_OAUTH | внешняя запись |
| telegram | status | auto | — | TELEGRAM_BOT_TOKEN | read |
| telegram | send | ask | channel.send | TELEGRAM_BOT_TOKEN | существующий канал |
| n8n | workflow_list | auto | — | N8N_API_KEY | read |
| n8n | workflow_run | ask | — | N8N_API_KEY | url валидируется от SSRF |
| obsidian | read | auto | filesystem.read | OBSIDIAN_VAULT | path-confined |
| obsidian | write | ask | filesystem.write | OBSIDIAN_VAULT | path-confined, ASK |
| browser | open | auto | browser.read | — | существующий браузер |
| browser | form_submit | ask | browser.control | — | sensitive submit → ASK |
| http | get | auto | — | — | SSRF-safe (resolve+redirect) |
| monitor | feed | auto | — | — | SSRF-safe |
| sql | read | auto | — | SQL_PLUGIN_DSN | только read-only |
| mcp | tool_list | auto | — | — | существующий runtime |
| mcp | tool_call | ask | — | — | неизвестный tool → DENY |
| ollama | chat | auto | — | — | локально, cloud_policy=never |
| openrouter | chat | ask | — | OPENROUTER_API_KEY | Gateway + Cost Governor authority |

Итого зарегистрировано: 23 capability. `sql.write` и любые не описанные —
отсутствуют → DENY.

## Границы (проверено тестами)
- **Ollama** — capability объявлена `llm.local`, `cloud_policy=never` (нет облачных вызовов).
- **OpenRouter** — `ask`, авторитет по деньгам остаётся у существующего Cost Governor ядра
  (unit-инварианты Cost Governor — 24 теста в bossman-core, зелёные).
- **MCP** — `tool_call`=ask + неизвестный инструмент не зарегистрирован → DENY; метаданные
  MCP авторитета не дают.
- **SQL** — только read-only форма; write-капабилити не существует.
- **Browser/Telegram** — переиспользуют существующие подсистемы; sensitive submit / send = ASK.

## Безопасность по умолчанию
- inert без выдачи: `REGISTRY.resolve(None) == []` — агент без явной выдачи plugin-инструментов не получает.
- unknown capability → DENY (отсутствие в реестре).
- external write/send → ASK (default_effect); destructive → не переигрывается (anti-replay).
- нет креда → `SKIP_EXTERNAL_CREDENTIAL`, побочного эффекта нет.

## FILES_CHANGED
```
command-center/bcc/plugin_security.py        (new)
command-center/bcc/features/plugins.py       (new)
command-center/tests/test_plugins_adapter.py (new)
```

## TEST_COUNTS
- plugin targeted: **48 passed**.
- command-center full: **481 passed / 2 skipped** (было 433/2; +48, 0 регрессий).
- bossman-core full: **906 passed / 4 skipped** (не затронут).
- secret scan: **PASS**.
