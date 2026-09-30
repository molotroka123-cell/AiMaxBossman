# FABLE5 — GENERAL ARCHITECTURE & PERFORMANCE AUDIT

**Репозиторий:** molotroka123-cell/AiMaxBossman
**Ветка / START_SHA:** `claude/bossman-control-v03-43igbk` @ `f492ab4`
**Дата:** 2026-08-31
**Охват:** bossman-core (22k LOC) + command-center (24k LOC) + bossman_v3 (0.9k) + CI/docs.
**Метод:** 5 параллельных read-only агентов (context-pipeline, model-router+reasoning,
memory-efficiency, V3-ROI+flags+skills, async-lifecycle+dead-code+observability) +
собственные замеры. Это НЕ offensive-security аудит (тот отдельно). Safety/red-team —
out of scope, не менялись.

> Правило: файл существует ≠ WORK. Числа, где не измерены на живом прогоне, помечены.

---

## 1. Executive summary

Ядро уже **быстрое и трезвое по замыслу**: model-router детерминированный (dict-lookup,
без «LLM чтобы решить нужен ли LLM»), простая задача = **1 round-trip** (нет
planner→critic→verifier цепочки на каждый запрос), память ограничена (2 транзакции/задача,
без N+1), в промпт память **не течёт обратно** неограниченно.

Реальные точки waste сосредоточены в **context-pipeline** (переэмбеддинг неизменного
контента, повторная сборка контекста, блокирующий sqlite в async hot-path), плюс
**один доказанный lifecycle-баг** (py3.12 teardown hang) и **мёртвый/дублирующий код**
(V3-пак как параллельная реализация уже подключённых систем; мёртвые V3-флаги; dead shim).

**Что уже исправлено в этом проходе** (раздел 18 мастер-промта разрешает: async-lifecycle,
perf/cache, dead-work): py3.12 hang, переэмбеддинг memory.md, memoize skill-discovery,
удалён dead shim `bossman/core/db.py`. Всё с тестами и полной регрессией (раздел 5 ниже).

---

## 2. Что менять НЕ нужно (проверено — уже хорошо)

| Область | Вердикт | Свидетельство |
|---|---|---|
| **Model Router** | keep-as-is | `gateway/router.py:56` `resolve()` — чистый dict-lookup + capability/cloud фильтр + circuit-breaker; **нет LLM-вызова для маршрутизации**; health TTL-кэш (`router.py:45`); cloud под Cost Governor (`app.py:105`), fail-closed при неизвестной цене |
| **Reasoning fast path (ядро)** | keep | `runner.py:256-322` — плоский `chat→tools` цикл; простая задача = **1 round-trip**; критики/верификаторы в dev_factory/computer_operator **детерминированные, без модели** |
| **Memory efficiency** | keep (только housekeeping) | 2 транзакции/задача, нет записей в шаговом цикле, индексы покрывают частые запросы, snapshot ≈ КБ (тяжёлые JSONB-колонки пустуют), **память не феедбечит в промпт** |
| **Skills routing** | cheap-fix только memoize | `skill_library` — O(1) dict-by-id, без scoring/reliability в hot-path; дорогой `reliability_lcb` (~14k float-ops) живёт offline (promotion-time) |
| **Context ranking/dedup/budget** | keep | ranking **не O(n²)** (single-scan), dedup по persisted `content_hash` (не пересчёт), token-budget precomputed once |

---

## 3. Найденные проблемы (по ROI = gain / cost)

### Исправлено в этом проходе

