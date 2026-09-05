"""Stable file identity for verified owned artifacts; no trust from mere existence."""
from pathlib import Path
import os
import stat


def identity(path):
    info = Path(path).stat()
    if not stat.S_ISREG(info.st_mode):
        raise ValueError("artifact must be a regular file")
    change_time = info.st_ctime_ns
    if os.name == "nt":
        # Windows st_ctime is creation time on older Python versions. NTFS
        # ChangeTime also invalidates receipts when an editor restores mtime.
        import ctypes
        from ctypes import wintypes
        import msvcrt
        class BasicInfo(ctypes.Structure):
            _fields_ = [(key, ctypes.c_longlong) for key in
                        ("creation", "access", "write", "change")] + [("attributes", wintypes.DWORD)]
        query = ctypes.WinDLL("kernel32", use_last_error=True).GetFileInformationByHandleEx
        query.argtypes = [wintypes.HANDLE, ctypes.c_int, ctypes.c_void_p, wintypes.DWORD]
        query.restype = wintypes.BOOL
        basic = BasicInfo()
        with Path(path).open("rb") as stream:
            if not query(msvcrt.get_osfhandle(stream.fileno()), 0, ctypes.byref(basic), ctypes.sizeof(basic)):
                raise ctypes.WinError(ctypes.get_last_error())
        change_time = basic.change
    return [info.st_dev, info.st_ino, info.st_size, info.st_mtime_ns, change_time]


def seal(path, sha256):
    from .media import digest_file
    before = identity(path)
    if digest_file(Path(path)) != sha256 or identity(path) != before:
        raise ValueError("artifact changed during verification")
    return {"version": 1, "identity": before, "sha256": sha256}


def matches(path, receipt, sha256):
    if not receipt or receipt.get("version") != 1 or receipt.get("sha256") != sha256:
        return False
    try:
        return identity(path) == receipt.get("identity")
    except (OSError, ValueError):
        return False
