# LLM ARCHITECTURE V2 — FOUNDATION (текущее состояние)

Status: PARTIAL (честно) · Date: 2026-08-30 · Аудит: `docs/audit/AUDIT_LLM_ARCH_V2_FOUNDATION_CODEX.md`

## 1. Целевая архитектура (не изменилась)

```text
USER GOAL → TASK ANALYZER → WORKING STATE → CANDIDATE RETRIEVAL
  → SECURITY+TEMPORAL FILTER → MATHEMATICAL CONTEXT OPTIMIZER
  → CONTEXT COMPILER → TASK GRAPH → MODEL INTELLIGENCE ROUTER
  → LLM/DETERMINISTIC EXECUTION → TYPED ACTION → POLICY/APPROVAL
  → EXECUTOR → FRESH OBSERVATION → WORKING STATE
```

Инвариант V1 неприкосновенен: `LLM → typed action → policy → approval →
executor → fresh observation`. V2 сидит НАД этим путём, не вместо.

## 2. Реализовано в Foundation (с тестами)

### 2.1 Working State (`bossman-core/bossman/working_memory.py`)
Append-only versioned rows: каждое материальное событие (update, append_observation,
append_failure, record_decision, complete_step, set_next_action, set_status)
пишет НОВУЮ строку version+1 → каждая версия = durable checkpoint, restore точен.
Optimistic concurrency: `update(task_id, version=expected)` при несовпадении с
latest → `OptimisticConcurrencyConflict`, silent overwrite невозможен.
Поля: objective, status, current_step, plan_version, constraints, invariants,
decisions, completed/pending_steps, open_questions, recent_failures,
observations, artifacts, relevant_files, next_action, context_version.
DDL: `db/schema.sql` (Postgres, additive). Тестированный транспорт —
aiosqlite-совместимый интерфейс соединения; asyncpg-адаптер — Phase 2.

### 2.2 Executable Task Graph (`command-center/bcc/v2/task_graph.py`)
TaskNode/TaskGraph, статусы PENDING/READY/RUNNING/SUCCEEDED/FAILED/BLOCKED/SKIPPED.
`validate_graph` (уникальность id, существование зависимостей, циклы через
итеративный DFS, известные action types, retry_limit ≤ 10), `ready_nodes`
(независимые узлы готовы одновременно — подготовка к параллелизму), mark_failed
→ BLOCKED зависимые / retry, skip-каскад, `graph_context_view` — компактный
локальный вид для LLM (только текущий узел + соседи, не весь план).

### 2.3 Observation Normalizer (`command-center/bcc/v2/obs_normalize.py`)
pytest/git/process/Stage13 → `NormalizedObservation` (kind, ok, summary, fields,
failure_names, truncated, raw_artifact). Bounded: сырой вывод (даже 50k строк)
уходит в raw_artifact (артефакт), в контекст — компактная структура ≤4000 chars.
Терпим к мусорному вводу (ok=False + note), детерминирован.

### 2.4 Model Intelligence Foundation (`command-center/bcc/v2/model_intelligence.py`)
`ModelCapabilityRecord` (UNKNOWN по умолчанию; ничего не выдумывает; merge с
`model_capability_checks`: verified > advertised). Reasoning levels L0-L4 с
прозрачными порогами (security/mutation ≥0.7 → L3+; verification+failures → L3/L4;
тривиальная задача → L0/L1) + reasons. `Confidence` (HIGH/MEDIUM/LOW/UNKNOWN) —
ТОЛЬКО рекомендация (replan/verification depth/escalation), никогда не
авторизует действие; Gateway/cloud policy остаётся авторитетной.
`ModelScorecardEvent` — сбор доказательств для будущей evidence-based маршрутизации.

### 2.5 Telemetry (`command-center/bcc/v2/context_telemetry.py`)
`ContextCompileTelemetry` / `WorkingStateTelemetry` / `TaskGraphTelemetry` +
emit через существующий EventBus (kinds `llm_arch.v2.context_compile`,
`llm_arch.v2.working_state`). Никакого нового metrics-фреймворка.

## 3. Специфицировано, НЕ реализовано (Phase 2 — вход с этого места)

### 3.1 Priority classes
P0 security invariants · P1 objective · P2 working state · P3 decisions ·
P4 failures/observations · P5 retrieved knowledge · P6 background.
P0/P1 — constrained selection (mandatory), не кандидаты рейтинга.

### 3.2 Mathematical Context Optimizer (контракт зафиксирован)
```text
Base_i   = wR·R + wI·I + wD·D + wC·C + wG·G + wF·F + wT·T + wQ·Q
Utility_i = Base_i / Tokens_i^alpha
Final_i  = Utility_i − lambda·max_{y∈Selected} Sim(x,y)   (MMR)
Recency(t) = 2^(−t/half_life);  security: half_life=∞ ( temporal truth beats recency)
info_gain_proxy = novelty (новое решение/причина отказа/смена состояния/контрпример);
                  энтропию НЕ фейкаем — имя честное
Dup: content-hash + normalized-text hash; семантика — опциональный similarity_fn
Knapsack: greedy utility/token (десятки кандидатов; точный рюкзак не нужен — задокументировать)
Mandatory reserve: P0+P1+essential-P2 вне конкурса; при переполнении бюджета —
  честная пометка MANDATORY OVER BUDGET, не молчаливое удаление
ContextDensity = Σ utility(selected optional) / Σ tokens(optional)
Все веса — в конфиге с safe defaults, не в бизнес-логике.
```
Порядок конвейера компилятора (порядок обязателен): objective → working state →
retrieval → SCOPE AUTHORIZATION → temporal validity → SECRET REDACTION →
features → optimizer → budget → dedup/MMR → pack. Релевантность никогда не
обходит авторизацию. Retrieved-текст — DATA с маркером untrusted, не authority.

### 3.3 Feature gate
`LLM_ARCH_V2_ENABLED` (конвенция `context_engine_enabled` из bossman config) —
добавить при первой вайринг-работе Phase 2.

## 4. Метрики успеха Phase 2 (не выдавать за интеллект модели)

CONTEXT_SELECTION_IMPROVEMENT (A/B на фикстуре: retained relevant, dupes,
mandatory, tokens, latency) и только затем REAL MODEL A/B (task success,
verification, retries). Density — инженерная метрика для A/B, не IQ.

## 5. Ограничения / известные долги

- Оптимизатор, Compiler V2, retrieval-каналы, feature gate, security-тесты
  компилятора — Phase 2 (см. аудит-матрицу).
- Working State: asyncpg-адаптер и миграция боевой БД — при первом живом запуске.
- Стаб `relevant_facts` в V1-компиляторе не тронут (V1 FROZEN, вмешательство
  запрещено) — закрывается Compiler V2.
- Два теста CC исторически виснут только на GitHub-раннере (известный баг
  NEXT.md §5, не связан с V2).

## 6. Рекомендуемый порядок Phase 2

1. Mathematical Context Optimizer (контракт §3.2 готов) + тесты §49
2. Context Compiler V2 + retrieval-каналы + security-тесты §52 + §53
3. Feature gate + A/B-фикстура §54 → отчёт CONTEXT_SELECTION_IMPROVEMENT
4. Gateway hook (post-resolve, pre-cache в `run_json`/`run_stream`) за флагом
5. Independent Verifier → Confidence-based escalation (данные уже собираются)
6. Evidence-driven model scorecards на ModelScorecardEvent
