# Bossman (AiMaxBossman)

**Bossman** — платформа личных ИИ-агентов, которая работает на вашем собственном
компьютере или домашнем сервере. Агенты выполняют задачи в браузере, терминале
и на рабочем столе, но каждое действие проходит через политику, лимиты и —
для необратимых шагов — явное подтверждение владельца. Всё происходящее
попадает в аудит. Облако используется осознанно и под счётчиком стоимости;
локальные модели можно использовать без единого облачного вызова.

Состав репозитория:

| Приложение | Путь | Роль |
|---|---|---|
| **bossman-core** | `bossman-core/bossman/` | ядро-агентная ОС: шлюз моделей, выполнение задач, память, подтверждения, песочница, обучение навыкам |
| **command-center** | `command-center/bcc/` | панель управления оператора: браузер, терминал, MCP-хаб, ревью-гейты, аналитика |

## Ключевые возможности

### Ядро (bossman-core)

- Запуск задач агентами: `bossman task "…"` или через API/UI; агенты = папки
  с настройками и памятью, без оркестраторов-фреймворков.
- Единый шлюз моделей (Gateway): маршрутизация между локальными и облачными
  провайдерами, облачная политика `never/ask/allow`, fail-closed при сомнениях.
- Cost Governor: лимиты расходов на запуск/задачу/проект/день, предупреждение
  и жёсткая остановка при превышении — лимиты задаёт владелец.
- Планировщик проектов: план → задачи → файловое состояние проекта
  (`bossman project plan/run/state`).
- Подтверждения владельца: необратимые действия — только после явного «да»
  (UI, Telegram-кнопки или выданное владельцем разрешение).
- Каноничная память на PostgreSQL: рабочая память, память решений и память
  отказов с восстановлением после рестарта.
- Периметр безопасности: SSRF-защита (pinned DNS), SQL только на чтение,
  изоляция путей/симлинков, argv-only команды без произвольного shell.
- Sandbox для запуска кода агентов (флаг `BOSSMAN_SANDBOX_ENABLED`, по
  умолчанию OFF): уровни изоляции от локального до gVisor/KVM; без поддержки
  железа тесты честно пропускаются, а не имитируют успех.
- CyberSec-слой (по умолчанию OFF): фаервол prompt-инъекций на границе
  приёма внешних данных, защитные модули; тренировочная лаборатория — за
  отдельным тройным гейтом.
- Уведомления: Telegram-бот со подтверждениями «да/нет» и webhook-секретом.
- Video Factory, Dev Factory, AI Lab, Search Everything — прикладные
  подсистемы ядра: генерация видео, инженерные конвейеры, лаборатория
  экспериментов, поиск по всему.
- Computer Operator: исполнитель действий на десктопе с allowlist приложений
  и защитой от зацикливания.
- Remote Client: доступ с телефона (PWA/iOS) по device-токенам со скоупами;
  наружу публикуется только клиентская поверхность.

### Universal Computer Apprentice (автономный исполнитель)

- Конечный автомат **observe → act → verify → recover**: агент наблюдает мир,
  действует, проверяет результат и восстанавливается после сбоев (зависший UI,
  таймаут, падение процесса) — с точками возврата и resume после рестарта.
- Семантические цели UI: агент указывает, *что* нажать (кнопка «Отправить»),
  а не слепые координаты.
- Идемпотентность побочных эффектов: повторная отправка формы или повторный
  клик не создаёт дубликат — эффект имеет собственную идентичность.
- Durable safety store (SQLite): согласия, счётчики эффектов и санкции
  переживают рестарт процесса.
- Одноразовые подтверждения владельца: разрешение выпускается сервером
  (nonce), а не «объявляется» самим агентом.
- Граница outreach: агент может собирать **публичные** бизнес-данные,
  готовить черновики и демонстрации и останавливаться в статусе
  `WAIT_APPROVAL`; массовая рассылка не предусмотрена архитектурой.
