"""Face swap of a clip through Bossman's own faceswap module (the same steps as DirectGenService._run_swap):
CFR normalize -> lossless chunks -> FaceFusion (preset quality: hyperswap_1a_256, pixel boost 1024, occlusion mask,
gfpgan_1.4 blend 25, thread count 1) -> join -> pose gate + hairline guard -> finish (H.264 crf 10).

Reference selector mode on the heroine only (the face of the FIRST frame of the clip is the one that is replaced):
  --reference 0 --distance 0.5     (one process, whole clip; reference mode takes the face from each process's own input)

  python tools/trend_edit/run_swap.py CLIP OUT.mp4 WORK FACE1,FACE2,... [--reference 0] [--distance 0.5]
"""
from __future__ import annotations

import argparse
import json
import subprocess
import sys
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "command-center"))
from bcc.direct_gen import faceswap  # noqa: E402


def main(argv=None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("video", type=Path)
    ap.add_argument("out", type=Path)
    ap.add_argument("work", type=Path)
    ap.add_argument("faces")
    ap.add_argument("--reference", type=int, default=None, help="reference frame number (heroine only mode)")
    ap.add_argument("--distance", type=float, default=0.5)
    ap.add_argument("--preset", default="quality")
    a = ap.parse_args(argv)
    faces = [Path(p) for p in a.faces.split(",")]
    work = a.work
    work.mkdir(parents=True, exist_ok=True)
    extra: list[str] = []
    if a.reference is not None:
        extra = ["--face-selector-mode", "reference", "--reference-frame-number", str(a.reference),
                 "--reference-face-distance", str(a.distance)]
    root = faceswap.default_root()
    t0 = time.time()
    log = {"video": str(a.video), "faces": [str(f) for f in faces], "preset": a.preset, "extra": extra}
    source = a.video
    cfr = work / "cfr.mp4"
    if faceswap.normalize_cfr(a.video, cfr):
        source, log["normalized_cfr"] = cfr, True
    frames = faceswap.frame_count(source)
    chunks = faceswap.plan_chunks(frames, 1 if extra else faceswap.DEFAULT_WORKERS)
    parts: list[tuple[Path, Path]] = []
    if len(chunks) == 1:
        parts = [(source, work / "swapped.mp4")]
    else:
        for i, (f0, f1) in enumerate(chunks):
            piece = work / f"in{i}.mp4"
            faceswap.cut_lossless(source, f0, f1, piece)
            parts.append((piece, work / f"out{i}.mp4"))

    def run(i: int) -> int:
        src, dst = parts[i]
        tmp, jobs = work / f"tmp{i}", work / f"jobs{i}"
        tmp.mkdir(exist_ok=True)
        jobs.mkdir(exist_ok=True)
        argv_ = faceswap.build_argv(root, faces, src, dst, a.preset, temp_path=tmp, jobs_path=jobs) + extra
        with open(work / f"ff{i}.log", "w", encoding="utf-8") as fh:
            return subprocess.run(argv_, cwd=root, stdout=fh, stderr=subprocess.STDOUT).returncode

    with ThreadPoolExecutor(len(parts)) as ex:
        codes = list(ex.map(run, range(len(parts))))
    log["engine_codes"], log["engine_seconds"] = codes, round(time.time() - t0, 1)
    if any(codes) or not all(d.is_file() for _, d in parts):
        print(json.dumps(log))
        return 1
    swapped = parts[0][1] if len(parts) == 1 else work / "swapped.mp4"
    if len(parts) > 1:
        faceswap.join([d for _, d in parts], swapped)
    gated, rep = work / "gated.mp4", work / "pose_gate.json"
    g = subprocess.run(faceswap.pose_gate_argv(root, source, swapped, gated, rep), cwd=root, capture_output=True, text=True)
    if g.returncode:
        print(g.stdout[-500:], g.stderr[-500:])
        return 2
    pg = json.loads(rep.read_text(encoding="utf-8"))
    log["pose_gate"] = {k: pg[k] for k in ("frames", "gated_frames", "gated_range") if k in pg}
    faceswap.finish(gated, a.video, a.out)
    log.update(frames=frames, chunks=len(parts), total_seconds=round(time.time() - t0, 1), out=str(a.out))
    (work / "run.json").write_text(json.dumps(log, indent=1), encoding="utf-8")
    print(json.dumps(log))
    return 0


if __name__ == "__main__":
    sys.exit(main())
