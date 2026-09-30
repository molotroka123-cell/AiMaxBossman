# FINAL PRE-RC BUG HUNT

Дата: 2026-08-30 · Ветвь: `claude/bossman-control-v03-43igbk`

START_HEAD:
`8b75db64d594a7e4de84f8c49c4074f613cccb6a` (актуальный remote на момент старта, синхронизирован через `git fetch` + fast-forward)

FINAL_HEAD:
`b0a5a0c` (см. git — фактический sha после push)

FILES_INSPECTED:
- `bossman-core/bossman/world_intelligence/{__init__,routes,subsystem}.py` (новый Pythia drop-in, commit 4fb8b6f)
- `bossman-core/bossman/api.py` (`_register_subsystems` / `_include_stage_routers`)
- `bossman-core/bossman/lifecycle.py` (SubsystemRegistry — critical/optional поведение)
- `bossman-core/bossman/projects/runner.py` (create_subprocess_shell — контекст)
- `bossman-core/bossman/sandbox/runtimes/safe.py` (fail-open проба)
- все 11 stage-роутеров ядра (smoke getattr(pkg,'router') + import)
- `command-center/bcc/features/tools_facts.py`, `bcc/v2/memory/facts.py` (FactStore, exemplar mismatch)
- `command-center/bcc/api.py` (approvals контракт), `bcc/approvals.py` (decide/replay)
- все feature-модули `command-center/bcc/features/*` (smoke import)
- `bcc/v2/terminal_control.py`, `bcc/v2/code_index.py`, `bcc/v2/mcp_runtime.py` (fail-open/traversal)
- отчёты: STATIC_CODE_AUDIT.md, RC_TEST_C_2026-08-30.md, RC_TEST_B_INTERMEDIATE, PYTHIA_WORLD_INTELLIGENCE_INTEGRATION_REPORT.md, FINAL_SUMMARY.md

BUGS_FOUND: 5 (все в новом Pythia drop-in 4fb8b6f; в остальном коде новых дефектов не найдено)

P0:
- нет

P1:
1. **`/world_intelligence/agent/view` падал 500 на первом запросе.** В `AgentViewOut`
   используется `Any` (`list[dict[str, Any]]`, `dict[str, Any]`), но `from typing import Any`
   не импортирован. Из-за `from __future__ import annotations` ошибка не всплывала при
   импорте — только при первом обращении (`PydanticUserError: AgentViewOut is not fully
   defined`). Это ГЛАВНАЯ machine-readable ручка Pythia для Bossman-контекста.
2. **Все 7 ручек `/world_intelligence/*` молча выпадали из приложения.** Пакет
   `bossman/world_intelligence/__init__.py` экспортировал только `build_subsystem`, но не
   `router`. `_include_stage_routers()` берёт `getattr(module, "router", None)` на уровне
   ПАКЕТА → `None` → include пропущен без ошибки. То есть роуты были зарегистрированы в
   коде, но недоступны в реальном app (implementation без registration).
3. **Ручки Pythia были без auth.** Стало видно только после починки п.2 (пока роуты были
   мёртвыми, red-team матрица проходила вхолостую). `test_stage13_auth_redteam.py`
   подтвердил: 7 data-роутов без `require_scope`. Pythia — источник знания только для
   чтения → добавлен скоуп `SCOPE_CHAT` (Stage 6) на уровне роутера.

P2:
4. **`get_pythia_view()` возвращал корутину без `await`.** `return get_pythia().agent_view()`
   вместо `return await get_pythia().agent_view()` — любой потребитель получал незапущенную
   корутину вместо данных.
5. **Аннотация зависимости ссылалась на несуществующий `PythiaWorldIntelligence`.** Класс
   называется `PythiaWorldSubsystem`; из-за future-annotations не падало, но неверно и
   хрупко (сломалось бы при `get_type_hints`). Исправлено на реальное имя; заодно убраны
   мёртвые импорты (`Subsystem`, `Field`, неиспользуемые convenience-функции).

BUGS_FIXED: 5 / 5

REGRESSION_TESTS_ADDED:
`bossman-core/tests/test_world_intelligence_pythia.py` (21 теста):
- auth: каждая из 7 ручек → 401/403 без токена (регресс п.3);
- fail-soft: каждая ручка → 200 под скоупом chat при offline-Pythia;
- `agent/view` отдаёт валидную структуру (регресс п.1) и корректно мапит реальный payload;
- `get_pythia_view` возвращает данные/None, не корутину (регресс п.4);
- пакет экспортирует `router` с 7 ручками (регресс п.2);
- сквозной тест на реальном `bossman.api.app`: аноним 401, авторизованный 200;
- `validate()` fail-soft при недоступной Pythia (critical=False → boot не падает);
- semantic boundary: у подсистемы нет action-authority методов.

ROUTE_CONTRACT:
- FactStore (exemplar `object_value↔object`, `known_at`/`query`/`current_only`) — проверен
  end-to-end: HTTP-роуты, tool-хендлеры, `FactStore.add/search/as_of`, `query_facts`
  (`known_as_of`) — все имена совпадают, фикс 17cd463 полон и консистентен. Новых
  mismatch не найдено.
- Approvals: фронт шлёт `{approve, by}`, бэк `ApprovalIn{approve, by}` — совпадает;
  `decide` идемпотентен (guard `status=="pending"`) — replay безопасен.
