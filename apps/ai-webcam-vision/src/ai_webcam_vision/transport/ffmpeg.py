"""ffmpeg-backed transport.

Everything that can leak a credential is contained here: the URL is only ever
handed to the child process, stderr is scrubbed before it becomes an error, and
the argument vector is never logged in raw form.
"""

from __future__ import annotations

import asyncio
import itertools
import shutil
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, Sequence

from ..errors import CaptureError, CaptureTimeout, DependencyMissing
from ..logging_setup import get_logger
from ..secretstore import SecretUrl, scrub
from .base import Frame, FrameSource, ProbeResult, SourceDescriptor, SourceKind, utcnow

log = get_logger("transport.ffmpeg")

_URL_PLACEHOLDER = "<stream-url>"


@dataclass(frozen=True)
class FfmpegInfo:
    available: bool
    path: str | None
    version: str | None
    reason: str | None = None

    def to_dict(self) -> dict:
        return {
            "available": self.available,
            "path": self.path,
            "version": self.version,
            "reason": self.reason,
        }


class FfmpegRunner:
    """Thin, timeout-bounded wrapper around the ffmpeg binary."""

    def __init__(self, ffmpeg_path: str = "ffmpeg") -> None:
        self._configured = ffmpeg_path
        self._resolved: str | None = None
        self._info: FfmpegInfo | None = None

    # ------------------------------------------------------------ discovery
    def resolve(self) -> str | None:
        if self._resolved is None:
            candidate = Path(self._configured)
            if candidate.is_file():
                self._resolved = str(candidate)
            else:
                self._resolved = shutil.which(self._configured)
        return self._resolved

    def info(self, *, refresh: bool = False) -> FfmpegInfo:
        """Honest availability report. Never claims a binary that is absent."""
        if self._info is not None and not refresh:
            return self._info
        path = self.resolve()
        if not path:
            self._info = FfmpegInfo(
                available=False,
                path=None,
                version=None,
                reason=f"ffmpeg binary {self._configured!r} not found in PATH",
            )
            return self._info
        import subprocess

        try:
            out = subprocess.run(
                [path, "-hide_banner", "-version"],
                capture_output=True,
                text=True,
                timeout=10,
                check=False,
            )
            first = (out.stdout or out.stderr or "").splitlines()
            version = first[0].strip() if first else "unknown"
            self._info = FfmpegInfo(available=out.returncode == 0, path=path, version=version,
                                    reason=None if out.returncode == 0 else "ffmpeg -version failed")
        except Exception as exc:  # pragma: no cover - defensive
            self._info = FfmpegInfo(False, path, None, scrub(exc))
        return self._info

    def require(self) -> str:
        info = self.info()
        if not info.available or not info.path:
            raise DependencyMissing(info.reason or "ffmpeg is not available")
        return info.path

    # -------------------------------------------------------------- running
    async def run(
        self,
        args: Sequence[str],
        *,
        timeout: float,
        url: SecretUrl | None = None,
        expect_stdout: bool = True,
    ) -> bytes:
        """Run ffmpeg with a hard timeout. Returns stdout bytes.

        The child is killed and reaped on timeout: a hung ffmpeg must never
        outlive the call that started it.
        """
        binary = self.require()
        argv = [binary, *args]
        log.debug("ffmpeg exec %s", " ".join(_safe_argv(argv, url)))

        try:
            proc = await asyncio.create_subprocess_exec(
                *argv,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )
        except FileNotFoundError as exc:
            raise DependencyMissing(f"ffmpeg binary disappeared: {scrub(exc)}") from None
        except OSError as exc:
            raise CaptureError(f"cannot start ffmpeg: {scrub(exc)}") from None

        try:
            stdout, stderr = await asyncio.wait_for(proc.communicate(), timeout=timeout)
        except asyncio.TimeoutError:
            await _terminate(proc)
            raise CaptureTimeout(f"ffmpeg exceeded {timeout:.1f}s and was killed") from None
        except asyncio.CancelledError:
            await _terminate(proc)
            raise

        if proc.returncode != 0:
            detail = scrub(stderr.decode("utf-8", "replace")).strip().splitlines()
            tail = " | ".join(detail[-3:]) if detail else "no stderr"
            raise CaptureError(f"ffmpeg exited with {proc.returncode}: {tail}")
        if expect_stdout and not stdout:
            raise CaptureError("ffmpeg produced no output")
        return stdout


