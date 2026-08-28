from __future__ import annotations

import shutil
import socket
import subprocess
import time
from pathlib import Path

import pytest

from ai_webcam_vision.config import Settings
from ai_webcam_vision.transport.ffmpeg import FfmpegRunner


def resolve_ffmpeg() -> str | None:
    """Real ffmpeg binary if one can be found, else None.

    Order: an explicit path, PATH, then the static binary shipped by
    imageio-ffmpeg (a dev dependency). Nothing is faked: when this returns
    None the ffmpeg-dependent tests skip and say so.
    """
    found = shutil.which("ffmpeg")
    if found:
        return found
    try:
        import imageio_ffmpeg

        return imageio_ffmpeg.get_ffmpeg_exe()
    except Exception:
        return None


@pytest.fixture(scope="session")
def ffmpeg_path() -> str:
    path = resolve_ffmpeg()
    if not path:
        pytest.skip("no ffmpeg binary available; transport boundary cannot be exercised")
    return path


@pytest.fixture(scope="session")
def ffmpeg_runner(ffmpeg_path: str) -> FfmpegRunner:
    return FfmpegRunner(ffmpeg_path)


@pytest.fixture(scope="session")
def video_fixture(ffmpeg_path: str, tmp_path_factory) -> Path:
    """A deterministic 3 second clip standing in for the camera stream."""
    target = tmp_path_factory.mktemp("fixtures") / "room.mp4"
    subprocess.run(
        [
            ffmpeg_path, "-y", "-hide_banner", "-loglevel", "error",
            "-f", "lavfi", "-i", "testsrc=size=320x180:rate=5:duration=3",
            "-c:v", "libx264", "-pix_fmt", "yuv420p", str(target),
        ],
        check=True,
        timeout=120,
    )
    assert target.stat().st_size > 0
    return target


@pytest.fixture(scope="session")
def static_fixture(ffmpeg_path: str, tmp_path_factory) -> Path:
    """A motionless clip: the "empty room" reference material."""
    target = tmp_path_factory.mktemp("fixtures-static") / "empty.mp4"
    subprocess.run(
        [
            ffmpeg_path, "-y", "-hide_banner", "-loglevel", "error",
            "-f", "lavfi", "-i", "color=c=gray:size=320x180:rate=5:duration=2",
            "-c:v", "libx264", "-pix_fmt", "yuv420p", str(target),
        ],
        check=True,
        timeout=120,
    )
    return target


def closed_port() -> int:
    """A TCP port that is bound and immediately released: connecting refuses."""
    sock = socket.socket()
    sock.bind(("127.0.0.1", 0))
    port = sock.getsockname()[1]
    sock.close()
    return port


@pytest.fixture
def base_env(tmp_path: Path) -> dict[str, str]:
    return {
        "AWV_ROOM_ID": "test-room",
        "AWV_CAMERA_MODE": "mock",
        "AWV_STATE_DIR": str(tmp_path / "state"),
        "AWV_RETRY_MAX_ATTEMPTS": "2",
        "AWV_RETRY_BASE_DELAY_SECONDS": "0",
        "AWV_ACTIVE_INTERVAL_SECONDS": "0.01",
        "AWV_IDLE_INTERVAL_SECONDS": "0.01",
        "AWV_MAX_SAMPLE_RATE_HZ": "1000",
        "AWV_LOG_LEVEL": "DEBUG",
    }


@pytest.fixture
def settings(base_env: dict[str, str]) -> Settings:
    return Settings.from_env(base_env)


def wait_for_job(client, job_id: str, timeout: float = 30.0, headers: dict | None = None) -> dict:
    """Poll jobs.status until the job reaches a terminal state."""
    deadline = time.monotonic() + timeout
    payload = {}
    while time.monotonic() < deadline:
        response = client.get(f"/api/v1/jobs/{job_id}", headers=headers or {})
        assert response.status_code == 200, response.text
        payload = response.json()
        if payload["status"] in {"succeeded", "failed", "cancelled"}:
            return payload
        time.sleep(0.05)
    raise AssertionError(f"job {job_id} did not finish in {timeout}s: {payload}")
