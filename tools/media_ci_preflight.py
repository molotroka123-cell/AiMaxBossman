#!/usr/bin/env python3
"""Require real FFmpeg AND ffprobe, encode/probe/decode a bounded local fixture.

Missing tools fail CI before pytest can silently skip renderer coverage. This is
media dependency evidence only, not whole-application or hardware acceptance.
"""
from __future__ import annotations
import argparse
import hashlib
import json
from pathlib import Path
import shutil
import subprocess
import tempfile


def preflight() -> dict:
    binaries = {name: shutil.which(name) for name in ("ffmpeg", "ffprobe")}
    missing = [name for name, value in binaries.items() if not value]
    if missing:
        raise RuntimeError("required media tools missing: " + ", ".join(missing))

    def run(argv):
        return subprocess.run(argv, check=True, stdout=subprocess.PIPE,
            stderr=subprocess.PIPE, timeout=30).stdout

    versions = {name: run([path, "-version"]).decode("utf-8", "replace").splitlines()[0]
                for name, path in binaries.items()}
    with tempfile.TemporaryDirectory(prefix="bossman-ffmpeg-check-") as directory:
        path = Path(directory) / "fixture.mp4"
        run([binaries["ffmpeg"], "-v", "error", "-nostdin", "-y", "-f", "lavfi", "-i",
             "color=blue:size=64x64:rate=25:duration=0.2", "-c:v", "libx264", "-pix_fmt", "yuv420p", str(path)])
        info = json.loads(run([binaries["ffprobe"], "-v", "error", "-count_frames", "-select_streams", "v:0",
            "-show_entries", "stream=codec_name,width,height,nb_read_frames", "-of", "json", str(path)]))
        streams = info.get("streams") or []
        if (len(streams) != 1 or streams[0].get("width") != 64 or streams[0].get("height") != 64
                or streams[0].get("nb_read_frames") != "5" or streams[0].get("codec_name") != "h264"):
            raise RuntimeError("media fixture post-state did not match requested encode")
        run([binaries["ffmpeg"], "-v", "error", "-xerror", "-nostdin", "-i", str(path), "-f", "null", "-"])
        return {"status": "PASS", "versions": versions, "streams": streams,
                "sha256": hashlib.sha256(path.read_bytes()).hexdigest(), "full_decode": True}


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()
    try:
        result = preflight()
    except (OSError, RuntimeError, ValueError, subprocess.SubprocessError) as exc:
        result = {"status": "FAIL", "reason": str(exc)}
    encoded = json.dumps(result, ensure_ascii=False, indent=2)
    print(encoded)
    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(encoded + "\n", encoding="utf-8")
    return 0 if result["status"] == "PASS" else 1


if __name__ == "__main__":
    raise SystemExit(main())
