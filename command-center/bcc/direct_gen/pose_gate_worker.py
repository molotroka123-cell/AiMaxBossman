"""Pose gate for the video face swap: keep the source face where the head is bowed toward the camera.

FaceFusion's detector stays confident on a bowed head (10.10, two-person clip, 10.1-11.5 s: detector 0.78-0.80) and
pastes a frontal face onto the crown — eyes and brows appear on the forehead. The head pitch is what separates those
frames: pitch = (nose_y - eyes_y) / (chin_y - eyes_y) on the 68 landmarks of the SOURCE frame's largest face, measured
0.54-0.62 bowed vs 0.32-0.46 normal. Per frame:
    alpha = clip((off - pitch) / (off - on), 0, 1);  out = alpha * swapped + (1 - alpha) * source
so the swap fades out/in over the on..off band instead of popping. Frames without a face keep the swapped frame.
Then a band around the target's hairline keeps the target (hairline_guard: the swapper paints the source person's fringe
there), and everything outside the padded face boxes is restored from the source exactly (keep_source_outside_faces).

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


HAIR = 17                 # CelebAMask class of the face parser
HAIRLINE_GUARD = 0.06     # x face side


def hairline_guard(source, processed, hair_mask, side: float):
    """The swapper paints the source person's fringe/sideburns into the target's skin at the hairline (10.10, kisliy refs:
    a dark smudge on the temple with every swapper/padding/enhancer setting). A band of HAIRLINE_GUARD x face side around
    the TARGET's hair keeps the target's pixels, with a soft edge."""
    import cv2
    import numpy as np
    if not hair_mask.any():
        return processed
    g = max(3, int(HAIRLINE_GUARD * side))
    band = cv2.dilate(hair_mask.astype(np.uint8) * 255, cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (2 * g + 1, 2 * g + 1)))
    m = cv2.GaussianBlur(band.astype(np.float32) / 255.0, (0, 0), g / 2)[..., None]
    return (m * source.astype(np.float32) + (1 - m) * processed.astype(np.float32)).round().astype(np.uint8)


def parse_hair(frame, face, forward_parse_face):
    """Hair mask at frame size from the face parser run on a 1.6 x face crop."""
    import cv2
    import numpy as np
    h, w = frame.shape[:2]
    x1, y1, x2, y2 = [float(v) for v in face.bounding_box]
    cx, cy, sz = (x1 + x2) / 2, (y1 + y2) / 2, max(x2 - x1, y2 - y1) * 1.6
    bx1, by1, bx2, by2 = int(max(0, cx - sz / 2)), int(max(0, cy - sz / 2)), int(min(w, cx + sz / 2)), int(min(h, cy + sz / 2))
    prep = cv2.resize(frame[by1:by2, bx1:bx2], (512, 512))[:, :, ::-1].astype(np.float32) / 255.0
    prep = (prep - np.array([0.485, 0.456, 0.406], np.float32)) / np.array([0.229, 0.224, 0.225], np.float32)
    labels = forward_parse_face(prep.transpose(2, 0, 1)[None]).argmax(0).astype(np.uint8)
    full = np.zeros((h, w), bool)
    full[by1:by2, bx1:bx2] = cv2.resize((labels == HAIR).astype(np.uint8), (bx2 - bx1, by2 - by1),
                                        interpolation=cv2.INTER_NEAREST) > 0
    return full


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


# ---------------------------------------------------------------- V2 (10.10, owner: "visible face transition")
#
# Causes found by the frame-by-frame audit of the two-person clip (tools/video_gate/artifact_audit.py):
#   1. alpha flipped 0.29 -> 0.91 -> 0.84 -> 1.0 -> 0.78 -> 1.0 over 8 frames at 11.1-11.4 s: the raw pitch of a bowed head
#      jitters around the on/off band, so the original and the swapped face alternate — a flicker, then a pop;
#   2. the swapped face was 1-2 dL brighter than the shadowed neck and the engine's colour re-ranging leaked into the
#      restored padded box (dE 1.3-1.9 against the source outside the pasted face), a visible box edge;
# V2: smoothed pitch + a slew-limited ramp (no reversal inside an episode), lighting of the swapped face matched to the
# source's low frequencies, and only pixels that the engine really changed are taken from the engine's frame.

#: seconds for a full 0 -> 1 swap fade: long enough that the face does not pop, short enough that the head is not
#: swapped while bowed (alpha is a lower envelope: it never exceeds the raw value, so the fade starts BEFORE the bow)
RAMP_S = 0.40
PITCH_MEDIAN = 5          # frames
RELIGHT_SIGMA = 0.12      # x face side: scale of the lighting that is transferred
RELIGHT_MAX_L = 22.0      # Lab L units
RELIGHT_AB_MAX = 8.0
RELIGHT_L_GAIN, RELIGHT_AB_GAIN = 1.0, 0.6
#: x face side: pasted-face region around the landmark hull (variants a-e measured on slices of the 10.10 clips:
#: 0.12 / 0.09 gave the softest seam with the same colour gain; the engine's own paste fades inside it)
REGION_DILATE, REGION_FEATHER = 0.12, 0.09
FOREHEAD_UP = 0.45                           # x (chin - brow): the swapper also repaints the forehead
CHANGE_LO, CHANGE_HI = 6.0, 18.0     # 0..255: below = the engine did not change the pixel (codec noise)


def median_pitch(pitches: list, radius: int = PITCH_MEDIAN // 2) -> list:
    """Temporal median over +-radius frames (None ignored); a gap of up to 2*radius frames takes its nearest value."""
    import numpy as np
    n = len(pitches)
    out: list = [None] * n
    for i in range(n):
        window = [p for p in pitches[max(0, i - radius):i + radius + 1] if p is not None]
        if window:
            out[i] = float(np.median(window))
    for i in range(n):
        if out[i] is None:
            for d in range(1, 2 * radius + 1):
                for j in (i - d, i + d):
                    if 0 <= j < n and out[j] is not None:
                        out[i] = out[j]
                        break
                if out[i] is not None:
                    break
    return out


def slew_limit(raw: list[float], step: float) -> list[float]:
    """Lower envelope with at most `step` change per frame in both directions: alpha never exceeds raw."""
    a = list(raw)
    for i in range(1, len(a)):
        a[i] = min(a[i], a[i - 1] + step)
    for i in range(len(a) - 2, -1, -1):
        a[i] = min(a[i], a[i + 1] + step)
    return a


def alpha_track(pitches: list, fps: float, on: float = PITCH_ON, off: float = PITCH_OFF, ramp_s: float = RAMP_S) -> list[float]:
    """Per-frame swap alpha for the whole clip: median-smoothed pitch -> raw alpha -> slew-limited -> smoothstep."""
    raw = [pose_alpha(p, on, off) for p in median_pitch(pitches)]
    step = 1.0 / max(1.0, ramp_s * fps)
    return [round(x * x * (3.0 - 2.0 * x), 4) for x in slew_limit(raw, step)]


def fit_affine(source, processed, boxes, sample: int = 20000):
    """3x3 matrix + offset (in 0..255 BGR) mapping the engine's frame back onto the source colours, fitted on pixels
    outside the padded face boxes (where both are the same picture, the engine only re-ranged its colours). Identity
    when there are too few pixels or the fit does not reduce the error."""
    import numpy as np
    h, w = source.shape[:2]
    outside = face_regions_mask((h, w), boxes, pad=FACE_PAD + 0.2, feather=0.01) < 0.01 if boxes else np.ones((h, w), bool)
    ys, xs = np.nonzero(outside[::4, ::4])
    if len(ys) < 500:
        return np.eye(3, dtype=np.float32), np.zeros(3, np.float32)
    pick = np.random.default_rng(0).choice(len(ys), min(sample, len(ys)), replace=False)
    ys, xs = ys[pick] * 4, xs[pick] * 4
    x = processed[ys, xs].astype(np.float32)
    y = source[ys, xs].astype(np.float32)
    design = np.hstack([x, np.ones((len(x), 1), np.float32)])
    coef, *_ = np.linalg.lstsq(design, y, rcond=None)
    fitted = design @ coef
    before, after = float(np.abs(x - y).mean()), float(np.abs(fitted - y).mean())
    if not after < before:
        return np.eye(3, dtype=np.float32), np.zeros(3, np.float32)
    return coef[:3].T.astype(np.float32), coef[3].astype(np.float32)


def apply_affine(frame, matrix, offset):
    import numpy as np
    out = frame.reshape(-1, 3).astype(np.float32) @ matrix.T + offset
    return np.clip(out, 0, 255).round().astype(np.uint8).reshape(frame.shape)


def swap_mask(source, processed, hull, side: float):
    """Soft 0..1 mask of the pixels the engine really changed (plus the landmark hull, where the face is surely swapped):
    below CHANGE_LO the difference is codec noise and the source pixel is kept."""
    import cv2
    import numpy as np
    d = np.abs(source.astype(np.int16) - processed.astype(np.int16)).max(axis=2).astype(np.float32)
    d = cv2.GaussianBlur(d, (0, 0), max(1.0, 0.01 * side))
    ch = np.clip((d - CHANGE_LO) / (CHANGE_HI - CHANGE_LO), 0.0, 1.0)
    g = max(2, int(0.03 * side))
    ch = cv2.morphologyEx(ch, cv2.MORPH_CLOSE, cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (2 * g + 1, 2 * g + 1)))
    m = np.maximum(ch, hull.astype(np.float32))
    return np.clip(cv2.GaussianBlur(m, (0, 0), max(1.0, 0.015 * side)), 0.0, 1.0)


def relight(source, processed, mask, side: float):
    """Transfer the SOURCE's low-frequency lighting (and mean skin tone) onto the swapped face: the face keeps its own
    texture and identity but sits in the scene's light (a bowed head in deep shadow gets a shadowed face, not a flat lit
    one). Lab: L'_p = L_p + gain * clip(blur(L_s) - blur(L_p)) inside the mask, blurs normalised by the mask."""
    import cv2
    import numpy as np
    if mask.max() < 0.05:
        return processed
    ls = cv2.cvtColor(source.astype(np.float32) / 255.0, cv2.COLOR_BGR2LAB)
    lp = cv2.cvtColor(processed.astype(np.float32) / 255.0, cv2.COLOR_BGR2LAB)
    sigma = max(2.0, RELIGHT_SIGMA * side)
    den = cv2.GaussianBlur(mask, (0, 0), sigma) + 1e-3
    low_s = cv2.GaussianBlur(ls[..., 0] * mask, (0, 0), sigma) / den
    low_p = cv2.GaussianBlur(lp[..., 0] * mask, (0, 0), sigma) / den
    delta = np.clip(low_s - low_p, -RELIGHT_MAX_L, RELIGHT_MAX_L)
    out = lp.copy()
    out[..., 0] = np.clip(lp[..., 0] + RELIGHT_L_GAIN * delta * mask, 0.0, 100.0)
    core = mask > 0.5
    if core.sum() > 50:
        for c in (1, 2):
            shift = float(np.clip((ls[..., c][core] - lp[..., c][core]).mean(), -RELIGHT_AB_MAX, RELIGHT_AB_MAX))
            out[..., c] = lp[..., c] + RELIGHT_AB_GAIN * shift * mask
    return np.clip(cv2.cvtColor(out, cv2.COLOR_LAB2BGR) * 255.0, 0, 255).round().astype(np.uint8)


