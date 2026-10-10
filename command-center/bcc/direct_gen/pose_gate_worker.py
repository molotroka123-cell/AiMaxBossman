"""Pose gate for the video face swap: keep the source face where the head is bowed toward the camera.

FaceFusion's detector stays confident on a bowed head (10.10, two-person clip, 10.1-11.5 s: detector 0.78-0.80) and
pastes a frontal face onto the crown — eyes and brows appear on the forehead. The head pitch is what separates those
frames: pitch = (nose_y - eyes_y) / (chin_y - eyes_y) on the 68 landmarks of the SOURCE frame's largest face, measured
0.54-0.62 bowed vs 0.32-0.46 normal. Per frame:
    alpha = clip((off - pitch) / (off - on), 0, 1);  out = alpha * swapped + (1 - alpha) * source
so the swap fades out/in over the on..off band instead of popping. Frames without a face keep the swapped frame.
Then everything outside the padded face boxes is restored from the source exactly (keep_source_outside_faces).

Run ONLY with FaceFusion's interpreter (it imports `facefusion`, not `bcc`), cwd = the FaceFusion checkout:
    <facefusion>/.venv/Scripts/python.exe pose_gate_worker.py source.mp4 swapped.mp4 out.mp4 report.json [on off]
Writes out.mp4 (libx264rgb -qp 0: RGB-lossless) and report.json {frames, gated_frames, gated_range, rows:[{frame,pitch,alpha}]}.
"""
from __future__ import annotations

import json
import shutil
import subprocess
import sys
from pathlib import Path

PITCH_ON, PITCH_OFF = 0.50, 0.56


def pose_alpha(pitch: float | None, on: float = PITCH_ON, off: float = PITCH_OFF) -> float:
    """1.0 = keep the swap, 0.0 = keep the source; linear in between."""
    if pitch is None:
        return 1.0
    return min(1.0, max(0.0, (off - pitch) / (off - on)))


def pitch_of(landmarks68) -> float:
    eyes = (landmarks68[36:42].mean(0) + landmarks68[42:48].mean(0)) / 2
    return float((landmarks68[30][1] - eyes[1]) / max(1e-3, landmarks68[8][1] - eyes[1]))


#: Gate 1 (10.10): with the face box padded by 0.6 x its side, 0 of 90 frames changed a pixel outside it (frame path).
FACE_PAD = 0.6
FEATHER = 0.15


def face_regions_mask(shape: tuple[int, int], boxes, pad: float = FACE_PAD, feather: float = FEATHER):
    """Float mask 1 inside every padded face box, 0 outside, linear ramp of `feather` x box side at the inner edge."""
    import numpy as np
    h, w = shape
    mask = np.zeros((h, w), np.float32)
    ys, xs = np.arange(h, dtype=np.float32)[:, None], np.arange(w, dtype=np.float32)[None, :]
    for x1, y1, x2, y2 in boxes:
        side = max(x2 - x1, y2 - y1)
        p, f = pad * side, max(1.0, feather * side)
        bx1, by1, bx2, by2 = x1 - p, y1 - p, x2 + p, y2 + p
        inside = np.minimum(np.minimum(xs - bx1, bx2 - xs), np.minimum(ys - by1, by2 - ys))
        mask = np.maximum(mask, np.clip(inside / f, 0.0, 1.0))
    return mask


def keep_source_outside_faces(source, processed, boxes):
    """FaceFusion's video path re-ranges colours of the WHOLE frame (10.10: mean 45.39 -> 42.93); everything outside the
    padded face boxes is restored from the source exactly, so only the face area carries the engine's output."""
    import numpy as np
    if not boxes:
        return processed
    m = face_regions_mask(source.shape[:2], boxes)[..., None]
    out = np.where(m >= 1.0, processed, source)
    edge = (m > 0) & (m < 1)
    if edge.any():
        blend = (m * processed.astype(np.float32) + (1 - m) * source.astype(np.float32)).round().astype(np.uint8)
        out = np.where(edge, blend, out)
    return out


def _probe(path: str) -> tuple[int, int, str]:
    out = subprocess.run([shutil.which("ffprobe") or "ffprobe", "-v", "error", "-select_streams", "v:0", "-show_entries",
                          "stream=width,height,r_frame_rate", "-of", "csv=p=0", path], capture_output=True, text=True)
    w, h, rate = out.stdout.strip().split(",")[:3]
    return int(w), int(h), rate


def _frames(path: str, w: int, h: int):
    import numpy as np
    p = subprocess.Popen([shutil.which("ffmpeg") or "ffmpeg", "-v", "error", "-i", path, "-f", "rawvideo", "-pix_fmt", "bgr24", "-"],
                         stdout=subprocess.PIPE)
    size = w * h * 3
    while True:
        b = p.stdout.read(size)
        if len(b) < size:
            p.wait()
            return
        yield np.frombuffer(b, np.uint8).reshape(h, w, 3)


def main(argv: list[str]) -> int:
    src, swp, dst, rep = argv[:4]
    on, off = (float(argv[4]), float(argv[5])) if len(argv) >= 6 else (PITCH_ON, PITCH_OFF)
    import numpy as np
    sys.path.insert(0, str(Path.cwd()))
    from facefusion import state_manager
    for k, v in {"execution_providers": ["directml"], "execution_device_ids": ["0"], "download_providers": ["github", "huggingface"],
                 "face_detector_model": "yolo_face", "face_detector_size": "640x640", "face_detector_angles": [0],
                 "face_detector_margin": [0, 0, 0, 0], "face_detector_score": 0.5, "face_landmarker_model": "2dfan4",
                 "face_landmarker_score": 0.5, "log_level": "error"}.items():
        state_manager.init_item(k, v)
    from facefusion.face_creator import get_many_faces

    w, h, rate = _probe(src)
    enc = subprocess.Popen([shutil.which("ffmpeg") or "ffmpeg", "-v", "error", "-y", "-f", "rawvideo", "-pix_fmt", "bgr24",
                            "-s", f"{w}x{h}", "-r", rate, "-i", "-", "-c:v", "libx264rgb", "-qp", "0", "-preset", "ultrafast", dst],
                           stdin=subprocess.PIPE)
    rows = []
    for i, (s, r) in enumerate(zip(_frames(src, w, h), _frames(swp, w, h))):
        faces = get_many_faces([s])
        p = pitch_of(max(faces, key=lambda f: f.bounding_box[2] - f.bounding_box[0]).landmark_set["68"]) if faces else None
        a = pose_alpha(p, on, off)
        o = r if a == 1.0 else s if a == 0.0 else (a * r.astype(np.float32) + (1 - a) * s.astype(np.float32)).round().astype(np.uint8)
        o = keep_source_outside_faces(s, o, [tuple(float(v) for v in f.bounding_box) for f in faces])
        enc.stdin.write(np.ascontiguousarray(o).tobytes())
        rows.append({"frame": i, "pitch": None if p is None else round(p, 3), "alpha": round(a, 3)})
    enc.stdin.close()
    if enc.wait() != 0 or not rows:
        print(json.dumps({"error": "encode_failed" if rows else "no_frames"}))
        return 2
    gated = [r["frame"] for r in rows if r["alpha"] < 1]
    report = {"pitch_on": on, "pitch_off": off, "frames": len(rows), "gated_frames": len(gated),
              "gated_range": [gated[0], gated[-1]] if gated else None}
    Path(rep).write_text(json.dumps({**report, "rows": rows}, indent=1), encoding="utf-8")
    print(json.dumps(report))
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
