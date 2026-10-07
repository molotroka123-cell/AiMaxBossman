"""Genjutsu (tools/genjutsu/genjutsu.py): CPU-only checks of the parts that decide what the owner hears and keeps.

Owner request 06.10: copy the source audio instead of re-encoding it to AAC; the background outside the feathered
mask must stay the source pixels in the composited frames.
"""
from __future__ import annotations

import json
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

np = pytest.importorskip("numpy", reason="root-ci installs no Command Center / media deps")
pytest.importorskip("PIL", reason="root-ci installs no Command Center / media deps")
from PIL import Image  # noqa: E402

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "tools" / "genjutsu"))
pytestmark = pytest.mark.skipif(shutil.which("ffmpeg") is None or shutil.which("ffprobe") is None,
                                reason="ffmpeg/ffprobe not on PATH")
gj = pytest.importorskip("genjutsu")


def _probe(path: Path) -> dict:
    return json.loads(subprocess.run(["ffprobe", "-v", "error", "-show_streams", "-show_format", "-of", "json", str(path)],
                                     capture_output=True, text=True, check=True).stdout)


def _job(tmp: Path, n: int, size=(64, 48)) -> tuple[Path, list[Image.Image]]:
    job = tmp / "job"
    for d in ("final", "src", "ctrl", "gen", "mask"):
        (job / d).mkdir(parents=True)
    frames = []
    rng = np.random.default_rng(0)
    for i in range(1, n + 1):
        src = Image.fromarray(rng.integers(0, 255, (size[1], size[0], 3), dtype=np.uint8))
        frames.append(src)
        for d in ("final", "src", "ctrl"):
            src.save(job / d / f"{i:04d}.png")
    return job, frames


def test_source_mp3_audio_is_copied_not_reencoded_and_cut_to_the_video(tmp_path):
    job, _ = _job(tmp_path, 24)
    src = tmp_path / "src.mp4"            # 6 s source: test video + mp3 audio
    subprocess.run(["ffmpeg", "-v", "error", "-y", "-f", "lavfi", "-i", "testsrc=size=64x48:rate=12:duration=6",
                    "-f", "lavfi", "-i", "sine=frequency=440:duration=6", "-c:v", "libx264", "-pix_fmt", "yuv420p",
                    "-c:a", "libmp3lame", "-b:a", "128k", str(src)], check=True)
    mp4 = gj.stage_encode(job, src, 12, 1.0)          # 24 frames @12 fps = 2 s, starting at 1 s
    a = next(s for s in _probe(mp4)["streams"] if s["codec_type"] == "audio")
    assert a["codec_name"] == "mp3"                   # copied, not re-encoded to aac
    assert abs(float(a["duration"]) - 2.0) <= 0.1     # cut to the video, not the rest of the source


def test_source_without_audio_still_encodes(tmp_path):
    job, _ = _job(tmp_path, 12)
    src = tmp_path / "silent.mp4"
    subprocess.run(["ffmpeg", "-v", "error", "-y", "-f", "lavfi", "-i", "testsrc=size=64x48:rate=12:duration=2",
                    "-c:v", "libx264", "-pix_fmt", "yuv420p", str(src)], check=True)
    mp4 = gj.stage_encode(job, src, 12, 0.0)
    assert not any(s["codec_type"] == "audio" for s in _probe(mp4)["streams"])


def test_background_outside_the_feather_is_the_source_pixels(tmp_path):
    job, frames = _job(tmp_path, 2, size=(96, 96))
    for i in (1, 2):
        Image.fromarray(np.full((96, 96, 3), 200, np.uint8)).save(job / "gen" / f"{i:04d}.png")
        m = np.zeros((96, 96), np.uint8)
        m[40:56, 40:56] = 255
        Image.fromarray(m).save(job / "mask" / f"{i:04d}.png")
    gj.stage_composite(job, frames, "swap")
    out = np.asarray(Image.open(job / "final" / "0001.png").convert("RGB"))
    src = np.asarray(frames[0])
    far = np.ones((96, 96), bool)
    far[10:86, 10:86] = False                          # well outside mask + 6 px blur
    assert (out[far] == src[far]).all()
    assert not (out[44:52, 44:52] == src[44:52, 44:52]).all()   # inside the mask the result is used