- Технические санкции, рейтинг надёжности и circuit breaker: проблемные
  цели/действия временно блокируются.

### Обучение навыкам (Learning Guard)

- Запись эпизодов работы в семантически якоренные эпизоды с очисткой
  секретов.
- Превращение проверенных эпизодов в **навыки**: shadow-повтор на свежих
  наблюдениях, повышение только против измеренного базового уровня
  `VerifiedSuccess`, откат (rollback) при деградации.
- Autonomy Trainer и локальное когнитивное переиспользование подключены к
  рантайму за отдельными флагами (по умолчанию OFF).

### Claude Code как внешний «учитель»

- При неспособности справиться самостоятельно агент обращается к Claude Code
  как к **недоверенному** внешнему учителю: герметичное рабочее пространство,
  чистка окружения, независимая проверка его патчей.
- Патч учителя применяется только после независимой верификации тестами;
  санкции и circuit breaker ограничивают злоупотребление.

### Command Center (панель оператора)

- Веб-панель на FastAPI: обзор, браузер, терминал, агентная карта (agent map),
  миссии, оркестры, навыки, «самолечение» (healing), governor, роутер моделей,
  OpenRouter, ресурсы, изображения, coding-сессии, форки, приложения, бенчмарки.
- MCP-хаб: подключение внешних MCP-инструментов (опциональная зависимость).
- Cache Intelligence: аналитика кэша промптов и когнитивного переиспользования
  (экономия токенов), advisory-only подсказки.
- Deep Fix Mode и Review Gates: глубокая починка с ревью перед применением.
- Единый реестр инструментов с решениями AUTO/ASK/DENY и шифрованный
  секрет-хранилище (Vault, Fernet).

### Приложения (apps/)

- 8 прикладных приложений поверх ядра: `bossman-accountant`, `social-farm`,
  `travel-architect`, `exam-trainer-ai`, `file-commander-mini`,
  `pc-autopilot-mini`, `ai-3d-maker`, `ai-webcam-vision` — манифесты и ТЗ,
  часть уже с исходным кодом.

### AI Company Mode

- Режим «AI-компании» (`bossman/company`: планировщик, исполнители,
  верификаторы, синтетический SEO) — **за флагом** `AI_COMPANY_MODE_ENABLED`
  и по умолчанию выключен; модель не может ни одобрять, ни верифицировать.

## Архитектура

```
bossman-core/bossman/
  gateway/          шлюз моделей: роутер, failover, prompt-cache, телеметрия
  api.py, runner.py FastAPI + единая петля выполнения агентов
  approvals.py      очередь подтверждений владельца
  computer_operator/  десктоп-исполнитель (Stage 13, allowlist)
  apprentice/       Universal Computer Apprentice: engine, durable, owner_auth,
                    outreach, teacher(+sandbox), skills, sanctions, flags
  learning_guard/   autonomy trainer, promotion, holdout, A/B
  benchmark/        внутренний бенчмарк (CLI `python -m bossman.benchmark`)
  sandbox/          изолированные рантаймы (SAFE/gVisor/KVM)
  cybersec/         фаервол инъекций, защитные модули (OFF по умолчанию)
  cost_control/     бюджеты и лимиты расходов
  notifications/    Telegram и dispatcher уведомлений
  video_factory/, dev_factory/, ai_lab/, search_everything/, research/,
  context_engine/, remote_client/, projects/, company/
command-center/bcc/  FastAPI control plane + ui/ (страницы оператора)
bossman_shared/      общие контракты кэш-интеллигенса (отдельный пакет)
learning/            журнал обучения (trace)
apps/                прикладные приложения
bossman-infra/       инфраструктура: LiteLLM, llama-swap, Postgres+pgvector,
                     Redis, Open WebUI, Uptime Kuma
tools/               утилиты CI (в т.ч. ci_secret_scan.py)
```

Требования: Python 3.11+, PostgreSQL (каноничная память), Redis (опционально),
Playwright для браузерной автоматизации.

