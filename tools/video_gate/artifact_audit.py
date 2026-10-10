"""Frame-by-frame artifact audit of a face-swapped video against its source (owner 10.10: «покадрово все разбери»).

Per frame, on the SWAPPED face (the source face whose area changed most in the result):
  identity      arcface cosine of the result face to the mean of the reference photos (per frame range when a clip was
                split into scenes with different people);
  jitter        RMS second time-difference of (result - source) 68 landmarks / inter-ocular distance (as Gate 1 T6);
  flicker       mean dE76 frame->frame inside the face hull, result minus source (motion is in both, the swap's own
                shimmer is the excess);
  seam          mean |grad (L_result - L_source)| on the boundary band of the changed area, face crop scaled to 256 px
                (a hard composite edge is a step in the difference image; a soft one is a ramp);
  bg_shift      mean |dE| of result vs source in the face crop OUTSIDE the changed area (should be 0: colour of the
                engine's whole-frame merge leaking into the restored padded box);
  colour        dE76 between (face skin - neck) in the result and the same contrast in the source (face/neck mismatch);
  swapped       share of the face hull that changed (dE > 4): ~0 means the original face shows;
  alpha         pose-gate alpha from the pose gate report (when given).
Worst segments: fixed reference scales (below, chosen before any result) turn each metric into a 0..n score; windows of
0.5 s with the highest summed score, non-overlapping. Crops for independent review: 4 frames of a window, face area only,
2x2 grid, 384 px max side.

Run with FaceFusion's interpreter, cwd = the FaceFusion checkout:
  python artifact_audit.py --source SRC --result RES --refs "0-251=a.jpg,b.jpg;252-=c.jpg" --out DIR
         [--pose-gate pose_gate.json] [--crops 3]
"""
from __future__ import annotations

import argparse
import json
import shutil
import subprocess
import sys
from pathlib import Path

import numpy as np

#: fixed BEFORE the first audit (10.10): value at which a metric counts as one "unit" of visible artifact
SCALES = {"flicker": 2.0, "seam": 3.0, "colour": 6.0, "jitter": 0.02, "identity_drop": 0.2, "bg_shift": 2.0}
WINDOW_S = 0.5
CHANGE_DE = 4.0
CROP = 256
#: method v2 (10.10, before any V2 measurement): a frame whose landmarks moved by more than this many inter-ocular
#: distances is a detector failure (blur, sunglasses), not jitter — in the V1 audit three such frames made the jitter
#: RMS of clip 2 1.39 against a p95 of 0.05. They count as `lm_outlier_frames` and score one unit.
LM_OUTLIER_IOD = 0.35
#: share of the face hull that must differ from the source for the frame to count as swapped
SWAPPED_MIN = 0.15


# ---------------------------------------------------------------- pure helpers (tested)
def parse_refs(text: str) -> list[tuple[int, int | None, list[str]]]:
    """"0-251=a,b;252-=c" -> [(0, 251, [a, b]), (252, None, [c])]; plain "a,b" -> whole clip."""
    out = []
    for part in [p for p in text.split(";") if p.strip()]:
        if "=" in part and part.split("=", 1)[0].replace("-", "").isdigit():
            rng, files = part.split("=", 1)
            a, _, b = rng.partition("-")
            out.append((int(a), int(b) if b else None, [f for f in files.split(",") if f]))
        else:
            out.append((0, None, [f for f in part.split(",") if f]))
    return out


def refs_for(frame: int, table) -> int:
    for k, (a, b, _) in enumerate(table):
        if frame >= a and (b is None or frame <= b):
            return k
    return 0


def jitter_series(dev: list) -> list[float | None]:
    """dev[i] = (result - source landmarks) / IOD or None; RMS of the second difference, None at the ends/gaps."""
    out: list[float | None] = [None] * len(dev)
    for i in range(1, len(dev) - 1):
        if dev[i - 1] is not None and dev[i] is not None and dev[i + 1] is not None:
            d2 = dev[i + 1] - 2 * dev[i] + dev[i - 1]
            out[i] = float(np.sqrt((d2 ** 2).sum(1).mean()))
    return out


