# CONTINUE — Telegram-звонки: всё готово до шага входа владельца

> **СТАТУС 2026-09-30 (lane `telegram-calls`).** Главный документ — `CLAUDE_MASTER_1_9.md`. Таблица проверок — `ACCEPTANCE.md`.
> **Реальный двусторонний звонок не совершался: NOT_RUN / OWNER_REQUIRED.** Всё ниже проверено БЕЗ Telegram
> (unit, loopback, offline end-to-end, настоящий Chromium) и так и помечено.

## Где что лежит

* Код: `command-center/bcc/telegram_calls/**` (воркер, менеджер, аудио, Vault-учётные данные, guard, add-on), API `command-center/bcc/features/telegram_calls.py`,
  CLI `command-center/bcc/terminal_cli/calls.py`, панель `command-center/ui/pages/telegram_calls.js`, мозг — Jeff: `bcc/pit/call_surface.py`.
* **Один** префикс API — `/api/telegram/calls/*`; **одни** события шины — `telegram_call.state` / `telegram_call.ended`. Алиасы `/api/calls/*` и `calls.*` удалены.
* Тесты: `cd command-center && python -m pytest tests/telegram_calls -q` (из `command-center/`) и
  `node --experimental-detect-module --test command-center/ui/tests/telegram_calls.test.mjs` (на Node ≥ 22 флаг не нужен).

## Следующее действие — ТОЛЬКО владелец

Остановка ровно на входе. Экран подключения готов: Dashboard → «Ещё» → **«Telegram-звонки»** (`#/telegram_calls`) → раздел «Подключение», шаг 1.
Бэкенд для настоящего звонка запускается **без** `BOSSMAN_CALLS_MODE` (переменная `offline_test` даёт красную плашку «ТЕСТ БЕЗ TELEGRAM» и фейковый Telegram).
Данные — каталог данных того Bossman, в котором настроен Jeff (локальная модель) и стоит голос Piper + модель Whisper.

1. **Диагностика** (кнопка в «Проверки» или `bossman call doctor`): каждая строка PASS/WARN/BLOCKED с «Что делать». Если «Зависимости» не PASS — «Установить зависимости»
   (`bossman call install`; скачивает закреплённый список пакетов по HTTPS, sha256 проверяется до записи). WARN по «Голос (Piper)» / «Распознавание речи (Whisper)» /
   «Локальная модель Jeff» означает, что разговор голосом не начнётся, пока это не поставлено.
2. **api_id и api_hash** (делает владелец, в своём браузере): https://my.telegram.org → войти по номеру ОСНОВНОГО аккаунта → «API development tools» → создать приложение
   (любое название, платформа Desktop) → скопировать `api_id` (число) и `api_hash` (32 символа).
3. **Подключение → шаг 1:** ввести `api_id`, `api_hash` → «Сохранить ключи» (уходят в зашифрованное хранилище Bossman, назад не показываются).
   **Шаг 2:** номер основного аккаунта в международном формате → «Получить код». **Шаг 3:** код, который придёт в приложение Telegram (чат «Telegram») → «Войти».
   **Шаг 4 (если включена двухэтапная защита):** облачный пароль → «Подтвердить».
   То же без мыши: `bossman call setup` (скрытый ввод; секреты аргументами командной строки отвергаются).
   * Код входа **не пересылать** ни в какие чаты и боты: Telegram аннулирует показанный в чате код. Номер, код и пароль в переписку не отправлять.
   * Это **новый вход Telethon**: в Telegram → Настройки → Устройства появится новая сессия. Сессию открытого Telegram Desktop/Web (tdata, cookies, localStorage) продукт **не использует и не копирует**.
   * Если «Telegram (Web), в который я вчера зашёл» означал именно эту веб-сессию — она для звонков не нужна и не берётся; вход выше заменяет её.
4. **Тестовый собеседник:** «Найти» (или юзернейм) → отметить «Это мой второй аккаунт» → «Выбрать». Кого звать, выбирает только владелец; в коде и конфигах id не зашит.
   Подключение основного аккаунта не даёт права звонить кому-либо ещё. Сам себя выбрать нельзя.
5. **Звонок:** переключатель «Разрешить звонки» → «Позвонить». На втором аккаунте принять звонок и говорить. Проверить: несколько реплик с контекстом,
   перебивание (barge-in), «STOP», затем повторный ручной «Позвонить» (автоперезвона нет ни в каком исходе). Дать ≥ 5 минут разговора и оценку разборчивости 1–5.
6. **После звонка:** в «Последние звонки» — краткий итог; «Сохранить итог в память» и «Создать черновики задач» — только по клику владельца (аудио не пишется, расшифровка не хранится).