def hull_mask(shape, landmarks68):
    import cv2
    import numpy as np
    m = np.zeros(shape, np.uint8)
    cv2.fillConvexPoly(m, cv2.convexHull(np.round(landmarks68).astype(np.int32)), 1)
    return m.astype(bool)


def face_region(shape, landmarks68, side: float):
    """Stable soft 0..1 region the engine's face lives in: the 68-point hull plus the forehead, dilated and feathered.
    Geometry only (no pixel differences), so it cannot flicker with codec noise from frame to frame."""
    import cv2
    import numpy as np
    pts = np.asarray(landmarks68, np.float32)
    drop = float(pts[8, 1] - pts[17:27, 1].min())
    brow = pts[17:27] - np.array([0.0, FOREHEAD_UP * drop], np.float32)
    m = np.zeros(shape, np.uint8)
    cv2.fillConvexPoly(m, cv2.convexHull(np.round(np.vstack([pts, brow])).astype(np.int32)), 255)
    g = max(1, int(REGION_DILATE * side))
    m = cv2.dilate(m, cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (2 * g + 1, 2 * g + 1)))
    return np.clip(cv2.GaussianBlur(m.astype(np.float32) / 255.0, (0, 0), max(1.0, REGION_FEATHER * side)), 0.0, 1.0)


