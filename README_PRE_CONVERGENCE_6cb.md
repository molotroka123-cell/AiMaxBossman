# AiMaxBossman — V8

**Локальная AI-машина для Windows:** единый Command Center для локальных/облачных моделей, агентов, файлов, браузера, сайтов, видео, памяти, приложений и удалённой связи через Telegram.

> **Состояние на 18 сентября 2026:** рабочая ветка `claude/bossman-final-convergence-hu2702`, текущий HEAD до этого README — `67c8e757972b568df8efecb8722d9dfb4f6c89df`.  
> V8 уже имеет многократно зелёную Windows-базу и установленную приёмку **46/46** на проверенном Windows-кандидате. Текущий HEAD содержит более новые оптимизации и Telegram Companion, поэтому **не называем его frozen**, пока новый exact-SHA Windows workflow и владельческий прогон не завершены.

[Установка](INSTALL.md) · [Текущее состояние](docs/final/CURRENT_STATE.md) · [Known-good SHA](docs/final/KNOWN_GOOD_SHAS.json) · [V8 capability matrix](docs/v8/CAPABILITY_MATRIX.md) · [Owner GUI run](docs/v8/OWNER_GUI_START_HERE_RU.md)

## Что это

Bossman задуман не как чат с моделью, а как локальная операционная среда для AI-работы:

- **Command Center** — задачи, агенты, модели, бюджеты, approvals, история и наблюдаемость.
- **Local-first** — локальные модели являются нормальным режимом работы; облако подключается явно.
- **Computer / Browser / Files** — действия идут через ограниченные инструменты и проверяемые эффекты, а не через заявления модели «готово».
- **Web Designer** — визуальный редактор на GrapesJS с сохранением проектов, undo/restart-проверками.
- **Video Studio** — FFmpeg/ffprobe, таймлайн, импорт/экспорт и проверка выходных байтов.
- **Studio V8** — единый каталог image/video-провайдеров; непроверенный provider не маскируется под live-ready.
- **Memory / Context** — долговременный контекст и изоляция проектов.
- **Apps / Trading Lab / File Intelligence** — приложения живут внутри общей политики Bossman.
- **Telegram Companion V8** — отдельный local-first помощник: разговор, поиск, наблюдение за доступностью Bossman и делегирование задач другим агентам. Сам Companion не получает прямой shell/desktop.
- **Recovery / rollback** — сохранение данных, поколение схемы и исполняемая репетиция отката.

## V8 — что уже доказано

| Область | Состояние | Что именно подтверждено |
|---|---|---|
| Windows application | **VERIFIED BASELINE** | На проверенных кандидатах цепочка bundle → installed owner-experience → freeze-gate проходила зелёной |
| Installed acceptance | **46/46** | 16 модулей, 0 failure/error/skip на проверенном Windows-кандидате |
| UI sweep | **PASS** | 31 страница, 139 действий; без мёртвых/ошибочных/молча выключенных контролов на проверенном кандидате |
| Restart/history | **PASS** | История восстанавливалась примерно за 1.4 с при бюджете 5 с на проверенном кандидате |
| Web Designer | **INTEGRATED** | Реальный GrapesJS, сохранение/undo/restart и исправления первого клика |
| Video Studio | **INTEGRATED** | Реальный FFmpeg-путь и проверки декодирования/результата там, где FFmpeg доступен |
| Studio V8 | **INTEGRATED, LIVE PENDING** | Каталог/контракты/редакторы есть; реальный облачный image/video provider не объявляется verified без живого прогона |
| Windows hardware detection | **INTEGRATED** | CIM → legacy WMIC → psutil; unknown не превращается в fake PASS |
| Update / rollback data | **PASS** | Поколение БД + шестишаговая репетиция; замена двух архивов на ПК владельца ещё owner-required |
| Telegram Companion | **CODE + OFFLINE CONTRACTS** | 79 offline-контрактов; local-first, отдельные owner/guest данные, task preview/confirm, Claude fallback с лимитами |
| Target Ryzen AI Max+ 395 | **OWNER REQUIRED** | Физические latency/RAM/shared-memory/model measurements нельзя заменить GitHub runner'ом |
| V8 Total freeze | **NOT YET** | Текущий HEAD новее последнего полностью принятого Windows-архива; exact-SHA workflow сейчас должен подтвердить новые байты |

### Последний записанный проверенный Windows baseline

В `KNOWN_GOOD_SHAS.json` и финальных отчётах хранится не «последний коммит», а **точно проверенные байты**. Один из последних полностью зелёных Windows-кандидатов — `235a07164f8d…`: Windows chain green, release state `WINDOWS_RC_READY_OWNER_REQUIRED`, без repository blockers; live model/Studio и целевое железо оставлены владельцу. Не перемаркировывайте старый ZIP новым SHA.

## Что ускорено в V8

Мы оптимизируем только измеренные узкие места, без отключения проверок.

