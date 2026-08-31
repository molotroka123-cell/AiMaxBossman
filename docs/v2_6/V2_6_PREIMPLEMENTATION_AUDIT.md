# BOSSMAN V2.6 — PRE-IMPLEMENTATION AUDIT

**Репозиторий:** molotroka123-cell/AiMaxBossman
**START_REMOTE_SHA:** `4aaa17b4388829e56760e8a4d9d3cb33e01e3342` (IN SYNC, чистое дерево)
**Ветка:** `claude/bossman-control-v03-43igbk`
**Метод:** 7 параллельных read-only аудит-агентов по 20 capability-областям V2.6;
каждая находка проверена до точного файла:строки и production call-site.
Принцип: файл существует ≠ WORK; unit-тест ≠ production integration.

**Fast-path baseline (ДО V2.6), измерен на 4aaa17b:**
чистая оркестрация задачи (pick_agent + system prompt + tool schemas +
ContextBuilder init/build), N=300: **p50=0.156ms, p95=0.267ms**.

## 0. Ключевой структурный факт

Два независимых приложения без общего кода: `bossman-core/bossman/`
(петля агентов, Postgres/Redis) и `command-center/bcc/` (FastAPI-оркестратор,
SQLite). Почти каждая способность существует ДВАЖДЫ в несовместимой форме
(EventBus, tool registry, router, память, sandbox, recovery). `bossman_v3/` —
полностью мёртвый пакет: `grep -rn "bossman_v3" bossman/` → 0 результатов,
импортируется только тестами.

## 1. Карта существующего по модулям V2.6

