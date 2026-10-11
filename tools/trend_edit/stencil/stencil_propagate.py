"""Propagate a few hand-corrected hair masks (keyframes) to every frame of a clip.

Backends
  flow   : dense optical flow (DIS, Farneback fallback) chained frame to frame, forward from the previous keyframe and
           backward from the next one, blended by distance, then a guided-filter refine of a +-6 px band. CPU, no torch.
  sam2   : SAM2.1 video predictor with the keyframe masks as mask prompts (needs torch + the `sam2` package, CPU is ok).
  fused  : mean of flow and sam2 soft masks, then the same band refine.

Consistency check (the evidence): every edited keyframe is withheld in turn, the clip is propagated from the REMAINING keyframes,
and the propagated mask at the withheld frame is compared (IoU, boundary F1 at 3 px) with the edited mask. The same table
reports the "nearest keyframe copied as is" baseline, so the number shows what propagation adds.

  python stencil_propagate.py run --pack PACK_DIR --edited DIR_OF_edited_pngs [--backend flow] [--out DIR]
  python stencil_propagate.py pseudo --pack PACK_DIR            # pipeline check: the AUTO masks play the edited keyframes
  python stencil_propagate.py synthetic --image gf7.jpg --gt gt_mask.png --out DIR   # pipeline check on known motion
"""
from __future__ import annotations

import argparse
import json
import re
import sys
import time
from pathlib import Path

import cv2
import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parent))
import stencil_core as sc  # noqa: E402

FLOW_LONG_SIDE = 640
BAND_PX = 6
GF_RADIUS, GF_EPS = 6, 1e-3


# ------------------------------------------------------------------ optical flow
def _gray_small(bgr: np.ndarray, long_side: int = FLOW_LONG_SIDE) -> tuple[np.ndarray, float]:
    h, w = bgr.shape[:2]
    s = min(1.0, long_side / max(h, w))
    g = cv2.cvtColor(bgr, cv2.COLOR_BGR2GRAY)
    if s < 1.0:
        g = cv2.resize(g, (int(round(w * s)), int(round(h * s))), interpolation=cv2.INTER_AREA)
    return g, s


class FlowEngine:
    """flow(a, b): dense flow field F with  a(x) ~ b(x + F(x))  (so b can be sampled to reconstruct a)."""

    def __init__(self, method: str = "dis"):
        self.method = method
        self.dis = cv2.DISOpticalFlow_create(cv2.DISOPTICAL_FLOW_PRESET_MEDIUM) if method == "dis" else None

    def __call__(self, a_bgr: np.ndarray, b_bgr: np.ndarray) -> np.ndarray:
        ga, s = _gray_small(a_bgr)
        gb, _ = _gray_small(b_bgr)
        if self.dis is not None:
            f = self.dis.calc(ga, gb, None)
        else:
            f = cv2.calcOpticalFlowFarneback(ga, gb, None, 0.5, 4, 21, 3, 7, 1.5, 0)
        h, w = a_bgr.shape[:2]
        if s < 1.0:
            f = cv2.resize(f, (w, h), interpolation=cv2.INTER_LINEAR) / s
        return f.astype(np.float32)


def warp_with(mask: np.ndarray, flow: np.ndarray) -> np.ndarray:
    """out(x) = mask(x + flow(x))."""
    h, w = mask.shape
    gx, gy = np.meshgrid(np.arange(w, dtype=np.float32), np.arange(h, dtype=np.float32))
    return cv2.remap(mask.astype(np.float32), gx + flow[..., 0], gy + flow[..., 1], cv2.INTER_LINEAR,
                     borderMode=cv2.BORDER_CONSTANT, borderValue=0)


def _sharpen(m: np.ndarray) -> np.ndarray:
    """Counteract the blur that one bilinear warp per frame adds (keeps 0.5 where it is)."""
    return np.clip((m - 0.5) * 1.35 + 0.5, 0.0, 1.0)


