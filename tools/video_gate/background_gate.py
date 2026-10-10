"""Stage 4 gate — background swap. Thresholds fixed in docs/owner/VIDEO_PIPELINE_STAGES_20261010.md before the first run
(section "Stage 4 — background swap", metric_version s4-v1, commit f6e8ddc7) and copied into T below.

  S4-T1 frames / fps / duration / alignment inside the person core K
  S4-T2 core K = erode(alpha >= 0.98, 0.005 H): lossless master max|C - S| == 0; H.264 delivery min PSNR in K >= 40 dB
  S4-T3 alpha warping error in the edge band (|dist to alpha=0.5 contour| <= 0.01 H): mean <= 0.08, p95 <= 0.15
  S4-T4 halo: ring 0.004 H .. 0.011 H outside alpha >= 0.5, mean dE76(C, B) <= 8.0
  S4-T5 old background: zone 0.015 H .. 0.06 H outside the gate's own person mask P, share of dE76(C, B) > 10 <= 0.01
  S4-T6 body: MediaPipe Pose 11-32 / torso <= 0.02
  S4-T7 holes: inside the subject mask eroded by 0.015 H, share alpha < 0.5 <= 0.005

P is independent of the tool's alpha: YOLOX persons (score >= 0.5) -> SAM2.1-tiny per box, on the SOURCE frames, run in
FaceFusion's interpreter (onnxruntime) like animation_gate's face phase. Everything else runs in the gate venv
(opencv + mediapipe):
  <gate-venv>/python background_gate.py --source source.mp4 --result master.mp4 --alpha JOB/alpha --background
      JOB/bg/background.png --out DIR [--lossless]
"""
from __future__ import annotations

import argparse
import json
import math
import subprocess
import sys
import time
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parent))

METRIC_VERSION = "s4-v1"
T = {"align_share": 1.0, "fps_tol": 0.01, "lossless_max_abs": 0, "psnr_min_db": 40.0,
     "warp_mean_max": 0.08, "warp_p95_max": 0.15, "halo_de_max": 8.0, "leak_de": 10.0, "leak_share_max": 0.01,
     "body_max": 0.02, "hole_share_max": 0.005}
CORE_U8 = 250                       # alpha >= 0.98
FRAC = {"core_erode": 0.005, "band": 0.01, "ring_in": 0.004, "ring_out": 0.011, "zone_in": 0.015, "zone_out": 0.06,
        "hole_erode": 0.015}
OCCLUSION_PX = 1.0
CONTROL_FRAMES = [0, 13, 25, 38, 51, 64, 76, 89]


def px(frac: float, h: int) -> int:
    return max(1, int(round(frac * h)))


# ---------------------------------------------------------------- pure measures (unit-tested; cv2, no GPU)
def dist_inside(mask: np.ndarray) -> np.ndarray:
    """Euclidean distance of each True pixel to the nearest False pixel (0 on False)."""
    import cv2
    m = mask.astype(np.uint8)
    if not m.any():
        return np.zeros(mask.shape, np.float32)
    return cv2.distanceTransform(m, cv2.DIST_L2, 5)


def core_mask(alpha_u8: np.ndarray, erode_px: int) -> np.ndarray:
    return dist_inside(alpha_u8 >= CORE_U8) > erode_px


def edge_band(alpha_u8: np.ndarray, w_px: int) -> np.ndarray:
    m = alpha_u8 >= 128
    return (m & (dist_inside(m) <= w_px)) | (~m & (dist_inside(~m) <= w_px))


def outer_ring(alpha_u8: np.ndarray, r_in: int, r_out: int) -> np.ndarray:
    d = dist_inside(~(alpha_u8 >= 128))
    return (d >= r_in) & (d <= r_out)


def leak_zone(people: np.ndarray, r_in: int, r_out: int) -> np.ndarray:
    d = dist_inside(~people)
    return (d > r_in) & (d <= r_out)


def lab(bgr: np.ndarray) -> np.ndarray:
    import cv2
    return cv2.cvtColor(bgr.astype(np.float32) / 255.0, cv2.COLOR_BGR2Lab)


def delta_e(a_bgr: np.ndarray, b_bgr: np.ndarray) -> np.ndarray:
    return np.linalg.norm(lab(a_bgr) - lab(b_bgr), axis=-1)


