# Bossman 2.5 — пятнадцать направлений как один продукт

Дата снимка: 2026-10-18
Проверено на: `fb58317fbeea48045bb1ceb78730ce9b9f6cface` (`origin/main`, коммит от 2026-10-18 10:10 -0700)
Статус документа: сверка кода и запущенных сейчас тестов; **не сертификат, не приёмка, не owner-PC proof**.
Связанные документы: [`BOSSMAN_2_5_DESIGN_AND_OSS_RU.md`](BOSSMAN_2_5_DESIGN_AND_OSS_RU.md), [`MASTER_PROMPT_BOSSMAN_2_5_ONE_RUN_RU.md`](MASTER_PROMPT_BOSSMAN_2_5_ONE_RUN_RU.md), [`TREE_15_DIRECTIONS_20261014_RU.md`](TREE_15_DIRECTIONS_20261014_RU.md).

> Файл с таким именем в `origin/main` на `fb58317f` не существовал (ни в ветках, ни в соседних
> checkout-ах). Он создан этим коммитом. Прежних утверждений «15 направлений приняты» не было и нет.

## 1. Принцип: один продукт, а не пятнадцать подсистем

Пятнадцать направлений — это **карта возможностей одного Bossman**
(`command-center/bcc/capability_tree_seed.json`: 15 прямых потомков корня `bossman`), а не пятнадцать
реализаций. Направление не получает собственного task engine, хранилища памяти, permission engine,
очереди или модельного флота. Новая возможность подключается к общему контуру ниже; если ей нужна
вторая очередь или второй движок разрешений, она не принимается.

| Слой контракта | Что это в коде (проверено чтением на `fb58317f`) | Что не доказано |
|---|---|---|
| **Task Kernel**: один task ID, очередь, статус, DAG | таблица `tasks` в `command-center/bcc/db.py`; DAG `command-center/bcc/v2/task_graph.py`; допуск исполнителя `command-center/bcc/task_admission.py`; тесты `command-center/tests/test_task_graph_v2.py` | Один и тот же task ID сквозь Telegram, UI, мобильное приложение и CLI на установленной сборке не измерен |
| **Policy / approval / STOP** | `command-center/bcc/permissions.py`, `approval_scope.py`, `approvals.py`, `features/pc_agent_policy.py`; STOP: `autonomy/stop.py`, тесты `test_owner_global_stop.py`, `test_computer_stop_race.py`, `test_owner_stop_lifecycle.py` | Живой STOP из Telegram на работающем runtime; отсев чужих отправителей; `bypassPermissions` на ПК владельца (см. `docs/audits/2026-10-18-run-audit/README.md`) |
| **Least-privilege workers** | `command-center/bcc/autonomy/workers.py`, `autonomy/hands.py`, `plugin_security.py`, `features/pc_agent_policy.py`, `coding_session.py` | Минимальные права на каждом реальном исполнителе в установленной сборке |
| **Общий контракт event / evidence / artifact** | `command-center/bcc/events.py` (`EventBus`), `trace.py`, `run_provenance.py`, `v2/verification.py` (ожидаемое/наблюдаемое состояние + receipt), `bossman-core/bossman/evidence_graph.py`, схема `docs/v2.2-20261013/evidence.schema.json` | Единый формат receipt во всех 15 направлениях; хранение artifact hash для каждого результата |
| **Тонкие интерфейсы** | Telegram `command-center/bcc/telegram_companion/`, UI `command-center/ui/`, CLI `command-center/bcc/terminal_cli/`, мобильное `apps/bossman-mobile/`, удалённый клиент `bossman-core/bossman/remote_client/` | Что каждый интерфейс обращается именно к одному backend и одной БД на одной сборке |

Правила, которые направления **не вправе** менять: владелец-only пульт, личная кнопка release/расходов/внешних
действий, конституция и её pin, ключи не в git, STOP, бюджет, `main` только через гейты
(`AGENTS.md`). Наличие файла или статус листа не включает вызовы, покупки, голос или фоновые траты.

## 2. Идентичность источников (сверка, не приёмка)

