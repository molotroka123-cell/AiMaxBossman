# CLAUDE CLI — ЗАКРЫТЬ BOSSMAN 1.9 + TELEGRAM-ЗВОНКИ И НАБРАТЬ ВТОРОЙ АККАУНТ (только через Bossman)

Репозиторий: `molotroka123-cell/AiMaxBossman` · ветка модуля: **`claude/telegram-live-calls-ah9gwl`** (готовая база, уже включает линию 1.9)
Линия 1.9: `origin/feat/bossman-1.9-freeze-20260929` (влита в ветку модуля, tip `9f4fca9a`) · также существует `origin/feat/bossman-1.9-final-freeze`.
Каноническая линия продукта: `release/bossman-owner` — **не менять до проверенной интеграции**. `main` не трогать, force-push нет, новую final-ветку не создавать.

> Документация ≠ готовность. Настоящий двусторонний звонок подтверждает только реальный звонок между двумя реальными аккаунтами на машине владельца. Юнит-тесты, loopback и «успешный ответ API» его не доказывают.

## 0. Цель этого запуска (слова владельца)

«Закрыть 1.9 + звонки и **набрать мне на второй аккаунт**, через Telegram, который я открыл, или данные, которые я введу сам. Всё через Боссман, только через его инфру и UX.»

**Второй аккаунт (выбран владельцем как единственный тестовый собеседник):** `@cc4349`, Telegram user id **`5271096441`**.
* Выбирается **только через UX Bossman** (экран «Telegram-звонки» → «Выбрать собеседника» или `bossman call peer …` — точный синтаксис в `bossman call --help`), с явным подтверждением. **В код, дефолты, тесты и конфиги репозитория id не зашивать.**
* Подключение основного аккаунта **не даёт права звонить никому другому**. Guard «ровно один собеседник, не сам аккаунт» уже реализован и должен остаться.

**Про вход (важно, безопасность):**
* Вход (api_id/api_hash с my.telegram.org, номер, код, 2FA) вводит **только владелец локально** — на экране подключения Bossman или в скрытом prompt `bossman call login`. Не просить секреты в чате, не читать из файлов, не печатать, не коммитить, не класть в evidence.
* «Через Telegram, который я открыл»: **сессию открытого Telegram Desktop (`tdata`) не извлекать и не копировать** — это обход защиты аккаунта. Правильный путь — новый вход Telethon (в Telegram он появится как новое устройство). Код входа **не пересылать через чаты/ботов** (Telegram аннулирует код, показанный в чате).
* «Данные через пульт в телеге»: если у Bossman уже есть владельческий Telegram-канал управления, его можно использовать **только для команд без секретов** (например, «набери» / STOP), с теми же проверками владельца, что уже есть в продукте. Секреты через него не проходят. Не строить для этого новый бот/движок.
* Секреты хранятся в **существующем Fernet Vault Bossman** (`<data_dir>/telegram-calls/credentials.enc`, ключ `secret.key` каталога данных): ACL, отсутствие в логах/diag-bundle/бэкапах и поведение после рестарта уже проверены тестами (`hardening.py`, `test_account_store`, `test_hardening`) — сохранить.

## 1. Жёсткие правила (не ослаблять)

