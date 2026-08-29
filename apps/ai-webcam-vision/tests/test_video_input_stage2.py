"""Video input, audited hostile.

Two questions: can a camera password reach anything a human or a machine can
read, and can a bad frame be mistaken for a good one.

The canary value below exists nowhere else. If it appears in a log line, an
exception, a health payload, an API response or a file on disk, the
application leaks the clinic's camera credentials.
"""

from __future__ import annotations

import asyncio
import json
import os
import subprocess
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from ai_webcam_vision.config import Settings
from ai_webcam_vision.errors import CaptureError, CaptureTimeout, StaleFrame
from ai_webcam_vision.runtime.service import VisionService
from ai_webcam_vision.transport.base import Frame, SourceKind
from ai_webcam_vision.transport.ffmpeg import FfmpegRunner
from ai_webcam_vision.transport.mock import SyntheticFrameSource, SyntheticScene

CANARY = "BOSSMAN_CANARY_SECRET_91f03f_DO_NOT_LEAK"  # ci-secret-scan: allow


def _all_files(root: Path) -> dict[str, str]:
    out: dict[str, str] = {}
    if not root.exists():
        return out
    for path in root.rglob("*"):
        if path.is_file():
            out[str(path)] = path.read_bytes().decode("utf-8", "replace")
    return out


# ------------------------------------------------------------------- canary
def test_named_canary_never_escapes_any_channel(tmp_path, ffmpeg_path):
    """Drive the mandated canary through a real, failing RTSP connection."""
    from fastapi.testclient import TestClient

    from ai_webcam_vision.api import build_app

    from conftest import closed_port, wait_for_job

    state_dir = tmp_path / "state"
    log_file = tmp_path / "app.log"
    settings = Settings.from_env({
        "AWV_ROOM_ID": "canary-room-2",
        "AWV_CAMERA_MODE": "rtsp",
        "AWV_CAMERA_HOST": "127.0.0.1",
        "AWV_CAMERA_PORT": str(closed_port()),
        "AWV_CAMERA_USERNAME": "clinic_camera_account",
        "AWV_CAMERA_PASSWORD": CANARY,
        "AWV_FFMPEG_PATH": ffmpeg_path,
        "AWV_STATE_DIR": str(state_dir),
        "AWV_LOG_FILE": str(log_file),
        "AWV_LOG_LEVEL": "DEBUG",
        "AWV_CONNECT_TIMEOUT_SECONDS": "3",
        "AWV_CAPTURE_TIMEOUT_SECONDS": "5",
        "AWV_RETRY_MAX_ATTEMPTS": "2",
        "AWV_RETRY_BASE_DELAY_SECONDS": "0",
    })
    service = VisionService(settings)
    app = build_app(settings, service=service)

    emitted: list[str] = []
    with TestClient(app) as client:
        for job_type in ("baseline", "probe", "sample"):
            created = client.post("/api/v1/jobs", json={"type": job_type})
            emitted.append(json.dumps(wait_for_job(client, created.json()["id"])))
        for path in (
            "/healthz",
            "/api/v1/health",
            "/api/v1/capabilities",
            "/api/v1/metrics",
            "/api/v1/jobs",
            "/api/v1/artifacts",
            "/api/v1/rooms/canary-room-2/metrics/today",
        ):
            response = client.get(path)
            assert response.status_code == 200, path
            emitted.append(response.text)

    # The health payload specifically, including every component detail.
    emitted.append(json.dumps(service.health()))
    emitted.append(json.dumps(service.capabilities()))
    emitted.append(json.dumps(service.metrics()))
    emitted.append(repr(settings))
    emitted.append(repr(service.source.descriptor))

    for name, content in _all_files(state_dir).items():
        emitted.append(name)
        emitted.append(content)
    if log_file.exists():
        emitted.append(log_file.read_text(encoding="utf-8"))

    haystack = "\n".join(emitted)
    assert CANARY not in haystack, "camera password escaped an emitted channel"
    # And the scenario really failed, so the leak path was actually exercised.
    assert "capture_failed" in haystack or "capture_timeout" in haystack