| Объект | Значение | Вывод |
|---|---|---|
| `origin/main` | `fb58317fbeea48045bb1ceb78730ce9b9f6cface` | Текущий источник для этого документа |
| Прежний SHA в `BOSSMAN_2_5_DESIGN_AND_OSS_RU.md` | `ceee487e826b9c920655cc79fcb91096503766a4` | Устарел: предок `main`, позади на 18 коммитов; заменён |
| `command-center/bcc/capability_tree_seed.json` | `as_of` 2026-10-05, `source_sha` `e4539337d97134e6d77d13eb00fe9d8f2d36237c`, ветка `feat/bossman-1.9-final-freeze`, 923 узла | Исторический снимок источника данных; не относится к `fb58317f`. Руками не правился: поле входное, не вычисляемое |
| `docs/architecture/tree-registry.json` | `head` `f8419e1c6766ef6e08dba4fb26309f86952bdf0b` (предок `main`, позади на 76 коммитов), 922 leaf | `head` пишет `tools/tree_registry_sync.py` (`git rev-parse HEAD`); не обновлялся, статусы не трогались |
| Evidence уровня `ci` в реестре | 471 leaf `GREEN_ON_SHA` на `4c29b2744bad34f7356907e1577003fc7ef33134` | Зелёное относится только к этому SHA, не к `fb58317f` и не к установленной сборке |
| Evidence уровня `owner_pc` | 0 из 922 (`NOT_RUN` у всех) | Ни одно направление не принято на ПК владельца |
| Публичный сайт дерева | `https://subtle-baklava-676893.netlify.app/` отвечает 200, страница ~9.8 КБ без SHA/даты снимка; `molotroka123-cell.github.io/ai/bossman-development` отвечает 404 | **NOT_VERIFIED**: сайт не читается как источник SHA. Совпадение сайта с seed/registry не заявляется |
| Установленная сборка (archive hash) | не читалась | **NOT_VERIFIED** |
| Отдельный checkout `C:\Users\asd\Bossman\wt-pc-agent-argon-20261013` | ветка `release/bossman-one-20261014`, `8a956d07afd29b3dd5a208df3d22e7ed552557ec`; изменено `apps/poker-vision/pokervision/strategy.py`, не отслеживаются `apps/poker-botlab/botlab/gto.py`, `apps/poker-botlab/tests/test_gto_tournament.py`, `tools/poker_gto_tournament.py` | Только чтение, не менялся. Poker-файлы в этот документ и коммит не включены; их владелец — Argon, не проверены (тесты не запускались) |

Согласование «seed SHA = registry head = main = установленная сборка = сайт» **не достигнуто**.
Достижение — через `tools/tree_registry_sync.py` и `tools/tree_apply_evidence.py` отдельным owner-gated
шагом, не правкой статусов вручную.

## 3. Пятнадцать направлений

Колонки реестра — из `docs/architecture/tree-registry.json` (поле `zone_parent`): код `PRESENT` / всего,
`ci GREEN_ON_SHA` (на `4c29b274`) / всего, `owner_pc` везде 0. Команды тестов запускались **в этой сессии**
из каталога `command-center/` как `python -m pytest tests/<файл> -q -p no:cacheprovider`
(если путь указан иначе — в строке). Сеть, GUI, Telegram, генерация и платные вызовы не использовались.
Слово «принято» ни к одному направлению не относится.

### 3.1 Jeff — собеседник
- **Реестр:** 129/141 код, 58/141 ci, owner_pc 0/141.
- **Найдено в коде:** `command-center/bcc/telegram_companion/service.py`, `features/jeff_master_parser.py`, `features/jeff_settings.py`, `features/jeff_insights.py`, `jeff_desktop.py`.
- **Подтверждено тестами сейчас:** `test_jeff_2_safety.py` — 162 passed; `test_jeff_local_only.py` — 9 passed; `test_jeff_privacy_in_code.py` — 8 passed.
- **Только запись/спека/гипотеза:** живой диалог на ПК владельца, голос, Stars (на паузе по решению владельца), 12 листьев с `code MISSING`.
- **Следующий шаг:** прогнать owner-run сценарий через общий пульт и приложить receipt. **Блокер:** owner-PC, Telegram-сессия владельца.

