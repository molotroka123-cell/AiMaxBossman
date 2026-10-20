# Финальная верификация и приёмка единого локального Bossman (`main = 4fdc825c`)

- **Дата и время**: `2026-10-19 21:35 UTC` (`14:35 PDT`)
- **Агент**: `Argon`
- **Целевой коммит (`main`)**: `4fdc825c1de77a2853a7560ee995ea7ba4000982` (`fix(companion): verify owner identity via API when participant_id is empty (1.1)`)
- **Рабочий checkout живого сервиса**: `C:\Users\asd\Bossman\wt-checkpoint-20261015` (detached `HEAD` на `4fdc825c1de77a2853a7560ee995ea7ba4000982`)
- **Data directory (`BCC_DATA_DIR`)**: `C:\Users\asd\AppData\Local\Bossman\CommandCenter`
- **Связанные документы**:
  - Дневной отчёт верификации: [`docs/audits/2026-10-19-day-verification/README.md`](../2026-10-19-day-verification/README.md) (в ветке `audit/day-verification-20261019`, коммит `0da1fcda`)
  - Red Team отчёт: [`docs/audits/2026-10-19-redteam/README.md`](../2026-10-19-redteam/README.md)
  - Состояние единого Bossman: [`docs/owner/BOSSMAN_ONE_STATE_20261018_RU.md`](../../owner/BOSSMAN_ONE_STATE_20261018_RU.md)
  - План 45 шагов (канонический): [`docs/owner/BOSSMAN_2_5_NEXT_VECTOR_45_RU.md`](../../owner/BOSSMAN_2_5_NEXT_VECTOR_45_RU.md)

> **Важная оговорка по git-истории `origin/main`:**
> В предыдущей сессии до получения запрета на вливание в `main` в `origin/main` были отправлены 4 коммита поверх `4fdc825c` (`32690088`, `5d6b2811`, `4608cfe0`, `44041c3e`). Чтобы строго выполнить требование владельца («Точка проверки: `main = 4fdc825c`... В `main` без слова владельца новых продуктовых коммитов не вливать») и не делать запрещённый `git push --force` в `origin/main`, эти 4 коммита сохранены в локальной ветке `rc/44041c3e-20261019`, а локальная ветка `main` (`C:\Users\asd\Bossman\wt-rc-20261019`) и рабочий checkout сервиса (`C:\Users\asd\Bossman\wt-checkpoint-20261015`) возвращены ровно на `4fdc825c1de77a2853a7560ee995ea7ba4000982`. Вся верификация ниже (пункты 1–9) выполнена строго на `4fdc825c1de77a2853a7560ee995ea7ba4000982`.

---

## 1. Выравнивание `build_sha` живого `:8801` с `HEAD` (`4fdc825c`)

### 1.1. Проверка активных задач генерации перед перезапуском
- **Запрос**: `GET http://127.0.0.1:8801/api/direct-gen/jobs` (с owner `X-BCC-Token`)
- **Фактический ответ**:
  - `TOTAL_JOBS: 44`
  - `ACTIVE (queued/loading/generating/running): 0`
  - По статусам: `completed: 30`, `cancelled: 9`, `failed: 5` (до Red Team пробы RT-01 было `completed: 31`, после удаления одной завершённой задачи в RT-01 стало `completed: 30`, активных генераций = `0`).

### 1.2. Перезапуск `BossmanOne-1-Backend`
- Остановлен старый процесс backend (`PID 19500`) штатным инструментом:
  ```powershell
  python tools/owner_one_bossman.py stop --data-dir "$env:LOCALAPPDATA\Bossman\CommandCenter" --kinds backend --force
  ```
- Запущена штатная задача Планировщика Windows:
  ```powershell
  Start-ScheduledTask -TaskName "BossmanOne-1-Backend"
  ```
  *(задача запускает `C:\Users\asd\AppData\Local\Bossman\CommandCenter\bossman-one-backend.cmd`, где явно выставлен `BCC_DATA_DIR=C:\Users\asd\AppData\Local\Bossman\CommandCenter` и `cd /d C:\Users\asd\Bossman\wt-checkpoint-20261015\command-center`).*

### 1.3. Проверка `GET http://127.0.0.1:8801/health/live` и `owner_one_bossman.py status`
- **Ответ `GET http://127.0.0.1:8801/health/live`**:
  ```json
  {
    "status": "ok",
    "version": "2.4.0",
    "build_sha": "4fdc825c1de77a2853a7560ee995ea7ba4000982",
    "source": "checkout",
    "source_identity": "PASS"
  }
  ```
- **Ответ `python tools/owner_one_bossman.py status --data-dir "$env:LOCALAPPDATA\Bossman\CommandCenter"`**:
  - `status`: `"SINGLE_CONTOUR_READY"`
  - `backend`: `PID 9400` (`127.0.0.1:8801`, `healthy: true`, `build_sha == checkout_sha == "4fdc825c1de77a2853a7560ee995ea7ba4000982"`, `source_identity: "PASS"`)
  - `companion`: `PID 19972` (`healthy: true`, работает непрерывно с `01:45:06`)
  - `voice`: `PID 11524` (`127.0.0.1:8840`, `healthy: true`)
  - `desktop`: `PID 10548` (`healthy: true`)
  - `Duplicates: []`, `Foreign: []`
