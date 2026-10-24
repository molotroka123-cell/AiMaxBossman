# Argon PC-Agent (Bossman): Отчёт, Архитектура и Памятка Владельца

**Ветка:** `feat/pc-agent-20261013` (профиль изменений: **Argon**)  
**Ядро:** `command-center/bcc/features/pc_agent.py` + `command-center/bcc/telegram_companion/pc_agent_bridge.py`

---

## 1. Этап A — Сводка по базе Bossman (`main`) и устраненные пробелы

1. **Архитектура ядра (`bcc`, `:8801`) и пульта (`bcc/telegram_companion`):**
   - Единое ядро FastAPI (`command-center/bcc/main.py`) автоподгружает модули из `bcc/features/` через `load_features()`.
   - Пульт (`command-center/bcc/telegram_companion/service.py`) работает через раздельные очереди `Store` (`control` и `chat`), поэтому `/stop`, `/pause`, `/approvals` и команды управления ПК (`/argon`, `/procs`, `/kill`, `/windows`, `/sys`) не блокируются генерацией или медленной моделью.
   - Оператор кода (`command-center/bcc/features/qwen_operator.py`) ведёт изолированные ветки `lab/op-<id>` с независимой облачной проверкой (`:free` модели реестра Bossman) и кнопкой `release_main` (`/work_yes N`).
2. **Безопасность и Конституция v2 (`docs/constitution/CONSTITUTION_V2.md`):**
   - Проверка личности владельца: `Settings.authorize(message)` проверяет одновременно `user_id`, `chat_id`, `chat.type == "private"`, `is_bot == False` и отсутствие пересылки (`forward_*`). Чужие сообщения молча игнорируются.
   - Пин Конституции (`docs/constitution/constitution.sha256`) сверяется `bcc/autonomy/policy.py`; менять конституцию и пин может только владелец локально через `tools/adopt_constitution_v2.ps1`.
   - Секреты хранятся в DPAPI/Fernet (`bcc/secrets.py`, `credentials.enc`, `vault.key`, `api-token`) и очищаются на выходе через `bossman.obs.redact` + `scrub_secrets`.
3. **Что было доработано в Argon PC-Agent (исправления вместо аудитов):**
   - Реализован исполнитель `pc.kill_process` (`psutil` + `taskkill /T /F`) с защитой системных процессов Windows и самого стека Bossman (даже при передаче числового PID) и обязательной пост-проверкой завершения процесса (`psutil.pid_exists`).
   - Добавлено прямое управление окнами (`pc.window_act`: `focus`, `close`, `minimize`, `maximize`, `restore` через Win32 `user32`), службами Windows (`pc.list_services`, `pc.control_service`), расширенная телеметрия железа (`pc.system_info`: ЦП, ОЗУ, диск, GPU/VRAM через `nvidia-smi`, активные модели Ollama в VRAM) и файловые операции с откатом (`pc.write_file`, `pc.edit_file`, `pc.copy_file`, `pc.move_file`, `pc.delete_file`).
   - Исправлена привязка прав `pc_agent_allowed` в `pc_agent_bridge.py`: теперь пульт работает напрямую через внутреннее ядро Bossman (`console_allowed` + `authorized(message) == person`), не требуя внешнего CLI-моста Claude/Codex.
   - Добавлен универсальный планировщик `plan_pc_task_with_llm` (с приоритетом бесплатных облачных моделей `:free` из реестра Bossman и резервом на локальную Qwen в Ollama), при этом каждый шаг плана проходит жёсткий детерминированный гейт `classify_pc_action`.

---

## 2. Этап B — Поток управления и уровни доступа (T0–T3)

```
Telegram (личный чат владельца)
  │
  ▼
Companion.authorized() + pc_agent_allowed()  [Чужие user_id/chat_id молча отсекаются]
  │
  ▼
POST /api/pc-agent/tasks (единые таблицы tasks + task_runs + tool_calls)
  │
  ▼
Планировщик (compile_plan_for_prompt + plan_pc_task_with_llm)
  │
  ▼
Детерминированный гейт classify_pc_action(tool, args, tainted, trust_active):
  ├─ T0 (чтение: экран, окна, процессы, службы, система, чтение файлов/папок, статус Bossman) ──► СРАЗУ
  ├─ T1 (обратимые: запись/правка файлов в рабочих папках с бэкапом, окна, Блокнот, браузер) ──► СРАЗУ + ОТКАТ
  ├─ T2 (рискованные: kill процесса, удаление файла, службы, команды вне белого списка)       ──► КНОПКА «✅ Разрешить #N» (или окно /trust)
  └─ T3 (ключи, vault, пин, Конституция, системные процессы, платежи, CSAM/NCII/облако фото)  ──► ВСЕГДА ОТКАЗ
  │
  ▼
execute_pc_step()  [Список argv без shell, окружение без секретов, таймаут, re-observe & verify]
  │
  ▼
AuditJournal (append-only audit.jsonl с цепочкой SHA-256 и очисткой секретов) + Отчёт в Telegram
```

---

## 3. Команды пульта владельца для управления ПК

| Команда или фраза | Уровень | Что делает |
|---|---|---|
| `/argon` или кнопка `🤖 Argon ПК` | T0 | Открывает панель управления ПК, процессами, окнами и службами |
| `/procs` / `/procs cpu` / `/procs <имя>` | T0 | Список топ-процессов по ОЗУ или ЦП + интерактивные кнопки `✖ <имя> (<PID>)` |
| `/kill <pid\|имя>` | T2 | Завершает процесс/дерево процессов (требует кнопку `✅ Разрешить`, либо `/trust`; системные процессы и Bossman защищены T3) |
| `/windows` | T0 | Список всех открытых окон верхнего уровня с PID и кнопками фокуса `🎯` |
| `/focus <окно\|pid>` | T1 | Разворачивает и выводит окно на передний план |
| `/close_win <окно\|pid>` | T1 | Закрывает указанное окно (`WM_CLOSE`) с проверкой закрытия |
| `/sys` | T0 | Сводка по железу: ЦП, ОЗУ, диск, GPU/VRAM, модели в Ollama и статус ядра Bossman |
| `/services [фильтр]` | T0 | Список работающих служб Windows (или поиск службы по имени) |
| `/run <команда>` | T0/T1/T2 | Запуск команды списком аргументов `argv` без сырой оболочки под контролем гейта |
| `/trust [минуты]` / `/untrust` | T2 | Включает/выключает временное окно доверия T2 (до 60 мин; снимается `/stop`) |
| `/rollback [task_id]` | T1 | Откатывает файловые изменения задачи из резервной копии |
| `/pc_audit [N]` | T0 | Показывает последние записи `audit.jsonl` (с проверкой цепочки SHA-256) |
| `/stop` | Kill-switch | Мгновенно убивает дочерние процессы агента (`taskkill /T /F`), сбрасывает `/trust` и блокирует новые задачи до `/resume` |

Также поддерживаются естественные фразы без слэш-команды:
- *«кто жрёт память»*, *«покажи процессы»*, *«убей процесс 1234»*
- *«покажи окна»*, *«переключись на окно Блокнот»*, *«закрой окно Калькулятор»*
- *«проверь службу Spooler»*, *«сколько места на диске»*, *«состояние железа»*
- *«аргон <любое поручение>»* / *«джарвис <любое поручение>»*
