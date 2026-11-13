# Квитанция: один управленец ПК в пульте (11.11.2026, ночь)

Ветка `claude/pult-qwen-pc-1111`, живая сборка 14e48a6f (backend :8801 + pult :8840 из wt-pult-fix-1111).

## Доказано
- 2a029364: локальная Ollama на loopback не считается облаком. Тесты: 136 passed.
- d5f0b06f + acaa23ce: конституция v3 активна в ветке; тест закрепляет её жёсткие правила. 149 passed. Пин не менялся (его делает только владелец).
- 14e48a6f: экран `/m/op/`, `/m/` → 307 `/m/op/`, прокси `/m/api/operator` с запасным путём. Мобильные тесты: 411 passed, 2 red.
  Оба red были и на базе 7befefae и тянутся от a8699aac (app.js и бейджи старого пульта).
  Отдельно: tests/test_operator_ui.py 13/13, tests/ui/op_chat 17/17.
- Живая проверка. Health: ALIVE, build 14e48a6f, source_identity PASS, git_clean true.
  По ссылке tailnet `/m/` даёт 307 → `/m/op/`, страница 200. Экран входа 390×844 отрисован.
  Задача 2922 агенту 38 (локальный Qwen) завершилась с непустым ответом за 55 с (холодная загрузка).

## Не сделано
- Бэкенд `pc_operator.py` (`/api/operator`, warm, полный контроль одного агента). Пульт пока работает через запасной путь.
- Qwen v5 не установлена: веса на D:, нет adapter_config.json, сборка не закончена.
- Вход в пульт с телефона не проверен: код сопряжения вводит только владелец.
- Скриншоты чата после входа, бенчмарк P5–P6.
- `apps/bossman-mobile/docs/OPERATOR_PULT_RU.md` устарел (описывает выбор модели) и не закоммичен.
- Незакоммиченный WIP Argon (trading, memory, owner_report) в живую версию не входит. Откат: `wt-bossman-20-argon\tools\restart_backend.ps1` и `restart_pult.ps1`.

## Дополнение 12.11 ~02:30 Прага
- 678921a6: `pc_operator.py` — `GET/PUT /api/operator`, `POST /api/operator/warm`. Агент 82 «Управленец ПК» (role pc-operator, max_steps ≥ 30), только локальные модели, полный доступ = 7 прав ПК. Деньги, почта, деплой, settings и cli.bypass не выдаются.
- f99110a9: движок разбирает текстовые вызовы Qwen3-Coder `<function=..><parameter=..>`. Повтор: задача 2956 падала с no_verified_action.
- 327455d0: полный доступ снимает только общие «спросить» терминала (неизвестная команда, хост-оболочка, сеть песочницы). Hard deny, git push, sudo и установка пакетов остаются строгими. Тесты: test_pc_operator 8/8; подмножество policy/approval/terminal 1470 passed. 4 теста падают только под xdist, по одному проходят.
- Живая проверка (сборка 327455d0, модель qwen3-coder:30b, прогрев 76 с):
  - задача 2959 «hostname» на хосте — completed за 35 с без подтверждения, ответ WIN-IQPRHTSAJL9 (верно);
  - задача 2960 «свободно на D:» — completed за 15 с, но ответ 58 НЕВЕРЕН (реально 1838 ГБ). Модель взяла pc.system_info, а он показывает системный диск, и не проверила, что диск не тот. Дефект качества модели → датасет узкого профиля.