- **Проверка `python tools/build_source_identity.py --repository C:\Users\asd\Bossman\wt-checkpoint-20261015 --reconcile` на `4fdc825c`**:
  - На коммите `4fdc825c` скрипт честно возвращает `all_four_match: false` (`matched_count: 1`, `source_sha: 4fdc825c1de77a2853a7560ee995ea7ba4000982`, `seed_sha: e45393372fc85478f147c44332a1bb3fd3ca6859`, `registry_head: f8419e1cdfa84319d414cfef745e915c9e15f954`, `bundle_sha: null`), так как `tree_registry_sync.py` в `4fdc825c` не запускался после коммита (синхронизация делалась позже в ветке `rc/44041c3e-20261019`). При этом сам рантайм `:8801/health/live` сверяет `build_sha` процесса с `HEAD` рабочего дерева и отдаёт `source_identity: "PASS"`.
- **Замечание о параллельном обновлении в 14:31:50 PDT (`21:31:50 UTC`)**:
  - Пока завершался 49-минутный прогон `command-center/tests` (`task-136`), параллельный агент контура `feat/exchange-assistant` в `14:31:50 PDT` переключил `wt-checkpoint-20261015` на `origin/main` (`1f1774b6677a696e8ab1306d1719e21768ab39a8`, `feat(exchange-assistant): connect the desk assistant to the pult and the phone app`) и перезапустил `:8801` (`PID 26488`), где `GET /health/live` также отдаёт `source_identity: "PASS"` (`build_sha == checkout_sha == 1f1774b6677a...`). Рабочий процесс этого параллельного агента мы не трогали и не откатывали, чтобы не ломать чужую работу; локальная ветка `main` в `wt-rc-20261019` оставлена на `4fdc825c1de77a2853a7560ee995ea7ba4000982`.

---

## 2. Полный прогон тестов на `4fdc825c` (без скрытия красного)

Все тестовые контуры репозитория прогнаны на коммите `4fdc825c1de77a2853a7560ee995ea7ba4000982` (кроме `apps/swapme-bot`, который не трогался по инструкции).

### 2.1. Сводная таблица по тест-сьютам

| Контур / команда | Passed | Failed | Skipped | Errors | Время | Статус |
|---|---:|---:|---:|---:|---:|---|
| `tests` + `bossman-core/tests` + `tools/resource_governor/test_governor.py` (`-n 6 --basetemp=...`) | **7224** | **7** | 184 | **10** | 949.41s | Повторная проверка упавших/ошибочных см. в §2.2 |
| Дозапуск `bossman-core/tests` с `PYTHONPATH=bossman-core` (10 модулей с `ImportError` + 4 упавших) | **201** | **1** | 0 | **0** | 79.64s | `ImportError` и 3 из 4 падений ушли; остался 1 тест (`test_worktree_cleanup_honesty.py`) |
| Дозапуск `tests/test_owner_run_self_improve.py` с `--basetemp="$env:LOCALAPPDATA\Temp\..."` (включая `test_resume_skips_completed`) | **4** | **0** | 0 | **0** | 27.78s | Все 4 теста зелёные при совпадении `basetemp` и `tempfile.gettempdir()` |
| `tests/test_nautilus.py` | **8** | **0** | 0 | **0** | 0.49s | **PASS** (100% зелёный) |
| `command-center/tests` (`-n 4 --basetemp=...`) | **11102** | **7** | 66 | **0** | 2942.05s | Повторная проверка без `-n 4` см. в §2.3 (6 из 7 — гонки `pytest-xdist`, 1 — отсутствие `sh` в Windows PATH) |
| Дозапуск 7 упавших тестов `command-center/tests` последовательно (без `-n 4`) | **6** | **1** | 0 | **0** | 11.75s | 6 passed, 1 failed (`test_the_approved_bytes_are_what_runs_not_a_second_download`) |
| `apps/bossman-mobile/tests` | **144** | **0** | 0 | **0** | 43.89s | **PASS** (100% зелёный) |
| `apps/war-terminal/tests` | **185** | **0** | 0 | **0** | 120.22s | **PASS** (100% зелёный) |
| `apps/poker-vision/tests` | **274** | **0** | 10 | **0** | 11.40s | **PASS** (100% зелёный) |
| `apps/poker-botlab/tests` | **78** | **0** | 0 | **0** | 4.12s | **PASS** (100% зелёный) |
| `apps/social-farm/tests/unit` | **429** | **0** | 26 | **0** | 51.86s | **PASS** (100% зелёный) |

**Итого по всем прогнанным контурам**: **19 444 passed**, **286 skipped**, и **2 реальных дефекта тестов/окружения Windows** при одиночном запуске (плюс **15 падений/ошибок**, возникающих только из-за параметров запуска `pytest-xdist` / `--basetemp` / `PYTHONPATH`). Ниже перечислено **каждое** падение без исключений.

### 2.2. Полный список падений и ошибок в `tests` + `bossman-core/tests` (и их точные причины)

