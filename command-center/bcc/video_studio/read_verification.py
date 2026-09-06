"""Bound concurrent read verification; never cache a completed content check.

Only simultaneous requests for the same file identity share a full hash. A later
request always rehashes, including same-size rewrites with restored timestamps.
Stat identity detects ordinary concurrent replacement; it is not a hash or a
claim of protection against privileged filesystem mutation after verification.
"""
from __future__ import annotations

import asyncio
from concurrent.futures import ThreadPoolExecutor
import threading


class ReadVerificationBusy(RuntimeError):
    """Admission is full; no unverified media may be served as a fallback."""


def identity(path):
    value = path.stat()
    return (value.st_dev, value.st_ino, value.st_size, value.st_mtime_ns,
            value.st_ctime_ns)


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
        before = identity(path)
        key = (str(path), reference["sha256"], before)

        def verify():
            if identity(path) != before:
                raise ValueError("media changed before read verification")
            resolved = library.resolve(reference)
            if resolved != path or identity(path) != before:
                raise ValueError("media changed during read verification")
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
        if identity(path) != before:
            raise ValueError("media changed after read verification")
        return result


read_verifier = ReadVerifier()
