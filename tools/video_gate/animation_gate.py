"""Animation gate (Gate 0 / Gate 1): does the result keep the source video's animation?

Thresholds are fixed in docs/owner/VIDEO_PIPELINE_STAGES_20261010.md (committed before this tool, a704eda9) and
copied into THRESHOLDS below; they are never chosen after looking at a result.

Two interpreters, one command:
  * the gate venv (opencv + mediapipe) runs everything: frames, alignment, pixels, optical flow, body keypoints, jitter,
    side-by-side video and contact sheet;
  * FaceFusion's interpreter is called once per clip for faces (yolo_face boxes, 2dfan4 68 landmarks, arcface).

  <gate-venv>/python animation_gate.py --gate 0 --source SRC --result RES --out DIR [--lossless]
  <gate-venv>/python animation_gate.py --gate 1 --source SRC --result RES --refs a.jpg,b.jpg --out DIR [--lossless]
SRC/RES: a video file or a folder of PNG frames. --lossless declares (before the run) that RES is a lossless file, so
T2 requires max|diff| == 0 outside the face mask; otherwise T2 is PSNR >= 40 dB outside the mask.
Metric version v2 (10.10): source twins are ties, faces/bodies are matched to the same person; thresholds unchanged.
Metric version v3 (10.10): T4 body uses Pose points 11-32 only — points 0-10 are the face, which Gate 1 changes on purpose
(owner spec: intentionally changed areas are not compared); the face has its own T3.
Metric version v4 (10.10): for Gate >= 1 the pose model sees both frames with the face mask greyed — on the 90-frame F1
clip the body area was pixel-identical (T2 = 0, flow 0.0075 px) yet MediaPipe moved the body points by 0.134 torso
because the face changed; v4 measures the body, not the detector's reaction to the face. Thresholds unchanged.
Body model: MediaPipe PoseLandmarker full (Apache-2.0), file from BOSSMAN_POSE_MODEL or <gate-venv>/models/.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import math
import os
import platform
import subprocess
import sys
import time
from pathlib import Path

import numpy as np

FACEFUSION = Path(os.environ.get("BOSSMAN_FACEFUSION_HOME", Path.home() / "Bossman" / "apps-local" / "facefusion"))
POSE_MODEL = Path(os.environ.get("BOSSMAN_POSE_MODEL", Path(sys.prefix) / "models" / "pose_landmarker_full.task"))
FACE_PAD = 0.6
BODY_FROM = 11            # MediaPipe Pose 0-10 = nose/eyes/ears/mouth; the body is 11 (shoulders) .. 32

THRESHOLDS = {
    0: {"align_share": 1.0, "fps_tol": 0.01, "lossless_max_abs": 0, "psnr_min_db": 40.0, "face_nme_max": 0.02,
        "body_max": 0.02, "flow_epe_max_px": 0.25, "jitter_max": 0.01},
    1: {"align_share": 1.0, "fps_tol": 0.01, "lossless_max_abs": 0, "psnr_min_db": 40.0, "face_nme_max": 0.05,
        "body_max": 0.02, "flow_epe_max_px": 0.25, "jitter_max": 0.02,
        "identity_mean_min": 0.75, "identity_std_max": 0.06, "source_identity_max": 0.30},
}


# ---------------------------------------------------------------- frames
def probe(path: Path) -> dict:
    if path.is_dir():
        return {"kind": "png_folder", "fps": None}
    r = subprocess.run(["ffprobe", "-v", "error", "-select_streams", "v:0", "-show_entries",
                        "stream=width,height,r_frame_rate,codec_name,pix_fmt:format=duration", "-of", "json", str(path)],
                       capture_output=True, text=True)
    d = json.loads(r.stdout or "{}")
    s, f = d["streams"][0], d.get("format", {})
    num, _, den = s["r_frame_rate"].partition("/")
    return {"kind": "video", "width": s["width"], "height": s["height"], "fps": float(num) / float(den or 1),
            "codec": s.get("codec_name"), "pix_fmt": s.get("pix_fmt"), "duration": float(f.get("duration") or 0)}


def read_frames(path: Path) -> list:
    """bgr24 frames; videos are decoded by ffmpeg (OpenCV mis-decodes yuvj444p)."""
    import cv2
    if path.is_dir():
        return [cv2.imread(str(p)) for p in sorted(path.glob("*.png"))]
    info = probe(path)
    w, h = info["width"], info["height"]
    raw = subprocess.run(["ffmpeg", "-v", "error", "-i", str(path), "-f", "rawvideo", "-pix_fmt", "bgr24", "-"],
                         capture_output=True).stdout
    n = len(raw) // (w * h * 3)
    return list(np.frombuffer(raw[: n * w * h * 3], np.uint8).reshape(n, h, w, 3))


def sha256_of(path: Path) -> str:
    h = hashlib.sha256()
    for f in (sorted(path.glob("*.png")) if path.is_dir() else [path]):
        h.update(f.read_bytes())
    return h.hexdigest()


# ---------------------------------------------------------------- faces (FaceFusion interpreter)
def face_phase(src: Path, res: Path, refs: list[str], out: Path) -> None:
    sys.path.insert(0, str(Path.cwd()))
    from facefusion import state_manager
    for k, v in {"execution_providers": ["directml"], "execution_device_ids": ["0"],
                 "download_providers": ["github", "huggingface"], "face_detector_model": "yolo_face",
                 "face_detector_size": "640x640", "face_detector_angles": [0], "face_detector_margin": [0, 0, 0, 0],
                 "face_detector_score": 0.5, "face_landmarker_model": "2dfan4", "face_landmarker_score": 0.5,
                 "log_level": "error"}.items():
        state_manager.init_item(k, v)
    import cv2
    from facefusion.face_creator import get_many_faces

    def emb(face):
        e = getattr(face, "embedding_norm", None)
        return e if e is not None else face.embedding / np.linalg.norm(face.embedding)

    def largest(fs):
        return max(fs, key=lambda f: f.bounding_box[2] - f.bounding_box[0]) if fs else None

    ref = None
    if refs:
        ref = np.mean([emb(largest(get_many_faces([cv2.imread(p)]))) for p in refs], axis=0)
        ref /= np.linalg.norm(ref)
    data, chosen = {}, []
    for tag, path in (("src", src), ("res", res)):
        lms, boxes, ids = [], [], []
        for k, frame in enumerate(read_frames(path)):
            fs = get_many_faces([frame])
            if tag == "src":
                f = largest(fs)
                chosen.append(None if f is None else [float(v) for v in f.bounding_box])
            else:                      # v2: the SAME person as in the source frame, not the result's own largest face
                f = match_face(fs, chosen[k] if k < len(chosen) else None)
            lms.append(np.asarray(f.landmark_set["68"], np.float32) if f is not None else np.full((68, 2), np.nan, np.float32))
            boxes.append([[float(v) for v in x.bounding_box] for x in fs])
            ids.append(float(np.dot(emb(f), ref)) if (f is not None and ref is not None) else float("nan"))
        data[tag] = {"lm": np.stack(lms) if lms else np.zeros((0, 68, 2), np.float32), "boxes": boxes, "id": ids}
    np.savez(out.with_suffix(".npz"), src_lm=data["src"]["lm"], res_lm=data["res"]["lm"],
             src_id=np.array(data["src"]["id"]), res_id=np.array(data["res"]["id"]))
    out.with_suffix(".boxes.json").write_text(json.dumps({"src": data["src"]["boxes"], "res": data["res"]["boxes"]}))


def iou(a, b) -> float:
    x1, y1, x2, y2 = max(a[0], b[0]), max(a[1], b[1]), min(a[2], b[2]), min(a[3], b[3])
    inter = max(0.0, x2 - x1) * max(0.0, y2 - y1)
    union = (a[2] - a[0]) * (a[3] - a[1]) + (b[2] - b[0]) * (b[3] - b[1]) - inter
    return inter / union if union > 0 else 0.0


def match_face(faces, box, min_iou: float = 0.3):
    """The face overlapping the source's chosen face box; None when that person is not found (counted as missing)."""
    if box is None or not faces:
        return None
    best = max(faces, key=lambda f: iou([float(v) for v in f.bounding_box], box))
    return best if iou([float(v) for v in best.bounding_box], box) >= min_iou else None


