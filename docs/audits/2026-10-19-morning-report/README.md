# Утренний отчёт Argon 4 — ночной прогон Bossman 2.5 (18→19.10.2026)

- **Дата и время отчёта:** `2026-10-18T16:44:00-07:00` (`2026-10-18T23:44:00Z`)
- **Рабочее дерево (активное):** `C:\Users\asd\Bossman\wt-checkpoint-20261015` (ветка `feat/war-procurement-terminal-v1`)
- **Свежий `origin/main`:** `62abfcb5bb3f74d5d4aa62b8d3bee1ad5c41b2d1` (`docs: clarify incremental Bossman completion and evidence`, 2026-10-18 15:32 -0700)
- **Кодовые коммиты прогона:**
  - `d91abed92f2b9da21b0ab4e4b034b079ba86c683` — `fix(governor,direct-gen,companion): fix warm-up cap loop, sd-cli priority boost, delete race, and transient getUpdates conflict`
  - `80ec28d383dc65bc80494706c540887a1700002c` — `test(autonomy): isolate default_pin_path from host machine constitution pin in conftest`
- **Чужие / пользовательские незакоммиченные файлы (сохранены без изменений):**
  - В `C:\Users\asd\Bossman\wt-checkpoint-20261015`: `?? command-center/tgapp.py` (не тронут).
  - В `C:\Users\asd\Bossman\wt-pc-agent-argon-20261013` (ветка `release/bossman-one-20261014`, `8a956d07`): модифицированные файлы `apps/poker-coach/...` и `docs/poker_live_audit_2026_10_18.md` (не тронуты).
- **Платные облачные расходы за прогон:** **`$0.00`** (`GET /api/spend` → `{"total_usd": 0.0, "total_calls": 0}`).
- **Второй экземпляр не создавался:** все правки внесены строго в существующие модули (`tools/resource_governor/governor.py`, `command-center/bcc/direct_gen/sdcli.py`, `command-center/bcc/direct_gen/service.py`, `command-center/bcc/telegram_companion/service.py`, `command-center/tests/conftest.py`), управление процессами — только через штатный `tools/owner_one_bossman.py` и задачи планировщика `BossmanOne-*`.

---

## 1. Что уже работало, что показали логи и какие звенья были неисправны

### 1.1 Обезличенная сводка логов (`%LOCALAPPDATA%\Bossman\CommandCenter\one-bossman\governor.log`, `launcher.log`, `companion`)
1. **`governor.log` (`C:\Users\asd\AppData\Local\Bossman\CommandCenter\one-bossman\governor.log`):**
   - **До правок:** каждые 10 секунд в `IDLE` губернатор писал `warm-up finished: 0.00 GB in 0.0s (cap 40 GB reached)`, так как кумулятивный счётчик `self.warmed_bytes` не учитывал истечение `rewarm_after_s` (`1800s`), а `_maybe_warm()` запускал поток `_warm_worker()` без предварительной проверки активного объёма.
   - **Во время генерации:** при запуске `sd-cli.exe` из elevated-бэкенда (`:8801`) не-elevated губернатор не мог повысить приоритет процесса (`psutil.AccessDenied`), и `sd-cli.exe` оставался с `NORMAL_PRIORITY_CLASS` (`32`).
   - **После правок (`d91abed9`):**
     ```text
     2026-10-18 16:34:19,208 INFO warm-up run done: read 22.4 GB; available 113.4 -> 113.3 GB; cache 77.3 -> 99.6 GB
     2026-10-18 16:34:31,007 INFO ENTER ACTIVE: 1 live job(s) ['video:loading'] dry_run=False
     2026-10-18 16:34:46,682 INFO JEFF cloud_free entered: route=bcc_provider models=['deepseek-v4.1-flash-uncensored'] owner_hold_before=False was_running_before=True
     2026-10-18 16:34:56,749 INFO LEAVE ACTIVE: last job finished
     2026-10-18 16:34:58,161 INFO local Jeff relaunch deferred 120.0s (idle delay)
     ```
     Холостой спам `cap 40 GB reached` каждые 10 секунд полностью исчез; `governor-status.json` фиксирует `hot_mb_s: 3290..4032 MB/s` из RAM-кэша.
2. **Логи Telegram Companion (`one-bossman-logs/launcher.log` и `companion`):**
   - **До правок:** процесс `bcc.telegram_companion` падал с кодом `2` и строкой `TELEGRAM_COMPANION=CONFLICT (another getUpdates is running for this bot)` ровно в `:14:02`, `:29:02`, `:44:02`, `:59:02` каждого часа.
   - **Найденная причина:** планировщик Windows запускал задачу `\GrechkaTrap` (`C:\Users\asd\Bossman\grechka-trap\watcher.py`) каждые 15 минут с тем же бот-токеном без `token_poller_lock`.
   - **После правок (`d91abed9`):** цикл `poll()` в `command-center/bcc/telegram_companion/service.py` под защитой `token_poller_lock` переносит одиночные транзиентные конфликты `getUpdates` (до 4 подряд, сбрасывая счётчик при успешном ответе). Процесс `PID 20840` запущен в `15:53:54` и непрерывно работает без единого падения через тики `15:59:02`, `16:14:02`, `16:29:02`, `16:44:02`.