def compose_v2(source, processed, faces, alpha: float, hair_masks=None):
    """One output frame. faces = [{"box": (x1,y1,x2,y2), "lm68": ndarray|None, "swapped": bool}]. Everything outside the
    padded face boxes is the source, byte for byte (the Gate 1 T2 mask)."""
    import numpy as np
    boxes = [tuple(float(v) for v in f["box"]) for f in faces]
    if not boxes or alpha <= 0.0:
        return keep_source_outside_faces(source, source, boxes) if boxes else source
    matrix, offset = fit_affine(source, processed, boxes)
    p = apply_affine(processed, matrix, offset)
    h, w = source.shape[:2]
    gate = np.zeros((h, w), np.float32)
    relit = p
    for f in faces:
        if not f.get("swapped"):
            continue
        x1, y1, x2, y2 = f["box"]
        side = float(max(x2 - x1, y2 - y1))
        if f.get("lm68") is not None:
            m = face_region((h, w), f["lm68"], side)
        else:                                                         # no landmarks: what the engine really changed
            m = swap_mask(source, p, np.zeros((h, w), bool), side)
        m = m * face_regions_mask((h, w), [tuple(float(v) for v in f["box"])], feather=0.05)
        relit = relight(source, relit, m, side)
        gate = np.maximum(gate, m)
    out = source.astype(np.float32) + (alpha * gate)[..., None] * (relit.astype(np.float32) - source.astype(np.float32))
    out = np.clip(out.round(), 0, 255).astype(np.uint8)
    for i, f in enumerate(faces):
        if f.get("swapped") and hair_masks and hair_masks[i] is not None:
            x1, y1, x2, y2 = f["box"]
            out = hairline_guard(source, out, hair_masks[i], float(max(x2 - x1, y2 - y1)))
    return keep_source_outside_faces(source, out, boxes)


