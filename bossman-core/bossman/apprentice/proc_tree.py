"""Start a child so that it can be killed WITH its descendants.

``subprocess.run(..., timeout=)`` kills only the direct child: a sidecar's test
run, a server a test started, a hung ``git`` — all survive as orphans that hold
files open (WinError 32 on cleanup) and keep ports busy. Here the child is:

  * POSIX: the leader of a new session, so ``killpg`` reaches every descendant
    that did not deliberately escape into its own session;
  * Windows: assigned to a Job Object with KILL_ON_JOB_CLOSE, so terminating
    the job (or this process dying) ends the whole tree, including grandchildren
    whose parent has already exited — something ``taskkill /T`` cannot find.

``run_tree`` is ``subprocess.run`` with that guarantee on timeout AND after a
normal exit (a finished child's leftover grandchildren are reaped too).
"""
from __future__ import annotations

import contextlib
import os
import signal
import subprocess
from dataclasses import dataclass
from typing import Any, Sequence


class _WinJob:
    """Minimal Job Object wrapper (ctypes, no pywin32)."""

    def __init__(self) -> None:
        import ctypes
        from ctypes import wintypes

        k32 = ctypes.WinDLL("kernel32", use_last_error=True)
        k32.CreateJobObjectW.restype = wintypes.HANDLE
        k32.CreateJobObjectW.argtypes = [ctypes.c_void_p, wintypes.LPCWSTR]
        k32.SetInformationJobObject.argtypes = [wintypes.HANDLE, ctypes.c_int, ctypes.c_void_p, wintypes.DWORD]
        k32.AssignProcessToJobObject.argtypes = [wintypes.HANDLE, wintypes.HANDLE]
        k32.TerminateJobObject.argtypes = [wintypes.HANDLE, wintypes.UINT]
        k32.CloseHandle.argtypes = [wintypes.HANDLE]

        class IoCounters(ctypes.Structure):
            _fields_ = [(n, ctypes.c_ulonglong) for n in (
                "ReadOperationCount", "WriteOperationCount", "OtherOperationCount",
                "ReadTransferCount", "WriteTransferCount", "OtherTransferCount")]

        class Basic(ctypes.Structure):
            _fields_ = [("PerProcessUserTimeLimit", ctypes.c_int64), ("PerJobUserTimeLimit", ctypes.c_int64),
                        ("LimitFlags", wintypes.DWORD), ("MinimumWorkingSetSize", ctypes.c_size_t),
                        ("MaximumWorkingSetSize", ctypes.c_size_t), ("ActiveProcessLimit", wintypes.DWORD),
                        ("Affinity", ctypes.c_size_t), ("PriorityClass", wintypes.DWORD),
                        ("SchedulingClass", wintypes.DWORD)]

        class Extended(ctypes.Structure):
            _fields_ = [("BasicLimitInformation", Basic), ("IoInfo", IoCounters),
                        ("ProcessMemoryLimit", ctypes.c_size_t), ("JobMemoryLimit", ctypes.c_size_t),
                        ("PeakProcessMemoryUsed", ctypes.c_size_t), ("PeakJobMemoryUsed", ctypes.c_size_t)]

        self._k32 = k32
        self.handle = k32.CreateJobObjectW(None, None)
        if not self.handle:
            raise OSError(ctypes.get_last_error(), "CreateJobObjectW failed")
        info = Extended()
        info.BasicLimitInformation.LimitFlags = 0x2000  # JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE
        if not k32.SetInformationJobObject(self.handle, 9, ctypes.byref(info), ctypes.sizeof(info)):
            raise OSError(ctypes.get_last_error(), "SetInformationJobObject failed")

    def assign(self, proc: subprocess.Popen) -> bool:
        return bool(self._k32.AssignProcessToJobObject(self.handle, int(proc._handle)))  # noqa: SLF001

    def terminate(self) -> None:
        if self.handle:
            self._k32.TerminateJobObject(self.handle, 1)

    def close(self) -> None:
        if self.handle:
            self._k32.CloseHandle(self.handle)
            self.handle = None


class ProcessTree:
    """A started child plus the means to kill everything it spawned."""

    def __init__(self, cmd: Sequence[str], **popen_kw: Any) -> None:
        self.job: _WinJob | None = None
        if os.name == "nt":
            popen_kw["creationflags"] = popen_kw.get("creationflags", 0) | subprocess.CREATE_NEW_PROCESS_GROUP
            with contextlib.suppress(OSError, AttributeError):
                self.job = _WinJob()
        else:
            popen_kw["start_new_session"] = True
        self.proc = subprocess.Popen(list(cmd), **popen_kw)
        if self.job is not None and not self.job.assign(self.proc):
            self.job.close()
            self.job = None

    @property
    def pid(self) -> int:
        return self.proc.pid

    def kill(self) -> None:
        """Kill the child and every descendant still in its group/job. Idempotent."""
        if os.name == "nt":
            if self.job is not None:
                self.job.terminate()
            else:
                with contextlib.suppress(OSError, subprocess.SubprocessError):
                    subprocess.run(["taskkill", "/F", "/T", "/PID", str(self.proc.pid)],
                                   capture_output=True, timeout=30)
        else:
            with contextlib.suppress(OSError):
                os.killpg(self.proc.pid, signal.SIGKILL)
        with contextlib.suppress(OSError):
            self.proc.kill()
        with contextlib.suppress(Exception):
            self.proc.wait(timeout=10)

    def close(self) -> None:
        self.kill()
        if self.job is not None:
            self.job.close()
            self.job = None


@dataclass
class TreeResult:
    returncode: int | None
    stdout: Any
    stderr: Any
    timed_out: bool


def run_tree(cmd: Sequence[str], *, input: Any = None, timeout: float | None = None,  # noqa: A002
             on_start: Any = None, **popen_kw: Any) -> TreeResult:
    """``subprocess.run`` whose timeout (and normal exit) reaps the whole tree.
    ``on_start(tree)`` lets a caller keep the handle to cancel it (owner STOP)."""
    if input is not None:
        popen_kw.setdefault("stdin", subprocess.PIPE)
    tree = ProcessTree(cmd, **popen_kw)
    if on_start is not None:
        on_start(tree)
    timed_out = False
    try:
        try:
            out, err = tree.proc.communicate(input=input, timeout=timeout)
        except subprocess.TimeoutExpired:
            timed_out = True
            tree.kill()
            out, err = tree.proc.communicate()
    finally:
        tree.close()
    return TreeResult(None if timed_out else tree.proc.returncode, out, err, timed_out)
