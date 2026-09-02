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