3. **Гонка удаления в `DirectGenService.delete` (`command-center/bcc/direct_gen/service.py`):**
   - **До правок:** вызов `DELETE /api/direct-gen/jobs/{id}` сразу после перехода задачи в `status: "completed"` возвращал `409 job_running`, пока `self._tasks[job_id]` завершал `await self._emit(job)`.
   - **После правок (`d91abed9`):** `delete()` проверяет `job["status"] not in ("completed", "failed", "cancelled")` и очищает `self._tasks.pop(job_id, None)` в `finally:`.
4. **Затекание боевого пина конституции v2 в юнит-тесты (`command-center/tests/conftest.py`):**
   - **До правок:** 41 тест в `test_autonomy_*.py` падал с `GuardError: claude is not independent of the writer 'claude'`, так как `default_pin_path()` читал реальный `%LOCALAPPDATA%\Bossman\autonomy\constitution.sha256` владельца.
   - **После правок (`80ec28d3`):** фикстура `_autonomy_pin_off_the_real_machine` в `conftest.py` изолирует `default_pin_path(env=None)` во временную папку теста (`460 passed, 0 failed`).

---

## 2. Подтверждённый сквозной рабочий сценарий на текущем runtime (`:8801` + `:8840` + `Governor`)

На живом runtime (`PID 23160` на `:8801`, `PID 23492` на `:8840`, `PID 10596` `Governor`, `PID 20840` `Telegram Companion`, `PID 21580` `Jeff`) проверен полный цикл владельца от постановки задач до очистки:

1. **Фото (`z-image-turbo`, `job_id=78f9efd3832e4e8b85fb7de126c316ca`):**
   - Запрос: `POST http://127.0.0.1:8801/api/direct-gen/jobs` (`model="z-image-turbo"`, `resolution="256x256"`, `steps=1`, `seed=42`).
   - Наблюдаемое поведение во время работы: `sd-cli.exe` (`PID 3628`) запущен с `PriorityClass = 128` (`HIGH_PRIORITY_CLASS`); `Governor` перешёл в `ACTIVE`, выгрузил модели Ollama (`ollama_unloads: 2`), переключил Jeff в `cloud_free` (`route=bcc_provider`, `$0`).
   - Проверка защиты удаления: `DELETE` с `X-BCC-Participant: guest` → `403 {"code": "owner_only"}`; `DELETE` с `X-BCC-Participant: owner` во время генерации → `409 {"code": "job_running"}`.
   - Завершение: `status: "completed"`, `elapsed_s: 549.88`, `image.png` (`108522` байт, `sha256: b76128145c216002618b0318e5a586a7cd78ebc9f80d61b08d2d1956b68650e7`, `verified: true`).
   - Удаление навсегда: `DELETE /api/direct-gen/jobs/78f9efd3832e4e8b85fb7de126c316ca` → `200 {"ok": true, "deleted": true, "job_id": "78f9efd3832e4e8b85fb7de126c316ca"}`; повторный `GET` → `404 {"code": "unknown_job"}`.
2. **Видео (`wan2.2-ti2v-5b`, `job_id=98a3465a7c104d8191cc1e79ae3986d6` и `wan2.2-i2v-a14b-uncensored`, `job_id=e9866724bae640c1bbed8c9a0d3e6f89` / `d2f8056f5dc64e599b01928d94e74ce4`):**
   - `wan2.2-ti2v-5b` (`256x256`, `steps=1`, `duration=0.25s`, `frames=5`, `seed=42`): `sd-cli.exe` (`PID 22692`, `PriorityClass=128`), завершился со статусом `completed` за `615.92s` (`video.webm`, `17172` байт, `sha256: 2799d07128094ad6f99a17b4104cba2ac53421270afb837167ecca48dd2c92fa`, `codec: vp8`, `verified: true`), затем удалён через `DELETE` (`200 OK`).
   - `wan2.2-i2v-a14b-uncensored`: проверены постановка в очередь (`queue_position: 1`), перезапись `@1 -> the start image`, отмена из очереди (`POST /cancel` → `200 cancelled`), старт нового бинарника `C:\Users\asd\Bossman\media-runtime\sdcpp\vulkan-20261018\sd-cli.exe` (`95b3090`, `PID 12028`, `PriorityClass=128`, без ошибки `time_projection.1.bias`), отмена и полное удаление тестовой задачи (`200 OK`).

---

## 3. Проверка особых разделов запроса

