"""Windows Job Object with KILL_ON_JOB_CLOSE (MEDIA-RESTART, first line of defence).

An engine process (and every process it spawns) is placed in a job whose only
handle is held by the Bossman backend. When the backend dies for any reason —
crash, "End task", hard kill — the kernel closes that handle and terminates the
whole tree, so no sd.cpp engine keeps holding GPU/unified memory unobserved.

ctypes only, no new dependency. Every function is best-effort and returns
None/False on failure: the durable per-job record in sdcpp.py is the second,
independent line of defence.
"""
from __future__ import annotations

import os

WINDOWS = os.name == "nt"

JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE = 0x2000
JOB_OBJECT_EXTENDED_LIMIT_INFORMATION_CLASS = 9
PROCESS_TERMINATE = 0x0001
PROCESS_SET_QUOTA = 0x0100
CREATE_SUSPENDED = 0x00000004

if WINDOWS:
    import ctypes
    from ctypes import wintypes

    class _BASIC(ctypes.Structure):
        _fields_ = [("PerProcessUserTimeLimit", ctypes.c_int64),
                    ("PerJobUserTimeLimit", ctypes.c_int64),
                    ("LimitFlags", wintypes.DWORD),
                    ("MinimumWorkingSetSize", ctypes.c_size_t),
                    ("MaximumWorkingSetSize", ctypes.c_size_t),
                    ("ActiveProcessLimit", wintypes.DWORD),
                    ("Affinity", ctypes.c_size_t),
                    ("PriorityClass", wintypes.DWORD),
                    ("SchedulingClass", wintypes.DWORD)]

    class _IO(ctypes.Structure):
        _fields_ = [(n, ctypes.c_uint64) for n in ("ReadOperationCount", "WriteOperationCount",
                                                    "OtherOperationCount", "ReadTransferCount",
                                                    "WriteTransferCount", "OtherTransferCount")]

    class _EXTENDED(ctypes.Structure):
        _fields_ = [("BasicLimitInformation", _BASIC), ("IoInfo", _IO),
                    ("ProcessMemoryLimit", ctypes.c_size_t), ("JobMemoryLimit", ctypes.c_size_t),
                    ("PeakProcessMemoryUsed", ctypes.c_size_t), ("PeakJobMemoryUsed", ctypes.c_size_t)]

    _k32 = ctypes.WinDLL("kernel32", use_last_error=True)
    _k32.CreateJobObjectW.argtypes = (ctypes.c_void_p, wintypes.LPCWSTR)
    _k32.CreateJobObjectW.restype = wintypes.HANDLE
    _k32.SetInformationJobObject.argtypes = (wintypes.HANDLE, ctypes.c_int, ctypes.c_void_p, wintypes.DWORD)
    _k32.SetInformationJobObject.restype = wintypes.BOOL
    _k32.OpenProcess.argtypes = (wintypes.DWORD, wintypes.BOOL, wintypes.DWORD)
    _k32.OpenProcess.restype = wintypes.HANDLE
    _k32.AssignProcessToJobObject.argtypes = (wintypes.HANDLE, wintypes.HANDLE)
    _k32.AssignProcessToJobObject.restype = wintypes.BOOL
    _k32.CloseHandle.argtypes = (wintypes.HANDLE,)
    _k32.CloseHandle.restype = wintypes.BOOL


def create_kill_on_close_job():
    """A new anonymous, non-inheritable job handle with KILL_ON_JOB_CLOSE, or None."""
    if not WINDOWS:
        return None
    handle = _k32.CreateJobObjectW(None, None)       # NULL security attributes -> not inheritable
    if not handle:
        return None
    info = _EXTENDED()
    info.BasicLimitInformation.LimitFlags = JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE
    if not _k32.SetInformationJobObject(handle, JOB_OBJECT_EXTENDED_LIMIT_INFORMATION_CLASS,
                                        ctypes.byref(info), ctypes.sizeof(info)):
        _k32.CloseHandle(handle)
        return None
    return handle


def assign(job, pid: int) -> bool:
    """Put process `pid` into `job`. Call while the child is still suspended so that
    every process it later creates is born inside the job."""
    if not WINDOWS or not job:
        return False
    proc = _k32.OpenProcess(PROCESS_SET_QUOTA | PROCESS_TERMINATE, False, int(pid))
    if not proc:
        return False
    try:
        return bool(_k32.AssignProcessToJobObject(job, proc))
    finally:
        _k32.CloseHandle(proc)


def close(job) -> None:
    """Close the job handle; any process still in the job is terminated by the kernel."""
    if WINDOWS and job:
        _k32.CloseHandle(job)
