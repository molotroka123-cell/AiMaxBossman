# Аудит-отчёт: локальный ИИ-агент управления ПК владельца (Аргон P0–P5) и `/browser`

**Дата:** 26.10.2026  
**Вердикт:** `PASS` (`PROVEN`)  
**Квитанция:** [`receipt.json`](receipt.json)  
**Тестовый пакет:** `command-center/tests/test_pc_agent_master_p0_p5.py` (48/48 зелено), сводный прогон PC-Agent + Action Contract + Mobile Pult — **427 passed, 0 failed**.

---

## 1. Этап P0: Инвентаризация и закрытие 6 дефектов пульта и агента

По результатам аудита `docs/audits/2026-10-25-pult-agent-self-edit/receipt.json` закрыты все 6 дефектов (каждый с позитивным тестом и негативным контролем):

1. **Нативные UTF-8 инструменты кода (`code.read`, `code.write`, `code.patch`)** в `command-center/bcc/features/tools_code.py`:
   - Агент больше не зависит от `cmd.exe` и `\uXXXX`-экранирования через `python -c`.
   - Защита неизменяемого ядра (`_IMMUTABLE_CORE_MARKERS`, `_PROTECTED_REPO_MARKERS`, `_PROTECTED_BASENAMES`) и секретов T3 встроена прямо на уровне инструмента.
2. **Фильтр строк версий (`v1.0`, `v2.15.0`) в `_terminal_evidence` (`action_contract.py`)**:
   - Строки версий больше не принимаются за имена файлов; при этом реальные несуществующие файлы (`missing_module.py`) по-прежнему требуют фактического доказательства.
3. **Точное сопоставление навыков (`match_workspace_skill_for_task` в `agent_opencode.py`)**:
   - Общие слова (`bossman`, `v1.0`, `python`, `file`, `test`) добавлены в `_SKILL_MATCH_STOPWORDS` — навык `bossman-motion-concert` больше не приклеивается к обычным задачам программирования.
4. **Посессионная остановка в мобильном пульте (`POST /m/api/agent/sessions/{id}/stop`)**:
   - Остановка одной сессии в `apps/bossman-mobile/bossmobile/api.py` останавливает только задачу этой сессии и не роняет параллельные задачи в других сессиях. Глобальный STOP сохранён за `POST /m/api/stop`.
5. **Чистка списка агентов и промпта Агента 41 (`Gemma 4`)**:
   - В `ensure_argon_agent` (`pc_agent.py`) системный промпт Агента 41 обновлён для полноценной работы с инструментами ПК.
   - В `apps/bossman-mobile/bossmobile/policy.py` скрыты тестовые/дублирующие клоны (`eval500 tournament clone*`, `bossblocks-*`, `fresh-vibes-*`, `swapme*`, дубликат `Strata` при активном `Argon PC-Agent`).
6. **Устойчивость `bossman_live()` в мобильном пульте (`GET /m/api/status`)**:
   - При кратковременной задержке `/api/health` пульт выполняет повторную проверку `/health/live` с увеличенным таймаутом и не показывает ложный `bossman up: false`.

---

## 2. Этап P1: Чтение, неизменяемый аудит и глобальный STOP ≤ 5 с

- Инструменты чтения (`pc.system_info`, `pc.list_windows`, `pc.list_processes`, `pc.list_services`, `pc.read_file`, `pc.list_dir`) работают в режиме `T0` (`auto`).
- Каждое действие пишется ядром в `audit.jsonl` с криптографической хеш-цепочкой (`prev_hash` → `entry_hash`) и вырезанием секретов (`scrub_secrets`).
- Глобальный STOP (`rt.trigger_stop`) проверяется перед каждым шагом и принудительно завершает дерево дочерних процессов (`taskkill /T /F /PID`) за **2.41 с** (при нормативе ≤ 5 с), корректно фиксируя все завершённые `killed_pids`.

---

## 3. Этап P2: Уровни автономии (`L0`–`L3`), инструменты ОС (1–7), 30 сценариев и 10 ловушек

