"""Run a console program under a Windows pseudo console (ConPTY), invisibly.

The owner's CMD window is a real console: stdin/stdout are TTYs, so ``bossman chat``
takes its prompt_toolkit path (history, completion, key handling), not the plain
readline path used when stdin is a pipe. ConPTY gives the child exactly that console
without a visible window and without taking keyboard focus from anybody.

    with ConPty([sys.executable, "-m", "bossman.cli", "chat"], env=env, cwd=dir) as con:
        con.expect("> ", 60); con.send("привет\r"); con.expect("soak-ok", 60)
"""
from __future__ import annotations

import ctypes
import re
import subprocess
import threading
import time
from ctypes import wintypes

k32 = ctypes.WinDLL("kernel32", use_last_error=True)

HPCON = wintypes.HANDLE
PROC_THREAD_ATTRIBUTE_PSEUDOCONSOLE = 0x00020016
EXTENDED_STARTUPINFO_PRESENT = 0x00080000
CREATE_UNICODE_ENVIRONMENT = 0x00000400
BELOW_NORMAL_PRIORITY_CLASS = 0x00004000
STARTF_USESTDHANDLES = 0x00000100
ANSI = re.compile(r"\x1b\[[0-?]*[ -/]*[@-~]|\x1b\][^\x07\x1b]*(?:\x07|\x1b\\)|\x1b[=>()][0-9A-Za-z]?|\x1b[78c]")


class COORD(ctypes.Structure):
    _fields_ = [("X", wintypes.SHORT), ("Y", wintypes.SHORT)]


class STARTUPINFOW(ctypes.Structure):
    _fields_ = [("cb", wintypes.DWORD), ("lpReserved", wintypes.LPWSTR), ("lpDesktop", wintypes.LPWSTR),
                ("lpTitle", wintypes.LPWSTR), ("dwX", wintypes.DWORD), ("dwY", wintypes.DWORD),
                ("dwXSize", wintypes.DWORD), ("dwYSize", wintypes.DWORD), ("dwXCountChars", wintypes.DWORD),
                ("dwYCountChars", wintypes.DWORD), ("dwFillAttribute", wintypes.DWORD),
                ("dwFlags", wintypes.DWORD), ("wShowWindow", wintypes.WORD), ("cbReserved2", wintypes.WORD),
                ("lpReserved2", ctypes.c_void_p), ("hStdInput", wintypes.HANDLE),
                ("hStdOutput", wintypes.HANDLE), ("hStdError", wintypes.HANDLE)]


class STARTUPINFOEXW(ctypes.Structure):
    _fields_ = [("StartupInfo", STARTUPINFOW), ("lpAttributeList", ctypes.c_void_p)]


class PROCESS_INFORMATION(ctypes.Structure):
    _fields_ = [("hProcess", wintypes.HANDLE), ("hThread", wintypes.HANDLE),
                ("dwProcessId", wintypes.DWORD), ("dwThreadId", wintypes.DWORD)]


k32.CreatePseudoConsole.argtypes = [COORD, wintypes.HANDLE, wintypes.HANDLE, wintypes.DWORD, ctypes.POINTER(HPCON)]
k32.CreatePseudoConsole.restype = ctypes.c_long
k32.ClosePseudoConsole.argtypes = [HPCON]
k32.CreatePipe.argtypes = [ctypes.POINTER(wintypes.HANDLE), ctypes.POINTER(wintypes.HANDLE), ctypes.c_void_p, wintypes.DWORD]
k32.InitializeProcThreadAttributeList.argtypes = [ctypes.c_void_p, wintypes.DWORD, wintypes.DWORD, ctypes.POINTER(ctypes.c_size_t)]
k32.UpdateProcThreadAttribute.argtypes = [ctypes.c_void_p, wintypes.DWORD, ctypes.c_size_t, ctypes.c_void_p,
                                          ctypes.c_size_t, ctypes.c_void_p, ctypes.c_void_p]
k32.CreateProcessW.argtypes = [wintypes.LPCWSTR, wintypes.LPWSTR, ctypes.c_void_p, ctypes.c_void_p, wintypes.BOOL,
                               wintypes.DWORD, ctypes.c_void_p, wintypes.LPCWSTR, ctypes.POINTER(STARTUPINFOEXW),
                               ctypes.POINTER(PROCESS_INFORMATION)]
k32.ReadFile.argtypes = [wintypes.HANDLE, ctypes.c_void_p, wintypes.DWORD, ctypes.POINTER(wintypes.DWORD), ctypes.c_void_p]
k32.WriteFile.argtypes = [wintypes.HANDLE, ctypes.c_void_p, wintypes.DWORD, ctypes.POINTER(wintypes.DWORD), ctypes.c_void_p]
k32.WaitForSingleObject.argtypes = [wintypes.HANDLE, wintypes.DWORD]
k32.GetExitCodeProcess.argtypes = [wintypes.HANDLE, ctypes.POINTER(wintypes.DWORD)]
k32.TerminateProcess.argtypes = [wintypes.HANDLE, wintypes.UINT]
k32.CloseHandle.argtypes = [wintypes.HANDLE]


