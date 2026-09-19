"""Bound concurrent read verification; never cache a completed content check.

Only simultaneous requests for the same file identity share a full hash. A later
request always rehashes, including same-size rewrites with restored timestamps.

WHAT THIS BOUNDARY DOES AND DOES NOT GUARANTEE
----------------------------------------------
Verification is DESCRIPTOR-bound, not pathname-bound. Every caller opens its own
read-only descriptor BEFORE admission and streams the HTTP body from it -- the
pathname is never re-opened at send time. An open descriptor pins the file object;
unlink+recreate, rename or a symlink swap of the pathname after the check cannot
substitute the bytes that are served. On Windows the descriptor is opened with
FILE_SHARE_DELETE so rename/unlink has the same descriptor-bound semantics rather
than failing with a sharing violation.

The shared hash runs on the worker's OWN descriptor rather than the caller's, so
that a cancelled caller closing its fd cannot break verification for the others.
Precisely: the worker proves "the file object with this (dev, ino, size, mtime,
ctime) hashes to the reference digest", and each caller's fstat -- taken before
and after -- proves its held descriptor is that same object, unchanged.

It does NOT stop a privileged writer from rewriting that same file object in place
while the body streams; stat identity is the only signal for that, and it is a
change detector, not protection. Windows st_ctime is creation time rather than a
change clock -- REQUIRES_CONTENT_RECHECK below turns the caller's post-verification
check into a real content hash there. Windows opens also refuse a reparse point at
the descriptor boundary instead of following one through a pathname race.
"""
from __future__ import annotations

import asyncio
from concurrent.futures import ThreadPoolExecutor
import os
from pathlib import Path
import re
import stat as stat_module
import sys
import threading

from . import media as media_module

# Windows stat ctime is CREATION time, not a content-change clock: a same-size
# in-place rewrite with restored mtime leaves the identity tuple untouched, so
# the guard is inert there. export_receipt already compensates the same way.
REQUIRES_CONTENT_RECHECK = os.name == "nt"

# O_NOFOLLOW / O_CLOEXEC / O_NOINHERIT / O_BINARY are platform-conditional.
OPEN_FLAGS = (os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0) | getattr(os, "O_CLOEXEC", 0)
              | getattr(os, "O_NOINHERIT", 0) | getattr(os, "O_BINARY", 0))


class ReadVerificationBusy(RuntimeError):
    """Admission is full; no unverified media may be served as a fallback."""


class VerifiedRead(type(Path())):
    """A verified path that OWNS the descriptor whose bytes were checked.

    It stays a Path so every existing caller keeps working, but the descriptor --
    not the pathname -- is what a response must be served from. Whoever receives
    one owns it: call ``detach`` to hand the descriptor to a response, or
    ``close`` to release it.
    """

    __slots__ = ("descriptor", "descriptor_stat", "sha256")
    if sys.version_info < (3, 12):  # pragma: no cover - 3.11 pathlib internals
        _flavour = type(Path())._flavour

    @classmethod
    def bind(cls, path, fd, info, digest=None):
        value = cls(path)
        value.descriptor = fd
        value.descriptor_stat = info
        value.sha256 = digest
        return value

    def detach(self):
        """Transfer descriptor ownership; this handle stops being closeable."""
        fd = getattr(self, "descriptor", None)
        if fd is None:
            raise ValueError("verified descriptor was already released")
        self.descriptor = None
        return fd, self.descriptor_stat

    def close(self):
        fd = getattr(self, "descriptor", None)
        self.descriptor = None
        if fd is not None:
            os.close(fd)


