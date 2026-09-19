# BACKLOG: 110 ДОПИЛОВ — freeze-20260907-130104

Составлен из результатов 8 параллельных аудитов + живых прогонов на SHA 931584d.
Формат: `[приоритет] ID — что сделать — доказательство (файл:строка / живой тест)`.
P0 = блокирует freeze. P1 = блокирует сценарий приёмки. P2 = качество/скорость/честность UI. P3 = бэклог.

## A. СЕКЬЮРИТИ И СЕКРЕТЫ (10)

- [P0] SEC-001 — Ротировать мастер-пароль vault и вычистить его из git-истории (BFG/rewrite), т.к. remote публичный — `git log -S "SuperSecretMasterPass123!"` (solana_volume_suite, docs/v3)
- [P0] SEC-002 — Убрать plaintext `VAULT_MASTER_PASSWORD` из `solana_volume_suite/.env`, заменить на новый секрет, не совпадающий с утёкшим — `.env:5`
- [P1] SEC-003 — Удалить из репозитория закоммиченные телеметрия-логи (метка ключа, user_id, spend): `docs/acceptance/HAILUO_RESCUE_api_log.jsonl`, SEEDANCE_LIVE_LOG.md
- [P1] SEC-004 — Расширить `tools/ci_secret_scan.py`: скан untracked .env-паттернов, unquoted `PASSWORD=...` (сейчас требуется кавычка, scanner.py:40), убрать self-skip по DICT_HINT (scanner.py:59-60)
- [P1] AP-001 — Убрать data_dir из дефолтных terminal-roots (`features/tools_terminal.py:76`): агент с terminal.run читает `token` и `bcc.db` → self-grant разрешений через `PATCH /api/agents` без approval (api.py:714)
- [P1] AP-005 — Привязать identity согласующего к аутентифицированному principal, а не к строке из тела (`api.py:385-388 ApprovalIn.by`)
- [P2] DO-016 — Шифровать/ограничивать скриншоты рабочего стола (`screenshot.py:6-14` пишет PNG в %TEMP% бессрочно, sensitive=False)
- [P2] AP-004 — Заменить кузнечибельный `_approved_digest` (sha256 от plan-полей, models.py:61-63) на server-issued nonce в apprentice-гейте (`apprentice/engine.py:391-395`)
- [P2] CI-00X — Добавить pre-publish secret-скан в zip/pack-скрипты (untracked-копии уже рематериализуют пароль: `.audit-work/`, `.checkpoint-push-b7aaf43/`)
- [P3] Добавить runtime-алерт при чтении `data/token` процессами агентов (обнаружение exfil-паттерна AP-001)

## B. OPENROUTER / КАТАЛОГ / КРЕДЫ (12)

