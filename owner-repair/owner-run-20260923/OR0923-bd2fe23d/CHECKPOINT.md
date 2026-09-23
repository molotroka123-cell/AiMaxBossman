# OWNER RUN 2026-09-23 — чекпоинт (RUN_ID OR0923-bd2fe23d)

Terminal Run 1.2 = другой пульт того же Bossman (общие файлы/память/задачи); цель — измеряемое самоулучшение и бизнес-пилот в окне 4–6 дней, не гарантированный заработок.
North Star: SELF_IMPROVEMENT_INFRASTRUCTURE_PRESENT (выше — не доказано в этом прогоне пока).

## Код и архив
- SOURCE_BRANCH `integrate/owner-final-20260922`, TESTED_SHA `bd2fe23dab55704d60ed468bcbb4664475e49879` (дерево = 3707d6ab, содержит PR #74; `release/bossman-owner` e0bf948d — предок, отстаёт).
- PACKAGING_BLOCKER: в GitHub Actions нет Windows ZIP ни для bd2fe23d, ни для 3707d6ab (последний — 68dded39). Собран локально штатным `tools/build_windows_bundle.py --zip --profile release` из чистого LF-клона: inputs LOCKED, `verify_windows_bundle.py` → `BOSSMAN_BUNDLE_ACCEPTANCE=PASS`.
- ZIP `BOSSMAN-Windows-x64-bd2fe23dab55.zip` SHA-256 `95476d4a560d9f347ca4797b75475dbd9e20c52417b209701f7692bf6ab3528c` (локальная сборка, не CI-сертификат).

## Среда
- Ryzen AI MAX+ 395 / Radeon 8060S / 119.6 GB. Сессия Claude Code и её дочерние процессы — с правами администратора.
- **ENVIRONMENT_BLOCKER: Smart App Control = On** блокирует неподписанные llama.cpp DLL (`0xC0E90002`). SAC не отключался (необратимо). Runtime заменён на подписанный Ollama 0.34.3 (отдельный экземпляр :11435, keep_alive=-1) + прокси :11500 `reasoning_effort=none` (аналог `--reasoning off`). Веса те же GGUF (импорт из локальных файлов, без загрузки).
- MAIN Qwen3.8-27B UD-Q5_K_M ctx 64k (~10 tok/s), FAST Qwen3.6-35B-A3B UD-Q5_K_M ctx 32k.
- Тестовая установка `C:\Users\asd\Bossman Test 0923\` (путь с пробелами), data root `...\data`, порт 8810. Рабочая установка/БД владельца не тронуты.

## Результаты на сейчас (факты)
- UI: вход, «Найти локальные» нашёл Ollama, модели и агенты созданы в окне; сразу видны в CLI.
- CLI из архива: help/status/list/exec/events/result/stop работают; stdout без ANSI и без токена.
- TR-10: повтор request_id → та же задача (replayed), replay по курсору без дублей; обрыв клиента → задача доживает, `events --follow` дочитывает.
- TR-11: закрытие окна Windows Terminal посреди задачи → задача завершилась в backend.
- TR-12: `stop` → STOPPED (exit 6); `stop --all` останавливает задачи и Computer Use; STOP Computer Use пережил рестарт backend; ожидавший approval аннулирован.
- TR-13: `approve` из окна учителя отклонён (NO_APPROVE); approval виден в UI «Ждут решения». Положительный approval — NOT_RUN (нужно решение владельца).
- TR-14: ESC/OSC52/BEL/RLO/CR в заголовке и ответе не доходят до stdout (JSON и текст).
- TR-20: мёртвая модель → повторы → подмена на другую локальную модель → PASS; облачная модель без цены заблокирована, с ценой — не выбрана.
- ConHost + Windows Terminal: кириллица без mojibake, Tab-дополнение, Ctrl+C = отмена с подтверждением backend, многострочная вставка правым кликом не исполняется построчно, resume сессии.

## Найденные дефекты (черновик)
- P1 чат: перенесённый контекст «Запомни …» на каждом следующем ходе включает action-contract → верный ответ = FAIL + новый approval memory.fact.add.
- P2: Ctrl+V не вставляет, Shift+Insert вставляет `[2;2~` в чате ConHost; после Ctrl+C `/exit` показывает «Terminate batch job (Y/N)?»; prompt чата выглядит как prompt cmd; `resume` не восстанавливает агента; 10 строк «проверка: NOT_APPLICABLE».
- P2: агент из UI получает `tools=[]` без способа выдать инструменты в UI/CLI; coding path настраивается только env `BOSSMAN_OPENHANDS_COMMAND` + рестарт; coding-задачи не применяются к проекту (нет apply) → общий файл CLI→UI через продукт не создаётся.
- P2: UI позволил удалить модель, используемую агентом, — агент молча остался без модели.
- P2: двухфазный submit — обрыв между draft и run оставляет draft до повторного подключения.
- P2 упаковка: .cmd в архиве с окончаниями CR CR LF (работают, но это ошибка генерации).
- Документация: TERMINAL.md говорит, что prompt_toolkit нет в архиве — он есть (3.0.52).
- `/api/evolution/status` есть и отвечает (пробел из handoff #74 закрыт как минимум для status).

## Дальше
Coaching-пилот 5 train + 5 holdout на MAIN идёт; затем coding self-repair через CLI, рестарт + transfer, GUI-vs-CLI пары, MVČR (синтетика), медиа.

---
# Чекпоинт 2 (~14:50Z)

## Coding path / самоулучшение — блокеры продукта
- **P1 CODING-CRLF-EVIDENCE (продукт):** на Windows с `core.autocrlf=true` (умолчание Git for Windows) coding path отказывает нетронутому репозиторию Bossman: «evidence mismatch: git hides changes» (CSV с CRLF в блобе). Регрессия падает до / проходит после; fix `fix/coding-path-crlf-blob-20260923` @7439917f (bossman-core/tests/apprentice: 132 passed, 18 skipped). В TESTED_SHA НЕ влит; на прогоне — обход окружения `GIT_CONFIG core.autocrlf=false`.
- **P1 функциональный пробел:** репозиторий Bossman 80 МБ > `_MAX_SNAPSHOT_BYTES` 32 МБ → «workspace evidence exceeds bounded snapshot size». «Bossman улучшает Bossman» на полном репо невозможно; для эксперимента — ограниченная lab-копия `tools/` того же SHA (отклонение записано в BENCHMARK_MANIFEST до действий ученика).
- P2: `bossman code` не принимает инструкцию из файла; перевод строки в аргументе обрезает вызов через bossman.cmd.

## Обучение
- Coaching-пакет 5+5 на MAIN: 10/10 без помощи, coached = unassisted = 1.0 → пакет насыщен, **NO_MEASURED_GAIN** (потолок), WEIGHTS_UNCHANGED.
- D1 (реальный дефект CR CR LF в .cmd архива), экзамен заморожен до попыток (hv_d1, D2 unseen запечатаны вне корней ученика).
  - попытка 1 L0: FAIL (max_steps 40, 7 мин) — цикл одинаковых поисков;
  - попытка 2 L1: FAIL (max_steps, 5.5 мин) — нашёл строку причины поиском, не воспользовался; 23 одинаковых вызова;
  - наблюдение: в local_sidecar нет детектора повторов.

## MVČR (HW-10) — синтетика + живые официальные источники
- Owner-Run.cmd self-improve-mvcr mvcr: GET только mv.gov.cz / ipc.gov.cz; форма «Tiskopis žádosti o vydání povolení k trvalému pobytu – občané 3. zemí» с provenance/sha256; хронология только из подтверждённых фактов (7 л. 6 мес.); разные суммы пошлины → вопрос владельцу, не выбор; итог PARTIAL_MISSING_DATA; ничего не подано/не подписано/не оплачено.

## Web Designer (UX в окне)
- заготовка → проект v1 → правка кода (маркер) → Ctrl+S/автосохранение → перезагрузка → маркер на месте, v2/v3 → «Вернуть v1» с подтверждением → v4 = v1, сохраняется после перезагрузки. P2: панель истории не обновляется до перезагрузки.

---
# Чекпоинт 3 (~15:40Z) — после коррекции владельца

Метки: RUNTIME=OLLAMA_PROXY · ADMIN_RUN · одобрения в тестовой среде = DELEGATED_BY_OWNER (сообщение владельца «разрешай всё сам»). Полная identity: `RUNTIME_IDENTITY.json` (SHA1) и `learning/RUNTIME_IDENTITY_SHA2.json` (build/installed SHA, sha256 GGUF обеих моделей = Ollama blob, Ollama 0.34.3, прокси, контексты, параметры sidecar, агенты, хэши политики/навыков). `git_head: unknown` в manifest coaching-раннера — evidence-layer дополнен этим файлом (это НЕ исправление продукта).

## Новый кандидат SHA2 = d53f3b1219224a3bfc73af25f70c67163224e8bc (ветка fix/owner-run-20260923-p1)
ZIP `BOSSMAN-Windows-x64-d53f3b121922.zip` SHA-256 `2edb42768a928e14276ef9eb86035bcf38f61bd6de98f50b6e400cc292d82007`, `BOSSMAN_BUNDLE_ACCEPTANCE=PASS`, установлен отдельно (`...\sha2\`), тот же data root. Три фикса (каждый: воспроизведение → падающая регрессия → минимальный fix → соседи):
1. **P1 CHAT-CONTEXT-REQUEST** (репро: задачи 10, 11, 12 после «Запомни…» в 9). Граница: CLI-чат вкладывает прошлые ходы в текст промпта; backend `action_contract`/`action_router` классифицировали весь текст. Fix: общий формат `bcc.conversation_context`, проверки читают только новое сообщение и только если промпт НАЧИНАЕТСЯ с заголовка терминала. Регрессия 2 fail → 4 pass; соседи 132 + e2e 11 + approval/memory 136 passed. **Живая проверка на SHA2: задача 22 — «Кодовое слово: Сазава, 6*7=42», PASS, без нового approval.**
2. **P1 CODING-CRLF-EVIDENCE** (см. чекпоинт 2).
3. **Sidecar: молчаливое обрезание результата инструмента до 6000 символов** — ученик видел первые ~150 строк 55-КБ файла без пометки и ходил по кругу. Теперь явная пометка «ОБРЕЗАНО … read_file start_line/end_line». Регрессия fail → pass; apprentice 133 passed/18 skipped.
Попытки D1 1–4 (L0–L3) на SHA1 = **pre-fix, confounded by ORCHESTRATION** — не засчитываются как провал модели.

## Одобрения владельца (делегировано)
- HW-03: скачивание PDF — одобрено в UI → `dummy.pdf` 13 264 байта, `%PDF-1.4`, sha256 `3df79d34…adb4` сверен независимо. Отказ повторной загрузки → второго файла нет.
- **P1 новый — ложный успех:** «Скачай PDF-файл https://…» (без слова «браузер») → роутер не распознал действие, модель ответила «нет инструментов», задача 18 = **PASS**.
- **P2:** после успешного скачивания browser-evidence ждёт URL сайта (страница about:blank) → FAIL без причины → задача 19 снова и снова просит скачать (в т.ч. после рестарта, с пустыми аргументами) — остановлена вручную.
- Память: в чистом data root не настроена (memory/stats 503, «задайте Obsidian vault») — настроено в UI «Локальные инструменты». `memory.fact.add` одобрен → fact#1 сохранён; задача 23 всё равно FAIL «effectful tool did not succeed» из-за первой неудачной попытки (P2). Модель выдумала `valid_at 2024-09-23` (качество модели).
- **Память после рестарта ✔:** задача 24 после перезапуска backend — «память: 1 записей», ответ «Бероунка», PASS.
- Одобрение из UI видно в CLI-чате («✓ разрешение #13: одобрено (ui)»).

## Жизненный цикл
- **P2:** закрытие окна во время coding-задачи: старый backend (PID 9248) освободил порт, но жил ~8 мин параллельно с новым на том же data root, пока sidecar не закончил. Новый backend корректно пометил задачу «прервана перезапуском; исход неизвестен, повтор — решение владельца»; перезаписи не было.
- STOP Computer Use сохранился и после обновления на SHA2.

## OpenRouter / Jev
- Ключ OpenRouter от владельца сохранён штатно (`keys set openrouter --stdin`, зашифрован, маска …1b52; в доказательствах ключа нет). 457 моделей в каталоге. Повтор TR-20: мёртвая локальная модель → лестница выбрала локальную, НЕ облачную.
- Jev: нужен ключ TypeSafe/DefAPI; selftest обвязки PASS; живой прогон OWNER_REQUIRED.

## Прочее
- Настройки Telegram тестового экземпляра показывают значения общей конфигурации компаньона пользователя (не data root) — вопрос изоляции (P2).

---
# Чекпоинт 4 (~16:45Z) — D1 post-fix, restart, D2, Jev, медиа

TESTED_SHA (текущий кандидат): **SHA3 `cdb4b09db49aebac46bea2f3b98ef143d4a343d6`** = SHA2 `d53f3b12` + NO_PROGRESS-детектор (требование владельца). Branch `fix/owner-run-20260923-p1`. ZIP SHA3 `484590c791fe0ffe1e494e98083e3650d11e7100440b272e13830921b73b8bc8` (bundle acceptance PASS). ZIP SHA2 `2edb42768a928e14276ef9eb86035bcf38f61bd6de98f50b6e400cc292d82007`. PRE-FIX baseline = bd2fe23d.
RUNTIME=OLLAMA_PROXY · PRIVILEGE=ADMIN_RUN · MODEL MAIN Qwen3.8-27B Q5 (gguf 2de73110…), FAST Qwen3.6-35B-A3B Q5 (c13ce262…) · identity: `learning/identity-*.json`, `RUNTIME_IDENTITY*.json`.

## D1 (SHA3) — STUDENT_COACHED_PASS на L4
L0/L1/L2 FAIL (no_progress_loop, 22–34 шага), L3 FAIL (max_steps, без петли), **L4 PASS**: diff ученика применяется, hv_d1 PASS на кандидате / FAIL на базе, регрессия ученика падает на базе и проходит на кандидате. Отрицательный контроль (LF-only patch) — REJECTED. Причина неудач L0–L3: MODEL (не переходит от поиска к правке, игнорирует предупреждения) + TOOL (regex `\r`). Ложный FAIL L4 сначала был HARNESS (встроенный Python не видит cwd) — исправлено.
**North Star: SELF_REPAIR_SINGLE_CYCLE_PASS (coached, L4)** — Qwen сделал фикс, скрытый verifier подтвердил, teacher patch не использовался.

## Урок и перезапуск
`POST /api/coding-recipes` → VERIFIED `coach-lesson:29a7424bd33b115b` (recipe `win-double-crlf-text-mode-write`), verifier — внешний инструмент со своим run id. Полный рестарт: PID 9780 → 15556, started_at 16:16:24Z; урок на месте.

## D2 unseen (раскрыт 16:21Z, после рестарта, без подсказок)
RAW (no memory): STUDENT_UNASSISTED_PASS, 7 шагов, 121.6 с. LESSON_AVAILABLE: урок извлечён и применён, STUDENT_UNASSISTED_PASS, 8 шагов, 149.6 с. **TRANSFER: NO_MEASURED_GAIN** (D2 решается и без урока; n=1).
P2 продукт: при autocrlf=true evidence-diff содержит CRLF-контекст рабочей копии и не применяется к LF-репозиторию.

## P1-соседи после рестарта (SHA3, живой чат)
запомни → вопрос: PASS без approval (задачи 35/36); браузер → вопрос: PASS (37 с проверенным browser evidence / 38); approval → вопрос: PASS. CRLF-фикс при обычном Git (без обхода): PASS (coding task e2f63e6235f9).

## Облако и Jev
Лимит $3/день включён (BOSSMAN_SPEND_METER_ENABLED + /api/spend/limit). Jev через OpenRouter System One API: контракт VERIFIED, 6/6 функц., 4/4 защитных, согласие 18/18, ≈$0.00084 → CONTRACT_VERIFIED + SHADOW_PASS; phase 2 INSUFFICIENT_EVIDENCE.

## GUI vs CLI (SHA2, n=3, короткие задачи)
MEASURED_OVERHEAD_REDUCTION (пилот): CLI 1 действие vs 9–10, контекст 3.9 КБ vs 22 КБ, overhead клиента 1.7 с vs 3.7 с. Пары A/B/C (файл/код/браузер) на SHA3 — в работе.

## Медиа
- Image Studio через ComfyUI: **ENVIRONMENT_BLOCKER** (SAC блокирует scipy `_nd_image.pyd`).
- sd.cpp (штатный провайдер Студии): изображение Z-Image-Turbo 768×768 PNG, полное декодирование, sha256 4b9d6247…, provenance (Vulkan observed, sha256 бинарника и весов совпали). P2: UI «Студии» не показывает sd.cpp-модели (есть только в /api/studio/models) → запуск через API; P2: запись задачи показывает дефолтные 1024×1024/30 шагов.
- Видео TestRun (Wan2.2 TI2V-5B, пресет test_1s) — идёт.

## Прочее
- Ярлык «Bossman CMD» на рабочем столе (Windows Terminal, без прав администратора) — CLI тестового экземпляра.
- Ветка `feat/cli-claude-parity-20260923` @12612c81: /compact /context /cost(/usage) /export /doctor /permissions (42 теста + e2e) — ещё не собрана.
- Video Studio: при первом переходе вечный скелетон до перезагрузки (P2).

---
# Чекпоинт 5 (~17:10Z) — финал сессии

- **Медиа sd.cpp:** видео Wan2.2 test_1s — h264 640×352, 17 кадров, 16 fps, 1.06 с, полное декодирование, Vulkan observed; cancel → sd-cli завершён ≤2 с, без результата; жёсткое падение backend → sd-cli не осиротел (Job Object), задача `interrupted_unknown`, retry → 409 (без слепого повтора). P2: закрытие окна во время медиазадачи оставляет backend работать (окно подключается к нему же).
- **GUI vs CLI (SHA3, 2 раунда):** A память/задача 4.1 → 1.8 с, C браузер 6.5 → 3.4 с (MEASURED_CLI_OVERHEAD_REDUCTION); B coding 21.8 vs 26.9 с (NO_MEASURED_GAIN, но 17 → 1 действие учителя). Ошибка harness первого прохода (старая карточка) исправлена, перемерено.
- **STANDARD_USER_RUN smoke** (Medium S-1-16-8192, backend из Проводника): status, кириллица «Прага», coding PASS, запись в data root, approval → dummy.pdf 13 264 байт sha256 3df79d34…, STOP — все PASS; отличий от ADMIN_RUN нет.
- **SHA4 `12612c8184a19dd477e09c60bdaa9a0d2eea21cd`** (feat/cli-claude-parity-20260923 = SHA3 + CLI): ZIP `012ceb838c2c39b5b07d233904da1398c4a648efa92c3d60fb4decc4fb4bb235`, bundle acceptance PASS; вживую в «Bossman CMD» (без админа): /doctor, /context, /cost (цена «—» для локальной), /compact (задача #62, 54 с), /export — работают. P2: /compact на короткой беседе увеличил контекст 220 → 2037 символов.
- Отчёты: REPORT_RU.md, BUGS.md, TR_MATRIX.md, HW_MATRIX.md, UX_CLI_PARITY.md, MODEL_AND_LEARNING_RESULTS.json, GUI_VS_CLI_MEASUREMENTS.json, RUN_MANIFEST.json, CONTINUE.md.