def alpha_reversals(alphas: list, eps: float = 0.02) -> int:
    """How many times the pose-gate alpha changes direction (swap in, out, in, ...): a clean gate episode is one
    fade out and one fade in = 1 reversal; 10.10 two-person clip V1 toggled 0.29 -> 0.91 -> 0.84 -> 1.0 -> 0.78 -> 1.0."""
    last, direction, n = None, 0, 0
    for a in alphas:
        if a is None:
            continue
        if last is not None and abs(a - last) > eps:
            d = 1 if a > last else -1
            if direction and d != direction:
                n += 1
            direction = d
        last = a
    return n


def frame_score(row: dict, id_ref: float | None) -> float:
    s = 0.0
    if row.get("flicker") is not None:
        s += max(0.0, row["flicker"]) / SCALES["flicker"]
    for k in ("seam", "colour", "jitter", "bg_shift"):
        if row.get(k) is not None:
            s += row[k] / SCALES[k]
    if row.get("identity") is not None and id_ref is not None:
        s += max(0.0, id_ref - row["identity"]) / SCALES["identity_drop"]
    a = row.get("alpha")
    if a is not None and 0.0 < a < 1.0:
        s += 1.0                                    # two faces mixed in one frame
    if row.get("lm_outlier"):
        s += 1.0
    return s


def worst_windows(scores: list[float], width: int, count: int) -> list[tuple[int, int, float]]:
    """Windows [a, b] with the highest mean score, at least one window width apart (distinct moments, not one long
    artifact reported four times)."""
    n = len(scores)
    if n == 0:
        return []
    width = max(1, min(width, n))
    c = np.concatenate([[0.0], np.cumsum(np.nan_to_num(np.asarray(scores, float)))])
    means = [(c[i + width] - c[i]) / width for i in range(n - width + 1)]
    taken: list[tuple[int, int, float]] = []
    for i in np.argsort(means)[::-1]:
        a, b = int(i), int(i) + width - 1
        if all(b + width < x or a > y + width for x, y, _ in taken):
            taken.append((a, b, round(float(means[i]), 3)))
        if len(taken) >= count:
            break
    return sorted(taken)


# ---------------------------------------------------------------- video io
def probe(path: str) -> tuple[int, int, float, int]:
    r = subprocess.run([shutil.which("ffprobe") or "ffprobe", "-v", "error", "-select_streams", "v:0", "-count_packets",
                        "-show_entries", "stream=width,height,r_frame_rate,nb_read_packets", "-of", "csv=p=0", path],
                       capture_output=True, text=True)
    w, h, rate, n = r.stdout.strip().split(",")[:4]
    a, _, b = rate.partition("/")
    return int(w), int(h), float(a) / float(b or 1), int(n)


def frames(path: str, w: int, h: int):
    p = subprocess.Popen([shutil.which("ffmpeg") or "ffmpeg", "-v", "error", "-i", path, "-f", "rawvideo", "-pix_fmt",
                          "bgr24", "-"], stdout=subprocess.PIPE)
    size = w * h * 3
    while True:
        b = p.stdout.read(size)
        if len(b) < size:
            p.wait()
            return
        yield np.frombuffer(b, np.uint8).reshape(h, w, 3)


# ---------------------------------------------------------------- measures on one frame
def lab(img):
    import cv2
    return cv2.cvtColor(img.astype(np.float32) / 255.0, cv2.COLOR_BGR2LAB)


def crop_box(box, w, h, scale=2.0):
    x1, y1, x2, y2 = box
    side = max(x2 - x1, y2 - y1) * scale
    cx, cy = (x1 + x2) / 2, (y1 + y2) / 2
    return int(max(0, cx - side / 2)), int(max(0, cy - side / 2)), int(min(w, cx + side / 2)), int(min(h, cy + side / 2))


def poly_mask(shape, pts):
    import cv2
    m = np.zeros(shape, np.uint8)
    if len(pts) >= 3:
        cv2.fillConvexPoly(m, cv2.convexHull(np.round(pts).astype(np.int32)), 1)
    return m.astype(bool)