def main_v2(argv: list[str]) -> int:
    src, swp, dst, rep = argv[:4]
    on, off = (float(argv[4]), float(argv[5])) if len(argv) >= 6 and not argv[4].startswith("--") else (PITCH_ON, PITCH_OFF)
    import numpy as np
    sys.path.insert(0, str(Path.cwd()))
    from facefusion import state_manager
    for k, v in {"execution_providers": ["directml"], "execution_device_ids": ["0"], "download_providers": ["github", "huggingface"],
                 "face_detector_model": "yolo_face", "face_detector_size": "640x640", "face_detector_angles": [0],
                 "face_detector_margin": [0, 0, 0, 0], "face_detector_score": 0.5, "face_landmarker_model": "2dfan4",
                 "face_landmarker_score": 0.5, "log_level": "error", "face_parser_model": "bisenet_resnet_34",
                 "face_occluder_model": "xseg_1"}.items():
        state_manager.init_item(k, v)
    from facefusion import face_masker
    from facefusion.face_creator import get_many_faces
    face_masker.pre_check()

    w, h, rate = _probe(src)
    a_, _, b_ = rate.partition("/")
    fps = float(a_) / float(b_ or 1)
    # pass 1: faces of the source, which face the engine changed, its pitch
    per_frame, pitches = [], []
    for s, r in zip(_frames(src, w, h), _frames(swp, w, h)):
        faces = get_many_faces([s])
        infos = []
        for f in faces:
            x1, y1, x2, y2 = [int(max(0, v)) for v in f.bounding_box]
            a, b = s[y1:y2, x1:x2], r[y1:y2, x1:x2]
            change = float(np.abs(a.astype(np.int16) - b.astype(np.int16)).mean()) if a.size else 0.0
            infos.append({"box": [float(v) for v in f.bounding_box], "lm68": np.asarray(f.landmark_set["68"], np.float32),
                          "change": change, "face": f})
        target = max(infos, key=lambda x: x["change"]) if infos else None
        per_frame.append(infos)
        pitches.append(pitch_of(target["lm68"]) if target is not None and target["change"] > SWAPPED_CHANGE else None)
    alphas = alpha_track(pitches, fps, on, off)

    enc = subprocess.Popen([shutil.which("ffmpeg") or "ffmpeg", "-v", "error", "-y", "-f", "rawvideo", "-pix_fmt", "bgr24",
                            "-s", f"{w}x{h}", "-r", rate, "-i", "-", "-c:v", "libx264rgb", "-qp", "0", "-preset", "ultrafast", dst],
                           stdin=subprocess.PIPE)
    rows = []
    for i, (s, r) in enumerate(zip(_frames(src, w, h), _frames(swp, w, h))):
        infos = per_frame[i]
        a = alphas[i]
        # no face found in this source frame: the engine may still have swapped one the detector here missed (blur,
        # glasses), so its frame is kept, colour-range corrected, and only where it really differs from the source
        if not infos:
            matrix, offset = fit_affine(s, r, [])
            p = apply_affine(r, matrix, offset)
            m = swap_mask(s, p, np.zeros((h, w), bool), 0.15 * h)[..., None] * a
            o = np.clip((s.astype(np.float32) + m * (p.astype(np.float32) - s.astype(np.float32))).round(), 0, 255).astype(np.uint8)
        else:
            faces = [{"box": x["box"], "lm68": x["lm68"], "swapped": x["change"] > SWAPPED_CHANGE} for x in infos]
            hair = [parse_hair(s, x["face"], face_masker.forward_parse_face) if (a > 0.0 and x["change"] > SWAPPED_CHANGE)
                    else None for x in infos]
            o = compose_v2(s, r, faces, a, hair)
        enc.stdin.write(np.ascontiguousarray(o).tobytes())
        rows.append({"frame": i, "pitch": None if pitches[i] is None else round(pitches[i], 3), "alpha": round(a, 3)})
    enc.stdin.close()
    if enc.wait() != 0 or not rows:
        print(json.dumps({"error": "encode_failed" if rows else "no_frames"}))
        return 2
    gated = [r["frame"] for r in rows if r["alpha"] < 1]
    report = {"version": 2, "pitch_on": on, "pitch_off": off, "ramp_s": RAMP_S, "frames": len(rows), "gated_frames": len(gated),
              "gated_range": [gated[0], gated[-1]] if gated else None}
    Path(rep).write_text(json.dumps({**report, "rows": rows}, indent=1), encoding="utf-8")
    print(json.dumps(report))
    return 0


