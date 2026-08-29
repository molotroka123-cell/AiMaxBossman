# ЭТАП 2.222 — фактическая интеграция в bossman-core

Этот файл фиксирует, как context_engine РЕАЛЬНО подключён в ядре `bossman-core`
(ТЗ v0.3). Обобщённый `INTEGRATION_GUIDE.md` из ZIP описывает план; здесь —
конкретные точки в коде. ACTUAL REPO = source of truth.

## Принцип: слой, а не замена

В ядре уже есть свой context-стек: `bossman/context.py` (`ContextBuilder`,
раздел 10 ТЗ) собирает блоки под KV-кэш llama.cpp и считает токены. Мы его НЕ
заменяли на `ContextCompiler` из ZIP. Вместо этого движок наполняет ранее пустой
блок `retrieved` этого же `ContextBuilder`. Второго memory-стека нет.

## Точки интеграции (файл:символ)

| Что | Где |
|-----|-----|
| Долгоживущий фасад движка на процесс | `bossman/context_engine/service.py` → `ContextEngine`, `get_engine()` |
| Наполнение блока `retrieved` реального билдера | `service.ContextEngine.inject_into_builder()` → `builder.set_retrieved(blocks)` (service.py) |
| Вызов инъекции + tool pruning в петле | `bossman/runner.py` → `run_task()` вызывает `apply_context_engine(builder, tools, ...)` сразу после сборки `ContextBuilder` и tool-схем |
| Определение инъекции/пруна | `bossman/runner.py` → `apply_context_engine()` |
| Structured compaction вместо lossy summarize | `bossman/runner.py` → `run_task()` в точке уплотнения (>70% / 25 вызовов) вызывает `compact_session()`, fallback — прежний LLM-summarize |
| Определение structured compaction | `bossman/runner.py` → `compact_session()` (CompactSkill) |
| Чистое закрытие SQLite на shutdown | `bossman/api.py` → `shutdown()` зовёт `context_engine.close_all()` до `db.close()` |
| Флаги/пути | `bossman/config.py` → `context_engine_enabled`, `context_db` |

### Почему не тронут `llm.py`

`bossman/llm.py` — транспорт: он только постит уже собранные сообщения в LiteLLM
и логирует usage/cloud-политику. Контекст собирается в `runner`+`context.py` ДО
`llm.chat()`. Поэтому инъекция сделана в runner (реальный assembly point), а не в
транспортном слое. `llm.py` не изменён.

## Поток данных

```text
task → runner.run_task
  → ContextBuilder(system, budget)            # bossman/context.py, без изменений
  → apply_context_engine(builder, tools)      # ЭТАП 2.222
       → engine.inject_into_builder → builder.set_retrieved([memory_block, *evidence])
       → prune_tool_schemas(tools, task)       # только релевантные + confirmed_* всегда
  → цикл: builder.build() → llm.chat()         # транспорт не тронут
  → при >70%/25 вызовов: compact_session()      # structured continuation state
       (fallback: LLM-summarize при выключенном/недоступном движке или FAIL gate)
```

## Классы памяти и provenance

Память разделена по `kind` (не один JSON): facts/decisions/constraints/procedures/
episodic/working/failure/unresolved/distilled/preference/todo/summary. Решения —
стабильный ID `DEC-000N`, superseded не удаляется. Negative memory —
`FAIL-000N` (symptom→cause→fix→verification), `retrieve_failures()` ищет
проваленные подходы первыми. Каждая durable-запись несёт provenance (source,
timestamp, content hash, confidence, verification). Contradictions помечаются
`DISPUTED` и не перезатираются.

## Плагины памяти

`MemoryPlugin` (Protocol): `StoreMemoryPlugin` (read/write, локальный store),
`MarkdownMemoryPlugin` и `JsonMemoryPlugin` (read-only мосты к существующим
папкам памяти). Obsidian/DB-адаптер добавляется реализацией того же протокола
без переписывания Compact/Compiler.

## Sensitivity-aware retrieval

`HybridRetriever.search(..., sensitivity_allow=(...))` не отдаёт sensitive-чанк
агенту без прав, даже если он релевантнее. `Ingestor.ingest_text(..., sensitivity=...)`
и `ContextEngine.index_text(..., sensitivity=...)`.

## Runtime и отключение

Индекс живёт в `workspace/_context/context.db` (SQLite/WAL) — вне git. Весь слой
отключается `CONTEXT_ENGINE_ENABLED=0`: тогда поведение ядра возвращается к
исходному (пустой `retrieved`, LLM-summarize). Любая ошибка движка/ранкера
деградирует к прежнему поведению, петля не падает.

## Прогон

`cd bossman-core && python -m pytest -q` → 91 passed (было 37; +56 тестов
context_engine; 35 прежних ядра сохранены). 2 браузерных e2e (ЭТАП 1) падают ТОЛЬКО
из-за отсутствия бинарника Chromium в песочнице — это окружение, не код.