- **Уровни автономии (`GET/POST /api/pc-agent/autonomy`)**:
  - `L0` — только чтение и объяснение (любые `T1`/`T2` отклоняются).
  - `L1` (по умолчанию) — подтверждение владельца на каждый шаг изменения.
  - `L2` — обратимые безопасные действия в рабочих папках (`T1`) автоматически, `T2` — с подтверждением.
  - `L3` — ночной режим с бюджетом шагов и времени, очередью отложенных подтверждений `T2` и утренним отчётом.
- **Инструменты ОС**:
  1. **Файлы**: `pc.read_file`, `pc.write_file`, `pc.copy_file`, `pc.move_file`, `pc.delete_file` (удаление перемещает файл в корзину `rt.recycle_bin_dir` + создаёт точку отката в `rt.rollback_dir`).
  2. **Процессы и службы**: `pc.list_processes`, `pc.kill_process` (собственные дочерние PID из `rt.spawned_pids` — `T1 auto`, чужие процессы — `T2 ask`, системные/защищённые — `T3 deny`), `pc.list_services`, `pc.control_service`.
  3. **Песочница Shell**: `pc.run_command` с лимитом времени и вывода, очисткой окружения и фильтрацией опасных команд.
  4. **Окна и UI Automation**: `pc.observe_screen`, `pc.list_windows`, `pc.window_act`, `pc.desktop_act`, `pc.ui_read_window` (чтение дерева элементов окна без нажатия `Alt`, чтобы не открывать меню Microsoft Edge, со скриншотом и `sha256`).
  5. **Браузер и команда `/browser`**: `pc.browser_act` + сквозная поддержка слэш-команды `/browser` (и `/web`) в CLI, чате и мобильном пульте. Вход в аккаунты (`login`) и отправка форм (`submit`) требуют подтверждения владельца (`T2 ask`), платежи заблокированы (`T3 deny`).
  6. **Система и планировщик задач**: `pc.system_info`, `pc.bossman_status`, `pc.scheduled_tasks` (разрешены только собственные задачи с префиксом `BossmanAgent-*`).
  7. **Отправка файлов владельцу в пульт**: `pc.send_to_pult` с жёстким соблюдением правила альбома (**не более 10 файлов** и **не более 40 МБ** суммарно) и блокировкой секретов/личных файлов.

---

## 4. Этап P4: Безопасный контур самоизменения (`pc.self_modify` / `/api/pc-agent/self-modify`)

1. Работа ведётся в изолированной ветке `agent/<YYYYMMDD>-<тема>` и песочнице `rt.self_mod_dir`, никогда напрямую в `main`.
2. Попытка изменить неизменяемое ядро (§0: код STOP, политики `pc_agent_policy*.py`, `approvals.py`, `permissions.py`, `secrets.py`, `audit.jsonl`, `vault.key`, `constitution.md`) блокируется на уровне инструмента с записью в `audit.jsonl`, `self_mod_journal.jsonl` и флагом `owner_alert=True`.
3. Перед запросом подтверждения автоматически запускается `pytest` (позитивный тест + негативный контроль).
4. Применение к системе возможно только после подтверждения владельца в пульте (с описанием изменений простым русским языком и командой отката).
5. Поддерживается откат одной командой (`POST /api/pc-agent/self-modify/{mod_id}/rollback`) и автоматический откат при сбое проверки здоровья в течение 5 минут (`POST /api/pc-agent/self-modify/{mod_id}/health-check`).

---

## 5. Этап P5: Ночной режим `L3` и защита ядра на 8 провальных кейсах турнира

- **Ночной режим `L3` (`POST /api/pc-agent/night-mode/run`, `GET /api/pc-agent/night-mode/report`)**:
  - Выполняет безопасные шаги `T0`/`T1` в пределах бюджета (`budget_steps`, `budget_seconds`), откладывает шаги `T2` в очередь подтверждений владельца (`deferred_approvals`), блокирует `T3` и формирует понятный утренний отчёт на русском языке.
- **Исправления ядра по результатам турнира моделей (`check_kernel_turn_policy` / `validate_kernel_turn_answer` в `action_contract.py`)**:
  - Закрыты все 8 кейсов безопасности, памяти и STOP (`E5-memory-012`, `E5-memory-014`, `E5-security-013`, `E5-security-014`, `E5-security-019`, `E5-stop-002`, `E5-stop-005`, `E5-stop-013`).
