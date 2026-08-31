# V3 PRE-CYBERSEC — SYNC / RECONCILIATION LOG

Ветка: `claude/bossman-control-v03-43igbk`. NO force push. Remote — источник истины.

## HEADs
- START_LOCAL_SHA = START_REMOTE_SHA = `4b6d7cb` (совпадали; конкурентной работы
  Agent B на этой ветке не было — проверено `git log --all --since=24h`).
- Сиблинг-ветки (не мержил, только сверился): `fix/p0-p1-schema-memory-pg-gate`,
  `feature/hard-reasoning-v2-master-prompt`, `security/audit-fixes-10-issues`,
  `claude/v2-reasoning-engine`, `claude/bossman-v2-context-os`.

## Классификация внешних изменений
| Источник | Что | Решение |
|---|---|---|
| `fix/p0-p1-schema-memory-pg-gate` (84af8df) | независимо нашёл ту же потерю `CREATE TABLE projects` | **KEEP-IDEA / REPAIR независимо** — я починил на своей ветке (ветки не мержу, чтобы не тащить чужой незавершённый контекст) |
| `ac044f6` и ранее (LLM-V2 memory) | working/decision/failure memory | **REPAIR** — см. P0 ниже |
| RC-фиксы (SQL CTE, DNS pin, max_bytes, LSP confinement) | уже в HEAD | KEEP (проверено, не дублировал) |

## P0/P1, найденные и ЗАКРЫТЫЕ в этом проходе (все — с живым Postgres)

| ID | Дефект | Доказательство | Статус |
|---|---|---|---|
| **P0-1** | `db/schema.sql`: утерян `CREATE TABLE projects` (регресс из `9880087`); колонки остались «сиротами», две таблицы ссылаются на `projects(id)` | pre-fix схема на чистой БД: `ERROR: syntax error at or near "id"` (exit 3). Схема **не применялась вовсе** — Postgres был недоступен в принципе | **FIXED** — post-fix: 14 таблиц, 27 индексов, `ON_ERROR_STOP=1` exit 0 |
| **P0-2** | `async with (await pool()) as conn:` в `decision_memory` (4 места) — вход в asyncpg **Pool** как в контекст-менеджер **ЗАКРЫВАЕТ пул** на выходе → весь процесс теряет БД | живой прогон: `InterfaceError: pool is closed` после первой же операции | **FIXED** — `.acquire()` / модульные хелперы; регресс-тест `test_no_pool_as_context_manager_anywhere` |
| **P0-3** | `failures`: модуль писал/читал `failure_id`, которого НЕТ в каноничной схеме | живой прогон: `UndefinedColumnError: column "failure_id" does not exist` | **FIXED** — `failure_id TEXT NOT NULL UNIQUE` добавлен в схему (симметрия с `decisions.decision_id`) |
| **P0-4** | Двойное JSON-кодирование: пул уже несёт jsonb-кодек (`json.dumps`), а модули дополнительно звали `json.dumps` → в JSONB лежала *строка*, `@>`-запросы не работали | живой тест `FM_JSONB_QUERYABLE` (containment `files @> '["a.py"]'`) | **FIXED** — нативные объекты; containment=1 |
| **P0-5** | `working_memory` требовал `project_id` + таблицу `working_memory_versions`, которых не было | ранее «guaranteed crash» | **FIXED** — переписан как typed view (task-scoped) + добавлена `working_memory_versions` |
| P1-1 | дубль таблицы `working_memory` в схеме | статически | FIXED (дедуп) |
| P1-2 | Windows Stage13 тест падал на Linux (нет `ctypes.windll`) | локально | FIXED (честный SKIP_HOST) |

## Единая авторитетность памяти (RESOLVED)
Было: три модуля со **своими встроенными DDL** (`DECISION_SCHEMA`, `FAILURE_SCHEMA`)
и working_memory с чужим пулом → несколько источников правды о схеме.

Стало — **ОДНА каноничная персистентность**:
```
db/schema.sql (единственный DDL)  →  bossman.db pool (jsonb codec, авто-применение схемы)
      ↓                    ↓                    ↓
WorkingMemory (view)  decision_memory (view)  failure_memory (view)
```
* встроенный DDL удалён из обоих модулей; `init_*_table()` теперь **проверяет**
  каноничную таблицу (`to_regclass`) и честно падает, если её нет — вместо тихого
  создания второй, расходящейся схемы;
* `WorkingMemory` использует канон-пул, ключ — `task_id` (как у сиблингов);
  проектный скоуп выводится из `tasks.project_id`, дублировать не нужно;
* `context_engine` остаётся **retrieval/RAG-индексом** (documents/chunks/embeddings),
  а не конкурирующим durable-авторитетом.
Регресс-тест запрещает возврат встроенного DDL и pool-closing паттерна.
