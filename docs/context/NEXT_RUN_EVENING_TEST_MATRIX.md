# BOSSMAN — EVENING FULL SYSTEM TEST MATRIX

Вечерний системный прогон (нового кода НЕ пишем). Допустимые статусы:
`PASS / FAIL / SKIP_HOST / SKIP_EXTERNAL_SERVICE / SKIP_EXTERNAL_CREDENTIAL / NOT_TESTED`.
SKIP не превращать в PASS. Выход: P0=0, P1=0, все host-capable обязательные гейты PASS,
каждый SKIP объяснён.

Адаптировано из BOSSMAN_NEXT_RUN_8_9_PACK (04) под фактическое состояние репо —
с конкретными командами/тестами. Сам временный ZIP в репо не хранится.

## Boot / health
- Core (`bossman-core`) и Command Center (`command-center`) поднимаются; DB/Redis если настроены;
  миграции/схема; здоровье подсистем (`GET /api/system`, lifecycle `registry.status()`).
- Без Docker → Postgres-ветка ядра: `SKIP_HOST`; покрытие обеспечено pytest через TestClient.

## Local model (Ollama → Gateway)
- Ollama доступен, модель загружена, маршрут через существующий Stage 3 Gateway,
  `cloud_policy=never`, счётчик облачных вызовов = 0.
- Нет Ollama → `SKIP_HOST`. Инвариант покрыт: `bossman-core tests/test_gateway_cost_governor.py`,
  `test_gateway_cloud_policy.py`.

## Coding / LSP
- Одноразовый репо: inspect → LSP symbols → definition/references → правка → тесты → diff →
  reviewer видит diff → restart/resume.
- LSP-мост: `command-center/bcc/lsp_bridge.py` (+ `bcc/features/code_intel.py`).
  Юнит: `command-center tests/test_code_intel_and_scorecard.py` (fake-LSP, реальные пайпы).
- Реальный языковой сервер (pyright/gopls) → LIVE или `SKIP_HOST` (задать `LSP_SERVERS`).

## Windows / Computer Operator
- «Открой Блокнот» через продовую цепочку → свежее наблюдение → (опц.) TYPE-маркер →
  неразрешённое приложение denied → shell-подобная цель denied.
- Юнит: `bossman-core tests/test_stage13_wiring_notepad.py` (allowlist, argv-only, fresh-observe).
- Нет Windows GUI → `SKIP_HOST`.

## Memory / FactStore
- write fact → retrieve → restart → retrieve → memory search → compaction → cold-session resume.
- Юнит: `command-center tests/test_v22_facts_api.py` (контракт object/known_at/query/current_only).

## Browser
- session → navigate → read DOM → click/type → takeover/resume → sensitive action → ASK.
- Реюз существующей browser-подсистемы; plugin `browser.form_submit` = ASK
  (`command-center tests/test_plugins_adapter.py`).

## Plugins (13 коннекторов)
- Для каждого: health(non-destructive), read=ALLOW, ASK-операция, reject=0 side effect,
  approve=ровно один эффект (где безопасно), duplicate callback=без дубля.
- Юнит: `command-center tests/test_plugins_adapter.py` (48). Внешние LIVE → `SKIP_EXTERNAL_CREDENTIAL`.
- Статус: `GET /api/plugins` (configured/missing, без сырых секретов).

## Cost Governor
- reserve/commit/release; исчерпанный бюджет STOP до сети; unknown price fail-closed;
  local mode → облако denied.
- Юнит: `bossman-core` cost_control (24 теста). Реальный платный вызов → `SKIP_EXTERNAL_CREDENTIAL`.

## Pythia
- offline fail-soft; auth на всех ручках; нет action authority; live/recovery если сервис есть.
- Юнит: `bossman-core tests/test_world_intelligence_pythia.py` (21). Live → `SKIP_EXTERNAL_SERVICE`.

## Benchmark (Bossman vs OpenCode)
- 10 одинаковых coding-задач на одном железе/модели → JSONL →
  `python -m bcc.eval_scorecard runs.jsonl` (`compare`): success/tests-green/interventions/
  cost/time/security-violations/resume-after-restart.
- Агрегатор покрыт юнитами; реальный A/B-прогон → LIVE (нужны хост+модель).

## Chaos
- kill local model, browser unavailable, restart UI/core, duplicate approval, cancel, retry.
- Инварианты: idempotent-release, single-decision approvals, degraded ≠ crash.

## Security red-team
- path traversal, symlink escape, SSRF private IP, redirect SSRF, arbitrary shell,
  unauthorized API, SQL write, MCP unknown capability, secret scan.
- Юнит: `test_plugins_adapter.py` (SSRF/redirect/path/symlink/SQL/redaction),
  `bossman-core test_stage13_*_redteam`, `test_core_auth_perimeter`. Scan: `tools/ci_secret_scan.py`.

## Full regression
- `bossman-core`: `pytest -q --timeout=180 --timeout-method=signal`.
- `command-center`: то же.
- compileall / JS syntax / secret scan / CI на точном финальном SHA.
- Известный runner-only флейк `test_v21_failure_injection::test_state_survives_process_restart_midway`
  (179s teardown, SQLAlchemy/aiosqlite) — не блокер, не связан с изменениями.