def frame_measures(s, r, box, lm_s) -> dict:
    """Seam, background shift, face/neck colour, swapped share on a 256 px face crop."""
    import cv2
    h, w = s.shape[:2]
    x1, y1, x2, y2 = crop_box(box, w, h)
    if x2 - x1 < 16 or y2 - y1 < 16:
        return {}
    sx, sy = CROP / (x2 - x1), CROP / (y2 - y1)
    S = lab(cv2.resize(s[y1:y2, x1:x2], (CROP, CROP), interpolation=cv2.INTER_AREA))
    R = lab(cv2.resize(r[y1:y2, x1:x2], (CROP, CROP), interpolation=cv2.INTER_AREA))
    lm = (lm_s - [x1, y1]) * [sx, sy]
    de = np.linalg.norm(R - S, axis=2)
    changed = cv2.GaussianBlur(de, (0, 0), 3) > CHANGE_DE
    hull = poly_mask((CROP, CROP), lm)
    out = {"swapped": round(float(changed[hull].mean()) if hull.any() else 0.0, 3)}
    k = cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (7, 7))
    band = cv2.morphologyEx(changed.astype(np.uint8), cv2.MORPH_GRADIENT, k).astype(bool)
    band &= ~cv2.erode(hull.astype(np.uint8), cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (25, 25))).astype(bool)
    dl = R[..., 0] - S[..., 0]
    g = np.hypot(cv2.Sobel(dl, cv2.CV_32F, 1, 0, ksize=3), cv2.Sobel(dl, cv2.CV_32F, 0, 1, ksize=3)) / 8.0
    out["seam"] = round(float(g[band].mean()), 3) if band.sum() >= 20 else None
    far = ~cv2.dilate(changed.astype(np.uint8), cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (31, 31))).astype(bool)
    out["bg_shift"] = round(float(de[far].mean()), 3) if far.sum() >= 200 else None
    skin = poly_mask((CROP, CROP), lm[list(range(1, 16)) + [29]])
    for idx in (list(range(36, 42)), list(range(42, 48)), list(range(48, 60)), list(range(17, 27)) + [27]):
        skin &= ~poly_mask((CROP, CROP), lm[idx])
    face_h = max(8.0, lm[8, 1] - min(lm[19, 1], lm[24, 1]))
    nx1, nx2 = int(max(0, lm[6, 0])), int(min(CROP, lm[10, 0]))
    ny1, ny2 = int(max(0, lm[8, 1] + 0.10 * face_h)), int(min(CROP, lm[8, 1] + 0.35 * face_h))
    if skin.sum() >= 50 and nx2 - nx1 >= 4 and ny2 - ny1 >= 4:
        fs, fr = S[skin].mean(0), R[skin].mean(0)
        ns, nr = S[ny1:ny2, nx1:nx2].reshape(-1, 3).mean(0), R[ny1:ny2, nx1:nx2].reshape(-1, 3).mean(0)
        out["colour"] = round(float(np.linalg.norm((fr - nr) - (fs - ns))), 3)
        out["skin_dL"] = round(float(fr[0] - fs[0]), 2)
    return out


def iou(a, b) -> float:
    x1, y1, x2, y2 = max(a[0], b[0]), max(a[1], b[1]), min(a[2], b[2]), min(a[3], b[3])
    inter = max(0.0, x2 - x1) * max(0.0, y2 - y1)
    union = (a[2] - a[0]) * (a[3] - a[1]) + (b[2] - b[0]) * (b[3] - b[1]) - inter
    return inter / union if union > 0 else 0.0


