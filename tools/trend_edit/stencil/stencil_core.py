"""Pure helpers of the hair-stencil workflow (numpy; OpenCV is imported lazily where a pixel op needs it).

Everything here is deterministic: no global RNG, k-means is seeded and uses its own implementation so the keyframe
choice for a clip is reproducible (tests/test_hair_stencil.py).
"""
from __future__ import annotations

import numpy as np

DARK_L = 30.0          # CIE L* (0..100) below which a pixel counts as "dark clothing / dark background"
NEAR_PX = 12           # "adjacent" = within this many pixels (at 720p-class source resolution)


def _cv2():
    import cv2
    return cv2


# ------------------------------------------------------------------ metrics
def iou(a: np.ndarray, b: np.ndarray) -> float:
    a = np.asarray(a) > 0.5
    b = np.asarray(b) > 0.5
    u = np.logical_or(a, b).sum()
    return 1.0 if u == 0 else float(np.logical_and(a, b).sum() / u)


def boundary_f1(a: np.ndarray, b: np.ndarray, tol: float = 3.0) -> float:
    """F1 of boundary pixels of two binary masks, a boundary pixel matches when the other boundary is within `tol` px."""
    cv2 = _cv2()
    a = (np.asarray(a) > 0.5).astype(np.uint8)
    b = (np.asarray(b) > 0.5).astype(np.uint8)
    k = np.ones((3, 3), np.uint8)
    ba = a - cv2.erode(a, k)
    bb = b - cv2.erode(b, k)
    if ba.sum() == 0 and bb.sum() == 0:
        return 1.0
    if ba.sum() == 0 or bb.sum() == 0:
        return 0.0
    da = cv2.distanceTransform((1 - ba).astype(np.uint8), cv2.DIST_L2, 3)
    db = cv2.distanceTransform((1 - bb).astype(np.uint8), cv2.DIST_L2, 3)
    prec = float((db[ba > 0] <= tol).mean())
    rec = float((da[bb > 0] <= tol).mean())
    return 0.0 if prec + rec == 0 else 2 * prec * rec / (prec + rec)


def guided_filter(guide: np.ndarray, src: np.ndarray, radius: int, eps: float) -> np.ndarray:
    """He et al. guided filter, grey guide, float32 0..1 (same maths as parsing_worker.guided_filter, kept local so that
    this module has no model dependencies)."""
    cv2 = _cv2()
    guide = guide.astype(np.float32)
    src = src.astype(np.float32)
    k = (2 * radius + 1, 2 * radius + 1)

    def mean(x):
        return cv2.boxFilter(x, cv2.CV_32F, k)

    m_i, m_p = mean(guide), mean(src)
    cov = mean(guide * src) - m_i * m_p
    var = mean(guide * guide) - m_i * m_i
    a = cov / (var + eps)
    b = m_p - a * m_i
    return mean(a) * guide + mean(b)


# ------------------------------------------------------------------ keyframe picking
def kmeans(X: np.ndarray, k: int, seed: int = 0, iters: int = 50) -> tuple[np.ndarray, np.ndarray]:
    """Deterministic k-means++ (numpy). Returns (labels, centres)."""
    X = np.asarray(X, np.float64)
    n = len(X)
    k = max(1, min(k, n))
    rng = np.random.default_rng(seed)
    centres = [X[int(rng.integers(n))]]
    for _ in range(1, k):
        d2 = np.min(((X[:, None, :] - np.array(centres)[None]) ** 2).sum(-1), axis=1)
        if d2.sum() <= 1e-12:
            centres.append(X[int(rng.integers(n))])
            continue
        centres.append(X[int(rng.choice(n, p=d2 / d2.sum()))])
    C = np.array(centres)
    lab = np.zeros(n, int)
    for it in range(iters):
        d = ((X[:, None, :] - C[None]) ** 2).sum(-1)
        new = d.argmin(1)
        if it and (new == lab).all():
            break
        lab = new
        for j in range(k):
            if (lab == j).any():
                C[j] = X[lab == j].mean(0)
    return lab, C