- Дашборд-контракты подтверждены живьём в RC_TEST_C (67/67, реальный движок, mock только
  на границе LLM); маршруты ядра покрыты полным pytest через TestClient.

MEMORY:
FactStore bi-temporal (`world_at`/`known_at`), `query`-фильтр, `current_only`, dedupe,
restart-persistence — контракт цел, фикс 17cd463 покрыт `test_v22_facts_api.py`. Запись→
чтение согласованы (`add` → `object`, `write_fact` → `object`). Дефектов не найдено.

APPROVALS:
`Approvals.decide` меняет строку только из `pending` → повторное решение/replay ничего не
делает (rowcount==0 → возврат текущей строки без повторного emit). REJECT не исполняет
действие. Дефектов не найдено.

STAGE13:
Computer Operator — Unwired убран ранее (f10c43b), путь plan→observe→policy→ActionRouter→
executor→fresh-observe цел; APP_LAUNCH allowlist deny-by-default, argv-only. В этом прогоне
изменений в Stage13 нет; тесты зелёные.

LOCAL_LLM_PATH:
Планировщик Computer Operator ходит в модель ТОЛЬКО через `llm.chat` (Stage 3 Gateway),
второго LLM-клиента не создано. Pythia НЕ является LLM-путём — это HTTP-источник знания,
без action-authority. Изменений в Gateway/local-path нет.

COST_GOVERNOR:
Не трогался. Ранее проверен (reserve/commit/release, fail-closed на unknown pricing,
cloud_policy=never > budget). Реальных платных cloud-вызовов не делалось.

PYTHIA:
Offline → все ручки fail-soft 200, ядро живо, `validate()` не бросает (critical=False,
degraded, boot не падает — проверено на реальном registry.start_all()). Semantic boundary:
подсистема эмитит только lifecycle-события `world_intelligence.state/stopped`, не факты и не
действия; предсказание не превращается в факт. Relevance: `agent_view` отдаёт
структурированный снимок с дефолтами, а не сырой дамп. 5 дефектов drop-in'а исправлены.

WINDOWS_COMPATIBILITY:
Проверены ранее исправленные зоны (discovery WINDOWS_MODEL_DIRS/expandvars, openclaw
path-forms, dev_factory difflib вместо GNU diff, app_launch SelectorEventLoop fallback) —
цела. Новых Windows-дефектов не найдено. Изменения этого прогона платформо-независимы.

SECURITY:
- Инвариант LLM→typed intent→policy→allowlist→executor не ослаблен.
- Новых shell=True/eval/exec/os.system нет; два `create_subprocess_shell` пре-существующие:
  `projects/runner.py` (доверенный шаблон spec + `shlex.quote` параметров) и
  `terminal_control.py` (терминал by-design). Не LLM→arbitrary-shell.
- Fail-open не найден: `code_index` path-containment fail-closed, `safe.py`/`mcp_runtime`
  — probe доступности, не auth-гейт.
- ДОБАВЛЕНА недостающая auth на 7 Pythia-ручек (были открыты после починки wiring).

BOSSMAN_CORE_TESTS:
906 passed / 0 failed / 4 skipped (`pytest --timeout=180 --timeout-method=signal`).
(2 падения после первичной починки — auth-redteam на незагейченных Pythia-ручках и
event-loop в собственном тесте — устранены добавлением скоупа и anyio-режима.)

COMMAND_CENTER_TESTS:
433 passed / 0 failed / 2 skipped (`pytest --timeout=180 --timeout-method=signal`, локально).

SECRET_SCAN:
PASS (`tools/ci_secret_scan.py`).

SKIPS:
- bossman-core: 4 skipped (hardware/capability-gated: реальный SAFE-рантайм, KVM/runsc,
  живой Windows GUI notepad — честный skip, не fake pass).
- Docker (Postgres/Redis) для bossman-core в этой среде не поднят → live-прогон Postgres-
  ветки ядра — SKIP_ENVIRONMENT; покрытие обеспечено полным pytest через TestClient.

KNOWN_LIMITATIONS:
- Pythia live-интеграция не проверялась против реального сервиса Pythia (не запущен в
  среде) — проверен весь fail-soft путь при offline. При наличии живой Pythia нужен
  отдельный smoke.
- `get_pythia()` создаёт СВОЙ глобальный инстанс, отдельный от зарегистрированного
  lifecycle-инстанса (`build_subsystem()`). Функционально безопасно (роуты делают свежий
  GET, от validate() не зависят), но httpx-клиент роут-инстанса не закрывается на shutdown
  (процесс всё равно завершается). Не дефект RC-уровня; оставлено как есть, чтобы не
  расширять правку.
- Command Center CI на GitHub-раннере периодически падает не-детерминированным зависанием
  teardown (`test_v21_failure_injection`, SQLAlchemy/aiosqlite pool) — предсуществующий
  runner-only флейк, не связан с этими правками; локально сьют зелёный.

UNRELATED_FILES_CHANGED: 0
(изменены ровно 3 файла Pythia + 1 новый тест; ничего постороннего.)

READY_FOR_FINAL_ABCD_GATE: YES
(с оговоркой: финальный RC-гейт объявляется ПОСЛЕ получения результатов всех четырёх
независимых тестов A/B/C/D — этот прогон их не подменяет и не закрывает этап.)

BLOCKERS: NONE