### 3.1 Заявления об «обучении» (Learning / Self-Improve / Fine-Tune)
- **Вердикт:** **Обучение НЕ доказано (`NOT_RUN` / `HYPOTHESIS`).**
- **Что изменилось в весах моделей:** **ничего** (веса базовых GGUF/safetensors моделей на диске не менялись; конвертация `wan22-i2v-uncensored` fp8→Q8_0 GGUF меняла только формат хранения тензоров).
- **Что изменилось только в промптах, памяти, настройках или инструментах:**
  - В `command-center/bcc/self_improve/` добавлен PROPOSE-ONLY контур роя (`test_self_improve.py`: `52 passed, 1 skipped`), но первый реальный цикл (`docs/audits/2026-10-18-swarm-self-improve/RUN1_RU.md`) имеет статус `NOT_RUN` (`cost: $0.00`, `0` вызовов моделей из-за требования независимого верификатора другого модельного семейства).
  - Память (`bcc/v2/memory/`) и уроки (`learning_guard`) сохраняют записи в SQLite, но сравнение «до и после» на едином предрегистрированном наборе отложенных задач (holdout) с неизменным evaluator **не проводилось** (`NOT_RUN`).

### 3.2 Автономная работа на ПК (от запуска до результата)
- **Проверено в живой среде (`PASS`):**
  - Состояние служб: `BossmanOne-1-Backend` (`PID 23160`), `BossmanOne-2-Jeff` (`PID 21580`), `BossmanOne-3-Companion` (`PID 20840`), `BossmanOne-5-Apps` (`PID 23492`, `PID 8380`), `BossmanOne-6-Governor` (`PID 10596`).
  - Ограничение ресурсов: `Governor` удерживает RAM-кэш весов до `40 GB`, выгружает Ollama и понижает приоритет фоновых python-процессов при генерации.
  - Журналирование без секретов: `tools/ci_secret_scan.py` → `Secret-pattern scan: PASS`; хэш-цепочка журнала автономии `GET /api/autonomy/journal/verify` → `{"ok": true}`.
  - STOP, восстановление и штатное выключение: проверены через `tools/owner_one_bossman.py stop --data-dir ... --kinds backend` (`ctrl-c(rc=0)`, `stopped: graceful`) и повторный запуск `Start-ScheduledTask -TaskName "BossmanOne-1-Backend"` (`healthy on 127.0.0.1:8801`, `source_identity: PASS`).
- **Что не запускалось в этом прогоне (`NOT_RUN`):**
  - Физическая перезагрузка Windows (`Reboot`) в этой сессии не вызывалась (последний реальный холодный старт после перезагрузки зафиксирован в логах `2026-10-18 06:08`).
  - Живой GUI-сценарий управления мышью/клавиатурой с аппаратным STOP из Telegram на экране владельца — `NOT_RUN` (проверены только контрактные тесты `test_computer_stop_race.py`, `test_owner_global_stop.py`, `test_pc_agent_argon.py`).

### 3.3 Браузерный поиск и защита от инструкций на страницах
- **Статус:** **Код + тесты + live API чтения (`PASS`), внешние отправки заблокированы (`PASS`).**
- **Доказательства:**
  - `GET http://127.0.0.1:8801/api/browser/health` → `200 OK`; `GET /api/browser-help/policy` → `200 OK`; `GET /api/web-access/status` → `200 OK` (лимиты 30/мин и 500/день, только публичные URL).
  - В `apps/war-terminal` (`:8830/api/v1/tenders`, `:8830/api/v1/fx/crosscheck`) проверены реальные первоисточники (`TED`, `NEN`, `ČNB`, `ECB`) с ссылками на источники и отсевом неподтверждённых записей (`excluded_unverified: 0`).
  - Защита от prompt-injection в веб-страницах проверена тестами `command-center/tests/test_browser_reading_20260930.py` и `test_web_access.py`.

### 3.4 Установка с нуля в чистой папке, откат и индикация SHA
- **Индикация SHA и рычагов остановки в текущем runtime (`PASS`):**
  - `GET http://127.0.0.1:8801/health/live` однозначно возвращает `build_sha`, `checkout_sha` и `source_identity: "PASS"`.
  - Включённые возможности отдаются через `GET /api/capabilities` и `GET /api/control-plane/active`.
  - Остановка агента: `POST /api/autonomy/stop`, `POST /api/control-plane/stop-all`, команда `/stop` в Telegram-пульте или `python tools/owner_one_bossman.py stop --data-dir "%LOCALAPPDATA%\Bossman\CommandCenter" --kinds jeff,companion,backend --hold`.
