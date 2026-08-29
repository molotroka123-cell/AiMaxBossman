---
audit_id: AUD-20260829-CONTEXT-MEMORY-001
scope: context-memory
type: implementation-audit
version: 1
status: pass-with-findings
repository: molotroka123-cell/AiMaxBossman
branch: claude/bossman-control-v03-43igbk
commit: fa7dbb28c251d58393a0aeaf590b3700d42620f7
previous_audit: null
auditor: Claude Code (implementation agent)
created_at: 2026-08-29
---

# ЭТАП 2.222 — Context & Memory Engine: implementation audit v1

## 1. Executive summary

context_engine из ZIP интегрирован в ядро `bossman-core` (ТЗ v0.3) как СЛОЙ поверх
существующего `bossman/context.py` (`ContextBuilder`), а не как его замена. Ранее
пустой блок `retrieved` теперь наполняется долговременной памятью (с provenance)
и evidence-чанками (с source-refs); компактизация петли переведена со свободного
LLM-summarize на structured continuation state с гарантией выживания критических
якорей; добавлены раздельные классы памяти, decision superseding со стабильными
ID, negative memory, contradiction-маркер, sensitivity-aware retrieval и tool
schema pruning. Все инварианты качества покрыты тестами и context quality
benchmark (shadow: baseline vs Context Engine). Полный прогон ядра: **91 passed**
(было 37; +56 тестов context_engine, 35 прежних сохранены). Тяжёлых зависимостей
не добавлено.

## 2. Scope and exclusions

В скоупе: `bossman/context_engine/*`, интеграция в `bossman/runner.py`,
`bossman/api.py`, `bossman/config.py`, тесты и доки ЭТАПА 2.222.
Не тронуто (границы): `command-center/`, `apps/`, `bossman/toolkit/browser.py`
(ЭТАП 1), `bossman/gateway/` (ЭТАП 3), `bossman/context.py` публичный API
(расширен только наполнением, не сломан), `bossman/llm.py` (транспорт).

## 3. Baseline

Ветка `claude/bossman-control-v03-43igbk`, отправная точка — коммит `7e70fcc`
(после интеграции ЭТАПА 1, `e98132a`). Прогон до работы: 35 passed + 2 браузерных
e2e падают из-за отсутствия Chromium (окружение). Референс «37 passed» — с Chromium.

## 4. Architecture observed

Поток: `runner.run_task` → `ContextBuilder` (context.py) → `apply_context_engine`
(инъекция retrieved + tool pruning) → цикл `builder.build()`→`llm.chat()` → при
>70%/25 вызовов `compact_session` (structured handoff, fallback LLM-summarize).
Движок `ContextEngine` — долгоживущий на процесс (`get_engine`), SQLite/WAL под
`workspace/_context` (вне git), чистое закрытие на shutdown в `api.py`.
Память разделена по `kind`; retrieval гибридный (lexical FTS5 OR+prefix / portable
overlap + vector cosine + rerank + recency/importance + dedup по content_hash).

## 5. Findings

| ID | severity | status | evidence | impact | fix | verification |
|----|----------|--------|----------|--------|-----|--------------|
| CTX-RET-001 | P2 | fixed | `store.lexical_search` использовал FTS5 implicit-AND и не падал на overlap при пустом результате | лишнее слово в запросе обнуляло recall, RU-морфология не ловилась | OR + prefix (`терм*`), fallback на token-overlap при пустом FTS | `tests/test_retrieval_extra.py::test_lexical_recall_with_extra_query_terms` |
| CTX-MEM-002 | P1 | fixed | у решений не было стабильного ID; provenance не фиксировался явно | нельзя ссылаться на решение и восстановить источник факта | `MemoryManager.decision` → `DEC-000N` (из store, переживает restart); provenance {source,timestamp,content_hash,confidence,verification} в metadata; `failure`→`FAIL-000N` | `tests/test_memory_classes.py` (7 кейсов) |
| CTX-CMP-003 | P0 | fixed | компактизация петли была lossy LLM-summarize; критические якоря могли исчезнуть | тихая потеря чисел/версий/путей/статуса тестов при уплотнении | CompactSkill: structured continuation state + неурезаемая секция Critical anchors + overflow indicator; в петле `compact_session` с fallback | `tests/test_compact_structured.py`, `tests/test_context_engine_integration.py::test_runner_structured_compaction_preserves_anchors` |
| CTX-MEM-004 | P1 | fixed | конфликтующие факты могли молча перезатираться | потеря противоречия/истории | `_detect_conflicts` → `DISPUTED`+`contradicted_by`, исходная не тронута; superseded не удаляется | `tests/test_memory_classes.py::test_contradiction_stored_not_overwritten`, `::test_decision_stable_ids_and_superseding` |
| CTX-SEC-005 | P2 | fixed | retrieval не учитывал sensitivity | sensitive-чанк мог уйти агенту без прав | `HybridRetriever.search(sensitivity_allow=...)`, `Ingestor/ContextEngine` проксируют sensitivity | `tests/test_retrieval_extra.py::test_sensitivity_aware_filtering`, benchmark T23/T24 |
| CTX-CTX-006 | P2 | fixed | блок `retrieved` в ContextBuilder никогда не наполнялся | RAG/память не доходили до модели | `apply_context_engine`→`inject_into_builder`→`set_retrieved`; durable memory впереди evidence | `tests/test_context_engine_integration.py` |
| CTX-TOOL-007 | P3 | fixed | все tools (в т.ч. 22 браузерных) шли в каждый prompt | раздутый tool-контекст | `prune_tool_schemas` (floor + `confirmed_*` always) | `tests/test_context_engine_integration.py::test_tool_schema_pruning_keeps_relevant_and_floor_and_always` |
| CTX-EMB-008 | INFO | accepted | боевой embedder/reranker не подключён; используется `HashEmbedder` fallback | recall ограничен без нормальной multilingual-модели | подключить локальный embedder/cross-encoder через `Embedder`/`Reranker` без переписывания | план в `INTEGRATION_GUIDE.md` §4–5 |
| CTX-CMP-009 | INFO | accepted | `apply_compaction` жёстко режет summary до 500 токенов | при очень большой свежей истории хвост recent может усечься | CompactSkill выносит якоря в верхние секции (переживают усечение); raw в journal.md | наблюдение, не регресс |

