from __future__ import annotations

import ctypes
import ctypes.wintypes as wt
import json
import os
import re
import signal
import sys
import threading
import time
from datetime import datetime, timezone
from pathlib import Path


BASE = Path(__file__).resolve().parent
RUNS = BASE / "runs"
STOP_FILE = BASE / "stop.request"
STARTED = time.time()
STAMP = datetime.now().strftime("%Y%m%d-%H%M%S")
OUT = RUNS / STAMP
EVENTS = OUT / "clicks.jsonl"
STREAM = OUT / "runtime-stream.jsonl"
STATUS = OUT / "status.txt"

WATCH_ROOTS = [
    Path(os.environ.get("LOCALAPPDATA", "")) / "Bossman",
    Path.home() / ".bossman",
    Path.home() / ".claude" / "projects",
    Path.home() / ".codex" / "sessions",
    Path.home() / ".ollama" / "logs",
]
WATCH_SUFFIXES = {".log", ".jsonl", ".ndjson"}
MAX_FILE_SIZE = 100 * 1024 * 1024
MAX_LINE = 64 * 1024

SECRET_PATTERNS = [
    (re.compile(r"(?i)(authorization\s*[:=]\s*bearer\s+)[^\s\"']+"), r"\1[REDACTED]"),
    (re.compile(r"(?i)((?:api[_-]?key|token|secret|password)\s*[\"']?\s*[:=]\s*[\"']?)[^\s\"',}]+"), r"\1[REDACTED]"),
    (re.compile(r"\bsk-[A-Za-z0-9_-]{12,}\b"), "[REDACTED_KEY]"),
]

stop_event = threading.Event()
write_lock = threading.Lock()
positions: dict[str, int] = {}


def now() -> str:
    return datetime.now(timezone.utc).astimezone().isoformat(timespec="milliseconds")


def redact(value: str) -> str:
    for pattern, replacement in SECRET_PATTERNS:
        value = pattern.sub(replacement, value)
    return value


def append_json(path: Path, record: dict) -> None:
    record = {"ts": now(), **record}
    with write_lock:
        with path.open("a", encoding="utf-8") as handle:
            handle.write(json.dumps(record, ensure_ascii=False) + "\n")


user32 = ctypes.windll.user32
kernel32 = ctypes.windll.kernel32
PROCESS_QUERY_LIMITED_INFORMATION = 0x1000


def foreground() -> dict:
    hwnd = user32.GetForegroundWindow()
    length = user32.GetWindowTextLengthW(hwnd)
    title_buffer = ctypes.create_unicode_buffer(length + 1)
    user32.GetWindowTextW(hwnd, title_buffer, length + 1)
    pid = wt.DWORD()
    user32.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
    process = ""
    handle = kernel32.OpenProcess(PROCESS_QUERY_LIMITED_INFORMATION, False, pid.value)
    if handle:
        try:
            size = wt.DWORD(32768)
            buffer = ctypes.create_unicode_buffer(size.value)
            if kernel32.QueryFullProcessImageNameW(handle, 0, buffer, ctypes.byref(size)):
                process = Path(buffer.value).name
        finally:
            kernel32.CloseHandle(handle)
    return {"hwnd": int(hwnd), "pid": int(pid.value), "process": process, "window": redact(title_buffer.value)}


class Point(ctypes.Structure):
    _fields_ = [("x", wt.LONG), ("y", wt.LONG)]


class MouseStruct(ctypes.Structure):
    _fields_ = [("pt", Point), ("mouseData", wt.DWORD), ("flags", wt.DWORD), ("time", wt.DWORD), ("extra", ctypes.POINTER(ctypes.c_ulong))]


HOOKPROC = ctypes.WINFUNCTYPE(ctypes.c_longlong, ctypes.c_int, wt.WPARAM, wt.LPARAM)
WM_NAMES = {0x0201: "left", 0x0204: "right", 0x0207: "middle"}


@HOOKPROC
def mouse_callback(code, message, data):
    if code >= 0 and int(message) in WM_NAMES:
        info = ctypes.cast(data, ctypes.POINTER(MouseStruct)).contents
        append_json(EVENTS, {
            "kind": "click",
            "button": WM_NAMES[int(message)],
            "x": int(info.pt.x),
            "y": int(info.pt.y),
            **foreground(),
        })
    return user32.CallNextHookEx(None, code, message, data)