#: mean |source - engine| (0..255) inside a face box above which the engine is taken to have swapped that face
SWAPPED_CHANGE = 4.0


def main(argv: list[str]) -> int:
    """V2 by default; `--v1` keeps the 10.10 morning behaviour (the comparison baseline)."""
    if "--v1" in argv:
        return main_v1([a for a in argv if a != "--v1"])
    return main_v2([a for a in argv if a != "--v2"])


def main_v1(argv: list[str]) -> int:
    src, swp, dst, rep = argv[:4]
    on, off = (float(argv[4]), float(argv[5])) if len(argv) >= 6 else (PITCH_ON, PITCH_OFF)
    import numpy as np
    sys.path.insert(0, str(Path.cwd()))
    from facefusion import state_manager
    for k, v in {"execution_providers": ["directml"], "execution_device_ids": ["0"], "download_providers": ["github", "huggingface"],
                 "face_detector_model": "yolo_face", "face_detector_size": "640x640", "face_detector_angles": [0],
                 "face_detector_margin": [0, 0, 0, 0], "face_detector_score": 0.5, "face_landmarker_model": "2dfan4",
                 "face_landmarker_score": 0.5, "log_level": "error", "face_parser_model": "bisenet_resnet_34",
                 "face_occluder_model": "xseg_1"}.items():
        state_manager.init_item(k, v)
    from facefusion import face_masker
    from facefusion.face_creator import get_many_faces
    face_masker.pre_check()

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
        for f in faces if a > 0.0 else []:
            side = max(f.bounding_box[2] - f.bounding_box[0], f.bounding_box[3] - f.bounding_box[1])
            o = hairline_guard(s, o, parse_hair(s, f, face_masker.forward_parse_face), float(side))
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
