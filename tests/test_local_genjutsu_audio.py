"""Synthetic coverage for preserving video when the source audio ends early."""

import importlib.util
import json
import subprocess
from pathlib import Path

import pytest


ROOT = Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location(
    "local_genjutsu_audio_test", ROOT / "tools" / "motion_studio" / "local_genjutsu.py"
)
genjutsu = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(genjutsu)


def make_source(path: Path, *, audio_duration: float | None) -> None:
    cmd = [
        "ffmpeg", "-v", "error", "-n", "-f", "lavfi", "-i",
        "color=c=blue:s=64x96:r=16:d=1",
    ]
    if audio_duration is not None:
        cmd += ["-f", "lavfi", "-i", f"sine=frequency=440:duration={audio_duration}", "-map", "0:v", "-map", "1:a"]
    cmd += ["-c:v", "libx264", "-pix_fmt", "yuv420p"]
    if audio_duration is not None:
        cmd += ["-c:a", "aac"]
    subprocess.run([*cmd, str(path)], check=True, timeout=20)


def probe(path: Path) -> dict:
    result = subprocess.run(
        ["ffprobe", "-v", "error", "-count_frames", "-show_entries",
         "stream=codec_type,nb_read_frames,width,height:format=duration", "-of", "json", str(path)],
        check=True, capture_output=True, text=True, timeout=20,
    )
    return json.loads(result.stdout)


@pytest.mark.parametrize("render", ["mux_audio", "finish"])
@pytest.mark.parametrize("audio_duration", [None, 0.25, 2.0])
def test_video_duration_is_preserved_for_missing_short_or_long_audio(tmp_path: Path, render: str, audio_duration: float | None) -> None:
    source = tmp_path / "source.mp4"
    output = tmp_path / "out.mp4"
    make_source(source, audio_duration=audio_duration)
    getattr(genjutsu, render)(source, source, 0.0, None, output)
    metadata = probe(output)
    video = next(s for s in metadata["streams"] if s["codec_type"] == "video")
    assert int(video["nb_read_frames"]) == 16
    assert 0.99 <= float(metadata["format"]["duration"]) <= 1.07
    assert any(s["codec_type"] == "audio" for s in metadata["streams"]) is (audio_duration is not None)


def test_head_mux_keeps_generated_video_geometry(tmp_path: Path) -> None:
    source = tmp_path / "source.mp4"
    output = tmp_path / "out.mp4"
    make_source(source, audio_duration=0.25)
    genjutsu.mux_audio(source, source, 0.0, None, output)
    video = next(s for s in probe(output)["streams"] if s["codec_type"] == "video")
    assert (video["width"], video["height"]) == (64, 96)