def test_canary_never_reaches_an_exception_or_its_traceback(tmp_path, ffmpeg_path):
    import traceback

    from conftest import closed_port

    from ai_webcam_vision.transport import build_source

    settings = Settings.from_env({
        "AWV_CAMERA_MODE": "rtsp",
        "AWV_CAMERA_HOST": "127.0.0.1",
        "AWV_CAMERA_PORT": str(closed_port()),
        "AWV_CAMERA_USERNAME": "clinic_camera_account",
        "AWV_CAMERA_PASSWORD": CANARY,
        "AWV_FFMPEG_PATH": ffmpeg_path,
        "AWV_STATE_DIR": str(tmp_path / "state"),
        "AWV_CONNECT_TIMEOUT_SECONDS": "3",
        "AWV_CAPTURE_TIMEOUT_SECONDS": "5",
    })
    source = build_source(settings)

    async def run() -> str:
        with pytest.raises(CaptureError) as excinfo:
            await source.grab()
        exc = excinfo.value
        return "\n".join([
            str(exc),
            repr(exc),
            "".join(exc.args),
            "".join(traceback.format_exception(type(exc), exc, exc.__traceback__)),
        ])

    assert CANARY not in asyncio.run(run())


def test_a_full_credentialed_url_is_never_logged(tmp_path, ffmpeg_path, caplog):
    """No `rtsp://user:pass@host` string, in any form, in any log record."""
    import logging

    from ai_webcam_vision.logging_setup import configure_logging

    from conftest import closed_port

    from ai_webcam_vision.transport import build_source

    log_file = tmp_path / "urls.log"
    configure_logging("DEBUG", log_file)
    settings = Settings.from_env({
        "AWV_CAMERA_MODE": "rtsp",
        "AWV_CAMERA_HOST": "127.0.0.1",
        "AWV_CAMERA_PORT": str(closed_port()),
        "AWV_CAMERA_USERNAME": "clinic_camera_account",
        "AWV_CAMERA_PASSWORD": CANARY,
        "AWV_FFMPEG_PATH": ffmpeg_path,
        "AWV_STATE_DIR": str(tmp_path / "state"),
        "AWV_CONNECT_TIMEOUT_SECONDS": "3",
        "AWV_CAPTURE_TIMEOUT_SECONDS": "5",
    })
    source = build_source(settings)

    async def run() -> None:
        with pytest.raises(CaptureError):
            await source.grab()

    asyncio.run(run())
    for handler in logging.getLogger("ai_webcam_vision").handlers:
        handler.flush()
    text = log_file.read_text(encoding="utf-8")
    assert CANARY not in text
    assert "clinic_camera_account:" not in text
    # A userinfo section may only ever appear fully masked.
    for line in text.splitlines():
        if "rtsp://" in line and "@" in line:
            assert "***:***@" in line or "<stream-url>" in line, line
    logging.getLogger("ai_webcam_vision").handlers.clear()


# ------------------------------------------------------------- frame health
def test_short_frame_is_rejected_not_padded(tmp_path, ffmpeg_path):
    """A truncated capture must fail, never become a half-black frame."""

    class ShortRunner(FfmpegRunner):
        async def run(self, args, *, timeout, url=None, expect_stdout=True):  # noqa: ANN001
            return b"\x00" * 100

    from ai_webcam_vision.transport import build_source

    settings = Settings.from_env({
        "AWV_CAMERA_MODE": "file",
        "AWV_CAMERA_FIXTURE": str(tmp_path / "nothing.mp4"),
        "AWV_FFMPEG_PATH": ffmpeg_path,
        "AWV_STATE_DIR": str(tmp_path / "state"),
    })
    source = build_source(settings, ShortRunner(ffmpeg_path))

    async def run():
        with pytest.raises(CaptureError) as excinfo:
            await source.grab()
        return str(excinfo.value)

    assert "short frame" in asyncio.run(run())


async def test_a_stale_frame_is_not_analysed_as_current(settings):
    """A frame that took a minute to arrive is not evidence about now."""
    source = SyntheticFrameSource(scene=SyntheticScene(room_activity=True))
    service = VisionService(settings, source=source)
    try:
        service.baseline.save(await source.grab())
        fresh = await source.grab()
        stale = Frame(
            seq=fresh.seq + 1,
            ts=datetime.now(timezone.utc) - timedelta(seconds=service.max_frame_age + 5),
            width=fresh.width,
            height=fresh.height,
            data=fresh.data,
            source_kind=SourceKind.SYNTHETIC,
        )
        with pytest.raises(StaleFrame):
            await service._analyze_and_store(stale)
        assert service.counters.frames_stale == 1
        assert service.counters.observations_stored == 0
    finally:
        await service.aclose()


