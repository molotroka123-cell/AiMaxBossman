# SKILLS + PLUGINS BUNDLE V1 — FINAL BUG CHECK / SECURITY AUDIT

Дата: 2026-08-30 · HEAD: `4e72785` · Bundle: `BOSSMAN_SKILLS_AND_PLUGINS_BUNDLE_V1.zip`

Метод: распаковка в scratchpad (вне git), чтение исходников, прогон собственных тестов
пакетов, целевой security-разбор. Пакеты НЕ вендорились в репозиторий (см. интеграционные
отчёты), поэтому «FIX_COMMIT» для находок пакета — рекомендация к моменту адаптерной
интеграции, а не правка в этом репо.

Формат: FINDING → FOUND_BY → ROOT_CAUSE → SEVERITY → RECOMMENDATION → VERIFIED_BY.

## Собственные тесты пакетов
- skills_pack: **8 passed** (`pytest`).
- plugins_pack: **10 passed** (`pytest`).

## Политика возможностей (проверено по manifests.py + manager.py)
Совпадает с требуемой таблицей по умолчанию:
- github: repo.read=ALLOW; issue.create/pr.create=ASK; pr.merge=ASK+destructive.
- gmail: search/read/draft=ALLOW; send=ASK+destructive.
- calendar: search=ALLOW; create/update=ASK; delete=ASK+destructive.
- drive: read=ALLOW; create/update=ASK.
- telegram: status.read=ALLOW; message.send=ASK.
- n8n: list/execution.read=ALLOW; workflow.run=ASK.
- obsidian: note.read=ALLOW; note.write=ASK.
- browser: page.open/read=ALLOW; form.fill/download=ASK.
- sql: query.read=ALLOW (запись как capability отсутствует → unknown → DENY).
- ollama: models.list/chat=ALLOW (адаптер шлёт `cloud_policy=never`).
- openrouter: models.list=ALLOW; chat=ASK (облако → approval + Cost Governor).
- mcp: server.list/tool.list=ALLOW; tool.call=ASK; неизвестный tool → `registry.resolve` бросает → DENY.
- `PluginManager.call`: DENY→PermissionError (audited, без выполнения); ASK→требует approval;
  ERROR→state=DEGRADED; выключенный плагин mid-flight → RuntimeError до выполнения. **Fail-closed.**

## Находки

### F1 — Plugins `validate_http_url` слабее, чем skills `safe_http_url`
- FOUND_BY: сравнение `plugins/security.py` vs `skills/security.py`.
- ROOT_CAUSE: plugins-версия проверяет только литеральный IP и не делает DNS-резолв, не
  запрещает redirects; skills-версия делает `assert_public_resolved_host` (анти-rebinding) и
  `follow_redirects=False` с явным отказом на redirect.
- SEVERITY: **P2** (не в проде: пакет инертен; станет P1, если сетевой плагин monitor/n8n
  подключить без усиления).
- RECOMMENDATION: при интеграции сетевые плагины (monitor RSS/HTTP, n8n webhook) должны
  использовать skills-класс SSRF-защиты (DNS-резолв + `follow_redirects=False` + повторная
  валидация каждого hop) или маршрутизироваться через существующий безопасный HTTP-слой ядра.
- VERIFIED_BY: чтение обоих модулей; skills web.py доказанно fail-closed.

### F2 — TOCTOU в `assert_public_resolved_host` (skills)
- FOUND_BY: анализ `skills/security.py` + `web.py`.
- ROOT_CAUSE: DNS резолвится при проверке, затем httpx резолвит повторно при запросе — окно
  на DNS-rebinding между проверкой и коннектом.
- SEVERITY: **P2** (общая сложная проблема; текущая защита разумна для V1: literal-block +
  resolve-check + no-redirect существенно поднимают планку).
- RECOMMENDATION: при интеграции — pin resolved IP и коннект строго на него (custom transport),
  либо downstream egress-allowlist ядра.
- VERIFIED_BY: чтение кода.

### F3 — `plugins/sql.py` read-only зависит от инъектируемого `query_fn`
- FOUND_BY: `plugins/sql.py`.
- ROOT_CAUSE: регэксп-гвард (READ_RE/BAD_RE + запрет multi-statement) корректен и fail-closed,
  но фактическую read-only-гарантию на уровне соединения обеспечивает вызывающий `query_fn`.
  skills-версия сильнее: открывает SQLite `mode=ro` (гарантия на уровне БД).
- SEVERITY: **P2**.
- RECOMMENDATION: при интеграции `query_fn` обязан использовать read-only соединение/роль
  (defense-in-depth поверх регэкспа).
- VERIFIED_BY: чтение; регэксп покрывает комментарии/casing/whitespace/CTE/PRAGMA-запись.

### F4 — Пакеты не подключены к authority ядра (главный интеграционный факт, не баг кода)
- FOUND_BY: чтение адаптеров (ollama/openrouter делегируют в инъектируемый `gateway_adapter`;
  mcp — в `bridge`; rest_base — токен из `token_getter`).
- ROOT_CAUSE: пакет спроектирован как набор адаптеров; конкретный биндинг к Stage3 Gateway /
  Cost Governor / MCP-runtime / secret-broker ядра НЕ входит в пакет.
- SEVERITY: **P1 для интеграции** (без биндинга плагины инертны; с НЕПРАВИЛЬНЫМ биндингом —
  риск обхода Gateway/Cost Governor).
- RECOMMENDATION: см. интеграционные отчёты — биндить только на существующие seam'ы ядра.
- VERIFIED_BY: чтение всех адаптеров.

## Не найдено (проверено)
- hardcoded секретов в пакете — нет (grep по sk-/ghp_/xox/AIza/BEGIN — чисто).
- eval/exec/os.system/shell=True в пакете — нет.
- MCP metadata как источник авторитета — нет (deny-by-default через registry.resolve).
- прямого второго LLM-клиента в ollama/openrouter — нет (только делегация в adapter).

## Вывод
Пакеты качественные и fail-closed по дизайну; критических (P0) дефектов не найдено. Все P2 —
это усиления, релевантные ТОЛЬКО при фактической адаптерной интеграции. F4 — ключевой момент:
интеграция должна биндить на существующую authority ядра, не поднимать вторую.