### 3.2 UX — Command Center и интерфейсы
- **Реестр:** 34/34 код, 32/34 ci.
- **Найдено в коде:** `command-center/ui/app.js`, `command-center/ui/pages/`, `command-center/bcc/app.py`, `events.py`.
- **Тесты сейчас:** `test_cc_ux_p1_findings.py` — 7 passed; `test_events_stream_keepalive.py` — 2 passed; `test_events_stream_last_event_id.py` — 4 passed.
- **Только запись:** реальные скриншоты установленной сборки (§7 DESIGN-документа), браузерные UX-тесты не запускались.
- **Следующий шаг:** снять по одному скриншоту очереди, approval, STOP и receipt из свежей сборки. **Блокер:** owner-run сборка и безопасная среда без личных данных.

### 3.3 Apps — приложения и инструменты
- **Реестр:** 15/16 код, 3/16 ci.
- **Найдено в коде:** `apps/*` (war-terminal, bossman-accountant, bossman-mobile, swapme-bot, shorts-factory и др.), `command-center/bcc/features/apps.py`, `apps_control.py`.
- **Тесты сейчас:** из `apps/war-terminal`: `python -m pytest tests -q -p no:cacheprovider` — 184 passed.
- **Только запись:** остальные приложения и их интеграция в общую очередь не запускались; War Terminal на живых данных (TED) этим прогоном не проверялся.
- **Следующий шаг:** подключить один вызов War Terminal через Task Kernel без нового ядра. **Блокер:** выбор безопасного сценария, runtime на ПК владельца.

### 3.4 Models — локальные и выбор моделей
- **Реестр:** 4/9 код (5 `MISSING`), 0/9 ci.
- **Найдено в коде:** `command-center/bcc/v2/model_router.py`, `model_intelligence.py`, `providers.py`, `features/local_first.py`.
- **Тесты сейчас:** `test_feat_router.py` — 5 passed; `test_free_providers.py` — 7 passed; `test_mandatory_model_policy.py` — **FAIL: 32 passed, 2 failed** (`test_banned_models_stay_rejected_across_reload_and_restart`, `test_only_banned_chat_models_fall_back_to_the_safe_defaults`; ожидаемые кортежи безопасных моделей пусты). Причина не исследована; проверить, зависит ли от локального состояния данных, до правок кода.
- **Только спека:** выбор моделей генерации (`docs/v2.2-20261013/GENERATION_MODEL_SELECTION_RU.md`, MC22-16), `/genmodel` не активирован.
- **Следующий шаг:** разобрать два красных теста (воспроизвести на чистом data dir). **Блокер:** нет; решение о влиянии на политику моделей — за интегратором, гейты не ослаблять.

### 3.5 Cloud — API и бесплатные провайдеры
- **Реестр:** 14/15 код, 13/15 ci.
- **Найдено в коде:** `command-center/bcc/provider_governance.py`, `v2/openrouter_catalog_service.py`, `v2/openrouter_ext.py`, `features/free_providers.py`, `fable_cap.py`.
- **Тесты сейчас:** `test_feat_openrouter.py` — 11 passed; `test_provider_governance_memory.py` — 5 passed.
- **Только запись:** живые вызовы провайдеров, лимиты и стоимость; платные вызовы запрещены без явного «да» владельца.
- **Следующий шаг:** измерить расход по существующему учёту на бесплатных провайдерах. **Блокер:** ключи в vault владельца, лимиты дня.

### 3.6 Computer — компьютер и браузер
- **Реестр:** 14/14 код, 9/14 ci.
- **Найдено в коде:** `command-center/bcc/desktop.py`, `v2/browser_control.py`, `hybrid/`, `features/pc_agent.py`, `features/pc_agent_policy.py`, `telegram_companion/pc_agent_bridge.py`, `bossman-core/docs/COMPUTER_USE_OPERATIONS.md`.
- **Тесты сейчас:** `test_computer_stop_race.py` — 11 passed; `test_owner_global_stop.py` — 3; `test_computer_verify_and_approval.py` — 5; `test_owner_stop_lifecycle.py` — 2; `test_pc_agent_argon.py` — **38 passed, 1 FAILED** (`test_reports_runner_no_silent_synthetic_and_two_runs_sha256`: демо-прогон `reports_runner.run_exchange_report` вернул `ok=False`; причина не исследована).
- **Только запись:** реальная мышь/клавиатура, идентичность окна, STOP из Telegram и restart на установленной сборке — `NOT_RUN`. API-тест не доказывает GUI.
- **Следующий шаг:** разобрать красный reports_runner-тест; затем один локальный GUI-fixture с STOP. **Блокер:** owner-PC для GUI.