# ---------------------------------------------------------------- audit
def audit(src: str, res: str, refs_text: str, pose_gate: str | None, out: Path, crops: int) -> dict:
    import cv2
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

    table = parse_refs(refs_text)
    ref_means = []
    for _, _, files in table:
        es = []
        for p in files:
            fs = get_many_faces([cv2.imread(p)])
            if fs:
                es.append(emb(max(fs, key=lambda f: f.bounding_box[2] - f.bounding_box[0])))
        m = np.mean(es, axis=0)
        ref_means.append(m / np.linalg.norm(m))
    alphas = {}
    if pose_gate and Path(pose_gate).is_file():
        alphas = {r["frame"]: r["alpha"] for r in json.loads(Path(pose_gate).read_text(encoding="utf-8"))["rows"]}
    w, h, fps, _ = probe(src)
    w2, h2, _, _ = probe(res)
    if (w, h) != (w2, h2):
        raise SystemExit(f"size mismatch {w}x{h} vs {w2}x{h2}")
    rows, dev, boxes = [], [], []
    prev = None
    for i, (s, r) in enumerate(zip(frames(src, w, h), frames(res, w, h))):
        row: dict = {"frame": i, "t": round(i / fps, 3), "alpha": alphas.get(i)}
        fs = get_many_faces([s])
        target = None
        if fs:
            def change(f):
                x1, y1, x2, y2 = [int(max(0, v)) for v in f.bounding_box]
                a, b = s[y1:y2, x1:x2], r[y1:y2, x1:x2]
                return float(np.abs(a.astype(np.int16) - b.astype(np.int16)).mean()) if a.size else 0.0
            target = max(fs, key=change)
        if target is None:
            rows.append(row)
            dev.append(None)
            boxes.append(None)
            prev = None
            continue
        box = [float(v) for v in target.bounding_box]
        lm_s = np.asarray(target.landmark_set["68"], np.float32)
        ref = ref_means[refs_for(i, table)]
        row["src_identity"] = round(float(np.dot(emb(target), ref)), 4)
        rf = [f for f in get_many_faces([r]) if iou([float(v) for v in f.bounding_box], box) >= 0.3]
        lm_r = None
        if rf:
            best = max(rf, key=lambda f: iou([float(v) for v in f.bounding_box], box))
            row["identity"] = round(float(np.dot(emb(best), ref)), 4)
            lm_r = np.asarray(best.landmark_set["68"], np.float32)
        iod = float(np.linalg.norm(lm_s[36:42].mean(0) - lm_s[42:48].mean(0))) or 1.0
        d_lm = None if lm_r is None else (lm_r - lm_s) / iod
        if d_lm is not None and float(np.linalg.norm(d_lm, axis=1).mean()) > LM_OUTLIER_IOD:
            row["lm_outlier"] = True                  # the detector found a different/garbled face: counted, not jitter
            d_lm = None
        dev.append(d_lm)
        row.update(frame_measures(s, r, box, lm_s))
        # flicker: dE frame->frame inside the face hull (both frames), result minus source
        hull = poly_mask((h, w), lm_s)
        if prev is not None:
            both = hull & prev[2]
            if both.sum() >= 100:
                ys, xs = np.where(both)
                yy1, yy2, xx1, xx2 = ys.min(), ys.max() + 1, xs.min(), xs.max() + 1
                m = both[yy1:yy2, xx1:xx2]
                d_r = np.linalg.norm(lab(r[yy1:yy2, xx1:xx2]) - lab(prev[1][yy1:yy2, xx1:xx2]), axis=2)[m].mean()
                d_s = np.linalg.norm(lab(s[yy1:yy2, xx1:xx2]) - lab(prev[0][yy1:yy2, xx1:xx2]), axis=2)[m].mean()
                row["flicker"] = round(float(d_r - d_s), 3)
        prev = (s.copy(), r.copy(), hull)
        boxes.append(box)
        rows.append(row)
    for row, j in zip(rows, jitter_series(dev)):
        if j is not None:
            row["jitter"] = round(j, 4)
    # identity reference per scene: the median of that scene's swapped frames
    id_ref = {}
    for k in range(len(table)):
        ids = [r["identity"] for r in rows if "identity" in r and refs_for(r["frame"], table) == k and r.get("swapped", 0) > SWAPPED_MIN]
        id_ref[k] = float(np.median(ids)) if ids else None
    for row in rows:
        row["score"] = round(frame_score(row, id_ref.get(refs_for(row["frame"], table))), 3)
    win = max(1, int(round(WINDOW_S * fps)))
    worst = worst_windows([r["score"] for r in rows], win, 4)

    def stat(key, fn=np.mean):
        v = [r[key] for r in rows if r.get(key) is not None]
        return round(float(fn(v)), 4) if v else None

    def p95(v):
        return np.percentile(v, 95)
    summary = {"frames": len(rows), "fps": round(fps, 4), "faces": sum(b is not None for b in boxes),
               "identity_mean": stat("identity"), "identity_p05": stat("identity", lambda v: np.percentile(v, 5)),
               "src_identity_mean": stat("src_identity"),
               "unswapped_frames": sum(1 for r in rows if r.get("swapped") is not None and r["swapped"] < SWAPPED_MIN),
               "mixed_alpha_frames": sum(1 for r in rows if r.get("alpha") is not None and 0 < r["alpha"] < 1),
               "alpha_reversals": alpha_reversals([r.get("alpha") for r in rows]),
               "lm_outlier_frames": sum(1 for r in rows if r.get("lm_outlier")),
               "flicker_mean": stat("flicker"), "flicker_p95": stat("flicker", p95),
               "seam_mean": stat("seam"), "seam_p95": stat("seam", p95),
               "bg_shift_mean": stat("bg_shift"), "colour_mean": stat("colour"), "colour_p95": stat("colour", p95),
               "skin_dL_mean": stat("skin_dL"), "jitter_rms": round(float(np.sqrt(np.mean(np.square(
                   [r["jitter"] for r in rows if r.get("jitter") is not None])))), 4) if any("jitter" in r for r in rows) else None,
               "jitter_p95": stat("jitter", p95), "score_mean": stat("score"), "score_p95": stat("score", p95),
               "worst_window_mean": round(float(np.mean([x[2] for x in worst])), 3) if worst else None}
    segs = []
    for a, b, sc in worst:
        part = rows[a:b + 1]
        comp = {}
        for k in ("flicker", "seam", "colour", "jitter", "bg_shift"):
            v = [x[k] for x in part if x.get(k) is not None]
            comp[k] = round(float(np.mean(v)), 3) if v else None
        ids = [x["identity"] for x in part if x.get("identity") is not None]
        comp["identity"] = round(float(np.mean(ids)), 3) if ids else None
        comp["mixed_alpha"] = sum(1 for x in part if x.get("alpha") is not None and 0 < x["alpha"] < 1)
        comp["unswapped"] = sum(1 for x in part if x.get("swapped") is not None and x["swapped"] < SWAPPED_MIN)
        segs.append({"frames": [a, b], "t": [round(a / fps, 2), round(b / fps, 2)], "score": sc, "components": comp})
    report = {"tool": "artifact_audit", "version": 1, "scales": SCALES, "window_s": WINDOW_S, "source": src, "result": res,
              "refs": refs_text, "pose_gate": pose_gate, "summary": summary, "worst_segments": segs, "rows": rows}
    out.mkdir(parents=True, exist_ok=True)
    (out / "audit.json").write_text(json.dumps(report, indent=1), encoding="utf-8")
    if crops:
        write_crops(res, w, h, boxes, segs[:crops], out / "crops")
    return report


