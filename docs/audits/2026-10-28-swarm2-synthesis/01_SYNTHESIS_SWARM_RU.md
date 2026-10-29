# Фаза 1 — Сводка Роя (Реестр утверждений, Сверка с кодом, Единая таблица статусов, Решения владельца, Красные тесты)

**Дата:** 28.10.2026  
**Ветка:** `main` (схождение `origin/main` `f5b05cd1`, `codex/exec-swarm-20261028` `893bf9e8`, `orch/dev1-kimi/S1` `f02a5aab`, `orch/dev2-glm/S2` `908e6a99` и фиксов Фазы 2)  
**Контур:** `MASTER_PROMPT_2_SYNTHESIS_CODING_DOCS_SWARM_RU.md`

---

## 1. Реестр утверждений (Claims Registry за 26–28.10.2026)

| ID | Утверждение / Источник | Заявленный ранее статус | Фактический статус после сверки с кодом (28.10) | Доказательство в коде / Тест / Артефакт |
|---|---|---|---|---|
| **CLM-01** | Надстройка Bossman 2.0 (`/api/v20/status`, `/api/v20/memory/audit`, `/api/v20/memory/sync`, `/api/v20/security/self-test`) работает в живом `:8801` | `PASS` | **VERIFIED (`PASS`)** | `command-center/bcc/features/bossman_20.py`, `test_bossman_20_superstructure.py` (`16/16 PASS`) |
| **CLM-02** | Память Obsidian (`C:\Users\asd\Bossman\audit-memory`) очищена от синтетического мусора и синхронизирована с Bossman 2.0 | `PASS` | **VERIFIED (`PASS`)** | 68 канонических `.md` заметок, `0` синтетических файлов `doc_*.md`, плюс импортирован `2026-10-28-Bossman-2.0-Swarm-Execution-Lessons.md` |
| **CLM-03** | Все мутирующие маршруты `/api/pc-agent/*` (кроме `/stop`) закрыты для роли `agent` и требуют `owner` | `PASS` (в `893bf9e8`) | **VERIFIED (`PASS`)** | `command-center/bcc/api.py`, `command-center/bcc/features/pc_agent.py`, `test_exec_swarm_regressions.py` + `test_swarm2_coding_fixes.py` |
| **CLM-04** | `POST /api/pc-agent/self-modify` с JSON `approved=True` проверяет реальную квитанцию одобрения владельца и остаётся fail-closed без OS-песочницы | `PARTIAL` (остаток в `FIVE_FINDINGS_RU.md` #2) | **VERIFIED (`PASS` — закрыто в Фазе 2)** | `command-center/bcc/features/pc_agent.py:6352-6415`, `test_swarm2_coding_fixes.py::test_self_modify_json_approved_true_without_approval_receipt_is_demoted` |
| **CLM-05** | `SpendFirewall` блокирует платные и неизвестные тарифы, парсит URL-хост строго через `urlsplit` и привязывает одноразовый токен владельца к `provider`/`model` | `PARTIAL` (остаток в `HANDOFF_SWARM_2_RU.md` #2) | **VERIFIED (`PASS` — закрыто в Фазе 2)** | `command-center/bcc/security/spend_firewall.py`, `test_spend_firewall_free_gate.py`, `test_swarm2_coding_fixes.py::test_spend_firewall_token_bound_to_provider_and_model_blocks_mismatch` |
| **CLM-06** | `cloud.ask` учитывает расходы всех статусов (`ok`, `empty`, `error`, `billing_unknown`), читает весь `audit.jsonl` без усечения на 2000 строк и уходит в fail-closed при повреждении журнала | `PARTIAL` (не было закоммичено в `893bf9e8`) | **VERIFIED (`PASS` — сведено и проверено в Фазе 2)** | `command-center/bcc/features/tools_cloud.py:187-247`, `test_exec_swarm_regressions.py` (`test_cloud_budget_corrupt_audit_fails_closed`) |
| **CLM-07** | Windows Job Object в `pc.run_command` убивает процесс до `resume()`, если `_win_job_attach` не удался, и уничтожает дерево дочерних процессов при `STOP`/таймауте | `PASS` | **VERIFIED (`PASS` для `pc.run_command`)** | `command-center/bcc/v2/terminal_control.py`, `test_run_command_job_object.py` (`2/2 PASS`), `test_exec_swarm_regressions.py` |
| **CLM-08** | PowerShell-команды с `-EncodedCommand`, backtick-обфускацией, `iex` и `DownloadString` блокируются как `T3` (`deny`) на уровне `_classify_run_command` | `PARTIAL` (`HANDOFF_SWARM_2_RU.md` #4) | **VERIFIED (`PASS` — закрыто в Фазе 2)** | `command-center/bcc/features/pc_agent_policy_cmds.py`, `pc_agent_policy_guards.py`, `test_swarm2_coding_fixes.py` |
| **CLM-09** | Слепой турнир `Base Qwen3 vs Defensive QLoRA` (`CYBER-QLORA-20261026`) выигран со счётом `0.95 vs 0.625` | Заявлялся `PASS` в `6674a66c` | **REVOKED (`NOT_MEASURED`)** | Отозван в `27cab320`: `docs/audits/2026-10-28-cyber-qlora-tournament/revocation.json` + `tests/test_qlora_tournament_integrity.py`. Честный статус: `NOT_MEASURED` до живого прогона весов. |
| **CLM-10** | Выполнение модельного Python-кода внутри `pc.self_modify` в изолированной OS-песочнице (Docker/WSL/bwrap) | `BLOCKED` / `NOT_RUN` | **HONEST FLAG (`NOT_RUN` / `verification_blocked_unisolated`)** | На хосте Windows 11 Docker/WSL-песочница не запущена (`probe_os_sandbox() == False`); контур честно остаётся fail-closed. |
| **CLM-11** | Аппаратные локальные рендеры `ComfyUI / Flux.1 / Wan2.1 / ACE-Step` | `PARTIAL` | **HONEST FLAG (`CODE_VERIFIED / LIVE_DAEMON_OFFLINE`)** | Графы и 7/7 гейтов верифицированы в `bcc/art_v2/` (`test_art_studio_v2_wunderkind.py`), но локальные демоны ComfyUI (`:8188`) и ACE-Step не запущены в фоне ради экономии VRAM/RAM. |
| **CLM-12** | 24-часовой непрерывный soak-прогон без рестартов | `NOT_RUN` | **HONEST FLAG (`NOT_RUN`)** | Запланирован в почасовом плане на 29.10.2026 (`07_TOMORROW_HOURLY_PLAN_20261029_RU.md`). |

---

## 2. Сверка с кодом (Code vs. Claims Reconciliation)

1. **`command-center/bcc/api.py` и `command-center/bcc/features/pc_agent.py`:**
   - В `api.py` правило авторизации для `agent` пропускает только `/api/pc-agent/stop`.
   - В `pc_agent.py` (`api_pc_agent_self_modify`) добавлена строгая сверка `approval_id` с `svc.approvals.get(aid)` (`status == "approved"`, `tool in {"pc.self_modify", "self_modify"}`); без неё `approved` принудительно сбрасывается в `False`, а в журнал пишется `unverified_json_approved_ignored`.
   - В `pc_agent.py` (`_execute_self_modify_step` → `health_check`) исправлена ошибка, из-за которой при `rb_res["ok"] == False` возвращался `ok: True`.
2. **`command-center/bcc/security/spend_firewall.py` и `command-center/bcc/economy_orchestrator.py`:**
   - `SpendFirewall.is_free_model` разбирает URL через `urlsplit` (защита от `http://127.0.0.1@evil.example` и `http://127.0.0.1.evil.example`).
   - `SpendFirewall.issue_one_time_owner_token` и `register_owner_token` поддерживают привязку `allowed_provider` и `allowed_model`; при несовпадении выбрасывается `PaidCallBlockedError` (`token_scope_mismatch`).
   - `EconomyOrchestrator.route` и `execute_cascade` интегрированы со `SpendFirewall` в режиме `strict_spend_firewall=True`.
3. **`command-center/bcc/features/tools_cloud.py`:**
   - `_read_audit` читает все строки `cloud_ask/audit.jsonl` и валидирует каждую строку (`ts`, конечный неотрицательный `cost_usd`). При любой повреждённой строке `budget_state` возвращает `remaining_usd: 0.0`, `billing_unknown: True`, `audit_unavailable: True`.
4. **`tools/run_cyber_qlora_holdout_tournament.py` и `docs/audits/2026-10-28-cyber-qlora-tournament/revocation.json`:**
   - Синтетическая таблица оценок удалена; скрипт возвращает `status: "NOT_MEASURED"`, пока не передан реальный evaluator с живыми моделями.

---

## 3. Единая таблица статусов по 15 веткам Bossman OS

| № | Ветка (`tree-registry.json`) | Код в `main` | Автотесты | Живой контур / Доказательство | Честный статус | Что остаётся за флагом |
|---|---|---|---|---|---|---|
| **1** | **Jeff (`jeff`)** | `telegram_companion/stars.py`, `chat.py`, `bossman_20.py` | `test_mobile_dag_devices_upgrade.py` (`PASS`) | Живой `:8801`, двухфазный резерв/возврат Stars (`BL-121` закрыт) | **PASS** | Живой внешний платёж Stars в прод-боте (`NOT_RUN` по правилу $0) |
| **2** | **Mobile (`mobile`)** | `apps/bossman-mobile/bossmobile/api.py`, `ui/app.js` | `apps/bossman-mobile/tests/test_mobile.py` + `test_mobile_dag_devices_upgrade.py` (`PASS`) | Живой `:8840` (`screen-stream`, `touch-preview`, `voice/turn`, `1-tap rollback`, `blast-radius`) | **PASS** | Полевые тесты LTE/WireGuard вне LAN (`NOT_RUN`) |
| **3** | **Apps (`apps`)** | `bcc/v2/app_passport.py`, `tools/site_app.py`, `BossBlocks WebGL` | `test_site_app_tool.py`, `test_mobile_dag_devices_upgrade.py` (`PASS`) | 13 приложений в реестре, карантин и `AppActionPassport` работают | **PASS** | — |
| **4** | **Models (`models`)** | `tools/intelligence_preservation_run.py`, `run_cyber_qlora_holdout_tournament.py` | `test_skills_models_ops_freeze.py`, `test_qlora_tournament_integrity.py` (`PASS`) | `intelligence-preservation-current.json` (`BL-003` закрыт); синтетический QLoRA-турнир отозван | **PARTIAL (`NOT_MEASURED` на QLoRA)** | Живой слепой турнир весов `Base Qwen3 vs QLoRA` (`NOT_MEASURED`) |
| **5** | **Cloud (`cloud`)** | `bcc/security/spend_firewall.py`, `bcc/economy_orchestrator.py`, `tools_cloud.py` | `test_spend_firewall_free_gate.py`, `test_economy_orchestrator.py`, `test_exec_swarm_regressions.py` (`PASS`) | `$0.00` гейт, привязка токена к `provider`/`model`, учёт `billing_unknown` и битого журнала | **PASS** | Межпроцессная ротация многогигабайтного `audit.jsonl` (`PLAN_ONLY`) |
| **6** | **Computer (`computer`)** | `pc_agent.py`, `pc_agent_policy*.py`, `terminal_control.py`, `astra_sandbox.py` | `test_pc_agent_argon.py`, `test_run_command_job_object.py`, `test_swarm2_coding_fixes.py` (`PASS`) | Job Object убивает сирот; PowerShell AST/token гейт `T3`; `self_modify` проверяет `approval_id` | **PASS (с fail-closed песочницей)** | Реальная OS-песочница Docker/WSL для автоприменения `pc.self_modify` (`NOT_RUN`) |
| **7** | **Media / Art (`media`)** | `command-center/bcc/art_v2/` (`photo_engine`, `video_engine`, `motion_timeline`, `vlm_critic`) | `test_art_studio_v2_wunderkind.py` (`16/16 PASS`) | Все 7/7 гейтов `Video Studio 2.0`, `AudioBeatAnalyzer` BPM и `VoxelToRenderBridge` в коде | **PARTIAL (`CODE_PASS / GPU_DAEMON_OFFLINE`)** | Живая генерация через запущенные демоны ComfyUI `:8188` и ACE-Step (`NOT_RUN`) |
| **8** | **Security (`security`)** | `audit_journal.py`, `spend_firewall.py`, `bossman_20.py` (`/api/v20/security/self-test`) | `test_defensive_security_p1_p2.py`, `test_bossman_20_superstructure.py` (`PASS`) | Живой `/api/v20/security/self-test` (`5/5 PASS`), неизменяемое ядро не тронуто | **PASS** | — |
| **9** | **Memory (`memory`)** | `bcc/v2/swarm_dag.py`, `memory_retrieval_v16.py`, `bossman_20.py` | `test_bossman_20_superstructure.py`, `test_mobile_dag_devices_upgrade.py` (`PASS`) | Obsidian vault (`68` заметок, `UP_TO_DATE`), `SwarmDAGPlanner` + `CrossFamilyVerifierEngine` | **PASS** | — |
| **10** | **Skills (`skills`)** | `.agents/skills/*` (все 17 ранее `MISSING` навыков созданы и валидированы) | `test_skills_models_ops_freeze.py` (`PASS`) | 0 ошибок манифестов, 0 критических нарушений `scan_policy` | **PASS** | — |
| **11** | **Plugins (`plugins`)** | `command-center/bcc/v2/mcp_hub.py` (транслитерация кириллицы `BL-131`) | `test_audit_upgrade_security_cloud_commerce.py` (`PASS`) | Русские имена MCP-инструментов корректно нормализуются (`поиск_базы` → `poisk_bazy`) | **PASS** | — |
| **12** | **Ops (`ops`)** | `build_identity.py`, `verify_windows_bundle.py` (`BL-130`), `bossman_20.py` | `test_defensive_security_p1_p2.py`, `test_orch_smoke_guard.py`, `test_orch_smoke_canon.py` (`PASS`) | Живой `/health/live` с проверкой `source_identity` и разделением `negative-control/` | **PASS** | 24-часовой непрерывный soak-прогон (`NOT_RUN`) |
| **13** | **OSS (`oss`)** | `docs/hybrid/sources.lock.json` (`BL-123` закрыт для `x264`/`x265`) | `test_skills_models_ops_freeze.py` (`PASS`) | Ревизии и лицензионный статус зафиксированы в `sources.lock.json` | **PASS** | — |
| **14** | **Commerce (`commerce`)** | `apps/swapme-bot/quote.py` (`compute_quote_strict`), `bcc/features/commerce_ledger.py` | `test_audit_upgrade_security_cloud_commerce.py`, `test_d1_swapme_mission_e2e.py` (`PASS`) | 6 узлов `OrderToDeliveryLedger` с разделением `PAPER_DEMO` и `AUDITED_PAID_REVENUE` | **PASS (offline/paper)** | Живые внешние сделки с реальными платежами (`NOT_RUN` по правилу безопасности) |
| **15** | **Devices (`devices`)** | `command-center/bcc/devices/worker_node.py` (`RemoteWorkerNode`, `VirtualUVCCameraProbe`) | `test_mobile_dag_devices_upgrade.py` (`PASS`) | Координатор кластера с каскадным `STOP <= 2.0s` и `VirtualUVCCameraProbe` | **PASS (`virtual_uvc_verified`)** | Подключение физического второго ПК и USB-камеры (`NOT_RUN`) |

---

## 4. Реестр решений владельца (Owner Decisions Registry)

| ID | Дата | Решение владельца | Как закреплено в коде и архитектуре |
|---|---|---|---|
| **ODR-01** | 21.10 / 28.10 | **Запрет отправки личных переписок, фото и видео в облако** | В `tools_cloud.py` (`static_denial`) и `pc_agent_policy_consts.py` (`HARD_INVARIANTS`) блокируются любые вложения, история чата и медиа-пути. |
| **ODR-02** | 26.10 / 28.10 | **Нулевой несанкционированный расход (`$0.00` по умолчанию)** | `SpendFirewall` блокирует любой платный или нераспознанный облачный тариф без одноразового токена владельца, привязанного к `provider`/`model` и `max_usd`. |
| **ODR-03** | 26.10 / 28.10 | **Неизменяемое ядро (§0 Конституции)** | `approvals.py`, `permissions.py`, `secrets.py`, `auth.py`, `control_plane.py`, `docs/constitution/*` защищены от любых правок агентами и самомодификации. |
| **ODR-04** | 27.10 / 28.10 | **`pc.self_modify` строго fail-closed без настоящей OS-песочницы** | Модельный Python никогда не исполняется на хосте Windows без контейнерной изоляции; статус честно остаётся `verification_blocked_unisolated`, а `approved=True` требует записи в `svc.approvals`. |
| **ODR-05** | 28.10 | **Отзыв любых синтетических бенчмарков и честные флаги** | Синтетический отчёт турнира `CYBER-QLORA-20261026` официально отозван (`revocation.json`); любые непроверенные вживую функции остаются за флагами `NOT_RUN` / `NOT_MEASURED`. |
| **ODR-06** | 28.10 | **Единый `main` (Harness Approved Bossman OS) и единый PDF-отчёт в пульт** | Все ветки роя (`origin/main`, `codex/exec-swarm-20261028`, `orch/dev1-kimi/S1`, `orch/dev2-glm/S2`) сведены в `main`; итоговый отчёт доставляется одним PDF-файлом в пульт. |

---

## 5. Список красных тестов и остатков (Red Tests & Residual Findings Ledger)

### 5.1. Красные тесты, обнаруженные и закрытые в Фазе 2 (`0 failed`)

1. **`test_exec_swarm_regressions.py::test_self_modify_health_reports_actual_rollback_result[False]`**
   - *Симптом до правки (RED):* `AssertionError: assert True is False` — `health_check` при сбое авто-отката возвращал `ok: True`.
   - *Правка:* `command-center/bcc/features/pc_agent.py:2753-2765` — `ok`, `verified` и `auto_rolled_back` берутся из результата `rb_res`.
   - *Статус после правки:* **GREEN (`PASS`)**.
2. **`test_swarm2_coding_fixes.py::test_self_modify_json_approved_true_without_approval_receipt_is_demoted`**
   - *Симптом до правки (RED):* `api_pc_agent_self_modify` принимал `"approved": true` из JSON-тела запроса без проверки `approval_id` в `svc.approvals`.
   - *Правка:* `command-center/bcc/features/pc_agent.py:6367-6412` — проверка `svc.approvals.get(aid)` и привязка `actor` к `request.state.principal.role`.
   - *Статус после правки:* **GREEN (`PASS`)**.
3. **`test_swarm2_coding_fixes.py::test_spend_firewall_token_bound_to_provider_and_model_blocks_mismatch`**
   - *Симптом до правки (RED):* токен `SpendFirewall` не привязывался к `allowed_provider` / `allowed_model` и мог быть потрачен на другую платную модель.
   - *Правка:* `command-center/bcc/security/spend_firewall.py:78-112, 282-297` — строгая проверка `token_scope_mismatch`.
   - *Статус после правки:* **GREEN (`PASS`)**.
4. **`test_swarm2_coding_fixes.py::test_powershell_encoded_or_obfuscated_commands_denied_as_t3`**
   - *Симптом до правки (RED при мутационном промпте):* `_classify_run_command` не вызывал `validate_powershell_ast_or_tokens` напрямую.
   - *Правка:* `command-center/bcc/features/pc_agent_policy_cmds.py:28, 199-201` — прямой вызов `validate_powershell_ast_or_tokens(joined)` с возвратом `T3` (`deny`).
   - *Статус после правки:* **GREEN (`PASS`)**.
5. **`test_defensive_security_p1_p2.py::test_owner_safe_runtime_drill_end_to_end`**
   - *Причина падения у Роя 1:* запуск теста на грязном рабочем дереве (`git status --porcelain` не пуст → `build_identity.py` честно возвращает `build_sha=None`, `SOURCE_IDENTITY_UNKNOWN`).
   - *Проверка на чистом дереве `main`:* **GREEN (`14/14 PASS`)**.

### 5.2. Недоказанные контуры, удерживаемые за честными флагами

- `pc.self_modify` выполнение тестов модели и автоприменение: **`NOT_RUN` / `verification_blocked_unisolated`** (требуется контейнерная OS-песочница WSL2/Docker).
- Живой слепой турнир весов `Base Qwen3 vs Defensive QLoRA`: **`NOT_MEASURED`** (требуется изолированное окно GPU без конкуренции с боевым рантаймом).
- Живая генерация видео/аудио через локальные GPU-демоны `ComfyUI (:8188)` и `ACE-Step`: **`NOT_RUN`** (демоны выключены по умолчанию для защиты памяти ПК).
- 24-часовой непрерывный soak-прогон: **`NOT_RUN`** (расписание в `07_TOMORROW_HOURLY_PLAN_20261029_RU.md`).

> **Обязательная формулировка статуса:** «Bossman движется к production; production/freeze/Tier 5 пока не доказан».
