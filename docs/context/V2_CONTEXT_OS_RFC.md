# V2 Context OS — RFC (Phase 1)

Status: DRAFT · Branch: `claude/bossman-v2-context-os` · Base: `d3a8935` (V1 RC ac9506f)
Author: BOSSMAN architecture · Date: 2026-08-30

## 1. Проблема

V1 отдаёт LLM один плоский контекст (system + facts dump + recent messages + tool schemas). Результат: загрязнение, перерасход токенов, потеря детерминизма, невозможность продолжить после рестарта без истории чата.

Следующий скачок — не роутер моделей, а архитектура контекста/планирования/исполнения.

## 2. Цель Phase 1

Ввести единый `Context OS` — ни один агент сам не собирает себе контекст.

```
Memory Store (5 каналов)
  ↓  Semantic Recall + Temporal Filter
Decision Store / Failure Store
  ↓
Hierarchical Context Manager (global → project → task → step)
  ↓
Context Compiler (цель + инварианты + состояние + память + ошибки + tools + next_action)
  ↓
Token Budgeter (жёсткий рез, stable слои → prompt cache)
  ↓
LLM (через Stage3 Gateway)
```

## 3. Пять каналов памяти (вместо одной vector DB)

| Канал | Хранит | Таблица/источник | Пример предиката |
|-------|--------|------------------|------------------|
| FACTS | фактические утверждения | `facts` (уже есть) | `project uses Stage13` |
| DECISIONS | архитектурные решения | `decisions` (NEW) | `decision: use existing Stage13` |
| EVENTS | что произошло | `events` + `run_events` + `tool_calls` | `task#12 forked` |
| FAILURES | симптом→причина→фикс→результат | `failures` (NEW) | `failure: git merge orphan` |
| PROCEDURES | как обычно выполнять задачу | `procedures` (NEW, позже) | `procedure: coding merge` |

Hybrid retrieval: семантика (FTS) + фильтр времени + фильтр области (global/project/task).

## 4. Иерархия контекста

```
global memory      — инварианты системы, политики, роли агентов
  project memory   — факты/решения проекта, структура репо, conventions
    task memory    — цель задачи, план, DAG, бюджет, накопленные факты
      step scratchpad — наблюдения текущего шага, tool results, tail логов
```

Агент на шаге получает ТОЛЬКО нужный слой. `global` и `project` — stable (идут в prompt cache), `task/step` — dynamic.

Реализация: `bcc/context_os/hierarchical.py` — `HierarchicalContextManager` с методами `get_global()`, `get_project(project_id)`, `get_task(task_id)`, `get_step(run_id, step)`, каждый возвращает `ContextLayer {text, tokens_est, hash}`. Кэш привязан к `hash`/`SHA`, не ко времени (твоё требование п.9).

## 5. API Context OS

Единственный контракт:

```python
ctx = await Context.request(
    task_id=12,
    objective="fix merge",
    max_tokens=8000,
    include=["decisions", "relevant_facts", "recent_failures", "current_diff", "next_action"],
)
# ctx.prompt — готовый prompt, ctx.usage — токены по слоям, ctx.hash
```

Поля `include` — белый список каналов. Никаких `*` и `all`. Агент не может запросить `preferences` у `finance agent`.

Расположение: `command-center/bcc/context_os/compiler.py` (`ContextCompiler`), вызывается из `Stage3 Gateway` перед каждым `llm.chat`, не из `engine.py` напрямую.

## 6. Схемы Decision / Failure

### decisions (NEW)

```
id PK, key TEXT UNIQUE  -- "use-existing-stage13@v1"
decision TEXT NOT NULL  -- "use existing Stage13"
reason TEXT             -- "avoid second PC engine"
alternatives_rejected JSON -- ["custom pyautogui executor"]
scope TEXT              -- "Bossman V1" / "project:bossman"
created_by TEXT, created_at, superseded_by FK → decisions.id
```

Правило: UPDATE запрещён, замена = новая строка + `superseded_by`. История не переписывается.

### failures (NEW)

```
id PK, symptom TEXT, root_cause TEXT, attempted_fix TEXT, result TEXT,
files JSON, test TEXT, task_id FK, run_id FK, created_at
```

Перед каждым `fix` агент обязан `FailureStore.search(symptom)`.

Обе таблицы — в `bcc/db.py` `metadata`, миграции через `V2_NEW_COLUMNS` + `create_all`.

## 7. State Machine + Checkpoint

Агент — машина `PLAN → EXECUTE → OBSERVE → VERIFY → RECOVER → DONE`, не чат.

`task_runs.checkpoint` уже хранит `{messages, step, note}`, расширяем до `{state, step, plan, next_action, confidence}`. Checkpoint пишется после каждого материального события (`decision`, `code_modification`, `test_result`, `approval`, `external_mutation`), не каждые N токенов.

Восстановление после рестарта: `engine.recover()` читает `state`, а не replay истории.

## 8. Token Budgeter + Prompt Cache

Разделение prompt:

```
system policy      stable   → cache 1h
architecture       stable   → cache 1h
tool schemas       stable   → cache 1h
repository facts   semi-stable → cache 5m
task state         dynamic  → no cache
```

Budgeter режет по приоритету: `objective + invariants + current_state` никогда не режутся, `relevant_facts`/`recent_failures` — по релевантности, `history tail` — первым.

## 9. Что НЕ делаем в Phase 1

Model Routing/Escalation/Ensemble (п.11-13), DAG-параллелизм (п.18), полная Procedural Memory (п.6) — Phase 2/3. Не добавляем новый inference framework.

## 10. План файлов Phase 1

```
command-center/bcc/context_os/__init__.py
command-center/bcc/context_os/hierarchical.py  — 4 слоя + hash-кэш
command-center/bcc/context_os/stores.py        — DecisionStore, FailureStore
command-center/bcc/context_os/compiler.py      — ContextCompiler + TokenBudgeter
command-center/bcc/context_os/state.py         — StateMachine, checkpoint hook
command-center/bcc/db.py                       — decisions, failures таблицы
command-center/tests/test_context_os_*.py      — hierarchical, compiler, stores, state
bossman-core/bossman/gateway/app.py            — hook compiler перед llm.chat (Phase 1 stub)
```

## 11. Критерии готовности Phase 1

- `Context.request` — единственный путь сборки prompt; прямой сбор в агентах удалён/депрекейт
- Hierarchical: task агент получает ≤8000 токенов, global/project не дублируются в step
- Decision/Failure: 100% решений из RFC пишутся в DecisionStore, поиск failures перед fix покрыт тестом
- State Machine: рестарт в `VERIFY` продолжает с `VERIFY`, не с `PLAN`; checkpoint на каждом `test_result`
- Cache: stable слои имеют `cache_control` для Anthropic, fallback — обычный inference, `Cost Governor` учёт сохранён
- Тесты: `test_context_os_*` зелёные, `secret_scan` PASS, `git diff --check` чист

## 12. Риски

- Слишком агрессивная фильтрация скроет нужный факт → метрика recall precision + fallback `include=["relevant_facts"]` с порогом
- Hash-кэш на изменчивых фактах → инвалидация по `facts.updated_at`, не по времени
