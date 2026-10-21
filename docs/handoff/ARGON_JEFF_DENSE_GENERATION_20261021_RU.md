# Jeff во время плотной генерации фото — 21.10.2026 (для Argon)

Ветка `fix/jeff-during-dense-generation-20261021` от `origin/main` `ae248d8f`. В `main` НЕ влита.
Живой проверки нет (NOT_RUN): по заданию ни один сервис не перезапускался, живой checkout не трогался.
Terminal Run: тот же Bossman (тот же бэкенд, данные, модели). Лестницу North Star эта правка не двигает — это эксплуатационный фикс.

## Что сломалось вживую 21.10
- **13:41** — включён resident-режим direct-gen (sd-server держит фото-модель между задачами), пошёл плотный поток фото.
- **Jeff остановлен governor.** `jeff_during_generation: cloud_free`, проверенного бесплатного маршрута нет → откат в `pause` → `owner_one_bossman.py stop --kinds jeff --hold`, записан `hold-jeff` → Jeff молчал весь поток. Вдобавок ENTER ACTIVE выгружает ВСЕ модели Ollama, включая Gemma Jeff. (И сам `cloud_free` через `generation-cloud.json` переводит Jeff на облачные модели, хотя с 17.10 Jeff только локальный.)
- **Gemma выбивалась direct-gen** (подтверждено вживую в 14:26). «Gemma по запросу»: `_default_qwen()` выбирает «gemma … uncensored» — ту же модель, что у Jeff (`gemma4-31b-uncensored:latest`, 34.7 ГБ Q8), и после каждого assist `_unload_ollama(name)` её выгружал. Главное: `_run_sd` в начале КАЖДОЙ sd-задачи зовёт `_unload_ollama(assist_model)` — у ASSISTED-задачи это Gemma, у DIRECT — последний assist или `qwen_lookup()`, то есть снова Gemma. Каждое фото из очереди выбивало модель Jeff.
- **14:02** — живой checkout переключили на незапушенный коммит `288f79f5`, бэкенд упал, 4 фото-задачи остановились; Claude поставил их в очередь заново.
- **14:08** — `check_violation`: «COST VIOLATION … deepseek-v4.1-flash-uncensored: leaving cloud_free for pause» → Jeff снова остановлен с hold («Jeff отвечает медленно»). Восстановлено вручную: `launch jeff` без `--ensure`, hold снят, Gemma предзагружена `keep_alive=2h`.

## Что меняет ветка
1. **governor, режим `keep`** — умолчание для новых конфигов (`DEFAULT_CONFIG`, `governor.py init`). Jeff не останавливается, hold не пишется, маршрут не переключается, после генерации ничего не восстанавливается. При выгрузке Ollama (ENTER ACTIVE, `ollama_unload_every_tick`) не трогается текущая модель Jeff (`<pit home>\jeff-model.json`, иначе `BOSSMAN_PIT_LOCAL_MODELS` / `local_models` из `config.json` / `bossman-uncensored-chat:latest`) и всё из нового `ollama_keep_models` (в любом режиме). В статусе — `ollama_kept`.
2. **Восстановление при старте в `keep`:** если прошлый запуск оставил свой `pause` или отложенный перезапуск, снимается ТОЛЬКО hold, записанный самим governor (содержимое совпадает), и Jeff поднимается сразу. Чужой или изменённый hold не трогается. Остаток `cloud_free` (флаг) откатывается к локальному Jeff.
3. **`cloud_free` больше не глушит Jeff:** при нарушении стоимости и при отсутствии проверенного маршрута — `keep` (локальный Jeff, модель не выгружается), если RAM позволяет: модель Jeff уже в Ollama или свободно ≥ `keep_min_free_gb` (40 ГБ). Иначе, как раньше, `pause`. `cloud_free_fallback: "pause"` возвращает старое поведение. Неизвестный режим → `pause`.
4. **ENTER ACTIVE:** сначала решение по Jeff, потом выгрузка Ollama — решение `keep` защищает модель, а остановленный/переключённый Jeff не успевает перезагрузить её после выгрузки.
5. **direct-gen:** `_unload_ollama` (после assist и в начале каждой sd-задачи) не выгружает текущую модель Jeff и модели из env `BOSSMAN_OLLAMA_KEEP_MODELS` (`bcc.pit.jeff_model.ollama_keep_models`). Единственное исключение — начало задачи, когда свободной RAM меньше, чем ей нужно (`sdcli.required_bytes`; у resident-движка с уже загруженной моделью — никогда): тогда модель Jeff выгружается, чтобы задача не упала с `insufficient_memory`. Остальные модели-помощники выгружаются как раньше; какую модель берёт assist — не изменено.
6. **governor `jeff_rewarm`:** в `keep`, если модель Jeff выгружена, задача уже `generating`, ни одна не `loading`, Jeff запущен и свободно ≥ `keep_min_free_gb` — модель загружается обратно в фоне (не чаще раза в 120 с, `keep_alive` 2h). В статусе — `jeff_rewarm`.

Тесты: `tools/resource_governor/test_governor.py` и `command-center/tests/test_direct_gen_jeff_keep.py` (+ все `test_direct_gen*.py`, Jeff-тесты); негативные контроли — без фикса новые тесты падают.

## Как выкатить, не прерывая генерации
Только через `origin/main`: PR → обычные ворота → merge (кнопка владельца). Не с этой ветки и не с незапушенных коммитов.

**A. governor — можно во время генерации** (бэкенд и очередь не трогает):
1. В `%LOCALAPPDATA%\Bossman\CommandCenter\one-bossman\governor.json` поставить `"jeff_during_generation": "keep"`. В живом файле явно записан `cloud_free`, новое умолчание его не перекрывает.
2. Взять `governor.py` из `origin/main`, НЕ переключая живой checkout `wt-checkpoint-20261015` (из него работает бэкенд). Например: `git worktree add --detach C:\Users\asd\Bossman\wt-governor-main origin/main` и перенаправить задачу `BossmanOne-6-Governor` на этот путь (в `register_task.ps1` `$repo` задан жёстко — поправить локально или `Set-ScheduledTask` с новым действием).
3. `Stop-ScheduledTask BossmanOne-6-Governor; Start-ScheduledTask BossmanOne-6-Governor`. Governor один на пользователя (`governor.lock`), состояние лежит в `governor-state.json`; в `keep` он при старте сам снимет свой hold и поднимет Jeff.
4. Проверка: `governor.py status` → `jeff_setting: keep`; во время задачи `jeff_mode: keep` и `ollama_kept: ["gemma4-31b-uncensored:latest"]` (или запись в `jeff_rewarm`); в `governor.log` нет `launcher stop jeff`; `hold-jeff` нет; `ollama ps` показывает Gemma во время генерации.

**B. бэкенд (правка direct-gen) — ТОЛЬКО при пустой очереди:** в `GET /api/direct-gen/jobs` нет задач `queued/loading/generating/postprocessing`. Тогда fast-forward checkout бэкенда до `origin/main` и перезапуск через owner launcher. До этого старый бэкенд продолжает выгружать Gemma в начале каждого фото: Jeff (живой благодаря A) отвечает, а `jeff_rewarm` возвращает модель, пока фото генерируется; первый ответ после выгрузки медленнее.

## Правило
Никогда не менять живой checkout и не перезапускать бэкенд, пока идут генерации (14:02: потеряны 4 задачи). Выкатывать только через `origin/main`; незапушенные коммиты на живой машине не запускать. Перезапуск governor допустим, бэкенда — только при пустой очереди.