def warp_back(prev: np.ndarray, flow_bw: np.ndarray) -> np.ndarray:
    """prev sampled at x + flow_bw(x): brings frame t-1 into frame t (flow_bw maps t -> t-1)."""
    import cv2
    h, w = prev.shape[:2]
    gx, gy = np.meshgrid(np.arange(w, dtype=np.float32), np.arange(h, dtype=np.float32))
    return cv2.remap(prev.astype(np.float32), gx + flow_bw[..., 0], gy + flow_bw[..., 1], cv2.INTER_LINEAR,
                     borderMode=cv2.BORDER_REPLICATE)


def occluded(flow_fw: np.ndarray, flow_bw: np.ndarray, thr: float = OCCLUSION_PX) -> np.ndarray:
    """Forward-backward check: fw(x + bw(x)) + bw(x) should be ~0; larger -> occluded / unreliable flow."""
    fw_at = np.dstack([warp_back(flow_fw[..., 0], flow_bw), warp_back(flow_fw[..., 1], flow_bw)])
    return np.linalg.norm(fw_at + flow_bw, axis=-1) > thr


def warping_error(a_prev: np.ndarray, a_cur: np.ndarray, flow_fw: np.ndarray, flow_bw: np.ndarray, band_px: int):
    """Mean |a_t - warp(a_{t-1})| (alpha in 0..1) in the edge band of a_t, occluded pixels excluded. None if empty."""
    cur = a_cur.astype(np.float32) / 255.0
    prev = a_prev.astype(np.float32) / 255.0
    sel = edge_band(a_cur, band_px) & ~occluded(flow_fw, flow_bw)
    if not sel.any():
        return None
    return float(np.abs(cur - warp_back(prev, flow_bw))[sel].mean())