| Модуль V2.6 | Что уже есть (файл:строки) | Статус | Недостающая дельта |
|---|---|---|---|
| **A. Uncertainty Engine** | Сигналы порознь: memory confidence+DISPUTED (`context_engine/memory.py:59-190`, в prompt через `service.py:58-68`) WORK; vision-confidence gate ≥0.72 (`computer_operator/policy.py:42`) WORK; staleness по generation (`manager.py:111-126`) WORK; `RiskSignal` (`cybersec/guards.py:113-147`) UNWIRED; `data_guardian.uncertainty` DEAD (v3) | **MISSING** (агрегат) | Детерминированный агрегатор `UncertaintySignal` + prod call-site |
| **B. Adaptive Compute / EVR / VOI** | `classify_reasoning` L0–L4 + `recommend_escalation` (`bcc/v2/model_intelligence.py:111-217`) — **только тесты**; бюджеты = деньги (cost_control WORK) и плоские счётчики (max_steps/replans) | **UNWIRED + MISSING** (EVR/VOI = 0 хитов) | Уровни C0–C4, EVR/VOI-хелперы, wiring `classify_reasoning` → `bcc/features/router.py:128` (сам roadmap репо, приоритет 6) |
| **C. Failure Pattern Learner** | `failure_memory.py` — write-only: единственная prod-запись `runner.py:367-370` с вырожденными полями (`error_class="task_failed"` всегда); read-API без prod-вызовов; кластеризация MISSING; `v3/self_healing` (Beta-posterior) DEAD | **PARTIAL (write) / MISSING (learn)** | Детерминированная классификация error_class + извлечение сигнатур/паттернов над verified-исходами, консервативно, через learning_guard |
| **D. Counterfactual Verifier** | Нет. Ближайшее: `computer_operator/verifier.py` (постусловия) WORK-scoped | **MISSING** | Ограниченная (1–3) детерминированная генерация критических допущений для irreversible/high-risk действий |
| **E. Verified Execution Cache** | НЕТ result-кэша нигде. Есть: provider prompt-cache (KV, `gateway/prompt_cache.py`) WORK-другое; TTL-кэши каталогов/skill-discovery — тривиальные | **MISSING** | Evidence-aware кэш с fingerprint/TTL/инвалидацией; честный call-site: parse-результаты файлов по sha256 (Модуль J), security-sensitive no-cache |
| **F. Task Compiler V2** | `bcc/v2/task_graph.py` (261 строк) — ПОЛНЫЙ протестированный DAG-движок с **нулём вызовов**; engine = линейные шаги; missions planner = regex-заглушка (`features/missions.py:20-29`); `projects/planner.py` — плоский YAML WORK-scoped | **UNWIRED** | EV-гейт компиляции + подключение task_graph к реальному потребителю; простые задачи остаются прямыми |
| **G. Model Portfolio Learning** | bcc router WORK (score: local/role/verified-caps/success-rate/tps/price, `bcc/v2/model_router.py:148-189`, prod `engine.py:526`), но success-rate **per-alias**, не per-task-class; `role_scores` ручные; gateway router статический (priority+health) | **PARTIAL** | Консервативные per-(model, task_class) verified-метрики с MIN_EPISODES-полом в скоринг, за флагом |
| **H. Flight Recorder** | Строки разбросаны: `runs`/`model_calls` (+window_fill, cache) /`tool_calls`/`approvals`/`cloud_calls`/`failures` — WORK-хранение; **нет** сборки трейса, **нет** explain в core (в bcc только `/router/explain`); per-task latency НЕ сохраняется (`took` только для Telegram, `runner.py:372-373`) | **PARTIAL** | Read-side сборка полного трейса + `explain` endpoint (0 накладных на hot path), персист latency, закрыть redaction-дыры (см. §2) |
| **I. Deep Research** | Web search / research pipeline — **0 хитов кода**. Единственный сетевой примитив `http` (`toolkit/net.py`) не выдан ни одному агенту; browser (22 tools, WORK) тоже не выдан; `search_everything` — только локальный корпус | **MISSING** | Типизированный claim→evidence→source пайплайн поверх существующих browser/http, QUICK/STANDARD/DEEP, VOI-bounded |
| **J. File Intelligence** | Только UTF-8 текст (`fs.read`); НЕТ pypdf/openpyxl/docx/pandas в зависимостях; zipfile — только security-инспекция (`sandbox/artifacts.py`) | **MISSING** | Типизированный parse-router: CSV/JSON/MD/TXT/ZIP (stdlib), DOCX/XLSX/PPTX (stdlib zip+xml), PDF (optional dep, честный UNAVAILABLE), provenance file/page/sheet/cell/hash |
| **K. Multimodal Perception** | `llm.vision_caption` → `vision.describe` WORK (узко: QA медиа, `projects/runner.py:117-121`); gateway vision-capability маршрутизация WORK; `browser.vision` bundle — производится, **никто не читает** (UNWIRED); operator: DOM/UIA-first, скриншот каждый шаг выбрасывается; OCR — 0 кода | **PARTIAL** | Типизированный perception-выход; починить 2 дефекта operator (см. §2); vision остаётся fallback'ом |
| **L. Media Router** | `tools/registry.yaml` + `projects/router.choose()` — **настоящий capability-router** WORK (t2v/i2v/tts/transcribe/qa_*…, privacy/budget/quality); bcc Images — полный пайплайн с mock-провайдером (real hard-blocked) | **WORK / PARTIAL** | Слить словарь capability; провенанс уже частично есть (`image_assets`) |
| **M. Artifact Engine** | Нет first-class артефактов: `ArtifactGate` — security-гейт (WORK для этого), `image_assets` — единственная tracked-output таблица (только images) | **MISSING** | Реестр артефактов: id/type/path/creator-task/evidence/hash/version + создание через существующие инструменты |
| **N. Personal Context Router** | `memory.md` инжектится ЦЕЛИКОМ каждый вызов (`runner.py:91-92`), слепое tail-clipping на 5% окна; при этом рядом полный ranking-движок (retrieved-блок WORK); `profiles.memory_namespace` полностью построен и **не вызывается ниоткуда**; Context Guardian — 0 кода | **PARTIAL / UNWIRED** | Relevance-отбор за флагом (OFF), RAW-fallback по умолчанию; НЕ удалять контекст без A/B-evidence |
| **O. Connected Data** | bcc plugins: 23 capability, 5 реальных, 18 STUB; **MCP мёртв на старте** — `mcp_runtime.load_sdk()` импортирует несуществующий `mcp.client.client` (`v2/mcp_runtime.py:41-45`), ImportError проглатывается; Telegram WORK (notify+approve); GitHub/email/calendar — STUB/UNWIRED (`office.py` = заглушки) | **PARTIAL** | Починить MCP SDK-импорт (реальный путь: `mcp.client.session`/`stdio`); typed capability discovery уже есть в plugins |
| **P. Cross-Artifact Reasoning** | `supersedes`/`contradicted_by` есть, не обходятся; `decision_timeline` — линейный; графа evidence нет | **MISSING** | Временный evidence-граф над артефактами Модуля J, typed refs |
| **Q. Python/Data Analysis** | НЕТ python-execution/dataframe-инструмента (grep pandas/jupyter → 2 хита, оба optional import в bcc local_index). Sandbox: shell.py fail-closed WORK (docker=AUTO, host=ASK); `sandbox/` пакет на FakeRuntime, real runtimes UNWIRED | **MISSING** | `analysis`-инструмент поверх существующего docker-sandbox-пути (не новый shell), та же approval-политика |
| **R. Voice** | `whisper_local`/`piper_local` — 2 YAML-строки в registry.yaml, маршрутизируются projects/router (capability `transcribe`/`tts`), бинарники вне репо; voice UI — заглушка | **PARTIAL (provider-dependent)** | Честный availability-probe; НЕ строить отдельную reasoning-архитектуру |
| **S. Scheduled Work** | bcc scheduler — production-grade WORK (`bcc/scheduler.py`: once/interval/daily, анти-double-fire, catch-up) + 8 feature-тикеров; core: `AgentSpec.schedule` парсится и **никем не исполняется** (UNWIRED) | **WORK (bcc) / UNWIRED (core)** | Исполнитель core-расписаний с owner/budget/stop-условиями, bounded |
| **22. Capability Registry** | Модели: `ModelTarget.capabilities` + `required_capabilities` + цены + окна (`gateway/config.py:48-74`) WORK, но словарь свободный (auto-выводится только `vision`); bcc: advertised-vs-verified probes WORK; медиа: `can:[...]` WORK | **PARTIAL** | Единый словарь констант (TEXT/CODE/VISION/OCR/STT/TTS/IMAGE_*/EMBEDDING…), потребители существующие |
| **23. DecisionSignals** | 0 хитов (`latency_priority`/`urgency` нет); межконтроллерная связь = нетипизированный `task["meta"]` | **MISSING** | Малый typed-объект, потребляют существующие контроллеры; НЕ второй оркестратор |
| **24. Fast Path** | O8 из FABLE5-аудита: каждый таск платит полный embed+hybrid search; baseline замерен (см. выше) | **PARTIAL** | Все новые хуки — за флагами/no-op по умолчанию; замер после |

