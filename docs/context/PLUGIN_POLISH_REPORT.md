# PLUGIN POLISH REPORT — truth matrix (POLISH Wave 2)

Принцип: адаптер поверх СУЩЕСТВУЮЩЕЙ authority (`REGISTRY` + `decide_effect` +
approvals + Vault + EventBus), а не второй фреймворк. Уровни зрелости:

- **CONTRACT_READY** — типизированный контракт зарегистрирован (`plugin:<id>.<cap>`).
- **POLICY_READY** — `default_effect` (auto/ask/deny) + права агента + anti-replay + credential-gate.
- **EXECUTION_IMPLEMENTED** — реальный хендлер выполняет действие (не только валидация/заглушка).
- **UNIT_VERIFIED** — детерминированные unit-тесты на реальном исполнении/границах.
- **LIVE_VERIFIED** — проверено против живого сервиса (требует хоста/креда → вечер).

Deny-капабилити (напр. `sql.write`) в манифесте ОТСУТСТВУЮТ намеренно → `resolve`
их не вернёт → DENY-by-default. Внешняя мутация/отправка → `default_effect=ask`.
Без креда → честный `SKIP_EXTERNAL_CREDENTIAL` (побочного эффекта нет, не падение).

## Матрица (13 коннекторов, 24 capability)

| Приоритет | Capability | Policy | Уровень | Реальное исполнение здесь |
|---|---|---|---|---|
| 1 | `github.repo_read` / `github.issue_create` | auto / **ask** | CONTRACT+POLICY_READY | нет креда/сети в приёмке → `SKIP_EXTERNAL_CREDENTIAL` / `NOT_TESTED_LIVE`; вечером с `GITHUB_TOKEN` |
| 2 | `obsidian.read` | auto | **EXECUTION_IMPLEMENTED + UNIT_VERIFIED** | реальное чтение внутри vault (`confine_path` + symlink-guard) |
| 2 | `obsidian.write` | **ask** | **EXECUTION_IMPLEMENTED + UNIT_VERIFIED** | реальная запись внутри vault (confined, escape заблокирован тестом) |
| 3 | `browser.open` / `browser.form_submit` | auto / **ask** | CONTRACT+POLICY_READY | реальный браузер — подсистема bossman-core (Stage 1/13); адаптер не дублирует authority; live вечером |
| 4 | `ollama.chat` | auto | CONTRACT+POLICY_READY | реальный путь — Stage 3 Gateway→Ollama (`cloud_policy=never`); адаптер не заводит второй LLM-путь; live вечером |
| 5 | `openrouter.chat` | **ask** | CONTRACT+POLICY_READY | реальный путь — Gateway + Cost Governor; `OPENROUTER_API_KEY` есть в env, но живой облачный вызов в приёмке не делаем → `NOT_TESTED_LIVE` |
| 6 | `telegram.status` / `telegram.send` | auto / **ask** | CONTRACT+POLICY_READY | реальный канал — существующая Telegram-подсистема; live вечером |
| 7 | `sql.read` | auto | **EXECUTION_IMPLEMENTED + UNIT_VERIFIED** | **реальное read-only исполнение** (`sqlite file:…?mode=ro`, single-statement guard, bounded); write заблокирован ДО и НА уровне БД (тест доказывает 0 изменений) |
| 8 | `mcp.tool_list` / `mcp.tool_call` | auto / **ask** | CONTRACT+POLICY_READY | реальный MCP — существующий `mcp_runtime` (bossman-core); неизвестный tool → DENY; live вечером |
| 9 | `n8n.workflow_list` / `n8n.workflow_run` | auto / **ask** | CONTRACT+POLICY_READY | url валидируется от SSRF; `N8N_API_KEY` отсутствует → SKIP; live вечером |
| 10 | `gmail.search` / `gmail.send` | auto / **ask** | CONTRACT+POLICY_READY | OAuth отсутствует → SKIP; `gmail.send` — destructive+ask |
| 11 | `calendar.search` / `calendar.create` | auto / **ask** | CONTRACT+POLICY_READY | OAuth отсутствует → SKIP |
| 12 | `drive.search` / `drive.write` | auto / **ask** | CONTRACT+POLICY_READY | OAuth отсутствует → SKIP |
| 13 | `http.get` / `monitor.feed` | auto | **EXECUTION_IMPLEMENTED + UNIT_VERIFIED** | реальный GET через `safe_get`: SSRF-блок приватных IP/hostname, revalidation на каждом hop, no auto-redirect, bounded body |

## Что стало «реальным исполнением» в этой волне
- **`sql.read`**: было validation-only → стало реальное read-only исполнение
  (`sqlite3.connect(f"file:{path}?mode=ro", uri=True)`), запись невозможна и на
  уровне драйвера. Гейт `sql_read_only_ok` режет multi-statement и write ДО коннекта.
- **`obsidian.write`**: было generic-заглушка → стало реальная confined-запись
  (создание родительских каталогов внутри vault, escape заблокирован).

## Безопасность плагинов (уже было, подтверждено)
`bcc/plugin_security.py`: SSRF (literal + DNS-resolve pinned IP, anti-rebinding,
per-hop revalidation, no auto-redirect), path/symlink confinement (`.resolve()`),
redaction ключей и значений секретов. Health-эндпоинт `/api/plugins` НЕ дёргает
внешние сервисы и не отдаёт сырые секреты — только `configured/missing/n/a`.

## Тесты
`command-center/tests/test_plugins_adapter.py` — **54 passed**:
политика/эффекты, deny-by-default, credential-gate, SSRF/redirect/path/symlink,
редакция, **реальный read-only SQL** (execute/write-blocked/no-dsn-skip),
**реальная Obsidian-запись** (execute/escape-blocked/no-cred-skip).

## Честный live-статус (вечерний REAL E2E)
LIVE_VERIFIED для github/ollama/openrouter/telegram/mcp/browser/n8n/gmail/calendar/
drive — по матрице `EVENING_LIVE_ACCEPTANCE.md` на реальном хосте с кредами.
Никакого fake-PASS: где нет живого вызова — метка `NOT_TESTED_LIVE` /
`SKIP_EXTERNAL_CREDENTIAL`.