- [P1] OR-002a — Снять жёсткий кламп 200 в `features/openrouter.py:148`; сделать серверную пагинацию (offset/cursor) + `total`/`has_more` в ответе. Живо: `limit=2000` → 200 из 430
- [P1] OR-002b — UI: бейдж «Показано X из N», search-first UX, сортировка не только alphabetical (z-ai/* невидимы в дефолте; живо: ZAI_IN_DEFAULT200=0)
- [P1] OR-004 — Запретить сохранять ключ/Connect/Sync/Pin провайдеру с kind/base_url ≠ OpenRouter (`features/openrouter.py:80-95`, UI fallthrough `providers[0]` — `ui/pages/openrouter.js:44`); живо воспроизведено
- [P2] OR-001a — Onboarding прямо на странице OpenRouter: preset-кнопка (kind=openrouter, base_url предзаполнен) + возврат на страницу после создания
- [P2] OR-001b — Добавить kind «openrouter» в ADAPTERS (`providers.py:410-413`) вместо ручного ввода base_url
- [P2] OR-003 — Унифицировать резолв креда: vault → `BOSSMAN_OPENROUTER_API_KEY` → `OPENROUTER_API_KEY` (сейчас 3 пути: `plugins.py:98-99` читает только env; `features/openrouter.py:271` другой env)
- [P2] OR-005 — Unique constraint на providers(name, base_url) (`registry.py:44-57`, `db.py:31-39`) + атомарный wizard (createProvider→createModel без orphan-провайдера при 409). Живо: дубли id=3/4 создались
- [P2] OR-006 — Классифицировать 401/403 в sync как «ключ отклонён», а не «OpenRouter недоступен» (`openrouter_catalog_service.py:79-83`, `features/openrouter.py:124-132`)
- [P2] OR-007 — Удалить мёртвый дубль `openrouter_catalog_service.list_catalog/pin` (никем не вызывается; divergent-логика = рассадник регрессий OR-002)
- [P3] REST-тест на идемпотентность double-connect/double-save на уровне endpoint'ов
- [P3] Показывать source каталога в UI (live/cached/stale) рядом с кнопкой Refresh
- [P3] TTL-cache: отдельная ошибка «каталог устарел (возраст X мин)» вместо общего 503

## C. ЧЕСТНОСТЬ РОУТИНГА / НАБЛЮДАЕМОСТЬ (7)

- [P1] MR-001 — Вернуть model_alias/provider в публичный payload run'ов (`_run_public` скрабит; живо: model=null в API при наличии в DB) + поле в UI карточке задачи
- [P1] MR-003 — Заполнять `run_events.data` (сейчас пустые payload'ы): model, provider, latency, tokens за каждый step
- [P1] MR-002b — Отдельный endpoint «полный каталог» (для экспорта/админа) без клампа, с флагом доверия
- [P2] Зафиксировать RESPONSE model id из ответа OpenRouter (сейчас нигде не сохраняется — identity proof только косвенный)
- [P2] Видимый fallback-баннер в UI чата (события есть — surfaced нет)
- [P1] PERF-00X — Добавить `/health` endpoint в BCC (сейчас 404 на /health, /api/health, /healthz; соседние app-манифесты пробуют именно /health — `features/apps.py:88`)
- [P3] Единая конвенция health-проб для всех app-манифестов + тест

## D. DESKTOP OPERATOR (17)

- [P0] DO-001 — Объявить зависимости pywinauto/pyautogui (extras `desktop`) + preflight при старте computer_operator; живо: ModuleNotFoundError, `_input` падает «pyautogui missing» (windows.py:67)
- [P0] DO-017 — Fail-fast при невозможности импорта desktop-бэкенда ДО цикла replan (живо: 21 LLM-вызов сожжён → «planner replan budget»)
- [P0] DO-009 — Перенести FalseCompletion-гейт из apprentice в production manager (`manager.py:100-103` принимает planner COMPLETE безусловно; verifier.py:11 rubber-stamp)
- [P1] DO-002 — Реализовать APP_CLOSE-бэкенд (windows.py:43-45 не поддерживает объявленный capability) + wrong-window guard
- [P1] DO-008 — Включить текст UI-дерева в верификационный blob (сейчас postcondition `contains_text` сравнивает только заголовок окна — `observer.py:15`, `subsystem.py:136`)
- [P1] DO-007 — Добавить OCR/vision-чтение экрана (сейчас пиксели не читаются нигде; Calculator-сценарий невыполним честно)
- [P1] DO-010 — Ре-наблюдение после approval перед исполнением (сейчас исполняется по pre-approval наблюдению, `manager.py:113-140`; окно ожидания может быть часами)
- [P1] DO-011 — Грyoундинг координат: bounding rect'ы в UI-дереве + проверка self-claimed `source:"vision"` (policy.py:42 доверяет self-report)
- [P1] DO-014 — Подключить durable SideEffectLedger к production manager (есть в apprentice `guards.py:114-137`, не подключён; retry/replan дублирует эффекты)
- [P2] DO-015 — One-time consume для manager-approvals + проводка server-issued nonce (approve остаётся навсегда `approved`)
- [P2] DO-003 — APP_LAUNCH с аргументами (открыть сохранённый файл по пути; сейчас argv фиксирован `app_launch.py:53-55`)
- [P2] DO-004 — Правый клик / контекстное меню + десктопное создание папки (нет в ActionKind; только хрупкий UI_INVOKE диалога)
- [P2] DO-005 — Ввод Unicode/кириллицы через clipboard-fallback (`pyautogui.write` ненадёжен, windows.py:71)
- [P2] DO-012 — Path-scoped AUTO/ASK политика для desktop-действий (сейчас policy.py:60 — всё разрешено)
- [P2] DO-013 — Не предлагать unbacked kinds при degrade-open (`planner.py:31-42` повторно предлагает APP_CLOSE и др.)
- [P3] Локало-независимый матч заголовков окон (title-contains на локализованных Windows)
- [P1] Тесты: e2e Notepad (type→save-as→close→reopen) и Calculator-read — сейчас 0 таких тестов в репо

## E. APPROVALS / ЖИЗНЕННЫЙ ЦИКЛ (14)

- [P0] AP-002 — Claim-before-effect для bcc tool-call'ов (крэш между эффектом и записью receipt → повторное исполнение approved нон-идемпотентного вызова на рестарте; `engine.py:1024-1027,1094-1098`)
- [P1] AP-003 — Вызывать consume() approval на agent-пути (сейчас только owner-direct browser/terminal)
- [P2] AP-006 — `on_approval_decided`/`resume()` не должны воскрешать stopped/paused задачу (`engine.py:1142-1156,290-305`)
- [P2] AP-007 — Речeck статуса миссии внутри dispatch-тика (гонка pause vs enqueue, `missions.py:137-159`)
- [P2] AP-008 — Stop/Pause для core V2 runner (сначала: как только задача запущена — остановить нельзя до исчерпания бюджета, `runner.py:368-439`)
- [P2] AP-009 — Долговечный WAIT_APPROVAL у apprentice (+реконсиляция выданных nonce после рестарта)
- [P2] AP-010 — Resume из LAST_VERIFIED_STATE (сейчас replay транскрипта модели в bcc; V3 journal стек критерий выполняет — переиспользовать)
- [P2] AP-011 — Требовать declared effects либо авто-регистрацию side-effect ожиданий (сейчас «NOT_REQUIRED» если мета не заполнена — `finalize.py:70-72`)
- [P2] AP-012 — Audit-событие при изменении permissions агента (`api.py:714-724` молча)
- [P2] Approval TTL/expiry в bcc (approved-записи живут вечно)
- [P2] Реконсиляция «already-dispatched ambiguous effect» после Stop (не replay вслепую)
- [P3] Durable Take-Control для терминальных задач (сейчас только browser-сессии, in-memory)
- [P3] UI-дедуп кнопки Pause/Stop (защита от двойного нажатия на slow-соединении)
- [P3] Метрика «approval lifetime» (создан → решение) в аналитике панели

## F. VIDEO (9)

- [P1] VS-001 — Смержить codex/video-studio в интеграционную ветку (или честно пометить absent; сейчас app в другой топологии)
- [P1] VS-002 — Состояние BLOCKED_FFMPEG + preflight-гейтинг export/preview (сейчас jobs принимаются и навсегда `queued` при отсутствии ffmpeg — `subsystem.py:32-37`, `routes.py:34-40`)
- [P2] VS-003 — Персистентная очередь или re-enqueue QUEUED jobs на старте (сейчас in-memory asyncio.Queue теряет задачи при рестарте)
- [P2] VS-004 — Компенсация при QueueFull (удалить/пометить zombie `queued` job)
- [P2] VS-005 — API resume/retry для INTERRUPTED/FAILED (механизм pipeline есть — endpoint нет)
- [P2] VS-006 — Full-decode верификация + сверка duration с запросом (сейчас только probe metadata; сцена 1с пройдёт как 5с)
- [P3] VS-008 — Починить contract verify_output (тест передаёт `duration`, код понимает только `duration_ticks`)
- [P3] VS-009 — Убрать всегда-`failed` статус в gate-ответах codex-ветки
- [P3] VS-010 — Дизейбл кнопок Export/Preview при отсутствии ffmpeg (UI-гейтинг capabilities)

## G. WEB DESIGNER (12)

- [P1] WD-001 — Починить «+ Проект»: state.id репопуляется из projects[0] в render() (`web_designer.js:567-572`) — форма создания недостижима при ≥1 проекте. Живо-подтверждено кодом
- [P1] WD-002 — Сохранять SVG case-sensitive атрибуты/теги (viewBox→viewbox ломает рендер; `web_designer_dom.py:74-76`; каждая правка переписывает весь inline SVG)
- [P2] WD-003 — Сохранять PI и CDATA секции (сейчас молча выбрасываются)
- [P2] WD-004 — Quote-aware парсер inline-стилей (сейчас split(";") рвёт значения с `;` внутри кавычек)
- [P2] WD-005 — Атомарные сохранения проекта (tmp+replace+fsync; сейчас 3 последовательных write_text)
- [P2] WD-006 — Глобально уникальные id проектов (сейчас max+1 после delete → коллизии viewport-состояний)
- [P3] WD-007 — Sanity-чек по всему документу, не первым 2000 символам
- [P3] WD-008 — Flush autosave на beforeunload/pagehide (окно 900мс теряет правки)
- [P3] WD-009 — Серверный export endpoint с артефактом и верификацией (сейчас клиентский Blob из живого буфера редактора)
- [P3] WD-010 — Кнопка удаления проекта в UI (API есть, кнопки нет)
- [P3] Исправить вводящую в заблуждение метку кнопки («Открыть проект» выполняет create — `web_designer.js:507-518`)
- [P2] Регрессион-тест DOM round-trip с inline SVG + Unicode (сейчас тест не покрывает SVG)

## H. V5 / LEARNING (17)

- [P0] V5-001 — Смержить V5 objective-слой (branch-only сейчас) или честно задокументировать отсутствие; в worktree `bossman/core/` = только мёртвый bytecode
- [P1] V5-002 — Удалять fleet_memory_reservations при release/expiry/reclaim (DELETE отсутствует нигде — вечная утечка)
- [P1] V5-003 — Fence/ownership-проверка на lease release (сейчас удаляет по lease_id без проверки владельца, `leases.py:95-96`)
- [P1] V5-004 — Провеpять provenance owner_approved/rollback_tested (caller-supplied booleans → durable evidence)
- [P1] V5-005 — Holdout + same-task-set pairing в skill_evaluation промоушене (сейчас N=5, observational, без canary)
- [P2] V5-006 — Исполняемый rollback-путь для промоушенов (сейчас статус-флаг без исполнения)
- [P2] V5-007 — Canary cohort state machine (PENDING/FAILED/PASSED + блок broad release) — в worktree отсутствует
- [P2] V5-008 — Включить EVIDENCE_TTL_S (сейчас dead code — 2-летние наблюдения считаются свежими)
- [P2] V5-009 — Реестр использованных nonce для signed evidence (сейчас replay возможен)
- [P2] V5-010 — CAS-гард переходов статусов миссий (stop может быть перезаписан completed)
- [P3] V5-011 — Аккумулировать ошибки валидации evidence (сейчас first-error-only)
- [P2] LG-001 — Подключить guard_promotion в production-флоу (сейчас UNWIRED; только holdout-эксклюзия живая)
- [P2] LG-002 — Автоматический rollback-on-degradation монитор/исполнитель (сейчас manual)
- [P2] LG-003 — Настоящая соль для holdout-хешей (сейчас константный префикс `bossman-holdout:` — dictionary-probing)
- [P3] LG-004 — Lock-safe запись эпизодов тренера через публичный API store (сейчас private `_append_atomic`)
- [P3] LG-006 — Вынести reuse-gate флаг-чек с горячего пути cache.get()
- [P3] V5-012 — Удалить мёртвый bytecode `bossman/core/__pycache__` (вводит аудиторов в заблуждение)

## I. HERMES / FUSION / ЧЕСТНОСТЬ ФИЧЕЙ (6)

- [P1] HM-001 — Реализовать Hermes (за default-OFF флагом) поверх learning_guard: DRAFT→SANDBOX→MEASURED→SECURITY→CANARY→PROMOTED; сейчас НЕ СУЩЕСТВУЕТ (grep=0 по всему репо включая zip'ы)
- [P1] FU-001 — Реализовать Fusion (multi-model синтез за флагом) с per-participant provider/model/latency/tokens/cost телеметрией; сейчас НЕ СУЩЕСТВУЕТ
- [P3] Удалить/протестировать мёртвый `bossman_v3/visual_state/fusion.py` (0% coverage, 0 импортов, имя конфликтует с будущей фичей)
- [P0] LG-005 — Удалить пустой пакет `bossman/cognitive/` (только stale .pyc) — documented ModuleNotFoundError-ландмин
- [P2] Пометить в README Hermes/Fusion как «не реализовано» до фактической сборки (честность фасада)
- [P3] Запретить имя `fusion` для не-LLM модулей (gitgrep-гейт в CI)

## J. ПРОИЗВОДИТЕЛЬНОСТЬ (10)

- [P1] PERF-001 — nvidia-smi в executor-поток (сейчас subprocess.run в event loop, до 5с блокировки; `metrics.py:57,115` + `api.py:588`); живо: /api/apps p95=9578мс
- [P1] PERF-002 — Асинхронное/потайловое чтение testing-лога (сейчас sync read_text до 32МБ в request-пути, p50=276мс; `testing_period.py:198`)
- [P2] PERF-004 — Убрать N+1 в /api/tasks (join last_run; `api.py:751-754`)
- [P2] PERF-005 — Cache-Control: max-age/immutable для static-ассетов (сейчас только ETag → рефетч каждого файла)
- [P2] Neighbor-app health-пробы в background-refresh, не в request-пути (`features/apps.py:175-196`)
- [P2] PERF-003 — Сократить латентность тривиальной core-задачи (живо: 77.6с; шторм health-проб/ретраев гейта)
- [P3] Payload-капы для /api/capabilities и /api/system (43KB/38KB сейчас — ок, но без пагинации растут)
- [P3] Perf-бюджеты в CI (p50-пороги на mock-endpoints)
- [P3] Кэшировать результат identify_server в desktop.py (сейчас повторные HTTP-пробы при каждом запуске)
- [P3] Сжатие (gzip/brotli) для /api/testing/events (84KB ответ)

## K. CI / РЕПО-ГИГИЕНА (12)

- [P2] CI-001 — Дедупликация полного core- Suite (запускается 2 раза на push)
- [P2] CI-002 — `permissions:` + `concurrency:` в bossman-v2-repair.yml (auto-PR на failure с дефолт-токеном)
- [P2] CI-003 — concurrency-группы в root-ci/astra-acceptance/solana-safety/benchmark
- [P2] CI-004 — Fallback + existence-чек для pinned SHA fetch (`git fetch origin 8a13f1d...` без проверки)
- [P3] CI-005 — Убрать overlap solana-safety vs root-ci (test_solana_safety.py в обоих)
- [P1] RH-001 — `git rm --cached` 19 корневых zip/png (~6+МБ бинарных паков в каждом клоне)
- [P3] RH-002 — Перенести 14 root-статусных .md в docs/
- [P1] RH-003 — Удалить `.audit-work/`, `.checkpoint-push-b7aaf43/` (stale полные копии репо с паролем на диске)
- [P2] DEP-001 — Пины версий для solana_volume_suite + добавление в dependabot/pip-audit scope
- [P3] Добавить lockfiles (requirements.lock/uv.lock) для воспроизводимости core/cc
- [P3] CI-джоб на отсутствие пустых пакетов (bossman/cognitive case) и мёртвого bytecode
- [P3] Автопроверка «README-фичи существуют в коде» (grep-гейт против HM-001/FU-001 класса)

## L. ОКРУЖЕНИЕ / СБОРКА (10)

- [P1] ENV-001 — Объявить bossman-shared в зависимостях bossman-core (`toolkit/net.py:38` импортит, pyproject не знает → core не стартует из своей директории; живо: ModuleNotFoundError)
- [P1] ENV-002 — Документировать/поставлять pgvector (compose с образом pgvector/pgvector; живо: plain postgres:16 → DEPENDENCY_UNAVAILABLE при старте)
- [P1] ENV-003 — Починить doc-code mismatch cloud_policy: README «never/ask/allow», код требует `allowed` (`agents.py:13`); живо: task → 500 ValueError
- [P2] ENV-004 — Задокументировать REDIS_URL для не-docker хоста + fail-fast с понятным сообщением (сейчас getaddrinfo failed «redis:6379»)
- [P2] ENV-005 — Задокументировать BOSSMAN_GATEWAY_URL с `/v1` либо авто-дополнение в клиенте (`client.py:55` vs gateway route `/v1/chat/completions`; живо: 404 на каждый чат)
- [P1] Core README quickstart: pgvector + redis + gateway + bootstrap device end-to-end (сейчас 5 последовательных landmin)
- [P3] bootstrap_remote_device.py как console-script (без PYTHONPATH-хака)
- [P2] PR-002 — Строгая валидация PATCH /profiles/{id}/toggles (плоский body молча no-op — вернуть 422)
- [P3] Единый dev-стек скрипт (postgres+redis+gateway+core+cc одной командой с health-чеком)
- [P3] Self-check команда `bossman doctor`: deps, DB, redis, gateway, ключи, порты

## M. МОДЕЛИ / ИМИДЖ / ПРОЧЕЕ (6)

- [P1] IMGEN-001 — Реализовать реальный image-provider контракт (OpenRouter image-модели / локальный SD) за тем же ImageProvider протоколом; сейчас только мок (`images.py:35,519-525`); живо: job с реальной моделью → failed
- [P2] IMGEN-002 — UI: честная метка «mock» у мок-модели + скрыть/дизейбл нереализованные
- [P2] Починить mojibake в русских строках/метаданных (pyproject description и ряд лог-строк — битая кодировка в исходниках)
- [P3] Секвенс-номера RunId в отчётах об ошибках задач (сейчас traceback в result сырой)
- [P3] Единый формат ошибок core API (сейчас mix: {error:{code}}, {detail}, 500 без тела)
- [P3] Ретраи 429/5xx в GatewayClient с jitter-бэкоффом видимые в events

---
Итого: 110 пунктов. P0=12, P1=32, P2=44, P3=22. Приоритетный путь к freeze: SEC-001/002 → DO-001/017/009 → OR-002/004 → MR-001 → ENV-001..005 → VS-001 → WD-001/002.

---

## N. ДОПИЛЫ ИЗ ЖИВОЙ СЕССИИ ВЛАДЕЛЬЦА (2026-09-07, сессия 6cbb17ce84db, +14 пунктов)

Источник: журнал тестового периода (6277 записей; ui.dead_click=40, ui.refused=71, ui.rage_click=2, http.error=31).
Фидбек владельца: «много чего не работает, много где дизайн не нравится».

### Конкретно не работает (объективные сигналы)

- [P1] UX-001 — Кнопка «Отправить в GitHub» (bcc-testing-publish) мертва на 7 страницах (18 мёртвых кликов: missions, tasks, web_research, openrouter, apps, home-v2, governor) — либо починить глобально, либо давать видимый ответ/ошибку при клике
- [P1] UX-002 — Кнопка «▶» (Play) в Video Studio мертва: 6 мёртвых кликов + rage-click (3 удара за 2с) — превью не запускается
- [P1] UX-003 — Мастер моделей: «Далее» мертва (4 клика) и «Новый провайдер» мертва (3 клика) на #/models — ключевой онбординг-флоу сломан в живой сессии
- [P1] UX-004 — Web Designer ai-edit: POST /api/web-designer/projects/1/ai-edit → 502 ×2 (модель для AI-правки не настроена) — фича видна в UI, но не работает
- [P1] UX-005 — Video Studio: media thumbnail/waveform → 409 ×4 — таймлайн показывает заглушки вместо превью
- [P1] UX-006 — 15× refused GET /api/system + 30+ refused других API со status=0 из UI-поллинга — совпадает с блокировкой event loop (PERF-001: sync nvidia-smi до 5с) → дашборд «замирает» без индикации
- [P2] UX-007 — Owner снова жал Connect на провайдере Ollama (POST /api/openrouter/1/connect → 502 ×6) — ловушка OR-004 реально ловит пользователя
- [P2] UX-008 — Кнопка «Все приложения» — rage-click; busy-состояния нет
- [P2] UX-009 — Кнопка «Закрыть» (#think-close) на trading_lab мертва — модалка не закрывается

### Дизайн (фидбек владельца: «дизайн не нравится» — системный полиш)

- [P2] UX-010 — Единый дизайн-проход по всем страницам: сетка, отступы, типографика, иконки; страницы визуально разнородные (home-v2 vs tasks vs video-studio)
- [P2] UX-011 — Состояния загрузки/ошибок для всех API-вызовов: при refused/dead-запросах интерфейс выглядит замершим (нет скелетонов/тостов) — главная причина ощущения «не работает»
- [P2] UX-012 — Video Studio: редизайн таймлайна/превью (пустые состояния, прогресс рендера, статус 409 показывать как «медиа обрабатывается»)
- [P2] UX-013 — Страница Моделей: разделить «Локальные»/«Облачные», убрать ловушку OR-004 (отдельная OpenRouter-секция с явным CTA)
- [P3] UX-014 — Empty-states с подсказкой действия на всех списках (миссии/задачи/провайдеры)

Итого с разделом N: 124 пункта (P0=12, P1=39, P2=50, P3=23).
