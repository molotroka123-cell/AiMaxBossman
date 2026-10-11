# Bossman 2.1 RC: отчёт о сборке и проверках (11.10.2026)

**Статус: кандидат НЕ готов к передаче посторонним.** Это не freeze и не релиз. Доказано: сборка, чистый старт, UI-смоук
(кроме одного пункта, см. разд. 4), сканы артефакта. Не доказано: CI на точном SHA, отказы 51 теста не разобраны,
лицензия кода не объявлена, исправление из п. 2.3 не вошло в собранный архив.

## 1. Точные идентификаторы

| Что | Значение |
|---|---|
| Ветка | `release/bossman-2.1-rc-20261011` от `origin/feat/bossman-genjutsu-jeff-unified-20261010` @ `e27abf18` |
| SHA, из которого собран архив | `db249bde16e234023feeb51568962db26d010541` |
| Архив | `C:\Users\asd\Bossman\release-rc-20261011\dist3\BOSSMAN-Windows-x64-db249bde16e2.zip`, 793 984 052 байт |
| SHA-256 ZIP | `b34d62c4732f82367d5b25110f16d16432697537a5ef3aeac85c0ff0104351dd` |
| MANIFEST | 23 832 файла, 2.03 ГБ, `build_inputs.locked=true`, `required_downloads=[]`, `source_dirty=false`, `installed_acceptance=NOT_RUN` |
| HEAD ветки | коммит `178dbeca` (правка API) и коммит с этим отчётом поверх `db249bde`; ни один не входит в архив |

ВАЖНО: папка `dist3\BOSSMAN-...` после UI-смоука не чистая (смоук запускал backend из неё и породил `comtypes/gen`, `__pycache__`).
Раздавать нужно ZIP, а не папку; ZIP записан до смоука. `dist2` (сборка из `942f3527`) устарела.

## 2. Что исправлено (каждое с тестом, красный до правки)

1. Сборка падала: `docs/v8/owner-final-run/NIGHTLY_PROGRESS_RU.md` не объявлен в `OWNER_RUN_FILES`. Файл перенесён в
   `docs/owner/`; тест `test_checked_in_owner_run_directory_matches_the_declared_contract`.
2. Путь сборщика (имя пользователя, папка вывода) был в 9 202 `.pyc` и 14 `direct_url.json`. `compileall -d site-packages`,
   путь в `direct_url.json` нейтрализован; повторный скан 9 202 -> 0 (3 `.pyc` в `comtypes/gen` создал мой смоук, не сборка).
   Тест `test_bytecode_and_wheel_metadata_carry_no_builder_path`. Вошло в архив.
3. `POST /api/tasks` с несуществующим `agent_id` и `run_now=false` отвечал 500 (FOREIGN KEY), теперь 404. Тест
   `test_task_for_unknown_agent_is_a_client_error_not_a_500`. НЕ вошло в архив (найдено уже на собранном).

## 3. Что PASSED (команды)

| Проверка | Команда | Результат |
|---|---|---|
| Сборка | `python tools/build_windows_bundle.py --out <dist3> --zip --profile release` (venv с закреплёнными build-tools) | `BOSSMAN_WINDOWS_BUNDLE=BUILT`, `BOSSMAN_BUILD_INPUTS=LOCKED` |
| Secret scan репозитория | `python tools/ci_secret_scan.py` | `Secret-pattern scan: PASS` |
| Скан артефакта (секреты, пути, типы) | `scan_bundle.py` -> `runs/rc_20261011/bundle_scan3.json` | реальных секретов 0 (7 срабатываний: тестовые канарейки и код разбора ключей); весов моделей/LoRA 0 (только тестовые `.npz` и `silero_vad.onnx` 1.2 МБ); личных медиа 0 (только иконки); БД 0. `molotroka` = публичный URL репозитория; `6273524c` = git blob sha |
| Офлайн-смоук | `BOSSMAN_LOCAL_ONLY=1 python tools/offline_smoke_local_only.py` | rc 0: openrouter и anthropic отказали на границе провайдера, 0 внешних соединений; loopback-модель ответила за 38.8 с |
| compileall | `python -m compileall -q command-center/bcc bossman-core/bossman bossman_shared tools` | rc 0 |
| JS | `node --check` по 83 файлам `ui/*.js` (без vendor); `node --test` четыре файла | 0 ошибок; 17+5+26+12 pass |
| Чистый старт | backend из архива, пустой `BCC_DATA_DIR`, порт 18931, HOME/LOCALAPPDATA в песочнице | жив за 4.1 с, `bcc.db` создана, `/api/tasks` без сессии даёт 401 |
| UI (headless Chromium из архива) | `rc_ui_smoke.py` (в `runs/rc_20261011/`), скриншоты в `release-rc-20261011\smoke-out` | вход по токену, 48 пунктов навигации, 10 страниц открылись без page error и 5xx (`home-v3, mission_console, apps, control, video-studio, router, skills, resources, capability-tree, openrouter`); кнопка STOP найдена в UI; `/api/control-plane/stop-all` отвечает 200 |
| Умолчания чистой установки | `ui_smoke.json`, `defaults` | local-only не включён (`local_only_enforced=false`; это умолчание, его надо включать явно), провайдеры `[]`, модели `[]`, агентов и задач нет |
| Лицензии Python-пакетов | `runtime\python.exe lic.py` -> `py_licenses.json` | 132 дистрибутива: 116 permissive/weak, 13 пакетов Bossman без указанной лицензии, MouseInfo GPLv3+, ai-webcam-vision «Proprietary», certifi и tqdm MPL-2.0 |
| pytest `command-center`, `-n 2` | `python -m pytest -q -n 2 --timeout=180 --timeout-method=thread -p no:cacheprovider` | **9920 passed, 61 skipped, 51 failed, 2 errors**, 5431 с. Дерево менялось в ходе прогона (коммиты 942f3527, db249bde, 178dbeca; менялись сборщик, один тест, docs) |