def _check(ok, what):
    if not ok:
        raise ctypes.WinError(ctypes.get_last_error(), what)


class ConPty:
    def __init__(self, argv: list[str], *, env: dict | None = None, cwd: str | None = None,
                 cols: int = 120, rows: int = 40):
        in_r, in_w, out_r, out_w = (wintypes.HANDLE() for _ in range(4))
        _check(k32.CreatePipe(ctypes.byref(in_r), ctypes.byref(in_w), None, 0), "CreatePipe")
        _check(k32.CreatePipe(ctypes.byref(out_r), ctypes.byref(out_w), None, 0), "CreatePipe")
        self.hpc = HPCON()
        hr = k32.CreatePseudoConsole(COORD(cols, rows), in_r, out_w, 0, ctypes.byref(self.hpc))
        if hr != 0:
            raise OSError(f"CreatePseudoConsole hr=0x{hr & 0xffffffff:08x}")
        k32.CloseHandle(in_r)
        k32.CloseHandle(out_w)
        self._in, self._out = in_w, out_r

        size = ctypes.c_size_t()
        k32.InitializeProcThreadAttributeList(None, 1, 0, ctypes.byref(size))
        self._attrs = ctypes.create_string_buffer(size.value)
        _check(k32.InitializeProcThreadAttributeList(self._attrs, 1, 0, ctypes.byref(size)), "InitAttrList")
        _check(k32.UpdateProcThreadAttribute(self._attrs, 0, PROC_THREAD_ATTRIBUTE_PSEUDOCONSOLE,
                                             self.hpc, ctypes.sizeof(HPCON), None, None), "UpdateAttr")
        si = STARTUPINFOEXW()
        si.StartupInfo.cb = ctypes.sizeof(STARTUPINFOEXW)
        # null std handles: never inherit the harness's redirected pipes
        si.StartupInfo.dwFlags = STARTF_USESTDHANDLES
        si.lpAttributeList = ctypes.cast(self._attrs, ctypes.c_void_p)
        pi = PROCESS_INFORMATION()
        block = None
        if env is not None:
            block = ctypes.create_unicode_buffer("\0".join(f"{k}={v}" for k, v in env.items()) + "\0\0")
        cmd = ctypes.create_unicode_buffer(subprocess.list2cmdline(argv))
        _check(k32.CreateProcessW(None, cmd, None, None, False,
                                  EXTENDED_STARTUPINFO_PRESENT | CREATE_UNICODE_ENVIRONMENT | BELOW_NORMAL_PRIORITY_CLASS,
                                  block, cwd, ctypes.byref(si), ctypes.byref(pi)), "CreateProcessW")
        self.pid = pi.dwProcessId
        self._hproc = pi.hProcess
        k32.CloseHandle(pi.hThread)
        self._buf: list[str] = []
        self._lock = threading.Lock()
        self._pos = 0
        self._reader = threading.Thread(target=self._pump, daemon=True)
        self._reader.start()

    def _pump(self):
        buf = ctypes.create_string_buffer(65536)
        n = wintypes.DWORD()
        dec = __import__("codecs").getincrementaldecoder("utf-8")(errors="replace")
        while True:
            ok = k32.ReadFile(self._out, buf, len(buf), ctypes.byref(n), None)
            if not ok or n.value == 0:
                break
            with self._lock:
                self._buf.append(dec.decode(buf.raw[: n.value]))

    # ------------------------------------------------------------------ io
    @property
    def raw(self) -> str:
        with self._lock:
            return "".join(self._buf)

    @property
    def text(self) -> str:
        # ConPTY repaints with cursor positioning instead of newlines
        raw = re.sub(r"\x1b\[\d*;?\d*H", "\n", self.raw)
        return ANSI.sub("", raw).replace("\r", "")

    def send(self, s: str) -> None:
        data = s.encode("utf-8")
        n = wintypes.DWORD()
        _check(k32.WriteFile(self._in, data, len(data), ctypes.byref(n), None), "WriteFile")

    def expect(self, pattern: str, timeout: float = 30.0, *, since_mark: bool = True) -> re.Match | None:
        rx = re.compile(pattern)
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            txt = self.text
            m = rx.search(txt, self._pos if since_mark else 0)
            if m:
                self._pos = m.end()
                return m
            if not self.alive():
                txt = self.text
                return rx.search(txt, self._pos if since_mark else 0)
            time.sleep(0.2)
        return None

    def mark(self) -> None:
        self._pos = len(self.text)

    def alive(self) -> bool:
        return k32.WaitForSingleObject(self._hproc, 0) == 0x102  # WAIT_TIMEOUT

    def wait(self, timeout: float = 30.0) -> int | None:
        k32.WaitForSingleObject(self._hproc, int(timeout * 1000))
        code = wintypes.DWORD()
        k32.GetExitCodeProcess(self._hproc, ctypes.byref(code))
        return None if code.value == 259 else int(code.value)  # STILL_ACTIVE

    def close(self) -> None:
        if self.alive():
            k32.TerminateProcess(self._hproc, 1)
        k32.ClosePseudoConsole(self.hpc)
        k32.CloseHandle(self._in)
        k32.CloseHandle(self._hproc)

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        self.close()