# ------------------------------------------------------------------ flow propagation
def propagate_flow(frames: list[np.ndarray], keys: dict[int, np.ndarray], method: str = "dis",
                   refine: bool = True) -> dict[int, np.ndarray]:
    """frames: BGR list (a contiguous segment). keys: {index in frames -> mask float 0..1}. Returns {index -> soft mask 0..1}
    for every frame of the segment (keyframes are returned unchanged)."""
    n = len(frames)
    ks = sorted(keys)
    if not ks:
        raise ValueError("no keyframes")
    eng = FlowEngine(method)
    back: dict[int, np.ndarray] = {}      # back[t]  = flow(t -> t-1): carries mask t-1 onto frame t
    fwd: dict[int, np.ndarray] = {}       # fwd[t]   = flow(t -> t+1): carries mask t+1 onto frame t
    lo, hi = ks[0], ks[-1]

    def need(t0, t1):
        for t in range(t0, t1 + 1):
            if 0 < t < n and t not in back:
                back[t] = eng(frames[t], frames[t - 1])
            if 0 <= t < n - 1 and t not in fwd:
                fwd[t] = eng(frames[t], frames[t + 1])

    need(0, n - 1)
    f_from: dict[int, dict[int, np.ndarray]] = {}    # f_from[k][t]: chain forward from key k
    b_from: dict[int, dict[int, np.ndarray]] = {}
    for i, k in enumerate(ks):
        end_f = ks[i + 1] if i + 1 < len(ks) else n - 1
        m = keys[k].astype(np.float32)
        ch = {k: m}
        for t in range(k + 1, end_f + 1):
            m = _sharpen(warp_with(m, back[t]))
            ch[t] = m
        f_from[k] = ch
        end_b = ks[i - 1] if i > 0 else 0
        m = keys[k].astype(np.float32)
        ch = {k: m}
        for t in range(k - 1, end_b - 1, -1):
            m = _sharpen(warp_with(m, fwd[t]))
            ch[t] = m
        b_from[k] = ch
    out: dict[int, np.ndarray] = {}
    for t in range(n):
        if t in keys:
            out[t] = keys[t].astype(np.float32)
            continue
        prev = max([k for k in ks if k < t], default=None)
        nxt = min([k for k in ks if k > t], default=None)
        if prev is not None and nxt is not None:
            w = (t - prev) / (nxt - prev)
            m = (1 - w) * f_from[prev][t] + w * b_from[nxt][t]
        elif prev is not None:
            m = f_from[prev][t]
        else:
            m = b_from[nxt][t]
        out[t] = m
    if refine:
        for t in out:
            if t not in keys:
                out[t] = band_refine(frames[t], out[t])
    return out


def band_refine(frame: np.ndarray, m: np.ndarray, band: int = BAND_PX) -> np.ndarray:
    """Snap the mask edge to the image edge inside a +-band px strip around the current boundary; the interior and the far
    exterior are left alone (so the guided filter cannot grow the mask onto a distant dark region)."""
    hard = (m > 0.5).astype(np.uint8)
    if hard.sum() == 0:
        return m
    k = cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (2 * band + 1, 2 * band + 1))
    inner, outer = cv2.erode(hard, k) > 0, cv2.dilate(hard, k) > 0
    guide = cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY).astype(np.float32) / 255.0
    g = np.clip(sc.guided_filter(guide, m.astype(np.float32), GF_RADIUS, GF_EPS), 0, 1)
    g = np.clip((g - 0.5) * 1.6 + 0.5, 0, 1)
    out = np.where(inner, 1.0, np.where(outer, g, 0.0)).astype(np.float32)
    return out


