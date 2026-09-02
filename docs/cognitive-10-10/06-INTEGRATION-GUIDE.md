# 06 — Integration Guide (для умной модели после аудита)

Пакет намеренно без внешних зависимостей. Подключение — адаптерами, не правками ядра.
Ядро (`memory/context/reasoning/tasks/verify`) менять только при провале holdout.

## 1. Быстрый старт (без инфры)

```python
from bossman.cognitive.runtime import CognitiveRuntime, RuntimeConfig
rt = CognitiveRuntime(RuntimeConfig(db_path="data/cognitive.sqlite3"))
rt.begin_task("t1", "fix benchmark race", constraints=["no secrets in logs"])
hits = rt.recall("race lock", owner_id="alice", project_id="bossman")
```

Тесты: `cd bossman-core && python -m pytest tests/test_cognitive_* -q` (30 passed).

## 2. Postgres-адаптер (канон `bossman.db` + `ContextStore`)

- Создать `bossman/cognitive/pg_adapter.py` (НЕ в этой ветке — после аудита):
  реализовать тот же интерфейс (`propose/search/get/delete`, journal CRUD)
  поверх `asyncpg` пула `bossman.db`; формат строк — 1:1 с `storage.SCHEMA`
  (колонки `memories10`, `journal`, `tombstones`, `conflicts`).
- Миграция: скопировать SQLite → Postgres `INSERT ... ON CONFLICT DO NOTHING`,
  сверить `count_verified()` до/после (требование restart-метрики).
- `WorkingMemory` (Postgres) ↔ `ThoughtState`: готовые конвертеры
  `CognitiveRuntime.working_state_to_thought / thought_to_context_items`.

## 3. Retrieval (`HybridRetriever` → S-компонента R)

- В `StoreMemoryPlugin.retrieve` заменить плоский скор на
  `memory.score_memory(m, query, weights=FROZEN, now_ts=...)` с
  `S = max(lexical, vector_cosine)` из `HybridRetriever.search`.
- Веса: `calibrate_weights(dev_pairs)` на dev → `.freeze()` → зафиксировать
  SHA dev-набора в отчёте → только потом holdout (`run_holdout`).

## 4. Context Compiler в gateway

- Заменить ручную сборку секций на `CognitiveRuntime.compile(items, fallback, raw_texts)`.
- `items` строить из: system invariants (P0), user goal (P0), working state (P1),
  constraints (P1), verified evidence (P1, только `independently_verified`),
  memory hits (P2), code/interfaces (P2), tool results (P2, `source_type="tool"`),
  unresolved (P3), current action (P0).
- Бюджет: `RuntimeConfig.context_budget_tokens` из лимита модели минус резерв.

## 5. DAG rollback/компенсации (доменные)

- Journal хранит `effect_id/receipt`; сами компенсации — колбэки домена:
  `compensations: dict[step_id, Callable[[JournalStep], Receipt]]`,
  вызывать при `cancel_branch`/`FAILED_FINAL`, receipt писать в шаг.

## 6. Fable через gateway

```python
class GatewayFable:  # реализует FableClient
    def ask(self, prompt, *, budget): return gateway_client.complete(...)
rt.attach_fable(GatewayFable())
rt.route_fable(prompt, FableOptions(...), local_ev=..., p0_security=..., budget=...)
```

Решение — всегда через `should_call_fable` (EV), бюджет — через CostGovernor.

## 7. Backup-проверка `DeletionResidual`

`assert_no_residual` покрывает store+кэш+hash. Адаптер обязан добавить
`extra_backup_probe(memory_id, content_hash)`: поиск в векторном индексе,
файловых снапшотах и бэкапах → включить в отчёт Red Team.

## 8. Что НЕ менять без повторного holdout

Веса R, пороги D, NEG-лексикон, TRANSITIONS journal, порядок `WriteFilter`,
`COMPILER_ORDER`, список `UNTRUSTED_SOURCES`. Любая правка → новый `run_holdout`.