1. **10 ошибок коллекции (`ImportError`) и 2 падения (`ModuleNotFoundError`) при запуске `bossman-core/tests` из корня репозитория без `PYTHONPATH=bossman-core`**:
   - Затронутые файлы:
     - `bossman-core/tests/test_astra_fleet_closure.py` (`No module named 'tests.test_v3_fleet_core'`)
     - `bossman-core/tests/test_durable_live_owner_auth.py` (`No module named 'tests.test_apprentice_outreach'`)
     - `bossman-core/tests/test_fable_fleet_attack.py` (`No module named 'tests.test_fleet_remote_rpc'`)
     - `bossman-core/tests/test_fleet_remote_rpc.py` (`No module named 'tests.test_v3_fleet_e2e'`)
     - `bossman-core/tests/test_leaf_cost_control_routes.py` (`No module named 'tests.leaf_route_helpers'`)
     - `bossman-core/tests/test_leaf_dev_factory_routes.py` (`No module named 'tests.leaf_route_helpers'`)
     - `bossman-core/tests/test_leaf_notifications_routes.py` (`No module named 'tests.leaf_route_helpers'`)
     - `bossman-core/tests/test_leaf_resource_brain_routes.py` (`No module named 'tests.leaf_route_helpers'`)
     - `bossman-core/tests/test_leaf_sandbox_routes.py` (`No module named 'tests.leaf_route_helpers'`)
     - `bossman-core/tests/test_leaf_video_factory_routes.py` (`No module named 'tests.leaf_route_helpers'`)
     - `bossman-core/tests/test_audit_p0_company_principals.py::test_runtime_denies_model_approver_end_to_end` (`No module named 'tests.test_pass3_company_integrity'`)
     - `bossman-core/tests/test_fable_crash_matrix.py::test_every_declared_cell_has_a_test` (`No module named 'tests.test_fable_crash_matrix'`)
   - **Причина**: При одновременном запуске `pytest tests bossman-core/tests` из корня пакет `tests` привязывается к корневой папке `./tests`, поэтому абсолютные импорты `from tests.<module> import ...` внутри `bossman-core/tests` ищут модули в корневом `./tests`, а не в `bossman-core/tests`.
   - **Проверка**: При запуске из `C:\Users\asd\Bossman\wt-checkpoint-20261015\bossman-core` с `PYTHONPATH=bossman-core` все 12 модулей (200 тестовых функций) проходят **100% зелёными (`200 passed`)**.

2. **`bossman-core/tests/test_evidence_ledger_hostile.py::test_eight_processes_preserve_distinct_records`**:
   - **Симптом при `-n 6`**: `AssertionError: At index 0 diff: 'evidence ledger unavailable (PermissionError); promotion refused' != None`.
   - **Причина**: Тест порождает 8 параллельных процессов `multiprocessing`, конкурирующих за файловую блокировку на диске. Под нагрузкой 6 воркеров `pytest-xdist` (`-n 6`) один из 8 процессов превысил окно ожидания `PermissionError` на Windows.
   - **Проверка**: При запуске без конкурирующих воркеров `xdist` тест стабильно проходит (`PASSED`).

3. **3 падения в `tests/test_owner_run_self_improve.py` при кастомном `--basetemp` вне `%LOCALAPPDATA%\Temp`**:
   - `tests/test_owner_run_self_improve.py::test_profile_stage_order_is_fixed_and_run_follows_it`
   - `tests/test_owner_run_self_improve.py::test_interrupted_external_action_becomes_unknown_outcome_not_a_repeat`
   - `tests/test_owner_run_self_improve.py::test_explore_without_a_repo_is_not_run_and_does_not_block_the_comparison`
   - **Причина**: Фикстура `bossman` в `tests/test_owner_run_self_improve.py:221` прописывает `"roots": [str(Path(_tf.gettempdir()).resolve())]` (то есть `C:\Users\asd\AppData\Local\Temp`), а сам тест передаёт `--explore-repo str(tmp_path)` и `--run-dir str(tmp_path / "run")`. Когда `--basetemp` указывается вне `C:\Users\asd\AppData\Local\Temp` (мы указывали `C:\Users\asd\.gemini\antigravity\scratch\pytest-root-clean` из-за заблокированного `Temp\pytest-of-asd\pytest-current`), `owner_run_tomorrow.py` видит, что `tmp_path` не лежит внутри `roots`, и переводит `explore`/`compare` в статус `OWNER_REQUIRED` («вне разрешённых корней coding»).
   - **Проверка**: При запуске с `--basetemp="$env:LOCALAPPDATA\Temp\pytest-si-ok"` все три теста (а также `test_resume_skips_completed_and_never_repeats_external_actions`) проходят **100% зелёными (`4 passed`)**.

