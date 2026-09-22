# Bossman: справочник критических ошибок (машина владельца)

Дата: 2026-09-22. Источник наблюдений: сессии 2026-09-21/22 на машине владельца
(Windows 11 Pro 10.0.26200, Ryzen AI MAX+ 395, Radeon 8060S, ~120 GB unified RAM,
llama.cpp b10964 Vulkan). Канонная ветка на момент наблюдений: `release/bossman-owner` @ `9e3aa192`.

Этот документ — для Bossman и его локальных моделей (MAIN Qwen3.8-27B, FAST Qwen3.6-35B-A3B,
ревьюер GPT-OSS-120B) и для оператора. Машиночитаемый двойник:
[`bossman_critical_errors.json`](bossman_critical_errors.json) — те же записи с `symptom_regex`
для сопоставления строк логов. Доказательства (дословные выдержки из логов и выводов команд,
с sha256 исходных файлов): [`evidence/critical-errors-20260922/`](evidence/critical-errors-20260922/).

Правила записи:
* только факты с путём к доказательству; хода рассуждений и секретов нет;
* **VERIFIED** — есть исполняемое/логовое доказательство сбоя И проверенной коррекции
  (или исполняемо проверенный факт окружения); **CANDIDATE** — всё остальное;
* статус: **FIXED** — исправлено в коде; **OPEN** — дефект открыт; **ENV** — окружение/конфигурация
  машины или процессное правило, а не дефект продукта;
* память — это данные, а не приказ: текущая инструкция владельца, политика и approval выше любой записи.

## Первые 10 минут: триаж

