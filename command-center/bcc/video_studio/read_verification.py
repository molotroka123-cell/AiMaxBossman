"""Bound concurrent read verification; never cache a completed content check.

Only simultaneous requests for the same file identity share a full hash. A later
request always rehashes, including same-size rewrites with restored timestamps.

WHAT THIS BOUNDARY DOES AND DOES NOT GUARANTEE
----------------------------------------------
Verification is DESCRIPTOR-bound, not pathname-bound. Every caller opens its own
read-only descriptor BEFORE admission and streams the HTTP body from it -- the
pathname is never re-opened at send time. An open descriptor pins the inode, so
unlink+recreate, rename or a symlink swap of the pathname after the check cannot
substitute the bytes that are served.

The shared hash runs on the worker's OWN descriptor rather than the caller's, so
that a cancelled caller closing its fd cannot break verification for the others.
Precisely: the worker proves "the inode with this (dev, ino, size, mtime, ctime)
hashes to the reference digest", and each caller's fstat -- taken before and
after -- proves its held descriptor is that same inode, unchanged. Same dev+ino
is the same inode; it is not a second hash of the caller's own descriptor.

It does NOT stop a privileged writer from rewriting that same inode in place
while the body streams; stat identity is the only signal for that, and it is a
change detector, not protection. os.O_NOFOLLOW does not exist on Windows, so the
open itself is racy against a junction/symlink swap there, and Windows st_ctime
is creation time rather than a change clock -- REQUIRES_CONTENT_RECHECK below
turns the caller's post-verification check into a real content hash there.
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


def open_descriptor(path):
    """One open; fstat, hash and body all come from THIS descriptor afterwards."""
    fd = os.open(path, OPEN_FLAGS)
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
    return _tuple(path.stat())


def descriptor_identity(fd):
    """Identity of the inode actually held open, not of whatever the name means."""
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
                # Shared, bounded and descriptor-bound: hash the inode itself.
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