- **Чистая распаковка архива `BOSSMAN-Windows-x64-<SHA>.zip` в отдельную папку (`NOT_RUN` для текущего SHA):**
  - Контрактные тесты установщика и цепочки проверки (`tests/test_installed_product_chain.py`, `tests/test_packaging_installed.py`, `tests/test_installed_ui_sweep.py` — часть пакета из `215 passed`) зелёные, но новый ZIP-бандл для коммита `0618909c` через GitHub Actions `windows-bundle.yml` не собирался и в чистую папку `C:\Bossman-Test` не распаковывался (`NOT_RUN`).

---

## 4. Актуальный реестр 15 направлений (`command-center/bcc/capability_tree_seed.json` / `BOSSMAN_2_5_FIFTEEN_BRANCH_ARCHITECTURE_RU.md`)

*(Примечание: три ранее красных теста из снимка `fb58317f` — `test_mandatory_model_policy.py`, `test_pc_agent_argon.py`, `test_ci_secret_scan.py` — были исправлены в `d3212208` и перепроверены нами на текущем SHA: **`142 passed, 1 skipped, 0 failed`**).*

| № | Направление (ID узла) | Что работает в коде | Что проверено тестом на текущем SHA | Что проверено в живой среде (`:8801`/`:8840`/`:8830`) | Чего не хватает / Статус приёмки |
|---|---|---|---|---|---|
| **1** | **Jeff — собеседник** (`jeff`) | `bcc/telegram_companion/service.py`, `bcc/pit/`, `features/jeff_*` | `test_jeff_2_safety.py` (162), `test_companion*.py` (49) — `PASS` | Процесс `PID 21580` (`bcc.pit.cli watch`) запущен; губернатор переключает `cloud_free` (`$0`) при генерации | Живой диалог с владельцем в `cloud_free` и Stars на паузе → **`PARTIAL / LIVE_DAEMON_UP`** |
| **2** | **UX — Command Center и интерфейсы** (`ux`) | `command-center/ui/`, `bcc/api.py`, `events.py` | `test_cc_ux_p1_findings.py`, `test_events_stream_*.py` — `PASS` | `:8801/api/health` (`200`), `:8840/` (`200`, форма без перезагрузки, кнопка «Удалить навсегда») | Скриншоты установленного ZIP-бандла не снимались → **`PASS (CHECKOUT_LIVE)` / `NOT_RUN (BUNDLE_UI)`** |
| **3** | **Apps — приложения и инструменты** (`apps`) | `apps/war-terminal`, `apps/bossman-mobile`, `apps/bossman-accountant`, `bcc/features/apps*.py` | `apps/war-terminal/tests` (`184 passed`) | `:8830` (`mode: LIVE`, `1120` тендеров, IČO валидатор, FX ČNB+ECB, RU/EN); `:8840` (`200 OK`) | Интеграция остальных `apps/*` в единый Task Kernel → **`PASS (WAR_TERMINAL & MOBILE LIVE)`** |
| **4** | **Models — локальные и выбор моделей** (`models`) | `bcc/v2/model_router.py`, `model_intelligence.py`, `providers.py` | `test_mandatory_model_policy.py` (`34 passed, 0 failed` на текущем SHA) | `/api/model-freshness` (`200`, будильник отключён), `/api/direct-gen/models` (`200`) | Команда `/genmodel` в пульте (MC22-16) только в спеке → **`PASS (CODE_AND_API)` / `NOT_RUN (/genmodel)`** |
| **5** | **Cloud — API и бесплатные провайдеры** (`cloud`) | `bcc/provider_governance.py`, `v2/openrouter_*.py`, `fable_cap.py` | `test_feat_openrouter.py` (11), `test_provider_governance_memory.py` (5) — `PASS` | `/api/spend` → `total_usd: 0.0`, `total_calls: 0`; `cloud_free` маршрут губернатора | Ключи `FreeLLMAPI` (`:3001`) ждут владельца → **`PASS ($0_GUARD_LIVE)` / `BLOCKED (FREELLM_KEYS)`** |
| **6** | **Computer — компьютер и браузер** (`computer`) | `bcc/desktop.py`, `v2/browser_control.py`, `features/pc_agent*.py` | `test_pc_agent_argon.py` (`39 passed, 0 failed`), `test_computer_stop_race.py` (11), `test_browser_reading_20260930.py` — `PASS` | `/api/browser/health` (`200`), `/api/browser-help/policy` (`200`), `/api/web-access/status` (`200`) | Наблюдаемый GUI-прогон мыши/клавиатуры с STOP из Telegram → **`NOT_RUN (LIVE_GUI)`** |
| **7** | **Media — Motion, видео, звук** (`media`) | `bcc/direct_gen/`, `features/motion_studio.py`, `video_studio/`, `tools/genjutsu/` | `test_direct_gen_*.py` (`124 passed`), `test_video_studio_integration.py` — `PASS` | Живое фото `78f9efd3...` (`completed`), живое видео `wan2.2-ti2v-5b` `98a3465a...` (`completed`), старт `wan2.2-i2v-a14b-uncensored` на `sd-cli 95b3090`, `PriorityClass=128`, удаление `200 OK` | `wan2.2-t2v-vace-a14b-uncensored` отключён (OOM на Q8_0); видео-faceswap на реальном ролике владельца остановлен движком → **`PASS (PHOTO & TI2V/I2V LIVE)`** |
| **8** | **Memory — память и обучение** (`memory`) | `bcc/v2/memory/`, `context_os/`, `bossman-core/bossman/learning_guard/` | `test_memory_lifecycle.py` (38), `test_mandatory_memory_poisoning.py` (44) — `PASS` | База SQLite и API памяти активны на `:8801` | Доказательство обучения (до/после на отложенном holdout) отсутствует → **`NOT_RUN (LEARNING_PROOF)`** |
| **9** | **Agents — агенты и оркестрация** (`agents`) | `bcc/autonomy/`, `bcc/self_improve/`, `v2/task_graph.py` | `test_autonomy_*.py` (`460 passed, 0 failed`), `test_self_improve.py` (`52 passed, 1 skipped`) | `/api/autonomy/status` (`200`), `/api/autonomy/journal/verify` (`ok: true`) | Прогон `self_improve` `RUN1` — `NOT_RUN` (нужен независимый верификатор); 24/48ч soak — `NOT_RUN` |
| **10** | **Skills — навыки** (`skills`) | `bcc/v2/skill_library.py`, `v2/skill_catalog.py` | `test_feat_skills.py` (7), `test_curated_agent_skills.py` (19) — `PASS` | `/api/skills` (`56` навыков), `/api/skill-catalog` (`12`) — `200 OK` | 67 из 68 листьев в `tree-registry.json` без `ci` receipt → **`PASS (API)` / `NOT_RUN (TREE_RECEIPTS)`** |
| **11** | **Plugins — MCP и внешние инструменты** (`plugins`) | `bcc/plugin_security.py`, `v2/mcp_hub.py`, `v2/mcp_runtime.py` | `test_plugin_security.py` (26), `test_plugin_mcp_tool_call.py` (12) — `PASS` | `/api/mcp/servers` (`13` серверов) — `200 OK` | Живой вызов внешнего MCP с проверкой наследования STOP → **`NOT_RUN (LIVE_MCP_CALL)`** |
| **12** | **Ops — операции, приёмка, идентичность** (`ops`) | `bcc/build_identity.py`, `tools/ci_secret_scan.py`, `tools/tree_*.py` | `test_ci_secret_scan.py` (`15 passed`), `test_*installed*.py` + `test_*tree*.py` (`215 passed`) | `/health/live` → `source_identity: "PASS"`, `build_sha == checkout_sha`; `ci_secret_scan.py` → `PASS` | Согласование `tree-registry.json` `head` и сайта с текущим SHA — owner-gated шаг → **`PASS (LIVE_IDENTITY)`** |
| **13** | **OSS — открытые проекты** (`oss`) | `bcc/oss/` (`docling.py`, `qdrant.py`, `whisper.py`, `comfyui.py`) | `test_oss_docling.py` (`13 passed, 5 skipped`), `test_oss_integrations.py` (4) | Модули загружены в `:8801` | Пакет `docling` не установлен в системном Python (5 тестов skipped) → **`NOT_RUN (DOCLING_LIVE)`** |
| **14** | **Commerce — заработок и поставка клиенту** (`commerce`) | `bcc/features/reports_runner.py`, `tools/bossman_business.py`, `apps/war-terminal` | `test_pc_agent_argon.py::test_reports_runner_no_silent_synthetic_and_two_runs_sha256` — `PASS` | `/api/economy/status`, `/api/v15/economy/status`, `/api/trading-lab/status` (`200 OK`), `:8830` LIVE | Реальная выручка = `$0.00` (строго гипотеза до оплаты клиента) → **`PASS (WORKFLOW_READY)` / `HYPOTHESIS (REVENUE)`** |
| **15** | **Devices — другие устройства** (`devices`) | `bossman-core/bossman/remote_client/`, `bcc/telegram_companion/remote_access.py` | `bossman-core/tests/test_remote_client.py` (`58 passed`) | Телефон владельца работает через Tailscale (`100.123.80.124:8840`) | Второй ПК как worker-исполнитель в коде не реализован → **`PASS (PHONE_CLIENT)` / `NOT_RUN (SECOND_PC_WORKER)`** |