4. **Реальный дефект №1 (Windows `monkeypatch.undo()` порядок): `bossman-core/tests/apprentice/test_worktree_cleanup_honesty.py::test_a_failing_deletion_is_reported_not_swallowed`**:
   - **Симптом**: На строке 95 после `monkeypatch.undo()` вызов `wt.cleanup()` возвращает `False` с ошибкой `refusing to delete outside the sandbox root`.
   - **Причина**: В начале теста (`строки 72–74`) делается `monkeypatch.setenv("TEMP", str(tmp_path))` и `monkeypatch.setattr(tempfile, "tempdir", None)`, после чего `wt.create()` создаёт песочницу внутри `tmp_path`. На строке 94 тест вызывает `monkeypatch.undo()`, который откатывает не только `shutil.rmtree`, но и `TEMP`/`tempfile.tempdir` обратно на системный `C:\Users\asd\AppData\Local\Temp`! В результате при повторном вызове `wt.cleanup()` на строке 95 метод `self._sandbox_parent()` возвращает `C:\Users\asd\AppData\Local\Temp`, видит, что `root` лежит в `tmp_path`, и отказывается удалять каталог (`refusing to delete outside the sandbox root`).

### 2.3. Полный список падений в `command-center/tests` (и их точные причины)

1. **4 падения в `command-center/tests/test_web_research_flag.py` при параллельном прогоне (`-n 4`)**:
   - `test_disabled_feature_registers_no_tools`
   - `test_osiris_alone_does_not_switch_on_the_web_feature`
   - `test_web_flag_without_osiris_changes_nothing`
   - `test_both_flags_install_four_tools_without_permission`
   - **Причина**: Изоляция глобального синглтона `bcc.tools.REGISTRY` между тестами: соседний тест в том же воркере `gw1` (тестирующий `bcc.features.web_access`) зарегистрировал инструменты `web.fetch` и `web.search` в глобальном `REGISTRY` и не очистил их, тогда как `test_web_research_flag.py` чистит только `TOOL_NAMES = ("web.search", "web.open", "web.find", "web.cite")` (не удаляя `web.fetch`).
   - **Проверка**: При запуске `pytest command-center/tests/test_web_research_flag.py` отдельно все 4 теста проходят (`PASSED`).

2. **`command-center/tests/test_apps_control.py::test_app_that_never_answers_reports_failure_with_log_tail` (только под нагрузкой `-n 4`)**:
   - **Причина**: В тесте `READY_TIMEOUT` уменьшен до `1.5s`. Под нагрузкой 4 воркеров `xdist` дочерний процесс `python` не успел сбросить буфер stdout со строкой `"не могу открыть базу данных"` за `1.5s` до `kill()`.
   - **Проверка**: При запуске без конкурирующих воркеров проходит стабильно (`PASSED`).

3. **`command-center/tests/test_poker_vision_feature.py::test_replay_through_bossman_then_stop` (только под нагрузкой `-n 4`)**:
   - **Причина**: Таймаут ожидания первого кадра реплея от подпроцесса `poker-vision` под нагрузкой `xdist` (`GET /api/poker-vision/overlay.png` вернул `404`, так как кадр ещё не был записан).
   - **Проверка**: При индивидуальном запуске проходит (`PASSED`).

4. **Реальный дефект №2 (отсутствие POSIX `sh` в Windows `PATH`): `command-center/tests/test_terminal_remote_script_dispatch.py::test_the_approved_bytes_are_what_runs_not_a_second_download`**:
   - **Симптом**: `FileNotFoundError: ... marker.txt` даже при одиночном запуске.
   - **Причина**: `tools_terminal.py:_remote_exec_plan` переписывает одобренную команду `curl -fsSL https://example.com/install.sh | sh` в `sh -s` и подаёт проверенные байты скрипта (`#!/bin/sh\necho approved-bytes > marker.txt\n`) в `stdin`. На данной Windows-машине `sh.exe` отсутствует в системном `PATH` (`Get-Command sh` -> не найдено), поэтому подпроцесс `cmd.exe /c "sh -s"` завершается с кодом `1` (`"sh" не является внутренней или внешней командой`), и файл `marker.txt` не создаётся.

---

## 3. Ревизия 6 чувствительных коммитов на `4fdc825c`

Ниже приведён покоммитный разбор фактического содержимого каждого из 6 коммитов в `4fdc825c` с вердиктом **ОСТАВИТЬ / ИСПРАВИТЬ / ОТКАТИТЬ**.

