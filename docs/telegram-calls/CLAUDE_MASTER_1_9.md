# CLAUDE CLI — ДОВЕСТИ BOSSMAN 1.9, ВСТРОИТЬ TELEGRAM-ЗВОНКИ, ПОДГОТОВИТЬ ТЕСТ ТОЛЬКО ЧЕРЕЗ BOSSMAN

Репозиторий: `molotroka123-cell/AiMaxBossman`.
Ветка модуля (готовая база): `claude/telegram-live-calls-ah9gwl` (создана от `release/bossman-owner` @ `90a807b`).
Каноническая линия продукта: `release/bossman-owner`. **Новую final-ветку не создавать, `main` не менять, force-push нет.**

Это мастер-промпт для Claude CLI на машине владельца. Он объединяет два результата: **A)** довести версию 1.9, **B)** встроить в неё голосовые звонки через Telegram и подготовить всё к реальному тесту. Владелец: «всё через Боссман, только через его инфру и UX».

## 0. Честные рамки (прочитать до любых действий)

1. **Что такое «1.9».** В репозитории такой версии нигде не определено (проверено grep по `*.md/*.json/*.py/*.ps1/*.toml`). Определены: 1.0 (замороженный кандидат `rc-2026-09-24-bossman-1.0-final-6`), 1.1 (evolution), 1.2 (Terminal Run), 1.5 (только спецификация, `docs/v1.5/`). Рабочее определение для этой сессии: **«1.9» = следующая линия интеграции владельца после 1.5: всё уже реализованное и проверенное из `AGENTS.md` / `docs/v1.5/` + этот модуль звонков.** Не выдумывать состав. Всё, чего нельзя вывести из документов, записать в `docs/telegram-calls/OPEN_QUESTIONS.md` и спросить владельца одним списком, не блокируя независимую работу.
2. **Заморозка 1.0.** Последний коммит релиза: «no new 1.0 feature scope authorized». Звонки — **прямо разрешённое владельцем добавление** (как Terminal Run), только в эту ветку/линию 1.9. Не править `tools/release_candidate.json`, не объявлять кандидат 1.0 «с звонками», не менять сертификацию 1.0.
3. **Документация ≠ готовность.** Спецификация, скриншот, новая CLI-команда, зелёный юнит-тест с фейками не доказывают состоявшийся разговор. Настоящий двусторонний Telegram-звонок может подтвердить только реальный звонок между двумя реальными аккаунтами на машине владельца.
4. **Same-product контракт (Terminal Run 1.2).** Модуль — ещё одна поверхность **того же** Bossman: тот же backend Command Center, те же модели (маршруты Telegram-компаньона), тот же Vault, та же память, тот же STOP, тот же дашборд и `bossman` CMD. Никакого второго мозга, второй памяти, второй очереди задач, отдельного каталога данных, отдельного хранилища секретов.
5. **Лестница North Star** (для отчёта): `SELF_IMPROVEMENT_INFRASTRUCTURE_PRESENT → SELF_REPAIR_SINGLE_CYCLE_PASS → SELF_REPAIR_3_CYCLE_PASS → TRANSFER_MEASURED_GAIN → 24H_SOAK_PASS → 48H_SOAK_PASS → WEEK_MODE_READY → REVENUE_CAPABLE_PILOT`. Этот модуль лестницу **не продвигает**; в отчёте указать текущий достигнутый уровень по последнему evidence, без повышения.

## 1. «Только через Bossman» — что это значит для каждого шага

