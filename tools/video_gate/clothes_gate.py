"""Stage 2 gate — clothes swap. Thresholds fixed in docs/owner/VIDEO_PIPELINE_STAGES_20261010.md before the first run (779cebc7).

  S2-T1 frames/alignment (as animation_gate v4)       S2-T2 outside the dilated clothes mask and the face: max|diff| == 0
  S2-T3 body pose (MediaPipe 11-32) / torso <= 0.03    S2-T4 face untouched: arcface(result face, source face) >= 0.90
  S2-T5 3 dominant Lab colours of the result clothes vs the reference outfit, mean dE <= 15
  S2-T6 flicker: mean frame-to-frame dE inside the clothes mask, result minus source <= 2.0

Run with the gate venv (opencv + mediapipe); the face phase is delegated to FaceFusion's interpreter like animation_gate:
  python clothes_gate.py --source src_dir_or_mp4 --result master.mp4 --holes hole_dir --masks mask_dir
      --ref-image outfit.png --ref-mask outfit_clothes.png --out DIR
"""
from __future__ import annotations

import argparse
import json
import subprocess
import sys
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parent))
import animation_gate as ag  # noqa: E402

T = {"align_share": 1.0, "outside_max_abs": 0, "body_max": 0.03, "face_sim_min": 0.90, "palette_de_max": 15.0,
     "flicker_de_max": 2.0}


def face_similarity_phase(src: Path, res: Path, out: Path) -> None:
    """FaceFusion interpreter: per frame cosine between the source face and the result face (same person matched by IoU)."""
    sys.path.insert(0, str(Path.cwd()))
    from facefusion import state_manager
    for k, v in {"execution_providers": ["directml"], "execution_device_ids": ["0"], "download_providers": ["github", "huggingface"],
                 "face_detector_model": "yolo_face", "face_detector_size": "640x640", "face_detector_angles": [0],
                 "face_detector_margin": [0, 0, 0, 0], "face_detector_score": 0.5, "face_landmarker_model": "2dfan4",
                 "face_landmarker_score": 0.5, "log_level": "error"}.items():
        state_manager.init_item(k, v)
    from facefusion.face_creator import get_many_faces

    def emb(f):
        e = getattr(f, "embedding_norm", None)
        return e if e is not None else f.embedding / np.linalg.norm(f.embedding)
    sims, boxes = [], []
    for s, r in zip(ag.read_frames(src), ag.read_frames(res)):
        fs = get_many_faces([s])
        if not fs:
            sims.append(float("nan"))
            boxes.append([])
            continue
        f = max(fs, key=lambda x: x.bounding_box[2] - x.bounding_box[0])
        m = ag.match_face(get_many_faces([r]), [float(v) for v in f.bounding_box])
        sims.append(float(np.dot(emb(f), emb(m))) if m is not None else float("nan"))
        boxes.append([[float(v) for v in x.bounding_box] for x in fs])
    out.write_text(json.dumps({"sims": sims, "boxes": boxes}), encoding="utf-8")


def palette(lab_pixels: np.ndarray, k: int = 3) -> np.ndarray:
    import cv2
    data = lab_pixels.reshape(-1, 3).astype(np.float32)
    if len(data) > 20000:
        data = data[np.random.default_rng(0).choice(len(data), 20000, replace=False)]
    _, labels, centers = cv2.kmeans(data, k, None, (cv2.TERM_CRITERIA_EPS + cv2.TERM_CRITERIA_MAX_ITER, 50, 0.5), 3,
                                    cv2.KMEANS_PP_CENTERS)
    order = np.argsort(-np.bincount(labels.ravel(), minlength=k))
    return centers[order]


def palette_de(a: np.ndarray, b: np.ndarray) -> float:
    """Mean dE of each colour of `a` to its nearest colour of `b` (order-free, symmetric average)."""
    d = np.linalg.norm(a[:, None, :] - b[None, :, :], axis=2)
    return float((d.min(1).mean() + d.min(0).mean()) / 2)


def to_lab(bgr):
    import cv2
    lab = cv2.cvtColor(bgr, cv2.COLOR_BGR2LAB).astype(np.float32)
    lab[..., 0] *= 100.0 / 255.0
    lab[..., 1:] -= 128.0
    return lab