## Слои ОС: кто / где / какая модель / как / доказано ли

| Слой | Вопрос | Где в репозитории | Состояние |
|---|---|---|---|
| Organization Layer | КТО делает работу (отделы, роли, команды, контракты, казначейство) | `bossman-core/bossman_v3/organization/` | INTEGRATED (флаг `BOSSMAN_V3_ORGANIZATION`) |
| Fleet OS | ГДЕ исполняется (узлы, лизинги с fence, очередь, приватность) | `bossman-core/bossman_v3/fleet/` | INTEGRATED (флаг `BOSSMAN_V3_FLEET`; удалённый транспорт — не production) |
| Model Router | КАКАЯ модель/провайдер | `command-center/bcc/v2/model_router`, `bossman/gateway` | VERIFIED |
| Task / Action Engine | КАК исполняется реальная операция | V2 `command-center/bcc` (заморожен на `ffda281`) + V3 `bossman_v3/execution` | VERIFIED |
| Verifier | ПРОИЗОШЛО ЛИ на самом деле | `bcc.v2.verification`, `bossman_v3/memory` (журнал: finished = чек ∧ проверка) | VERIFIED |
| Memory | ЧТО должно сохраниться | `bossman_v3/memory`, `organization/memory_scope.py`, `bossman/context_engine` | IMPLEMENTED |
| Autonomous Operations | КОГДА начинать новую работу | — | НЕ НАЧАТО (отдельная миссия) |
| Benchmark / CEO Scorecard | КАК измеряем зрелость и регрессии | `bossman_v3/benchmark_overlay` (пассивный), `docs/benchmark/current-scorecard.json`, `bossman/benchmark` | IMPLEMENTED (детерминированные стресс-бенчмарки; live-прогон не записан) |

Наличие drop-in ZIP или дизайн-документа не делает слой рабочим: статус выше — только по коду и тестам в репозитории.

## Live OS Scorecard

Живой снимок зрелости по десяти осям. Источник истины — `docs/benchmark/current-scorecard.json`;
README — только проекция, которую перерисовывает `python scripts/update_readme_scorecard.py`
(проверка в CI: `--check` → `README_SCORECARD_CURRENT=PASS/FAIL`). Оценки могут снижаться —
это требование, а не сбой. Подробные улики по каждой оси: `docs/benchmark/current-scorecard.md`.

