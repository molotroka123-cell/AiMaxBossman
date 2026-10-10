"""Gate 0 input: run the Bossman face-swap pipeline with NO effect (FaceFusion replaced by an identity copy).

Same functions and order as DirectGenService._run_swap: CFR normalize -> lossless chunks -> (identity) -> join ->
pose gate worker (with swapped == source) -> finish (source audio). Writes both the lossless intermediate
(<out>/lossless.mp4, the pose gate output) and the delivered file (<out>/final.mp4); animation_gate.py then measures
lossless.mp4 with --lossless (max|diff| must be 0) and final.mp4 with the PSNR threshold.

  <bcc venv>/python tools/video_gate/gate0_passthrough.py <video> <out_dir>
"""
from __future__ import annotations

import json
import subprocess
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "command-center"))
from bcc.direct_gen import faceswap  # noqa: E402


def main(video: Path, out: Path) -> dict:
    out.mkdir(parents=True, exist_ok=True)
    t0 = time.time()
    log: dict = {"video": str(video)}
    source = video
    if faceswap.normalize_cfr(video, out / "cfr.mp4"):
        source, log["normalized_cfr"] = out / "cfr.mp4", True
    frames = faceswap.frame_count(source)
    chunks = faceswap.plan_chunks(frames, faceswap.DEFAULT_WORKERS)
    log.update(frames=frames, chunks=chunks)
    if len(chunks) == 1:
        swapped = out / "identity.mp4"
        faceswap.cut_lossless(source, 0, max(frames - 1, 0), swapped)
    else:
        parts = []
        for i, (a, b) in enumerate(chunks):
            parts.append(out / f"in{i}.mp4")
            faceswap.cut_lossless(source, a, b, parts[-1])        # identity: the "processed" chunk is the chunk
        swapped = out / "joined.mp4"
        faceswap.join(parts, swapped)
    root = faceswap.default_root()
    gated, rep = out / "lossless.mp4", out / "pose_gate.json"
    g = subprocess.run(faceswap.pose_gate_argv(root, source, swapped, gated, rep), cwd=root, capture_output=True, text=True)
    if g.returncode:
        raise RuntimeError("pose gate failed: " + (g.stdout + g.stderr)[-600:])
    faceswap.finish(gated, video, out / "final.mp4")
    log.update(pose_gate={k: v for k, v in json.loads(rep.read_text(encoding="utf-8")).items() if k != "rows"},
               seconds=round(time.time() - t0, 1), reference=str(source))
    (out / "pipeline.json").write_text(json.dumps(log, indent=1), encoding="utf-8")
    return log


if __name__ == "__main__":
    print(json.dumps(main(Path(sys.argv[1]), Path(sys.argv[2]))))