def full_res_flow(g0: np.ndarray, g1: np.ndarray, shape) -> np.ndarray:
    """Farneback at half resolution (as animation_gate T5), scaled back to full resolution."""
    import cv2
    h, w = shape
    half = lambda g: cv2.resize(g, (w // 2, h // 2), interpolation=cv2.INTER_AREA)
    f = cv2.calcOpticalFlowFarneback(half(g0), half(g1), None, 0.5, 3, 15, 3, 5, 1.2, 0)
    return cv2.resize(f, (w, h), interpolation=cv2.INTER_LINEAR) * np.float32(w / (w // 2))


def p95(values: list[float]) -> float | None:
    return float(np.percentile(values, 95)) if values else None


def tercile_means(weights: list[float], values: list) -> list:
    """Split frames into motion terciles by `weights` and average `values` (None skipped) per tercile."""
    pairs = [(w, v) for w, v in zip(weights, values) if v is not None and w is not None]
    if len(pairs) < 3:
        return []
    pairs.sort(key=lambda p: p[0])
    k = len(pairs)
    cuts = [0, k // 3, 2 * k // 3, k]
    return [{"motion_px_mean": round(float(np.mean([p[0] for p in pairs[cuts[i]:cuts[i + 1]]])), 3),
             "value_mean": round(float(np.mean([p[1] for p in pairs[cuts[i]:cuts[i + 1]]])), 4),
             "frames": cuts[i + 1] - cuts[i]} for i in range(3)]


def checks_from(m: dict, lossless: bool) -> dict:
    ok = lambda v, lim: v is not None and v <= lim  # noqa: E731
    return {
        "S4_T1_frame_count": m["frames"][0] == m["frames"][1], "S4_T1_fps": m["fps_ok"], "S4_T1_duration": m["duration_ok"],
        "S4_T1_alignment": m["aligned_share"] >= T["align_share"],
        "S4_T2_core_pixels": (m["max_abs_core"] == T["lossless_max_abs"]) if lossless else
        (m["min_psnr_core_db"] is not None and m["min_psnr_core_db"] >= T["psnr_min_db"]),
        "S4_T3_alpha_mean": ok(m["warp_error_mean"], T["warp_mean_max"]),
        "S4_T3_alpha_p95": ok(m["warp_error_p95"], T["warp_p95_max"]),
        "S4_T4_halo": ok(m["halo_de_mean"], T["halo_de_max"]),
        "S4_T5_leakage": ok(m["leak_share_mean"], T["leak_share_max"]),
        "S4_T6_body": ok(m["body_nme_mean"], T["body_max"]),
        "S4_T7_holes": ok(m["hole_share_mean"], T["hole_share_max"]),
    }


# ---------------------------------------------------------------- person masks (FaceFusion interpreter, onnxruntime)
def mask_phase(src: Path, out: Path) -> None:
    from PIL import Image
    sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "genjutsu"))
    import genjutsu as g
    import animation_gate as ag
    det, seg = g.Detector(), g.Segmenter()
    people, subject, counts = [], [], []
    for f in ag.read_frames(src):
        img = Image.fromarray(np.ascontiguousarray(f[:, :, ::-1]))
        boxes = [b for b in det(img) if b[4] >= 0.5]
        ms = [seg(img, b[:4]) for b in boxes]
        counts.append(len(ms))
        people.append(np.any(ms, axis=0) if ms else np.zeros(f.shape[:2], bool))
        subject.append(max(ms, key=lambda m: int(m.sum())) if ms else np.zeros(f.shape[:2], bool))
    np.savez_compressed(out, people=np.packbits(np.stack(people), axis=-1), subject=np.packbits(np.stack(subject), axis=-1),
                        width=people[0].shape[1], counts=np.array(counts))


def run_mask_phase(src: Path, out: Path) -> dict:
    import animation_gate as ag
    py = ag.FACEFUSION / ".venv" / "Scripts" / "python.exe"
    r = subprocess.run([str(py), str(Path(__file__).resolve()), "--mask-phase", "--source", str(src), "--out", str(out)],
                       capture_output=True, text=True)
    if r.returncode or not out.exists():
        raise SystemExit("mask phase failed: " + (r.stderr or r.stdout)[-800:])
    z = np.load(out)
    w = int(z["width"])
    return {"people": np.unpackbits(z["people"], axis=-1)[..., :w].astype(bool),
            "subject": np.unpackbits(z["subject"], axis=-1)[..., :w].astype(bool), "counts": z["counts"].tolist()}


# ---------------------------------------------------------------- visual evidence
def heat(d: np.ndarray) -> np.ndarray:
    import cv2
    return cv2.applyColorMap(np.clip(d * 4, 0, 255).astype(np.uint8), cv2.COLORMAP_INFERNO)


def panel(s, r, a, k, height):
    import cv2
    w = int(s.shape[1] * height / s.shape[0]) // 2 * 2
    d = np.abs(s.astype(np.int16) - r.astype(np.int16)).max(axis=2) * k
    tiles = (s, r, cv2.cvtColor(a, cv2.COLOR_GRAY2BGR), heat(d))
    return np.hstack([cv2.resize(x, (w, height), interpolation=cv2.INTER_AREA) for x in tiles]), w


def side_by_side(src, res, alphas, cores, fps, path: Path, height: int = 360) -> None:
    w = panel(src[0], res[0], alphas[0], cores[0], height)[1]
    enc = subprocess.Popen(["ffmpeg", "-v", "error", "-y", "-f", "rawvideo", "-pix_fmt", "bgr24", "-s", f"{w * 4}x{height}",
                            "-r", str(fps or 30), "-i", "-", "-c:v", "libx264", "-crf", "18", "-pix_fmt", "yuv420p", str(path)],
                           stdin=subprocess.PIPE)
    for s, r, a, k in zip(src, res, alphas, cores):
        enc.stdin.write(np.ascontiguousarray(panel(s, r, a, k, height)[0]).tobytes())
    enc.stdin.close()
    enc.wait()


def contact_sheet(src, res, alphas, cores, idx, path: Path, width: int = 240) -> None:
    import cv2
    cols = []
    for i in idx:
        h = int(src[i].shape[0] * width / src[i].shape[1])
        d = np.abs(src[i].astype(np.int16) - res[i].astype(np.int16)).max(axis=2) * cores[i]
        tiles = (src[i], res[i], cv2.cvtColor(alphas[i], cv2.COLOR_GRAY2BGR), heat(d))
        col = np.vstack([cv2.resize(x, (width, h), interpolation=cv2.INTER_AREA) for x in tiles])
        cv2.putText(col, str(i), (6, 22), cv2.FONT_HERSHEY_SIMPLEX, 0.7, (255, 255, 255), 2)
        cols.append(col)
    cv2.imwrite(str(path), np.hstack(cols), [cv2.IMWRITE_JPEG_QUALITY, 92])


# ---------------------------------------------------------------- main
def main(argv=None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--source", required=True)
    ap.add_argument("--result", default="")
    ap.add_argument("--alpha", default="")
    ap.add_argument("--background", default="")
    ap.add_argument("--out", required=True)
    ap.add_argument("--lossless", action="store_true")
    ap.add_argument("--masks", default="", help="reuse a masks.npz from an earlier run of this gate on the same source")
    ap.add_argument("--mask-phase", action="store_true", help=argparse.SUPPRESS)
    a = ap.parse_args(argv)
    if a.mask_phase:
        mask_phase(Path(a.source), Path(a.out))
        return 0
    import cv2
    import animation_gate as ag
    t0 = time.time()
    out = Path(a.out)
    out.mkdir(parents=True, exist_ok=True)
    src_p, res_p = Path(a.source), Path(a.result)
    src_info, res_info = ag.probe(src_p), ag.probe(res_p)
    src, res = ag.read_frames(src_p), ag.read_frames(res_p)
    n = min(len(src), len(res))
    h, w = src[0].shape[:2]
    alphas = [cv2.imread(str(p), cv2.IMREAD_GRAYSCALE) for p in sorted(Path(a.alpha).glob("*.png"))][:n]
    bgp = Path(a.background)
    bg_files = sorted(bgp.glob("*.png")) if bgp.is_dir() else [bgp]
    bgs = [cv2.imread(str(bg_files[i % len(bg_files)])) for i in range(n)]
    t_mask = time.time()
    masks_path = Path(a.masks) if a.masks else out / "masks.npz"
    pm = run_mask_phase(src_p, masks_path) if not a.masks else None
    if pm is None:
        z = np.load(masks_path)
        wz = int(z["width"])
        pm = {"people": np.unpackbits(z["people"], axis=-1)[..., :wz].astype(bool),
              "subject": np.unpackbits(z["subject"], axis=-1)[..., :wz].astype(bool), "counts": z["counts"].tolist()}
    t_mask = time.time() - t_mask
    r_core, r_band = px(FRAC["core_erode"], h), px(FRAC["band"], h)
    r_ring = (px(FRAC["ring_in"], h), px(FRAC["ring_out"], h))
    r_zone = (px(FRAC["zone_in"], h), px(FRAC["zone_out"], h))
    r_hole = px(FRAC["hole_erode"], h)
    cores = [core_mask(alphas[i], r_core) for i in range(n)]
    # T1 + T2
    small = lambda f: cv2.resize(f, (w // 4, h // 4), interpolation=cv2.INTER_AREA).astype(np.float32)  # noqa: E731
    s4, r4 = [small(f) for f in src[:n]], [small(f) for f in res[:n]]
    rows, aligned = [], 0
    for i in range(n):
        k4 = cv2.resize(cores[i].astype(np.uint8), (w // 4, h // 4), interpolation=cv2.INTER_NEAREST) > 0
        cand = {j: float(((r4[i] - s4[j]) ** 2)[k4].mean()) if k4.any() else 0.0 for j in range(max(0, i - 2), min(n, i + 3))}
        twin = {j: float(((s4[i] - s4[j]) ** 2)[k4].mean()) if k4.any() else 0.0 for j in cand}
        ok = ag.aligned_to_self(cand, i, twin)
        aligned += ok
        d = np.abs(src[i].astype(np.int16) - res[i].astype(np.int16))[cores[i]]
        mse = float((d.astype(np.float32) ** 2).mean()) if d.size else 0.0
        rows.append({"frame": i, "aligned": bool(ok), "core_px": int(cores[i].sum()), "max_abs_core": int(d.max()) if d.size else 0,
                     "psnr_core_db": 99.0 if mse == 0 else round(10 * math.log10(255 ** 2 / mse), 2),
                     "people": pm["counts"][i] if i < len(pm["counts"]) else None})
    # T3 (+ SAM2 per-frame reference) and motion
    grays = [cv2.cvtColor(f, cv2.COLOR_BGR2GRAY) for f in src[:n]]
    warp, warp_ref, motion = [None], [None], [None]
    for i in range(1, n):
        fw = full_res_flow(grays[i - 1], grays[i], (h, w))
        bw = full_res_flow(grays[i], grays[i - 1], (h, w))
        e = warping_error(alphas[i - 1], alphas[i], fw, bw, r_band)
        sub = [pm["subject"][i - 1].astype(np.uint8) * 255, pm["subject"][i].astype(np.uint8) * 255]
        er = warping_error(sub[0], sub[1], fw, bw, r_band)
        person = alphas[i] >= 128
        mv = float(np.linalg.norm(fw, axis=-1)[person].mean()) if person.any() else None
        warp.append(e)
        warp_ref.append(er)
        motion.append(mv)
        rows[i].update(warp_error=None if e is None else round(e, 4), warp_error_sam2_ref=None if er is None else round(er, 4),
                       motion_px=None if mv is None else round(mv, 3))
    # T4, T5, T7
    halo, leak, holes = [], [], []
    for i in range(n):
        de = delta_e(res[i], bgs[i])
        ring = outer_ring(alphas[i], *r_ring)
        hv = float(de[ring].mean()) if ring.any() else None
        zone = leak_zone(pm["people"][i], *r_zone)
        lv = float((de[zone] > T["leak_de"]).mean()) if zone.any() else None
        inner = dist_inside(pm["subject"][i]) > r_hole
        ov = float((alphas[i][inner] < 128).mean()) if inner.any() else None
        halo.append(hv)
        leak.append(lv)
        holes.append(ov)
        rows[i].update(halo_de=None if hv is None else round(hv, 3), leak_share=None if lv is None else round(lv, 5),
                       hole_share=None if ov is None else round(ov, 5))
    # T6 body
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
            rows[i]["body_nme"] = round(body[-1], 4)
    sf, rf = src_info.get("fps"), res_info.get("fps")
    fps_ok = sf is not None and rf is not None and abs(sf - rf) <= T["fps_tol"]
    dur_ok = not (src_info.get("duration") and res_info.get("duration") and sf) or \
        abs(src_info["duration"] - res_info["duration"]) <= 1.0 / sf + 1e-6
    clean = lambda v: [x for x in v if x is not None]  # noqa: E731
    mean = lambda v: round(float(np.mean(clean(v))), 4) if clean(v) else None  # noqa: E731
    m = {"frames": [len(src), len(res)], "fps_ok": bool(fps_ok), "duration_ok": bool(dur_ok),
         "aligned_share": round(aligned / n, 4) if n else 0.0,
         "max_abs_core": max(r["max_abs_core"] for r in rows), "min_psnr_core_db": min(r["psnr_core_db"] for r in rows),
         "core_share_mean": round(float(np.mean([c.mean() for c in cores])), 4),
         "warp_error_mean": mean(warp), "warp_error_p95": None if not clean(warp) else round(p95(clean(warp)), 4),
         "warp_error_sam2_reference_mean": mean(warp_ref), "warp_frames": len(clean(warp)),
         "halo_de_mean": mean(halo), "leak_share_mean": None if not clean(leak) else round(float(np.mean(clean(leak))), 5),
         "body_nme_mean": round(float(np.mean(body)), 4) if body else None, "bodies_measured": len(body),
         "hole_share_mean": None if not clean(holes) else round(float(np.mean(clean(holes))), 5),
         "people_per_frame_max": max(pm["counts"]) if pm["counts"] else 0,
         "motion_terciles_warp": tercile_means(motion, warp), "motion_terciles_halo": tercile_means(motion, halo)}
    checks = checks_from(m, a.lossless)
    side_by_side(src[:n], res[:n], alphas, cores, sf, out / "side_by_side.mp4")
    idx = [i for i in CONTROL_FRAMES if i < n]
    contact_sheet(src[:n], res[:n], alphas, cores, idx, out / "contact_sheet.jpg")
    report = {"gate": "stage4-background", "metric_version": METRIC_VERSION, "thresholds": T, "fractions_of_h": FRAC,
              "pixels": {"core_erode": r_core, "band": r_band, "ring": r_ring, "zone": r_zone, "hole_erode": r_hole},
              "lossless_declared": a.lossless,
              "command": [Path(sys.executable).name, Path(__file__).name, *(argv if argv is not None else sys.argv[1:])],
              "versions": ag.versions(), "person_masks": "YOLOX-L (score>=0.5) + SAM2.1-tiny ONNX, CPU, gate's own",
              "source": {**src_info, "sha256": ag.sha256_of(src_p)}, "result": {**res_info, "sha256": ag.sha256_of(res_p)},
              "alpha_sha256": ag.sha256_of(Path(a.alpha)), "background_sha256": ag.sha256_of(bgp),
              "metrics": m, "checks": checks, "verdict": "PASS" if all(checks.values()) else "FAIL", "control_frames": idx,
              "seconds": {"total": round(time.time() - t0, 1), "mask_phase": round(t_mask, 1)}, "per_frame": rows}
    (out / "report.json").write_text(json.dumps(report, ensure_ascii=False, indent=1), encoding="utf-8")
    print(json.dumps({k: v for k, v in report.items() if k not in ("per_frame", "versions")}, ensure_ascii=False))
    return 0 if report["verdict"] == "PASS" else 1


if __name__ == "__main__":
    sys.exit(main())
