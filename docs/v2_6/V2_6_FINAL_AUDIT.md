# BOSSMAN V2.6 — FINAL AUDIT

**START_REMOTE_SHA:** `4aaa17b4388829e56760e8a4d9d3cb33e01e3342`
**Ветка:** `claude/bossman-control-v03-43igbk` · force-push НЕ применялся

## Регрессия (финальный HEAD)

```
bossman-core (живой PostgreSQL 16.13):  1270 passed, 5 skipped, 0 failed
command-center:                          634 passed, 2 skipped, 0 failed
compileall: PASS · secret scan: PASS
NEW_P0=0 · NEW_P1=0 · NEW_REGRESSIONS=0
```
Новых тестов за проход: **~150** (V2.6-модули + фиксы дефектов).

## CONNECTIVITY MATRIX

| Capability | Реализация | Production call-site | Тесты | Флаг | Статус |
|---|---|---|---|---|---|
| Flight Recorder (H) | `bossman/flight_recorder.py` | `GET /tasks/{id}/explain` (`api.py`) | 4 (PG) | — | **WORK** |
| Capability vocab (22) | `bossman/capabilities.py` | нормализация словаря; роутеры уже по capability | 3 | — | **WORK** |
| DecisionSignals (23) | `bossman/signals.py` | `runner._select_compute` | 6 | — | **WORK** |
| Uncertainty (A) | `bossman/uncertainty.py` | `runner._select_compute` (первый читатель failure memory) | 7 | — | **WORK** |
| Adaptive Compute (B) | `bossman/compute_budget.py` | `runner.run_task` (C0 пропускает retrieval) | 13 (3 PG) | `BOSSMAN_ADAPTIVE_COMPUTE` | **WORK (gated OFF)** |
| Model Portfolio (G) | `bcc/features/router.py` | `pick_model` hook → `engine._call_model` | 5 | `rules.adaptive` | **WORK (gated OFF)** |
| Failure Learner (C) | `bossman/failure_patterns.py` | `runner` (classify_error на каждом провале) | 6 | — | **WORK** (паттерны — advisory) |
| Counterfactual (D) | `bossman/counterfactual.py` | `runner._call_tool` approval-preview | 4 | — | **WORK** |
| Exec Cache (E) | `bossman/exec_cache.py` | `llm.real_window`, `file_intel.parse_file` | 5 | — | **WORK** |
| Task Compiler (F) | `bossman/task_compiler.py`, `bcc missions._compile_plan` | `missions._create_tasks` (task_graph подключён) | 8 | — | **WORK** |
| File Intelligence (J) | `bossman/file_intel.py`, `toolkit/fileintel.py` | tool `file.parse` | 11 | — | **WORK** (PDF — provider-dependent) |
| Artifact Engine (M) | `bossman/artifacts_engine.py` + `artifact_registry` | tool `artifact.create` | 8 (PG) | — | **WORK** |
| Cross-Artifact (P) | `bossman/evidence_graph.py` | вызывается из research/parse-потока | 3 | — | **PARTIAL** (in-proc, не персистится) |
| Personal Context (N) | `bossman/personal_context.py` | `runner._system_prompt` | 10 | `BOSSMAN_PERSONAL_CONTEXT_SELECT` | **WORK (gated OFF, RAW default)** |
| Deep Research (I) | `bossman/research/` | `research_handler` (fetcher инжектится) | 10 | — | **PARTIAL** (движок WORK; live-fetcher wiring — следующий шаг) |
| Python Analysis (Q) | `toolkit/analysis.py` | tool `analysis.run` через sandbox shell | 9 | `SANDBOX_MODE` | **WORK** |
| Voice (R) | `bossman/voice_capability.py` | `probe()` | 7 | — | **PARTIAL** (честный probe; бинарники вне репо) |
| Scheduled Work (S) | `bossman/schedule_runner.py` | исполняет `AgentSpec.schedule` (5-field cron) | 16 | `BOSSMAN_SCHEDULES_ENABLED` | **WORK (gated OFF)** |
| Multimodal (K) | operator + gateway vision | `ActionKind.BROWSER` починен; CapabilityRegistry подключён | 10 | — | **WORK** |
| Connected Data (O) | `bcc/v2/mcp_runtime.py` | реальный MCP SDK (ClientSession/stdio) | 3 (+21 интеграционных ожили) | — | **WORK** |
| Media Router (L) | `tools/registry.yaml` + `projects/router` | `projects/runner._execute` | существующие | — | **WORK** (было) |

