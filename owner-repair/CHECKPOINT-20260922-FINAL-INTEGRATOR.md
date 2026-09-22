# CHECKPOINT — финальный интегратор, 2026-09-22 (машина владельца)

Сессия свёрнута по команде владельца. Ничего не сломано, всё закоммичено и запушено.
Этот файл — точка передачи следующему чату/агенту.

## Линия и ветки (все запушены)

| Ветка | SHA | Что в ней |
|---|---|---|
| `integrate/owner-final-20260922` | `362177db` | **рабочая линия интеграции**, сюда слито всё ниже |
| `feat/telegram-local-llm-20260922` | `fc266856` | база: Telegram + видео-пресеты + RC-кандидат 6 |
| `feat/telegram-owner-console-20260922` | `bd598fa6` | единый путь Telegram через approvals (слито) |
| `fix/media-lifecycle-20260922` | `54eb7473` | сирота/STOP/дедлайн/частичные сегменты (слито) |
| `docs/media-candidates-20260922` | `5b839e04` | матрица моделей-кандидатов (слито) |
| `feat/coaching-exam-20260922` | `e4675935` | экзамен для локального Qwen (НЕ слито: живой прогон не завершён) |
| `feat/memory-lifecycle-20260922` | инженер памяти дописывал в момент сворачивания | память/recall (НЕ слито) |
| `claude/bossman-1-0-rc-owner-ready-cfesui` | `737a31b1` | сертифицированный кандидат 6 (`fa7ceae1` CERTIFIED) |
| `release/bossman-owner` | `f8dbaee3` | каноническая конечная ветка, не трогалась |

Порядок предков: `release/bossman-owner` ⊂ RC ⊂ `feat/video-duration-presets` ⊂ `feat/telegram-local-llm` ⊂
`integrate/owner-final-20260922`. Merge старых веток не требуется, force-push не делался.

## Закрытые дефекты (воспроизведение → регрессия → фикс → соседние тесты)

MEDIA-LIFECYCLE (движок умирает вместе с ядром, Windows Job Object), CU-UNKNOWN-RESTART (неизвестный исход
переживает рестарт), DEADLINE-CEILING (абсолютные потолки 12 ч/сегмент и 24 ч/задание + тот же потолок в
watchdog), MEDIA-PARTIAL (стоп сохраняет готовые сегменты, помеченные как неполные), FFMPEG-PATHEXT (имя
бинарника из файловой системы, не из реестра), SETUP-RESET (отказ 403 без дочитывания тела рвал соединение).
Подробности и классификация — `owner-repair/repair-ledger.md`, запись от 2026-09-22.

## Прогон тестов на линии

`telegram_contracts` + `test_telegram_settings` + `test_studio_*` + `test_owner_stop_lifecycle`:
**447 passed, 0 failed** при `app/BOSSMAN-Windows-x64-0c1cbe651f52/media` в PATH.
Без ffmpeg в PATH 7 тестов Studio падают из-за отсутствия skip-guard — ENVIRONMENT/HARNESS, не дефект продукта.

## Что проверено вживую на этой машине

* FLUX.2-klein-4B: 1024×1024, 4 шага, 78 с; кадр соответствует запросу. Файлы сверены с Hugging Face LFS.
* Сравнение четырёх моделей фото на одной сцене и seed — `owner-repair/evidence/image-models-comparison-20260922.md`.
* Видео TestRun 1 с (кораблик на воде): ffprobe, полное декодирование, кадры, mock=false, наблюдённый Vulkan —
  `owner-repair/evidence/video-testrun-verification-20260922.md`. PASS только для режима 1 с.

## Что НЕ сделано (честно)

1. **Живой экзамен Qwen — NOT_RUN.** Раннер готов (`wt-exam`, ветка `feat/coaching-exam-20260922`), кейсы
   заморожены, holdout изолирован, mock-проверка 18 passed. Живой прогон был запущен и **остановлен** по
   команде владельца через ~15 минут: успели только засеяться 2 урока (`LSN-MEDIA-EN-ENCODER`,
   `LSN-STUDIO-FIRST-LIST-TIMEOUT`), `report.json` не создан. Никаких чисел о способностях Qwen нет.
   Повтор: см. команду в `wt-exam/owner-repair/coaching-exam-20260922/README.md`; нужны поднятые llama-серверы.
2. **LTX-2.5 — OWNER_REQUIRED_LICENSE.** Нужно согласие владельца на https://huggingface.co/Lightricks/LTX-2.5.
   Ничего не скачано, условия не приняты.
3. **Длинные видео 5/10/15/30 с и I2V — NOT_RUN.** Проверен только TestRun 1 с.
4. ~~Частичный результат в слое заданий Studio~~ — **ЗАКРЫТО** веткой `fix/partial-video-rts5-20260922`, слита в линию: стоп и лимит времени отдают готовые сегменты (только прошедшие верификацию, помеченные неполными, никогда не «completed»), в Telegram приходит часть с честной подписью или сообщение «сохранять нечего». Проверено на MOCK_ENGINE, живой генерацией — NOT_RUN.
   владельца и дорабатывал; в `integrate/owner-final-20260922` на момент чекпоинта есть только уровень провайдера.
5. **Память/recall** (`feat/memory-lifecycle-20260922`) — не слито, отчёт инженера не получен.
6. **Реальная доставка Telegram и живой Computer Use нового пульта — NOT_RUN** (только mock-контракты).
7. **Exact-SHA сертификация и Windows ZIP для `362177db` — NOT_RUN.** Старый сертификат `fa7ceae1` на новую линию
   не распространяется.

## Состояние машины на момент чекпоинта

* llama-серверы 8081/8082/8083 — **остановлены**; sd-cli не работает; ~60 ГБ свободно.
* Bossman backend на :8800 — **работает** из исходников `wt-telegram` (данные в `wt-telegram/command-center/data`,
  не рабочая база владельца). Остановить: убить python-процесс с `wt-telegram` в командной строке.
* Telegram-компаньон — **работает на старом коде** из `wt-telegram` (ещё с прямыми `/sh` и `/claude`).
  Новый пульт (без прямого shell) лежит в линии интеграции, но в живой бот не выкачен.
* Токен бота — `%LOCALAPPDATA%\Bossman\telegram-companion\companion.env` (вне git).
* Lease: `handoff/LEASE.json`, владелец `final-integrator-opus5-asd-ed-20260922`; предыдущий сохранён рядом.
* Осиротевший прогон видео предыдущей сессии (sd-cli pid 1644) остановлен по распоряжению владельца.

## Первые шаги следующего агента

1. `git fetch --all --prune`; работать в НОВОМ worktree от `integrate/owner-final-20260922`.
2. Забрать `feat/memory-lifecycle-20260922`, проверить и слить; затем решить судьбу `feat/coaching-exam-20260922`.
3. Выкатить новый Telegram-пульт в живого бота (перезапуск компаньона из линии интеграции) и проверить вживую.
4. Живой экзамен Qwen с числами; затем exact-SHA сертификация и ZIP для нового кандидата.