def run_face_phase(src: Path, res: Path, refs: list[str], out: Path) -> dict:
    py = FACEFUSION / ".venv" / "Scripts" / "python.exe"
    if not py.exists():
        py = FACEFUSION / ".venv" / "bin" / "python"
    r = subprocess.run([str(py), str(Path(__file__).resolve()), "--face-phase", "--source", str(src), "--result", str(res),
                        "--refs", ",".join(refs), "--out", str(out)], cwd=FACEFUSION, capture_output=True, text=True)
    if r.returncode != 0 or not out.with_suffix(".npz").exists():
        raise RuntimeError("face phase failed: " + (r.stderr or r.stdout)[-800:])
    z = np.load(out.with_suffix(".npz"))
    boxes = json.loads(out.with_suffix(".boxes.json").read_text())
    return {"src_lm": z["src_lm"], "res_lm": z["res_lm"], "src_id": z["src_id"], "res_id": z["res_id"], "boxes": boxes}


# ---------------------------------------------------------------- measures
def face_mask(shape, boxes) -> np.ndarray:
    """True = must be unchanged: everything outside the face boxes padded by FACE_PAD x their side."""
    h, w = shape
    keep = np.ones((h, w), bool)
    for x1, y1, x2, y2 in boxes:
        pad = FACE_PAD * max(x2 - x1, y2 - y1)
        keep[max(0, int(y1 - pad)):max(0, int(y2 + pad)), max(0, int(x1 - pad)):max(0, int(x2 + pad))] = False
    return keep


