"""ffmpeg discovery must not stop at PATH.

A working static binary shipped inside a Python package is a real, usable
binary. Reporting "not supported" while it sits on disk is a defect, not
caution: the owner is told the camera cannot work when it can.
"""

from __future__ import annotations

import shutil

from ai_webcam_vision.config import Settings
from ai_webcam_vision.transport.ffmpeg import FfmpegRunner


def bundled_binary() -> str | None:
    try:
        import imageio_ffmpeg
    except Exception:
        return None
    try:
        return imageio_ffmpeg.get_ffmpeg_exe()
    except Exception:
        return None


def test_discovery_falls_back_to_the_bundled_binary(monkeypatch):
    """PATH is empty; a bundled binary exists; the app must find it."""
    monkeypatch.setenv("PATH", "/nonexistent-path-for-this-test")
    monkeypatch.delenv("AWV_FFMPEG", raising=False)
    runner = FfmpegRunner("ffmpeg")
    info = runner.info()

    bundled = bundled_binary()
    if bundled is None:
        # Honest negative: nothing to find, and the reason must say where we looked.
        assert info.available is False
        assert "imageio_ffmpeg" in (info.reason or "")
        return
    assert info.available is True, info.reason
    assert info.path == bundled
    assert info.source == "imageio_ffmpeg"


def test_awv_ffmpeg_environment_variable_is_honoured(monkeypatch, ffmpeg_path):
    monkeypatch.setenv("PATH", "/nonexistent-path-for-this-test")
    monkeypatch.setenv("AWV_FFMPEG", ffmpeg_path)
    runner = FfmpegRunner("ffmpeg")
    info = runner.info()
    assert info.available is True
    assert info.path == ffmpeg_path
    assert info.source == "AWV_FFMPEG"


def test_path_wins_over_the_bundled_binary(monkeypatch, ffmpeg_path, tmp_path):
    """A system ffmpeg is preferred: PATH -> AWV_FFMPEG -> imageio_ffmpeg."""
    fake_dir = tmp_path / "bin"
    fake_dir.mkdir()
    link = fake_dir / "ffmpeg"
    shutil.copy(ffmpeg_path, link)
    link.chmod(0o755)
    monkeypatch.setenv("PATH", str(fake_dir))
    monkeypatch.delenv("AWV_FFMPEG", raising=False)
    runner = FfmpegRunner("ffmpeg")
    info = runner.info()
    assert info.available is True
    assert info.path == str(link)
    assert info.source == "path"


def test_explicit_configuration_beats_every_fallback(monkeypatch, ffmpeg_path):
    monkeypatch.setenv("AWV_FFMPEG", "/definitely/not/here/ffmpeg")
    runner = FfmpegRunner(ffmpeg_path)
    info = runner.info()
    assert info.available is True
    assert info.path == ffmpeg_path
    assert info.source == "configured"


def test_unavailable_reason_names_every_place_searched(monkeypatch):
    monkeypatch.setenv("PATH", "/nonexistent-path-for-this-test")
    monkeypatch.setenv("AWV_FFMPEG", "/nonexistent-path-for-this-test/ffmpeg")
    runner = FfmpegRunner("ffmpeg", allow_bundled=False)
    info = runner.info()
    assert info.available is False
    reason = info.reason or ""
    for place in ("PATH", "AWV_FFMPEG", "imageio_ffmpeg"):
        assert place in reason, reason


async def test_file_mode_service_reports_ffmpeg_available_without_path_ffmpeg(
    monkeypatch, tmp_path, video_fixture
):
    """The end-to-end consequence: health must not say 'unavailable'."""
    from ai_webcam_vision.runtime.service import VisionService

    monkeypatch.setenv("PATH", "/nonexistent-path-for-this-test")
    monkeypatch.delenv("AWV_FFMPEG", raising=False)

    settings = Settings.from_env({
        "AWV_CAMERA_MODE": "file",
        "AWV_CAMERA_FIXTURE": str(video_fixture),
        "AWV_STATE_DIR": str(tmp_path / "state"),
    })
    service = VisionService(settings)
    try:
        health = service.health()
        if bundled_binary() is None:
            # Honest negative on a host with nothing to discover.
            assert health["ffmpeg"]["available"] is False
            assert health["status"] == "unavailable"
            return
        assert health["ffmpeg"]["available"] is True, health["ffmpeg"]
        assert health["status"] != "unavailable"
    finally:
        await service.aclose()