Последняя целевая оптимизация вынесла блокирующее чтение hardware telemetry из основного asyncio-loop. В контролируемом тесте при искусственной задержке системного чтения 250 мс медианная задержка независимого callback снизилась примерно **с 240.7 мс до 0.35 мс**. Само чтение не стало магически быстрее — перестала ждать остальная программа. Это **не** обещание такого же множителя для всего UI или inference на Ryzen.

Подробности: [PERF_METRICS_ASYNC_RU](docs/v8/PERF_METRICS_ASYNC_RU.md).

## Telegram Companion V8

Telegram теперь рассматривается как отдельный канал связи с Bossman, а не как «ещё одна кнопка уведомлений».

Он умеет:

- разговаривать через локальную OpenAI-compatible модель;
- хранить короткую отдельную память для каждого разрешённого пользователя;
- выполнять явный `/search` через настроенный локальный SearXNG;
- `/task` → показать поручение → `/confirm` → передать его назначенному агенту Bossman;
- `/result` — показывать только собственные задачи пользователя;
- `/status` и `/watch` — оставаться каналом диагностики, даже если разговорная LLM недоступна;
- использовать Claude/OpenRouter как **явно разрешённый** резерв с тарифной проверкой и лимитами.

Companion **не получает прямого управления компьютером**. Он делегирует реальную работу обычным агентам Bossman, поэтому approvals, permissions и бюджеты остаются в силе.

Живой Telegram/Claude/локальная модель на машине владельца ещё должны пройти owner acceptance. Подробности: [TELEGRAM_COMPANION_RU](docs/v8/TELEGRAM_COMPANION_RU.md).

## Запуск

### Рекомендуемый путь — готовый Windows archive

Для презентации/работы используйте только архив, SHA-256 которого записан в `docs/final/KNOWN_GOOD_SHAS.json` и связан с соответствующим acceptance run. Не скачивайте случайный ZIP из старой ветки.

### Разработка из репозитория

Windows:

```powershell
.\start-bossman.ps1
```

Linux/macOS:

```bash
bash start-bossman.sh
```

Панель по умолчанию: `http://127.0.0.1:8800`.

Повторный запуск без переустановки:

```powershell
.\start-bossman.ps1 -SkipInstall
```

```bash
bash start-bossman.sh --skip-install
```

## Open-source foundation

Мы не тащим проект целиком только потому, что он выглядит интересно. В runtime остаются только проверенные компоненты и идеи, которые дают измеримую пользу.

| Возможность | Основа | Роль |
|---|---|---|
| Browser | Playwright + Chromium | Управляемый браузер |
| MCP | Official MCP Python SDK | Коннекторы/инструменты |
| Web Designer | GrapesJS | Визуальное редактирование |
| Video | FFmpeg / ffprobe | Монтаж, экспорт, верификация |
| Timeline interchange | OpenTimelineIO | OTIO import/export |
| Documents | pypdf / document pipeline | Работа с документами |
| Windows UI | pywinauto / pywin32 / PyAutoGUI | Действия в реальном desktop-сеансе |
| Coding agent | OpenHands adapter | Отдельный кодовый исполнитель |
| Telegram | Telegram Bot API + Bossman policies | Local-first companion и approvals/notifications |

OpenContext, дополнительные оркестраторы, поисковые модули и другие OSS-кандидаты считаются **кандидатами**, пока конкретная интеграция не имеет кода, лицензии, тестов и измеренной пользы. README не выдаёт наличие ссылки/форка за работающую функцию.

## Live OS Scorecard

Автоматически поддерживаемый снимок из `docs/benchmark/current-scorecard.json`.
Новые оценки без новых измерений не выставляются: README здесь — проекция, а не
источник. Перерисовывается `python scripts/update_readme_scorecard.py`, а
`--check` в разделе «Проверка» падает, если снимок разошёлся с источником.