def standardise(F: np.ndarray, weights: np.ndarray | None = None) -> np.ndarray:
    F = np.asarray(F, np.float64)
    sd = F.std(0)
    sd[sd < 1e-9] = 1.0
    Z = (F - F.mean(0)) / sd
    return Z * (weights if weights is not None else 1.0)


def kcenter(Z: np.ndarray, k: int, min_gap: int = 1, include_ends: bool = True) -> list[int]:
    """Greedy farthest-point (k-center) choice: minimises the WORST distance from any frame to its nearest keyframe, which is
    what limits a propagation (k-means minimises the mean and leaves the rare poses uncovered). Deterministic."""
    n = len(Z)
    chosen = [0, n - 1] if include_ends and n > 1 else [int(np.argmax(((Z - Z.mean(0)) ** 2).sum(1)))]
    chosen = sorted(set(chosen))
    d = np.min(((Z[:, None, :] - Z[chosen][None]) ** 2).sum(-1), axis=1)
    while len(chosen) < min(k, n):
        ok = np.array([all(abs(i - c) >= min_gap for c in chosen) for i in range(n)])
        if not ok.any():
            break
        cand = np.where(ok, d, -1.0)
        j = int(np.argmax(cand))
        chosen.append(j)
        d = np.minimum(d, ((Z - Z[j]) ** 2).sum(1))
    # local search: swap a non-end pick for any frame when that lowers the worst gap (greedy alone is only a 2-approximation)
    D = ((Z[:, None, :] - Z[None]) ** 2).sum(-1)
    fixed = {0, n - 1} if include_ends else set()

    def cost(sel):
        m = D[:, sel].min(1)
        return (float(m.max()), float(m.mean()))

    best = cost(chosen)
    for _ in range(6):
        improved = False
        for ci, c in enumerate(list(chosen)):
            if c in fixed:
                continue
            for cand in range(n):
                if cand in chosen or any(abs(cand - o) < min_gap for o in chosen if o != c):
                    continue
                trial = chosen[:ci] + [cand] + chosen[ci + 1:]
                cc = cost(trial)
                if cc < best:
                    chosen, best, improved = trial, cc, True
        if not improved:
            break
    return sorted(chosen)