<!-- BOSSMAN_LIVE_SCORECARD_START -->
| # | Ось системы | Оценка | Статус | Уверенность | Улики |
|---|---|---:|---|---|---|
| 1 | Execution Truth | 8.5/10 | VERIFIED | HIGH | EH-01: улика verified=True доверяется только с HMAC-подписью доверенного signer'а; журнал подписывает закрытый шаг (000f331, bossman-core/tests/test_v3_evidence_signing.py); FL-01: fence движка — зомби-воркер не пишет receipt/статус, внешний эффект не повторяется (2487694, command-center/tests/test_fence_fl01.py); TaskJournal: finished = receipt ∧ verified (bossman-core/tests/test_v3_memory_kernel.py, test_v3_invariants.py, test_v3_compound_resume.py); FleetExecutionBridge отбрасывает поддельные verified-улики, пересобирая их из журнала (test_v3_fleet_e2e.py::forged evidence, 084ad3a); PLACED→VERIFIED запрещён в LEGAL_TRANSITIONS (test_v3_fleet_core.py); V2 action contract тесты (command-center/tests/test_action_contract.py); EH-05: FAIL гейта без явного requeue = сбой гейта (4c8fec2, command-center/tests/test_gate_contract_requeue.py) |
| 2 | Security | 8.3/10 | VERIFIED | HIGH | P0-A gateway loopback fail-closed при proxy-заголовках (cbdabf2, bossman-core/tests/test_gateway_loopback_proxy.py); P0-B монотонная алгебра политики DENY⊗X=DENY, hook-ASK⊗AUTO=ASK (eb0e969, command-center/tests/test_policy_algebra.py); Fleet: PRIVATE/LOCAL_ONLY — жёсткий гейт планировщика, CredentialBroker выдаёт только гранты (test_v3_fleet_core.py); Secret scan в каждом CI-прогоне (tools/ci_secret_scan.py); EH-01: ключ подписи улик 0600 вне модели; fail-closed без ключа (bossman_shared/evidence.py); OpenRouter только через env → ключ в vault, не в репозитории/логах/API (3e673d3, test_feat_openrouter_env_bootstrap.py); SEC-01 секрет-скан 2.0: 13 семейств паттернов, энтропия, ZIP по содержимому, запрещённые файлы (tests/test_ci_secret_scan.py); SEC-03 rate-limit/lockout на /api/login до сравнения токена (command-center/tests/test_login_rate_limit.py) |
| 3 | Tooling / OS Integration | 7.0/10 | INTEGRATED | MEDIUM | V2 реестр инструментов/decide_effect/approvals заморожен на ffda281 и покрыт command-center/tests/test_v21_*; V3-порты → живой bcc (bossman-core/tests/test_v3_command_center_adapters.py); OpenRouter как провайдер из окружения (ключ+модели — данные), тот же tool loop через фейковый провайдер детерминированно (command-center/tests/test_feat_openrouter_agent_flow.py) |
| 4 | Organization Layer | 7.0/10 | INTEGRATED | MEDIUM | OrganizationRuntime над V3ExecutionBridge/FleetExecutionBridge; ORG-03..07, MEM-02 закрыты (084ad3a); E2E: родитель не COMPLETE при непроверенном ребёнке, рестарт без дублей (bossman-core/tests/test_v3_organization_e2e.py); ORG-01/02: фича `/api/org/*` за флагами, агент организации → агент V2, задача+run V2 на контракт; PlannerPort/DeterministicPlanner; контракт без шагов → BLOCKED/no_executable_steps (efaa55f, test_v3_organization_planner.py, command-center/tests/test_feat_organization.py) |
| 5 | Fleet & Resources | 6.5/10 | INTEGRATED | MEDIUM | FleetStore/LeaseManager/WorkQueue CAS-claim, fencing, reclaim (bossman-core/tests/test_v3_fleet_core.py, 20 тестов); E2E #1–#4: размещение→исполнение, смерть узла→resume без дублей, приватность, двойной claim (test_v3_fleet_e2e.py); FL-01: task_runs.fence, условные записи и heartbeat, assert_fence до эффекта в V2 и в V3-адаптере (2487694, test_fence_fl01.py); 10 safety proofs (реестр + истёкшая аренда без власти + размещение не штрафует исполнителя); REMOTE_TRANSPORT_PRODUCTION_READY=NO, NODE_AUTH_PRODUCTION_READY=NO (test_v3_fleet_safety_proofs.py) |
| 6 | Memory / Context | 6.2/10 | IMPLEMENTED | MEDIUM | TaskJournal + FailureMemory + ContextAssembler с редакцией (bossman_v3/memory); ScopedKnowledge: явное наследование include_parents, экспорт по allowlist (MEM-02, 084ad3a) |
| 7 | Testing / CI | 7.3/10 | VERIFIED | MEDIUM | 4 workflow (root-ci, Bossman Core CI, Command Center CI, V2 Auto-Repair) зелёные по точному SHA 714bb01/fb201a4; Полный регресс ядра на стабильном HEAD; benchmark-тесты проверяют SHA; README_SCORECARD_CURRENT проверяется в root-ci (scripts/update_readme_scorecard.py --check); Пассивный benchmark overlay: 9 hard fail'ов, 5 стресс-бенчмарков над реальными Organization/Fleet/CompoundRunner, мост в scorecard `--from-benchmark` (bossman-core/tests/test_v3_benchmark_overlay.py, test_v3_org_benchmark.py); Сквозной E2E миссия→организация→флот→узел→V3→файл→свежее чтение→подписанные улики→VERIFIED→ревью→COMPLETE→benchmark→scorecard (bossman-core/tests/test_v3_cross_layer_e2e.py); Реестр 10 доказательств безопасности флота, каждое привязано к существующему тесту (test_v3_fleet_safety_proofs.py) |
| 8 | Observability / CEO Control | 5.5/10 | PARTIAL | LOW | control_plane снимки организации и флота из durable store (bossman_v3/organization/control_plane.py, fleet/control_plane.py); GET /api/control-plane: organization/queue/treasury/fleet/slo/attention из durable-источников, снимок совпадает после рестарта (5709611, command-center/tests/test_feat_control_plane.py); AST-скан: события и run-лог не несут messages/prompt/api_key/cookie/token (test_no_private_fields_in_events.py) |
| 9 | Treasury / Cost | 6.5/10 | IMPLEMENTED | MEDIUM | TR-01/02/03: актуальные цены 5 семейств (provisional), токен-оценка по скрипту, потолок in·max(p_in,p_cw)+out·p_out (e724a44, tests/test_fable_budget_pricing.py); ResourceTreasury: INV-3 PartitionViolation, конверты org→dept→mission (test_v3_organization_core.py) |
| 10 | Mission UX / Command Center | 6.0/10 | IMPLEMENTED | MEDIUM | Command Center UI (command-center/ui) с approvals, задачами, инструментами; command-center/tests зелёные; Компактная навигация + OpenRouter Connect (исправлен appendChild) — ветка claude/v2-ui-sidebar-compact, 1401 passed, НЕ влита; Данные для страницы владельца доступны: `/api/control-plane`, `/api/org/snapshot` (UI-страница — TZ-10, не сделана) |

