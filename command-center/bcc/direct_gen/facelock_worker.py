"""FaceLock: put the face of the reference photo back, bit for bit, into an edited result.

Photo-edit models (Qwen-Image-Edit and friends) redraw the face and smear it. FaceLock takes the face from the
REFERENCE, aligns it to the face the model drew with a similarity transform fitted on the 68 landmarks, pastes it
through a convex-hull mask that is eroded a little and feathered, and matches colour (LAB mean/std) on the seam band
only. Everything outside the hull stays what the model drew.

Never guesses. It refuses (and says why) when: a face is missing in either picture, the face counts differ, there is
more than one face, a detector/landmark score is low, the landmark shapes do not fit each other (another head pose),
or the transform is absurd. A refusal writes no output file; the caller keeps the unlocked result.

Run with FaceFusion's interpreter (it provides numpy, OpenCV and the detector models; no cloud, CPU only):
    <facefusion>/.venv/Scripts/python.exe facelock_worker.py reference.png result.png out.png report.json
The pure functions below take a `detect` callable, so tests inject landmarks without any model.
"""
from __future__ import annotations

import json
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Callable

import cv2
import numpy as np

MIN_DETECTOR_SCORE = 0.60       # FaceFusion yolo_face; real faces score 0.8+, the detector floor is 0.5
MIN_LANDMARK_SCORE = 0.50
MAX_RESIDUAL = 0.22             # landmark misfit after the similarity fit, in units of the inter-ocular distance
SCALE_RANGE = (0.2, 5.0)        # reference face size / result face size
MAX_ROTATION_DEG = 50.0
FOREHEAD_LIFT = 0.18            # brows are lifted by this x (brow-to-chin) so the hull covers the brow ridge
ERODE = 0.055                   # hull erosion, x face width
FEATHER = 0.030                 # gaussian sigma of the edge, x face width
SEAM_BAND = 0.07                # colour statistics are read from this inner band, x face width
GAIN_RANGE = (0.92, 1.08)       # contrast correction is clamped: the reference face must stay the reference face


@dataclass
class FaceInfo:
    box: tuple[float, float, float, float]
    lm68: np.ndarray            # (68, 2) float, x/y in the picture
    score: float                # detector confidence
    lm_score: float = 1.0       # landmarker confidence


Detector = Callable[[np.ndarray], "list[FaceInfo]"]


def _skip(reason: str, **extra) -> tuple[None, dict]:
    return None, {"status": "skipped", "reason": reason, **extra}


def eye_distance(lm: np.ndarray) -> float:
    return float(np.linalg.norm(lm[36:42].mean(0) - lm[42:48].mean(0)))


def fit_similarity(src: np.ndarray, dst: np.ndarray) -> np.ndarray:
    """Umeyama least squares: the 2x3 matrix M (scale * rotation + shift, no reflection) with dst ~ M @ [src, 1]."""
    src, dst = np.asarray(src, np.float64), np.asarray(dst, np.float64)
    mu_s, mu_d = src.mean(0), dst.mean(0)
    s0, d0 = src - mu_s, dst - mu_d
    cov = d0.T @ s0 / len(src)
    u, sig, vt = np.linalg.svd(cov)
    fix = np.eye(2)
    if np.linalg.det(u) * np.linalg.det(vt) < 0:
        fix[1, 1] = -1
    rot = u @ fix @ vt
    scale = float((sig * np.diag(fix)).sum() / max(1e-12, (s0 ** 2).sum() / len(src)))
    shift = mu_d - scale * rot @ mu_s
    return np.hstack([scale * rot, shift[:, None]])


def apply(m: np.ndarray, pts: np.ndarray) -> np.ndarray:
    return np.asarray(pts, np.float64) @ m[:, :2].T + m[:, 2]


def hull_points(lm: np.ndarray, lift: float = FOREHEAD_LIFT) -> np.ndarray:
    """68 landmarks with the brows moved up: the convex hull then also covers the brow ridge and a bit of forehead."""
    pts = np.asarray(lm, np.float64).copy()
    up = lift * (lm[8, 1] - lm[17:27, 1].mean())
    pts[17:27, 1] -= up
    return pts


