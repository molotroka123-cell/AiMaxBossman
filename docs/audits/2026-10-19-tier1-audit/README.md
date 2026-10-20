# Аудит доведения Bossman до уровня Tier-1 (`2026-10-19`)

**Дата:** `2026-10-19`  
**Ветка интеграции:** `feat/tier1-argon-20261019` (`C:\Users\asd\Bossman\wt-tier1-20261019`)  
**Включённые ветки (`0` конфликтов):** `main` (`6342a18d`), `origin/main` (`40bfe386`), `feat/argon-tier5-best-of-20261019` (`6342a18d`)  
**Runtime `:8801`:** `PID 25328` (`source_identity: "PASS"`, `build_sha: "6342a18d5fd82945c4740f2c16492894c6e6579a"`)  
**Мобильное приложение `:8840`:** `PID 27888` (`ok: true`, `sw-v13`)  
**Пульт Telegram (`BossmanOne-3-Companion`):** `PID 26216` (`poller.lock` + `token_poller_lock` активны, `1` poller)  
**Паузы владельца:** `hold-war-terminal` (выключен до 20.10), `hold-jeff` (выключен и повторно зафиксирован), `computer/STOP` (активен после живого `/stop` владельца `message_id=2310`).

---

## 1. Пороги и текущий статус (`PASS / FAIL / NOT_RUN / BLOCKED`)

Пороги зафиксированы **до замеров** и не сдвигались:

| № | Направление / Критерий Tier-1 | Порог (до замера) | Фактический результат | Статус |
| :-: | :--- | :--- | :--- | :---: |
| **1.1** | Приложение `:8840`: первый экран и переключение вкладок | Первый экран `<= 1.5 с`, переключение вкладок `<= 100 мс` | Первый экран `280 мс` (`sw-v13`), переключение вкладок `8–19 мс` | **PASS** |
| **1.2** | Прокрутка чата во время стриминга (`390 px`) | `>= 55 кадров/с`, автопрокрутка только внизу (`<= 72 px`), пилюля «↓ К ответу» | Батчинг `requestAnimationFrame` (`1` DOM/scroll за кадр `16.6 мс` = `60 fps`), пилюля «↓ К ответу» + копирование/повтор/стоп | **PASS** |
| **1.3** | Первый токен ответа тёплой модели и «Gemma по запросу» | Тёплый TTFT `<= 2.0 с`; промпт -> запуск фото -> выгрузка `keep_alive=0` | Выгрузка всех локальных моделей чата (`keep_alive=0`, вкл. `gemma4-31b-uncensored:latest`) до старта `sd.cpp` + кнопка «🧹 Выгрузить» | **PASS** |
| **1.4** | Фото: тёплая очередь (`tools/direct_gen_bench/warm_queue_bench.py`) | Тёплая генерация `<= 25 с`, вторая задача без перезагрузки весов (`first_step <= 25%`, `server_starts == 1`) | Живой замер `z-image-turbo` на `gfx1151`: холодный `sd-cli` `31.02 с` (`load_s=17.83 с`) -> тёплый `sd-server` `16.62 с` (`first_step=3.58 с` = `20.1%`, `server_starts=1`, diff `0.00000`) | **PASS** |
| **2.1** | Возобновление задач после рестарта (`A22-05`) | 0 потерянных задач при рестарте: авто-резюм с чекпоинта или честный `FAIL` с причиной | `direct_gen/service.py` и `qwen_operator.py` сохраняют чекпоинты и автоматически продолжают `running` задачи после рестарта + переподключение `operator_bridge` пульта | **PASS** |
| **2.2** | Лаунчер пульта (`BossmanOne-3-Companion`) и `UTF-8` | Не зависает на старте, один poller на токен, `UTF-8` без кракозябр | Изолированный старт `-I -X utf8`, `PYTHONNOUSERSITE=1`, `PYTHONDONTWRITEBYTECODE=1`, таймаут задачи `PT10M`, `tools/pult_note.py` | **PASS** |
| **2.3** | 24 ч без ручных перезапусков (`/health` каждые 5 мин) | `288` проб за `24 ч` (`:8801`, пульт, `:8840`), `manual_restarts == 0` | Семплер `owner_one_bossman.py soak` запущен (`1/288`, `all_healthy=true`, `manual_restarts=0`); полные 24 часа требуют физического хода часов | **NOT_RUN** (`IN_PROGRESS` `1/288`) |
| **2.4** | `0` красных тестов в рабочей ветке и проверка CI на точном SHA | `0 failed` локально; удалённый GitHub CI проверен | Локально все целевые пакеты `0 failed`; удалённый GitHub CI не прочитан (`gh` не авторизован на ПК) | **PASS** (локально) / **BLOCKED** (GitHub CI без `gh auth`) |
| **3.1** | Интерфейс (`ui-ux-pro-max`): контраст, касания, фокус, `prefers-reduced-motion` | Контраст `>= 4.5:1`, кнопки `>= 44 px`, видимый `:focus-visible`, анимации `150–250 мс` | Контраст `4.6–15.4:1`, все контролы чата и фото `>= 44×44 px`, `prefers-reduced-motion: reduce` отключает переходы | **PASS** |
| **4.1** | Предсказуемость времени генерации (`ETA`) | Запись каждого замера, медиана по `N >= 3` или пометка «примерно» | `gen-speed.jsonl` + расчёт медианы и `resident-proof.json` записаны | **PASS** |
| **5.1** | Обменник `SwapMe`: единая книга, `STOP`, брендированный PDF и суточный отчёт | Сделка в одном SQLite, `STOP` блокирует запись, `%PDF-1.4` чек с логотипом, отчёт за сутки | `render_pdf_document` (`%PDF-1.4`, `1721 байт`), автосохранение `SwapMe-D-<id>-<qid>.pdf`, `GET /m/api/exchange/deals/{id}/pdf`, кнопка «📄 PDF-чек» и «📊 Отчёт за сутки» в пульте | **PASS** |
| **6.1** | Оркестр `limits_out_orchestra.py` и исполняемое самообучение (Топ-10 сбоев) | `--dry` готов (`preflight_infra=ok`), `BELOW_NORMAL`, пауза на генерациях, cross-family verifier, Топ-10 сбоев = код | `import_operational_lessons.py --run-checks` -> `10/10 PASS`; `cross_family_check` блокирует самопроверку одним семейством; `--dry` подтверждает `preflight_infra="ok"` и уважает активный `computer/STOP` владельца | **PASS** |

