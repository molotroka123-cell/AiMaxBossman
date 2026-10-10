"""Parent side of the code-mode sandbox.

The model's code runs in a CHILD PROCESS (`_child.py`, started with `-I -S`, a
scrubbed environment and a throw-away working directory). The only channel
between the two is a JSON-lines protocol on stdin/stdout. The child can ask the
parent to call a tool; the parent answers through `host_call`, which the engine
wires to its ordinary per-call pipeline (grant check, malformed-args check,
policy, owner approval, leases, journaling, STOP). Nothing in this module
decides permissions - it only moves bytes and enforces resource limits:

  * wall-clock budget for the child's own compute (time spent inside a tool
    call is NOT charged to the code, tools have their own timeouts);
  * cap on the number of tool calls, on argument size and on result size;
  * cap on printed output (enforced in the child, re-checked here);
  * memory ceiling: POSIX RLIMIT_AS, Windows Job Object (best effort - the
    outcome says whether it was applied);
  * the child is always killed on timeout, STOP, abort, protocol violation and
    cancellation.

NOT provided here (documented in docs/architecture/CODE_MODE_FACADE_RU.md):
OS-level network denial and filesystem ACLs for the child. The dialect has no
import/open/socket, so the child has no path to them short of an interpreter
escape; the parent's limits and the absence of secrets in its environment are
the second line.
"""
from __future__ import annotations

import asyncio
import json
import os
import shutil
import sys
import tempfile
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Awaitable, Callable

from . import _child

CHILD_PATH = Path(_child.__file__).resolve()
CodeRejected = _child.CodeRejected
validate_code = _child.validate_code
MAX_CODE_CHARS = _child.MAX_CODE_CHARS


@dataclass(frozen=True)
class SandboxLimits:
    wall_seconds: float = 60.0
    max_calls: int = 25
    max_output_chars: int = 8000
    max_arg_chars: int = 100_000
    max_result_chars: int = 200_000
    memory_mb: int = 512


class AbortRun(Exception):
    """Raised by a host callback to stop the sandbox immediately.

    `kind` is "ask" (the call needs an owner decision), "stop" (owner STOP) or
    "other"."""

    def __init__(self, reason: str, kind: str = "other") -> None:
        super().__init__(reason)
        self.reason = reason
        self.kind = kind


@dataclass
class SandboxOutcome:
    # ok | error | rejected | timeout | aborted | violation
    status: str
    output: str = ""
    value: Any = None
    error: str = ""
    truncated: bool = False
    calls: int = 0
    abort_kind: str = ""
    abort_reason: str = ""
    memory_limited: bool = False


HostCall = Callable[[str, dict], Awaitable[dict]]


# --------------------------------------------------------------- resource limits

def _windows_job(pid: int, memory_mb: int) -> Any:
    """Put the child into a Job Object with a per-process memory ceiling, a
    one-process active limit and kill-on-close. Returns the job handle (close it
    to kill the child) or None when unavailable."""
    import ctypes
    from ctypes import wintypes

    k32 = ctypes.WinDLL("kernel32", use_last_error=True)
    k32.OpenProcess.restype = wintypes.HANDLE
    k32.OpenProcess.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]
    k32.CreateJobObjectW.restype = wintypes.HANDLE
    k32.CreateJobObjectW.argtypes = [ctypes.c_void_p, wintypes.LPCWSTR]
    k32.SetInformationJobObject.restype = wintypes.BOOL
    k32.SetInformationJobObject.argtypes = [wintypes.HANDLE, ctypes.c_int, ctypes.c_void_p, wintypes.DWORD]
    k32.AssignProcessToJobObject.restype = wintypes.BOOL
    k32.AssignProcessToJobObject.argtypes = [wintypes.HANDLE, wintypes.HANDLE]
    k32.CloseHandle.argtypes = [wintypes.HANDLE]

    class IO_COUNTERS(ctypes.Structure):
        _fields_ = [(n, ctypes.c_ulonglong) for n in (
            "ReadOperationCount", "WriteOperationCount", "OtherOperationCount",
            "ReadTransferCount", "WriteTransferCount", "OtherTransferCount")]

    class BASIC(ctypes.Structure):
        _fields_ = [("PerProcessUserTimeLimit", ctypes.c_int64), ("PerJobUserTimeLimit", ctypes.c_int64),
                    ("LimitFlags", wintypes.DWORD), ("MinimumWorkingSetSize", ctypes.c_size_t),
                    ("MaximumWorkingSetSize", ctypes.c_size_t), ("ActiveProcessLimit", wintypes.DWORD),
                    ("Affinity", ctypes.c_size_t), ("PriorityClass", wintypes.DWORD),
                    ("SchedulingClass", wintypes.DWORD)]

    class EXTENDED(ctypes.Structure):
        _fields_ = [("BasicLimitInformation", BASIC), ("IoInfo", IO_COUNTERS),
                    ("ProcessMemoryLimit", ctypes.c_size_t), ("JobMemoryLimit", ctypes.c_size_t),
                    ("PeakProcessMemoryUsed", ctypes.c_size_t), ("PeakJobMemoryUsed", ctypes.c_size_t)]

    PROCESS_SET_QUOTA, PROCESS_TERMINATE = 0x0100, 0x0001
    LIMIT_ACTIVE_PROCESS, LIMIT_PROCESS_MEMORY, LIMIT_KILL_ON_CLOSE = 0x8, 0x100, 0x2000
    proc = k32.OpenProcess(PROCESS_SET_QUOTA | PROCESS_TERMINATE, False, pid)
    if not proc:
        return None
    job = k32.CreateJobObjectW(None, None)
    if not job:
        k32.CloseHandle(proc)
        return None
    info = EXTENDED()
    info.BasicLimitInformation.LimitFlags = LIMIT_ACTIVE_PROCESS | LIMIT_PROCESS_MEMORY | LIMIT_KILL_ON_CLOSE
    info.BasicLimitInformation.ActiveProcessLimit = 1
    info.ProcessMemoryLimit = int(memory_mb) * 1024 * 1024
    ok = bool(k32.SetInformationJobObject(job, 9, ctypes.byref(info), ctypes.sizeof(info))) \
        and bool(k32.AssignProcessToJobObject(job, proc))
    k32.CloseHandle(proc)
    if not ok:
        k32.CloseHandle(job)
        return None
    return (k32, job)


