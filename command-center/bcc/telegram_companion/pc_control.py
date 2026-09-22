"""Owner-only computer control from Telegram.

Glue only (owner rule: reuse, don't rebuild): Claude Code is driven through its
official headless CLI (`claude -p --output-format json --resume`), shell commands
through Windows PowerShell, screenshots through .NET System.Drawing. Nothing here
is reachable by guests; the switch is `pc_control` in the local config.
"""
from __future__ import annotations

import asyncio
import base64
import json
import os
import shutil
import subprocess

SHELL_TIMEOUT = 120
OUTPUT_LIMIT = 12000

SCREENSHOT_PS = r"""
Add-Type -AssemblyName System.Windows.Forms, System.Drawing
$b = [System.Windows.Forms.SystemInformation]::VirtualScreen
$bmp = New-Object System.Drawing.Bitmap $b.Width, $b.Height
$g = [System.Drawing.Graphics]::FromImage($bmp)
$g.CopyFromScreen($b.Left, $b.Top, 0, 0, $bmp.Size)
$ms = New-Object System.IO.MemoryStream
$bmp.Save($ms, [System.Drawing.Imaging.ImageFormat]::Png)
[Convert]::ToBase64String($ms.ToArray())
"""

# Environment of a parent Claude Code session must not leak into the child CLI.
_CHILD_ENV_DROP = ("CLAUDECODE", "CLAUDE_CODE_ENTRYPOINT", "CLAUDE_CODE_SSE_PORT")


def _child_env() -> dict:
    env = {k: v for k, v in os.environ.items() if k not in _CHILD_ENV_DROP and not k.startswith("TG_COMPANION_")}
    env.setdefault("PYTHONIOENCODING", "utf-8")
    return env


def _kill_tree(pid: int):
    with open(os.devnull, "wb") as null:
        subprocess.run(["taskkill", "/T", "/F", "/PID", str(pid)], stdout=null, stderr=null, check=False)


async def _run(args: list[str], *, stdin: bytes | None = None, cwd: str | None = None,
               timeout: float = SHELL_TIMEOUT) -> tuple[int | None, bytes, bytes]:
    """Run a process; on timeout kill its whole tree and return code None."""
    proc = await asyncio.create_subprocess_exec(
        *args, cwd=cwd or None, env=_child_env(),
        stdin=asyncio.subprocess.PIPE if stdin is not None else asyncio.subprocess.DEVNULL,
        stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE,
        creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
    try:
        out, err = await asyncio.wait_for(proc.communicate(stdin), timeout)
        return proc.returncode, out, err
    except asyncio.TimeoutError:
        _kill_tree(proc.pid)
        return None, b"", b""
    except asyncio.CancelledError:
        _kill_tree(proc.pid)   # /claude_stop or shutdown: never leave an orphaned agent running
        raise


def _powershell_args(script: str) -> list[str]:
    # -EncodedCommand: no quoting/injection issues between Python and PowerShell.
    encoded = base64.b64encode(script.encode("utf-16-le")).decode("ascii")
    return ["powershell.exe", "-NoProfile", "-NonInteractive", "-ExecutionPolicy", "Bypass",
            "-EncodedCommand", encoded]


def _decode(data: bytes) -> str:
    for enc in ("utf-8", "cp866", "cp1251"):
        try:
            return data.decode(enc)
        except UnicodeDecodeError:
            continue
    return data.decode("utf-8", "replace")


async def shell(command: str, cwd: str | None = None) -> str:
    script = "[Console]::OutputEncoding = [Text.Encoding]::UTF8\n$ProgressPreference='SilentlyContinue'\n" + command
    code, out, err = await _run(_powershell_args(script), cwd=cwd)
    if code is None:
        return f"⏱ Команда не уложилась в {SHELL_TIMEOUT} с и остановлена."
    text = (_decode(out) + ("\n[stderr]\n" + _decode(err) if err.strip() else "")).strip()
    if len(text) > OUTPUT_LIMIT:
        text = "…\n" + text[-OUTPUT_LIMIT:]
    return f"💻 код выхода {code}\n\n{text or '(пусто)'}"


async def screenshot() -> bytes:
    code, out, _ = await _run(_powershell_args(SCREENSHOT_PS), timeout=60)
    if code != 0 or not out.strip():
        raise RuntimeError("SCREENSHOT_FAILED")
    return base64.b64decode(out.strip())


def claude_binary() -> str | None:
    return shutil.which("claude")


async def claude(prompt: str, *, session: str | None, cwd: str, permission_mode: str,
                 timeout: float) -> tuple[str, str | None, float | None]:
    """One Claude Code turn; the prompt goes via stdin (never through a shell command line)."""
    binary = claude_binary()
    if not binary:
        raise RuntimeError("CLAUDE_CLI_NOT_FOUND")
    args = [binary, "-p", "--output-format", "json", "--permission-mode", permission_mode]
    if session:
        args += ["--resume", session]
    code, out, err = await _run(args, stdin=prompt.encode("utf-8"), cwd=cwd, timeout=timeout)
    if code is None:
        return f"⏱ Claude не уложился в {int(timeout // 60)} мин и остановлен.", session, None
    try:
        data = json.loads(_decode(out).strip().splitlines()[-1])
    except (ValueError, IndexError):
        tail = (_decode(err) or _decode(out)).strip()[-1500:]
        if session and "session" in tail.lower():
            # Stale session id: start fresh instead of failing forever.
            return await claude(prompt, session=None, cwd=cwd, permission_mode=permission_mode, timeout=timeout)
        return f"Claude завершился с кодом {code}.\n{tail}", session, None
    text = str(data.get("result") or "").strip() or "(Claude ничего не ответил)"
    if data.get("is_error"):
        text = "⚠️ " + text
    return text, data.get("session_id") or session, data.get("total_cost_usd")
