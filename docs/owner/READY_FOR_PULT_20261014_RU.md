# Готовность Bossman к работе владельца через пульт (14.10.2026)

**Дата и время фиксации:** 2026-10-14 00:45 PDT  
**Единая ветка-кандидат:** `release/bossman-one-20261014` (включает `origin/main` `cd71cddc` + `feat/pc-agent-20261013` `fb9ccf30` + `feat/bossman-2-2-20261013` `08eaca59`).  
**Статус этапа 2.2:** Подготовлен и доказан до кнопки владельца (`release_main` **PENDING**, `#42`). **Этап 2.2 НЕ объявлен закрытым**, пока владелец лично не нажмёт кнопку слияния (`/work_yes 42` / `✅ Разрешить`) и коммит не попадёт в `main`.

---

## 1. Что поднято и работает сейчас (ПРОВЕРЕНО)

На порту `:8801` и в Telegram работает **строго один** экземпляр бэкенда и **строго один** poller пульта, запущенные из единой ветки `release/bossman-one-20261014` (`C:\Users\asd\Bossman\wt-pc-agent-argon-20261013`):

| Компонент | Статус | Адрес / Порт | Лог-файл |
|---|---|---|---|
| **Ollama** (`bossman-community-qwen-uncensored-ctx64k:latest`) | `RUNNING` | `http://127.0.0.1:11434` | `C:\Users\asd\AppData\Local\Bossman\one-bossman-logs\ollama-20261013.log` |
| **Backend Command Center (`bcc.main:app`)** | На момент первичного отчёта бэкенд был `SOURCE_IDENTITY_STALE` до перезапуска; перезапущен с финального SHA ветки `fix/pc-agent-security-20261014` | `http://127.0.0.1:8801` | `C:\Users\asd\AppData\Local\Bossman\one-bossman-logs\backend-source-20261013.log` |
| **Пульт владельца (`bcc.telegram_companion`)** | `RUNNING` (1 poller) | Telegram long-polling | `C:\Users\asd\AppData\Local\Bossman\one-bossman-logs\companion-source-20261013.log` |

---

## 2. Ожидающая кнопка владельца (НЕ НАЖАТА)

В пульте Telegram владельца ждёт решение о слиянии документационного коммита в `main`:

- **Задача оператора (`task_id`):** `c56a43b1d89c`
- **Номер подтверждения (`approval_id`):** **`42`**
- **Статус решения:** **`PENDING`**
- **Категория:** `release_main`
- **Точная команда при одобрении:** `git push origin lab/op-c56a43b1d89c:main`
- **Лабораторная ветка и коммит:** `lab/op-c56a43b1d89c` · SHA `2a3adecdf48ca6f034710488a55f48e8e89c9fee`
- **Изменённый файл:** `docs/owner/MOTION_STUDIO_QWEN_CALLS_RU.md` (17 строк, не защищённый файл)
- **Независимая облачная проверка:** `nvidia/nemotron-3-ultra-550b-a55b:free` (`different_family_free_cloud`) → **`VERDICT: APPROVE`**
- **Как владельцу принять решение в Telegram:**
  - **Разрешить слияние в `main`:** нажать кнопку `✅ Разрешить` под сообщением о решении `#42` **или** отправить `/work_yes 42`
  - **Отклонить слияние:** нажать кнопку `⛔ Отклонить` **или** отправить `/work_no 42`

---

## 3. Команды запуска, проверки и остановки (PowerShell)

### Проверка живого состояния
```powershell
Invoke-RestMethod http://127.0.0.1:8801/health/live | ConvertTo-Json -Depth 5
```

### Запуск (из ветки `fix/pc-agent-security-20261014`)
```powershell
# 1. Бэкенд :8801
& "C:\Users\asd\Bossman\venv-tests-20261012\Scripts\python.exe" `
  "C:\Users\asd\Bossman\creative-runs\PULT-SOURCE-20261013\run_backend_source.py" `
  "C:\Users\asd\Bossman\wt-pc-agent-argon-20261013"

# 2. Пульт владельца (1 poller)
& "C:\Users\asd\Bossman\venv-tests-20261012\Scripts\python.exe" `
  "C:\Users\asd\Bossman\creative-runs\PULT-SOURCE-20261013\run_pult_source.py" `
  "C:\Users\asd\Bossman\wt-pc-agent-argon-20261013"
```

### Остановка
```powershell
Get-CimInstance Win32_Process | Where-Object {
  $_.CommandLine -like '*bcc.telegram_companion*' -or $_.CommandLine -like '*bcc.main:app*8801*'
} | ForEach-Object { Stop-Process -Id $_.ProcessId -Force }
```

---

## 4. Что проверено живыми прогонами и автотестами (с выводами)