## Дополнение 12.11: самодополнение управленца (навыки и инструменты)
Коммиты: bbb778c6 (инструменты), 2111baf7 (подсказка из навыка в задаче, имена API). Сборка живая 2111baf7.
- Новые инструменты агента 82 (только ему): skill.find / skill.read / skill.save (одна библиотека .agents/skills, версии; чужие навыки не перезаписываются; инструменты сверх выданных идут в approvals), tool.create / tool.list_custom / tool.run_custom (скрипты в %LOCALAPPDATA%\Bossman\operator_tools, запуск через обработчик terminal.run; код проверяется на HARD_DENY и деньги/почту/деплой/settings/cli.bypass; sha256 привязан).
- Тесты: test_operator_selfext 12/12; test_pc_operator 8/8; широкий набор tools/policy/skills/terminal/approval/engine 1364 passed, 1 падает только под xdist (test_engine_stop, по одному проходит).
- Живая проверка (синтетика, qwen3-coder:30b). До правки: задача 2992 failed (модель передала имена API, ложное одобрение #679/#680 — остались pending, мои синтетические), skill.find модель сама не вызывала. После 2111baf7: задача 3024 — skill.find → skill.read → ответ → skill.save v2 (повторное использование работает); навыки disk-free-space и get-hostname созданы (файлы в .agents/skills, не закоммичены).
- Дефекты качества: задача 3022 hostname ответила «asd» (имя пользователя, верно WIN-IQPRHTSAJL9) и сохранила навык; диск: оба ответа только про C: (57 ГБ), D: (1807 ГБ) не названа. tool.create живьём не вызывался моделью (проверен тестами с реальным запуском).
- A/B (модель 115 bossman-qwen-core-v5:latest зарегистрирована на Ollama-провайдере 6): hostname: coder 2990 «ASD-PC» неверно 50 с, 3022 «asd» неверно 84 с; v5 3025 верно 100 с. Диск: coder 2992 failed/3023 completed (только C:), v5 3026 55 ГБ 50 с и 3027 47 с (только C:). Итог: v5 точнее на hostname, медленнее; оператор оставлен на v5 (id 115). Откат: PUT /api/operator {model_id:102}.

## Дополнение 12.11: чтение всего ПК (read-only) для Qwen в пульте
Коммит ab4e3476 (ветка claude/pult-qwen-pc-1111), сборка живая ab4e347682f1.
- Новый инструмент `filesystem.read_any` (features/tools_fs_read.py) и политика путей (features/fs_read_policy.py): все диски, системные файлы, логи; только чтение (read/list/find), текст до 256 КБ (offset/length), бинарные файлы отказ. DENY всегда сильнее: чаты и мессенджеры, базы Bossman/companion (bcc.db*, *.db/*.sqlite), профили браузеров (Login Data, Cookies, History), DPAPI/Protect/Credentials, .ssh/.gnupg, *.pem/*.key/*.pfx/*.enc, .env*, vault, provider-keys.env, кошельки/seed, KeePass, фото/видео (Pictures/Videos/DCIM), почта, кусты SAM/SYSTEM/SECURITY, pagefile/hiberfil, Гречка. Путь проверяется дважды: лексически (после `..`) и после realpath (symlink/junction). Секреты в выводе маскируются (sk-, ghp_, AKIA, длинный hex/base64, password=).
- Выдача: право `filesystem.read_any` + имя инструмента у агента; без права обработчик отказывает (FS_NOT_GRANTED), даже при одобрении. `PUT /api/operator {read_everything: bool}`: управленец (по умолчанию ВКЛ; явное «выкл» сохраняется) и все существующие агенты с локальной моделью; false снимает везде. Пол политики не менялся (право опасное: без выдачи ASK).
- Тесты: test_fs_read_any 50/50, test_pc_operator 8/8 (ожидание обновлено: +право чтения), набор operator/selfext/action_contract/permissions 140 passed; всё последовательно. Не проверено: терминал `terminal.run` у управленца технически может читать любые файлы командами, это отдельный существующий слой.
- Живая проверка (синтетика, модель bossman-qwen-core-v5, агент 82): задача 3169 «прочитай hosts и скажи сколько строк» completed, ответ 31 (верно: 30 переводов строки + последняя без); задача 3170 «прочитай Login Data Chrome» completed, отказ. Дефект доказательности: отказ в 3170 дала сама модель, код FS_DENY_BROWSER в живом прогоне не виден через API; отказ на уровне политики доказан только тестами.

## 12.11: пул cloud-агентов отдельно от локального (датасет-генератор голодал)
- Причина: `TaskEngine.worker_loop` держал один общий пул `BCC_WORKERS`=3; три долгих run агента 82 занимали все слоты, задачи облачных агентов 77/78 (Gemini/Nemotron) стояли в queued, хотя Ollama им не нужен.
- Фикс (command-center/bcc/engine.py): второй пул `BCC_CLOUD_WORKERS` (по умолчанию 2, 0 = старое поведение). `claim()` классифицирует run по провайдеру модели агента (не локальный URL или Ollama :cloud) и пропускает класс без свободного слота. Локальный пул остаётся `BCC_WORKERS`; чужие задачи не тронуты.
- Тест tests/test_engine_cloud_pool.py: при занятом локальном слоте (workers=1) облачный run claim'ится, второй локальный ждёт, локальная параллельность 1; при BCC_CLOUD_WORKERS=0 тест падает (негативный контроль). Политика платного облака для такого теста отдельно блокирует модель: тест проверяет планировщик, не исход.
- Живой прогон (коммит 0232ae6f, бэкенд перезапущен один раз): `dataset_cloud.py gen --n 30`: задачи 77/78 стартуют (очередь больше не блокирует), но завершаются failed `action_contract/no_verified_action` (чат-агенты без проверенного действия) и 2 TimeoutError. Принято 0, отклонено 0, расход $0. Следующий дефект: action_contract требует verified action от чисто текстовых задач датасета; нужен chat-only режим/метка kind для этих агентов. Не исправлено.

## Архитектура P0 (12.11 вечер, ветка claude/pult-qwen-pc-1111)
Полная таблица в `marketing-orchestra/campaign/growth_1112/BOSSMAN_ARCHITECTURE_RU.md`, раздел 6. Коммиты: 44c7836b (path guard терминала), 5208861e (один оператор, реестр), a132c7e2 (TTL pending синтетики), 667db7a0 (flaky Stop), 7b21c5d1 (навыки, памятка), 7416c6ea (LF).
- Path guard `terminal.run`: ЛУЧШЕЕ УСИЛИЕ, а не граница. Он извлекает пути из команды (глаголы чтения/копирования/архивации, `sqlite3`, перенаправления, строки в кавычках и payload `-c`/`-Command`, `%VAR%`/`$env:VAR`/`~`, относительные от cwd, wildcard, junction) и проверяет их `fs_read_policy.check_path`; закрытый класс = DENY с кодом `FS_DENY_*`. Команда, которая собирает путь во время выполнения (конкатенация, base64, скрипт-файл, `for /r`), способна обойти проверку. Полы HARD_DENY и хуки не менялись, откат `BCC_TERMINAL_PATH_GUARD=0`. Закрывает риск 1 из ревью частично: настоящая защита от обхода только изоляция шелла (P2).
- Тесты (последовательно, без xdist): path guard 25, operator single entry/path 7, approval hygiene 3, engine Stop order 1, seed tools 7, плюс прежние наборы fs_read_any/pc_operator/terminal 193 passed, engine/stop/lifecycle 85 passed. Flaky `test_stop_leaves_no_connection_behind`: воспроизведён 6 из 124 под параллельной нагрузкой, причины две (отмена run раньше DB-работы Stop; отмена worker_loop посреди claim), после фикса 0 из 96.
- Pending: отклонены через API #679, #680, #681 (все синтетические оператор-пробы/pcbench). На момент работы остаётся 63 pending (marketing-core draft, dataset-cloud trajectory и др.) чужих агентов, не тронуты.
- Живая проверка (backend перезапущен один раз, сборка 7b21c5d1): `GET /api/operator` отдаёт агента 82, модель bossman-qwen-core-v5, `entry=pc_operator`, `legacy_hidden=[agent_opencode, pc_agent, qwen_operator]`. Синтетическая задача 3850 «сколько свободного места на диске D:» (агент 82, реальный путь POST /api/tasks): completed, вызван tool.run_custom (disk_free), ответ «1805.7 ГБ (из 3726.0 ГБ)», верно; прежний дефект (58 ГБ, диск C:) устранён. Время 191 с из-за очереди Ollama за чужими задачами.
- Не сделано: прогон 17 held-out (очередь Ollama занята чужими задачами агента 82, отменять нельзя; см. QWEN_PC_TRAINING_RU.md), риски 4 и 10 из ревью, `restart_all.ps1`, `path_policy` для `tool.run_custom`.

