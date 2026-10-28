"""Windows Job Object process-tree containment (audit 28.10, 6.2 gap 3 / PLG-UP-1 / DEV-UP-2 building block).

`taskkill /T /F` walks the tree it can see at that moment: a child spawned in between escapes. A Job Object is held by
the kernel: every process started inside it, at any depth, dies when the job is terminated or its last handle closes
(JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE), even if Bossman itself crashes. The job can also cap the number of live
processes and the job's memory.

`spawn_in_job` removes the start-up race: the process is created SUSPENDED, put into the job, then resumed, so it can
not spawn a child before the job holds it. Windows only; elsewhere every entry point raises NotImplementedError and the
caller keeps its existing kill path. This module is a primitive: it is not wired into STOP here. (bcc/code_mode/sandbox.py
has its own single-child job with a per-process memory ceiling; this one is for whole trees.)
"""
from __future__ import annotations

import ctypes
import subprocess
import sys
from ctypes import wintypes
from typing import Any

IS_WINDOWS = sys.platform == "win32"

JOB_OBJECT_LIMIT_ACTIVE_PROCESS = 0x00000008
JOB_OBJECT_LIMIT_JOB_MEMORY = 0x00000200
JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE = 0x00002000
JobObjectBasicAccountingInformation = 1
JobObjectExtendedLimitInformation = 9
CREATE_SUSPENDED = 0x00000004


def _require_windows() -> None:
    if not IS_WINDOWS:
        raise NotImplementedError("Windows Job Objects exist only on Windows")


if IS_WINDOWS:
    class IO_COUNTERS(ctypes.Structure):
        _fields_ = [(n, ctypes.c_ulonglong) for n in ("ReadOperationCount", "WriteOperationCount", "OtherOperationCount",
                                                      "ReadTransferCount", "WriteTransferCount", "OtherTransferCount")]

    class JOBOBJECT_BASIC_LIMIT_INFORMATION(ctypes.Structure):
        _fields_ = [("PerProcessUserTimeLimit", ctypes.c_longlong), ("PerJobUserTimeLimit", ctypes.c_longlong),
                    ("LimitFlags", wintypes.DWORD), ("MinimumWorkingSetSize", ctypes.c_size_t),
                    ("MaximumWorkingSetSize", ctypes.c_size_t), ("ActiveProcessLimit", wintypes.DWORD),
                    ("Affinity", ctypes.c_size_t), ("PriorityClass", wintypes.DWORD), ("SchedulingClass", wintypes.DWORD)]

    class JOBOBJECT_EXTENDED_LIMIT_INFORMATION(ctypes.Structure):
        _fields_ = [("BasicLimitInformation", JOBOBJECT_BASIC_LIMIT_INFORMATION), ("IoInfo", IO_COUNTERS),
                    ("ProcessMemoryLimit", ctypes.c_size_t), ("JobMemoryLimit", ctypes.c_size_t),
                    ("PeakProcessMemoryUsed", ctypes.c_size_t), ("PeakJobMemoryUsed", ctypes.c_size_t)]

    class JOBOBJECT_BASIC_ACCOUNTING_INFORMATION(ctypes.Structure):
        _fields_ = [("TotalUserTime", ctypes.c_longlong), ("TotalKernelTime", ctypes.c_longlong),
                    ("ThisPeriodTotalUserTime", ctypes.c_longlong), ("ThisPeriodTotalKernelTime", ctypes.c_longlong),
                    ("TotalPageFaultCount", wintypes.DWORD), ("TotalProcesses", wintypes.DWORD),
                    ("ActiveProcesses", wintypes.DWORD), ("TotalTerminatedProcesses", wintypes.DWORD)]

    _k32 = ctypes.WinDLL("kernel32", use_last_error=True)
    _nt = ctypes.WinDLL("ntdll")
    _k32.CreateJobObjectW.restype = wintypes.HANDLE
    _k32.CreateJobObjectW.argtypes = [wintypes.LPVOID, wintypes.LPCWSTR]
    _k32.SetInformationJobObject.argtypes = [wintypes.HANDLE, ctypes.c_int, wintypes.LPVOID, wintypes.DWORD]
    _k32.QueryInformationJobObject.argtypes = [wintypes.HANDLE, ctypes.c_int, wintypes.LPVOID, wintypes.DWORD,
                                               ctypes.POINTER(wintypes.DWORD)]
    _k32.AssignProcessToJobObject.argtypes = [wintypes.HANDLE, wintypes.HANDLE]
    _k32.TerminateJobObject.argtypes = [wintypes.HANDLE, wintypes.UINT]
    _k32.CloseHandle.argtypes = [wintypes.HANDLE]
    _k32.OpenProcess.restype = wintypes.HANDLE
    _k32.OpenProcess.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]
    _nt.NtResumeProcess.argtypes = [wintypes.HANDLE]
    PROCESS_SET_QUOTA, PROCESS_TERMINATE = 0x0100, 0x0001