def _close_job(job: Any) -> None:
    if job:
        try:
            job[0].CloseHandle(job[1])
        except Exception:  # noqa: BLE001
            pass


def _child_env() -> dict[str, str]:
    """No inherited secrets, tokens or provider keys: only what the interpreter needs to start."""
    env = {"PYTHONIOENCODING": "utf-8", "PYTHONUTF8": "1", "PYTHONDONTWRITEBYTECODE": "1"}
    for key in ("SYSTEMROOT", "SYSTEMDRIVE", "WINDIR", "TEMP", "TMP", "LANG", "LC_ALL"):
        if os.environ.get(key):
            env[key] = os.environ[key]
    return env


async def _spawn(workdir: str, memory_mb: int) -> tuple[asyncio.subprocess.Process, Any]:
    if "python" not in Path(sys.executable or "").name.lower():
        # A frozen/embedded launcher would start the APPLICATION again with these arguments.
        raise RuntimeError(f"no standalone Python interpreter for the sandbox ({sys.executable!r})")
    kwargs: dict[str, Any] = {}
    if os.name == "nt":
        import subprocess as _sp
        kwargs["creationflags"] = getattr(_sp, "CREATE_NO_WINDOW", 0)
    else:
        def _preexec() -> None:     # pragma: no cover - POSIX only
            import resource
            limit = int(memory_mb) * 1024 * 1024
            resource.setrlimit(resource.RLIMIT_AS, (limit, limit))
            resource.setrlimit(resource.RLIMIT_NPROC, (64, 64))
        kwargs["preexec_fn"] = _preexec
    proc = await asyncio.create_subprocess_exec(
        sys.executable, "-I", "-S", str(CHILD_PATH),
        stdin=asyncio.subprocess.PIPE, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.DEVNULL,
        cwd=workdir, env=_child_env(), limit=8 * 1024 * 1024, **kwargs)
    job = None
    if os.name == "nt":
        try:
            job = _windows_job(proc.pid, memory_mb)
        except Exception:  # noqa: BLE001 - best effort
            job = None
    return proc, job


async def _kill(proc: asyncio.subprocess.Process) -> None:
    if proc.returncode is None:
        try:
            proc.kill()
        except ProcessLookupError:
            pass
    try:
        await asyncio.wait_for(proc.wait(), timeout=5.0)
    except Exception:  # noqa: BLE001
        pass


def _clip(text: str, limit: int) -> str:
    return text if len(text) <= limit else text[:limit] + f"\n...[cut: {len(text) - limit} chars]"


# ------------------------------------------------------------------ the runner

