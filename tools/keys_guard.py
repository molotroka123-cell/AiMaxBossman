'''Module for DPAPI protection and atomic writes on Windows.

Provides functions to protect and unprotect data using Windows DPAPI,
and to write bytes atomically to a file.
'''
from __future__ import annotations

import ctypes
import json
import os
import tempfile
from ctypes import wintypes
from pathlib import Path


class KeysGuardError(Exception):
    """Base exception for keys_guard errors."""


class _Blob(ctypes.Structure):
    _fields_ = [
        ('cbData', wintypes.DWORD),
        ('pbData', ctypes.POINTER(ctypes.c_char))
    ]


def dpapi_protect(data: bytes) -> bytes:
    """Protect data using DPAPI CryptProtectData."""
    if not data:
        return b''

    # Prepare input blob
    buf = ctypes.create_string_buffer(data, len(data))
    in_blob = _Blob(len(data), ctypes.cast(buf, ctypes.POINTER(ctypes.c_char)))

    # Output blob
    out_blob = _Blob()
    # CryptProtectData parameters: pDataIn, szDataDescr, pOptionalEntropy,
    # pvReserved, pPromptStruct, dwFlags, pDataOut
    ret = ctypes.windll.crypt32.CryptProtectData(
        ctypes.byref(in_blob),
        None,  # szDataDescr
        None,  # pOptionalEntropy
        None,  # pvReserved
        None,  # pPromptStruct
        0,     # dwFlags
        ctypes.byref(out_blob)
    )
    if ret == 0:
        raise KeysGuardError("DPAPI protect failed")

    try:
        # Copy protected data
        protected = ctypes.string_at(out_blob.pbData, out_blob.cbData)
        return protected
    finally:
        # Free memory allocated by CryptProtectData
        ctypes.windll.kernel32.LocalFree(out_blob.pbData)


def dpapi_unprotect(blob: bytes) -> bytes:
    """Unprotect data using DPAPI CryptUnprotectData."""
    if not blob:
        return b''

    # Prepare input blob
    buf = ctypes.create_string_buffer(blob, len(blob))
    in_blob = _Blob(len(blob), ctypes.cast(buf, ctypes.POINTER(ctypes.c_char)))

    # Output blob
    out_blob = _Blob()
    ret = ctypes.windll.crypt32.CryptUnprotectData(
        ctypes.byref(in_blob),
        None,  # ppszDataDescr
        None,  # pOptionalEntropy
        None,  # pvReserved
        None,  # pPromptStruct
        0,     # dwFlags
        ctypes.byref(out_blob)
    )
    if ret == 0:
        raise KeysGuardError("DPAPI unprotect failed")

    try:
        # Copy unprotected data
        unprotected = ctypes.string_at(out_blob.pbData, out_blob.cbData)
        return unprotected
    finally:
        ctypes.windll.kernel32.LocalFree(out_blob.pbData)


def atomic_write_bytes(path: Path, data: bytes) -> None:
    """Write bytes to path atomically using a temporary file."""
    # Ensure parent directory exists
    path.parent.mkdir(parents=True, exist_ok=True)

    # Create a temporary file in the same directory
    fd, tmp_path = tempfile.mkstemp(dir=path.parent, prefix=path.name + ".tmp")
    try:
        with os.fdopen(fd, 'wb') as tmp_file:
            tmp_file.write(data)
            tmp_file.flush()
            os.fsync(tmp_file.fileno())
        # Replace the target file with the temporary file
        os.replace(tmp_path, path)
    except Exception:
        # Clean up temporary file on error
        try:
            os.unlink(tmp_path)
        except OSError:
            pass
        raise