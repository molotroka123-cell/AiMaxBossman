# BOSSMAN V2.6 — ARCHITECTURE (Adaptive Intelligence + Capability Layer)

Все модули V2.6 — **тонкие типизированные адаптеры поверх существующих
авторитетов** (Gateway/Router/Tool Registry/Policy/Approval/Verifier/EventBus/
Memory/Context Engine/Learning Guard/Cost/Sessions). Ни один существующий
компонент не продублирован. Канонический инвариант исполнения не изменён:

```
intent → typed action → capability/tool resolution → policy/scopes →
approval → executor/provider → fresh observation → verification →
evidence/artifact → telemetry
```

`LLM → произвольный shell` по-прежнему не существует.

## Принцип: demand-driven activation

Ничто из V2.6 не запускается на каждом таске. Все новые контуры либо
детерминированы и стоят микросекунды (signals/uncertainty/compute level),
либо выключены по умолчанию (флаги), либо активируются только своим классом
задач (parse — при файле, research — при вызове инструмента, vision — по
capability-алиасу). Fast path замеряется до/после (см. V2_6_BENCHMARK_REPORT).

## Слои и их место

| Модуль | Файл | Тип адаптера | Активация |
|---|---|---|---|
| DecisionSignals (23) | `bossman/signals.py` | typed shared state; читают контроллеры, никто не «оркестрирует» | всегда, µs |
| Uncertainty Engine (A) | `bossman/uncertainty.py` | детерминированный агрегатор; самооценка модели может только ПОДНЯТЬ score | по запросу потребителя |
| Adaptive Compute (B) | `bossman/compute_budget.py` + `runner._select_compute` | C0–C4 пороги над signals; EVR/VOI-хелперы; mandatory security не скипается | `BOSSMAN_ADAPTIVE_COMPUTE` (OFF) |
| Failure Pattern Learner (C) | `bossman/failure_patterns.py` | классификация error_class на записи + консервативные кластеры на чтении КАНОНИЧЕСКОЙ failure memory | classify — всегда на провале; patterns — по запросу |
| Counterfactual Verifier (D) | `bossman/counterfactual.py` | ≤3 детерминированных допущения в approval-preview | только confirm-действия |
| Verified Exec Cache (E) | `bossman/exec_cache.py` | LRU+TTL+env-fingerprint; security-kinds не кэшируются | точечные call-sites (`real_window`, parse) |
| Task Compiler V2 (F) | `bossman/task_compiler.py` + bcc `missions._compile_plan` | EV-гейт + типовой контракт; bcc-план компилируется через существующий `v2/task_graph` | при плане миссии |
| Model Portfolio (G) | bcc `features/router.py` | per-(model, task.kind) verified-метрики c полом эпизодов в СУЩЕСТВУЮЩИЙ скоринг | `rules.adaptive` (OFF) |
| Flight Recorder (H) | `bossman/flight_recorder.py` + `GET /tasks/{id}/explain` | read-side сборка трейса из канонических таблиц; 0 накладных на петлю | по запросу |
| Deep Research (I) | `bossman/research/` | детерминированный claim→evidence→source конвейер над существующими browser/http; QUICK/STANDARD/DEEP; VOI-стоп | вызов инструмента |
| File Intelligence (J) | `bossman/file_intel.py` + `toolkit/fileintel.py` | typed parse-router (stdlib-first; PDF=pypdf опционально, честный unavailable); provenance file/page/sheet/slide/hash | `file.parse` |
| Multimodal (K) | существующий `llm.vision_caption` + gateway vision-capability + фиксы operator (BROWSER adapter, CapabilityRegistry) | vision = fallback, DOM/UIA первичны | capability-алиас |
| Media Router (L) | существующий `tools/registry.yaml` + `projects/router.choose` | capability-роутер уже был WORK; словарь сведён в `bossman/capabilities.py` | план проекта |
| Artifact Engine (M) | `bossman/artifacts_engine.py` + таблица `artifacts` + `artifact.create` | id/type/hash/version/creator/evidence; форматы честно ограничены детерминированными | вызов инструмента |
| Personal Context (N) | `bossman/personal_context.py` + `runner._memory_for_system` | критические ограничения в system, остальное — ранжированные чанки через СУЩЕСТВУЮЩИЙ context_engine; RAW fallback | `BOSSMAN_PERSONAL_CONTEXT_SELECT` (OFF: retention не доказан без A/B) |
| Connected Data (O) | существующие bcc plugins/MCP + фикс SDK-импорта (D1) | typed capability discovery уже в `plugins.Capability` | конфиг владельца |
| Cross-Artifact (P) | `bossman/evidence_graph.py` | временный evidence-граф, не персистится, typed refs | по запросу |
| Python Analysis (Q) | `toolkit/analysis.py` | `python3 -c` СТРОГО через существующий sandbox-путь shell.py (docker network-none; host=ASK) | вызов инструмента |
| Voice (R) | `bossman/voice_capability.py` + существующие whisper/piper-строки registry | честный availability-probe; отдельной reasoning-архитектуры нет | по запросу |
| Scheduled Work (S) | `bossman/schedule_runner.py` (core) + существующий bcc scheduler | исполняет уже парсившийся `AgentSpec.schedule`; owner/бюджет/stop | `BOSSMAN_SCHEDULES_ENABLED` (OFF) |
| Capability vocab (22) | `bossman/capabilities.py` | advisory-словарь, синонимы легаси-конфигов нормализуются | всегда |

## Безопасность (раздел 26)

- Ingest/egress: новые источники (parse, research) выходят к модели через
  существующую границу (rights=read → external-data header + `ingest_guard`);
  наружу — через `egress_guard`. Trust-ordering не изменён: контент файлов/веба —
  ДАННЫЕ, не команды.
- D3 закрыт: `tool_calls.args`, previews approvals, `cloud_calls.prompt_preview`
  проходят канонический `obs.redact`/`plugin_security.redact` в обоих
  приложениях; сырые args получает только handler, anti-replay hash — от сырых.
- Кэш (E) fail-closed по классам: live/credentials/security-state не кэшируются.
- Adaptive compute не может отключить security-верификацию (`MANDATORY_ACTIONS`).

## Learning Quality Guard (раздел 27)

Всё адаптивное остаётся под существующим guard'ом: failure-паттерны — только
advisory (proposal-only), эпизоды secret holdout отфильтрованы и на записи
(runner) и на чтении (extract_patterns); durable-продвижение любой выученной
стратегии/политики требует candidate→validation→shadow→verified→owner через
`bossman/learning_guard` (см. LEARNING_GUARD_CONNECTIVITY_AUDIT). Роутер bcc:
per-class метрики — вход скоринга, а не «релиз политики»; релиз `router.rules`
остаётся ручным решением владельца.

## Rollback

Каждый модуль отключается независимо: env-флаги (B/N/S), `rules.adaptive` (G),
отсутствие вызова (D/E/I/J/M/P/Q/R), и ни один не является зависимостью петли —
исключение любого возвращает поведение ядра до V2.6.