def pick_keyframes(features: np.ndarray, k: int = 9, min_gap: int = 2, seed: int = 0,
                   weights: np.ndarray | None = None, include_ends: bool = True, method: str = "kcenter") -> list[int]:
    """Indices (into the sampled-frame list, ascending) of `k` frames that cover the feature space.

    method "kcenter" (default): greedy farthest-point, minimises the worst-case gap. method "kmeans":
    k-means on standardised features, then per cluster the member closest to the centre (medoid). With include_ends the first
    and last sample replace the nearest picks, so a propagation never has to extrapolate past a keyframe.
    `min_gap` = minimum distance in sample indices between two picks (near-duplicates are useless to an editor)."""
    Z = standardise(features, weights)
    n = len(Z)
    k = min(k, n)
    if n <= k:
        return list(range(n))
    if method == "kcenter":
        return kcenter(Z, k, min_gap, include_ends)
    lab, C = kmeans(Z, k, seed)
    picks: list[int] = []
    for j in range(len(C)):
        mem = np.where(lab == j)[0]
        if len(mem) == 0:
            continue
        d = ((Z[mem] - C[j]) ** 2).sum(1)
        picks.append(int(mem[int(np.argmin(d))]))
    if include_ends:
        for e in (0, n - 1):
            if e not in picks:
                near = min(picks, key=lambda p: abs(p - e))
                if abs(near - e) <= max(min_gap, n // (2 * k)):
                    picks[picks.index(near)] = e
                else:
                    picks.append(e)
    picks = sorted(set(picks))
    ends = {0, n - 1} if include_ends else set()
    while len(picks) > k:                    # ends were added on top: drop the most redundant non-end pick
        cand = [p for p in picks if p not in ends]
        if not cand:
            break
        def crowd(p):
            others = [q for q in picks if q != p]
            return min(((Z[p] - Z[q]) ** 2).sum() for q in others)
        picks.remove(min(cand, key=crowd))
    out: list[int] = []
    for p in picks:
        if out and p - out[-1] < min_gap:
            if p == n - 1 and include_ends:
                out[-1] = p
            continue
        out.append(p)
    while len(out) < k:                      # top up with the farthest-point rule if the gap rule removed some
        rest = [i for i in range(n) if all(abs(i - o) >= min_gap for o in out)]
        if not rest:
            break
        far = max(rest, key=lambda i: min(((Z[i] - Z[o]) ** 2).sum() for o in out))
        out.append(far)
    return sorted(out)


def mask_pose_features(hair: np.ndarray, skin: np.ndarray) -> dict[str, float]:
    """Head-pose / hair-state proxies from two binary masks (no landmark model, no GPU).

    yaw   = horizontal offset of the hair centroid from the skin (face+neck) centroid / hair width
    pitch = vertical offset of the same / hair height
    These are NOT degrees: they order frames by pose change, which is all the picker needs."""
    h = np.asarray(hair) > 0.5
    s = np.asarray(skin) > 0.5
    H, W = h.shape
    ys, xs = np.where(h)
    if len(ys) < 50:
        return {"area": 0.0, "yaw": 0.0, "pitch": 0.0, "width": 0.0, "height": 0.0, "skin_ratio": 0.0, "ok": 0.0}
    y0, y1, x0, x1 = ys.min(), ys.max(), xs.min(), xs.max()
    hh, hw = max(1, y1 - y0), max(1, x1 - x0)
    gy0, gy1 = max(0, int(y0 - 0.3 * hh)), min(H, int(y1 + 0.3 * hh))
    gx0, gx1 = max(0, int(x0 - 0.3 * hw)), min(W, int(x1 + 0.3 * hw))
    sy, sx = np.where(s[gy0:gy1, gx0:gx1])
    if len(sy) < 30:
        return {"area": float(h.sum() / (hh * hw)), "yaw": 0.0, "pitch": 0.0, "width": hw / W, "height": hh / H,
                "skin_ratio": 0.0, "ok": 0.5}
    scx, scy = sx.mean() + gx0, sy.mean() + gy0
    return {"area": float(h.sum() / (hh * hw)), "yaw": float((xs.mean() - scx) / hw), "pitch": float((ys.mean() - scy) / hh),
            "width": hw / W, "height": hh / H, "skin_ratio": float(len(sy) / max(1, h.sum())), "ok": 1.0}


def shape_descriptor(hair: np.ndarray, n: int = 12) -> np.ndarray:
    """n x n occupancy of the hair mask inside its own bbox, flattened (the 'hair state': parted, swept, tied...)."""
    cv2 = _cv2()
    h = (np.asarray(hair) > 0.5).astype(np.float32)
    ys, xs = np.where(h > 0)
    if len(ys) < 50:
        return np.zeros(n * n, np.float32)
    crop = h[ys.min():ys.max() + 1, xs.min():xs.max() + 1]
    return cv2.resize(crop, (n, n), interpolation=cv2.INTER_AREA).reshape(-1)


def frame_features(hair: np.ndarray, skin: np.ndarray) -> np.ndarray:
    """[yaw, pitch, fill, width, height, centre_x, centre_y, abs_area] of one frame (sizes / centre as fractions of the frame)."""
    p = mask_pose_features(hair, skin)
    ys, xs = np.where(np.asarray(hair) > 0.5)
    H, W = np.asarray(hair).shape
    cx = float((xs.min() + xs.max()) / 2 / W) if len(xs) else 0.0
    cy = float((ys.min() + ys.max()) / 2 / H) if len(ys) else 0.0
    abs_area = float((np.asarray(hair) > 0.5).sum() / (H * W))
    return np.array([p["yaw"], p["pitch"], p["area"], p["width"], p["height"], cx, cy, abs_area], np.float64)


def clip_feature_matrix(per_frame: list[np.ndarray], descriptors: list[np.ndarray], pca: int = 3) -> np.ndarray:
    """Stack frame features + the first `pca` principal components of the shape descriptors (sign-fixed: reproducible)."""
    F = np.array(per_frame, np.float64)
    D = np.array(descriptors, np.float64)
    if len(D) > pca + 1 and D.std() > 1e-9:
        Dc = D - D.mean(0)
        _u, _s, vt = np.linalg.svd(Dc, full_matrices=False)
        P = Dc @ vt[:pca].T
        for j in range(P.shape[1]):
            if vt[j][np.argmax(np.abs(vt[j]))] < 0:
                P[:, j] *= -1
        F = np.hstack([F, P])
    return F


# ------------------------------------------------------------------ source separability
def separability(hair: np.ndarray, skin: np.ndarray, L: np.ndarray, dark: float = DARK_L, near: int = NEAR_PX) -> dict[str, float]:
    """How hard is this frame's hair/non-hair boundary for a colour-blind matte?

    boundary = outline of the hair mask. A boundary pixel is 'dark-adjacent' when a dark (L* < `dark`) NON-hair pixel lies
    within `near` px. dark_clothes_bg = such pixels away from skin class (black hoodie, dark room); neck_shadow = dark non-hair
    pixels hugging the skin class (shadow under the chin, on the neck): the two error sources of the 11.10 bench.
    score = 1 - (dark_clothes_bg + 0.5 * neck_shadow), clipped to 0..1 (higher = easier)."""
    cv2 = _cv2()
    h = (np.asarray(hair) > 0.5).astype(np.uint8)
    if h.sum() < 50:
        return {"score": 0.0, "dark_clothes_bg": 1.0, "neck_shadow": 1.0, "boundary_px": 0, "ok": 0}
    s = (np.asarray(skin) > 0.5).astype(np.uint8)
    edge = (h - cv2.erode(h, np.ones((3, 3), np.uint8))) > 0
    ys, xs = np.where(h > 0)
    hh, hw = ys.max() - ys.min(), xs.max() - xs.min()
    y0, y1 = max(0, int(ys.min() - 0.25 * hh)), min(h.shape[0], int(ys.max() + 0.25 * hh))
    x0, x1 = max(0, int(xs.min() - 0.25 * hw)), min(h.shape[1], int(xs.max() + 0.25 * hw))
    box = np.zeros(h.shape, bool)
    box[y0:y1, x0:x1] = True
    dark_nh = (np.asarray(L) < dark) & (h == 0) & box
    # drop thin dark leftovers (flyaway strands and fringe pixels the hair mask missed are DARK too, but they are hair, not
    # clothing): an opening whose size scales with the head keeps only blobs (hoodie, shadow, dark room)
    ko = max(5, int(round(hh / 30)) | 1)
    dark_nh = cv2.morphologyEx(dark_nh.astype(np.uint8), cv2.MORPH_OPEN, cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (ko, ko))) > 0
    skin_near = cv2.dilate(s, np.ones((9, 9), np.uint8)) > 0
    d_skin = dark_nh & skin_near
    d_other = dark_nh & ~skin_near
    ker = cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (2 * near + 1, 2 * near + 1))
    near_other = cv2.dilate(d_other.astype(np.uint8), ker) > 0
    near_skin = cv2.dilate(d_skin.astype(np.uint8), ker) > 0
    fo = float(near_other[edge].mean())
    fs = float(near_skin[edge].mean())
    return {"score": float(np.clip(1.0 - (fo + 0.5 * fs), 0, 1)), "dark_clothes_bg": fo, "neck_shadow": fs,
            "boundary_px": int(edge.sum()), "ok": 1}
