# Проектирование OS-песочницы и модель угроз для `pc.self_modify` (Tier 4 / Fail-Closed)

Дата: `2026-10-27`
Статус `pc.self_modify` в рабочем runtime: **`FAIL-CLOSED` (`verification_blocked_unisolated` / `verification_status: "NOT_RUN"` / `tests_passed: false` / `applied: false`)**
Связанный аудит Red Team: [`docs/audits/2026-10-27-redteam-self-mod/README_RU.md`](../2026-10-27-redteam-self-mod/README_RU.md)

---

## 1. Инвариант безопасности (Zero-Trust к модельному коду)

На текущем этапе развития Bossman (`Tier 4 candidate`) контур `pc.self_modify` работает строго в режиме **proposal-only (fail-closed)**:
1. Любой предложенный моделью или внешним вызовом код (`proposed_code`, `test_code`) **никогда не исполняется** на хостовой ОС через `pytest`, `python -c` или дочерние процессы хоста.
2. Параметры `approved=True`, `apply_after_approval=True` или `action="apply"` **не могут обойти** требование верификации в изолированной OS-песочнице: при попытке `apply` возвращается отказ `apply_blocked_unverified` (`ok: False`, `verified: False`).
3. Приоритет детерминированных отказов (`status` contract) проверяется **до** этапа верификации:
   - `blocked_invalid_mod_id` — при невалидном `mod_id` (любые слэши `/`, `\`, `..` или символы вне `^[A-Za-z0-9][A-Za-z0-9._-]{0,79}$`);
   - `blocked_outside_workspace` — при любой попытке указать `target_file` вне `workspace_dir` (включая POSIX `../`, Windows `..\`, абсолютные пути и симлинки/reparse points на любой ОС);
   - `blocked_immutable_core` — при попытке затронуть неизменяемое ядро (`approvals.py`, `permissions.py`, `secrets.py`, `auth.py`, `control_plane.py`, `docs/constitution/*` и др.);
   - `blocked_forbidden_target` — при попытке записи вне разрешённого поддерева (`skills/`, `prompts/`, `agents/`, `workflows/`, `ui_patches/`, `tests/`);
   - `blocked_unsafe_rollback_record` — при попытке подменить пути в `proposal.json` (`applied_files` / `backup_path`) во время `rollback`;
   - `verification_blocked_unisolated` (`verification_status: "NOT_RUN"`, `tests_passed: false`) — для всех синтаксически и структурно допустимых черновиков до подключения аппаратной/OS-изоляции.

---

## 2. Модель угроз (Threat Model)

Выполнение произвольного Python-кода (`proposed_code` + `test_code`), сгенерированного LLM (особенно в присутствии ненадёжных внешних данных в контексте), создаёт 6 классов критических угроз, которые **не перекрываются** переменными окружения (`HTTP_PROXY=http://127.0.0.1:1`), очисткой `os.environ` или AST-эвристиками:

| ID | Вектор угрозы | Почему полумеры (`_scrubbed_env`, `HTTP_PROXY`, обычный `pytest`) не защищают | Требуемый барьер OS-уровня |
|---|---|---|---|
| **TM-1** | **Прямой сетевой выход и эксфильтрация** (`socket.connect`, `urllib`, DNS-туннелирование, raw sockets) | `HTTP_PROXY` игнорируется прямыми вызовами `socket.socket()` и `NO_PROXY`. | Полное отсутствие сетевых интерфейсов (Network Namespace с `lo` down / Windows AppContainer без `internetClient` и `privateNetworkClientServer` + WFP-фильтр). |
| **TM-2** | **Чтение хостовых файлов и секретов владельца** (`~/.ssh`, `%APPDATA%`, `.env`, SQLite БД Bossman, браузерные куки) | Обычный процесс `pytest` наследует права текущего пользователя ОС (`user_sid`) и видит весь диск `C:\` / `/home`. | Изолированная корневая ФС / виртуализированный том: read-only bind-mount только белого списка библиотек Python и read-write временный каталог (`scratch`), нулевая видимость профиля пользователя и `BCC_DATA_DIR`. |
| **TM-3** | **Запись вне рабочего дерева и побег через симлинки / NTFS junctions** | Проверка путей до запуска не мешает коду внутри `test_code` вызвать `open("C:/Users/...", "w")` или создать junction во время исполнения. | Запрет записи на уровне DACL / mount table везде, кроме одноразового временного каталога песочницы, плюс запрет создания симлинков/reparse points. |
| **TM-4** | **Повышение привилегий и атака на живые процессы Bossman** (`:8801`, `:8840`, `:11434`, `taskkill`, `ctypes.windll`) | Процесс с тем же UID/SID может открывать дескрипторы соседних процессов (`OpenProcess`), слать запросы на `127.0.0.1:8801/11434` и инжектировать код. | Untrusted Integrity Level / AppContainer SID / отдельный непривилегированный UID без доступа к loopback хоста и с `JOB_OBJECT_LIMIT_ACTIVE_PROCESS`. |
| **TM-5** | **Исчерпание ресурсов (DoS: fork-бомба, память, CPU, диск)** | Таймаут `asyncio.wait_for` не ограничивает потребление RAM до OOM хоста или порождение фоновых сиротских процессов. | Ядерные квоты: Windows Job Object (`JOB_OBJECT_LIMIT_PROCESS_MEMORY`, `JOB_OBJECT_LIMIT_JOB_MEMORY`, `JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE`, CPU Rate Control) / Linux cgroups v2 (`memory.max`, `pids.max`, `cpu.max`) + квота размера `scratch`. |
| **TM-6** | **Устойчивость (Persistence) после завершения или STOP** | Фоновый дочерний процесс, отвязанный через `CREATE_NEW_PROCESS_GROUP` / `setsid()`, переживает завершение `pytest`. | Принудительное уничтожение всего дерева через Job Object (`KILL_ON_JOB_CLOSE`) / PID namespace при выходе или по сигналу глобального `STOP` ($\le 1\text{–}5\text{ с}$), с последующим полным удалением временного каталога. |

---

## 3. Архитектура целевой OS-изоляции для `pc.self_modify`

Включение автоматического прогона тестов (`verification_status: "RUN"`) допускается **только** при наличии верифицированного драйвера OS-изоляции (`SandboxBackend`), удовлетворяющего всем 6 требованиям:

### 3.1. Сеть (No-Network by Kernel)
- **Windows**: запуск внутри одноразовой микро-ВМ (Windows Sandbox / WSL2 Hyper-V container с `--network=none`) либо AppContainer с нулевыми сетевыми capabilities **и** явным блокирующим правилом Windows Filtering Platform (WFP) на все исходящие/входящие пакеты, включая `127.0.0.1` и `::1` хоста.
- **Linux (CI / контейнер)**: `unshare --net` (отдельный сетевой namespace без veth-интерфейсов и без доступа к loopback хоста).

### 3.2. Файловая система (Allowlisted Read-Only Mounts + Ephemeral Scratch)
- Никакого прямого доступа к рабочему дереву репозитория или `BCC_DATA_DIR`.
- В песочницу монтируются:
  1. Минимальный интерпретатор Python + стандартная библиотека и `pytest` в режиме **Read-Only**;
  2. Одноразовый пустой каталог `sandbox_ws/` (размер $\le 64\text{ МБ}$), куда копируются **только** целевой модуль (`canonical_target_rel`) и синтетический тест (`test_rel`).
- После завершения проверки `sandbox_ws/` уничтожается целиком; из песочницы извлекаются только код возврата (`int`) и усечённые/очищенные от секретов `stdout`/`stderr` ($\le 16\text{ КБ}$).

### 3.3. Привилегии и лимиты ресурсов
- **Права**: Low/Untrusted Integrity Level (Windows) или непривилегированный `nobody`/dynamic UID (`unshare --user` на Linux).
- **Лимиты**:
  - Wall-clock timeout: $\le 15\text{ с}$ (и мгновенное прерывание по глобальному `STOP`);
  - RAM: $\le 512\text{ МБ}$ (`JOB_OBJECT_LIMIT_JOB_MEMORY` / `memory.max`);
  - Процессы: $\le 8$ (`JOB_OBJECT_LIMIT_ACTIVE_PROCESS` / `pids.max`);
  - CPU: $\le 1$ ядро (`JOB_OBJECT_CPU_RATE_CONTROL_ENABLE` / `cpu.max`).

---

## 4. Обязательный набор синтетических Escape-тестов (Acceptance Gate до включения исполнения)

Ни один драйвер песочницы не может перевести `pc.self_modify` из `NOT_RUN` в активный режим, пока на целевой ОС (включая Windows 11) не пройдут 7 обязательных негативных escape-тестов:

1. **Escape-1 (Write outside workspace)**: попытка записи в родительский каталог (`../`, `..\`) и в абсолютный путь хоста (`%TEMP%`, `/tmp` хоста) $\rightarrow$ отказ ядра ОС (`PermissionError` / `FileNotFoundError`), хостовый файл не создан.
2. **Escape-2 (Host file / secret read)**: попытка чтения файла-приманки (`canary_secret.txt`) в профиле пользователя и в `BCC_DATA_DIR` $\rightarrow$ файл невидим или `PermissionError`.
3. **Escape-3 (Raw socket & loopback network)**: попытка `socket.create_connection(("127.0.0.1", 8801))` и `socket.create_connection(("1.1.1.1", 53))` $\rightarrow$ немедленный `OSError` (`ENETUNREACH` / `EACCES`).
4. **Escape-4 (Path traversal & Windows separator normalization)**: передача `..\host_target.py`, `../host_target.py` и смешанных путей $\rightarrow$ детерминированный отказ `blocked_outside_workspace` на этапе precheck на всех ОС.
5. **Escape-5 (Symlink / NTFS Junction escape)**: попытка обратиться к симлинку/junction внутри `skills/`, указывающему наружу $\rightarrow$ отказ `blocked_outside_workspace` (и `blocked_unsafe_rollback_record` при откате).
6. **Escape-6 (Resource & process tree cleanup on STOP / timeout)**: запуск бесконечного цикла с порождением дочернего процесса внутри песочницы + сигнал `STOP` $\rightarrow$ полное уничтожение дерева процессов за $\le 5\text{ с}$ и очистка временной папки.
7. **Escape-7 (Immutable core & rollback integrity)**: попытка изменить или откатить файл из неизменяемого ядра (`approvals.py`, `permissions.py`, `secrets.py`, `auth.py`, `control_plane.py`, `docs/constitution/*`) $\rightarrow$ `blocked_immutable_core` / `blocked_unsafe_rollback_record` с `owner_alert: True`.