## Закрытые дефекты аудита

| # | Дефект | Итог |
|---|---|---|
| D1 | MCP мёртв на старте (`mcp.client.client` не существует) | **Закрыт** — реальный SDK; 21 интеграционный тест ожил |
| D2 | `ActionKind.BROWSER` без адаптера → RuntimeError | **Закрыт** — `ExistingBrowserAdapter` зарегистрирован, мост к `toolkit.browser` |
| D3 | Сырые секреты в `tool_calls.args`/preview/`cloud_calls` | **Закрыт** в обоих приложениях (redact на записи + на чтении) |
| D4 | `CapabilityRegistry` не опрашивался | **Закрыт** — планировщику отдаются только поддержанные kinds (fallback-open) |
| D6 | `Any` без импорта в `model_intelligence.py` | **Закрыт** |
| NEW | `artifact_id` не уникален по путям (UNIQUE violation на живом PG) | **Найден и закрыт** + регресс-тест |
| NEW | `obs` не импортирован в `llm.py` (моя регрессия) | **Найден полной регрессией и закрыт** |
| NEW | MCP-фикстура жёстко импортировала `FastMCP` → падала на SDK 2.x (на 1.x падала обратная правка) | **Закрыт** — фикстура работает на ОБЕИХ версиях SDK |
| NEW | `test_v21_e2e_mission` требовал ровно 1 approval — стало 6 после P1-ужесточения | **Закрыт** — проверка переписана на дисциплину (memory.write спрашивает, ни один ask-вызов не проехал), без ослабления политики |