<!-- BOSSMAN_LIVE_SCORECARD_START -->
| # | Ось системы | Оценка | Статус | Уверенность | Улики |
|---|---|---:|---|---|---|
| 1 | Execution Truth | 8.8/10 | VERIFIED | HIGH | AT-01 effect obligations and fresh post-state verification are closed with regression coverage; Fencing, anti-replay and recovery invariants remain in force; V6 performance work did not weaken effect-boundary semantics |
| 2 | Security | 8.5/10 | VERIFIED | HIGH | Secret scan and blocking SAST/SCA completed successfully on the V6 code candidate; Approvals, authorization, privacy routing, freshness and fail-closed behavior were explicitly preserved through V6 |
| 3 | Tooling / OS Integration | 7.5/10 | INTEGRATED | MEDIUM | Windows-path CI is green on the current V6 code candidate; Owner-session reconnect, app restart recovery, provider UX and Trading Lab crash paths have repository fixes |
| 4 | Organization Layer | 7.3/10 | INTEGRATED | MEDIUM | Mission/task orchestration remains durable and blocked-only missions now terminate honestly instead of appearing to run forever; Organization/Fleet execution contracts and verified-child completion rules remain covered |
| 5 | Fleet & Resources | 7.0/10 | INTEGRATED | MEDIUM | Lease/fence/queue safety contracts remain covered and V6 does not bypass the canonical execution path; Interactive work is protected from background FFmpeg contention by lowered child-process priority |
| 6 | Memory / Context | 6.5/10 | IMPLEMENTED | MEDIUM | Durable task/recovery memory and scoped context contracts remain intact; Long-session testing now explicitly watches stale context, zombie runs, duplicate subscribers and memory growth |
| 7 | Testing / CI | 8.0/10 | VERIFIED | MEDIUM | Bossman Core full coverage/rest/security/gateway/stage8-14 jobs are green on 413a97a1; ASTRA acceptance, Solana safety, Windows paths and secret/SAST gates are green; Python 3.14 is a hard Command Center lane |
| 8 | Observability / CEO Control | 7.0/10 | PARTIAL | MEDIUM | V6 adds Services.start phase tracing, UI_READY/first-page timing and Computer Use phase_timing; Testing-period evidence from owner session 6cbb17ce84db is retained with corrected dead-click classification |
| 9 | Treasury / Cost | 6.8/10 | IMPLEMENTED | MEDIUM | Budget/cost gates remain unchanged and fail-closed during V6 performance work; Unknown pricing is not treated as free and Fable hard-cap accounting remains isolated from tests |
| 10 | Mission UX / Command Center | 6.8/10 | IMPLEMENTED | MEDIUM | V6 lazy pages reduced first-render modules from 42/788 KiB to 14/290 KiB in the measured harness; Owner-session reconnect, blocked mission, app restart and provider/trading error paths received targeted fixes |

- **Current bottleneck:** V6 phase 0/1 is repository-complete pending external validation: Core/ASTRA/Solana/Windows/security evidence is green on the current code candidate, but an uninterrupted full Command Center exact-SHA matrix plus owner Windows/local-model/Video/Web Designer/long-session acceptance is still missing.
- **Next highest-value fix:** Finish one uninterrupted exact-SHA CI certification, then run the second owner Dashboard acceptance on Windows with the configured model/provider and close only reproduced Video Studio, Web Designer and long-session findings.
- **Last evidence SHA:** `413a97a1ce2936f9543fea5d8f0b529256fc1de2` · **Current HEAD SHA:** `5a2c7e9d6b93` · **Evidence freshness:** PARTIALLY_STALE
- **Last scorecard update:** 2026-09-07
- **Benchmark hard failures:** none observed
- **Live hardware attestation:** PENDING
- **Exact-SHA CI:** UNPROVEN

_Среднее (вторично, не авторитетно): 7.4/10. 10.0 = ATTESTED; ни одна ось не ATTESTED без живой аттестации железа._
<!-- BOSSMAN_LIVE_SCORECARD_END -->

## Проверка

Быстрые репозиторные проверки:

```bash
python tools/ci_secret_scan.py
python tools/skips_registry.py --check
python scripts/update_readme_scorecard.py --check
```

Проверка локального bundle:

```bash
python tools/build_local_bundle.py --out /path/to/new-empty-directory
```

Telegram offline contracts:

```bash
python -m pytest command-center/tests/telegram_contracts/test_companion.py
python -m pytest bossman-core/tests/test_telegram_transport_response_truth.py
```

GitHub Actions и Linux **не заменяют** физическую приёмку на Ryzen AI Max+ 395 / Radeon 8060S / 128 GB unified memory.

## Правила V8 Total freeze

V8 Total можно назвать закрытым только когда **одни и те же финальные байты** прошли:

1. exact-SHA CI без обязательных скрытых skips;
2. clean Windows bundle и installed acceptance;
3. реальный GUI owner-run через поставленное приложение;
4. local model → agent → tool → проверяемый результат → restart;
5. Studio image/video → реальные байты → редакторы → restart;
6. Telegram live round-trip, если он входит в демонстрационный профиль;
7. target-hardware latency/RAM/shared-memory/reclaim;
8. update + rollback с сохранением данных;
9. итоговый ZIP получает имя, размер и SHA-256 **после** теста и больше не перестраивается.

До этого статус честно остаётся `WINDOWS_RC_READY_OWNER_REQUIRED` или более ранним состоянием, а не «FROZEN потому что хочется».

## Структура

- `command-center/` — UI, задачи, интеграции и V8 features.
- `bossman-core/` — execution truth, providers, permissions, recovery.
- `apps/` — встроенные приложения.
- `bossman_shared/`, `learning/`, `schemas/` — общие контракты и learning layer.
- `tools/`, `scripts/` — сборка, диагностика и acceptance.
- `docs/final/` — актуальные инженерные доказательства.
- `docs/v8/` — V8 audits, owner-run и capability contracts.
- `docs/archive/` — история, не текущая истина.

---

### Для владельца

Цель V8 — не максимальное число функций в README. Цель — чтобы на реальном компьютере Bossman **быстро открывался, понимал задачу, выполнял её через правильного агента, показывал реальный результат, переживал перезапуск и не заставлял владельца разбираться в инженерных деталях**.

Поэтому текущий README специально разделяет **реально проверенное**, **реализованное, но ожидающее live-проверки**, и **ещё не закрытое**.
