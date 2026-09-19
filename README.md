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