async def run_sandboxed(code: str, tools: dict[str, str], host_call: HostCall,
                        limits: SandboxLimits | None = None) -> SandboxOutcome:
    """Run `code` with `tools` = {python_name: api_name}. Never raises for code or
    tool problems; CancelledError propagates after the child is killed."""
    limits = limits or SandboxLimits()
    try:
        validate_code(code, frozenset(tools))
    except CodeRejected as exc:
        return SandboxOutcome(status="rejected", error=str(exc))

    api_names = set(tools.values())
    workdir = tempfile.mkdtemp(prefix="bcc-codemode-")
    proc = None
    job = None
    calls = 0
    try:
        try:
            proc, job = await _spawn(workdir, limits.memory_mb)
        except (OSError, RuntimeError) as exc:
            return SandboxOutcome(status="error", error=f"sandbox unavailable: {exc}"[:300])
        outcome_base = {"memory_limited": job is not None or os.name != "nt"}
        init = {"code": code, "tools": tools, "max_output_chars": limits.max_output_chars,
                "max_calls": limits.max_calls, "max_arg_chars": limits.max_arg_chars}
        proc.stdin.write((json.dumps(init) + "\n").encode("utf-8"))
        await proc.stdin.drain()
        budget = float(limits.wall_seconds)
        last_out, last_trunc = "", False       # what the code printed before its latest tool call
        while True:
            started = time.monotonic()
            try:
                raw = await asyncio.wait_for(proc.stdout.readline(), timeout=max(0.05, budget))
            except asyncio.TimeoutError:
                await _kill(proc)
                return SandboxOutcome(status="timeout", calls=calls, **outcome_base,
                                      output=_clip(last_out, limits.max_output_chars), truncated=last_trunc,
                                      error=f"code exceeded {limits.wall_seconds:.0f} s of its own compute")
            except (asyncio.LimitOverrunError, ValueError):
                await _kill(proc)
                return SandboxOutcome(status="violation", calls=calls, error="protocol line too long", **outcome_base)
            budget -= time.monotonic() - started
            if not raw:
                await _kill(proc)
                return SandboxOutcome(status="error", calls=calls, **outcome_base,
                                      error=f"sandbox exited unexpectedly (code {proc.returncode})")
            try:
                msg = json.loads(raw.decode("utf-8"))
            except (ValueError, UnicodeDecodeError):
                await _kill(proc)
                return SandboxOutcome(status="violation", calls=calls, error="malformed sandbox message", **outcome_base)
            if not isinstance(msg, dict):
                await _kill(proc)
                return SandboxOutcome(status="violation", calls=calls, error="malformed sandbox message", **outcome_base)

            kind = msg.get("t")
            if kind == "done":
                status = str(msg.get("status") or "error")
                if status not in ("ok", "error", "rejected"):
                    status = "error"
                await _kill(proc)
                return SandboxOutcome(
                    status=status, output=_clip(str(msg.get("output") or ""), limits.max_output_chars),
                    value=msg.get("value"), error=str(msg.get("error") or "")[:500],
                    truncated=bool(msg.get("truncated")), calls=calls, **outcome_base)

            if kind != "call":
                await _kill(proc)
                return SandboxOutcome(status="violation", calls=calls, error="unknown sandbox message", **outcome_base)

            tool = msg.get("tool")
            args = msg.get("args")
            if (tool not in api_names or not isinstance(args, dict) or msg.get("id") != calls + 1
                    or len(json.dumps(args)) > limits.max_arg_chars or calls >= limits.max_calls):
                await _kill(proc)
                return SandboxOutcome(status="violation", calls=calls, error="illegal tool call from sandbox",
                                      **outcome_base)
            calls += 1
            last_out = str(msg.get("out") or "")[:limits.max_output_chars]
            last_trunc = bool(msg.get("trunc"))
            try:
                reply = await host_call(str(tool), args)
                reply_line = {"t": "ret", "id": calls, "result": {
                    "ok": bool(reply.get("ok")),
                    "content": _clip(str(reply.get("content") or ""), limits.max_result_chars),
                    **({"status": str(reply["status"])} if reply.get("status") else {})}}
            except AbortRun as exc:
                await _kill(proc)
                return SandboxOutcome(status="aborted", calls=calls, abort_kind=exc.kind,
                                      abort_reason=exc.reason, output=last_out, truncated=last_trunc,
                                      **outcome_base)
            except asyncio.CancelledError:
                raise
            except Exception as exc:  # noqa: BLE001 - a broken host is data for the code, not a crash
                reply_line = {"t": "err", "id": calls, "error": f"{type(exc).__name__}: {exc}"[:300]}
            proc.stdin.write((json.dumps(reply_line) + "\n").encode("utf-8"))
            try:
                await proc.stdin.drain()
            except (ConnectionResetError, BrokenPipeError):
                return SandboxOutcome(status="error", calls=calls, error="sandbox closed its input", **outcome_base)
    finally:
        if proc is not None:
            await _kill(proc)
        _close_job(job)
        shutil.rmtree(workdir, ignore_errors=True)
