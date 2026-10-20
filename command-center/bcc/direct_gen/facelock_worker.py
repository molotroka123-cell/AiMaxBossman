"""FaceLock: put the face of the reference photo back, bit for bit, into an edited result.

Photo-edit models (Qwen-Image-Edit and friends) redraw the face and smear it. FaceLock takes the face from the
REFERENCE, aligns it to the face the model drew with a similarity transform fitted on the 68 landmarks, pastes it
through a convex-hull mask that is eroded a little and feathered, and relights it like the scene (one linear-light gain
per channel from the median of the face core). Everything outside the hull stays what the model drew.

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
FOREHEAD_LIFT = 0.08            # brows are lifted by this x (brow-to-chin) so the hull covers the brow ridge
ERODE = 0.055                   # hull erosion, x face width
FEATHER = 0.030                 # gaussian sigma of the edge, x face width
CORE_ERODE = 0.10               # colour statistics come from the face core: the hull eroded by this x face width
GAMMA = 2.2                     # sRGB-ish: gains are applied in linear light
CAST_RANGE = (0.6, 1.7)         # per-channel deviation from the common exposure gain: the scene's colour cast, bounded
TOP_FADE = 0.12                 # the upper edge (forehead/hairline has no landmarks) fades in over this x face width
EXPOSURE_RANGE = (0.03, 6.0)


@dataclass
class FaceInfo:
    box: tuple[float, float, float, float]
    lm68: np.ndarray            # (68, 2) float, x/y in the picture
    score: float                # detector confidence
    lm_score: float = 1.0       # landmarker confidence
    embedding: np.ndarray | None = None     # FaceFusion's normalised recognizer embedding (video gate only)


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


def _linear(img_bgr: np.ndarray) -> np.ndarray:
    return (img_bgr.astype(np.float32) / 255.0) ** GAMMA


def match_colour(warped: np.ndarray, result: np.ndarray, core: np.ndarray) -> tuple[np.ndarray, dict]:
    """Relight the warped reference face like the scene: ONE gain per channel in linear light = median of the face core
    in the model's picture / the same median in the reference. A multiplicative gain dims a studio-lit face to a sunset
    or a dark room without touching its structure or over-saturating lips and skin (a LAB lightness shift did), and the
    model's own face is the best witness of how this scene lights this person (hair or background at the rim are not)."""
    sel = core > 0
    if int(sel.sum()) < 50:
        return warped, {"colour": "skipped(core too small)"}
    w, r = _linear(warped), _linear(result)
    gain = (np.median(r[sel], axis=0) + 1e-4) / (np.median(w[sel], axis=0) + 1e-4)
    exposure = float(np.exp(np.log(gain).mean()))
    cast = np.clip(gain / exposure, *CAST_RANGE)                # a limited part of the scene's colour cast is followed
    gain = np.clip(exposure * cast, *EXPOSURE_RANGE)
    out = np.clip(w * gain, 0.0, 1.0) ** (1.0 / GAMMA)
    return np.clip(np.round(out * 255.0), 0, 255).astype(np.uint8), {
        "exposure_gain": round(exposure, 3), "cast_bgr": [round(float(v), 3) for v in cast]}


def facelock(reference: np.ndarray, result: np.ndarray, detect: Detector, *, min_score: float = MIN_DETECTOR_SCORE,
             min_lm_score: float = MIN_LANDMARK_SCORE,
             ref_faces: "list[FaceInfo] | None" = None) -> tuple[np.ndarray | None, dict]:
    """(locked BGR image, report) or (None, report with status 'skipped' and a reason). Inputs are never modified.
    ``ref_faces``: the reference's faces detected once already (video: one reference, many frames)."""
    ref_faces = detect(reference) if ref_faces is None else ref_faces
    res_faces = detect(result)
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
    k_core = max(2, int(round(CORE_ERODE * fw)))
    face_only = hull_mask((h, w), apply(m, ra))        # jaw to brows, without the lifted brow ridge
    core = cv2.erode(face_only, cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (2 * k_core + 1, 2 * k_core + 1)))
    adjusted, colour = match_colour(warped, result, core if int((core > 0).sum()) >= 200 else inner)
    sigma = max(0.8, FEATHER * fw)
    alpha = cv2.GaussianBlur(inner.astype(np.float32) / 255.0, (0, 0), sigma)
    alpha[(alpha < 0.004) | (mask0 == 0)] = 0.0         # the gaussian tail never leaves the hull (<2% step at its rim)
    top = float(pts[:, 1].min())                       # no landmark marks the hairline: the upper rim fades in softly
    alpha *= np.clip((np.arange(h, dtype=np.float32)[:, None] - top) / max(1.0, TOP_FADE * fw), 0.0, 1.0)
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
            emb = getattr(f, "embedding_norm", None)
            out.append(FaceInfo(tuple(float(v) for v in f.bounding_box), lm, float(f.score_set["detector"]),
                                float(f.score_set.get("landmarker", 1.0)),
                                None if emb is None else np.asarray(emb, np.float64)))
        return out
    return detect




# ----------------------------------------------------------------------------- video: many frames, one reference
#
# The video path reuses facelock() frame by frame (no other face model): the reference is detected ONCE, every frame of
# the clip gets the same treatment as a photo, and a frame FaceLock refuses keeps exactly what the model drew. The
# caller (bcc.direct_gen.video_facelock) decides from the counts whether the clip as a whole may ship locked.

PASSTHROUGH_OK = ("no_face_in_result",)   # the face left the frame: nothing to lock, the frame is right as it is


