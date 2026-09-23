"""Owner-only Claude Code bridge for the Telegram companion.

Restored on the owner's explicit decision (2026-09-22) after the direct execution path
was removed. Only the Claude Code part comes back: there is still no raw shell (/sh).
Glue over the official headless CLI (`claude -p --output-format json --resume`); the
prompt goes through stdin, never through a command line. Off unless the local config
sets ``claude_bridge: true``; guests never reach it; STOP cancels a running turn and
kills the process tree.
"""
from __future__ import annotations

import asyncio
import contextlib
import json
import os
import shutil
import signal
import subprocess

PERMISSION_MODES = frozenset({"default", "acceptEdits", "plan", "bypassPermissions"})

# Environment of a parent Claude Code session must not leak into the child CLI,
# and the companion's own secrets never reach it.
_CHILD_ENV_DROP = ("CLAUDECODE", "CLAUDE_CODE_ENTRYPOINT", "CLAUDE_CODE_SSE_PORT")


def _child_env() -> dict:
    env = {k: v for k, v in os.environ.items()
           if k not in _CHILD_ENV_DROP and not k.startswith("TG_COMPANION_")}
    env.setdefault("PYTHONIOENCODING", "utf-8")
    return env


_WINDOWS = os.name == "nt"
# taskkill ran with no timeout, synchronously inside the event loop: a hung taskkill
# froze the whole companion exactly when it was stopping a runaway agent.
TASKKILL_TIMEOUT_S = 30.0


def _kill_tree(pid: int):
    """Kill the agent and its descendants. Windows: taskkill /T (bounded); elsewhere
    the agent leads its own session (see _run), so its whole group goes."""
    if not _WINDOWS:
        with contextlib.suppress(OSError):
            os.killpg(pid, signal.SIGKILL)
        return
    with open(os.devnull, "wb") as null, contextlib.suppress(OSError, subprocess.SubprocessError):
        subprocess.run(["taskkill", "/T", "/F", "/PID", str(pid)], stdout=null, stderr=null, check=False,
                       timeout=TASKKILL_TIMEOUT_S)


async def _reap(proc) -> None:
    """Kill the tree, then make sure the direct child is gone and reaped."""
    _kill_tree(proc.pid)
    with contextlib.suppress(ProcessLookupError):
        proc.kill()
    with contextlib.suppress(asyncio.TimeoutError, ProcessLookupError):
        await asyncio.wait_for(proc.wait(), 10)


async def _run(args: list[str], *, stdin: bytes, cwd: str, timeout: float) -> tuple[int | None, bytes, bytes]:
    """Run a process; on timeout or cancel kill its whole tree."""
    proc = await asyncio.create_subprocess_exec(
        *args, cwd=cwd or None, env=_child_env(),
        stdin=asyncio.subprocess.PIPE, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE,
        creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
        **({} if _WINDOWS else {"start_new_session": True}))
    try:
        out, err = await asyncio.wait_for(proc.communicate(stdin), timeout)
        return proc.returncode, out, err
    except asyncio.TimeoutError:
        await _reap(proc)
        return None, b"", b""
    except asyncio.CancelledError:
        await _reap(proc)   # /claude_stop, STOP or shutdown: never leave an orphaned agent
        raise


def _decode(data: bytes) -> str:
    for enc in ("utf-8", "cp866", "cp1251"):
        try:
            return data.decode(enc)
        except UnicodeDecodeError:
            continue
    return data.decode("utf-8", "replace")


def claude_binary() -> str | None:
    return shutil.which("claude")


def codex_binary() -> str | None:
    return shutil.which("codex")


CODEX_SANDBOXES = frozenset({"read-only", "workspace-write", "danger-full-access"})


async def codex(prompt: str, *, session: str | None, cwd: str, sandbox: str,
                timeout: float) -> tuple[str, str | None]:
    """One Codex CLI turn (`codex exec --json`, prompt via stdin). Returns (text, thread id)."""
    import tempfile
    if sandbox not in CODEX_SANDBOXES:
        raise ValueError("invalid codex sandbox")
    binary = codex_binary()
    if not binary:
        raise RuntimeError("CODEX_CLI_NOT_FOUND")
    fd, last = tempfile.mkstemp(prefix="bossman-codex-", suffix=".txt")
    os.close(fd)
    try:
        args = [binary, "exec"]
        if session:
            args += ["resume", session]
        args += ["--json", "-s", sandbox, "--skip-git-repo-check", "-o", last]
        if not session and cwd:
            args += ["-C", cwd]
        args.append("-")
        code, out, err = await _run(args, stdin=prompt.encode("utf-8"), cwd=cwd, timeout=timeout)
        if code is None:
            return f"⏱ Codex не уложился в {int(timeout // 60)} мин и остановлен.", session
        thread = session
        for line in _decode(out).splitlines():
            try:
                event = json.loads(line)
            except ValueError:
                continue
            if isinstance(event, dict) and event.get("type") == "thread.started":
                thread = str(event.get("thread_id") or "") or thread
        with open(last, encoding="utf-8", errors="replace") as fh:
            text = fh.read().strip()
        if not text:
            tail = (_decode(err) or _decode(out)).strip()[-1500:]
            if session and code != 0:
                return await codex(prompt, session=None, cwd=cwd, sandbox=sandbox, timeout=timeout)
            return f"Codex завершился с кодом {code}.\n{tail}", thread
        return text, thread
    finally:
        with contextlib.suppress(OSError):
            os.unlink(last)


async def claude(prompt: str, *, session: str | None, cwd: str, permission_mode: str,
                 timeout: float) -> tuple[str, str | None, float | None]:
    """One Claude Code turn. Returns (text, session id to resume, cost in USD or None)."""
    if permission_mode not in PERMISSION_MODES:
        raise ValueError("invalid permission mode")
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
