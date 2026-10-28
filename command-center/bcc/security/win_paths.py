"""Windows path canonicalization for confinement checks (audit 28.10, CMP-UP-3 / 6.2: «канонизация путей»).

A confinement check that compares path TEXT (`abspath(p).startswith(root)`) is fooled by a junction inside the root that
points outside, by 8.3 short names, by extended-length prefixes, by alternate data streams (`a.txt:hidden`), reserved
device names (`NUL`, `CON`) and trailing dots/spaces that Windows silently drops. Here the answer comes from the file
system: the path is opened and `GetFinalPathNameByHandleW` returns the real, final, long-name location after every
junction and symlink. A path that does not exist yet is canonicalized through its longest existing parent.

`is_inside(root, path)` is the check to use; `canonical_path` returns the final path (or raises PathRefused).
It is a primitive: it is not wired into the tools here. Windows only (NotImplementedError elsewhere).
"""
from __future__ import annotations

import ctypes
import os
import re
import sys
from ctypes import wintypes

IS_WINDOWS = sys.platform == "win32"
_RESERVED = {"con", "prn", "aux", "nul", *(f"com{i}" for i in range(1, 10)), *(f"lpt{i}" for i in range(1, 10))}
_FILE_FLAG_BACKUP_SEMANTICS = 0x02000000
_FILE_SHARE_ALL = 0x7
_OPEN_EXISTING = 3
_VOLUME_NAME_DOS = 0x0
_INVALID = ctypes.c_void_p(-1).value
_BS = chr(92)
_EXT = _BS * 2 + "?" + _BS            # the extended-length prefix: two backslashes, a question mark, a backslash
_DEV = _BS * 2 + "." + _BS            # the device namespace prefix


class PathRefused(ValueError):
    """The path cannot be a plain file-system location (device name, stream, trailing dot, ...)."""


if IS_WINDOWS:
    _k32 = ctypes.WinDLL("kernel32", use_last_error=True)
    _k32.CreateFileW.restype = wintypes.HANDLE
    _k32.CreateFileW.argtypes = [wintypes.LPCWSTR, wintypes.DWORD, wintypes.DWORD, wintypes.LPVOID, wintypes.DWORD,
                                 wintypes.DWORD, wintypes.HANDLE]
    _k32.GetFinalPathNameByHandleW.restype = wintypes.DWORD
    _k32.GetFinalPathNameByHandleW.argtypes = [wintypes.HANDLE, wintypes.LPWSTR, wintypes.DWORD, wintypes.DWORD]
    _k32.CloseHandle.argtypes = [wintypes.HANDLE]


def _require_windows() -> None:
    if not IS_WINDOWS:
        raise NotImplementedError("GetFinalPathNameByHandleW exists only on Windows")


def _strip_prefix(p: str) -> str:
    if p.startswith(_EXT + "UNC" + _BS):
        return _BS * 2 + p[len(_EXT) + 4:]
    if p.startswith(_EXT):
        return p[len(_EXT):]
    return p


def _screen(path: str) -> str:
    """Refuse what is not a plain file-system path; return the path without an extended-length prefix."""
    if "\x00" in path:
        raise PathRefused("NUL character in the path")
    low = path.lower()
    if path.startswith((_DEV, "//./")):
        raise PathRefused("device namespace path")
    if low.startswith(_EXT + "globalroot") or low.startswith(_EXT + "harddiskvolume"):
        raise PathRefused("raw object-manager path")
    p = _strip_prefix(path)
    _drive, rest = os.path.splitdrive(p)
    for part in re.split(r"[\\/]+", rest):
        if not part or part in (".", ".."):
            continue
        if ":" in part:
            raise PathRefused(f"alternate data stream or colon in component {part!r}")
        if part != part.rstrip(" ."):
            raise PathRefused(f"component {part!r} ends with a dot or space (Windows drops it silently)")
        if part.split(".")[0].lower() in _RESERVED:
            raise PathRefused(f"reserved device name {part!r}")
    return p


def _final_of_existing(path: str) -> str | None:
    h = _k32.CreateFileW(path, 0, _FILE_SHARE_ALL, None, _OPEN_EXISTING, _FILE_FLAG_BACKUP_SEMANTICS, None)
    if h is None or h == _INVALID:
        return None
    try:
        size = _k32.GetFinalPathNameByHandleW(h, None, 0, _VOLUME_NAME_DOS)
        if not size:
            return None
        buf = ctypes.create_unicode_buffer(size + 1)
        if not _k32.GetFinalPathNameByHandleW(h, buf, size + 1, _VOLUME_NAME_DOS):
            return None
        return _strip_prefix(buf.value)
    finally:
        _k32.CloseHandle(h)


def canonical_path(path: "str | os.PathLike") -> str:
    """Final real path with long names, after junctions/symlinks. A not-yet-existing tail is appended to its canonical parent."""
    _require_windows()
    p = os.path.abspath(_screen(os.fspath(path)))    # . and .. are resolved textually; the file system resolves the rest
    tail: list[str] = []
    cur = p
    while True:
        final = _final_of_existing(cur)
        if final is not None:
            break
        parent, name = os.path.split(cur)
        if not name or parent == cur:
            raise PathRefused(f"no existing parent for {path!r}")
        tail.append(name)
        cur = parent
    for name in reversed(tail):
        final = os.path.join(final, name)
    return final


def is_inside(root: "str | os.PathLike", path: "str | os.PathLike") -> bool:
    """True when `path` really lies in `root` (or is it), by final real locations. A refused path is NOT inside."""
    try:
        r = canonical_path(root).rstrip(_BS + "/").lower()
        p = canonical_path(path).rstrip(_BS + "/").lower()
    except PathRefused:
        return False
    return p == r or p.startswith(r + _BS)