def _windows_shared_delete_fd(path):  # pragma: no cover - exercised on Windows CI
    """Open one read descriptor without blocking rename/delete on Windows.

    CRT ``os.open`` omits FILE_SHARE_DELETE, which made a verified download hold
    the pathname hostage (WinError 32) and violated the descriptor-bound contract.
    CreateFileW gives the exact share contract. FILE_FLAG_OPEN_REPARSE_POINT plus
    an attribute check is the Windows analogue of O_NOFOLLOW: we refuse a reparse
    object rather than silently following it.
    """
    import ctypes
    import msvcrt
    from ctypes import wintypes

    GENERIC_READ = 0x80000000
    FILE_SHARE_READ = 0x00000001
    FILE_SHARE_WRITE = 0x00000002
    FILE_SHARE_DELETE = 0x00000004
    OPEN_EXISTING = 3
    FILE_ATTRIBUTE_NORMAL = 0x00000080
    FILE_ATTRIBUTE_REPARSE_POINT = 0x00000400
    FILE_FLAG_OPEN_REPARSE_POINT = 0x00200000
    FILE_FLAG_SEQUENTIAL_SCAN = 0x08000000
    FILE_ATTRIBUTE_TAG_INFO_CLASS = 9
    INVALID_HANDLE_VALUE = ctypes.c_void_p(-1).value

    class FILE_ATTRIBUTE_TAG_INFO(ctypes.Structure):
        _fields_ = [("FileAttributes", wintypes.DWORD), ("ReparseTag", wintypes.DWORD)]

    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    create = kernel32.CreateFileW
    create.argtypes = [wintypes.LPCWSTR, wintypes.DWORD, wintypes.DWORD,
                       wintypes.LPVOID, wintypes.DWORD, wintypes.DWORD, wintypes.HANDLE]
    create.restype = wintypes.HANDLE
    close = kernel32.CloseHandle
    close.argtypes = [wintypes.HANDLE]
    close.restype = wintypes.BOOL
    get_info = kernel32.GetFileInformationByHandleEx
    get_info.argtypes = [wintypes.HANDLE, ctypes.c_int, wintypes.LPVOID, wintypes.DWORD]
    get_info.restype = wintypes.BOOL

    handle = create(
        os.fspath(path), GENERIC_READ,
        FILE_SHARE_READ | FILE_SHARE_WRITE | FILE_SHARE_DELETE,
        None, OPEN_EXISTING,
        FILE_ATTRIBUTE_NORMAL | FILE_FLAG_OPEN_REPARSE_POINT | FILE_FLAG_SEQUENTIAL_SCAN,
        None,
    )
    if handle == INVALID_HANDLE_VALUE:
        raise ctypes.WinError(ctypes.get_last_error())
    try:
        tag = FILE_ATTRIBUTE_TAG_INFO()
        if not get_info(handle, FILE_ATTRIBUTE_TAG_INFO_CLASS, ctypes.byref(tag), ctypes.sizeof(tag)):
            raise ctypes.WinError(ctypes.get_last_error())
        if tag.FileAttributes & FILE_ATTRIBUTE_REPARSE_POINT:
            raise ValueError("verified read refuses a Windows reparse point")
        # Ownership transfers to the CRT descriptor. os.close(fd) closes HANDLE.
        fd = msvcrt.open_osfhandle(int(handle), os.O_RDONLY | getattr(os, "O_BINARY", 0))
        handle = None
        return fd
    finally:
        if handle is not None:
            close(handle)


def open_descriptor(path):
    """One open; fstat, hash and body all come from THIS descriptor afterwards."""
    fd = _windows_shared_delete_fd(path) if os.name == "nt" else os.open(path, OPEN_FLAGS)
    try:
        info = os.fstat(fd)
        if not stat_module.S_ISREG(info.st_mode):
            raise ValueError("verified read requires a regular file")
        return fd, info
    except BaseException:
        os.close(fd)
        raise


def _tuple(value):
    return (value.st_dev, value.st_ino, value.st_size, value.st_mtime_ns, value.st_ctime_ns)


def identity(path):
    # On Windows Path.stat() and fstat() for a HANDLE adopted by the CRT can
    # expose different metadata encodings for the same file. Comparing those two
    # representations made a freshly opened, unchanged file fail closed. Re-open
    # the pathname through the same no-reparse/share-delete descriptor boundary so
    # both sides use the same identity representation; replacement still changes
    # the file object, and the mandatory content recheck below covers in-place
    # same-size rewrites whose Windows timestamps are not a reliable change clock.
    if os.name != "nt":
        return _tuple(path.stat())
    fd, info = open_descriptor(path)
    try:
        return _tuple(info)
    finally:
        os.close(fd)


def descriptor_identity(fd):
    """Identity of the file object actually held open, not of whatever the name means."""
    return _tuple(os.fstat(fd))