def iod(lm) -> float:
    return float(np.linalg.norm(lm[36:42].mean(0) - lm[42:48].mean(0))) or 1.0


def body_keypoints(frames) -> list:
    import mediapipe as mp
    from mediapipe.tasks.python import BaseOptions, vision
    opts = vision.PoseLandmarkerOptions(base_options=BaseOptions(model_asset_path=str(POSE_MODEL)),
                                        running_mode=vision.RunningMode.IMAGE, num_poses=2)
    out = []
    with vision.PoseLandmarker.create_from_options(opts) as model:
        for f in frames:
            r = model.detect(mp.Image(image_format=mp.ImageFormat.SRGB, data=np.ascontiguousarray(f[:, :, ::-1])))
            if not r.pose_landmarks:
                out.append([])
                continue
            h, w = f.shape[:2]
            out.append([np.array([(p.x * w, p.y * h, p.visibility or 0.0) for p in pose], np.float32) for pose in r.pose_landmarks])
    return out


def pick_bodies(src_poses, res_poses):
    """v2: the source's largest person (shoulder width) and the result pose closest to it — the same person."""
    if not src_poses or not res_poses:
        return None, None
    s = max(src_poses, key=lambda k: float(np.linalg.norm(k[11, :2] - k[12, :2])))
    r = min(res_poses, key=lambda k: float(np.linalg.norm(k[:, :2] - s[:, :2], axis=1).mean()))
    return s, r


def body_scale(kp) -> float:
    shoulders = (kp[11, :2] + kp[12, :2]) / 2
    if min(kp[23, 2], kp[24, 2]) >= 0.5:
        return float(np.linalg.norm(shoulders - (kp[23, :2] + kp[24, :2]) / 2)) or 1.0
    return float(np.linalg.norm(kp[11, :2] - kp[12, :2])) or 1.0


def jitter_rms(dev: list) -> list[float]:
    """Per-frame RMS of the second temporal difference of the landmark deviation (result - source) / IOD."""
    return [float(np.sqrt((((dev[i + 1] - 2 * dev[i] + dev[i - 1]) ** 2).sum(1)).mean()))
            for i in range(1, len(dev) - 1) if dev[i - 1] is not None and dev[i] is not None and dev[i + 1] is not None]


def aligned_to_self(cand: dict[int, float], i: int, twin: dict[int, float] | None = None) -> bool:
    """Result frame i is aligned when source frame i is (one of) the closest among i-2..i+2.
    v2 (10.10, after v1 FAILed on ref-clip-01): a source with 24->30 telecine has near-identical neighbours (MSE 0.01-0.03
    vs median 190); when the closest source frame j is itself indistinguishable from source frame i (twin[j] = MSE(s_i, s_j)
    <= the result's own noise cand[i]), picking j is a tie, not a shift."""
    best = min(cand, key=cand.get)
    if cand[i] <= cand[best] * 1.0001 + 1e-6:
        return True
    return twin is not None and twin.get(best, float("inf")) <= cand[i]