1. **Jeff и Jev — разные компоненты.** Jeff = PIT (`bcc/pit/`, Jeff UX, `jeff_desktop`, голосовой тракт Whisper/Piper). Jev и `telegram_companion` **не превращать в нового Jeff**. Звонок — ещё одна ПОВЕРХНОСТЬ того же Jeff: `bcc/pit/call_surface.py` (`CallParticipantRuntime`, `surface="call"`), тот же public_guard, согласия, zero-start, память по участнику, локальные модели. Второго мозга/памяти/очереди нет.
2. **Не делать параллельный Telegram-движок и не переписывать готовый голосовой тракт.** STT = `bcc.pit.speech.transcribe_wav` (Jeff), TTS = тот же внешний Piper (`bcc.oss.piper.synthesize_pcm`, GPL остаётся отдельным процессом), голос — `jeff_desktop.default_voice_env`. VoIP — единственный новый компонент: `py-tgcalls 3.0.0` + `ntgcalls 3.0.0` (LGPL-3.0) + `telethon 1.45.0` (MIT), в отдельном процессе `python -I -m bcc.telegram_calls`.
3. Тот же backend/данные/Vault/память/STOP/дашборд/`bossman` CMD (Terminal Run 1.2 = ещё одна поверхность того же продукта). Никакого отдельного каталога данных или хранилища секретов.
4. Звонки выключены по умолчанию; **`dial` ровно один раз, автоперезвона нет ни в каком исходе** (включая неизвестный результат); STOP глушит звук синхронно, не запускает новую генерацию и завершает звонок; запись аудио выкл.; облачного fallback нет; транскрипт не хранится; голос собеседника — данные, а не команды.
5. Заморозка 1.0: не править `tools/release_candidate.json`, не менять `windows_bundle_lock.txt` руками (extra `calls` — вне `runtime`, add-on `bcc/telegram_calls/addon.py`). Звонки — явно разрешённое владельцем добавление, не повод переписывать архитектуру.
6. North Star для отчёта (звонки лестницу **не продвигают**, указать уровень по последнему evidence, без повышения): `SELF_IMPROVEMENT_INFRASTRUCTURE_PRESENT → SELF_REPAIR_SINGLE_CYCLE_PASS → SELF_REPAIR_3_CYCLE_PASS → TRANSFER_MEASURED_GAIN → 24H_SOAK_PASS → 48H_SOAK_PASS → WEEK_MODE_READY → REVENUE_CAPABLE_PILOT`.

## 2. Сначала

```
git status --short ; git worktree list
git fetch origin '+refs/heads/*:refs/remotes/origin/*' --prune
git checkout claude/telegram-live-calls-ah9gwl
```
Прочитать: `AGENTS.md`, `BOSSMAN_1_5_START_HERE.md`, `docs/v1.5/README.md`, `docs/terminal/TERMINAL_RUN_1_2_MASTER.md`, `CLAUDE_NEXT_ACTION.md`, затем весь `docs/telegram-calls/`.
Окружение: Python 3.12, `pip install -e bossman-core -e "command-center[dev,terminal,calls]"`. Тесты модуля: `cd command-center && python -m pytest tests/telegram_calls -q` (запускать **из `command-center/`**, не из каталога пакета).

**Устаревшее в `ARCHITECTURE.md` / `ACCEPTANCE.md` / `CONTINUE.md` (поправить первым делом, этот файл главнее):** там ещё «база release/bossman-owner», «1.9 не определена», «brain = telegram_companion», «Bossman-приветствие». Верно: база — линия 1.9, мозг — Jeff (PIT call surface), приветствие — Jeff, хранилище — каталог данных Bossman, API — `/api/telegram/calls/*` (алиас `/api/calls/*` **удалён 2026-09-30**, события шины — только `telegram_call.state` / `telegram_call.ended`). Эти три файла приведены в соответствие 2026-09-30.

## 3. Состояние на момент передачи (что реально есть; уровни доказательства)