def hull_mask(shape: tuple[int, int], pts: np.ndarray) -> np.ndarray:
    mask = np.zeros(shape[:2], np.uint8)
    cv2.fillConvexPoly(mask, cv2.convexHull(np.round(pts).astype(np.int32)), 255)
    return mask


def _lab(img_bgr: np.ndarray) -> np.ndarray:
    return cv2.cvtColor(img_bgr, cv2.COLOR_BGR2LAB).astype(np.float32)


def match_colour(warped: np.ndarray, result: np.ndarray, band: np.ndarray) -> tuple[np.ndarray, dict]:
    """Shift/scale the warped reference face in LAB so its seam band has the result's mean and (clamped) std."""
    sel = band > 0
    if int(sel.sum()) < 50:
        return warped, {"colour": "skipped(band too small)"}
    w_lab, r_lab = _lab(warped), _lab(result)
    mu_w, sd_w = w_lab[sel].mean(0), w_lab[sel].std(0) + 1e-3
    mu_r, sd_r = r_lab[sel].mean(0), r_lab[sel].std(0) + 1e-3
    gain = np.clip(sd_r / sd_w, *GAIN_RANGE)
    out = (w_lab - mu_w) * gain + mu_r
    shifted = cv2.cvtColor(np.clip(out, 0, 255).astype(np.uint8), cv2.COLOR_LAB2BGR)
    return shifted, {"lab_shift": [round(float(v), 2) for v in (mu_r - mu_w)], "lab_gain": [round(float(v), 3) for v in gain]}


def facelock(reference: np.ndarray, result: np.ndarray, detect: Detector, *, min_score: float = MIN_DETECTOR_SCORE,
             min_lm_score: float = MIN_LANDMARK_SCORE) -> tuple[np.ndarray | None, dict]:
    """(locked BGR image, report) or (None, report with status 'skipped' and a reason). Inputs are never modified."""
    ref_faces, res_faces = detect(reference), detect(result)
    n_ref, n_res = len(ref_faces), len(res_faces)
    counts = {"faces_reference": n_ref, "faces_result": n_res}
    if n_ref == 0:
        return _skip("no_face_in_reference", **counts)
    if n_res == 0:
        return _skip("no_face_in_result", **counts)
    if n_ref != n_res:
        return _skip(f"face_count_mismatch ref={n_ref} result={n_res}", **counts)
    if n_ref > 1:
        return _skip(f"multiple_faces_unsupported n={n_ref}", **counts)
    a, b = ref_faces[0], res_faces[0]
    scores = {"score_reference": round(float(a.score), 3), "score_result": round(float(b.score), 3)}
    if min(a.score, b.score) < min_score:
        return _skip(f"low_detector_confidence {min(a.score, b.score):.2f}<{min_score:.2f}", **counts, **scores)
    if min(a.lm_score, b.lm_score) < min_lm_score:
        return _skip(f"low_landmark_confidence {min(a.lm_score, b.lm_score):.2f}<{min_lm_score:.2f}", **counts, **scores)
    ra, rb = np.asarray(a.lm68, np.float64), np.asarray(b.lm68, np.float64)
    if ra.shape != (68, 2) or rb.shape != (68, 2) or not np.isfinite(ra).all() or not np.isfinite(rb).all():
        return _skip("bad_landmarks", **counts, **scores)
    m = fit_similarity(ra, rb)
    scale = float(np.hypot(m[0, 0], m[1, 0]))
    angle = float(np.degrees(np.arctan2(m[1, 0], m[0, 0])))
    resid = float(np.linalg.norm(apply(m, ra) - rb, axis=1).mean() / max(1e-6, eye_distance(rb)))
    geom = {"scale": round(scale, 3), "rotation_deg": round(angle, 1), "residual": round(resid, 3)}
    if not SCALE_RANGE[0] <= scale <= SCALE_RANGE[1]:
        return _skip(f"implausible_scale {scale:.2f}", **counts, **scores, **geom)
    if abs(angle) > MAX_ROTATION_DEG:
        return _skip(f"implausible_rotation {angle:.0f}deg", **counts, **scores, **geom)
    if resid > MAX_RESIDUAL:
        return _skip(f"faces_do_not_match_pose residual={resid:.2f}>{MAX_RESIDUAL}", **counts, **scores, **geom)

    h, w = result.shape[:2]
    src, m_use = reference, m
    if scale < 0.75:                                   # shrinking a big reference: area-resample first, no aliasing
        src = cv2.resize(reference, None, fx=scale, fy=scale, interpolation=cv2.INTER_AREA)
        m_use = np.hstack([m[:, :2] / scale, m[:, 2:]])      # small-reference coordinates -> result coordinates
    warped = cv2.warpAffine(src, m_use, (w, h), flags=cv2.INTER_CUBIC, borderMode=cv2.BORDER_REFLECT)
    pts = apply(m, hull_points(ra))
    mask0 = hull_mask((h, w), pts)
    if int(mask0.sum() // 255) < 400:
        return _skip("face_too_small", **counts, **scores, **geom)
    fw = float(np.ptp(apply(m, ra)[:, 0]))             # face width in result pixels
    k_er = max(1, int(round(ERODE * fw)))
    inner = cv2.erode(mask0, cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (2 * k_er + 1, 2 * k_er + 1)))
    k_band = max(2, int(round(SEAM_BAND * fw)))
    deep = cv2.erode(inner, cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (2 * k_band + 1, 2 * k_band + 1)))
    band = cv2.subtract(inner, deep)
    adjusted, colour = match_colour(warped, result, band)
    sigma = max(0.8, FEATHER * fw)
    alpha = cv2.GaussianBlur(inner.astype(np.float32) / 255.0, (0, 0), sigma)
    alpha[(alpha < 0.004) | (mask0 == 0)] = 0.0         # the gaussian tail never leaves the hull (<2% step at its rim)
    alpha = alpha[..., None]
    out = (adjusted.astype(np.float32) * alpha + result.astype(np.float32) * (1.0 - alpha))
    locked = np.clip(np.round(out), 0, 255).astype(np.uint8)
    report = {"status": "applied", "reason": "", **counts, **scores, **geom, **colour,
              "face_width_px": round(fw, 1), "mask_pixels": int((inner > 0).sum())}
    return locked, report