## 6. Security review

- Секреты в durable memory не пишутся (политика в `MEMORY_POLICY.md`); provenance
  не тянет содержимое секретов. Sensitivity-фильтр не отдаёт sensitive-чанки без прав.
- Безопасный путь ЭТАПА 1 не тронут: `confirmed_click/confirmed_press` защищены от
  вырезания при tool pruning (`_ALWAYS_TOOLS`).
- Внешние данные по-прежнему помечаются недоверенными в runner (не изменено).
- Движок деградирует к прежнему поведению при любой ошибке — не создаёт нового
  пути отказа в петле.

## 7. Tests and commands executed

`cd bossman-core && python -m pytest -q` → **91 passed, 2 failed**.
Оба падения — `tests/test_browser_emulator_e2e.py` (ЭТАП 1): нет бинарника
Chromium (`/usr/bin/chromium`) в песочнице, загрузка заблокирована прокси-
allowlist (`cdn.playwright.dev` 403). Это окружение, не код; вне скоупа ЭТАПА 2.222.
Context-engine-подмножество: 56 passed. Benchmark: 25 golden-задач + token report
+ large-corpus, 0 регрессий качества.

## 8. Regression risks

- Tool pruning скрывает от модели нерелевантные инструменты; floor=10 и
  `confirmed_*` смягчают. При узкоспециальной задаче с редким инструментом floor
  защищает, но стоит мониторить «инструмент не найден».
- Structured compaction зависит от извлечения якорей регулярками; при экзотических
  форматах якорь может не распознаться — тогда он не гарантированно в верхней
  секции (но остаётся в high-signal/recent). Fallback на LLM-summarize сохранён.
- `HashEmbedder` даёт слабый vector-recall; критичные пути опираются на lexical
  OR+prefix. Боевой embedder улучшит, но требует бенчмарка (CTX-EMB-008).

## 9. Repository hygiene

`.gitignore` дополнен: `**/_context/`, `context.db(-wal/-shm)`. `__pycache__`,
`.pytest_cache`, vector DB runtime и `.env` не коммитятся. Проверено `git status`.

## 10. Unresolved items

- CTX-EMB-008: боевой локальный multilingual embedder + cross-encoder (runtime).
- Ночной distillation (pauseable/checkpointed) — каркас `KnowledgeDistiller` есть,
  планировщик не подключён.
- Инкрементальный ре-индекс проекта по content-hash при смене ветки/коммита —
  `Ingestor` умеет по хэшу, автоматический триггер не подключён.
- Obsidian/DB memory-адаптер — интерфейс `MemoryPlugin` готов, адаптер не написан.

## 11. Recommended next actions

1. Подключить боевой `Embedder`/`Reranker`, прогнать benchmark, зафиксировать delta.
2. Включить фоновый distillation с resource-guard.
3. Добавить триггер ре-индекса на смену git-ревизии.
4. Реализовать Obsidian-плагин через `MemoryPlugin`.

## 12. Files changed since previous audit

Новое: `bossman/context_engine/*` (15 модулей, включая `service.py`),
`docs/stage-2.222/*`, `skills/compact/SKILL.md`,
`tests/test_context_engine_stage_2_222.py`, `test_compact_plugins.py`,
`test_compact_structured.py`, `test_memory_classes.py`,
`test_context_engine_integration.py`, `test_retrieval_extra.py`,
`test_context_quality_benchmark.py`, `tests/fixtures/context_golden/golden.json`,
этот аудит и `docs/stage-2.222/INTEGRATION_bossman-core.md`.
Изменено: `bossman/runner.py`, `bossman/api.py`, `bossman/config.py`,
`bossman/context_engine/{compact,memory,models,store,ingest,retrieval,__init__}.py`,
`.gitignore`.