def write_crops(res: str, w: int, h: int, boxes, segs, folder: Path, cell: int = 192) -> None:
    """4 frames per worst window, face area only (2.0 x box), 2x2 grid = 384 px: the only pixels sent for review."""
    import cv2
    folder.mkdir(parents=True, exist_ok=True)
    want = {}
    for k, seg in enumerate(segs):
        a, b = seg["frames"]
        for j, f in enumerate(np.linspace(a, b, 4).round().astype(int)):
            want.setdefault(int(f), []).append((k, j))
    cells: dict[int, list] = {k: [None] * 4 for k in range(len(segs))}
    for i, fr in enumerate(frames(res, w, h)):
        if i in want:
            box = boxes[i] if i < len(boxes) and boxes[i] is not None else next(
                (boxes[j] for j in range(i, -1, -1) if j < len(boxes) and boxes[j] is not None), None)
            if box is None:
                continue
            x1, y1, x2, y2 = crop_box(box, w, h, 1.8)
            img = cv2.resize(fr[y1:y2, x1:x2], (cell, cell), interpolation=cv2.INTER_AREA)
            for k, j in want[i]:
                cells[k][j] = img
    for k, cs in cells.items():
        cs = [c if c is not None else np.zeros((cell, cell, 3), np.uint8) for c in cs]
        grid = np.vstack([np.hstack(cs[:2]), np.hstack(cs[2:])])
        cv2.imwrite(str(folder / f"seg{k}.jpg"), grid, [cv2.IMWRITE_JPEG_QUALITY, 90])


def main(argv=None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--source", required=True)
    ap.add_argument("--result", required=True)
    ap.add_argument("--refs", required=True)
    ap.add_argument("--pose-gate")
    ap.add_argument("--out", required=True)
    ap.add_argument("--crops", type=int, default=0)
    a = ap.parse_args(argv)
    rep = audit(a.source, a.result, a.refs, a.pose_gate, Path(a.out), a.crops)
    print(json.dumps({"summary": rep["summary"], "worst_segments": rep["worst_segments"]}, indent=1))
    return 0


if __name__ == "__main__":
    sys.exit(main())
