# V3 PRE-CYBERSEC — SYNC / RECONCILIATION LOG

Ветка: `claude/bossman-control-v03-43igbk`. NO force push. Remote — источник истины.

## HEADs
- START_LOCAL_SHA (до сверки): `e3bbe39` (локальные дубликаты Lane-2, уже в remote)
- START_REMOTE_SHA: `ac044f6` — принят как база (remote ушёл далеко вперёд)
- Действие: `git checkout -B … origin/…` (сброс на remote); мои прошлые cherry-pick'и
  Lane-2 (LSP confinement) оказались ДУБЛИКАТАМИ уже-влитой работы → отброшены.

## Классификация внешних изменений на remote (ключевое)
| SHA | Что | Решение |
|---|---|---|
| e3d53a5,5941c8f,09e5a22 | RC Master Fix Pass: SQL modifying-CTE gate, DNS pinned-connect, streaming max_bytes, redaction, регресс-тесты | KEEP — проверено присутствие в HEAD (см. ниже) |
| 5fbe17f, ac9506f | LSP workspace confinement (canonical allowed_roots/_within) | KEEP — совпадает с моим Lane-2 (дубликат отброшен) |
| cc4ef67 | V1 freeze | KEEP |
| 0516bb0→93e4ef8, 9880087, 50e0668, 8468674 | LLM-V2: working/decision/failure memory, task graph, model intelligence | KEEP + **REPAIR** (fake-green P0/P1, см. ниже) |
| 31d6982, eef14fd | context-os (hierarchical/compiler/state/stores) | KEEP |
| befad9a, ba84377, 7fe28ef | apps ecosystem V1 | KEEP |

Проверка RC-фиксов в текущем HEAD (не дублировать): `sql_read_only_ok` режет
modifying-CTE (`plugins.py:183`); `resolve_pinned_ip`+`stream`+`max_bytes` в
`plugin_security.py:106,176,192`; LSP `allowed_roots/_within` в `code_intel.py:94`. ✅

## Baseline (до моих правок, честно)
- bossman-core: **939 passed, 1 failed, 4 skipped**.
  - 1 failed = `test_stage13_windows_adapter::test_windows_foreground_…` →
    **HOST_SPECIFIC**: тест мокает `pywinauto`, но не `is_windows`; на Linux
    `WindowsDesktop.foreground()` бьёт `RuntimeError("Windows backend requires Windows")`
    раньше мок-пути. Требует Windows-хост. Владелец — недавняя computer_operator-работа. Не трогаю.
- command-center: **1 error (collection)** — `tests/test_working_memory.py` не
  импортируется (`OptimisticConcurrencyConflict` отсутствовал) → блокировал весь набор. **NEW/REAL P1 → починено.**

## P0/P1, найденные и обработанные (fake-green в LLM-V2 memory)
Модули `working_memory.py`/`decision_memory.py`/`failure_memory.py` — **не
импортируются нигде в проде** (orphaned scaffolding parallel-work), но написаны
с реальными дефектами:

| ID | Дефект | Файл | Действие |
|---|---|---|---|
| P1 | контракт имени: `ConcurrencyError` vs ожидаемый `OptimisticConcurrencyConflict` (ронял сбор CC) | working_memory.py:261 | FIX: канонический тип + alias |
| P0 | asyncpg: `executescript`/`commit` (нет в asyncpg Pool) | decision/failure init | FIX: `execute()` (simple-query DDL) |
| P0 | asyncpg: SQLite `AUTOINCREMENT` в embedded DDL (parse-fail на PG) | decision_memory.py:63 | FIX: `BIGSERIAL`/`BIGINT` |
| P1 | `await conn.execute()` затем `async for` (execute → строка статуса) | failure_memory.py:180 | FIX: `fetch()` |
| P1 | `result.status` на строке | failure_memory.py:212 | FIX: сравнение строки `== "UPDATE 1"` |
| P1 | dead double-pool в `query_failures` | failure_memory.py:247 | FIX: одиночный `fetch()` |
| P1 | `commit()` в `supersede_decision` | decision_memory.py:248 | FIX: `execute()`, без commit |
| **P0 (OPEN)** | схема/класс рассинхрон: `WorkingMemory` требует `project_id` + `working_memory_versions`, которых НЕТ в `db/schema.sql` (+ дубликат таблицы `working_memory` строки 146/230) | working_memory.py / db/schema.sql | **DEFER — не патчу вслепую** (нет live PG для валидации миграции; риск сломать boot > пользы). Задокументировано в FREEZE-доке как открытый P0. |

Покрытие починок — детерминированное (без live PG):
`command-center/tests/test_working_memory.py` (5 passed + 1 SKIP_HOST real-PG gate),
`bossman-core/tests/test_memory_asyncpg_contract.py` (6 passed).

## Postgres
Живого Postgres в среде НЕТ (нет `PG*`/`DATABASE_URL`, порт 5432 закрыт, docker
недоступен; `config.py` указывает на docker-hostname `postgres`). Поэтому «REAL
POSTGRES GATE» — **SKIP_HOST**; asyncpg 0.31 установлен, но сервера нет.
