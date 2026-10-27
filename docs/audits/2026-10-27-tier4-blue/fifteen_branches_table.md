# Матрица 15 (18) Направлений Bossman — Внутренняя Оценка Tier 4 (Blue Team)

Дата: 2026-10-27T08:12:00Z  
Точный SHA исходного кода: `745c99e6a0e7cf22c056343571710ce62336ad84`  
Точный SHA живого рантайма (`:8801 /health/live`): `745c99e6a0e7cf22c056343571710ce62336ad84`  
Рабочее дерево: `C:\Users\asd\Bossman\wt-trio-argon` (чистое дерево, чужие файлы Poker/war-terminal/eval500 не затронуты)  

---

| № | Направление / Ветка | Что реально есть в коде | Конкретный файл улик / проверочный тест | Открытые дефекты | Статус | Следующий целевой тест |
|---|---|---|---|---|---|---|
| 1 | **Jeff / LLM** | Маршрутизация на локальные llama-server/ollama (`r1-70b`, `gemma4-31b`, `qwen3-coder-30b`, `ace-decider-08b`) | `tools/owner_machine_report.py`, `tests/test_owner_machine_report.py` | — | **PASS** | `tests/test_v5_golden_missions.py` на замер качества промптов |
| 2 | **Models (Resource Governor)** | Правило «одна тяжёлая модель в памяти», отслеживание PID 27840 (Qwen36 35B MoE, 26.9GB VRAM) и PID 25484 (Ace Decider 0.8B) | `docs/audits/2026-10-27-m1-runtime-identity/receipt.json` | — | **PASS** | `test_resource_governor.py` на попытку вытеснения |
| 3 | **Memory** | Журнал аудита `audit.jsonl` с крипто-привязкой `.head`, подписанные улики `bossman_shared.evidence` | `bossman_shared/evidence.py`, `tests/test_evidence_signing_shared.py` | — | **PASS** | `test_audit_p0_trace_identity.py` |
| 4 | **Agents (OS-Agent Trio)** | Пакет `bcc/os_agent/`, интеграция ролей Argon + Codex + Claude, синхронизация task_id | `command-center/bcc/os_agent/__init__.py`, `command-center/tests/test_argon_os_agent_trio.py` | — | **PASS** | `test_argon_os_agent_trio.py` |
| 5 | **Skills / Tools** | 130 инструментов и навыков, санитизация списков файлов, блокировка опасных расширений | `command-center/bcc/features/pc_agent.py`, `command-center/tests/test_pc_agent_traps_claude.py` | — | **PASS** | `test_pc_agent_traps_claude.py` (254 теста) |
| 6 | **Computer / Windows** | Автономия PC-Agent (T1/T2 ask/T3 forbidden), исполнение разрешённых шагов в workspace | `command-center/bcc/features/pc_agent.py`, `command-center/tests/test_pc_agent_master_p0_p5.py` | — | **PASS** | `test_pc_agent_master_p0_p5.py` |
| 7 | **Browser** | Изолированное чтение веб-страниц, защита от SSRF и утечки локальных секретов | `command-center/bcc/features/pc_agent_policy_guards.py` | — | **PASS** | `test_pc_agent_traps_claude.py` (секция browser) |
| 8 | **Telegram** | Telegram Companion, форматирование отчётов владельцу `owner_report.py` без утечки секретов | `command-center/bcc/telegram_companion/owner_report.py`, `command-center/tests/test_owner_report.py` | — | **PASS** | `test_answering_companion.py` |
| 9 | **Mobile (Pult :8840)** | Мобильный пульт, авторизация по токену, мгновенный STOP, история звонков, OpenCode | `apps/bossman-mobile/bossmobile/api.py`, `apps/bossman-mobile/tests/` | — | **PASS** | `apps/bossman-mobile/tests/test_mobile.py` |
| 10 | **Command Center / UX** | Диспетчер задач `:8801`, современная типографика (tabular-nums), кнопки cubic-bezier, пульсирующий STOP, rollback hints | `command-center/ui/style.css`, `apps/bossman-mobile/bossmobile/ui/app.css` | — | **PASS** | `apps/bossman-mobile/tests/ui/every_function.test.mjs` |
| 11 | **War Terminal (:8830)** | Офлайн-генератор тендерных досье на статических фикстурах (CZ ISVZ, Prozorro, CNB FX), блокировка сети на уровне сокетов | `tools/prove_tender_dossier.py`, `docs/audits/2026-10-24-earn-tender-dossier/receipt.json` | — | **PASS** (Capability only, revenue NOT_RUN) | Интеграция живого стрима ISVZ после выхода в прод |
| 12 | **Media / Video** | Motion Studio, Local Genjutsu, кадрирование `--head-zoom`, генерация кадров | `tools/motion_studio/local_genjutsu.py` | — | **PASS** | `tests/test_video_facelock.py` |
| 13 | **Devices / Cam** | Поддержка Face SDK, калибровка камеры, пассивный режим чтения без фоновой слежки | `tools/owner_machine_report.py` (стадия 6) | Камера не подключена в тестовом контейнере | **NOT_RUN** | Подключение физической веб-камеры на стенде |
| 14 | **Ops / CI / Integrity** | Проверка целостности SHA, `ci_secret_scan.py`, изоляция временных каталогов `--basetemp` | `tools/ci_secret_scan.py`, `tools/build_source_identity.py` | — | **PASS** | `python tools/ci_secret_scan.py` |
| 15 | **OSS / Defensive QLoRA** | Read-only мониторинг `CYBER-QLORA-20261026`, нулевые расходы ($0.00), поды не создавались | `C:\Users\asd\Bossman\creative-runs\CYBER-QLORA-20261026\` | — | **PASS** (Read-Only) | Холдаут-турнир Base vs QLoRA после завершения обучения |
| 16 | **Commerce / SwapMe CRM** | Расчёт покупки 1,000 USDT за CZK с 3% маржой, генерация PDF, проверка остановки STOP, персистентность после рестарта | `command-center/tests/test_d1_swapme_mission_e2e.py`, `docs/north-star/d1_swapme_mission_receipt.json` | — | **PASS** (Synthetic, zero real payments) | Тестирование боевого API банка после согласования |
| 17 | **Reports (Executive)** | Сверка оборота Telegram-экспорта, 2 независимых прогона с полным совпадением SHA-256, эндпоинт `/m/api/calls/answering/reports` (200) | `command-center/bcc/features/reports_runner.py`, `tools/owner_machine_report.py` | — | **PASS** | Сверка с реальным экспортом владельца через конфиг |
| 18 | **Security & OS-Sandbox** | Защита от prompt injection, path traversal, symlink escape, неизменяемое ядро. `pc.self_modify` защищён OS-sandbox контрактом | `docs/audits/2026-10-27-tier4-blue/sandbox_and_stop_e2e.json` | OS-песочница недоступна на Windows 11 (DACL python.exe); оставлен fail-closed | **NOT_RUN** (Безопасный fail-closed отказ) | Подключение bwrap/runsc при переносе на Linux или Docker/WSL |

---

### Итоговый вердикт по матрице:
- **PASS**: 16 направлений (функциональные тесты, крипто-квитанции, STOP-гейты, CI и секрет-сканы).
- **NOT_RUN (Безопасный fail-closed)**: 2 направления (`Devices/Cam` — физическая камера не активна; `Security/Self-Modify execution` — модельные тесты заблокированы на хосте без OS-песочницы, вызовы `apply`/`rollback` переведены в T2 ask, откат защищён от path traversal и файлов неизменяемого ядра).
- **FAIL**: 0.
- **BLOCKED**: 0 (живой рантайм полностью привязан к чистому SHA).