- **Current bottleneck:** EH-02/EH-04: верификаторы пост-состояния только для 4 семейств и нет единого finalize(); флот/SLO не подключены к Command Center (control-plane честно показывает enabled=false / NOT_IMPLEMENTED); Windows-job и coverage-gate отсутствуют (CI VERIFIED только по Linux).
- **Next highest-value fix:** TZ-01 §2.2–2.3: ActionReceipt + детерминированные верификаторы пост-состояния (terminal/files/apps/github), единый finalize() с grep-тестом; затем TZ-08 §2.1–2.2 (ретеншн, гистограммы/SLO) и TZ-07 windows-job.
- **Last evidence SHA:** `97b3091e10fc829f2967ff57c5b3d2415d412ec0` · **Current HEAD SHA:** `97b3091e10fc` · **Evidence freshness:** FRESH
- **Last scorecard update:** 2026-09-05
- **Benchmark hard failures:** none observed
- **Live hardware attestation:** PENDING
- **Exact-SHA CI:** NOT_RUN

_Среднее (вторично, не авторитетно): 6.9/10. 10.0 = ATTESTED; ни одна ось не ATTESTED без живой аттестации железа._
<!-- BOSSMAN_LIVE_SCORECARD_END -->

### Текущее узкое место

См. **Current bottleneck** и **Next highest-value fix** в блоке выше — они выбираются по
серьёзности hard-fail → риску безопасности/исполнения → самой низкой доказанной оценке →
влиянию на реальные миссии → порядку зависимостей, а не по самой низкой цифре.

## Для кодирующих агентов

Use the README Live OS Scorecard as the latest human-readable maturity snapshot, then verify
all relevant claims against current repository evidence before modifying architecture.

Перед архитектурной работой:

1. прочитать Live OS Scorecard выше;
2. сверить **Last evidence SHA** с `git rev-parse HEAD` (freshness `PARTIALLY_STALE` = код менялся после последней улики);
3. открыть blockers затронутой оси в `docs/benchmark/current-scorecard.json`;
4. прочитать актуальную архитектуру: `docs/v3/NEXT_SESSION_HANDOFF.md`, `docs/v3/organization/ARCHITECTURE.md`, `docs/v3/fleet/ARCHITECTURE.md`, аудит `docs/audit/2026-09-05_AUDITOR_SCORECARD_10x10.md` и ТЗ `docs/audit/tz/`;
5. README сам по себе — не доказательство исполнения: доказательство — тесты, чеки и CI по точному SHA;
6. после материальной работы обновить `current-scorecard.json` и выполнить `python scripts/update_readme_scorecard.py`.

