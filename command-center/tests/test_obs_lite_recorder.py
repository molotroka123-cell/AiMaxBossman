"""Dedicated unit and route tests for obs_lite_recorder (screen recording & ffmpeg edge cropping)."""
from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

from bcc.features import obs_lite_recorder
from bcc.tools import REGISTRY


def test_obs_lite_status_metadata_and_pinned_hashes():
    st = obs_lite_recorder.status()
    assert st["id"] == "obs_lite"
    assert st["version"] == "v0.3.0"
    assert st["license"] == "MIT"
    assert st["privacy_egress"] == "loopback_only"
    assert st["sha256_zip"] == "593100A30EA0BC7BDC45B4B67E6D934B412E9FD7E52E536D43123245950241FE"
    assert st["sha256_msi"] == "4F0F78CF057BDCCF2A64BC77E13CA1FEBA634EA0B1CD6344BEBC987F9843FD81"
    assert st["status"] in ("installed", "needs_setup")


def test_parse_selftest_stdout_extracts_all_telemetry():
    sample_stdout = (
        "obs-lite selftest starting (8s)\n"
        "source: Monitor 1 (1920x1080 @ 60Hz)\n"
        "encoder: AMDh264Encoder (Hardware)\n"
        "captured frames: 211\n"
        "encoded: 264 lagged: 0\n"
        "file: C:\\Users\\asd\\Videos\\selftest.mp4\n"
    )
    parsed = obs_lite_recorder.parse_selftest_stdout(sample_stdout)
    assert parsed["source"] == "Monitor 1 (1920x1080 @ 60Hz)"
    assert parsed["encoder"] == "AMDh264Encoder (Hardware)"
    assert parsed["captured_frames"] == 211
    assert parsed["encoded_frames"] == 264
    assert parsed["lagged_frames"] == 0
    assert parsed["output_mp4"] == "C:\\Users\\asd\\Videos\\selftest.mp4"


def test_parse_selftest_stdout_handles_malformed_or_fallback_path():
    raw = (
        "source: Primary Display\n"
        "captured frames: not_a_number\n"
        "encoded: bad lagged: bad\n"
        "Saved recording to D:/captures/clip_01.mp4\n"
    )
    parsed = obs_lite_recorder.parse_selftest_stdout(raw)
    assert parsed["source"] == "Primary Display"
    assert parsed["captured_frames"] == 0
    assert parsed["encoded_frames"] == 0
    assert parsed["lagged_frames"] == 0
    assert parsed["output_mp4"] == "D:/captures/clip_01.mp4"


def test_crop_video_edges_computes_even_dimensions_and_invokes_ffmpeg(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    src_mp4 = tmp_path / "raw.mp4"
    src_mp4.write_bytes(b"\x00\x00\x00\x18ftypmp42" + b"\x00" * 128)
    out_mp4 = tmp_path / "cropped.mp4"
    fake_ffmpeg = tmp_path / "ffmpeg.exe"
    fake_ffmpeg.write_bytes(b"MZ")

    monkeypatch.setattr(obs_lite_recorder, "find_ffmpeg_exe", lambda: fake_ffmpeg)
    captured_cmd: list[str] = []

    def _fake_run(cmd, **kwargs):
        captured_cmd.extend(cmd)
        out_mp4.write_bytes(b"\x00\x00\x00\x18ftypmp42" + b"\x01" * 96)
        return subprocess.CompletedProcess(cmd, 0, stdout="ok", stderr="")

    monkeypatch.setattr(subprocess, "run", _fake_run)

    res = obs_lite_recorder.crop_video_edges(
        src_mp4,
        out_mp4,
        crop_left=81,
        crop_right=80,
        crop_top=55,
        crop_bottom=64,
        in_width=1920,
        in_height=1080,
    )
    # (1920 - 161) // 2 * 2 = 1758; (1080 - 119) // 2 * 2 = 960
    assert res["width"] == 1758
    assert res["height"] == 960
    assert res["crop_filter"] == "crop=1758:960:81:55"
    assert res["size_bytes"] > 0
    assert "-vf" in captured_cmd
    assert "crop=1758:960:81:55" in captured_cmd


def test_crop_video_edges_raises_when_ffmpeg_or_input_missing(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setattr(obs_lite_recorder, "find_ffmpeg_exe", lambda: None)
    with pytest.raises(RuntimeError, match="ffmpeg not found"):
        obs_lite_recorder.crop_video_edges(tmp_path / "in.mp4", tmp_path / "out.mp4")

    fake_ffmpeg = tmp_path / "ffmpeg.exe"
    fake_ffmpeg.write_bytes(b"MZ")
    monkeypatch.setattr(obs_lite_recorder, "find_ffmpeg_exe", lambda: fake_ffmpeg)
    with pytest.raises(FileNotFoundError, match="Input video not found"):
        obs_lite_recorder.crop_video_edges(tmp_path / "missing.mp4", tmp_path / "out.mp4")


def test_record_screen_runs_selftest_and_stamps_output(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    fake_exe = tmp_path / "obs-lite.exe"
    fake_exe.write_bytes(b"MZ")
    monkeypatch.setattr(obs_lite_recorder, "find_obs_lite_exe", lambda: fake_exe)

    def _fake_run(cmd, cwd=None, **kwargs):
        selftest_mp4 = Path(cwd) / "selftest.mp4"
        selftest_mp4.write_bytes(b"\x00\x00\x00\x18ftypmp42" + b"\x02" * 200)
        stdout = (
            "source: Display 1\n"
            "encoder: AMDh264Encoder\n"
            "captured frames: 120\n"
            "encoded: 120 lagged: 0\n"
            f"file: {selftest_mp4}\n"
        )
        return subprocess.CompletedProcess(cmd, 0, stdout=stdout, stderr="")

    monkeypatch.setattr(subprocess, "run", _fake_run)
    rec = obs_lite_recorder.record_screen(duration_sec=5, output_dir=tmp_path)
    assert rec["returncode"] == 0
    assert rec["duration_requested_s"] == 5
    assert rec["captured_frames"] == 120
    assert rec["encoded_frames"] == 120
    assert rec["lagged_frames"] == 0
    assert rec["lag_ratio"] == 0.0
    assert Path(rec["output_mp4"]).is_file()
    assert rec["size_bytes"] > 100


async def test_obs_lite_tool_spec_is_ask_gated_and_external_output():
    await obs_lite_recorder._setup(None)
    spec = REGISTRY.get("obs_lite.record")
    assert spec is not None
    assert spec.default_effect == "ask"
    assert spec.permission == "desktop.control"
    assert spec.external_output is True