Что Claude делает после входа: `bossman call status`, замер задержки «конец реплики → первый звук» (p50/p95 не менее чем по 10 репликам, отдельно STT/LLM/TTS),
эхо (ложные barge-in / самоответы), устойчивость. **Цифр задержки до этого замера нет и обещать «мгновенную речь» нельзя.**

## Что осталось открытым (честно)

* NOT_RUN: настоящий вход, настоящий звонок, замеры на живой линии, реальный инференс Whisper/Piper/локальной модели внутри звонка, установка add-on в установленный продукт владельца.
* Патчи лида (чужие файлы; готовые тексты — `swarm-20260930/telegram-calls/*.patch`, тесты уже в наборе и пропускаются до применения, `skipped` не PASS):
  S6 `S6_bossman_doctor.patch` (строки ASR/TTS/модель/ACL в `scripts/bossman_doctor.py`), S7 `S7_control_plane.patch` (плоскость `calls` в «Остановить всё»; STOP при этом уже работает: событие `computer.stop` завершает звонок, e2e),
  **`J2_call_surface_deny_by_default.patch` (`bcc/pit/j2/pipeline.py`, `bcc/pit/runtime.py`) — до его применения слой Jeff 2.0 пишет сказанное на звонке на диск (`proactive`, `director`, `persona`, `learning_log`) и `research` ходит в веб: обещание «текст разговора на диск не пишется» на звонке НЕ выполняется.**
* Решение владельца: действует ли настроение Jeff («грубиян», позиция) на ИСХОДЯЩЕМ звонке (сейчас да, оверлей общий для всех собеседников); слои безопасности при этом включены.
* Риск, не проверенный на живой линии: STOP в момент дозвона — корректного `discardCall` нет (мьютекс py-tgcalls), воркер убивается через ~3 с, исход UNKNOWN; второй аккаунт может звонить до таймаута сервера.
* Из аудита исправлено в моих файлах и проверено тестами: гонка STOP/набор в воркере, single flight набора, защёлка STOP при сбое записи файла, фиксированное раскрытие «ИИ-ассистент», отозванный участник, озвученная разметка, pre-TTS аудит без текста, fail-closed egress (`ACCEPTANCE.md`, раздел D).
* Не сделано из списка мастера: CI-secret-scan строка для звонков, `test_readme_commands_are_real`, строка в `docs/v8/CAPABILITY_MATRIX.json`, реестр skips; S8 (стриминг фраз) — только если замер задержки не пройдёт цель.
* Окно Jeff (`jeff.html`) не содержит кнопки звонка: звонки — панель «Telegram-звонки» и `bossman call`.

North Star: звонки лестницу не продвигают; уровень остаётся тем, что говорит последнее release-evidence. Тот же Bossman: никакого второго мозга, памяти, очереди или хранилища секретов.

## Answering machine (incoming calls): emulator-verified, not live

Branch `claude/telegram-answering-machine`. Jeff answers an incoming Telegram call for the owner (honest greeting, finds out what the caller
needs, a log delivered to the owner's Telegram console). Design: `ARCHITECTURE.md` (section "Incoming calls"); evidence: `ACCEPTANCE.md` section E (AM-1…AM-12).

**Owner steps to make it live (none of this has been done; all of it is local to the owner's machine):**
1. ACCEPTANCE rows 21/22 first: `bossman call install`, then `bossman call setup` (api_id / api_hash, phone, code, 2FA are typed ONLY by the owner), and `bossman call doctor`
   must show the Jeff voice tract (Whisper / Piper paths) and a local model; the answering machine uses the same engines.
2. Keep the Telegram companion (owner console) running with the owner console ON, otherwise the reports wait in the outbox
   (`bossman call answer reports --pending`).
3. Optional: `bossman call answer config --ring-delay 12 --max-call 180 [--allow ID] [--deny ID] [--allow-unknown yes|no] [--greeting "..."]`.
4. `bossman call answer on` (or the toggle in the panel). **After every Bossman restart arm it again** (`answer on`): the worker starts only on an owner action.
5. Test with the second account: let it ring the main account and do not answer: Jeff must answer after the ring delay, say it is an assistant, take a message, and
   the notice must arrive in the owner's Telegram. Then ring again and pick up on your phone inside the ring delay: Jeff must not join.
   Write down what happens on the owner's other devices when Jeff answers or when STOP declines (ACCEPTANCE row AM-10): the engine does not report why a ringing call went away.
6. STOP (`bossman call stop`, the panel, the global STOP) stops answering until `bossman call resume`; the setting stays on.

Known limits: a phone number a caller dictates is masked in the log (the Telegram id / name is what you call back); reports are kept to the newest 200; the engine
cannot tell "caller gave up" from "answered elsewhere", so both produce a notice unless the owner's pick-up is reported explicitly.
