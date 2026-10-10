"""Clean before/after files of one source clip through the face swap — no titles, no split gimmicks — with a JSON of
config, model versions, timings and per-frame identity next to each.

  python tools/trend_edit/make_sbs.py --source ref09-4s.mp4 --name her --swapped swap-gf.mp4 --work work-gf --id id-gf.json
         --refs a.jpg,b.jpg --out OUT_DIR

Left = the input of the run (unchanged), right = its result, same frame numbers, same size, 24 fps, no audio.
The JSON lists the frames whose ArcFace cosine to the person's photos is below `low_identity_cos` — failures are data.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import re
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "command-center"))
from bcc.direct_gen import faceswap  # noqa: E402


def sha1(path: Path, chunk: int = 1 << 20) -> str:
    h = hashlib.sha1()
    with open(path, "rb") as fh:
        while data := fh.read(chunk):
            h.update(data)
    return h.hexdigest()[:16]


def facefusion_version() -> str:
    meta = Path(faceswap.default_root()) / "facefusion" / "metadata.py"
    try:
        m = re.search(r"'version':\s*'([^']+)'", meta.read_text(encoding="utf-8"))
        return m.group(1) if m else "unknown"
    except OSError:
        return "unknown"


def main(argv=None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--source", type=Path, required=True)
    ap.add_argument("--swapped", type=Path, required=True)
    ap.add_argument("--name", required=True)
    ap.add_argument("--work", type=Path, required=True)
    ap.add_argument("--id", type=Path, required=True, dest="idjson")
    ap.add_argument("--refs", required=True)
    ap.add_argument("--out", type=Path, required=True)
    ap.add_argument("--low", type=float, default=0.5)
    a = ap.parse_args(argv)
    a.out.mkdir(parents=True, exist_ok=True)
    out = a.out / f"sbs-{a.name}.mp4"
    subprocess.run(["ffmpeg", "-v", "error", "-y", "-i", str(a.source), "-i", str(a.swapped), "-filter_complex",
                    "[0:v]scale=1000:-2[a];[1:v]scale=1000:-2[b];[a][b]hstack=inputs=2", "-an", "-c:v", "libx264", "-crf", "16",
                    "-preset", "slow", "-pix_fmt", "yuv420p", str(out)], check=True)
    ident = json.loads(a.idjson.read_text(encoding="utf-8"))
    run = json.loads((a.work / "run.json").read_text(encoding="utf-8"))
    pf = ident.get("per_frame", [])
    low = [i for i, v in enumerate(pf) if v is None or v < a.low]
    preset = faceswap.PRESETS["quality"]
    info = {
        "output": str(out), "source_clip": str(a.source), "source_sha1_16": sha1(a.source), "swapped_clip": str(a.swapped),
        "frames": ident.get("frames"), "fps": 24, "left": "input of the run (unchanged)", "right": "face swap result",
        "person": a.name, "reference_photos": a.refs.split(","),
        "engine": "FaceFusion", "facefusion_version": facefusion_version(),
        "preset": {"id": preset.id, "args": list(preset.args)} if hasattr(preset, "args") else {"id": "quality"},
        "extra_args": run.get("extra"), "face_selector": "reference, frame 0, distance 0.5 (heroine only)",
        "pose_gate": run.get("pose_gate"), "seed": None, "seed_note": "FaceFusion swap is deterministic given inputs; no seed",
        "timings_seconds": {"engine": run.get("engine_seconds"), "total": run.get("total_seconds")},
        "identity_arcface_cosine": {"mean": ident.get("identity_mean"), "min": ident.get("identity_min"),
                                    "std": ident.get("identity_std"), "frames_scored": ident.get("frames_scored")},
        "low_identity_cos": a.low, "low_identity_frames": low,
        "note": "frames in low_identity_frames are failures of the swap or of the face detector (hands over the face); "
                "they are shown here unedited and are not used in the promo",
    }
    (a.out / f"sbs-{a.name}.json").write_text(json.dumps(info, indent=1, ensure_ascii=False), encoding="utf-8")
    print(out, "low frames:", low)
    return 0


if __name__ == "__main__":
    sys.exit(main())