async def test_a_fresh_frame_is_accepted(settings):
    source = SyntheticFrameSource(scene=SyntheticScene(room_activity=True))
    service = VisionService(settings, source=source)
    try:
        service.baseline.save(await source.grab())
        await service.sample_once()
        assert service.counters.frames_stale == 0
        assert service.counters.observations_stored == 1
    finally:
        await service.aclose()


async def test_stale_frames_do_not_stop_the_persistent_runtime(settings):
    """One late frame is a hiccup, not a reason to stop watching the room."""
    from ai_webcam_vision.runtime.supervisor import RuntimeState, RuntimeSupervisor

    class LateThenFine(SyntheticFrameSource):
        def __init__(self) -> None:
            super().__init__(scene=SyntheticScene(room_activity=True))
            self.late = True

        async def grab(self) -> Frame:
            frame = await super().grab()
            if self.late:
                self.late = False
                return Frame(
                    seq=frame.seq,
                    ts=datetime.now(timezone.utc) - timedelta(hours=1),
                    width=frame.width,
                    height=frame.height,
                    data=frame.data,
                    source_kind=frame.source_kind,
                )
            return frame

    source = LateThenFine()
    service = VisionService(settings, source=source)
    service.baseline.save(await SyntheticFrameSource().grab())

    async def sleeper(_delay: float) -> None:
        await asyncio.sleep(0)

    supervisor = RuntimeSupervisor(service, sleep=sleeper)
    try:
        await supervisor.start()
        assert await supervisor.wait_for_state(RuntimeState.RUNNING, timeout=10.0)
        assert service.counters.frames_stale == 1
        assert service.counters.observations_stored >= 1
    finally:
        await supervisor.stop()
        await service.aclose()


# ------------------------------------------------------ child process reaping
async def test_timed_out_ffmpeg_leaves_no_process_behind(ffmpeg_path):
    """Killed is not enough: it must be reaped, or zombies accumulate."""
    psutil = pytest.importorskip("psutil")

    runner = FfmpegRunner(ffmpeg_path)
    me = psutil.Process()
    before = {child.pid for child in me.children(recursive=True)}

    with pytest.raises(CaptureTimeout):
        await runner.run(
            ["-hide_banner", "-loglevel", "error", "-f", "lavfi",
             "-i", "testsrc=size=320x180:rate=25", "-f", "null", "-"],
            timeout=1.0,
        )

    await asyncio.sleep(0.3)
    survivors = []
    for child in me.children(recursive=True):
        if child.pid in before:
            continue
        try:
            if child.status() == psutil.STATUS_ZOMBIE:
                survivors.append(("zombie", child.pid))
            elif child.is_running():
                survivors.append(("running", child.pid))
        except psutil.NoSuchProcess:
            continue
    assert not survivors, survivors


async def test_repeated_timeouts_do_not_accumulate_children(ffmpeg_path):
    psutil = pytest.importorskip("psutil")

    runner = FfmpegRunner(ffmpeg_path)
    me = psutil.Process()
    for _ in range(3):
        with pytest.raises(CaptureTimeout):
            await runner.run(
                ["-hide_banner", "-loglevel", "error", "-f", "lavfi",
                 "-i", "testsrc=size=320x180:rate=25", "-f", "null", "-"],
                timeout=0.6,
            )
    await asyncio.sleep(0.3)
    alive = [c.pid for c in me.children(recursive=True) if c.is_running()
             and c.status() != psutil.STATUS_ZOMBIE]
    assert not alive, alive


def test_the_stream_url_argument_exposure_is_declared_not_hidden():
    """The URL is in /proc/<pid>/cmdline. That is documented, not denied."""
    docs = (Path(__file__).resolve().parents[1] / "docs" / "PRIVACY.md").read_text(encoding="utf-8")
    assert "cmdline" in docs