def main(argv=None) -> int:
    import cv2
    ap = argparse.ArgumentParser()
    ap.add_argument("--source", required=True)
    ap.add_argument("--result", required=True)
    ap.add_argument("--holes", required=True)
    ap.add_argument("--masks", required=True)
    ap.add_argument("--ref-image", required=True)
    ap.add_argument("--ref-mask", required=True)
    ap.add_argument("--out", required=True)
    ap.add_argument("--face-phase", action="store_true", help=argparse.SUPPRESS)
    a = ap.parse_args(argv)
    out = Path(a.out)
    if a.face_phase:
        face_similarity_phase(Path(a.source), Path(a.result), out)
        return 0
    out.mkdir(parents=True, exist_ok=True)
    py = ag.FACEFUSION / ".venv" / "Scripts" / "python.exe"
    r = subprocess.run([str(py), str(Path(__file__).resolve()), "--face-phase", "--source", a.source, "--result", a.result,
                        "--holes", a.holes, "--masks", a.masks, "--ref-image", a.ref_image, "--ref-mask", a.ref_mask,
                        "--out", str(out / "faces.json")], cwd=ag.FACEFUSION, capture_output=True, text=True)
    if r.returncode:
        raise SystemExit("face phase failed: " + (r.stderr or r.stdout)[-600:])
    faces = json.loads((out / "faces.json").read_text(encoding="utf-8"))
    src, res = ag.read_frames(Path(a.source)), ag.read_frames(Path(a.result))
    n = min(len(src), len(res))
    holes = [cv2.imread(str(Path(a.holes) / f"{i + 1:04d}.png"), cv2.IMREAD_GRAYSCALE) > 127 for i in range(n)]
    masks = [cv2.imread(str(Path(a.masks) / f"{i + 1:04d}.png"), cv2.IMREAD_GRAYSCALE) > 127 for i in range(n)]
    rows, aligned, max_abs = [], 0, 0
    small = lambda f: cv2.resize(f, (f.shape[1] // 4, f.shape[0] // 4), interpolation=cv2.INTER_AREA).astype(np.float32)
    s4, r4 = [small(f) for f in src[:n]], [small(f) for f in res[:n]]
    for i in range(n):
        keep = ~holes[i] & ag.face_mask(src[i].shape[:2], faces["boxes"][i])
        k4 = cv2.resize(keep.astype(np.uint8), (s4[i].shape[1], s4[i].shape[0]), interpolation=cv2.INTER_NEAREST) > 0
        cand = {j: float(((r4[i] - s4[j]) ** 2)[k4].mean()) if k4.any() else 0.0 for j in range(max(0, i - 2), min(n, i + 3))}
        twin = {j: float(((s4[i] - s4[j]) ** 2)[k4].mean()) if k4.any() else 0.0 for j in cand}
        ok = ag.aligned_to_self(cand, i, twin)
        aligned += ok
        d = np.abs(src[i].astype(np.int16) - res[i].astype(np.int16))[keep]
        mx = int(d.max()) if d.size else 0
        max_abs = max(max_abs, mx)
        rows.append({"frame": i, "aligned": bool(ok), "max_abs_outside": mx, "face_sim": faces["sims"][i]})
    bs, br = ag.body_keypoints(src[:n]), ag.body_keypoints(res[:n])
    body = []
    for i in range(n):
        sp, rp = ag.pick_bodies(bs[i], br[i])
        if sp is None or rp is None:
            continue
        vis = (sp[:, 2] >= 0.5) & (rp[:, 2] >= 0.5)
        vis[:ag.BODY_FROM] = False
        if vis.sum() >= 4:
            body.append(float(np.linalg.norm(sp[vis, :2] - rp[vis, :2], axis=1).mean() / ag.body_scale(sp)))
    ref = cv2.imread(a.ref_image)
    ref_mask = cv2.imread(a.ref_mask, cv2.IMREAD_GRAYSCALE)
    ref_mask = cv2.resize(ref_mask, (ref.shape[1], ref.shape[0]), interpolation=cv2.INTER_NEAREST) > 127
    ref_pal = palette(to_lab(ref)[ref_mask])
    step = max(1, n // 10)
    pal = [palette_de(palette(to_lab(res[i])[masks[i]]), ref_pal) for i in range(0, n, step) if masks[i].sum() > 500]
    src_pal = [palette_de(palette(to_lab(src[i])[masks[i]]), ref_pal) for i in range(0, n, step) if masks[i].sum() > 500]
    flick_r, flick_s = [], []
    for i in range(1, n):
        m = masks[i] & masks[i - 1]
        if m.sum() > 500:
            flick_r.append(float(np.linalg.norm(to_lab(res[i])[m] - to_lab(res[i - 1])[m], axis=1).mean()))
            flick_s.append(float(np.linalg.norm(to_lab(src[i])[m] - to_lab(src[i - 1])[m], axis=1).mean()))
    sims = [s for s in faces["sims"][:n] if s == s]
    metrics = {"frames": [len(src), len(res)], "aligned_share": round(aligned / n, 4) if n else 0.0, "max_abs_outside": max_abs,
               "body_nme_mean": round(float(np.mean(body)), 4) if body else None, "bodies_measured": len(body),
               "face_sim_mean": round(float(np.mean(sims)), 4) if sims else None, "faces_measured": len(sims),
               "palette_de_result": round(float(np.mean(pal)), 2) if pal else None,
               "palette_de_source_for_reference": round(float(np.mean(src_pal)), 2) if src_pal else None,
               "flicker_de_result": round(float(np.mean(flick_r)), 3) if flick_r else None,
               "flicker_de_source": round(float(np.mean(flick_s)), 3) if flick_s else None}
    fl = (metrics["flicker_de_result"] - metrics["flicker_de_source"]) if flick_r else None
    checks = {"S2_T1_frames": len(src) == len(res), "S2_T1_alignment": metrics["aligned_share"] >= T["align_share"],
              "S2_T2_outside": max_abs <= T["outside_max_abs"],
              "S2_T3_body": metrics["body_nme_mean"] is not None and metrics["body_nme_mean"] <= T["body_max"],
              "S2_T4_face": metrics["face_sim_mean"] is not None and metrics["face_sim_mean"] >= T["face_sim_min"],
              "S2_T5_outfit": metrics["palette_de_result"] is not None and metrics["palette_de_result"] <= T["palette_de_max"],
              "S2_T6_flicker": fl is not None and fl <= T["flicker_de_max"]}
    report = {"gate": "stage2-clothes", "thresholds": T, "metrics": {**metrics, "flicker_added": None if fl is None else round(fl, 3)},
              "checks": checks, "verdict": "PASS" if all(checks.values()) else "FAIL", "per_frame": rows}
    (out / "report.json").write_text(json.dumps(report, ensure_ascii=False, indent=1), encoding="utf-8")
    ag.side_by_side(src[:n], res[:n], 30, out / "side_by_side.mp4")
    ag.contact_sheet(src[:n], res[:n], out / "contact_sheet.jpg")
    print(json.dumps({k: v for k, v in report.items() if k != "per_frame"}, ensure_ascii=False))
    return 0 if report["verdict"] == "PASS" else 1


if __name__ == "__main__":
    sys.exit(main())