# ------------------------------------------------------------------ SAM2 video predictor (optional backend)
def propagate_sam2(frames: list[np.ndarray], keys: dict[int, np.ndarray], tmp_dir: Path,
                   ckpt: Path, cfg: str = "configs/sam2.1/sam2.1_hiera_t.yaml") -> dict[int, np.ndarray]:
    """SAM2.1 video predictor, mask prompts on the keyframes, forward + reverse propagation, blended by distance."""
    import torch
    from sam2.build_sam import build_sam2_video_predictor
    tmp_dir.mkdir(parents=True, exist_ok=True)
    for i, fr in enumerate(frames):
        cv2.imwrite(str(tmp_dir / f"{i:05d}.jpg"), fr, [cv2.IMWRITE_JPEG_QUALITY, 95])
    pred = build_sam2_video_predictor(cfg, str(ckpt), device="cpu")
    n = len(frames)
    ks = sorted(keys)
    res = {}
    with torch.inference_mode():
        for reverse in (False, True):
            st = pred.init_state(video_path=str(tmp_dir), offload_video_to_cpu=True, offload_state_to_cpu=True)
            for k in ks:
                pred.add_new_mask(st, frame_idx=k, obj_id=1, mask=torch.from_numpy(keys[k] > 0.5))
            start = ks[-1] if reverse else ks[0]
            r = {}
            for fi, _ids, logits in pred.propagate_in_video(st, start_frame_idx=start, reverse=reverse):
                r[fi] = torch.sigmoid(logits[0, 0]).cpu().numpy().astype(np.float32)
            res[reverse] = r
            pred.reset_state(st)
    out: dict[int, np.ndarray] = {}
    for t in range(n):
        if t in keys:
            out[t] = keys[t].astype(np.float32)
            continue
        f, b = res[False].get(t), res[True].get(t)
        prev = max([k for k in ks if k < t], default=None)
        nxt = min([k for k in ks if k > t], default=None)
        if f is not None and b is not None and prev is not None and nxt is not None:
            w = (t - prev) / (nxt - prev)
            out[t] = (1 - w) * f + w * b
        else:
            out[t] = f if f is not None else b
    return out


# ------------------------------------------------------------------ consistency check
def holdout_eval(frames: list[np.ndarray], keys: dict[int, np.ndarray], tests: list[int] | None = None,
                 method: str = "dis", backend: str = "flow", sam_args: dict | None = None) -> list[dict]:
    """Leave each test keyframe out, propagate from the others, compare with the withheld edited mask.
    The segment is cut at the nearest remaining keyframes on both sides so only the needed frames are processed."""
    rows = []
    tests = tests if tests is not None else sorted(keys)
    for tkey in tests:
        rest = {k: v for k, v in keys.items() if k != tkey}
        if not rest:
            continue
        lo_c = max([k for k in rest if k < tkey], default=None)
        hi_c = min([k for k in rest if k > tkey], default=None)
        lo = lo_c if lo_c is not None else tkey - 0
        hi = hi_c if hi_c is not None else tkey
        a = min(lo, tkey)
        b = max(hi, tkey)
        seg = frames[a:b + 1]
        sub = {k - a: v for k, v in rest.items() if a <= k <= b}
        if backend == "flow":
            pr = propagate_flow(seg, sub, method)
        else:
            pr = propagate_sam2(seg, sub, **(sam_args or {}))
        got = pr[tkey - a]
        gt = keys[tkey]
        near = min(rest, key=lambda k: abs(k - tkey))
        rows.append({"frame": int(tkey), "iou": sc.iou(got, gt), "f1_3px": sc.boundary_f1(got, gt, 3),
                     "baseline_copy_iou": sc.iou(rest[near], gt), "baseline_copy_f1_3px": sc.boundary_f1(rest[near], gt, 3),
                     "nearest_key": int(near), "gap_frames": int(abs(near - tkey)),
                     "left_key": lo_c, "right_key": hi_c})
    return rows


def summarise(rows: list[dict]) -> dict:
    if not rows:
        return {}
    a = lambda k: float(np.mean([r[k] for r in rows]))  # noqa: E731
    return {"n": len(rows), "iou_mean": a("iou"), "iou_min": float(min(r["iou"] for r in rows)),
            "f1_3px_mean": a("f1_3px"), "baseline_copy_iou_mean": a("baseline_copy_iou"),
            "baseline_copy_f1_3px_mean": a("baseline_copy_f1_3px")}


# ------------------------------------------------------------------ IO for packs
FNUM = re.compile(r"_f(\d+)")