## 2. Найденные дефекты (чинить в рамках V2.6)

| # | Дефект | Где | Класс |
|---|---|---|---|
| D1 | **MCP мёртв на старте**: импорт несуществующего `mcp.client.client`; `sdk_available()` проглатывает ImportError → «SDK not installed» навсегда при ~850 строках корректной обвязки | `bcc/v2/mcp_runtime.py:41-45` | P1 (broken feature) |
| D2 | **`ActionKind.BROWSER` недостижим**: policy валидирует BROWSER-URL, но `ExistingBrowserAdapter` не регистрируется в `subsystem.py:82-86` → runtime `RuntimeError("no backend supports BROWSER")` | `computer_operator/adapters/browser.py`, `subsystem.py` | P1 (broken path) |
| D3 | **Redaction-обход канонического `obs.redact`**: `tool_calls.args` пишутся сырыми (`runner.py:166,194,205,210` и `bcc/engine.py:808`), approval preview сырой (`runner.py:183-184` → Telegram), `cloud_calls.prompt_preview` сырой (`llm.py:146`) | оба приложения | P1 (secret-in-log) |
| D4 | `CapabilityRegistry` operator'а никогда не опрашивается — планировщику предлагаются действия, которых хост не умеет (ровно тот failure mode, который модуль обещает предотвращать) | `computer_operator/capabilities.py` | P2 |
| D5 | `context_os/integration.py:26-42` — хук определён и тут же выброшен (никогда не регистрируется) | bcc | P2 (dead by bug) |
| D6 | `model_intelligence.py:40` — `Any` используется без импорта (маскируется `from __future__ import annotations`) | bcc/v2 | P3 (latent) |
| D7 | Скриншот на каждом шаге operator'а снимается и выбрасывается — чистая стоимость + PII-поверхность без потребителя | `computer_operator/observer.py` | P3 (perf) |