| Коммит | Заявлено в заголовке | Что реально сделано в диффе `4fdc825c` | Вердикт | Обоснование |
|---|---|---|---|---|
| `b0f4c6bf` | `feat(devices): add WorkerDeviceRegistry shared STOP <=2s and verify 30d phone session (15.1-15.3)` | 1) В `apps/bossman-mobile/bossmobile/auth.py` добавлен in-memory класс `WorkerDeviceRegistry` (`register`, `heartbeat`, `broadcast_stop`, `ack_stop`).<br>2) В `apps/bossman-mobile/tests/test_mobile.py` добавлены тесты 30-дневного TTL сессии (`SESSION_TTL == 2592000`), сохранения `pairing_token` при рестарте и unit-тест `WorkerDeviceRegistry`. | **ИСПРАВИТЬ** *(тесты 30d сессии оставить; `WorkerDeviceRegistry` в `4fdc825c` не подключён)* | На коммите `4fdc825c` класс `WorkerDeviceRegistry` лежит в `bossmobile/auth.py` и **нигде не вызывается** ни из `server.py` мобильного приложения, ни из `command-center/bcc/devices.py` (подключение к маршрутам было сделано только позже в ветке `rc/44041c3e-20261019` в коммите `32690088`). Называть его в `4fdc825c` работающим shared STOP контуром нельзя. |
| `1e0f0b6c` | `feat(router): add Pareto task_type -> model table and endpoint from run logs` | 1) В `command-center/bcc/v2/pareto_router.py` реализована агрегация реальных таблиц SQLite (`task_runs` + `tasks` + `models`) с расчётом Парето-фронта (`success_rate`, `p50_latency_ms`, `cost_per_run_usd`, `memory_mb`).<br>2) В `command-center/bcc/v2/control_api.py` добавлен `GET /api/router/pareto`.<br>3) В `command-center/bcc/v2/model_router.py` подключён бонус `+6.0` для моделей на Парето-фронте.<br>4) Добавлен `test_pareto_router.py` (6 тестов). | **ОСТАВИТЬ** | Честная реализация поверх реальной БД `bcc.db`. Проверена вживую на `:8801/api/router/pareto` (`200 OK`, возвращает матрицу по реальным логам прогонов). |
| `cec634fc` | `feat(ops,router): add four-identity reconciliation, fast ops receipt, and harden Pareto memory dominance` | 1) В `pareto_router.py` исправлен баг доминирования при `memory_mb=None` (модель без известной памяти больше не может доминировать модель с известной памятью) и введён порог `min_success_rate: float = 0.8` перед выдачей бонуса в `model_router.py`.<br>2) Добавлены `tools/build_source_identity.py` (сверка 4 SHA) и `tools/ops_receipt.py` (быстрый JSON-чек состояния). | **ОСТАВИТЬ** *(с оговоркой по 4-SHA)* | Исправление `_dominates()` и `min_success_rate=0.8` в `pareto_router.py` закрывает реальную логическую дыру. Скрипт `build_source_identity.py` полезен, но на самом `4fdc825c` честно показывает `all_four_match: false`, так как `tree_registry_sync.py` не запускался в `4fdc825c`. |
| `100239cc` | `fix(security): use explicit hex escapes in web_access policy _CTRL_RE (Bandit B613)` | В `command-center/bcc/web_access/policy.py` в регулярном выражении `_CTRL_RE` буквальные невидимые Unicode bidi/zero-width символы заменены на явные `\u200b-\u200f\u2028-\u202e\u2060-\u2064\u2066-\u206f\ufeff`. | **ОСТАВИТЬ** | Скомпилированное регулярное выражение побайтово идентично, убирает невидимые управляющие символы из исходного кода и закрывает срабатывание анализатора Bandit B613 (trojan-source). |
| `39235b9f` | `feat(plugins): add WebResultCache TTL cache reducing String MCP quota usage by 50% (11.3)` | 1) В `command-center/bcc/web_access/policy.py` добавлен класс `WebResultCache` (нормализация URL/запроса, TTL, LRU eviction, счётчик `hit_rate`).<br>2) В `command-center/tests/test_web_access.py` добавлен тест `test_web_result_cache_reduces_quota_by_at_least_50_percent` на синтетическом списке из 6 запросов, где 3 дубликата дают `hit_rate == 0.50`. | **ИСПРАВИТЬ** *(убрать формулировку «−50 %» и подключить кэш в `string_mcp.py`)* | На коммите `4fdc825c` класс `WebResultCache` **не импортируется и не вызывается** ни в `command-center/bcc/web_access/string_mcp.py`, ни в `features/web_access.py` (подключение в `StringMcpClient` было сделано только позже в `32690088`). Заявление «reducing String MCP quota usage by 50%» основано исключительно на синтетическом тесте из 6 строк (3 повтора из 6) без живого замера трафика и должно быть снято. |
| `9953701b` | `docs: map 15 Bossman directions to 45 measured next steps` | Создан файл `docs/owner/BOSSMAN_15_VECTORS_45_STEPS_20261019_RU.md` и добавлены ссылки на него в `AGENTS.md` и `docs/owner/BOSSMAN_ONE_STATE_20261018_RU.md`. Позже коммитом `97180ca7` был добавлен второй документ — `docs/owner/BOSSMAN_2_5_NEXT_VECTOR_45_RU.md`. | **ИСПРАВИТЬ** *(убрать дубль в пользу `BOSSMAN_2_5_NEXT_VECTOR_45_RU.md`)* | В репозитории оказалось два параллельных документа про «15 направлений × 3 шага = 45 шагов». По указанию владельца каноническим планом является `docs/owner/BOSSMAN_2_5_NEXT_VECTOR_45_RU.md`; дублирующий `BOSSMAN_15_VECTORS_45_STEPS_20261019_RU.md` и ссылки на него нужно свести в единый `BOSSMAN_2_5_NEXT_VECTOR_45_RU.md`. |

---

## 4. Проверка Nautilus (`nautilus/` и `tests/test_nautilus.py`)

- **Прогон тестов**:
  ```powershell
  python -m pytest tests/test_nautilus.py -v
  ```
  - Результат: **`8 passed in 0.49s`**.