## 4. Что FAILED

1. **UI-смоук, пункт `task_created`:** на чистой установке агентов нет; первая версия скрипта взяла `agent_id=1` и получила 500.
   Это дефект API (п. 2.3). Итог в `ui_smoke.json`: `FAIL`. Обновлённый сценарий «провайдер -> модель -> агент -> задача ->
   читаемый отказ -> STOP» **не запускался** (приказ остановиться).
2. **51 failed + 2 errors в pytest** (`runs/rc_20261011/cc_pytest_failures.txt`). По файлам: Jeff/PIT
   (`test_jeff_owner_bugtest_fixes` 8, `test_jeff_1_8_stream` 8, `test_jeff_1_8_route` 6, `test_pit_rc19_audit_jeff` 4,
   `test_jeff_1_8_web_stream` 4, `test_pit_rc19_jeff` 3, `test_jeff_availability` 2, `test_jeff_1_8_local` 2, и по одному
   `test_pit_web`, `test_pit_telegram_fake`, `test_jeff_ux_browser`, `test_jeff_audit_fixes`, `test_leaf_jeff_insights`),
   `test_direct_gen_faceswap` 4, `test_open_news_skill` 2 errors (имя параметризованного теста), по одному
   `test_video_descriptor_boundary`, `test_ux2_pages_sweep`, `test_terminal_session_retention`, `test_health_model_components`,
   `test_leaf_plugins_real_handlers::test_ollama_chat_live_local_model` (требует живой локальной модели).
   Разбор «окружение или дефект» НЕ сделан. Одно проверено отдельно: `tests/test_jeff_1_8_route.py` падает и в изоляции, ответ
   «Привет 🙂» вместо ожидаемого «готово». Похоже на дрейф быстрого ответа-приветствия относительно теста, но это не доказано.
3. Два теста зависли примерно на 12 минут (`--timeout-method=thread` не сработал); я убил их workers, xdist пометил упавшими.
   Какие именно, не установлено.

## 5. NOT RUN

* CI на точном SHA (GitHub Actions: `windows-bundle.yml`, `command-center-ci.yml` и другие обязательные). Локально проверить нельзя.
* `tools/verify_windows_bundle.py --archive <zip> --expected-sha db249bde...` (приёмка архива как продукта).
* pytest `bossman-core`, корневой `tests/` (включая `test_mp21_d_volume_suite_quarantine.py`), `tools/astra_security_gate.py`
  (bandit, pip-audit), ruff, mypy.
* Умолчания Code Mode, браузерного поиска Jeff, торговли paper-only: не измерены в рантайме этого архива (опора только на код и
  тесты прежних прогонов). Каталога `solana_volume_suite` в архиве нет по списку сборщика; отдельной проверки по именам файлов,
  кроме скана типов, не делалось.
* Обновление/откат на старую схему БД, установка на машине без Python, SmartScreen, подпись кода.
* Живые провайдеры, Telegram, Gmail.

## 6. Блокеры и следующая команда

| Блокер | Следующий шаг |
|---|---|
| Лицензия кода Bossman не объявлена (нет `LICENSE` в корне; в архиве GPL-FFmpeg и GPLv3+ MouseInfo, у ai-webcam-vision «Proprietary») | решение владельца, затем файл `LICENSE` и обновление `LICENSES/` |
| 51 failed + 2 errors | `cd command-center; python -m pytest -q -p no:cacheprovider tests/test_jeff_1_8_route.py tests/test_pit_rc19_jeff.py` и далее по группам |
| Правка 404 не в архиве | пересобрать из HEAD ветки: `python tools/build_windows_bundle.py --out <новый dist> --zip --profile release` (около 12 мин) |
| Полный UI-сценарий | `<копия распакованного ZIP>\runtime\python.exe rc_ui_smoke.py <папка> 18931 <пустой data> <out>` |
| CI на точном SHA | push ветки (ff-only) и запуск workflow в GitHub Actions; затем `tools/exact_sha_certify.py` |
| Приёмка архива | `python tools/verify_windows_bundle.py --archive <zip> --expected-sha <sha> --out acc.json` |
| Подпись кода, юридическая политика, контакт поддержки | OWNER_REQUIRED |
| Модели и веса с NC-лицензиями | перечень в `RELEASE_RC_GATES_20261011.md`, разд. 3; не вкладывать |

## 7. Как владельцу установить и откатить

Установка: сверить `Get-FileHash <zip> -Algorithm SHA256` со значением из разд. 1; распаковать в новую папку (не поверх); перед
первым запуском скопировать `%LOCALAPPDATA%\Bossman\CommandCenter`; запустить `Start-Bossman.cmd`. Для пробы на отдельных данных
задать `BCC_DATA_DIR` на пустую папку. Откат: запустить `Start-Bossman.cmd` прежней папки (`BOSSMAN-Windows-x64-6de18f8d9a08`);
при несовместимости вернуть копию данных; подробно `docs/owner/ROLLBACK_RU.md`. Откат схемы БД назад не проверялся.

## 8. Что осталось на диске

`C:\Users\asd\Bossman\release-rc-20261011\`: `dist2` (устарела), `dist3` (RC), `venv-test`, `venv-build`, `logs`, `smoke-out`,
`smoke-data`. Сервисы :8800/:8801 и каталог данных владельца не трогались; процессы смоука остановлены.