### 3.7 Media — Motion, видео, звук
- **Реестр:** 58/61 код, 29/61 ci.
- **Найдено в коде:** `command-center/bcc/features/motion_studio.py`, `motion_workflow/`, `direct_gen/`, `video_studio/`, `tools/video_gate/`, `tools/genjutsu/`.
- **Тесты сейчас:** `test_jeff_2_media.py` — 48 passed; `test_direct_gen_faceswap.py` — 34 passed; из корня репозитория `tests/test_background_swap_gate.py` — 18 passed.
- **Только запись:** результаты Gate 0/1 и качество видео; генерации не запускались. Порядок этапов — `docs/owner/VIDEO_PIPELINE_STAGES_20261010.md`; пороги фиксируются до измерения.
- **Следующий шаг:** ничего не генерировать без owner-подтверждения этапа. **Блокер:** GPU, свободный ПК, согласие на лица.

### 3.8 Memory — память и обучение
- **Реестр:** 58/62 код, 57/62 ci.
- **Найдено в коде:** `command-center/bcc/v2/memory/`, `context_os/`, `bossman-core/bossman/learning_guard/`, `docs/architecture/MEMORY_SINGLE_WRITER.md`.
- **Тесты сейчас:** `test_memory_recall_engine.py` — 4; `test_mandatory_memory_poisoning.py` — 44; `test_memory_lifecycle_wiring.py` — 7 passed, 1 skipped; из корня `tests/test_memory_lifecycle.py` — 38 passed.
- **Только запись/гипотеза:** перенос на скрытых новых задачах, retention после полного рестарта, выигрыш на неизменном evaluator. Попадание памяти не есть обучение.
- **Следующий шаг:** предрегистрировать holdout и пороги до измерения. **Блокер:** независимый verifier другого семейства моделей (см. §5).

### 3.9 Agents — агенты и оркестрация
- **Реестр:** 19/21 код, 17/21 ci.
- **Найдено в коде:** `command-center/bcc/v2/task_graph.py`, `v2/agent_graph.py`, `features/action_router.py`, `task_admission.py`, `autonomy/`.
- **Тесты сейчас:** `test_task_graph_v2.py` — 12; `test_action_router.py` — 8; `test_autonomy_policy.py` — 71; `test_autonomy_workers.py` — 19; `test_coding_workers.py` — 5 (все passed).
- **Только запись:** сквозной task ID через все интерфейсы; 24h/48h soak.
- **Следующий шаг:** один сквозной сценарий с проверкой одного task ID в Telegram и UI. **Блокер:** запущенный runtime владельца.

### 3.10 Skills — навыки
- **Реестр:** 51/68 код (17 `MISSING`), 1/68 ci.
- **Найдено в коде:** `command-center/bcc/v2/skill_library.py`, `v2/skill_catalog.py`, `skills_catalog/`.
- **Тесты сейчас:** `test_feat_skills.py` — 7 passed; из корня `tests/test_curated_agent_skills.py` — 19 passed.
- **Только запись:** большая часть листьев не имеет ci-evidence (67 из 68 `NOT_RUN`); «уроки» не меняют исход без исполняемой формы.
- **Следующий шаг:** выбрать 1 навык и пройти ci-evidence через `tools/tree_apply_evidence.py`. **Блокер:** зелёный CI на точном SHA.