## 3. Мёртвый/неподключённый код, учтённый планом

- `bossman_v3/*` — DEAD целиком (intentional freeze, НЕ трогаем: proposal-only инварианты).
- `bcc/v2`: `task_graph.py`, `model_intelligence.py`, `context_telemetry.py`, `obs_normalize.py` — UNWIRED; первые два подключаются модулями F и B.
- `context_engine/telemetry.py` (core) — DEAD (23 строки).
- `resource_brain.rank_models` — DEAD.
- `office.py` 6 заглушек, `world_intelligence` (внешняя Pythia) — UNWIRED external.
- `eval_scorecard.py`, `browser.vision` bundle — UNWIRED.

## 4. План реализации (только недостающая дельта, thin adapters)

Порядок фаз — по зависимости; каждая фаза: код + флаг + тесты + фиксация,
следующая не начинается при новых P0/P1. Все новые модули **не меняют
дефолтное поведение** (OFF/no-op) — локальные тесты владельца не задеваются.

- **PHASE 1**: Flight Recorder (read-side трейс + `explain` + latency персист + D3-redaction) · единый capability-словарь · `DecisionSignals`.
- **PHASE 2**: Uncertainty Engine (агрегатор) · Adaptive Compute (C0–C4, EVR/VOI, wiring classify_reasoning→router hook — D6 попутно) · Model Portfolio (per-class консервативные метрики).
- **PHASE 3**: Failure Pattern Learner (классификация error_class + сигнатуры, через learning_guard) · Counterfactual Verifier (bounded) · Verified Execution Cache · Task Compiler V2 (EV-гейт + task_graph потребитель).
- **PHASE 4**: Personal Context Router (за флагом, RAW default) · File Intelligence (stdlib-first) · Artifact Engine · Cross-Artifact refs.
- **PHASE 5**: Deep Research Engine (typed, VOI-bounded) · Connected Data (D1 MCP-фикс).
- **PHASE 6**: Multimodal Perception (typed выход, D2/D4-фиксы) · Media Router (словарь).
- **PHASE 7**: Python/Data Runtime (поверх docker-sandbox) · Voice probe · Scheduled Work (core executor).

## 5. Что НЕ делаем (границы честности)

- Не создаём второй Gateway/Registry/Router/Policy/Approval/Verifier/EventBus/
  Secret Store/Memory/Context Engine/Operator/Skills/Learning Guard/Cost/Sessions.
- Не размораживаем `bossman_v3` (proposal-only остаётся).
- Не заявляем quality-улучшений без A/B evidence: модули, требующие реального
  железа/провайдеров (real image-gen, whisper/piper бинарники, live web
  benchmark, OCR-модель), получают честный статус PARTIAL/provider-dependent.
- Не убираем raw-контекст ради токенов: selection только за флагом + RAW fallback.
- Существующие тесты не редактируются и не скипаются.