## Дорожная карта (по состоянию, без дат)

- **DONE** — V2 action contract и заморозка `ffda281`; V3 ядро (журнал, CompoundRunner, адаптеры); Organization Layer + точка входа `/api/org/*`; Fleet OS (локальный транспорт) + FL-01 fence движка; подписанные улики (EH-01); P0-A/P0-B; TR-01..03; Live Scorecard; пассивный benchmark overlay с 5 стресс-бенчмарками; `/api/control-plane`; OpenRouter через env (ключ и модели); секрет-скан 2.0; rate-limit логина; сквозной E2E и реестр 10 доказательств безопасности флота.
- **INTEGRATING** — benchmark-отчёт → scorecard (`--from-benchmark`) в регулярном прогоне; закрытие ТЗ аудита по порядку владельца.
- **NEXT** — TZ-01 §2.2–2.3 верификаторы пост-состояния и `finalize()`, TZ-08 ретеншн/SLO/span'ы, страница control-plane (TZ-10), TZ-07 (coverage, windows-job), SEC-02/04/06.
- **LATER** — удалённый транспорт флота production-grade, живая аттестация железа, Autonomous Operations (отдельная миссия), TZ-06/03/10.

## Быстрый старт

### Ядро (bossman-core)

```bash
cd bossman-core
python -m venv .venv && . .venv/bin/activate   # Windows: .venv\Scripts\Activate.ps1
pip install -e ".[dev]"
cp .env.example .env      # адреса 127.0.0.1; на боевой машине SANDBOX_MODE и
                          # BOSSMAN_UNSAFE_LOCAL_EXEC оставьте пустыми
bossman serve             # http://127.0.0.1:8700
```

CLI:

```bash
bossman task "…" [--agent coder]
bossman project plan <slug> <brief.md>
bossman project run <slug>
```

Точка входа шлюза: `bossman-gateway`. В проде ядро разворачивается поверх
`bossman-infra` через `compose.core.yaml` (изолированная сеть `bossman-internal`).

### Панель управления (command-center)

```bash
cd command-center
pip install -e .
bcc                       # http://127.0.0.1:8800 (токен печатается в консоли)
```

### Тесты

```bash
cd bossman-core   && python -m pytest -q --timeout=120
cd command-center && python -m pytest -q --timeout=120
python tools/ci_secret_scan.py
```

Тесты, требующие живого PostgreSQL/внешних сервисов, без окружения честно
помечаются `SKIP` с явной причиной (`SKIP_HOST`, `SKIP_EXTERNAL_CREDENTIAL`) —
fake-green в репозитории запрещён.

## CI и качество

Реальные workflow в `.github/workflows/`:

| Workflow | Что делает |
|---|---|
| `bossman-core-ci.yml` | тесты ядра на Python 3.11 и 3.12 по группам (security, gateway-context, stage8-14, rest) + compileall |
| `command-center-ci.yml` | тесты панели (pytest + Chromium для UI-проверок), py3.11/3.12 |
| `root-ci.yml` | тесты корня (learning layer, общие контракты, tools), secret scan, compileall, whitespace-гигиена |
| `bossman-benchmark.yml` | на PR — детерминированные tier'ы smoke+pr без платных вызовов; вручную — с аттестациями владельца/бюджета для LIVE |
| `bossman-v2-repair.yml` | авто-ремонт с живым Postgres (pgvector) и честным отчётом (REPAIR ATTEMPTED ≠ VERIFIED) |

## Безопасность и флаги

Все опасные возможности автономии **выключены по умолчанию** и включаются
только явной переменной окружения (`1`/`true`/`yes`).