| ID | Проблема | file:line | Cost | Что сделано |
|---|---|---|---|---|
| **O1** | **py3.12 teardown hang** — `worker_loop`/`execute` отменяли per-run+heartbeat задачи fire-and-forget; `Services.stop` диспозил пул БД, пока осиротевшие задачи держали aiosqlite-коннекты → закрытие event loop виснет ~180s под 3.12 | `engine.py:204`, `:293`; `api.py:145` | High (CI-красный на 3.12) | `TaskEngine.aclose()` дренирует `_active` **до** `db.close()`; `execute` awaits heartbeat; `worker_loop` finally awaits отменённые. Доказано: py3.12 full-suite больше **не виснет** (294s), 3.11 — 611 passed |
| **O2** | **Переэмбеддинг неизменного `memory.md` на каждой задаче** — `index_text` всегда звал `replace_chunks` (DELETE+re-embed), хотя `document_id` уже = stable_id(uri, hash) | `ingest.py:19`; `runner.py:102` | Med-High | Fast-path: `store.document_indexed(id)` → skip chunk+embed для идентичного контента; изменение контента → полный ре-индекс. Тест `test_context_engine_reembed_fastpath.py` |
| **O3** | **skill discovery re-globает+re-парсит ФС на каждый вызов** (parse = read+yaml доминирует) | `skill_library.py:56` | Low-Med | Мемоизация `discover()` по ключу (path, mtime) всех SKILL.md: re-glob дёшев, re-parse пропускается пока ФС не менялась. Тест `test_skill_discovery_memoized.py` |
| **O4** | **Dead shim `bossman/core/db.py`** — 0 импортёров, ложное обоснование, без теста | — | trivial | Удалён пакет `bossman/core/`. `bossman.db` — единственный db-слой |

### Осталось (design-only / следующая волна — см. ROADMAP)

| ID | Проблема | file:line | Cost | Ожидаемый выигрыш |
|---|---|---|---|---|
| **O5** | **Блокирующий sqlite в async hot-path** — все `lexical_search`/`all_vector_chunks`/memory-`SELECT` синхронны внутри `run_task`, сериализуют единственный worker event loop | `store.py:74`; `runner.py:245` | **High** под конкуренцией | thread-executor или async-драйвер → восстановить конкурентность. Нужен бенчмарк под нагрузкой |
| **O6** | **Полный скан вектор-таблицы + json.loads каждого вектора на retrieval**; вектор-пул не ограничен (`candidate_limit` кэпит только лексику) | `store.py:155`; `retrieval.py:58` | High (растёт с корпусом) | хранить вектора блобом/ANN-индекс или prefilter лексикой. Пока корпус мал — низкий приоритет |
| **O7** | **`_history_messages()` пересчитывается 3× за шаг** (fill/block_tokens/build), каждый O(history) | `context.py:181,196`; `runner.py:264,280,282` | Med (растёт с историей) | мемоизация per-step (история append-only) → −⅔ стоимости сборки |
| **O8** | **Нет fast-path для тривиальных задач** — запрос всегда эмбеддится и гоняет полный hybrid-search | `runner.py:245`; `retrieval.py:46` | Med | гейт retrieval по длине/эвристике/пустому корпусу |
| **O9** | **classify_reasoning (L0–L4) — мёртвый код** (реализован, только в тестах); difficulty-branching не подключён | `bcc/v2/model_intelligence.py:123` | opportunity | либо подключить к выбору alias/глубины верификации, либо удалить |
| **O10** | **V3Flags (9 полей) — мёртвые флаги** (определены, не читаются ни одним адаптером); V3 «выключен» только потому что не импортируется | `bossman_v3/feature_flags.py` | — | не трогать frozen; при будущем подключении — обязательна проверка флага в точке вызова |
| **O11** | **compaction ре-сканит весь диалог ~15 регексами/предложение** каждый раз (не инкрементально) | `context_engine/compact.py:138` | Med (на границе compaction) | классифицировать только новые turn'ы |
| **O12** | Наблюдаемость: есть input/output/context tokens + cache_hit; **нет** router_latency, context_build_latency, memory_lookup_latency, tool_latency, verification_latency, retry_count | `obs.py`, `runner.py` | cheap-add | по одному `perf_counter`-брекету на существующий call-site |

---

## 4. Крупные архитектурные вопросы (решения)