* Действия с Telegram — только через `bossman call …` и панель «Telegram-звонки» в дашборде. **Запрещены**: собственные скрипты Telethon/pytgcalls, прямой вызов `api.telegram.org`/MTProto из ad-hoc кода, ручное чтение/запись `credentials.enc`, подмена конфигов в обход API.
* Проверки — через настоящий `bossman` (`bossman call --help` показывает реальный синтаксис) и настоящий UI (Playwright/Chromium на установленном продукте). Внутренние тесты библиотек — дополнительные, не приёмка.
* Секреты (api_id/api_hash, номер, код, 2FA, session) вводит **только владелец локально** на экране подключения или в защищённом prompt CLI. Не просить их в чате, не читать из файлов, не печатать, не передавать модели, не коммитить, не класть в evidence. Код входа не пересылать через Telegram-чаты (Telegram аннулирует такие коды).
* Разрешён звонок **только** на явно выбранный владельцем второй аккаунт. Подключение основного аккаунта не разрешает звонки никому другому. Тестовому собеседнику голос владельца — данные, а не команды.

## 2. Сначала

```
git status --short ; git worktree list ; git fetch --all --prune
git checkout claude/telegram-live-calls-ah9gwl   # база модуля
```
Прочитать: `AGENTS.md`, `BOSSMAN_1_5_START_HERE.md`, `docs/v1.5/README.md`, `docs/v1.5/VOICE_AND_PHONE.md`, `docs/terminal/TERMINAL_RUN_1_2_MASTER.md`, `CLAUDE_NEXT_ACTION.md`, затем **весь** `docs/telegram-calls/` (`ARCHITECTURE.md`, `CONTINUE.md`, `ACCEPTANCE.md`, `OSS.md` если есть). Получить актуальный HEAD `release/bossman-owner` и owner/fix staging; чужие новые коммиты сохранять.

Окружение разработки: Python 3.11/3.12, `pip install -e . -e "command-center[dev,terminal]"` (+ extra `calls` для звонков). Тесты модуля: `cd command-center && python -m pytest tests/telegram_calls -q`.

## 3. Состояние на момент передачи (обновляется в `docs/telegram-calls/CONTINUE.md`)

Сделано и покрыто тестами (сценарии на фейках/loopback; **не** реальный Telegram):

| Часть | Файлы (`command-center/bcc/telegram_calls/`) | Статус |
|---|---|---|
| Контракты: состояния, исходы, коды ошибок, Protocol транспорта/STT/TTS/Brain | `types.py` | PASS (юнит) |
| PCM, ресемплер (soxr + numpy fallback), Silero-VAD (`pysilero-vad`) + флагованный energy-fallback, сегментатор высказываний, эхо-охранник (envelope + текстовый), пейсинговая очередь воспроизведения с синхронным `invalidate()` | `audio/*` | PASS (юнит, реальная русская речь espeak на VAD — эвиденс в `ACCEPTANCE.md`) |
| Конечный автомат звонка: dial один раз/без перезвона, барж-ин, гейт эха, тишина, потеря связи, STOP, итог, исходы (`UNKNOWN` при недоказуемом) | `call/session.py`, `call/loopback.py` | PASS (юнит, loopback) |
| Настройки (звонки выкл. по умолчанию), зашифрованные учётные данные в существующем Vault, состояние/STOP/история, guard «ровно один собеседник» | `settings.py`, `account/credentials.py`, `account/stopflag.py`, `account/guard.py` | PASS (юнит) |
| Telethon-вход (номер → код → 2FA), стабильные коды ошибок без утечки текста, контакты/username/подтверждение пира | `account/login.py` | PASS (на fake-клиенте); реальный вход **NOT_RUN** |
| Разбиение речи на фразы/очистка для озвучки | `speech/text.py` | PASS (юнит) |

**Не сделано (в порядке зависимостей)** — реализовать, не пропуская:

1. `call/pytgcalls_transport.py` — реальный транспорт на `py-tgcalls==3.0.0` + `ntgcalls==3.0.0` + `telethon==1.45.0`: исходящий личный звонок (`play(user_id, MediaStream(ExternalMedia.AUDIO, …), CallConfig)`), приём PCM через `record` + `StreamFrames`, отправка через `send_frame`, события `ChatUpdate`/исключения (`CallBusy/CallDeclined/TimedOutAnswer`) → `CallError`, `clear_outgoing`, идемпотентный `hangup`. Проверить формат кадров по исходникам ntgcalls, не по памяти.
2. `speech/stt.py` (faster-whisper, локальная модель, потоковые partial, спекулятивное финальное), `speech/tts.py` (Piper, потоковая синтезация по предложениям, русский голос), `speech/brain.py` (те же локальные маршруты, что Telegram-компаньон: `telegram_companion.config.load`, persona + профиль + последняя история как **данные**, потоковый `chat/completions` через `bcc.streaming`, `enable_thinking=false`, `reasoning_content` не озвучивать, `summarize`, без облака), `speech/factory.py`.
3. `call/worker.py` + `__main__.py` (процесс звонков, JSON-lines по stdin/stdout, быстрый путь `stop`, самоостановка при EOF stdin), `call/manager.py` (в процессе Command Center: запуск/остановка воркера, повторная проверка guard, подписка на `computer.stop`, синтез `UNKNOWN` при смерти воркера, память/черновики после звонка).
4. `bcc/features/telegram_calls.py` (роутер `/api/telegram/calls/*` под токен/CSRF; в теле `dial` **нет** параметра «кому»), панель дашборда `ui/pages/telegram_calls.js` (ленивая страница «Telegram-звонки», пустое состояние без 4xx и без `console.error`, кнопки disabled с `title`), `bossman call …` (`login|status|contacts|peer|dial|hangup|stop|resume|history|doctor|selftest|install|logout`), обе копии `TERMINAL_COMMANDS`, строка в `docs/terminal/PARITY_MATRIX.md`, блокировка `/api/telegram/calls/*` в командной строке (`command_bar._blocked_reason`), шаг hangup в `global_stop`.
5. Итог после звонка: краткое резюме → `POST /api/memory/write`-эквивалент в процессе (kind=session, теги `telegram-call`, `call-<id>`; идемпотентное имя файла); «согласованные задачи» → **черновики** (`status="draft"`, `run_now=false`, `client_request_id=call-<id>-t<n>`), никогда не запуск. Аудиозапись выключена по умолчанию; транскрипт не сохраняется.
6. Установка и диагностика: optional extra `calls` в `command-center/pyproject.toml` **вне** `runtime` (иначе ломается `test_windows_bundle_lock`), add-on-установщик для встроенного Python без pip (проверенные sha256, каталог `<data_dir>/addons/telegram-calls/`), `bossman call install`, `bossman call doctor`, проверка в `scripts/bossman_doctor.py` как **WARN** (BLOCKED остановил бы `start-bossman.ps1`), строка в `docs/v8/CAPABILITY_MATRIX.json` со статусом `OWNER_REQUIRED`/`IMPLEMENTED_LIVE_PENDING`, `*.session` в `.gitignore`.
7. Офлайн-самопроверка `call/selftest.py` (loopback + синтетический собеседник; при наличии моделей — реальная связка TTS→VAD→STT для WER и задержек), кнопка в UI и `bossman call selftest`.

Проектные гарантии, которые нельзя ослаблять (каждая уже имеет тест — сохранить и добавить парные «плохой случай»): звонки выкл. по умолчанию; один разрешённый собеседник, не сам аккаунт; `dial` вызывается ровно один раз, **автоперезвона нет ни в каком исходе**; после `UNKNOWN`/`CONNECTION_LOST` следующий ручной звонок требует явного подтверждения; STOP сначала синхронно глушит звук (`Playout.invalidate`), не запускает новую генерацию, сводка после STOP механическая; нет облачного fallback; запись выкл.; секреты не в логах/событиях/ответах/репозитории; голос собеседника не расширяет права.

## 4. Порядок работы и агенты