Флаги Universal Computer Apprentice (`bossman/apprentice/flags.py`):

| Флаг | Что включает |
|---|---|
| `BOSSMAN_UNIVERSAL_COMPUTER_APPRENTICE` | мастер-флаг аппрентиса |
| `BOSSMAN_SKILL_RECORDING` | запись эпизодов в навыки |
| `BOSSMAN_SKILL_SHADOW_REPLAY` | shadow-повтор навыков |
| `BOSSMAN_SKILL_PROMOTION` | повышение навыков в прод |
| `BOSSMAN_CLAUDE_CODE_FALLBACK` | Claude Code как внешний учитель |
| `BOSSMAN_EXTERNAL_OUTREACH` | внешний outreach (с границей WAIT_APPROVAL) |
| `BOSSMAN_APPRENTICE_DRY_RUN_PREVIEW` | предпросмотр действий без исполнения |
| `BOSSMAN_APPRENTICE_CHECKPOINT_RESUME` | resume после рестарта |
| `BOSSMAN_APPRENTICE_ANCHOR_REDUNDANCY` | избыточность семантических якорей |
| `BOSSMAN_APPRENTICE_LESSON_PRECHECK` | предпроверка уроков |
| `BOSSMAN_APPRENTICE_EVIDENCE_EXPORT` | экспорт доказательств |

Другие гейты: `AI_COMPANY_MODE_ENABLED` (AI Company Mode),
`BOSSMAN_SANDBOX_ENABLED` (песочница), `BOSSMAN_CYBERSEC_V1_ENABLED`
(CyberSec-слой), `BOSSMAN_CYBER_LAB_ENABLED` + `BOSSMAN_CYBER_LAB_ACK`
(тренировочная лаборатория, нужен ещё и факт одноразовой песочницы),
`BOSSMAN_AUTONOMY_TRAINER_SHADOW` / `BOSSMAN_COGNITIVE_REUSE_EXPERIMENT`
(обучающий слой), `BOSSMAN_V3_ENABLED` + пофичевые `BOSSMAN_V3_*` (V3 7-Pack,
adapter-only). LIVE-бенчмарк дополнительно требует `--allow-live`,
`BOSSMAN_BENCHMARK_OWNER_APPROVED=1` и `BOSSMAN_BENCHMARK_BUDGET_RESERVED=1`.

Дополнительно: секреты только по ссылке/маске (Vault с шифрованием at-rest),
маскирование секретов в записях и логах, deny-by-default в разрешениях,
одноразовые nonce-подтверждения, hash-привязка приёмочных тестов.

### Провайдеры через окружение (без ключей в репозитории)

- `BOSSMAN_OPENROUTER_API_KEY` — при старте Command Center один раз создаёт провайдера
  OpenRouter (`https://openrouter.ai/api/v1`) с ключом, зашифрованным в vault; в коде,
  логах и ответах API ключа нет. Без переменной ничего не создаётся.
- `BOSSMAN_EVIDENCE_KEY_FILE` — файл ключа подписи улик (по умолчанию
  `~/.bossman/keys/evidence.key`, 0600, создаётся сам). Улика `verified=True` без валидной
  подписи не принимается.
- Organization Layer как продукт: `BOSSMAN_V3_ENABLED=1` + `BOSSMAN_V3_ORGANIZATION=1` →
  `/api/org/*`; снимок владельца — `GET /api/control-plane`.

## Внутренний бенчмарк

Бенчмарк — воспроизводимая оценочная среда: фикстуры запускаются в дочерних
процессах, отчёты привязаны к фактическому SHA коммита, датасет версионируется
с фиксированным сидом и помечен как непригодный для обучения. Классы
доказательств разделены: `MOCK` / `SIMULATED` / `REAL_SANDBOX` / `LIVE`;
MOCK-фикстура никогда не засчитывается как LIVE, а класс без примеров честно
отчитывается `INSUFFICIENT_EVIDENCE`. Release-гейт фиксирован: NO-GO при любом
P0-провале, небезопасном действии или дубликате эффекта.