### Context OS — вердикт: **HYBRID (оставить движок, портировать 2 идеи)**, нужен бенчмарк
`context_os` — **НЕ Pareto-улучшение**: выигрывает только по инкрементальному кэшу и
async-I/O, но **регрессирует** по retrieval / ranking / dedup / confidence / staleness /
compression (частью это явный POC-stub: `compiler.py:59` `[RELEVANT_FACTS]…stub`,
monkey-patch в `integration.py`). Брать целиком = выкинуть сильнейшее у движка.
**Рекомендация:** не переключаться, а портировать в текущий `context_engine` две вещи
context_os: (1) hash-keyed layer/assembly cache (закрывает O7 и часть O2), (2) неблокирующий
I/O (закрывает O5). Затем O6 (bounded/indexed vector) и O8 (trivial fast-path). Проверить
бенчмарком (assembly-latency, embed/query-time, event-loop stall под конкуренцией).

### V3 7-Pack — вердикт: **в основном параллельная реализация уже подключённого**
| Модуль | Дублирует | Вердикт |
|---|---|---|
| computer_agent | `computer_operator/manager` (async, wired) | **REMOVE orchestrator / SIMPLIFY** — портировать raw-shell block + stale-obs check |
| recovery_kernel `LoopDetector` | `computer_operator/loop_guard` (сильнее, wired) | **REMOVE** LoopDetector; DEFER watchdog/checkpoint |
| data_guardian | `context_engine/compact` (anchor-survival, wired) | **MERGE-concept / DEFER** (не два компактора) |
| self_healing | `failure_memory` + bcc `healing.py` + replan-budget | **SIMPLIFY/DEFER** — сохранить EV-decision, порт+схема не готовы |
| visual_state | `computer_operator/observer` (уже фьюзит) | **DEFER** |
| skill_factory | нет прямого дубля, но нет потребителя | **DEFER** (большая поверхность) |
| self_improvement | overlaps bcc `benchlab` | **DEFER / KEEP-offline** (0 runtime-cost) |
Итого: подключать весь пак ради статуса не нужно; ценность — фрагменты (EV-decision,
promotion-gates, мелкие safety-проверки). Оркестратор, LoopDetector, второй компактор — избыточны.

### Model Router — **keep-as-is** (см. раздел 2). Опция: expected-gain-aware выбор alias — не обязательно.

### Memory — **keep**; единственное — агрегатный append-only рост (`working_memory_versions`/
`decisions`/`failures` не чистятся), линейный по числу задач, маленькие строки. Retention-джоб
(последние N версий на задачу; архив resolved-failures > TTL) — **design-only, не срочно**.

### Browser/Computer-control — **DESIGN_ONLY**: `toolkit/browser.py` (агентский tool-loop),
`bcc/v2/browser_control` (control-plane), Stage13 BROWSER (убран из vocabulary в прошлом проходе,
адаптер не подключён). Единый typed browser-path требует явного дизайна dispatch↔toolkit — не
строить вслепую.

## 5. Регрессия после фиксов O1–O4

```
bossman-core (без PG):        1078 passed, 14 skipped, 0 failed
bossman-core (живой PG 16.13): 1087 passed, 5 skipped, 0 failed
  pg_memory_gate + runner_memory_wiring: 9 passed
command-center (py3.11, playwright): см. FINAL (611 passed baseline, +2 lifecycle-drain теста)
py3.12 full suite: НЕ виснет (294s; 16 «failed» = ModuleNotFoundError playwright в
  одноразовом venv, не мой код — в CI playwright установлен и они зелёные)
compileall: PASS · secret-scan: PASS
NEW_P0=0 · NEW_P1=0 · NEW_REGRESSIONS=0
```

## 6. PYTHON312_FLAKE_ROOT_CAUSE (доказано)
Осиротевшие фоновые задачи (`_execute_pooled`, `_heartbeat`) отменялись без `await`;
`Services.stop` при `start_workers=False` шёл прямо в `db.close()`→`engine.dispose()`,
пока эти задачи держали aiosqlite-коннекты; при закрытии function-scoped loop под 3.12
`_cancel_all_tasks` возобновлял их на `await s.commit()` уже закрытого пула → 180s epoll-хэнг.
Фикс — строгий порядок **drain (`engine.aclose()`) → dispose**, плюс await отменённого
heartbeat в `execute`. Тайм-аут НЕ увеличивался, тест НЕ скипался (раздел 13 соблюдён).