async def _terminate(proc: asyncio.subprocess.Process) -> None:
    if proc.returncode is not None:
        return
    try:
        proc.kill()
    except ProcessLookupError:  # pragma: no cover - already gone
        return
    try:
        await asyncio.wait_for(proc.wait(), timeout=5)
    except asyncio.TimeoutError:  # pragma: no cover - unkillable child
        log.error("ffmpeg child did not die after kill")


def _safe_argv(argv: Sequence[str], url: SecretUrl | None) -> list[str]:
    """Argument vector rendered for logs: the URL is replaced, never masked-in-place."""
    raw = url.reveal() if url is not None else None
    out = []
    for item in argv:
        out.append(_URL_PLACEHOLDER if raw and item == raw else scrub(item))
    return out


class FfmpegFrameSource(FrameSource):
    """Pulls a single grayscale frame per call through ffmpeg.

    One ffmpeg invocation per sample keeps the failure surface small and makes
    every capture independently timeout-bounded; the sampling rate is what
    keeps the cost sane, not a long-lived process.
    """

    def __init__(
        self,
        *,
        url_provider: Callable[[], SecretUrl],
        runner: FfmpegRunner,
        descriptor: SourceDescriptor,
        width: int,
        height: int,
        connect_timeout: float,
        capture_timeout: float,
        rtsp_transport: str | None = "tcp",
    ) -> None:
        self._url_provider = url_provider
        self._runner = runner
        self.descriptor = descriptor
        self._width = width
        self._height = height
        self._connect_timeout = connect_timeout
        self._capture_timeout = capture_timeout
        self._rtsp_transport = rtsp_transport
        self._counter = itertools.count(1)

    # ------------------------------------------------------------- internal
    def _input_args(self, url: SecretUrl) -> list[str]:
        args = ["-hide_banner", "-loglevel", "error", "-nostdin"]
        if self.descriptor.kind is SourceKind.RTSP_CAMERA and self._rtsp_transport:
            args += ["-rtsp_transport", self._rtsp_transport]
            args += ["-timeout", str(int(self._connect_timeout * 1_000_000))]
        args += ["-i", url.reveal()]
        return args

    # ---------------------------------------------------------------- probe
    async def probe(self) -> ProbeResult:
        info = self._runner.info()
        if not info.available:
            return ProbeResult(False, self.descriptor, None, "dependency_missing", info.reason)
        url = self._url_provider()
        args = self._input_args(url) + [
            "-frames:v", "1", "-f", "null", "-",
        ]
        started = time.perf_counter()
        try:
            await self._runner.run(args, timeout=self._connect_timeout, url=url, expect_stdout=False)
        except (CaptureError, DependencyMissing) as exc:
            return ProbeResult(False, self.descriptor, None, exc.code, exc.safe_message)
        latency = (time.perf_counter() - started) * 1000.0
        return ProbeResult(True, self.descriptor, round(latency, 2))

    # ----------------------------------------------------------------- grab
    async def grab(self) -> Frame:
        url = self._url_provider()
        args = self._input_args(url) + [
            "-frames:v", "1",
            "-vf", f"scale={self._width}:{self._height}",
            "-pix_fmt", "gray",
            "-f", "rawvideo",
            "pipe:1",
        ]
        data = await self._runner.run(args, timeout=self._capture_timeout, url=url)
        expected = self._width * self._height
        if len(data) < expected:
            raise CaptureError(
                f"short frame: got {len(data)} bytes, expected {expected}"
            )
        return Frame(
            seq=next(self._counter),
            ts=utcnow(),
            width=self._width,
            height=self._height,
            data=data[:expected],
            source_kind=self.descriptor.kind,
        )

    async def grab_snapshot_jpeg(self, max_width: int, blur_sigma: float) -> bytes:
        """Privacy-safe still: downscaled, grayscale and blurred inside ffmpeg.

        The full-resolution frame never reaches this process' memory or disk.
        """
        url = self._url_provider()
        chain = [f"scale={int(max_width)}:-2", "format=gray"]
        if blur_sigma > 0:
            chain.append(f"gblur=sigma={blur_sigma:g}")
        args = self._input_args(url) + [
            "-frames:v", "1",
            "-vf", ",".join(chain),
            "-q:v", "12",
            "-f", "image2",
            "-vcodec", "mjpeg",
            "pipe:1",
        ]
        return await self._runner.run(args, timeout=self._capture_timeout, url=url)

    async def aclose(self) -> None:
        return None