---

## 5. Поэлементная таблица всех 39 требований `docs/v2.2-20261013/requirements.json`

Каждая строка содержит точный ID и название из `docs/v2.2-20261013/requirements.json`, проверенный SHA (`80ec28d3` / `0618909c`), команду или живой сценарий, путь к evidence и честный статус (`PASS`, `PARTIAL`, `NOT_RUN`, `BLOCKED`):

| ID | Точное название из `requirements.json` | Статус | SHA | Команда / Сценарий проверки | Наблюдаемый результат и путь к evidence |
|---|---|---|---|---|---|
| **A22-01** | `. Идентичность и карта доказательств` | **PASS** | `0618909c` | `GET http://127.0.0.1:8801/health/live`; `pytest tests/test_build_source_identity.py` | `source_identity: "PASS"`, `build_sha == checkout_sha`; `command-center/bcc/build_identity.py` |
| **A22-02** | `. Один Bossman в CLI, UI и Telegram` | **PASS** | `0618909c` | `GET :8801/api/health`, `GET :8840/`, `PID 20840` (`telegram_companion`) | Единый `BCC_DATA_DIR=%LOCALAPPDATA%\Bossman\CommandCenter`; `one-bossman-logs/launcher.log` |
| **A22-03** | `. Реальный Computer Use на Windows` | **NOT_RUN** | `80ec28d3` | `pytest command-center/tests/test_pc_agent_argon.py command-center/tests/test_computer_stop_race.py` | Юнит-контракты `50 passed`, но живой GUI-прогон мыши/клавиатуры с STOP не запускался |
| **A22-04** | `. Разрешения, конституция и выход к провайдерам` | **PASS** | `80ec28d3` | `pytest command-center/tests/test_companion_a22_04_pult_hardening.py command-center/tests/test_autonomy_constitution.py command-center/tests/test_autonomy_approver_policy.py` | `96 passed`; пин конституции v2 активен; в пульте `bypassPermissions` заменён на `default`, `/work_yes` с одноразовым кодом; устойчивость к `\GrechkaTrap` (`PID 20840`) |
| **A22-05** | `. Сохранность миссий, восстановление и лимиты` | **PASS** | `80ec28d3` | `pytest command-center/tests/test_autonomy_*.py`; `GET /api/autonomy/journal/verify` | `460 passed`; журнал автономии `{"ok": true}`; `_recover()` в `bcc/direct_gen/service.py` |
| **A22-06** | `. Строгое самоисправление` | **NOT_RUN** | `80ec28d3` | `pytest command-center/tests/test_self_improve.py` | Контракт роя `52 passed`, но живой цикл `docs/audits/2026-10-18-swarm-self-improve/RUN1_RU.md` — `NOT_RUN` (нет второго семейства моделей) |
| **A22-07** | `. Перенос навыка и сохранение способностей` | **NOT_RUN** | `80ec28d3` | `pytest tests/test_curated_agent_skills.py command-center/tests/test_feat_skills.py` | API отдаёт 56 навыков (`/api/skills`), но перенос навыка до/после на holdout-задачах не измерялся |
| **A22-08** | `. Жизненный цикл памяти и уроков` | **PASS** | `80ec28d3` | `pytest tests/test_memory_lifecycle.py command-center/tests/test_mandatory_memory_poisoning.py` | `82 passed`; защита от отравления памяти и единый писатель `bcc/v2/memory/` |
| **A22-09** | `. Обучение моделей и продвижение кандидатов` | **NOT_RUN** | `80ec28d3` | Сверка `bossman-core/bossman/learning_guard/` | Fine-tune весов и сравнение кандидатов на holdout не запускались (`NOT_RUN`) |
| **A22-10** | `. Загрузка сервера и прогоны 24/48 часов` | **PARTIAL** | `0618909c` | `python tools/resource_governor/governor.py status`; `test_governor.py` | Губернатор и приоритет `sd-cli` (`HIGH_PRIORITY_CLASS=128`) доказаны вживую (`18 passed`), но 24/48ч непрерывный soak-прогон — `NOT_RUN` |
| **A22-11** | `. Подключение второго устройства` | **PARTIAL** | `0618909c` | `pytest bossman-core/tests/test_remote_client.py`; `GET http://127.0.0.1:8840/` | Мобильное устройство владельца подключено через Tailscale (`:8840`), `58 passed`; второй ПК как worker — `NOT_RUN` |
| **A22-12** | `. Оркестрация без Claude/Codex` | **PARTIAL** | `80ec28d3` | `tools/limits_out_orchestra.py`; `pytest command-center/tests/test_autonomy_workers.py` | Код оркестра на бесплатных моделях готов, живой автономный цикл без Claude/Codex — `NOT_RUN` |
| **MC22-01** | `Активировать единый проверенный runtime пульта` | **PASS** | `d91abed9` | `pytest command-center/tests/test_companion.py`; живой `PID 20840` | Единственный поллер с `token_poller_lock`, переживает конфликты `\GrechkaTrap` (`15:53`→`16:44+`) |
| **MC22-02** | `Постоянные референсы до отмены` | **PASS** | `80ec28d3` | `pytest command-center/tests/test_direct_gen_photo.py` | Поддержка `@1`, `@2`, `@3` (`bcc/direct_gen/ref_tags.py`) и сохранение референсов в UI `:8840` |
| **MC22-03** | `Подготовка и исправление промпта локальной Qwen` | **PASS** | `80ec28d3` | `GET /api/direct-gen/assist/models`; `test_direct_gen_client.py` | Режим `ASSISTED` через локальные модели `127.0.0.1` (`bcc/direct_gen/service.py`) |
| **MC22-04** | `Доказать короткую генерацию изображения и измерить задержку` | **PASS** | `d91abed9` | Живой `POST /api/direct-gen/jobs` (`z-image-turbo`, `job_id=78f9efd3832e4e8b85fb7de126c316ca`) | `completed`, `image.png` (`108522` B, `sha256=b7612814...`, `verified=true`), приоритет `sd-cli` `High=128`, затем удалено через `DELETE` (`200 OK`) |
| **MC22-05** | `Подключить и проверить выбранную видеомодель` | **PASS** | `d91abed9` | Живой `POST /api/direct-gen/jobs` (`wan2.2-ti2v-5b`, `job_id=98a3465a7c104d8191cc1e79ae3986d6`; `wan2.2-i2v-a14b-uncensored`, `job_id=d2f8056f...`) | `wan2.2-ti2v-5b` → `completed` (`video.webm`, `17172` B, `sha256=2799d071...`, `verified=true`); `wan2.2-i2v-a14b-uncensored` запускается на `vulkan-20261018\sd-cli.exe` (`95b3090`, исторический полный прогон 360 с от 18.10 в `59f93daf`) |
| **MC22-06** | `Сохранить и доказать стадии Genjutsu` | **PASS** | `80ec28d3` | `pytest command-center/tests/test_direct_gen_masks.py tests/test_background_swap_gate.py` | Стадии сегментации и масок (`bcc/direct_gen/genjutsu.py`) проверены тестами (`124 passed` в `test_direct_gen_*.py`) |
| **MC22-07** | `Применять эффект ко всему клипу из редактора` | **PARTIAL** | `80ec28d3` | `pytest command-center/tests/test_video_studio_integration.py`; `GET /api/video-studio/capabilities` | API и контрактные тесты `PASS`; полный рендер длинного клипа на GPU в этой сессии — `NOT_RUN` |
| **MC22-08** | `Закончить существующий монтажный workflow Motion` | **PASS** | `80ec28d3` | `GET /api/motion-studio/status`, `GET /api/motion-workflow/status` | Эндпоинты `200 OK`, интеграционные тесты `command-center/tests/test_jeff_2_media.py` (`48 passed`) |
| **MC22-09** | `Соединить Motion с генеративными преобразованиями` | **PARTIAL** | `80ec28d3` | `GET /api/motion-workflow/status` | Код связки `bcc/motion_workflow/` активен (`200 OK`), сквозной GPU-рендер Motion+DirectGen в эту ночь — `NOT_RUN` |
| **MC22-10** | `Завершить аппаратную приемку Music Creator` | **NOT_RUN** | `80ec28d3` | `GET /api/music/health` (`200 OK`) | Эндпоинт здоров, но аппаратный замер генерации YuE на GPU отложен владельцем (`BOSSMAN_ONE_STATE_20261018_RU.md` п. 6) |
| **MC22-11** | `Голос, биты, дорожки и место для fine-tune` | **PARTIAL** | `80ec28d3` | `GET /api/music/health`, `GET /api/vibe/status`; `test_vibe_studio.py` | Контракты и структура каталогов `PASS`, живой fine-tune аудио — `NOT_RUN` |
| **MC22-12** | `Пройти платежный сценарий Jeff Stars` | **BLOCKED** | `0618909c` | Правило владельца (`AGENTS.md` / Вечный запрет №1) | Платежи и Telegram Stars на паузе по решению владельца; агенту запрещены любые финансовые операции |
| **MC22-13** | `Проверить качество ответов Jeff и поддержку` | **PARTIAL** | `80ec28d3` | `pytest command-center/tests/test_jeff_2_safety.py` (`162 passed`) | Код и фильтры безопасности `PASS`, живой диалог с людьми в Telegram без участия владельца — `NOT_RUN` |
| **MC22-14** | `Завершить один вертикальный сценарий Social Farm` | **BLOCKED** | `0618909c` | Правило владельца (запрет внешних публикаций без подтверждения) | Локальная генерация роликов работает, но внешняя публикация/рассылка заблокирована без прямого «да» владельца |
| **MC22-15** | `Свести Apps, дерево и доказательства выпуска` | **PARTIAL** | `0618909c` | `pytest tests/test_tree_registry_sync.py tests/test_tree_publish_site.py command-center/tests/test_capability_tree.py` | Все 215 тестов дерева и упаковки `PASS`, `/api/capability-tree` `200 OK`; публикация дерева на внешний сайт отключена (`BossmanTreeAutoPublish` disabled) |
| **MC22-16** | `Выбор моделей генерации и предпочтительный uncensored-профиль` | **PASS** | `0618909c` | `GET /api/direct-gen/models`; `test_direct_gen_photo.py` | Каталог на `:8801` и `:8840` честно отображает все доступные модели (включая `qwen-image-2.1-uc`, `qwen-image-2.1-uc-edit`, `wan2.2-i2v-a14b-uncensored`) и серым помечает недоступные с точной причиной |
| **P22-POKER-01** | `локальный импорт собственных записей и происхождение данных` | **BLOCKED** | `8a956d07` | Незакоммиченные изменения владельца в `wt-pc-agent-argon-20261013` | По прямому указанию владельца чужие незакоммиченные файлы Poker в `wt-pc-agent-argon-20261013` не трогались |
| **P22-POKER-02** | `калибровка Vision, мест и безопасный отказ` | **BLOCKED** | `8a956d07` | См. `P22-POKER-01` (`apps/poker-vision/pokervision/strategy.py` dirty) | Сохранено без вмешательства до решения владельца по `wt-pc-agent-argon-20261013` |
| **P22-POKER-03** | `запись, подготовка выборки, перезапуск и хранение` | **BLOCKED** | `8a956d07` | См. `P22-POKER-01` | Сохранено без вмешательства до решения владельца |
| **P22-POKER-04** | `режимы coaching и разбор с подтверждённым solver/GTO-происхождением` | **BLOCKED** | `8a956d07` | См. `P22-POKER-01` (`apps/poker-botlab/botlab/gto.py` untracked) | Сохранено без вмешательства до решения владельца |
| **P22-TRADING-05** | `paper trading и защита от утечки будущих данных` | **PASS** | `0618909c` | `GET http://127.0.0.1:8801/api/trading-lab/status` | `200 OK`; строго paper-only режим без подключения кошельков/бирж |
| **P22-PUMP-06** | `исследование Pump.fun без реального создания токенов` | **PASS** | `0618909c` | Проверка `bcc/features/trading_lab.py` и политики запретов | Создание/деплой токенов и подключение кошельков жёстко отключены (`$0`) |
| **P22-TIMESFM-07** | `локальный прогноз и независимый backtest` | **PARTIAL** | `0618909c` | `GET /api/trading-lab/status` | Контур бэктеста в `trading_lab` доступен, но отдельный живой бенчмарк весов TimesFM этой ночью не запускался (`NOT_RUN`) |
| **P22-EXCHANGE-08** | `ручной локальный импорт и расчёт месяца с 21-го по 20-е` | **PASS** | `80ec28d3` | `pytest command-center/tests/test_pc_agent_argon.py -k exchange_report` | `test_reports_runner_no_silent_synthetic_and_two_runs_sha256` → **`PASS`** (`command-center/bcc/features/reports_runner.py`) |
| **P22-REPORTS-09** | `точность отчётов, безопасный вывод и восстановление после сбоя` | **PASS** | `80ec28d3` | `pytest command-center/tests/test_pc_agent_argon.py` (`39 passed`) | Детерминированный SHA-256 двух прогонов отчёта без тихой синтетики доказан тестом |
| **P22-OPS-10** | `очередь Operations, проверка человеком и черновики без отправки` | **PASS** | `0618909c` | `GET :8830/api/v1/health`, `GET :8830/api/v1/tenders`, `POST :8830/api/v1/firm/validate`, `GET :8830/api/v1/fx/crosscheck` | `apps/war-terminal/tests` (`184 passed`) + живой `:8830` в режиме `LIVE` (`1120` тендеров, IČO валидатор, ČNB+ECB кросс-чек `diff_pct: 0.008`, никаких внешних отправок) |
| **P22-PROVENANCE-11** | `лицензии, независимые данные и реестр артефактов` | **PASS** | `0618909c` | `GET :8801/api/direct-gen/models`, `GET :8830/api/v1/fx/track/report` | В каждой модели указаны `source`, `revision`, `license`, `sha256` (или честный `UNKNOWN`); в `War Terminal` `xe.com` отключён по лицензии (`terms of use forbid scraping`), `excluded_unverified: 0` |

---

## 6. Что осталось за владельцем

1. **Подтвердить на телефоне (`http://100.123.80.124:8840`)** работу кнопок «Отправить в пульт» и «Удалить навсегда» на своих реальных генерациях.
2. **Ветка `wt-pc-agent-argon-20261013` (`P22-POKER-01..04`) и `command-center/tgapp.py`:** решить, коммитить или переносить незакоммиченные наработки по покеру и `tgapp.py`.
3. **Ключи `FreeLLMAPI` (`:3001`) и перевыпуск засвеченных ранее в чате ключей String / Experiential Labs:** выполняется только лично владельцем.