def measure(src, res, faces, gate: int, lossless: bool, src_info, res_info, body=True) -> tuple[dict, dict, list]:
    import cv2
    t = THRESHOLDS[gate]
    n = min(len(src), len(res))
    masks = [face_mask(src[i].shape[:2], faces["boxes"]["src"][i] + faces["boxes"]["res"][i]) if gate >= 1
             else np.ones(src[i].shape[:2], bool) for i in range(n)]
    small = lambda f: cv2.resize(f, (f.shape[1] // 4, f.shape[0] // 4), interpolation=cv2.INTER_AREA).astype(np.float32)
    s4, r4 = [small(f) for f in src], [small(f) for f in res[:n]]
    aligned, rows = 0, []
    for i in range(n):
        m4 = cv2.resize(masks[i].astype(np.uint8), (s4[i].shape[1], s4[i].shape[0]), interpolation=cv2.INTER_NEAREST) > 0
        cand = {j: float(((r4[i] - s4[j]) ** 2)[m4].mean()) if m4.any() else 0.0
                for j in range(max(0, i - 2), min(len(src), i + 3))}
        twin = {j: float(((s4[i] - s4[j]) ** 2)[m4].mean()) if m4.any() else 0.0 for j in cand}
        ok = aligned_to_self(cand, i, twin)
        aligned += ok
        d = np.abs(src[i].astype(np.int16) - res[i].astype(np.int16))[masks[i]]
        mse = float((d.astype(np.float32) ** 2).mean()) if d.size else 0.0
        rows.append({"frame": i, "aligned": bool(ok), "max_abs_outside": int(d.max()) if d.size else 0,
                     "psnr_outside_db": 99.0 if mse == 0 else round(10 * math.log10(255 ** 2 / mse), 2)})
    sl, rl = faces["src_lm"][:n], faces["res_lm"][:n]
    nme, dev = [], []
    for i in range(n):
        if i >= len(sl) or i >= len(rl) or np.isnan(sl[i]).any() or np.isnan(rl[i]).any():
            dev.append(None)
            continue
        k = iod(sl[i])
        nme.append(float(np.linalg.norm(sl[i] - rl[i], axis=1).mean() / k))
        rows[i]["face_nme"] = round(nme[-1], 4)
        dev.append((rl[i] - sl[i]) / k)
    jit = jitter_rms(dev)
    bodies = []
    if body:
        if gate >= 1:   # v4: the intentionally changed face area is greyed in BOTH frames before pose estimation
            grey = lambda f, keep: np.where(keep[..., None], f, np.uint8(127))
            bs = body_keypoints([grey(src[i], masks[i]) for i in range(n)])
            br = body_keypoints([grey(res[i], masks[i]) for i in range(n)])
        else:
            bs, br = body_keypoints(src[:n]), body_keypoints(res[:n])
        for i in range(n):
            sp, rp = pick_bodies(bs[i], br[i])
            if sp is None or rp is None:
                continue
            vis = (sp[:, 2] >= 0.5) & (rp[:, 2] >= 0.5)
            vis[:BODY_FROM] = False            # v3: face points belong to T3 (intentionally changed in Gate 1)
            if vis.sum() < 4:
                continue
            bodies.append(float(np.linalg.norm(sp[vis, :2] - rp[vis, :2], axis=1).mean() / body_scale(sp)))
            rows[i]["body_nme"] = round(bodies[-1], 4)
    half = lambda f: cv2.cvtColor(cv2.resize(f, (f.shape[1] // 2, f.shape[0] // 2), interpolation=cv2.INTER_AREA),
                                  cv2.COLOR_BGR2GRAY)
    sh, rh = [half(f) for f in src[:n]], [half(f) for f in res[:n]]
    epe = []
    for i in range(n - 1):
        fs = cv2.calcOpticalFlowFarneback(sh[i], sh[i + 1], None, 0.5, 3, 15, 3, 5, 1.2, 0)
        fr = cv2.calcOpticalFlowFarneback(rh[i], rh[i + 1], None, 0.5, 3, 15, 3, 5, 1.2, 0)
        m = cv2.resize((masks[i] & masks[i + 1]).astype(np.uint8), (fs.shape[1], fs.shape[0]),
                       interpolation=cv2.INTER_NEAREST) > 0
        if m.any():
            epe.append(float(np.linalg.norm(fs - fr, axis=2)[m].mean()))
            rows[i]["flow_epe_px"] = round(epe[-1], 4)
    sf, rf = src_info.get("fps"), res_info.get("fps")
    fps_ok = (sf is None and rf is None) or (sf is not None and rf is not None and abs(sf - rf) <= t["fps_tol"])
    dur_ok = True
    if src_info.get("duration") and res_info.get("duration") and sf:
        dur_ok = abs(src_info["duration"] - res_info["duration"]) <= 1.0 / sf + 1e-6
    max_abs = max(r["max_abs_outside"] for r in rows) if rows else None
    min_psnr = min(r["psnr_outside_db"] for r in rows) if rows else None
    metrics = {"frames": [len(src), len(res)], "aligned_share": round(aligned / n, 4) if n else 0.0,
               "max_abs_outside": max_abs, "min_psnr_outside_db": min_psnr,
               "face_nme_mean": round(float(np.mean(nme)), 4) if nme else None, "faces_measured": len(nme),
               "body_nme_mean": round(float(np.mean(bodies)), 4) if bodies else None, "bodies_measured": len(bodies),
               "flow_epe_mean_px": round(float(np.mean(epe)), 4) if epe else None,
               "jitter_rms": round(float(np.sqrt(np.mean(np.square(jit)))), 4) if jit else None}
    checks = {
        "T1_frame_count": len(src) == len(res), "T1_fps": bool(fps_ok), "T1_duration": bool(dur_ok),
        "T1_alignment": metrics["aligned_share"] >= t["align_share"],
        "T2_pixels": (max_abs == t["lossless_max_abs"]) if lossless else (min_psnr is not None and min_psnr >= t["psnr_min_db"]),
        "T3_face": metrics["face_nme_mean"] is not None and metrics["face_nme_mean"] <= t["face_nme_max"],
        "T4_body": metrics["body_nme_mean"] is not None and metrics["body_nme_mean"] <= t["body_max"],
        "T5_flow": metrics["flow_epe_mean_px"] is not None and metrics["flow_epe_mean_px"] <= t["flow_epe_max_px"],
        "T6_jitter": metrics["jitter_rms"] is not None and metrics["jitter_rms"] <= t["jitter_max"],
    }
    if gate == 1:
        ids = [float(v) for v in faces["res_id"][:n] if not np.isnan(v)]
        sids = [float(v) for v in faces["src_id"][:n] if not np.isnan(v)]
        metrics.update(identity_mean=round(float(np.mean(ids)), 4) if ids else None,
                       identity_std=round(float(np.std(ids)), 4) if ids else None,
                       source_identity_mean=round(float(np.mean(sids)), 4) if sids else None)
        checks.update(T7_identity_mean=bool(ids) and metrics["identity_mean"] >= t["identity_mean_min"],
                      T7_identity_std=bool(ids) and metrics["identity_std"] <= t["identity_std_max"],
                      T7_source_identity=bool(sids) and metrics["source_identity_mean"] <= t["source_identity_max"])
    return metrics, checks, rows


# ---------------------------------------------------------------- visual evidence
def diff_heat(s, r):
    import cv2
    d = np.abs(s.astype(np.int16) - r.astype(np.int16)).max(axis=2)
    return cv2.applyColorMap(np.clip(d * 4, 0, 255).astype(np.uint8), cv2.COLORMAP_INFERNO)


def side_by_side(src, res, fps, path: Path, height: int = 480) -> None:
    import cv2
    w = int(src[0].shape[1] * height / src[0].shape[0]) // 2 * 2
    enc = subprocess.Popen(["ffmpeg", "-v", "error", "-y", "-f", "rawvideo", "-pix_fmt", "bgr24", "-s", f"{w * 3}x{height}",
                            "-r", str(fps or 30), "-i", "-", "-c:v", "libx264", "-crf", "18", "-pix_fmt", "yuv420p", str(path)],
                           stdin=subprocess.PIPE)
    for s, r in zip(src, res):
        row = np.hstack([cv2.resize(x, (w, height), interpolation=cv2.INTER_AREA) for x in (s, r, diff_heat(s, r))])
        enc.stdin.write(np.ascontiguousarray(row).tobytes())
    enc.stdin.close()
    enc.wait()


def contact_sheet(src, res, path: Path, count: int = 8, width: int = 240) -> list[int]:
    import cv2
    n = min(len(src), len(res))
    idx = sorted({int(round(i * (n - 1) / (count - 1))) for i in range(count)}) if n > 1 else [0]
    cols = []
    for i in idx:
        h = int(src[i].shape[0] * width / src[i].shape[1])
        col = np.vstack([cv2.resize(x, (width, h), interpolation=cv2.INTER_AREA) for x in (src[i], res[i], diff_heat(src[i], res[i]))])
        cv2.putText(col, str(i), (6, 22), cv2.FONT_HERSHEY_SIMPLEX, 0.7, (255, 255, 255), 2)
        cols.append(col)
    cv2.imwrite(str(path), np.hstack(cols), [cv2.IMWRITE_JPEG_QUALITY, 92])
    return idx


def versions() -> dict:
    import cv2
    import mediapipe as mp
    ff = subprocess.run(["git", "-C", str(FACEFUSION), "rev-parse", "--short", "HEAD"], capture_output=True, text=True).stdout.strip()
    fv = subprocess.run(["ffmpeg", "-version"], capture_output=True, text=True).stdout.split("\n")[0]
    return {"python": platform.python_version(), "opencv": cv2.__version__, "mediapipe": mp.__version__,
            "numpy": np.__version__, "facefusion": ff or "unknown", "ffmpeg": fv, "os": platform.platform(),
            "face_models": "yolo_face 640 / 2dfan4 / arcface_w600k_r50 (FaceFusion)",
            "body_model": f"MediaPipe PoseLandmarker full ({POSE_MODEL.name}, sha256 {sha256_of(POSE_MODEL)[:12]})"}


def main(argv=None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--gate", type=int, choices=(0, 1), default=0)
    ap.add_argument("--source", required=True)
    ap.add_argument("--result", required=True)
    ap.add_argument("--refs", default="")
    ap.add_argument("--out", required=True)
    ap.add_argument("--lossless", action="store_true")
    ap.add_argument("--face-phase", action="store_true", help=argparse.SUPPRESS)
    a = ap.parse_args(argv)
    refs = [p for p in a.refs.split(",") if p]
    if a.face_phase:
        face_phase(Path(a.source), Path(a.result), refs, Path(a.out))
        return 0
    if a.gate == 1 and not refs:
        ap.error("--gate 1 needs --refs")
    t0 = time.time()
    src_p, res_p, out = Path(a.source), Path(a.result), Path(a.out)
    out.mkdir(parents=True, exist_ok=True)
    src_info, res_info = probe(src_p), probe(res_p)
    faces = run_face_phase(src_p, res_p, refs, out / "faces")
    src, res = read_frames(src_p), read_frames(res_p)
    metrics, checks, rows = measure(src, res, faces, a.gate, a.lossless, src_info, res_info)
    side_by_side(src, res, src_info.get("fps"), out / "side_by_side.mp4")
    control = contact_sheet(src, res, out / "contact_sheet.jpg")
    report = {"gate": a.gate, "metric_version": "v4", "thresholds": THRESHOLDS[a.gate], "lossless_declared": a.lossless,
              "command": [Path(sys.executable).name, Path(__file__).name, *(argv if argv is not None else sys.argv[1:])],
              "versions": versions(), "source": {**src_info, "sha256": sha256_of(src_p)},
              "result": {**res_info, "sha256": sha256_of(res_p)}, "metrics": metrics, "checks": checks,
              "verdict": "PASS" if all(checks.values()) else "FAIL", "control_frames": control,
              "seconds": round(time.time() - t0, 1), "per_frame": rows}
    (out / "report.json").write_text(json.dumps(report, ensure_ascii=False, indent=1), encoding="utf-8")
    print(json.dumps({k: v for k, v in report.items() if k != "per_frame"}, ensure_ascii=False))
    return 0 if report["verdict"] == "PASS" else 1


if __name__ == "__main__":
    sys.exit(main())