### Шаг 1. Подъём системы, `/health/live`, статус оператора и пин конституции v2
- На момент первичного отчёта бэкенд `:8801` находился в состоянии `SOURCE_IDENTITY_STALE` до перезапуска (из-за незакоммиченных правок в рабочем дереве). После коммита и перезапуска из чистого SHA ветки `fix/pc-agent-security-20261014` в `/health/live` нет `SOURCE_IDENTITY_STALE`.
- `GET http://127.0.0.1:8801/api/qwen-operator/status` → `operator_role: "operator:l1-lab"`, `constitution.autonomy_loop: "OK"`, `authority: {"push": false, "merge": false, "deploy": false}`.
- Файл `docs/constitution/BOSSMAN_CONSTITUTION.md` и пин `%LOCALAPPDATA%\Bossman\autonomy\constitution.sha256` побайтово совпадают: `7d845db367b3acfb58418ee9d844a4c887c512e4804e91fd708034667d768970`.
- **Защита от shell/path-обходов в `pc_agent.py` (`pc_agent_policy.py`):** **ЧАСТИЧНО**; обходы 1–5 (`1a` `notepad_roundtrip` до `mkdir`/`save_rollback_backup` и запрет чтения бэкапов секретов, `1b` нормализация путей и запрет передачи токена вне белого списка `pc.bossman_api`, `1c` атомарное однократное возобновление `resume_after_approval` под `rt.lock` + БД CAS без побочного эффекта в `GET`, `1d` блокировка `always_owner_button` и неизвестных бинарников при `/trust`, `1e` защита служб и проверка `cmdline` при `kill_process` по имени с лимитом 1 процесс) исправлены в ветке `fix/pc-agent-security-20261014` (`23/23 passed` в `test_pc_agent_argon.py`), независимая проверка — не проведена.

### Шаг 2. UX-прогон пульта (`/help`, `/work` read-only Motion Studio через `bossman_calls`, `/work_status`) — ПРОВЕРЕНО
1. **`/help`**: доставлен в Telegram со всеми инлайн-кнопками.
2. **Короткая read-only задача (`db5935c0b31d`)**:
   - Выполнены 2 вызова `bossman_calls`: `GET /api/motion-studio/status` → `200`, `GET /api/motion-workflow/projects` → `200`.
   - Ответ доставлен в Telegram (`telegram_message_id: 1685`), `read_only: true`, `diff_bytes: 0`.
3. **`/work_status`**: доставлены карточки статуса во время работы (`1686`) и по завершении (`1687`).

### Шаг 3. Задача-доказательство слияния (`docs/` → `lab/op-X` → облачный рецензент → `release_main` `PENDING`) — ПРОВЕРЕНО
- Задача **`c56a43b1d89c`**:
  - Создан файл `docs/owner/MOTION_STUDIO_QWEN_CALLS_RU.md` в лаборатории `lab-c56a43b1d89c`.
  - Прогнаны обязательные тесты в лаборатории: `pytest command-center/tests/test_qwen_operator_bossman_api.py` → `21 passed in 8.16s` (`exit_code: 0`).
  - Создан лабораторный коммит на ветке `lab/op-c56a43b1d89c`: SHA `2a3adecdf48ca6f034710488a55f48e8e89c9fee`.
  - Независимая модель `nvidia/nemotron-3-ultra-550b-a55b:free` вернула `VERDICT: APPROVE`.
  - Создано и отправлено в пульт владельца решение **`#42`** (`category: "release_main"`, `status: "PENDING"`). **Кнопка не нажата.**

### Шаг 4. Задача-ловушка на откат целой попытки (`d00bf4d25ca2`) — ПРОВЕРЕНО
- Запущена задача `d00bf4d25ca2` на создание `command-center/tests/test_portcheck_trap_demo.py`, где первая попытка падает в лабораторном рабочем дереве `lab-d00bf4d25ca2`.
- В журнале шагов задачи зафиксированы 2 полных отката попытки:
  - `attempt=1`: `pytest` в лаборатории упал (`exit 1`) → `{"n": 4, "kind": "rollback", "status": "done", "attempt": 1}` → `diagnose`.
  - `attempt=2`: `pytest` в лаборатории упал (`exit 1`) → `{"n": 7, "kind": "rollback", "status": "done", "attempt": 2}` → `diagnose`.
  - `attempt=3`: `pytest` в лаборатории прошёл (`exit 0`, `1 passed`) → коммит `9012b391c7f0c274984502ad2f2ae5b488c0256c` на ветке `lab/op-d00bf4d25ca2`, рабочее дерево чистое.

### Шаг 5. Запись `quality_audit` в ledger, `/edit_engine` и стоп-кран сторонних интеграций (`NOT_ACTIVATED`)
- **Строгая запись `quality_audit` в `simulated_ledger.jsonl`:** `run_pc_task` передаёт результат `verify_deliverable_strict` в `log_earning_attempt(..., quality_audit=...)`, сохраняя хэш, размер и статус проверки в `simulated_ledger.jsonl`; пустой или битый артефакт переводит задачу в `NEEDS_REVISION` (`verified=False`).
- **Переключатель `/edit_engine qwen|uncensored` (`POST /api/pc-agent/edit-engine`):** явно переключает профиль редактирования между `qwen` (`Qwen-Image-Edit-2509-Q4_K_M.gguf`) и `uncensored` (`z_image_nsfw_v2`, `sdcpp:z-image-turbo -i <path> --strength 0.6`).
- **Сторонние интеграции (`Blender MCP`, `Fincept`, `Vibe-Trading`):** по стоп-крану владельца оставлены выключенными (**`NOT_ACTIVATED`**).

---

## 5. Что НЕ ПРОВЕРЕНО (честный список)

1. **Фактическое слияние в `main` по кнопке владельца (`/work_yes 42` / `✅ Разрешить`):** намеренно НЕ нажималось агентом.
2. **Независимая проверка исправлений обходов 1–5 в `pc_agent.py` / `pc_agent_policy.py`:** статус **ЧАСТИЧНО**; обходы 1–5 исправлены и покрыты 5 байпас-тестами в ветке `fix/pc-agent-security-20261014`, но независимый внешний аудит после патча ещё не проведён.
3. **Платежи, Telegram Stars и внешние рассылки/публикации:** на паузе по решению владельца.
4. **Поведение после перезагрузки самой Windows 11:** автозапуск после ребута ОС в рамках этого прогона не тестировался.