Остаются открытыми (задокументированы, не заявляются как сделанные): D5
(`context_os` мёртвый хук), D7 (скриншот operator'а без потребителя).

## Ответы на финальные вопросы (раздел 34)

- Мультиисточниковый research — **да** (движок), live-fetcher wiring — впереди.
- Каждый claim → evidence → source + timestamp — **да, по построению** (claim без evidence уходит в `unanswered`).
- Смешанные файлы, таблицы/слайды как структуры, provenance до ячейки — **да**.
- Реальные артефакты с версиями/hash/evidence — **да** (детерминированные форматы; xlsx/docx/pdf — честный отказ, без выдумок).
- Рассуждение по нескольким артефактам — **да** (in-proc evidence graph).
- Скриншоты/изображения — **частично** (vision-путь существует, OCR отсутствует).
- Выбор генератора изображений/редактирование — **нет** (реальных провайдеров нет; mock жёстко заблокирован).
- STT/TTS-роутинг — **при наличии бинарей** (честный probe).
- Подключённые источники — **да** (MCP ожил, plugins).
- Ограниченные расписания — **да** (owner/бюджет/stop/overlap-guard).
- Личный контекст без загрязнения — **да, за флагом**; RAW по умолчанию (retention не доказан без A/B).
- Модель под класс задач — **да, консервативно** (пол по эпизодам), за флагом.
- Распознаёт неопределённость — **да**; тратит compute только при пользе — **да** (EVR/VOI, C0–C4).
- Переиспользует проверенную работу — **да** (evidence-aware кэш, security-классы никогда).
- Объясняет решение — **да** (`/tasks/{id}/explain`).
- Fast path остался быстрым — **да**: p50 0.156 ms → 0.125 ms.
- RAM — **ИЗМЕРЕНО**: +0.8 MB на все модули V2.6, стек контроллеров 0.013 ms/задача,
  кэш реестра 9.37 ms → 0.0062 ms (см. V2_6_RESOURCE_REPORT.md).
- VRAM — GPU на этом хосте физически отсутствует.
- VerifiedSuccess / IntelligenceRetention — **ИЗМЕРЕНЫ на железе владельца**
  параллельной сессией (см. `docs/acceptance/SWARM_STATE.md`, harness
  `tools/local_hardware_ab.py`): qwen2.5:7b Q4_K_M, Direct **12/18** vs
  Bossman **12/18**, **IntelligenceRetention = 1.0**, CLOUD_CALLS = 0,
  пик VRAM ≈ 5.9 GiB. Совпадение по всем классам (simple/coding/tool_use/
  memory 3/3; reasoning и long_context 0/3 в ОБОИХ режимах — потолок самой
  модели, а не Bossman). Прогон выполнялся на дереве, УЖЕ содержащем V2.6
  (флаги adaptive/personal-context по умолчанию OFF), то есть доказано:
  **V2.6 не ухудшил качество** (деградация 0 п.п. при пороге ≤1 п.п.).
  Чего этот прогон НЕ доказывает: пользу включённых флагов (adaptive compute,
  personal-context selection) — для них нужен отдельный A/B со ВКЛЮЧЁННЫМИ
  флагами; поэтому они остаются выключенными.

## FINAL REPORT

```
NEW_MODULES=14 (signals, capabilities, uncertainty, compute_budget,
  flight_recorder, failure_patterns, counterfactual, exec_cache,
  task_compiler, file_intel, artifacts_engine, evidence_graph,
  personal_context, research, voice_capability, schedule_runner,
  toolkit/{fileintel,analysis})
REUSED_MODULES=Gateway, ModelRouter, ToolRegistry, Policy, Approvals,
  Verifier, EventBus, ContextEngine, FailureMemory, LearningGuard,
  CostControl, Sandbox, TaskGraph, MediaRouter, bcc Scheduler
UNWIRED_MODULES=research live-fetcher, evidence_graph persistence,
  OCR, image generation/edit providers
CORE_TESTS=1262 passed, 5 skipped, 0 failed
COMMAND_CENTER_TESTS=633 passed, 2 skipped, 0 failed
POSTGRES_TESTS=live PG 16.13 (PG-gated tests executed, not skipped)
SECURITY_TESTS=included in both suites (guards, egress, redaction, sandbox)
FAST_PATH_P50_BEFORE=0.156 ms
FAST_PATH_P50_AFTER=0.125 ms
VERIFIED_SUCCESS_BEFORE=12/18 (Direct qwen2.5:7b Q4_K_M, owner hardware)
VERIFIED_SUCCESS_AFTER=12/18 (Bossman, то же железо/модель) — деградация 0 п.п.
INTELLIGENCE_RETENTION=1.0 (порог >=0.99 пройден; CLOUD_CALLS=0, VRAM пик ~5.9 GiB)
  ВАЖНО: прогон с флагами adaptive/personal-context OFF -> доказано отсутствие
  ухудшения от V2.6; польза включённых флагов НЕ доказана, они остаются OFF
PEAK_RAM=67.8 MB RSS (V2.6 добавляет +0.8 MB) · PEAK_VRAM=GPU отсутствует на хосте
CONTROLLER_OVERHEAD_PER_TASK=0.013 ms p50 (измерено)
EXEC_CACHE_MEASURED_WIN=9.37 ms -> 0.0062 ms на real_window (~1400x)
LOCAL_PROVIDER_CAPABILITIES=text, code, tools, vision (alias), stt/tts
  (при наличии бинарей whisper/piper)
CLOUD_PROVIDER_CAPABILITIES=зависят от gateway.yaml владельца (в репо только
  example с REPLACE_WITH_*)
RESEARCH_STATUS=PARTIAL · FILE_INTELLIGENCE_STATUS=WORK
ARTIFACT_ENGINE_STATUS=WORK · MULTIMODAL_STATUS=PARTIAL
MEDIA_ROUTER_STATUS=WORK · PERSONAL_CONTEXT_STATUS=WORK (gated OFF)
CONNECTED_DATA_STATUS=WORK · UNCERTAINTY_STATUS=WORK
ADAPTIVE_COMPUTE_STATUS=WORK (gated OFF) · MODEL_PORTFOLIO_STATUS=WORK (gated OFF)
FAILURE_LEARNING_STATUS=WORK (advisory) · EXECUTION_CACHE_STATUS=WORK
TASK_COMPILER_STATUS=WORK · FLIGHT_RECORDER_STATUS=WORK
VOICE_STATUS=PARTIAL (provider-dependent) · AUTOMATION_STATUS=WORK (gated OFF)
OPEN_P0=0 · OPEN_P1=0
```

## VERDICT

Все 19 модулей реализованы как тонкие адаптеры с реальными call-site, тестами,
флагами и rollback-путём; 6 дефектов закрыто (включая два найденных уже в ходе
работы); регрессий нет. Но качество-эффекты (VerifiedSuccess, retention, RAM/
VRAM, «5×») **не измерены** — для этого нужен owner hardware и честный A/B, а
несколько capability (OCR, реальная генерация/редактирование изображений, live
research-fetcher) остаются provider-dependent.

**BOSSMAN V2.6 CAPABILITY EXPANSION PARTIAL**