### 3.11 Plugins — MCP и внешние инструменты
- **Реестр:** 29/29 код, 26/29 ci.
- **Найдено в коде:** `command-center/bcc/plugin_security.py`, `v2/mcp_hub.py`, `v2/mcp_runtime.py`, `features/mcp_catalog.py`.
- **Тесты сейчас:** `test_plugin_security.py` — 26; `test_plugins_adapter.py` — 54; `test_plugins_stub_not_success.py` — 10; `test_plugin_mcp_tool_call.py` — 12 (все passed).
- **Только запись:** реальные внешние MCP-серверы не вызывались; Playwright MCP — кандидат (см. DESIGN, §4).
- **Следующий шаг:** allowlist методов и проверка наследования STOP/approval на одном реальном MCP-вызове в локальной fixture. **Блокер:** нет внешнего блокера.

### 3.12 Ops — операции, приёмка, идентичность
- **Реестр:** 245/271 код, 223/271 ci.
- **Найдено в коде:** `command-center/bcc/build_identity.py`, `run_provenance.py`, `tools/acceptance_registry.json`, `tools/tree_registry_sync.py`, `tools/tree_apply_evidence.py`, `tools/ci_secret_scan.py`, `docs/v8/ACCEPTANCE_AND_FREEZE_GATES.md`.
- **Тесты сейчас:** из корня: `test_acceptance_registry.py` — 72; `test_build_source_identity.py` — 15; `test_tree_registry_in_sync.py` — 3; `test_tree_registry_sync.py` — 9; `test_tree_apply_evidence.py` — 21; `test_tree_publish_site.py` — 23 (passed); `test_ci_secret_scan.py` — **14 passed, 1 FAILED** (`test_repository_itself_is_clean`).
- **Найденный дефект (на `main`, не от этого коммита):** `python tools/ci_secret_scan.py` сообщает `command-center/tests/test_web_access.py:84: openai key`. Вероятно фальшивая канарейка без маркера `ci-secret-scan: allow`; не подтверждено. Гейт не менялся.
- **Следующий шаг:** проверить строку 84 и либо добавить штатный allow-маркер, либо убрать значение (отдельным коммитом, с review). **Блокер:** решение интегратора; ослаблять сканер нельзя.

### 3.13 OSS — открытые проекты
- **Реестр:** 126/126 код (как ссылки), 0/126 ci.
- **Найдено в коде:** `command-center/bcc/oss/` (`docling.py`, `qdrant.py`, `whisper.py`, `comfyui.py`, `piper.py` и др.).
- **Тесты сейчас:** `test_oss_docling.py` — 13 passed, 5 skipped (пакет `docling` не установлен: `ModuleNotFoundError`); `test_oss_integrations.py` — 4; `test_oss_memory_refusals.py` — 5.
- **Только гипотеза:** польза Docling/Playwright MCP/OCDS Kit и остальных кандидатов из DESIGN — по документации upstream; PoC на синтетических файлах `NOT_RUN`.
- **Следующий шаг:** установить `docling` в изолированном venv и прогнать 5 пропущенных тестов на синтетическом PDF/DOCX. **Блокер:** разрешение на установку; модели автоматически не качать.

### 3.14 Commerce — заработок и поставка клиенту
- **Реестр:** 6 листьев, все `idea`, источник — `docs/owner/TREE_15_DIRECTIONS_20261014_RU.md`; ci 0/6.
- **Найдено в коде (смежное):** `command-center/bcc/features/reports_runner.py`, `tools/bossman_business.py`, `apps/swapme-bot/`, `apps/bossman-accountant/`.
- **Тесты сейчас:** `test_bossman_business_entry.py` — 2; `test_bossman_15_economy_tools.py` — 4 (passed); связанный `test_pc_agent_argon.py::test_reports_runner_no_silent_synthetic_and_two_runs_sha256` — **FAIL** (см. §3.6).
- **Только гипотеза:** заказ, оплата, выручка, возвраты — доказательств нет; техническое demo не есть доход.
- **Следующий шаг:** починить/диагностировать reports_runner-тест, затем один воспроизводимый отчёт на синтетическом экспорте. **Блокер:** реальный заказчик и оплата — только решение владельца.