Команды (из `bossman-core/`, после `pip install -e ".[dev]"`):

```bash
python -m bossman.benchmark run --tier smoke      # tiers: smoke | pr | nightly | release
python -m bossman.benchmark run --tier pr
python -m bossman.benchmark run-isolated --sha <SHA> --tier pr   # прогон кода коммита в отдельном worktree
python -m bossman.benchmark compare --base <SHA> --candidate <SHA>
python -m bossman.benchmark compare-isolated --base <SHA> --candidate <SHA>
python -m bossman.benchmark report --latest
```

Каждый прогон пишет SHA-привязанный JSON-отчёт, Markdown-отчёт и append-only
запись в `history.jsonl`.

## Статус проекта

- Проект в активной разработке на ветке `claude/bossman-control-v03-43igbk`.
- CI зелёный: Core CI (py3.11/3.12), Command Center CI, root-ci, benchmark CI
  и auto-repair CI проходят на актуальном HEAD.
- Финальные проходы закрытия (PASS 1–3: честный бенчмарк, герметичный
  workspace учителя, durable LIVE + owner auth) закоммичены и зелёные в CI.
- Живые приёмочные сценарии (реальный E2E: живой учитель-провайдер, реальный
  GUI/outreach) частично заблокированы окружением (нет баланса провайдера,
  изоляционных рантаймов и части кредов) и честно помечены
  `BLOCKED_BY_ENVIRONMENT`, а не выданы за пройденные.
- Лицензия в репозитории не указана; права принадлежат владельцу проекта.

> **СДЕСЬ БЫЛ АСТРА**
>
> Ниже — моё видение развития Bossman. Это предложения для V4 и V5, а не
> заявление о том, что перечисленные возможности уже реализованы или проверены.
>
> Общий [протокол решения задач](docs/MODEL_REASONING_PLAYBOOK.md) подключён к
> сообщениям Core/BCC; [10 предложений по развитию](docs/TOP_10_IMPROVEMENTS.md)
> содержат приоритеты и проверяемые критерии приёмки.
>
> **V4 — надёжное исполнение с доказательствами.** Я бы направил основной ресурс
> на объединение уже созданных механизмов: один контракт миссии, один глобальный
> бюджет, постоянные идентичности исполнителя и независимого проверяющего,
> общий журнал эффектов для Core, Command Center и Fleet. Любое «готово» должно
> означать проверенное изменение конкретного объекта. После падения Bossman
> сначала выясняет, что произошло, затем продолжает работу с прежним ID.
> Неопределённый платёж, отправка или публикация остаются на разборе до получения
> подтверждения. Приоритеты V4: защищённое хранилище полномочий, транзакционное
> завершение, единая приватность на выходе к провайдеру и удобное восстановление
> оператором. Критерий выпуска — воспроизводимые миссии с реальными файлами,
> репозиториями и контролируемыми сервисами, включая аварии и потерю аренды.
>
> **V5 — длительная автономная работа в границах владельца.** На проверенной V4
> можно строить команды агентов, которые ведут проекты днями: сохраняют контекст,
> передают задачи между узлами, сравнивают варианты действий и учитывают измеренную
> стоимость ошибок. Обучение я бы разрешал через испытания кандидатов на неизменных
> контрольных наборах, ограниченный пробный запуск и откат при ухудшении.
> Репутация может уменьшать автономию; расширять полномочия должен владелец.
> В панели нужны понятные ответы: что изменилось, чем это доказано, сколько
> потрачено, где остаётся неопределённость и какое решение требуется от человека.
> Успех V5 я бы измерял долей подтверждённых результатов за неделю, стоимостью
> результата, числом повторных эффектов и временем восстановления после сбоя.
>
> Мой главный приоритет: сначала сделать обещания системы проверяемыми,
> затем увеличивать длительность и самостоятельность её работы.