def _read(path: Path) -> np.ndarray | None:
    return cv2.imdecode(np.fromfile(str(path), np.uint8), cv2.IMREAD_COLOR)


def _write_png(path: Path, img: np.ndarray) -> bool:
    ok, buf = cv2.imencode(".png", img)
    if not ok:
        return False
    tmp = path.with_name(path.name + ".part")
    tmp.write_bytes(buf.tobytes())
    tmp.replace(path)
    return True


def lock_frames(reference: np.ndarray, frames: "list[tuple[str, np.ndarray]]", detect: Detector, write,
                progress=None) -> dict:
    """FaceLock on every frame. ``write(name, image)`` stores the frame that ships (locked, or the model's own);
    ``progress(done, total)`` after each frame. Returns counts + the per-frame status (``a`` applied, ``n`` no face in
    the frame, ``r`` refused) so the caller can pick gate samples among frames that really carry the locked face."""
    ref_faces = detect(reference)
    if len(ref_faces) != 1:
        reason = "no_face_in_reference" if not ref_faces else f"multiple_faces_unsupported n={len(ref_faces)}"
        return {"status": "skipped", "reason": reason, "frames": len(frames), "applied": 0}
    applied, no_face, refused, reasons, marks = 0, 0, 0, {}, []
    for i, (name, img) in enumerate(frames, start=1):
        locked, rep = facelock(reference, img, detect, ref_faces=ref_faces)
        if locked is not None:
            applied += 1
            marks.append("a")
            write(name, locked)
        else:
            key = str(rep.get("reason", "")).split(" ", 1)[0]
            if key in PASSTHROUGH_OK:
                no_face += 1
                marks.append("n")
            else:
                refused += 1
                marks.append("r")
                reasons[key] = reasons.get(key, 0) + 1
            write(name, img)
        if progress is not None:
            progress(i, len(frames))
    return {"status": "done", "reason": "", "frames": len(frames), "applied": applied, "no_face": no_face,
            "refused": refused, "refusals": reasons, "marks": "".join(marks)}


def cosine(a: np.ndarray, b: np.ndarray) -> float:
    a, b = np.asarray(a, np.float64).ravel(), np.asarray(b, np.float64).ravel()
    return float(a @ b / max(1e-12, float(np.linalg.norm(a) * np.linalg.norm(b))))


def similarities(reference: np.ndarray, samples: "list[tuple[str, np.ndarray]]", detect: Detector) -> dict:
    """Identity similarity (cosine of FaceFusion's recognizer embeddings) of the reference face and the face in each
    sampled frame. A sample without exactly one face, or without an embedding, is reported as not measured (None)."""
    ref = detect(reference)
    if len(ref) != 1 or ref[0].embedding is None:
        return {"status": "skipped", "reason": "reference_not_measurable", "samples": []}
    out = []
    for name, img in samples:
        faces = detect(img)
        if len(faces) != 1 or faces[0].embedding is None:
            out.append({"file": name, "similarity": None, "reason": f"faces={len(faces)}"})
        else:
            out.append({"file": name, "similarity": round(cosine(ref[0].embedding, faces[0].embedding), 4), "reason": ""})
    return {"status": "done", "reason": "", "samples": out}


def _frames_in(folder: Path) -> list[Path]:
    return sorted(p for p in folder.iterdir() if p.suffix.lower() == ".png")


def main_frames(ref_p: Path, frames_dir: Path, out_dir: Path, rep_p: Path, detect: Detector) -> int:
    reference = _read(ref_p)
    if reference is None:
        rep_p.write_text(json.dumps({"status": "skipped", "reason": "unreadable_reference"}), encoding="utf-8")
        return 0
    out_dir.mkdir(parents=True, exist_ok=True)
    paths = _frames_in(frames_dir)
    frames = []
    for p in paths:
        img = _read(p)
        if img is None:
            rep_p.write_text(json.dumps({"status": "skipped", "reason": f"unreadable_frame {p.name}"}), encoding="utf-8")
            return 0
        frames.append((p.name, img))

    def write(name: str, img: np.ndarray) -> None:
        if not _write_png(out_dir / name, img):
            raise RuntimeError(f"encode_failed {name}")

    def progress(done: int, total: int) -> None:
        print(f"FACELOCK {done}/{total}", flush=True)

    report = lock_frames(reference, frames, detect, write, progress)
    rep_p.write_text(json.dumps(report), encoding="utf-8")
    return 0


def main_similarity(ref_p: Path, samples_dir: Path, rep_p: Path, detect: Detector) -> int:
    reference = _read(ref_p)
    samples = [(p.name, img) for p in _frames_in(samples_dir) if (img := _read(p)) is not None]
    report = (similarities(reference, samples, detect) if reference is not None
              else {"status": "skipped", "reason": "unreadable_reference", "samples": []})
    rep_p.write_text(json.dumps(report), encoding="utf-8")
    return 0


def main(argv: list[str]) -> int:
    """photo: reference result out report.json
    video frames: --frames reference frames_dir out_dir report.json   (prints «FACELOCK i/n» per frame)
    video gate:   --similarity reference samples_dir report.json"""
    if len(argv) == 5 and argv[0] == "--frames":
        return main_frames(Path(argv[1]), Path(argv[2]), Path(argv[3]), Path(argv[4]), facefusion_detector())
    if len(argv) == 4 and argv[0] == "--similarity":
        return main_similarity(Path(argv[1]), Path(argv[2]), Path(argv[3]), facefusion_detector())
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
