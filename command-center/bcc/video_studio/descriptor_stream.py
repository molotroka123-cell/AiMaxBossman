"""Serve HTTP bodies from the verified descriptor, never from a pathname again.

starlette's FileResponse re-stats and re-opens ``self.path`` when it is called,
and may emit ``http.response.pathsend`` -- handing the pathname to the ASGI
server, which opens it a third time. Either undoes a completed verification: the
name can point somewhere else by then. These responses read only from the
descriptor that was fstat-ed and hashed, and close it on every exit path,
including an aborted body and a client disconnect.
"""
from __future__ import annotations

import mimetypes
import os
import re
from urllib.parse import quote

from starlette.responses import Response, StreamingResponse

CHUNK = 256 * 1024
RANGE = re.compile(r"^bytes=(\d*)-(\d*)$")
UNSATISFIABLE = object()


def content_disposition(filename: str) -> str:
    """Owner-supplied names never reach a header raw; quote() forces the encoded
    form for anything outside the unreserved set, quotes and control bytes too."""
    encoded = quote(filename)
    if encoded == filename:
        return f'attachment; filename="{filename}"'
    return f"attachment; filename*=utf-8''{encoded}"


def parse_range(value, size):
    """A single byte range. Multi-range and malformed headers are ignored, which
    RFC 9110 permits; only a syntactically valid but unsatisfiable one is 416."""
    if not value:
        return None
    match = RANGE.match(value.strip())
    if not match:
        return None
    first, last = match.group(1), match.group(2)
    if not first and not last:
        return None
    if not size:
        return UNSATISFIABLE
    if not first:
        length = int(last)
        if length <= 0:
            return UNSATISFIABLE
        return max(0, size - length), size - 1
    start = int(first)
    if start >= size:
        return UNSATISFIABLE
    end = size - 1 if not last else min(int(last), size - 1)
    if end < start:
        return UNSATISFIABLE
    return start, end


class DescriptorResponse(StreamingResponse):
    """Owns one descriptor and closes it exactly once, whatever happens."""

    def __init__(self, fd, *, status_code, headers, media_type, start, length):
        self._fd = fd
        super().__init__(self._stream(fd, start, length), status_code=status_code,
                         headers=headers, media_type=media_type)

    async def _stream(self, fd, start, length):
        from .media import blocking, pread
        offset, remaining = start, length
        try:
            while remaining > 0:
                # blocking() waits for the worker even when the caller is being
                # cancelled, so the descriptor is never closed mid-read.
                block = await blocking(pread, fd, min(CHUNK, remaining), offset)
                if not block:
                    # The held inode was truncated under us. Stop short rather
                    # than invent padding to satisfy the declared content-length.
                    return
                offset += len(block)
                remaining -= len(block)
                yield block
        finally:
            self.release()

    def release(self):
        fd, self._fd = self._fd, None
        if fd is not None:
            os.close(fd)

    async def __call__(self, scope, receive, send):
        try:
            await super().__call__(scope, receive, send)
        finally:
            self.release()


def descriptor_response(fd, stat, *, media_type=None, filename=None, range_header=None):
    """Take ownership of `fd` and serve `stat.st_size` bytes read from it.

    The length comes from the fstat taken at verification time, not from a fresh
    stat of the pathname, and the reads are positional -- no shared seek pointer.
    """
    keep = False
    try:
        size = stat.st_size
        headers = {"accept-ranges": "bytes"}
        if filename:
            headers["content-disposition"] = content_disposition(filename)
        if media_type is None:
            media_type = (filename and mimetypes.guess_type(filename)[0]) or "application/octet-stream"
        window = parse_range(range_header, size)
        if window is UNSATISFIABLE:
            return Response(status_code=416, headers={**headers, "content-range": f"bytes */{size}"})
        start, end = window if window else (0, size - 1)
        length = end - start + 1 if size else 0
        headers["content-length"] = str(length)
        if window:
            headers["content-range"] = f"bytes {start}-{end}/{size}"
        response = DescriptorResponse(fd, status_code=206 if window else 200, headers=headers,
                                      media_type=media_type, start=start, length=length)
        keep = True
        return response
    finally:
        if not keep:
            os.close(fd)


def stream_verified(handle, request=None, *, media_type=None, filename=None):
    """Hand a VerifiedRead's descriptor to a response; the handle is spent after."""
    if media_type is None:
        media_type = ((filename and mimetypes.guess_type(filename)[0])
                      or mimetypes.guess_type(handle.name)[0])
    fd, info = handle.detach()
    return descriptor_response(fd, info, media_type=media_type, filename=filename,
                               range_header=request.headers.get("range") if request else None)