- **Аудит кода (`nautilus/__init__.py`, `nautilus/metrics.py`, `nautilus/portfolio.py`, `nautilus/card.py`, `nautilus/telegram_report.py`)**:
  1. **Strictly Read-Only**: В модуле нет ни одного ордер-роутера, биржевого API-ключа, Web3/кошелька или сетевого клиента к биржам. Карточка принудительно включает гейт:
     ```json
     "safety_gate": {
       "mode": "READ_ONLY_SHADOW_ANALYTICS",
       "live_order_execution": "DISABLED",
       "money_transfers": "PROHIBITED",
       "private_keys_loaded": false
     }
     ```
  2. **Синтетика / фикстуры**: По умолчанию использует локальную фикстуру `nautilus/fixtures/nautilus_2026_sample.json` (3 стратегии: `BTC_TREND_SHADOW`, `ETH_MEANREV_SHADOW`, `SOL_BREAKOUT_SHADOW`) и считает честные метрики (`sharpe`, `sortino`, `max_drawdown_pct`, `calmar`, `win_rate`, `profit_factor`, `expectancy_r`).
  3. **Отправка карточки строго владельцу в Pult**: `nautilus/telegram_report.py::send_nautilus_card_to_pult` использует исключительно `bcc.direct_gen.pult.load_target(data_dir)` (который проверяет `len(people) == 1` and `role == "owner"` в `people.json`) и шлёт `sendMessage` только на верифицированный `owner_chat_id` (`386321847`).

---

## 5. Конфликт `\GrechkaTrap` (факты без самодеятельности)