| Часть | Где | Статус |
|---|---|---|
| Контракты, коды ошибок, аудио (PCM/ресемплер/Silero-VAD/endpointer/эхо-охранник/пейсинговый playout), конечный автомат звонка, loopback | `bcc/telegram_calls/{types,audio/*,call/session,call/loopback}.py` | PASS unit/loopback (`tests/telegram_calls`, 26 тестов сессии) |
| Транспорт py-tgcalls (10 мс/960 Б кадры, ChatUpdate, hangup идемпотентен), воркер JSON-lines, offline_test-режим, selftest | `call/{pytgcalls_transport,worker,offline_mode,selftest}.py` | PASS unit; движок реально собирается против never-connected Telethon (`bossman call selftest`) — **в песочнице**; реальный звонок NOT_RUN |
| Vault-учётные данные, вход, guard, STOP-флаг, hardening (ACL, редактор логов, deny в diag-bundle, `*.session` в .gitignore) | `account/*`, `hardening.py` | PASS unit; реальный вход NOT_RUN |
| Add-on установщик (20 пакетов win_amd64-cp312, sha256, безопасная распаковка) | `addon.py`, `addon_lock.json` | PASS unit; установка на Windows NOT_RUN |
| **Jeff как мозг звонка:** швы S1–S5 в `bcc/pit/*` (флаги `allow_web`, дедлайн, разговорная форма, история звонка, блок-лист для surface `call`, `beam_size`), `CallParticipantRuntime`, `JeffSTT/JeffTTS/JeffBrain`, итог звонка (одна consent-gated запись `last_call_summary` в namespace собеседника) | `bcc/pit/call_surface.py`, `speech/jeff_engines.py` | PASS unit: 20 + 35 тестов; регресс Jeff/PIT 278 + 421 PASS (существующее поведение не изменено) |
| Менеджер воркера, API, панель дашборда, `bossman call …`, пост-звонковый итог (сохранение в память/черновики задач — по клику владельца) | `call/manager.py`, `features/telegram_calls.py`, `ui/pages/telegram_calls.js`, `terminal_cli/calls.py`, `postcall.py` | PASS unit: manager/API/CLI/postcall 143 + 92 повторно; UI-тест `ui/tests/telegram_calls.test.mjs` и браузерная приёмка (Playwright) **NOT_RUN**; работа UX-агента остановлена владельцем на середине |

**Известные шероховатости, оставленные при остановке (закрыть):**
* Два экземпляра агента одновременно переписывали `features/telegram_calls.py`: сейчас роутер смонтирован под **обоими** префиксами (`/api/telegram/calls` — контрактный, `/api/calls` — алиас), события шины — под обоими именами (`telegram_call.*` и `calls.*`). Выбрать **один** канонический префикс (рекомендация: `/api/telegram/calls`, как в docs и командной строке), привести UI/CLI/тесты/доки к нему, алиас оставить или убрать осознанно. **СДЕЛАНО 2026-09-30:** канонический `/api/telegram/calls`, алиас и события `calls.*` убраны; UI/CLI/тесты/доки приведены.
* Оставшиеся швы Jeff: **S6** — строки в doctor Jeff (ASR/TTS/ACL, статус WARN, не BLOCKED); **S7** — плоскость `calls` в `features/control_plane.py` и hangup в `global_stop` (STOP должен работать одинаково из дашборда, CLI и Telegram-канала); S8 (стриминг фраз) — только если измеренная задержка первого звука не проходит цель.
* Полный прогон `tests/telegram_calls` одной командой после остановки агентов **не завершён** (последний общий прогон до правок UX: 204 PASS; после — по частям, см. выше). Первым делом прогнать всё целиком.
* Не сделано: независимая adversarial-проверка, docs/OSS/latency-разделы, CI-secret-scan, `test_readme_commands_are_real`, строка в `docs/v8/CAPABILITY_MATRIX.json` (`OWNER_REQUIRED`/`IMPLEMENTED_LIVE_PENDING`), skips-registry.

## 4. План работы (не более 2 агентов одновременно; `git add` только явными путями)

1. Полный прогон тестов + `node --test command-center/ui/tests/telegram_calls.test.mjs`; починить красное (воспроизвести → падающий тест → минимальный фикс).
2. Один префикс API; закрыть швы S6/S7; поправить устаревшие доки.
3. `bossman call install` → `bossman call doctor` → `bossman call selftest` (всё **без Telegram**, помечать «ТЕСТ БЕЗ TELEGRAM»). Браузерная приёмка панели на реальном запущенном Bossman (Playwright/Chromium): пустое состояние без 4xx/`console.error`, кнопки disabled с `title`.
4. Независимый аудитор (только чтение, свежий контекст): секреты в логах/событиях, гонки STOP/hangup, ни одного redial, права guard, границы `bcc/pit/*` (тест периметра `test_cu_participant_perimeter`). Исправить находки.
5. Интеграция в 1.9 — по `AGENTS.md` и `docs/v1.5/CLAUDE_MERGE_MASTER.md` (ledger, port по смыслу, exact-SHA CI, Windows-lock только на Windows-раннере, независимый аудит; красный обязательный gate блокирует объявление готовности; `skipped`/`cancelled` ≠ PASS).