# ----------------------------------------------------------------------------- FaceFusion detector + CLI


def facefusion_detector() -> Detector:
    sys.path.insert(0, str(Path.cwd()))
    from facefusion import state_manager
    for k, v in {"execution_providers": ["cpu"], "execution_device_ids": ["0"], "download_providers": ["github", "huggingface"],
                 "face_detector_model": "yolo_face", "face_detector_size": "640x640", "face_detector_angles": [0],
                 "face_detector_margin": [0, 0, 0, 0], "face_detector_score": 0.5, "face_landmarker_model": "2dfan4",
                 "face_landmarker_score": 0.5, "log_level": "error"}.items():
        state_manager.init_item(k, v)
    from facefusion.face_creator import get_many_faces

    def detect(img: np.ndarray) -> list[FaceInfo]:
        out = []
        for f in get_many_faces([img]):
            lm = np.asarray(f.landmark_set["68"], np.float64)
            out.append(FaceInfo(tuple(float(v) for v in f.bounding_box), lm, float(f.score_set["detector"]),
                                float(f.score_set.get("landmarker", 1.0))))
        return out
    return detect


def main(argv: list[str]) -> int:
    if len(argv) != 4:
        print("usage: facelock_worker.py reference result out report.json", file=sys.stderr)
        return 2
    ref_p, res_p, out_p, rep_p = (Path(a) for a in argv)
    reference = cv2.imdecode(np.fromfile(str(ref_p), np.uint8), cv2.IMREAD_COLOR)
    result = cv2.imdecode(np.fromfile(str(res_p), np.uint8), cv2.IMREAD_COLOR)
    if reference is None or result is None:
        rep_p.write_text(json.dumps({"status": "skipped", "reason": "unreadable_image"}), encoding="utf-8")
        return 0
    locked, report = facelock(reference, result, facefusion_detector())
    if locked is not None:
        ok, buf = cv2.imencode(".png", locked)
        if not ok:
            report = {"status": "skipped", "reason": "encode_failed"}
        else:
            out_p.write_bytes(buf.tobytes())
    rep_p.write_text(json.dumps(report), encoding="utf-8")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