async def open_verified(path, digest, message, *, size=None):
    """Open, fstat and hash one file through a single descriptor, then keep it.

    Used for files that are NOT content-addressed by their own name (published
    exports, prepared derivatives): the expected digest comes from a separate
    record, and the descriptor that proved it is the one handed on to be served.
    """
    if not isinstance(digest, str) or not re.fullmatch(r"[0-9a-f]{64}", digest):
        raise RuntimeError(message)
    fd, info = open_descriptor(path)
    try:
        before = _tuple(info)
        if size is not None and info.st_size != size:
            raise RuntimeError(message)
        if await media_module.blocking(media_module.digest_descriptor, fd) != digest:
            raise RuntimeError(message)
        if descriptor_identity(fd) != before:
            raise RuntimeError(message)
        handle = VerifiedRead.bind(path, fd, os.fstat(fd), digest)
        fd = None
        return handle
    finally:
        if fd is not None:
            os.close(fd)


class ReadVerifier:
    """A process-wide bounded pool, isolated from the shared asyncio executor."""

    def __init__(self, workers=2, capacity=16):
        if not isinstance(capacity, int) or isinstance(capacity, bool) or capacity < 1:
            raise ValueError("read verification capacity must be a positive integer")
        self._pool = ThreadPoolExecutor(max_workers=workers, thread_name_prefix="video-read-hash")
        self._capacity = capacity
        self._lock = threading.Lock()
        self._pending = {}

    async def resolve(self, library, media):
        # Resolve and enforce owned containment for every caller before sharing.
        reference = {"sha256": media.get("sha256"), "relative_path": media.get("relative_path")}
        path = library.owned_path(reference)
        # Own descriptor per caller: the body is streamed from this fd, so the
        # pathname may be re-pointed afterwards without changing what is served.
        fd, info = open_descriptor(path)
        try:
            before = _tuple(info)
            if identity(path) != before:
                raise ValueError("media changed before read verification")
            key = (str(path), reference["sha256"], before)

            def verify():
                if identity(path) != before:
                    raise ValueError("media changed before read verification")
                resolved = library.resolve(reference)
                if resolved != path or identity(path) != before:
                    raise ValueError("media changed during read verification")
                # Shared, bounded and descriptor-bound: hash the file object itself.
                # Its own descriptor, not the caller's -- a caller may be
                # cancelled and close its fd while this worker is still reading.
                worker, _ = open_descriptor(path)
                try:
                    if descriptor_identity(worker) != before:
                        raise ValueError("media changed during read verification")
                    if media_module.digest_descriptor(worker) != reference["sha256"]:
                        raise ValueError("media content hash mismatch; relink required")
                    if descriptor_identity(worker) != before or identity(path) != before:
                        raise ValueError("media changed during read verification")
                finally:
                    os.close(worker)
                return resolved

            with self._lock:
                future = self._pending.get(key)
                # A worker may have completed before its cleanup callback acquires
                # this lock. Completed hashes are never a cache, even in that gap.
                if future is not None and future.done():
                    del self._pending[key]
                    future = None
                if future is None:
                    if len(self._pending) >= self._capacity:
                        raise ReadVerificationBusy("media read verification capacity exceeded; retry later")
                    future = self._pool.submit(verify)
                    self._pending[key] = future

            def completed(done):
                with self._lock:
                    if self._pending.get(key) is done:
                        del self._pending[key]

            # Outside the lock: add_done_callback may run synchronously.
            future.add_done_callback(completed)
            wrapped = asyncio.wrap_future(future)
            # Observe failures even if every HTTP caller disconnects. Shield ensures
            # one cancelled caller never cancels shared or queued verification.
            wrapped.add_done_callback(lambda done: None if done.cancelled() else done.exception())
            result = await asyncio.shield(wrapped)
            if identity(path) != before or descriptor_identity(fd) != before:
                raise ValueError("media changed after read verification")
            if REQUIRES_CONTENT_RECHECK:  # pragma: no cover - Windows only
                # The identity comparisons above decide nothing on Windows, so the
                # caller's own descriptor must be hashed, not merely stat-ed.
                if await media_module.blocking(media_module.digest_descriptor, fd) != reference["sha256"]:
                    raise ValueError("media content hash mismatch; relink required")
            handle = VerifiedRead.bind(result, fd, os.fstat(fd), reference["sha256"])
            fd = None
            return handle
        finally:
            if fd is not None:
                os.close(fd)


read_verifier = ReadVerifier()