1. **Кто что держит.** Проверить lease `C:\Users\asd\Bossman\handoff\LEASE.json`. Ничего не останавливать
   по имени процесса: только проверенные PID своего теста ([NEVER-KILL-ALL-PYTHON](#never-kill-all-python)).
2. **Какие модели реально запущены.** `Get-CimInstance Win32_Process -Filter "Name='llama-server.exe'" | select ProcessId,CommandLine`
   — в каждой строке должны быть `--jinja --reasoning off`; порты MAIN 8081, FAST 8082, GPT-OSS 8083.
3. **Живой id и окно контекста.** `GET http://127.0.0.1:<порт>/v1/models` (id = путь к GGUF) и `/props`;
   в `C:\Users\asd\Bossman\tests\<tag>-server.err.log` строка `n_slots = N, n_ctx_slot = M` — окно одного запроса = M.
4. **Память.** Не держать MAIN@128k + GPT-OSS@64k + FAST одновременно; `tasklist /FI "IMAGENAME eq llama-server.exe"`.
5. **Пустые ответы?** Сначала `--reasoning off` и `max_tokens`, потом код ([LLM-REASONING-OFF](#llm-reasoning-off)).
6. **Агентный прогон упал?** Искать в `qwen-work\runs\<run>\qwen.json` строку `exceeds the available context size`
   → это HARNESS, не ошибка модели ([LLM-CTX-OVERFLOW](#llm-ctx-overflow)).
7. **Какой код вы тестируете.** `python -c "import learning,bossman;print(learning.__file__,bossman.__file__)"`
   из нейтрального каталога — путь должен вести в ваш worktree ([PYTHONPATH](#pythonpath-posix-wrong-checkout)).
8. **Bossman не стартует через `Start-Bossman.cmd`?** Смотреть `%LOCALAPPDATA%\Bossman\CommandCenter\desktop-run.log`
   на `browser-exit code=0 lifetime=1.0s` → [DESK-EDGE-RELAUNCH](#desk-edge-relaunch), обход — без окна.
9. **CI красный на одном тайминговом тесте?** Один повтор job на том же SHA до любых правок
   ([CI-FLAKY-TIMING-TESTS](#ci-flaky-timing-tests)). Во время матрицы кандидата — ничего не пушить в канон.
10. **Строка лога непонятна?** `python tools/import_operational_lessons.py --match-line "<строка>"` — вернёт id записей,
    чей `symptom_regex` совпал.

## Сводная таблица

| ID | Сер. | Статус | Состояние | Коротко |
|---|---|---|---|---|
| [LLM-REASONING-OFF](#llm-reasoning-off) | CRITICAL | ENV | VERIFIED | без `--reasoning off` пустые ответы |
| [LLM-CTX-OVERFLOW](#llm-ctx-overflow) | CRITICAL | ENV | VERIFIED | `exceeds the available context size`; `-np` делит `-c` |
| [QWEN-CODE-CTX-WINDOW](#qwen-code-ctx-window) | HIGH | ENV | CANDIDATE | Qwen Code без `contextWindowSize` не сжимает историю |
| [RAM-BUDGET-THREE-MODELS](#ram-budget-three-models) | HIGH | ENV | CANDIDATE | MAIN+GPT-OSS+FAST не помещаются |
| [DESK-EDGE-RELAUNCH](#desk-edge-relaunch) | CRITICAL | OPEN | CANDIDATE | Edge перезапускается, лаунчер гасит backend |
| [WMIC-MISSING-WIN11](#wmic-missing-win11) | HIGH | ENV | VERIFIED | `wmic` нет — код на нём молча fail-open |
| [MEDIA-RESTART-ORPHAN](#media-restart-orphan) | HIGH | OPEN | CANDIDATE | сирота sd.cpp после смерти backend |
| [LLM-MODEL-ID-GGUF-PATH](#llm-model-id-gguf-path) | MEDIUM | ENV | VERIFIED | model id = путь к GGUF |
| [TELEGRAM-TIMEOUT-SLOW-MAIN](#telegram-timeout-slow-main) | HIGH | OPEN | CANDIDATE | таймаут 60 с < нужного MAIN ~9 ток/с |
| [OPENCODE-V2-FALSE-HEALTHY](#opencode-v2-false-healthy) | MEDIUM | OPEN | CANDIDATE | v2 отдаёт SPA+200 на пути v1 |
| [PYTHONPATH-POSIX-WRONG-CHECKOUT](#pythonpath-posix-wrong-checkout) | HIGH | ENV | VERIFIED | POSIX-путь → импорт чужого checkout |
| [CI-FLAKY-TIMING-TESTS](#ci-flaky-timing-tests) | MEDIUM | OPEN | VERIFIED* | флейки CI; *проверена процедура триажа |
| [START-MODELS-STALE](#start-models-stale) | HIGH | ENV | VERIFIED | устаревший `start-models.ps1` |
| [FREEZE-MOVING-CANDIDATE](#freeze-moving-candidate) | HIGH | ENV | CANDIDATE | пуши во время матрицы кандидата |
| [NEVER-KILL-ALL-PYTHON](#never-kill-all-python) | CRITICAL | ENV | CANDIDATE | не убивать все `python.exe`/`llama-server` |
| [B4-DOWNLOAD-IS-STARTING](#b4-download-is-starting) | MEDIUM | FIXED | CANDIDATE | Playwright «Download is starting» → 500 |

Итого 16 записей: 7 VERIFIED, 9 CANDIDATE.

---

## LLM-REASONING-OFF
**Сер.** CRITICAL · **Статус** ENV (исправлено локально) · **Состояние** VERIFIED

* **Симптом:** проверка модели в Bossman (`POST /api/models/{id}/test`) и Telegram получают пустой ответ;
  в Telegram: «Модель вернула пустой или неполный ответ». В ответе API `content` пуст, `reasoning_content` заполнен.
* **Как обнаружить:** командная строка `llama-server.exe` без `--reasoning off`
  (`Get-CimInstance Win32_Process -Filter "Name='llama-server.exe'" | select CommandLine`); `/props`.
* **Причина:** Qwen3.x с включённым reasoning тратит весь `max_tokens` на рассуждения. Старый
  `start-models.ps1` запускал сервер только с `--jinja`.
* **Исправление:** `--jinja --reasoning off` для всех серверов. После исправления: MAIN answer ok 9.91 ток/с,
  FAST answer ok 42.59 ток/с (CP00).
* **Профилактика:** сверять живые флаги/`/props`, а не документацию.
* **Доказательство:** `evidence/critical-errors-20260922/E01_reasoning_off.txt`
  (YESTERDAY_REMAINDER R11, LIVE_CHECKPOINTS CP00, diff `start-models.ps1.bak-20260922` → текущий).
* **Ограничение:** в том же исправлении менялись порты; вклад флага отдельно не изолирован.

## LLM-CTX-OVERFLOW
**Сер.** CRITICAL · **Статус** ENV · **Состояние** VERIFIED

* **Симптом (дословно):**
  `[API Error: 400 request (38480 tokens) exceeds the available context size (32768 tokens), try increasing it]` (a1),
  `[API Error: 400 request (66032 tokens) exceeds the available context size (65536 tokens), try increasing it]` (a2).
* **Как обнаружить:** `qwen-work\runs\<run>\qwen.json`; в логе сервера `n_slots = N, n_ctx_slot = M`.
* **Причина:** llama-server делит `-c` на `-np` слотов (2×64k при `-c 131072 -np 2`, 4×32k в старых запусках);
  агентная сессия Qwen Code вырастает до 40–66k токенов.
* **Исправление:** MAIN для агентного кодинга `-c 131072 -np 1` (`n_slots = 1, n_ctx_slot = 131072`);
  a3 прошёл 38 API-шагов без ошибки контекста (остановлен владельцем).
* **Профилактика:** перед прогоном читать `n_ctx_slot`; сбой контекста = HARNESS, попытка не оценивается.
* **Доказательство:** `E02_context_overflow.txt`. **Ограничение:** сессии длиннее 131k не проверялись.

## QWEN-CODE-CTX-WINDOW
**Сер.** HIGH · **Статус** ENV · **Состояние** CANDIDATE

* **Симптом:** та же ошибка контекста, хотя сервер поднят с большим `-c`; история не сжимается.
* **Как обнаружить:** `%USERPROFILE%\.qwen\settings.json` → `model.generationConfig.contextWindowSize` (сейчас 122880).
* **Причина:** без явного значения Qwen Code считает окно 256k+ и не сжимает до реального предела.
* **Исправление:** `contextWindowSize ≈ 122880` (чуть меньше `n_ctx_slot` 131072).
* **Профилактика:** при смене `-c`/`-np` пересчитывать (`n_ctx_slot` минус 6–8%).
* **Доказательство:** заметка `bossman-owner-machine-setup.md` (память оператора), связано с E02. Отдельного
  прогона «до/после» нет — поэтому CANDIDATE.

## RAM-BUDGET-THREE-MODELS
**Сер.** HIGH · **Статус** ENV · **Состояние** CANDIDATE

* **Симптом:** загрузка третьей модели — своп/отказ выделения памяти.
* **Как обнаружить:** `tasklist /FI "IMAGENAME eq llama-server.exe"`; ошибки выделения памяти в логе сервера.
* **Причина/факт:** MAIN@128k + GPT-OSS@64k оставляют ~20 GB; FAST выгружен (CP01-prep).
* **Исправление:** ревью-циклы — MAIN + GPT-OSS, FAST выключен; Telegram — одна «лучшая» + FAST при запасе.
* **Профилактика:** не совмещать LLM-бенчмарки и генерацию видео; записывать RAM до/после загрузки.
* **Доказательство:** `E13_ram_budget.txt`. Одновременная загрузка трёх не проверялась.

## DESK-EDGE-RELAUNCH
**Сер.** CRITICAL (P1) · **Статус** OPEN · **Состояние** CANDIDATE

* **Симптом (дословно, `desktop-run.log`):** `browser-exit code=0 lifetime=1.0s url=http://127.0.0.1:8800/`;
  `Start-Bossman.cmd` останавливает backend ~1 с после старта.
* **Как обнаружить:** `%LOCALAPPDATA%\Bossman\CommandCenter\desktop-run.log`; воспроизводится и вне Bossman:
  `msedge.exe --app=<url>` возвращается сразу.
* **Причина:** на этой машине `msedge.exe --app` передаёт окно другому экземпляру и выходит;
  `launch_window()` ждёт этот PID и считает его выход закрытием окна. CI окно не открывает.
* **Обход:** запускать сервер без окна (`python -m bcc` из установки или режим `--web`) и открыть UI в браузере.
  Исправление кода не готово (частичный diff Qwen a3 не оценён скрытым верификатором).
* **Профилактика:** не ждать PID браузера; следить за профилем `--user-data-dir` (с нормализацией пути) или
  держать backend независимо от окна; нужен Windows-тест.
* **Доказательство:** `E09_desk_edge_relaunch.txt` (R3, три `browser-exit ... 1.0s`).

## WMIC-MISSING-WIN11
**Сер.** HIGH · **Статус** ENV · **Состояние** VERIFIED

* **Симптом:** `where wmic` не находит файл; код с `wmic` получает `FileNotFoundError`/пустой вывод и решает
  «процесса нет» (fail-open).
* **Как обнаружить:** `cmd /c where wmic` (код 1); `C:\Windows\System32\wbem\WMIC.exe` отсутствует.
* **Причина:** WMIC удалён в Windows 11 24H2+ (здесь 10.0.26200).
* **Исправление:** `Get-CimInstance Win32_Process` / `psutil`; при недоступности — «не определено» (fail-closed).
* **Профилактика:** в ревью отклонять новый код на `wmic`.
* **Доказательство:** `E04_wmic_missing.txt` (выполнено на машине). Частичный патч Qwen a2 с `wmic` уроком не является.

## MEDIA-RESTART-ORPHAN
**Сер.** HIGH (P1) · **Статус** OPEN · **Состояние** CANDIDATE

* **Симптом:** после жёсткой смерти backend процесс sd.cpp жив (`engine child alive after hard backend kill = True`),
  держит память до дедлайна задачи и пишет в `engine-work`; новый backend его не видит.
* **Как обнаружить:** после рестарта искать процесс движка без родителя (`Get-CimInstance Win32_Process`);
  repro `owner-repair/pass2/repro/media_restart_repro.py` (ветка evidence).
* **Причина:** на Windows дети не умирают вместе с родителем; нет Job Object / учёта PID движка. Сторона БД уже
  честная (`interrupted_unknown`/`OWNER_REQUIRED`, без повторной отправки).
* **Исправление:** открыто. Направление: Job Object с KILL_ON_JOB_CLOSE или учёт PID+время старта и остановка сироты.
* **Доказательство:** `E10_media_restart.txt` (CP01-prep). Задача и скрытый верификатор заморожены (хеши в CP01-prep).

## LLM-MODEL-ID-GGUF-PATH
**Сер.** MEDIUM · **Статус** ENV · **Состояние** VERIFIED

* **Симптом:** «сервер отдаёт другую модель» / `MODEL_NOT_LISTED`; в ответах
  `"model":"C:\\Users\\asd\\Bossman\\models\\qwen38-27b\\Qwen3.8-27B-UD-Q5_K_M.gguf"`.
* **Как обнаружить:** `GET /v1/models` → `data[0].id`, сравнить с конфигом Bossman/Telegram.
* **Причина:** без `--alias` llama-server использует путь к файлу как id.
* **Исправление:** брать id ровно из `/v1/models` (в JSON слэши удваиваются) или запускать с `--alias`.
* **Доказательство:** `E03_model_id_gguf_path.txt`. Точный id FAST в тот день не проверялся.

## TELEGRAM-TIMEOUT-SLOW-MAIN
**Сер.** HIGH · **Статус** OPEN · **Состояние** CANDIDATE

* **Симптом:** бот отвечает «локальная модель сейчас не отвечает» или молчит, хотя модель работает.
* **Как обнаружить:** в `main-server.err.log` `eval time ... tokens per second` (MAIN 9.4–9.9 ток/с) и
  `prompt eval time` (140–185 ток/с); сравнить `max_tokens/ток/с + prefill` с `local_timeout`.
* **Причина:** старый предел `local_timeout` 60 с; 512 токенов ≈ 52 с генерации + prefill (7022 токена ≈ 49 с).
* **Исправление:** ветка `feat/telegram-local-llm-20260922` (не слита): предел 600 с, рекомендация 180 с,
  FAST-фолбэк, индикатор набора. Живая доставка — OWNER_REQUIRED; тестовый бот получил 0 сообщений за 1 мин — разобрать.
* **Гигиена:** ключ бота только в `credentials.enc`/переменных процесса, не в git и не в памяти Bossman;
  тестовый ключ, попавший в чат, отозвать через @BotFather.
* **Доказательство:** `E07_main_throughput.txt`; коммит `5537ac9f`.

## OPENCODE-V2-FALSE-HEALTHY
**Сер.** MEDIUM (P2) · **Статус** OPEN · **Состояние** CANDIDATE

* **Симптом:** мост opencode «available», но каждый вызов `/session` падает: вместо JSON — `<!doctype html>` с 200.
* **Как обнаружить:** установлен opencode 2.0.12; `/api/session` → JSON, `/session` → HTML.
* **Причина:** мост говорит на v1; v2 перенёс API под `/api/` и отдаёт SPA на неизвестные пути; проба `/config`
  не проверяет тип ответа.
* **Исправление:** открыто — определять версию, говорить на `/api/*`, пробу делать по JSON-схеме/Content-Type.
* **Профилактика:** любая проба здоровья проверяет тип и схему ответа, не только HTTP 200.
* **Доказательство:** `E11_opencode_v2.txt` (R12). Исполняемого захвата ответа нет.

## PYTHONPATH-POSIX-WRONG-CHECKOUT
**Сер.** HIGH · **Статус** ENV · **Состояние** VERIFIED

* **Симптом:** тесты «зелёные», но проверяют не тот код: `learning.__file__` →
  `C:\Users\asd\Bossman\wt-release\...`; в `sys.path` несуществующие `C:\c\Users\...`.
* **Как обнаружить:** `python -c "import learning,bossman;print(learning.__file__,bossman.__file__)"` из нейтрального каталога.
* **Причина:** `/c/Users/...` из PowerShell превращается в `C:\c\Users\...` и отбрасывается; venv содержит
  editable-установку `wt-release`, которая и импортируется. Кроме того, текущий каталог (`sys.path[0]`) перекрывает
  `PYTHONPATH`: запуск из `wt-release` снова берёт `wt-release`.
* **Исправление:** только Windows-пути (`cygpath -w` в Git Bash), разделитель `;`, нейтральный cwd, проверка `__file__`.
* **Профилактика:** установленный кандидат не чинить через pip/PYTHONPATH/копирование исходников — это новый артефакт.
* **Доказательство:** `E05_pythonpath_posix.txt` (три запуска интерпретатора на машине).

## CI-FLAKY-TIMING-TESTS
**Сер.** MEDIUM (P2) · **Статус** OPEN · **Состояние** VERIFIED (процедура триажа)

* **Симптом:** root-ci красный на `test_v5_human_speed::test_objective_cas_under_10ms…` (p100 21.5 мс на общем раннере)
  при идентичном коде; также `test_action_contract::test_code_action_not_satisfied_by_an_unrelated_terminal_call`
  (py3.11, `waiting_approval` vs `failed`), `test_refresh_during_typing_ui::test_a_late_connection_does_not_eat_what_the_owner_is_typing`
  (py3.14); на Windows `test_ux2_desktop::test_second_window_refused_while_first_instance_alive` подделывает `pid=1`.
* **Как обнаружить:** `tools/exact_sha_certify.py`; сравнить тот же тест на соседних SHA; `run_attempt` в сертификате.
* **Исправление (триаж):** один повтор job на том же SHA; зелёный + идентичный код → флейк, не регрессия
  (0c1cbe65: root-ci attempt 2 → CERTIFIED 10/10). Сами тесты не исправлены.
* **Профилактика:** не переписывать продукт ради флейка; тайминги — устойчивой статистикой (квант таймера Windows 15.625 мс).
* **Доказательство:** `E06_flaky_ci_timing.txt`.

## START-MODELS-STALE
**Сер.** HIGH · **Статус** ENV · **Состояние** VERIFIED

* **Симптом:** Bossman ищет MAIN на 8081, а там FAST/ничего; пустые ответы; `-Target stop` гасит чужие прогоны.
* **Как обнаружить:** сравнить `C:\Users\asd\Bossman\start-models.ps1` с аудированной раскладкой
  (MAIN 8081, FAST 8082, GPT-OSS 8083, `--jinja --reasoning off`); `GET /v1/models` на каждом порту.
* **Причина:** скрипт не версионируется и отстал от раскладки портов/флагов.
* **Исправление:** 2026-09-22 (резерв `.bak-20260922`): 8081/8082 и `--reasoning off`.
  **Осталось:** в скрипте `-c 32768` (мало для Qwen Code — MAIN для агентов поднимать `-c 131072 -np 1`) и
  `stop` = `Stop-Process -Name llama-server -Force` (гасит все серверы).
* **Доказательство:** `E08_start_models_stale.txt`, `E01_reasoning_off.txt`.

## FREEZE-MOVING-CANDIDATE
**Сер.** HIGH · **Статус** ENV (процесс) · **Состояние** CANDIDATE

* **Симптом:** exact-SHA матрица (~40 мин) не успевает на объявленном SHA — head уходит вперёд каждые несколько минут.
* **Как обнаружить:** `git log origin/release/bossman-owner` после объявления кандидата; сертификатор видит прогоны не того SHA.
* **Причина:** параллельные сессии пушат документацию в канон во время заморозки (F-22).
* **Исправление/профилактика:** после входа кандидата в матрицу — никаких пушей в канон; наблюдения — в `evidence/*`;
  заморозку ломает только воспроизведённый релизный дефект.
* **Источник:** `owner-repair/CONTINUATION.md` (п. 5), `docs/owner/TOMORROW_OPERATOR_RUNBOOK.md` §2.

## NEVER-KILL-ALL-PYTHON
**Сер.** CRITICAL · **Статус** ENV (процесс) · **Состояние** CANDIDATE

* **Симптом:** после «зачистки» падают чужие прогоны, backend, Telegram-компаньон, модели других сессий.
* **Как обнаружить (до действия):** `Get-CimInstance Win32_Process -Filter "Name='python.exe'" | select ProcessId,CommandLine`.
* **Правило:** запрещено `taskkill /F /IM python.exe`, `Stop-Process -Name python` и `Stop-Process -Name llama-server`
  (включая `start-models.ps1 -Target stop`) во время прогонов; останавливать только проверенные PID своего теста;
  ресурсы — через lease.
* **Источник:** `docs/owner/TOMORROW_OPERATOR_RUNBOOK.md` §4.

## B4-DOWNLOAD-IS-STARTING
**Сер.** MEDIUM · **Статус** FIXED (`79a12276`) · **Состояние** CANDIDATE

* **Симптом:** `playwright._impl._errors.Error: Page.goto: Download is starting` → `ERROR: Exception in ASGI application` (500).
* **Причина:** переход на URL-вложение запускает скачивание, `goto` бросает исключение.
* **Исправление:** слушатель скачиваний, политика, `.part` → размер/sha256/тип, карантин исполняемых, честный 422;
  регрессия `command-center/tests/test_browser_download_b4.py`.
* **Доказательство:** `E12_b4_download.txt` (`startup-night4.log`). Регрессия в этой сессии не перезапускалась;
  на установленном кандидате — NEEDS_RETEST.

---

## Как это попадает в память Bossman

Импорт идёт через настоящий путь обучения Bossman, ничего не дублируя:
`tools/import_operational_lessons.py` → `ApprenticeMemory.record_lesson` (строгий `assert_sanitized`) →
`learning.trace.LearningStore` (валидация, инварианты VERIFIED, журнал, версии/tombstone, атомарная запись).
Перед записью: `learning_guard.reject_if_holdout`, `cybersec.injection.scan` (high/critical → отказ),
проверка sha256 доказательств. Запись с состоянием VERIFIED в каталоге, у которой доказательство отсутствует
или изменено, сохраняется как UNVERIFIED (CANDIDATE_LESSON) с причиной. Повторный импорт ничего не дублирует;
изменённая запись становится новой версией.

Каждая запись: `record_type=lesson`, `task_type=owner-ops`, `task_id=oplesson:<ID>`, `applicability`
(scope `owner-machine/windows-11`, privacy `internal-owner-ops`, provenance, lesson_state), `evidence`
(путь + sha256), для VERIFIED — `verifiers` (external_tool) и `evidence_records`.

Ограничение (честно): в продукте ApprenticeMemory пока не привязан к постоянному каталогу данных и уроки
не подмешиваются в каждый вызов модели автоматически (см. `docs/evo/DURABLE_MEMORY_OPERATING_CONTRACT.md`).
Поиск — явный: `--query` или `search_lessons()`; строки логов — `--match-line` / `match_log_line()`.

### Команда для интегратора (НЕ выполнена на живых данных)

Выполнять после установки, из нейтрального каталога, Windows-путями. Каталог данных выбирает интегратор;
ниже — предлагаемый (он же должен стать каталогом ApprenticeMemory в продукте).

```powershell
Set-Location C:\Users\asd
$repo = 'C:\Users\asd\Bossman\wt-guides'          # или checkout release/bossman-owner после слияния
$py   = 'C:\Users\asd\Bossman\wt-release\.venv\Scripts\python.exe'
$data = "$env:LOCALAPPDATA\Bossman\CommandCenter\learning\apprentice"
$env:PYTHONPATH = "$repo;$repo\bossman-core"

# 1) предпросмотр (без записи, флаг не нужен)
& $py "$repo\tools\import_operational_lessons.py" --data-dir $data --dry-run

# 2) запись (флаг записи обучения включается явно, только для этой команды)
$env:BOSSMAN_SKILL_RECORDING = '1'
& $py "$repo\tools\import_operational_lessons.py" --data-dir $data
Remove-Item Env:BOSSMAN_SKILL_RECORDING

# 3) проверка после «рестарта» (новый процесс, тот же каталог)
& $py "$repo\tools\import_operational_lessons.py" --data-dir $data --query "empty answer from local model"
```

Ожидаемо: `added` 16 (7 verified, 9 candidate), `rejected` пусто, код выхода 0; повторный запуск — `unchanged` 16;
первый результат запроса — `LLM-REASONING-OFF`, `VERIFIED`. Код выхода 1 — есть отклонённые записи (см. `rejected`),
2 — не включён `BOSSMAN_SKILL_RECORDING`.

Тесты: `python -m pytest tests/test_import_operational_lessons.py`.