def frame_no(name: str) -> int:
    m = FNUM.search(name)
    if not m:
        raise ValueError(f"no _fNNNNN in {name}")
    return int(m.group(1))


def read_masks(folder: Path) -> dict[int, np.ndarray]:
    out = {}
    for p in sorted(folder.glob("*.png")):
        try:
            idx = frame_no(p.name)
        except ValueError:
            continue
        m = cv2.imread(str(p), cv2.IMREAD_GRAYSCALE)
        if m is not None:
            out[idx] = m.astype(np.float32) / 255.0
    return out


def read_range(src: Path, a: int, b: int) -> list[np.ndarray]:
    cap = cv2.VideoCapture(str(src))
    cap.set(cv2.CAP_PROP_POS_FRAMES, a)
    out = []
    for _ in range(a, b + 1):
        ok, fr = cap.read()
        if not ok:
            break
        out.append(fr)
    cap.release()
    return out


def overlay(frame, m, alpha=0.45):
    mk = (m > 0.5)[..., None]
    red = np.zeros_like(frame)
    red[:, :] = (40, 40, 255)
    o = np.where(mk, (frame * (1 - alpha) + red * alpha).astype(np.uint8), frame)
    return o


def spans_frames(meta: dict) -> list[tuple[int, int]]:
    fps = meta["fps"]
    return [(int(round(a * fps)), int(round(b * fps))) for a, b in meta["spans_s"]] if meta["spans_s"] else []