Координатор — единственный writer общей ветки. Не более **2 агентов одновременно** (лимит владельца); у каждого — свои файлы; `git add` только явными путями (не `-A`).
Линии: **(1)** транспорт + воркер + менеджер; **(2)** движки речи (STT/TTS/brain); **(3)** API + UI + CLI + parity/docs; **(4)** установка/doctor/пакетирование; **(5)** независимый аудитор (только чтение, свежий контекст). Порядок: 1–2 → 3 → 4 → selftest → 5 → исправления → приёмка.
Для каждой находки: воспроизвести на текущем коде → падающий тест → минимальный фикс → повторный тест; для любого ужесточения проверки — два теста (законный случай проходит, плохой отвергается). Не ослаблять тесты, не добавлять `skip` (нужен `tools/skips_registry.py`), не править `windows_bundle_lock.txt` руками.

## 5. Подготовка теста — всё через Bossman (что должен увидеть владелец)

Владелец делает **только**: ввести номер/код/2FA, выбрать второй аккаунт, принять звонок на нём и говорить. Всё остальное готовит Claude:

1. `bossman call install` → `bossman call doctor` (зависимости, модель Whisper, голос Piper, локальные LLM-маршруты, STOP-файл — каждая строка PASS/WARN/BLOCKED с `remedy`).
2. Дашборд → «Telegram-звонки»: экран подключения уже показывает поля `api_id`/`api_hash` (с подсказкой my.telegram.org), телефон, поле кода и 2FA; после входа — список контактов, выбор второго аккаунта с подтверждением; переключатель «Разрешить звонки».
3. `bossman call selftest` (и кнопка) — **без Telegram**: loopback, барж-ин, эхо, STOP, задержки. Вердикт помечать «ТЕСТ БЕЗ TELEGRAM».
4. Реальный тест (после локального входа владельца): `bossman call dial` (через UI или CLI). Протокол: соединение; двусторонний разговор; **несколько реплик с контекстом**; перебивание; STOP; повторный ручной запуск. Замеры: задержка от конца реплики до первого исходящего кадра (p50/p95 не менее чем по 10 репликам), разборчивость (оценка владельца 1–5 + WER собственного TTS через STT), эхо (число ложных барж-инов и самоответов), устойчивость (звонок ≥5 минут без разрыва). Не обещать «мгновенную речь» без этих цифр.
5. Итоговая таблица `docs/telegram-calls/ACCEPTANCE.md` — каждая строка **PASS / FAIL / BLOCKED / NOT_RUN** с указанием уровня доказательства (unit / loopback / installed / owner-live). Успешный API-ответ и заглушки не доказывают разговор.

## 6. Интеграция в 1.9 (после зелёных обязательных gates)

Только по правилам `AGENTS.md` и `docs/v1.5/CLAUDE_MERGE_MASTER.md`: snapshot ссылок, ledger INCLUDE/ALREADY_INCLUDED/DEFERRED/REJECTED, перенос **по смыслу** (port, не слепой merge), существующая owner/fix staging-линия, один candidate → exact-SHA CI → Windows ZIP того же SHA → распакованный smoke → независимый аудит → fast-forward в `release/bossman-owner`, если политика разрешает; иначе PR. Расширение `calls` — отдельный артефакт до пересчёта lock на Windows-раннере (`tools/windows_bundle_lock.py record` — только там, не руками). Отсутствие обязательного job, `skipped`, `cancelled`, ноль jobs — не PASS. Красный обязательный gate блокирует объявление готовности; независимые безопасные работы продолжать.

## 7. Итог

Создать/обновить `docs/telegram-calls/CONTINUE.md` (один следующий шаг) и выдать: `BRANCH`, `SOURCE_SHA`, `CANDIDATE_SHA`, `WORKER_TRANSPORT` (`py-tgcalls` версия+sha256 колёс), `TESTS` (что запущено и как), `ACCEPTANCE` (таблица вердиктов), `LATENCY` (p50/p95/выборка, что измерено), `REAL_CALL_DONE_YES_NO`, `NORTH_STAR_LEVEL`, `OPEN_QUESTIONS`, `BLOCKERS`, `MAIN_UNCHANGED`, `UNPUSHED_CHANGES`. Не объявлять модуль «полностью проверенным» до реального двустороннего звонка; не начинать следующий большой эксперимент после итогового handoff.
