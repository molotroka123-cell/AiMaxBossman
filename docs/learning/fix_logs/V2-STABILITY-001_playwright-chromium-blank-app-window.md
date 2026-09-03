# V2-STABILITY-001: Chromium из кэша Playwright в `--app` открывает ПУСТОЕ окно

## Symptom
На Windows/desktop `bcc-desktop` открывает окно Chromium, но страница Command Center
не загружается: пустой белый экран, запрос к серверу (`GET /`) вообще не уходит.
Окно «открылось», но похоже на сломанное; без CDP не отличить от тихих крашей.
Это одна из причин «не открывается / выкидывает пользователя».

## Repro (воспроизведено на этой машине)
1. `BCC_DATA_DIR=<temp>`; `BCC_PORT=8877` (loopback, данные владельца не трогались).
2. `python -m bcc` → сервер жив, `/api/identity` отвечает `bossman-command-center`.
3. Chromium 151 из `%LOCALAPPDATA%\ms-playwright\chromium-1234`:
   ```
   chrome.exe --app=http://127.0.0.1:8877/ --user-data-dir=<tmp> \
     --remote-debugging-port=9333 --remote-allow-origins=*
   ```
4. CDP: target `page` есть с URL `http://127.0.0.1:8877/`, но `document.readyState`
   = `complete`, `document.body.innerHTML.length` = 0, `location.href` = `about:blank`,
   `performance.getEntriesByType('navigation')` пуст. В логе сервера запроса нет.
5. Матрица: та же самая команда с **системным** Google Chrome 152 — страница
   загружается (body ~22Кб), та же команда с Edge — загружается.
   То есть: это не флаг, не порт и не профиль — это конкретная сборка Chromium.
6. Сам Chromium-кэш исправен: `Browser.getVersion` по CDP отвечает, headless-режим
   работает. Ограничение проявляется именно в режиме `--app` в данной среде.

## Evidence
- `targets-debug2`: единственный page-target с URL, но документ пуст (diag3/diag4).
- `diag5`: M1 `--app` = body 0; M3 нормальная навигация = body 22122.
- `diag6`: с PW-Chromium req_seen=False; с системным Chrome и Edge req_seen=True.
  (Примечание: метрика `req_seen` снималась до исправления подсчёта доступов —
  решающее различие подтверждено матрицей M1/M3 + системным Chrome в B2..B8.)
- После перехода харнесса на системный Chrome: сценарии  1–8 все PASS (см.
  `docs/audits/2026-09-03__v2-stability-pass__interim.md` и финальный отчёт).

## Root cause
Не применяется патч к коду владельца — применяется **диагностика**: сборка
Chromium из кэша Playwright (`%LOCALAPPDATA%\ms-playwright`, `/opt/pw-browsers`)
в этой среде не выполняет навигацию в режиме `--app`. Системный Chrome/Edge
работает. Владелец, у которого autodetect выбрал такой браузер, получал пустое
окно без объяснения.

## Rejected hypotheses
- Origin guard CDP / `--remote-allow-origins=*`: совпадает по времени, но головой
  не навигация (сырой WebSocket CDP работает; пустая страница была и без драйвера).
- Конфликт профилей/остаточные процессы: опровергнуто чистой перезапусками и
  kill дерева процессов (картина не менялась).
- Порт/сервер: сервер стабилен, identity 200; при прямом запуске URL без --app
  страница грузится с того же сервера.
- `--no-first-run`/дебаг-флаги: исключены матрицей C2 (без флагов — то же пустое окно).

## Fix
Минимальный доказанный, для `command-center/bcc/desktop.py` (run()):
- если autodetect выбрал браузер, в пути которого есть `ms-playwright`/`pw-browsers`
  /`playwright` — печатается предупреждение с готовой инструкцией (путь к
  системному Chrome и `--browser`/`BCC_DESKTOP_BROWSER`), запись в `desktop-run.log`;
- реализован пресет явного таймаута окна: `--window-timeout` /
  `BCC_APP_STARTUP_TIMEOUT`, при выходе 124 — читаемая причина + совет открыть URL
  в обычном браузере (порядок «веб-версия на случай пустого окна»);
- `--status` (JSON desktop.lock + живость сервера) и `desktop.lock` с
  `window_opened_at` для диагностики без CDP;
- реестровый поиск браузеров Windows (`App Paths\chrome.exe/msedge.exe`,
  WOW6432Node) — автодетект сразу находит системный браузер;
- `bcc-open` (`--web`): веб-версия в системном браузере, сервер поднимается тем же
  процессом;
- `find_browser` переупорядочивает кандидатов только при вызове со значениями по
  умолчанию (явный список не трогается — регрессия `test_find_browser_prefers_preinstalled_chromium`).

## Regression
`command-center/tests/test_ux2_desktop.py`: +5 тестов
(`test_runtime_window_timeout_is_passed_to_launcher`, `test_lock_records_window_opened_at`,
`test_status_prints_json_state_without_launching`, `test_bcc_open_entry_command_gets_web_flag`,
`test_bcc_desktop_entry_does_not_inject_web_flag`).
Прогон: 40 passed / 1 failed на Windows (упавший — пре-существующий,
`test_second_window_refused_while_first_instance_alive`, Unix-семантика pid=1;
на pristine коде падает так же; CI на Ubuntu проходит).

## Adversarial checks
- Пустой/недоступный сервер: прежние коды выхода (2,3,4) не менялись.
- Явный `--browser` + `--browser-arg`: передача не тронута (логика не переставлялась).
- `--web` не инжектируется для `bcc-desktop` (тест) и инжектируется для `bcc-open` (тест).
- ALL_PROXY/HTTP(S)_PROXY на мёртвый прокси: окно и логин работают (сценарий B8).
- Токен в URL/логах: не передаётся; секрет в этом разборе не упоминается.

## Lesson
Autodetect браузера не может доверять «есть бинарник» — для режима `--app` нужен
честный признак сборки (системный против кэша автоматизации), а окно без
навигации обязано объясняться владельцу, а не выглядеть тихим крашем.
Живой desktop-сценарий на системном браузере — единственное доказательство;
headless-тесты и CDP-пробы зелёными не считаются.