class JobObjectError(OSError):
    pass


def _check(ok: Any, what: str) -> None:
    if not ok:
        raise JobObjectError(ctypes.get_last_error(), f"{what} failed (WinError {ctypes.get_last_error()})")


class JobObject:
    """A kernel job. `kill_on_close` (default) makes closing the last handle kill every process still inside it."""

    def __init__(self, *, kill_on_close: bool = True, active_process_limit: int | None = None,
                 memory_limit_bytes: int | None = None) -> None:
        _require_windows()
        if active_process_limit is not None and active_process_limit < 1:
            raise ValueError("active_process_limit must be >= 1")
        if memory_limit_bytes is not None and memory_limit_bytes < 1 << 20:
            raise ValueError("memory_limit_bytes must be at least 1 MiB")
        self._h = _k32.CreateJobObjectW(None, None)
        _check(self._h, "CreateJobObject")
        info = JOBOBJECT_EXTENDED_LIMIT_INFORMATION()
        flags = 0
        if kill_on_close:
            flags |= JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE
        if active_process_limit is not None:
            flags |= JOB_OBJECT_LIMIT_ACTIVE_PROCESS
            info.BasicLimitInformation.ActiveProcessLimit = active_process_limit
        if memory_limit_bytes is not None:
            flags |= JOB_OBJECT_LIMIT_JOB_MEMORY
            info.JobMemoryLimit = memory_limit_bytes
        info.BasicLimitInformation.LimitFlags = flags
        try:
            _check(_k32.SetInformationJobObject(self._h, JobObjectExtendedLimitInformation, ctypes.byref(info),
                                                ctypes.sizeof(info)), "SetInformationJobObject")
        except JobObjectError:
            self.close()
            raise

    @property
    def closed(self) -> bool:
        return self._h is None

    def _handle(self):
        if self._h is None:
            raise JobObjectError(0, "job object is closed")
        return self._h

    def assign_pid(self, pid: int) -> None:
        proc = _k32.OpenProcess(PROCESS_SET_QUOTA | PROCESS_TERMINATE, False, pid)
        _check(proc, f"OpenProcess({pid})")
        try:
            _check(_k32.AssignProcessToJobObject(self._handle(), proc), "AssignProcessToJobObject")
        finally:
            _k32.CloseHandle(proc)

    def active_count(self) -> int:
        info = JOBOBJECT_BASIC_ACCOUNTING_INFORMATION()
        _check(_k32.QueryInformationJobObject(self._handle(), JobObjectBasicAccountingInformation, ctypes.byref(info),
                                              ctypes.sizeof(info), None), "QueryInformationJobObject")
        return int(info.ActiveProcesses)

    def terminate(self, exit_code: int = 1) -> None:
        """Kill every process in the job now (the whole tree, including children born a moment ago)."""
        _check(_k32.TerminateJobObject(self._handle(), exit_code), "TerminateJobObject")

    def close(self) -> None:
        """Release the handle; with kill_on_close this also kills everything still inside."""
        if self._h is not None:
            _k32.CloseHandle(self._h)
            self._h = None

    def __enter__(self) -> "JobObject":
        return self

    def __exit__(self, *exc) -> None:
        self.close()


def spawn_in_job(argv: list[str], job: JobObject, **popen_kwargs: Any) -> subprocess.Popen:
    """Start `argv` inside `job` with no window in which it could spawn an escaping child."""
    _require_windows()
    flags = int(popen_kwargs.pop("creationflags", 0)) | CREATE_SUSPENDED
    proc = subprocess.Popen(argv, creationflags=flags, **popen_kwargs)
    try:
        job.assign_pid(proc.pid)
        _check(_nt.NtResumeProcess(int(proc._handle)) == 0, "NtResumeProcess")
    except BaseException:
        proc.kill()
        raise
    return proc