## 4a. Дополнение владельца: ПОЛНЫЙ прогон 1.9 и общение через Telegram

* **Полный прогон 1.9** перед объявлением готовности: весь `pytest` command-center и bossman-core, все `node --test ui/tests`, `scripts/bossman_doctor.py`, Windows-gates и CI на точном SHA. Красное чинить по циклу «воспроизвести → тест → минимальный фикс». Результат — таблицей PASS/FAIL/BLOCKED/NOT_RUN, `skipped`/`cancelled` ≠ PASS.
* **Общение с владельцем через Telegram** (тотально, но только через существующие каналы Bossman/Jeff): статус, вопросы, отчёты по этапам, «набери» и STOP — через уже существующий владельческий Telegram-канал управления, с его проверками владельца. Нового бота/движка не строить. **Секреты (api_hash, номер, код входа, 2FA) через этот канал не передаются** — только локальный ввод владельца. Код входа, отправленный в чат, Telegram аннулирует.
* Ход: Claude сам ведёт всё до шага входа, шлёт владельцу в Telegram короткий статус и остановку «жду локальный вход»; после входа набирает `@cc4349` (`5271096441`) через `bossman call dial`, с одним звонком и без автоперезвона.

## 5. Тест «набрать второй аккаунт» — всё через Bossman

Владелец делает **только**: ввести api_id/api_hash, номер, код, 2FA (локально); подтвердить выбор `@cc4349` / `5271096441`; принять звонок на втором аккаунте и говорить. Остальное готовит Claude:

1. `bossman call doctor` — PASS/WARN/BLOCKED с `remedy` по каждой строке: зависимости, модель Whisper, голос Piper, локальные LLM-маршруты Jeff, STOP-файл.
2. Дашборд → «Telegram-звонки»: подключение → выбор собеседника → «Разрешить звонки» → «Позвонить». Экран подключения должен быть готов до остановки на шаге входа владельца.
3. Реальный протокол: соединение; двусторонний разговор; **несколько реплик с контекстом**; перебивание (barge-in); STOP; повторный ручной запуск. Замеры: задержка «конец моей реплики → первый исходящий звук» (p50/p95 не менее чем по 10 репликам, отдельно STT/LLM/TTS), разборчивость (оценка владельца 1–5), эхо (ложные барж-ины/самоответы), устойчивость (≥5 минут). Не обещать «мгновенную речь» без этих цифр.
4. После звонка: короткое резюме и договорённости (память и черновики задач — только по клику владельца; аудио не пишется).
5. `docs/telegram-calls/ACCEPTANCE.md` — каждая строка **PASS / FAIL / BLOCKED / NOT_RUN** + уровень доказательства (unit / loopback / installed / owner-live).

Остановиться нужно **ровно** на шаге, где требуется ввод владельца, с уже открытым экраном подключения.

## 6. Итоговый отчёт

`BRANCH`, `SOURCE_SHA`, `CANDIDATE_SHA`, `WORKER_TRANSPORT` (версии + sha256 колёс), `TESTS`, `ACCEPTANCE`, `LATENCY`, `REAL_CALL_DONE_YES_NO`, `NORTH_STAR_LEVEL`, `OPEN_QUESTIONS`, `BLOCKERS`, `MAIN_UNCHANGED`, `UNPUSHED_CHANGES`. Не объявлять модуль «полностью проверенным» до реального двустороннего звонка.