def run_pack(pack: Path, keys_f: dict[int, np.ndarray], out: Path, backend: str, method: str, do_holdout: bool,
             sam_args: dict | None = None, label: str = "run", holdout_limit: int = 0) -> dict:
    meta = json.loads((pack / "keyframes.json").read_text(encoding="utf8"))
    src = Path(meta["source"])
    name = meta["name"]
    segs = spans_frames(meta)
    out.mkdir(parents=True, exist_ok=True)
    (out / "masks").mkdir(exist_ok=True)
    report = {"label": label, "backend": backend, "flow_method": method, "pack": str(pack), "segments": [], "holdout": []}
    t0 = time.time()
    previews = []
    for a, b in segs:
        ks = {k: v for k, v in keys_f.items() if a <= k <= b}
        if not ks:
            report["segments"].append({"span": [a, b], "skipped": "no keyframe in span"})
            continue
        frames = read_range(src, a, b)
        sub = {k - a: v for k, v in ks.items() if k - a < len(frames)}
        if backend == "flow":
            pr = propagate_flow(frames, sub, method)
        else:
            pr = propagate_sam2(frames, sub, **(sam_args or {}))
            if backend == "fused":
                fl = propagate_flow(frames, sub, method)
                pr = {t: band_refine(frames[t], 0.5 * pr[t] + 0.5 * fl[t]) if t not in sub else pr[t] for t in pr}
        for t, m in pr.items():
            cv2.imwrite(str(out / "masks" / f"{name}_f{a + t:05d}.png"), (np.clip(m, 0, 1) * 255).round().astype(np.uint8))
        for t in sorted(pr)[:: max(1, len(pr) // 6)]:
            previews.append((a + t, overlay(frames[t], pr[t])))
        if do_holdout and len(sub) >= 2:
            tl = sorted(sub)[1:-1] or sorted(sub)          # interior keyframes first: they have a neighbour on both sides
            if holdout_limit and len(tl) > holdout_limit:
                tl = [tl[int(round(i))] for i in np.linspace(0, len(tl) - 1, holdout_limit)]
            rows = holdout_eval(frames, sub, tl, method, backend if backend != "fused" else "flow", sam_args)
            for r in rows:
                r["frame"] += a
            report["holdout"] += rows
        report["segments"].append({"span": [a, b], "frames": len(frames), "keyframes": sorted(a + k for k in sub)})
    report["holdout_summary"] = summarise(report["holdout"])
    report["seconds"] = round(time.time() - t0, 1)
    if previews:
        tiles = []
        for idx, im in previews[:12]:
            s = 360 / im.shape[1]
            t = cv2.resize(im, None, fx=s, fy=s, interpolation=cv2.INTER_AREA)
            cv2.putText(t, f"f{idx}", (6, 18), cv2.FONT_HERSHEY_SIMPLEX, 0.5, (255, 255, 255), 1, cv2.LINE_AA)
            tiles.append(t)
        while len(tiles) % 3:
            tiles.append(np.zeros_like(tiles[0]))
        cv2.imwrite(str(out / "preview.jpg"), np.vstack([np.hstack(tiles[i:i + 3]) for i in range(0, len(tiles), 3)]))
    (out / "report.json").write_text(json.dumps(report, indent=1), encoding="utf8")
    return report


# ------------------------------------------------------------------ synthetic known-motion check
def synth_clip(img: np.ndarray, gt: np.ndarray, n: int = 25, seed: int = 1):
    """n frames of a smooth head-like motion applied to image and GT (affine + low-frequency elastic warp)."""
    h, w = gt.shape
    rng = np.random.default_rng(seed)
    gx, gy = np.meshgrid(np.arange(w, dtype=np.float32), np.arange(h, dtype=np.float32))
    fr, ms = [], []
    ph = rng.uniform(0, 6.28, 4)
    for t in range(n):
        u = t / (n - 1)
        ang = np.deg2rad(5.0 * np.sin(2 * np.pi * u * 0.75 + ph[0]))
        sc_ = 1.0 + 0.04 * np.sin(2 * np.pi * u * 0.5 + ph[1])
        tx = 0.03 * w * np.sin(2 * np.pi * u * 0.6 + ph[2])
        ty = 0.02 * h * np.sin(2 * np.pi * u * 0.4 + ph[3])
        M = cv2.getRotationMatrix2D((w / 2, h / 2), np.rad2deg(ang), sc_)
        M[0, 2] += tx
        M[1, 2] += ty
        a = cv2.warpAffine(img, M, (w, h), flags=cv2.INTER_CUBIC, borderMode=cv2.BORDER_REFLECT)
        g = cv2.warpAffine(gt.astype(np.float32), M, (w, h), flags=cv2.INTER_LINEAR)
        # low-frequency elastic part (about +-7 px at 1040 px), moves hair relative to the face a little
        ex = 7.0 * np.sin(gy / 190.0 + 3.0 * u) * np.sin(gx / 260.0 + 2.0 * u)
        ey = 5.0 * np.cos(gx / 210.0 - 2.5 * u)
        a = cv2.remap(a, gx + ex, gy + ey, cv2.INTER_CUBIC, borderMode=cv2.BORDER_REFLECT)
        g = cv2.remap(g, gx + ex, gy + ey, cv2.INTER_LINEAR)
        # mild photometric noise so that flow is not matching identical pixels
        a = np.clip(a.astype(np.float32) + rng.normal(0, 1.5, a.shape), 0, 255).astype(np.uint8)
        fr.append(a)
        ms.append((g > 0.5).astype(np.float32))
    return fr, ms


def run_synthetic(image: Path, gt_path: Path, out: Path, n: int, key_step: int, method: str) -> dict:
    img = cv2.imdecode(np.fromfile(str(image), np.uint8), 1)
    gt = cv2.imread(str(gt_path), 0).astype(np.float32) / 255.0
    h, w = gt.shape
    img = cv2.resize(img, (w, h), interpolation=cv2.INTER_CUBIC)
    ys, xs = np.where(gt > 0.5)
    S = 1040
    y0 = int(np.clip((ys.min() + ys.max()) // 2 - S // 2, 0, max(0, h - S)))
    x0 = int(np.clip((xs.min() + xs.max()) // 2 - S // 2, 0, max(0, w - S)))
    img, gt = img[y0:y0 + S, x0:x0 + S], gt[y0:y0 + S, x0:x0 + S]
    frames, truth = synth_clip(img, gt, n)
    keys_idx = list(range(0, n, key_step))
    if keys_idx[-1] != n - 1:
        keys_idx.append(n - 1)
    keys = {k: truth[k] for k in keys_idx}
    t0 = time.time()
    pr = propagate_flow(frames, keys, method)
    sec = time.time() - t0
    rows = [{"frame": t, "iou": sc.iou(pr[t], truth[t]), "f1_3px": sc.boundary_f1(pr[t], truth[t], 3)}
            for t in range(n) if t not in keys]
    ho = holdout_eval(frames, keys, [k for k in keys_idx[1:-1]], method)
    rep = {"label": "synthetic known motion on gf7 (PIPELINE CHECK, perfect-GT keyframes, not evidence on real video)",
           "crop": [x0, y0, S, S], "frames": n, "key_frames": keys_idx, "flow_method": method, "seconds": round(sec, 1),
           "non_key_frames": {"n": len(rows), "iou_mean": float(np.mean([r["iou"] for r in rows])),
                              "iou_min": float(min(r["iou"] for r in rows)),
                              "f1_3px_mean": float(np.mean([r["f1_3px"] for r in rows]))},
           "holdout_keyframes": ho, "holdout_summary": summarise(ho)}
    out.mkdir(parents=True, exist_ok=True)
    (out / "synthetic_report.json").write_text(json.dumps(rep, indent=1), encoding="utf8")
    mid = n // 2
    cv2.imwrite(str(out / "synthetic_mid.jpg"), np.hstack([overlay(frames[mid], truth[mid]), overlay(frames[mid], pr[mid])]))
    return rep


def main(argv=None) -> int:
    ap = argparse.ArgumentParser()
    sub = ap.add_subparsers(dest="cmd", required=True)
    for name in ("run", "pseudo"):
        p = sub.add_parser(name)
        p.add_argument("--pack", required=True)
        p.add_argument("--edited", help="folder with <stem>_edited.png (run only)")
        p.add_argument("--out")
        p.add_argument("--backend", default="flow", choices=("flow", "sam2", "fused"))
        p.add_argument("--flow", default="dis", choices=("dis", "farneback"))
        p.add_argument("--no-holdout", action="store_true")
        p.add_argument("--holdout-limit", type=int, default=0, help="at most N withheld keyframes per span (0 = all interior)")
        p.add_argument("--sam-ckpt", default=str(Path.home() / "Bossman" / "venv-sam2-cpu-tmp" / "sam2.1_hiera_tiny.pt"))
    s = sub.add_parser("synthetic")
    s.add_argument("--image", required=True)
    s.add_argument("--gt", required=True)
    s.add_argument("--out", required=True)
    s.add_argument("--frames", type=int, default=25)
    s.add_argument("--key-step", type=int, default=8)
    s.add_argument("--flow", default="dis", choices=("dis", "farneback"))
    a = ap.parse_args(argv)
    if a.cmd == "synthetic":
        r = run_synthetic(Path(a.image), Path(a.gt), Path(a.out), a.frames, a.key_step, a.flow)
        print(json.dumps({k: r[k] for k in ("label", "non_key_frames", "holdout_summary")}, indent=1))
        return 0
    pack = Path(a.pack)
    if a.cmd == "run":
        if not a.edited:
            ap.error("--edited is required for run")
        keys = read_masks(Path(a.edited))
        label = "run on edited keyframes"
    else:
        keys = read_masks(pack / "auto_masks")
        label = "PIPELINE CHECK: auto masks stand in for edited keyframes; IoU measures flow vs the auto mask, not vs a human"
    out = Path(a.out) if a.out else pack / ("propagated" if a.cmd == "run" else "pseudo_check")
    sam_args = {"tmp_dir": out / "_sam_frames", "ckpt": Path(a.sam_ckpt)} if a.backend != "flow" else None
    r = run_pack(pack, keys, out, a.backend, a.flow, not a.no_holdout, sam_args, label, a.holdout_limit)
    print(json.dumps({"label": r["label"], "holdout_summary": r["holdout_summary"], "seconds": r["seconds"]}, indent=1))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
