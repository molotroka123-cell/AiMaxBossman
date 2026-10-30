"""Owner-only «Гречка» intake (owner 30.10): `/grechka` arms a 30-minute window; every photo / video / document the
owner sends OR FORWARDS meanwhile is downloaded into memory, encrypted with Windows DPAPI (CurrentUser — only the
owner's account can open it) and written to the encrypted folder as `00_<timestamp>_<n>.enc`, so it sorts FIRST.

Nothing is looked at: bytes never reach a model, the Store inbox, history, logs or the chat (the reply is a count).
A forwarded item is accepted only while the window is armed AND the forwarder is the configured owner in the owner's
private chat; it is stored, never treated as a command. `/grechka stop` closes the window early.
"""
from __future__ import annotations

import ctypes
import os
import re
import time
from ctypes import wintypes
from pathlib import Path

COMMAND = re.compile(r"^/(?:grechka|гречка)(?:@\w+)?(?:\s+(stop|стоп|off))?\s*$", re.I)
WINDOW_S = 30 * 60
MAX_BYTES = 20 * 1024 * 1024          # Bot API getFile download limit
FOLDER = Path(os.environ.get("BOSSMAN_GRECHKA_DIR") or r"C:\Users\asd\Bossman\Гречка")
FORWARD_KEYS = ("forward_origin", "forward_from", "forward_from_chat", "forward_sender_name", "forward_date")


class _Blob(ctypes.Structure):
    _fields_ = [("cbData", wintypes.DWORD), ("pbData", ctypes.POINTER(ctypes.c_char))]


def dpapi_protect(data: bytes) -> bytes:
    """CryptProtectData, CurrentUser scope, no entropy — the same format grechka-send-pult.ps1 decrypts."""
    buf = ctypes.create_string_buffer(data, len(data))
    src = _Blob(len(data), ctypes.cast(buf, ctypes.POINTER(ctypes.c_char)))
    out = _Blob()
    if not ctypes.windll.crypt32.CryptProtectData(ctypes.byref(src), None, None, None, None, 0, ctypes.byref(out)):
        raise OSError("CryptProtectData failed")
    try:
        return ctypes.string_at(out.pbData, out.cbData)
    finally:
        ctypes.windll.kernel32.LocalFree(out.pbData)


def media_file_id(message: dict) -> str | None:
    """Largest photo size, or the video / document / animation file. Metadata only; nothing is downloaded here."""
    photo = message.get("photo")
    if isinstance(photo, list) and photo:
        sizes = [p for p in photo if isinstance(p, dict) and isinstance(p.get("file_id"), str)]
        if sizes:
            return max(sizes, key=lambda p: (p.get("width", 0) or 0) * (p.get("height", 0) or 0))["file_id"]
    for kind in ("video", "document", "animation", "video_note"):
        item = message.get(kind)
        if isinstance(item, dict) and isinstance(item.get("file_id"), str):
            return item["file_id"]
    return None


def without_forward(message: dict) -> dict:
    """The same message minus forward markers, ONLY to ask «who sent it to us» (the forwarder), never as a command."""
    return {k: v for k, v in message.items() if k not in FORWARD_KEYS and k != "sender_chat"}


class GrechkaIntake:
    def __init__(self) -> None:
        self.until: dict[str, float] = {}
        self.saved: dict[str, int] = {}

    def active(self, who: str) -> bool:
        return self.until.get(who, 0) > time.time()

    def arm(self, who: str) -> None:
        self.until[who] = time.time() + WINDOW_S
        self.saved[who] = 0

    def disarm(self, who: str) -> int:
        self.until.pop(who, None)
        return self.saved.pop(who, 0)

    def store(self, who: str, data: bytes) -> int:
        FOLDER.mkdir(parents=True, exist_ok=True)
        self.saved[who] = self.saved.get(who, 0) + 1
        n = self.saved[who]
        name = f"00_{time.strftime('%Y%m%d_%H%M%S')}_{n:03d}.enc"
        tmp = FOLDER / (name + ".tmp")
        tmp.write_bytes(dpapi_protect(data))
        os.replace(tmp, FOLDER / name)
        return n
