"""Motion Studio manifest + recovery of a finished render after a backend restart."""
from __future__ import annotations

import hashlib
import json
import shutil
import subprocess
import time
from pathlib import Path

import pytest

from bcc.features import motion_studio as ms

pytestmark = pytest.mark.skipif(not shutil.which("ffmpeg") or not shutil.which("ffprobe"), reason="needs ffmpeg")


def _make_video(path: Path, audio: bool = True) -> None:
    cmd = ["ffmpeg", "-y", "-loglevel", "error", "-f", "lavfi", "-i", "color=c=black:s=320x180:d=1:r=10"]
    if audio:
        cmd += ["-f", "lavfi", "-i", "sine=frequency=440:duration=1", "-c:a", "aac"]
    cmd += ["-c:v", "libx264", "-pix_fmt", "yuv420p", str(path)]
    subprocess.run(cmd, check=True)


def _job(folder: Path, mode: str = "full", **extra) -> dict:
    folder.mkdir(parents=True, exist_ok=True)
    (folder / "spec.json").write_text(json.dumps({"scenes": [{"type": "title"}]}), encoding="utf-8")
    job = {"id": "abc123def456", "mode": mode, "state": "running", "error": "", "started": time.time(),
           "finished": None, "style": "epic", "no_voice": True, "pid": 0, "dir": str(folder),
           "times": [], "music": True, "source": "custom", "scenes": 1, "subtitle_lines": 0}
    job.update(extra)
    return job


def test_settle_done_writes_manifest_with_hashes(tmp_path):
    folder = tmp_path / "job"
    job = _job(folder)
    _make_video(folder / "video.mp4")
    (folder / "soundtrack.wav").write_bytes(b"RIFF" + b"0" * 2000)
    ms._settle(job, 0)
    assert job["state"] == "done"
    manifest = json.loads((folder / "manifest.json").read_text(encoding="utf-8"))
    assert manifest["version"] == 1 and manifest["job_id"] == job["id"] and manifest["verified"] is True
    assert manifest["spec_sha256"] == hashlib.sha256((folder / "spec.json").read_bytes()).hexdigest()
    by = {f["name"]: f for f in manifest["files"]}
    assert set(by) >= {"video.mp4", "spec.json", "soundtrack.wav"}
    assert by["video.mp4"]["sha256"] == hashlib.sha256((folder / "video.mp4").read_bytes()).hexdigest()
    assert by["video.mp4"]["bytes"] == (folder / "video.mp4").stat().st_size
    assert manifest["check"]["audio"] and manifest["check"]["video"]
    assert "manifest.json" in ms.OUTPUTS


def test_failed_job_has_no_verified_manifest(tmp_path):
    folder = tmp_path / "job"
    job = _job(folder)
    ms._settle(job, 3)
    assert job["state"] == "failed"
    path = folder / "manifest.json"
    assert (not path.exists()) or json.loads(path.read_text(encoding="utf-8"))["verified"] is False


def test_lost_process_with_complete_verified_output_is_recovered_not_interrupted(tmp_path):
    folder = tmp_path / "job"
    job = _job(folder)
    _make_video(folder / "video.mp4")
    (folder / "make_video.log").write_text("scene 1\nvideo: " + str(folder / "video.mp4") + "\n", encoding="utf-8")
    ms._settle(job, None)
    assert job["state"] == "done"
    assert job.get("recovered") is True


def test_lost_process_without_finish_marker_stays_interrupted(tmp_path):
    folder = tmp_path / "job"
    job = _job(folder)
    _make_video(folder / "video.mp4")          # a file exists, but the log never printed the final video: line
    (folder / "make_video.log").write_text("scene 1\n", encoding="utf-8")
    ms._settle(job, None)
    assert job["state"] == "interrupted"


def test_lost_process_with_nothing_is_interrupted(tmp_path):
    folder = tmp_path / "job"
    job = _job(folder)
    ms._settle(job, None)
    assert job["state"] == "interrupted"