def mouse_loop() -> None:
    hook = user32.SetWindowsHookExW(14, mouse_callback, kernel32.GetModuleHandleW(None), 0)
    if not hook:
        append_json(EVENTS, {"kind": "recorder_error", "source": "mouse_hook", "error": ctypes.WinError().strerror})
        return
    msg = wt.MSG()
    try:
        while not stop_event.is_set():
            while user32.PeekMessageW(ctypes.byref(msg), None, 0, 0, 1):
                user32.TranslateMessage(ctypes.byref(msg))
                user32.DispatchMessageW(ctypes.byref(msg))
            time.sleep(0.02)
    finally:
        user32.UnhookWindowsHookEx(hook)


def candidates():
    for root in WATCH_ROOTS:
        if not root.exists():
            continue
        try:
            for path in root.rglob("*"):
                try:
                    if path.is_file() and path.suffix.lower() in WATCH_SUFFIXES and path.stat().st_size <= MAX_FILE_SIZE:
                        yield path
                except OSError:
                    continue
        except OSError:
            continue


def tail_once(initial: bool = False) -> None:
    for path in candidates():
        key = str(path)
        try:
            size = path.stat().st_size
            if key not in positions:
                positions[key] = size if initial else 0
            if size < positions[key]:
                positions[key] = 0
            if size == positions[key]:
                continue
            with path.open("rb") as handle:
                handle.seek(positions[key])
                chunk = handle.read(min(size - positions[key], 2 * 1024 * 1024))
                positions[key] = handle.tell()
            for raw in chunk.splitlines():
                line = redact(raw[:MAX_LINE].decode("utf-8", errors="replace"))
                append_json(STREAM, {"kind": "runtime_log", "source": key, "line": line})
        except (OSError, PermissionError) as exc:
            append_json(STREAM, {"kind": "tail_error", "source": key, "error": str(exc)})


def tail_loop() -> None:
    tail_once(initial=True)
    while not stop_event.wait(1.0):
        tail_once()


def count_lines(path: Path) -> int:
    if not path.exists():
        return 0
    with path.open("rb") as handle:
        return sum(1 for _ in handle)


def finish() -> None:
    duration = time.time() - STARTED
    summary = (
        "# Bossman debug recording\n\n"
        f"- Started: {datetime.fromtimestamp(STARTED).astimezone().isoformat(timespec='seconds')}\n"
        f"- Finished: {datetime.now().astimezone().isoformat(timespec='seconds')}\n"
        f"- Duration: {duration:.1f} seconds\n"
        f"- Click events: {count_lines(EVENTS)}\n"
        f"- Runtime log records: {count_lines(STREAM)}\n\n"
        "Files:\n\n"
        "- `clicks.jsonl`: mouse clicks with foreground window/process.\n"
        "- `runtime-stream.jsonl`: new Bossman, Jev, Telegram, Ollama, Claude and Codex log lines.\n"
        "- `status.txt`: recorder lifecycle.\n\n"
        "No keyboard input was recorded. Known token/key/password patterns were redacted.\n"
    )
    (OUT / "SUMMARY.md").write_text(summary, encoding="utf-8")
    with STATUS.open("a", encoding="utf-8") as handle:
        handle.write(f"STOPPED {now()}\n")
    (BASE / "LATEST.txt").write_text(str(OUT), encoding="utf-8")


def request_stop(*_args) -> None:
    stop_event.set()


def main() -> int:
    OUT.mkdir(parents=True, exist_ok=True)
    STOP_FILE.unlink(missing_ok=True)
    STATUS.write_text(f"RUNNING {now()} pid={os.getpid()}\n", encoding="utf-8")
    (BASE / "recorder.pid").write_text(str(os.getpid()), encoding="ascii")
    signal.signal(signal.SIGINT, request_stop)
    signal.signal(signal.SIGTERM, request_stop)
    append_json(EVENTS, {"kind": "recorder_start", "pid": os.getpid(), "watch_roots": [str(p) for p in WATCH_ROOTS]})
    threads = [
        threading.Thread(target=mouse_loop, name="mouse", daemon=True),
        threading.Thread(target=tail_loop, name="tail", daemon=True),
    ]
    for thread in threads:
        thread.start()
    print(f"Bossman recorder RUNNING\nOutput: {OUT}\nStop with STOP-RECORDER.cmd or Ctrl+C")
    try:
        while not stop_event.wait(0.25):
            if STOP_FILE.exists():
                stop_event.set()
    finally:
        for thread in threads:
            thread.join(timeout=3)
        tail_once()
        append_json(EVENTS, {"kind": "recorder_stop"})
        finish()
        STOP_FILE.unlink(missing_ok=True)
        (BASE / "recorder.pid").unlink(missing_ok=True)
        print(f"Saved: {OUT}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