### 3.15 Devices — другие устройства
- **Реестр:** 6 листьев, все `idea`; ci 0/6.
- **Найдено в коде (смежное):** `bossman-core/bossman/remote_client/` (`DeviceRegistry`, `revoke_device`, `lock_device`, сессии — для клиентских устройств, например телефона), `bossman-core/scripts/bootstrap_remote_device.py`, `command-center/bcc/telegram_companion/remote_access.py`.
- **Тесты сейчас:** из `bossman-core`: `python -m pytest tests/test_remote_client.py tests/test_core_auth_perimeter.py -q -p no:cacheprovider` — 58 passed.
- **Только спека:** второй ПК как исполнитель задач (выбор устройства, очередь ресурсов, heartbeat, общий STOP) — в коде в таком виде **не найден**; `remote_client` обслуживает клиентский доступ, а не worker-ПК.
- **Следующий шаг:** описать контракт «устройство-исполнитель» поверх существующего `DeviceRegistry` без второго ядра. **Блокер:** второй физический ПК владельца для любого живого теста; до тех пор `NOT_RUN`.

## 4. Сводка

| Показатель | Значение |
|---|---|
| Направлений с кодом и выбранными тестами | 13 из 15 имеют собственный код и хотя бы один запущенный тест; у Commerce и Devices только смежный код |
| Красные тесты, запущенные сейчас (на `main`, до любых правок) | 4 теста в 3 файлах: `test_mandatory_model_policy` (2, Models), `test_pc_agent_argon::test_reports_runner_no_silent_synthetic_and_two_runs_sha256` (1, Computer/Commerce), `test_ci_secret_scan::test_repository_itself_is_clean` (1, Ops). Причины не устранялись в этом документационном коммите |
| Направлений, принятых на ПК владельца | 0 |
| Платные вызовы / сеть записи / GUI / Telegram / генерация | не использовались |

## 5. Самоулучшение: что существует фактически

- В `origin/main` (`fb58317f`) пакета `command-center/bcc/self_improve` **нет**. В `main` есть контур `command-center/bcc/autonomy/` (цикл, constitution, policy, staging, review, journal) с тестами `test_autonomy_*.py`, и `tools/tree_self_repair_cycle.py`; доказанного `SELF_REPAIR_SINGLE_CYCLE_PASS` в этом документе не заявляется.
- Отдельный рой: worktree `C:\Users\asd\Bossman\integration-20261006\.claude\worktrees\agent-a6482b2d540214b4a`, ветка `worktree-agent-a6482b2d540214b4a`, база `67a2a0b54239ff78c89c724d254e9f575d33dfb3` (старше `main`). Файлы **проиндексированы (`A`), но не закоммичены**: пакет `command-center/bcc/self_improve/` (`loop.py`, `limits.py`, `approval.py`, `sandbox.py`, `observation.py`, `drafts.py`, `delegation.py`, `apply.py`, `botmap.py`, `redact.py`, `store.py`, `owner.py`), `command-center/tests/test_self_improve.py` (в файле 42 функции `test_*`, число параметризованных кейсов в заявленные 53 здесь не пересчитывалось и тесты в этом worktree **не запускались**), UI-страница, `docs/audits/2026-10-18-swarm-self-improve/`.
- Режим пакета — PROPOSE-ONLY (docstring `self_improve/__init__.py`): применение требует токена владельца, который агент не выпускает.
- **Прогон 1 (`RUN1_RU.md` в том worktree): `NOT_RUN`, стоимость 0.00 USD, ни одного вызова модели.** Причина — нет независимого проверяющего другого семейства без изменения живой конфигурации. Слово «улучшено» не применимо. Это путь к `SELF_REPAIR_SINGLE_CYCLE_PASS`, а не его достижение.
- В этой сессии модели, обучение и платные вызовы не запускались.

## 6. Условия, при которых направление можно называть принятым

1. Один точный source SHA, согласованный с tree seed, registry `head`, archive hash установленной сборки и публичным сайтом.
2. Зелёный CI на этом SHA и `owner_pc` evidence из файла владельца (`tools/tree_apply_evidence.py`).
3. Предрегистрированные пороги и holdout; отрицательные контроли; независимый verifier receipt.
4. Ни одного обхода STOP/approval и ни одной второй реализации ядра.

Пока не выполнено — статус по каждому направлению остаётся таким, как в §3.