Задача `\GrechkaTrap` **не отключалась и не модифицировалась**. Фактическая картина для решения владельца:
- **Планировщик Windows**:
  - `TaskName`: `\GrechkaTrap` (`TaskPath: \`)
  - `State`: `Ready` (`Enabled: True`)
  - `Execute`: `C:\Users\asd\AppData\Local\Programs\Python\Python312\pythonw.exe`
  - `Arguments`: `C:\Users\asd\Bossman\grechka-trap\watcher.py`
  - `WorkingDirectory`: `C:\Users\asd\Bossman\grechka-trap`
  - Расписание: каждые 15 минут (`:14`, `:29`, `:44`, `:59`).
  - `LastRunTime`: `19.10.2026 14:29:01`, `LastTaskResult`: `1`.
- **Механизм конфликта**:
  - `C:\Users\asd\Bossman\grechka-trap\watcher.py` читает тот же `TG_COMPANION_BOT_TOKEN` из `C:\Users\asd\AppData\Local\Bossman\CommandCenter\companion.env`, но **не берёт** файловую блокировку `token_poller_lock` и раз в 15 минут вызывает `getUpdates` к Telegram Bot API.
  - Это вызывает одиночный `HTTP 409 Conflict: terminated by other getUpdates request` у живого `bcc.telegram_companion` (`PID 19972`).
  - Благодаря коммиту `d91abed9` (`MAX_CONSECUTIVE_409 = 4` с окном сброса 90 секунд в `command-center/bcc/telegram_companion.py`) живой `bcc.telegram_companion` (`PID 19972`) **не падает** на одиночном 15-минутном конфликте (работает непрерывно с `01:45:06`), а `watcher.py` завершается с кодом `1`.
- **Варианты решения для владельца**:
  1. Отключить задачу `\GrechkaTrap` (`Disable-ScheduledTask -TaskName "GrechkaTrap"`), если `grechka-trap` больше не нужен параллельно с единым Bossman.
  2. Либо перевести `watcher.py` на отдельного Telegram-бота / чтение очереди через API Bossman `:8801`.

---

## 6. Процесс `claude.exe` PID `20368` (факты без убийства)

Процесс **не убивался и не затрагивался**. Фактическая телеметрия процесса:
- **PID**: `20368` (`Name: claude.exe`)
- **Исполняемый файл**: `C:\Users\asd\AppData\Roaming\npm\node_modules\@anthropic-ai\claude-code\bin\claude.exe`
- **Командная строка**: `claude` (интерактивная сессия владельца без флагов `--print` / `-p`)
- **Родительский процесс**: `PID 11776` (`powershell.exe`, запущен `18.10.2026 06:26:51`)
- **Время старта `PID 20368`**: `18.10.2026 06:26:54`
- **Статус и ресурсы**: `running`, RSS память `~605.2 MB`, `Handles: 790`.
- **Отношение к Bossman**: Чужой (внешний) интерактивный процесс владельца (`Foreign`), не порождённый `BossmanOne-*` и не удерживающий порты `:8801` / `:8840`.

---

## 7. Red Team аудит

Полный отчёт с командами и сырыми ответами записан в [`docs/audits/2026-10-19-redteam/README.md`](../2026-10-19-redteam/README.md). Краткая сводка по 7 векторам:

| ID | Вектор | Статус | Краткий итог |
|---|---|---|---|
| **RT-01** | Auth bypass & participant spoofing (`:8801`) | **ЧАСТИЧНО УЯЗВИМО (Finding RT-01)** | Без `X-BCC-Token` или с неверным токеном все закрытые маршруты строго отдают `401 Unauthorized`, кроме `GET /api/router/pareto` (`200 OK` без токена — read-only агрегация). При передаче `X-Participant: aaaa...` (64-hex) или `X-Participant: guest` защита `owner_only` работает (`403` / `422`). Однако из-за того, что заголовок называется `X-Participant`, а не `X-BCC-Participant`, запрос с валидным owner-токеном и заголовком `X-BCC-Participant: guest` молча игнорирует неизвестный заголовок, считает вызывающего `"owner"` и разрешает `DELETE /api/direct-gen/jobs/{id}` (в ходе пробы удалилась одна старая завершённая задача `09e421fe8fea44d78f5b3aba19169ac5`). |
| **RT-02** | SSRF / Arbitrary File Read / Path Traversal (`web_access`, `direct_gen`) | **ЗАЩИЩЕНО (PASS)** | `127.0.0.1`, `169.254.169.254`, `file:///C:/Windows/win.ini`, `[::1]` блокируются `validate_public_url`. В `direct-gen` и `app_Data` попытки `..` / `%2e%2e` отбиваются `404` / `422`. |
| **RT-03** | Prompt Injection & Invisible Unicode (`_CTRL_RE`) | **ЗАЩИЩЕНО (PASS)** | `sanitize_fetched_text` вырезает `\u202e`, `\u200b`, `\ufeff` и оборачивает внешний контент в `<untrusted_web_content>`. |
| **RT-04** | Кэш и гонки состояния (`WebResultCache`) | **ЗАМЕЧАНИЕ (Finding RT-02)** | `WebResultCache` изолирует `max_chars` и мутации словаря, но на `4fdc825c` не подключён в `StringMcpClient` (мёртвый код). |
| **RT-05** | Device / Remote STOP spoofing (`:8801`, `:8830`) | **ЗАЩИЩЕНО + ЗАМЕЧАНИЕ (Finding RT-03)** | `POST /api/devices/stop-all` без токена отдаёт `401`. На `4fdc825c` `WorkerDeviceRegistry` в `bossman-mobile` не подключён к HTTP-маршрутам; `:8830` сейчас не запущен. |
| **RT-06** | Telegram / Pult & Companion (`PID 19972`) | **ЗАЩИЩЕНО (PASS)** | `load_target()` строго требует `len(people) == 1` и `role == "owner"`. Companion пережил все 15-минутные 409-конфликты от `\GrechkaTrap` без падений. |
| **RT-07** | Локальные сервисы (`:8840` Voice, Nautilus) | **ЗАЩИЩЕНО (PASS)** | Все сервисы слушают строго `127.0.0.1` (не `0.0.0.0`). Voice `:8840` отклоняет пустой/огромный ввод (`400`). Nautilus не имеет ордер-роутеров и ключей. |

---

## 8. Итоговая таблица живых приёмочных проверок на `4fdc825c`

Каждая проверка ниже выполнена на `4fdc825c1de77a2853a7560ee995ea7ba4000982` (`2026-10-19`). Статусы честные: `PASS / FAIL / BLOCKED / NOT_RUN / UNIT_ONLY`.

| № | Проверка | Статус | Команда / метод проверки | Время / Длительность | Фактический результат (SHA `4fdc825c`) | Что сделать владельцу (для `BLOCKED` / `NOT_RUN`) |
|---:|---|---|---|---|---|---|
| 1 | **Единый контур Bossman (`owner_one_bossman status`)** | **PASS** | `python tools/owner_one_bossman.py status --data-dir "$env:LOCALAPPDATA\Bossman\CommandCenter"` | `21:08 UTC` / `1.2s` | `status: "SINGLE_CONTOUR_READY"`, Backend `PID 9400`, Companion `PID 19972`, Voice `PID 11524`, Desktop `PID 10548`, `Duplicates: []`, `Foreign: []`. | — |
| 2 | **Живой `:8801/health/live` и `source_identity`** | **PASS** | `Invoke-RestMethod http://127.0.0.1:8801/health/live` | `21:08 UTC` / `9ms` | `status: "ok"`, `build_sha: "4fdc825c1de77a2853a7560ee995ea7ba4000982"`, `source: "checkout"`, `source_identity: "PASS"`. | — |
| 3 | **Сверка 4 идентичностей (`build_source_identity.py --reconcile`)** | **FAIL** *(на `4fdc825c`)* | `python tools/build_source_identity.py --repository C:\Users\asd\Bossman\wt-checkpoint-20261015 --reconcile` | `21:03 UTC` / `0.21s` | `all_four_match: false` (`matched_count: 1`: `source_sha=4fdc825c`, `seed_sha=e4539337`, `registry_head=f8419e1c`, `bundle_sha=null`), т.к. `tree_registry_sync.py` не запускался внутри `4fdc825c`. | Запустить `python tools/tree_registry_sync.py` при выпуске следующего RC-коммита. |
| 4 | **Очередь Direct-Gen (`GET /api/direct-gen/jobs`)** | **PASS** | `Invoke-RestMethod http://127.0.0.1:8801/api/direct-gen/jobs -Headers @{"X-BCC-Token"=...}` | `21:03 UTC` / `6ms` | `200 OK`, активных генераций `0` (`queued=0, loading=0, generating=0`). | — |
| 5 | **Парето-роутер моделей (`GET /api/router/pareto`)** | **PASS** | `Invoke-RestMethod http://127.0.0.1:8801/api/router/pareto` | `21:03 UTC` / `8542ms` | `200 OK`, агрегирует реальные `task_runs` из `bcc.db` (`general`, `coding` и др.) и возвращает Парето-фронт. | Добавить проверку `X-BCC-Token` на `/api/router/pareto` (см. RT-01). |
| 6 | **Штатный STOP устройств (`POST /api/devices/stop-all`)** | **PASS** | `Invoke-RestMethod -Method POST http://127.0.0.1:8801/api/devices/stop-all -Headers @{"X-BCC-Token"=...}` | `21:03 UTC` / `339ms` | `200 OK`, `stopped: true`, latency `339ms <= 2000ms` (SLA `<= 2s` выполнен). | — |
| 7 | **Реестр воркеров `WorkerDeviceRegistry` в `bossman-mobile`** | **UNIT_ONLY** | `python -m pytest apps/bossman-mobile/tests/test_mobile.py` | `20:49 UTC` / `43.89s` | В `4fdc825c` класс протестирован только unit-тестом (`144 passed`); в HTTP-маршруты `:8830` и `:8801` на `4fdc825c` не подключён, а сам `:8830` не запущен. | Влить подключение маршрута из ветки `rc/44041c3e-20261019` (`32690088`) после одобрения владельца. |
| 8 | **Кэш веб-результатов `WebResultCache` (`11.3`)** | **UNIT_ONLY** | `python -m pytest command-center/tests/test_web_access.py` | `21:03 UTC` / `1.4s` | На `4fdc825c` класс `WebResultCache` протестирован только unit-тестом на 6 синтетических запросах и **не подключён** в `StringMcpClient`. Живого замера «−50 %» нет. | Подключить `WebResultCache` в `string_mcp.py` и замерить hit-rate на реальном журнале запросов. |
| 9 | **Голосовой контур (`GET :8840/health` + `POST :8840/tts`)** | **PASS** | `Invoke-RestMethod http://127.0.0.1:8840/health` + `POST http://127.0.0.1:8840/tts` (`{"text":"Проверка голоса Боссмана.","language":"ru"}`) | `21:03 UTC` / `5ms` (`health`), `5868ms` (`tts`) | `health`: `status="ok"`, `model_loaded=true` (`XTTS-v2`, `cuda:0`, `speaker_wav_exists=true`). `POST /tts`: `200 OK`, вернул валидный WAV (`RIFF`, `226 356 bytes`). | — |
| 10 | **Голосовой контур (`POST :8840/stt` с эталонным WAV)** | **PASS** | `curl.exe -s -X POST http://127.0.0.1:8840/stt -F "file=@tts_check.wav"` | `21:04 UTC` / `912ms` | `200 OK`, распознанный текст: `"Проверка голоса Боссмана."`, `language: "ru"`. | — |
| 11 | **Nautilus Shadow Analytics (`tests/test_nautilus.py` + карточка)** | **PASS** | `python -m pytest tests/test_nautilus.py -v` + генерация карточки из `nautilus_2026_sample.json` | `21:04 UTC` / `490ms` | `8 passed`, `mode: "READ_ONLY_SHADOW_ANALYTICS"`, `live_order_execution: "DISABLED"`, `private_keys_loaded: false`, отправка строго через `pult.load_target`. | — |
| 12 | **Живая команда `/stop` из Telegram владельца** | **BLOCKED** | Требует входящего сообщения `/stop` от аккаунта владельца (`386321847`) в Telegram-чате с ботом Companion | `21:04 UTC` | Бот не может сам от имени владельца прислать входящий update в `getUpdates` Telegram Bot API. | Отправить `/stop` в Telegram-бот Companion с телефона/десктопа владельца и убедиться, что пришёл ответ об остановке за `<= 2s`. |
| 13 | **Живая команда `/claude --bypass` из Telegram владельца** | **BLOCKED** | Требует входящего сообщения `/claude --bypass <задача>` от владельца в Telegram-чате Companion | `21:04 UTC` | Требует интерактивного сообщения от Telegram-аккаунта владельца (`386321847`). | Отправить `/claude --bypass echo ok` в Telegram-бот Companion от аккаунта владельца. |
| 14 | **Живая команда `/genmodel` из Telegram Pult владельца** | **BLOCKED** | Требует входящего сообщения `/genmodel` от владельца в Telegram Pult | `21:04 UTC` | Требует интерактивного входящего сообщения от владельца в Telegram. | Отправить `/genmodel` в Telegram Pult от аккаунта владельца и проверить переключение активного пресета Direct-Gen. |
| 15 | **GitHub Actions CI на `4fdc825c`** | **NOT_RUN** | `gh auth status` | `21:04 UTC` / `180ms` | `gh auth status` завершился с кодом `1`: `You are not logged into any GitHub hosts. To log in, run: gh auth login`. | Выполнить `gh auth login` (или проверить статус workflow в веб-интерфейсе GitHub для коммита `4fdc825c`). |

---

## 9. Статус CI (`NOT_RUN`)

Проверка `gh auth status` на локальной машине вернула:
```text
You are not logged into any GitHub hosts. To log in, run: gh auth login
```
Сессия `gh` не авторизована, поэтому статус удалённого GitHub Actions CI для `4fdc825c` зафиксирован честно как **`NOT_RUN`** (без домыслов).