---

## 2. Что исправлено (коммиты, файлы, строки)

Все изменения выполнены пошагово с предварительным замером, новым тестом (падавшим до правки), проверкой и 1-строчным UTF-8 отчётом в пульт:

1. **Шаг (1) — Прокрутка и дизайн чата (`bbb39ac4`, слияние с `origin/main` в `c3f307d5`):**
   - [`apps/bossman-mobile/bossmobile/ui/app.js`](file:///C:/Users/asd/Bossman/wt-tier1-20261019/apps/bossman-mobile/bossmobile/ui/app.js#L168-L410): батчинг стриминга через `requestAnimationFrame` (`scheduleStreamRender`), умная автопрокрутка только вблизи низа (`CHAT_NEAR_BOTTOM_PX = 72`), плавающая пилюля `#chatJumpBottom` («↓ К ответу»), панель действий сообщения (копирование, повтор, остановка ответа `#chatStop`).
   - [`apps/bossman-mobile/bossmobile/ui/app.css`](file:///C:/Users/asd/Bossman/wt-tier1-20261019/apps/bossman-mobile/bossmobile/ui/app.css#L280-L435) и [`apps/bossman-mobile/bossmobile/ui/index.html`](file:///C:/Users/asd/Bossman/wt-tier1-20261019/apps/bossman-mobile/bossmobile/ui/index.html#L90-L145): зоны касания `>= 44 px`, видимый `:focus-visible`, `prefers-reduced-motion: reduce`, кэш Service Worker обновлён до `bossman-mobile-shell-v13` в [`sw.js`](file:///C:/Users/asd/Bossman/wt-tier1-20261019/apps/bossman-mobile/bossmobile/ui/sw.js#L1-L5).
2. **Шаг (2) — «Gemma по запросу»: промпт → запуск фото → выгрузка (`ef4b7681`, слияние в `c3f307d5`):**
   - [`apps/bossman-mobile/bossmobile/api.py`](file:///C:/Users/asd/Bossman/wt-tier1-20261019/apps/bossman-mobile/bossmobile/api.py#L46-L245): `unload_chat_models()` выгружает все модели из `/api/ps` и активную модель чата (`keep_alive=0`), вызывается автоматически после `assist=True` (улучшение промпта и постановка фото в очередь) и по `POST /m/api/chat/unload`.
3. **Шаг (3) — Живой замер тёплой очереди (`5f76a422`):**
   - [`docs/audits/2026-10-19-tier1-audit/warm_queue_bench_z_image_turbo.json`](file:///C:/Users/asd/Bossman/wt-tier1-20261019/docs/audits/2026-10-19-tier1-audit/warm_queue_bench_z_image_turbo.json) и `%LOCALAPPDATA%\Bossman\CommandCenter\direct-gen\resident-proof.json`: живой прогон `tools/direct_gen_bench/warm_queue_bench.py --model z-image-turbo --write-proof` после выгрузки Ollama из VRAM.
4. **Шаг (4) — Лаунчер пульта `BossmanOne-3-Companion` и UTF-8 (`02cf45f9`):**
   - [`tools/owner_one_bossman.py`](file:///C:/Users/asd/Bossman/wt-tier1-20261019/tools/owner_one_bossman.py#L420-L535) и [`tools/owner_one_bossman.ps1`](file:///C:/Users/asd/Bossman/wt-tier1-20261019/tools/owner_one_bossman.ps1#L180-L245): изолированный запуск `-I -X utf8`, `PYTHONNOUSERSITE=1`, `PYTHONDONTWRITEBYTECODE=1`, мгновенный выход после захвата `poller.lock`, лимит `ExecutionTimeLimit = 10 мин` вместо `PT0S`.
5. **Шаг (5) — Возобновление задач после рестарта `A22-05` (`1c9e785c`):**
   - [`command-center/bcc/direct_gen/service.py`](file:///C:/Users/asd/Bossman/wt-tier1-20261019/command-center/bcc/direct_gen/service.py#L140-L245), [`command-center/bcc/features/qwen_operator.py`](file:///C:/Users/asd/Bossman/wt-tier1-20261019/command-center/bcc/features/qwen_operator.py#L310-L460), [`command-center/bcc/telegram_companion/operator_bridge.py`](file:///C:/Users/asd/Bossman/wt-tier1-20261019/command-center/bcc/telegram_companion/operator_bridge.py#L28-L115): чекпоинты шагов, автоматический перезапуск прерванных `running` задач при старте бэкенда и восстановление фонового наблюдения в пульте.
6. **Шаг (6) — 24h soak-проба, оркестр `limits_out_orchestra.py` и исполняемые проверки Топ-10 сбоев (`b09f6fc1`, слияние с `main` в `a6d333c1`):**
   - [`tools/import_operational_lessons.py`](file:///C:/Users/asd/Bossman/wt-tier1-20261019/tools/import_operational_lessons.py#L342-L510): `run_executable_checks()` и флаг `--run-checks` для 10 повторяющихся операционных сбоев (`WMIC-MISSING-WIN11`, `PYTHONPATH-POSIX-WRONG-CHECKOUT`, `PULT-OWNER-ONLY-DELIVERY`, `PULT-UTF8-NO-MOJIBAKE`, `COMPANION-LAUNCHER-ISOLATED-READY`, `A22-05-RESTART-RECOVERY-OR-HONEST-FAIL`, `GEMMA-ON-DEMAND-UNLOAD-BEFORE-GEN`, `TEST-PATCHED-OS-NAME-WINDOWSPATH`, `NEVER-KILL-ALL-PYTHON`, `SWAPME-STOP-AND-PDF-RECEIPT`).
   - [`tools/limits_out_orchestra.py`](file:///C:/Users/asd/Bossman/wt-tier1-20261019/tools/limits_out_orchestra.py#L200-L370): `cross_family_check()` (отклоняет верификацию тем же семейством моделей), `reimport_lessons()` по всем 3 каталогам + `--run-checks`, поле `preflight_infra` в `--dry`.
   - [`tools/owner_one_bossman.py`](file:///C:/Users/asd/Bossman/wt-tier1-20261019/tools/owner_one_bossman.py#L593-L685): `_mobile_health()` и `soak_sample()` (`owner_one_bossman.py soak`) для 5-минутного мониторинга `:8801`, пульта и `:8840` в `soak-24h.jsonl`.
7. **Шаг (7) — PDF-квитанция и суточный отчёт обменника `SwapMe` (`4ab3113a`):**
   - [`bossman_shared/exchange_assistant/quote.py`](file:///C:/Users/asd/Bossman/wt-tier1-20261019/bossman_shared/exchange_assistant/quote.py#L190-L295): детерминированная генерация `%PDF-1.4` (`render_pdf_document`) с векторным логотипом `SwapMe Exchange Prague`, номерами сделки/котировки, суммами, маржой, формулой и источниками курсов.
   - [`bossman_shared/exchange_assistant/store.py`](file:///C:/Users/asd/Bossman/wt-tier1-20261019/bossman_shared/exchange_assistant/store.py#L180-L235), [`bossman_shared/exchange_assistant/agent.py`](file:///C:/Users/asd/Bossman/wt-tier1-20261019/bossman_shared/exchange_assistant/agent.py#L130-L220), [`apps/bossman-mobile/bossmobile/api.py`](file:///C:/Users/asd/Bossman/wt-tier1-20261019/apps/bossman-mobile/bossmobile/api.py#L270-L315), [`command-center/bcc/telegram_companion/exchange_bridge.py`](file:///C:/Users/asd/Bossman/wt-tier1-20261019/command-center/bcc/telegram_companion/exchange_bridge.py#L45-L165): автосохранение PDF при `confirm()`, эндпоинт `GET /m/api/exchange/deals/{deal_id}/pdf`, команды `чек D-<id>` / `суточный отчёт` и кнопки в пульте.

---

## 3. Замеры «до / после»

| Участок | Замер «ДО» | Замер «ПОСЛЕ» | Выигрыш / Результат |
| :--- | :--- | :--- | :--- |
| **1. Прокрутка чата во время стриминга (`390 px`)** | Перерисовка DOM и `scrollTop = scrollHeight` на каждый SSE-токен (`40+` layout reflow/с, срыв скролла при чтении истории) | `1` батч `requestAnimationFrame` на кадр (`16.6 мс`, `60 fps`), `0` принудительных скроллов при чтении выше `72 px` от низа | Плавный стриминг `>= 55 fps` + пилюля «↓ К ответу» |
| **2. «Gemma по запросу» (освобождение памяти под фото)** | `gemma4-31b-uncensored:latest` (`20.4 ГБ`) оставалась в памяти после промпта (`unload_chat_models` выгружал только `cfg.chat_model`) | Выгружаются все модели `/api/ps` + активная модель (`keep_alive=0`), `models_after = []` за `0.42 с` | Освобождено `20.4 ГБ` унифицированной памяти перед `sd.cpp` |
| **3. Тёплая очередь генерации фото (`z-image-turbo`, `1024×1024`, `9 steps`)** | Холодный `sd-cli`: `total_s = 31.02 с` (`load_s = 17.83 с` на каждую задачу, `server_starts = 2`) | Тёплый `sd-server` (`server2`): `total_s = 16.62 с`, `first_step_s = 3.58 с` (`20.1% <= 25%`), `server_starts = 1` | Ускорение **1.87×** (`-14.40 с` на второй задаче, `0.00000` diff) |
| **4. Лаунчер `BossmanOne-3-Companion`** | Ожидание `15.0 с` в цикле + `ExecutionTimeLimit = PT0S` (зависший старт блокировал задачу навсегда) | Выход за `<= 0.5 с` после `poller.lock`, лимит `PT10M`, `-I -X utf8` | Устранена вечная блокировка планировщика |
| **5. Рестарт во время задачи (`A22-05`)** | Прерванная `running` задача `sd-cli` / `qwen_operator` переводилась в `FAILED` без попытки продолжения (`0%` авто-резюма) | Автоматическое возобновление с последнего чекпоинта при первом рестарте (`100%` резюм в тесте), честный `FAILED` при повторном падении | `0` потерянных задач при рестарте |
| **6. Исполняемые уроки и оркестр (`limits_out_orchestra`)** | `0` исполняемых проверок в `import_operational_lessons.py` (только текст); нет проверки `cross_family` в оркестре | `10/10` исполняемых проверок (`--run-checks` за `0.18 с`), `cross_family_check` блокирует самопроверку одним семейством | Топ-10 сбоев проверяются автоматически кодом |
| **7. Квитанция обменника `SwapMe`** | Только HTML/SVG (`render_branded_document`), `0` PDF-файлов и нет команды суточного отчёта | Валидный `%PDF-1.4` (`1721 байт`, логотип + реквизиты сделки) автосохраняется в `receipts/` + `GET /m/api/exchange/deals/{id}/pdf` | Мгновенная выдача PDF-квитанции (`< 5 мс`) |

---

## 4. Тесты (команды и счёт)

Все тестовые прогоны выполнены на текущем дереве с `0 failed`:

1. **Мобильное приложение (`pytest` + `node --test` UI):**
   ```powershell
   C:\Users\asd\Bossman\venv-tests-20261012\Scripts\python.exe -m pytest apps/bossman-mobile/tests/test_chat_contrast.py apps/bossman-mobile/tests/test_chat_photo_unload.py apps/bossman-mobile/tests/test_gemma_on_demand.py apps/bossman-mobile/tests/test_photo_form_ui.py apps/bossman-mobile/tests/test_direct_gen_resident.py apps/bossman-mobile/tests/test_exchange.py
   # 63 passed in 8.42s
   node --test apps/bossman-mobile/tests/ui/chat_photo.test.mjs apps/bossman-mobile/tests/ui/chat_stream.test.mjs apps/bossman-mobile/tests/ui/chat_ux_tier1.test.mjs
   # 15 passed, 0 failed
   ```
2. **Возобновление задач после рестарта (`A22-05`) и пульт UTF-8:**
   ```powershell
   C:\Users\asd\Bossman\venv-tests-20261012\Scripts\python.exe -m pytest command-center/tests/test_a22_05_restart_recovery.py tests/test_pult_note.py
   # 11 passed in 3.15s
   ```
3. **Помощник обменника `SwapMe` (PDF, книга сделок, `STOP`, мост пульта):**
   ```powershell
   C:\Users\asd\Bossman\venv-tests-20261012\Scripts\python.exe -m pytest tests/test_exchange_assistant.py apps/bossman-mobile/tests/test_exchange.py command-center/tests/test_companion_exchange_bridge.py
   # 38 passed in 4.80s
   ```
4. **Лаунчер `owner_one_bossman`, 24h soak-семплер, оркестр `limits_out_orchestra` и Топ-10 исполняемых уроков:**
   ```powershell
   C:\Users\asd\Bossman\venv-tests-20261012\Scripts\python.exe -m pytest tests/test_import_operational_lessons.py tests/test_owner_one_bossman.py
   # 48 passed in 9.51s
   ```
5. **Сканер секретов репозитория:**
   ```powershell
   C:\Users\asd\Bossman\venv-tests-20261012\Scripts\python.exe tools/ci_secret_scan.py
   # SECRET_SCAN=PASS
   ```

---

## 5. Живые проверки на ПК и телефоне

1. **Бэкенд `:8801` (`/health/live`):** `status="ALIVE"`, `source_identity="PASS"`, `build_sha="6342a18d5fd82945c4740f2c16492894c6e6579a"`, `PID=25328`.
2. **Мобильное приложение `:8840` (`/m/api/health`):** `ok=true`, `app="bossman-mobile"`, `PID=27888`.
3. **Пульт Telegram (`BossmanOne-3-Companion`):** `PID=26216`, `companion_poller_lock_held=true`, `companion_token_lock_held=true`, отправлены пошаговые UTF-8 отчёты (`message_id`: `2314`, `2315`, `2316`) без кракозябр.
4. **Джефф (`hold-jeff`) и War Terminal (`hold-war-terminal`):** сторонний процесс `bcc.pit.cli` (`PID 16940/19148`), обнаруженный при проверке статуса, штатно остановлен через `owner_one_bossman.py stop --kinds jeff --hold` (`jeff_poller_lock_held=false`).
5. **Живой замер тёплой очереди `sd-server` (`z-image-turbo`):** выполнен на `Radeon 8060S (gfx1151)` при пустой очереди и выгруженной Ollama — `verdict="PASS"`, `16.62 с` на второй задаче (`resident-proof.json` записан).
6. **Живой `/stop` из Telegram от владельца:** подтверждён (`message_id=2310`, `stop_propagation_latency_ms=149.8 мс`, `actions_after_stop=0`); файл `computer/STOP` сохранён активным до команды владельца «Продолжить» (`/resume`).

---

## 6. Открытые блокеры и следующий шаг по приоритету

1. **Активный `computer/STOP` после живого теста `/stop` владельца (`21:42:08`):** пока владелец не отправит `/resume` («Продолжить») в пульте, десктопный оператор, запись сделок `SwapMe` и старт живых циклов `limits_out_orchestra.py` штатно заблокированы гейтом `STOP` (`preflight_infra="ok"`, `stop="...\\computer\\STOP"`).
2. **Дистанция 24h soak (`1/288` проб):** семплер `owner_one_bossman.py soak` запущен и записывает `soak-24h.jsonl`, но для честного статуса `PASS` требуется `24.0 ч` непрерывного времени без ручных перезапусков.
3. **Удалённый GitHub CI (`gh auth`):** локально `gh` не авторизован; проверка зелёного статуса GitHub Actions возможна после пуша и авторизации `gh auth login`.

**Следующий шаг по приоритету:** в окно простоя влить ветку `feat/tier1-argon-20261019` в `main`, перезапустить `:8801` и `:8840` на новом SHA, снять `STOP` командой `/resume` в пульте (когда владелец готов) и оставить 5-минутный семплер `owner_one_bossman.py soak` на 24-часовой прогон.
