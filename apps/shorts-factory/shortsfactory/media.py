"""ffmpeg/ffprobe helpers: probing, audio energy, scene changes, face-centred framing, 9:16 render and validation.

Reused (copied pure logic, see docs/README_RU.md "Что переиспользовано"):
  * probe()            - adapted from tools/trend_edit/analyze_edit.py::probe
  * detect_cuts()      - copied from tools/trend_edit/analyze_edit.py::detect_cuts (frame-difference cut detector)
  * fit-with-blurred-fill framing - the idea of tools/trend_edit/fx.py::blur_fill, done in one ffmpeg filter graph
"""
from __future__ import annotations

import json
import os
import re
import subprocess
from pathlib import Path

import numpy as np

TARGET_W, TARGET_H = 1080, 1920
MAX_S = 60.0
FFMPEG_THREADS = os.environ.get("SHORTS_FFMPEG_THREADS", "4")      # modest CPU load by default
_NOWIN = {"creationflags": 0x08000000} if os.name == "nt" else {}  # CREATE_NO_WINDOW


class RenderError(RuntimeError):
    pass


def run(cmd: list[str], cwd: str | Path | None = None, timeout: float = 3600) -> subprocess.CompletedProcess:
    r = subprocess.run(cmd, capture_output=True, cwd=cwd, timeout=timeout, **_NOWIN)
    if r.returncode != 0:
        tail = r.stderr.decode("utf-8", "replace")[-1500:]
        raise RenderError(f"{Path(cmd[0]).name} failed ({r.returncode}): {tail}")
    return r


# ------------------------------------------------------------------ probe (adapted from tools/trend_edit/analyze_edit.py)
def probe(path: str | Path) -> dict:
    r = run(["ffprobe", "-v", "error", "-show_entries",
             "stream=codec_type,codec_name,width,height,avg_frame_rate,sample_rate,channels:format=duration,bit_rate",
             "-of", "json", str(path)])
    d = json.loads(r.stdout)
    streams = d.get("streams") or []
    v = next((s for s in streams if s.get("codec_type") == "video"), None)
    a = next((s for s in streams if s.get("codec_type") == "audio"), None)
    fps = None
    if v and v.get("avg_frame_rate") and v["avg_frame_rate"] != "0/0":
        num, _, den = v["avg_frame_rate"].partition("/")
        fps = round(float(num) / float(den or 1), 3)
    return {"width": int(v["width"]) if v else None, "height": int(v["height"]) if v else None, "fps": fps,
            "video_codec": v.get("codec_name") if v else None, "audio_codec": a.get("codec_name") if a else None,
            "has_audio": a is not None, "has_video": v is not None,
            "duration": round(float((d.get("format") or {}).get("duration") or 0.0), 3)}


# ------------------------------------------------------------------ audio energy
def audio_energy(path: str | Path, hop_s: float = 0.5, start: float = 0.0, dur: float | None = None) -> dict:
    """RMS loudness per hop (dBFS) of the mono 16 kHz track, plus z-scores. Local, numpy only."""
    cmd = ["ffmpeg", "-v", "error", "-nostdin"]
    if start:
        cmd += ["-ss", f"{start:.3f}"]
    cmd += ["-i", str(path)]
    if dur:
        cmd += ["-t", f"{dur:.3f}"]
    cmd += ["-vn", "-ac", "1", "-ar", "16000", "-f", "s16le", "-"]
    raw = run(cmd).stdout
    y = np.frombuffer(raw, np.int16).astype(np.float32) / 32768.0
    hop = int(16000 * hop_s)
    n = len(y) // hop
    if n == 0:
        return {"hop_s": hop_s, "t": [], "db": [], "z": []}
    frames = y[: n * hop].reshape(n, hop)
    rms = np.sqrt((frames ** 2).mean(axis=1) + 1e-12)
    db = 20 * np.log10(rms + 1e-9)
    sd = float(db.std()) or 1.0
    z = (db - float(np.median(db))) / sd
    return {"hop_s": hop_s, "t": [round(start + i * hop_s, 3) for i in range(n)], "db": [round(float(x), 2) for x in db],
            "z": [round(float(x), 3) for x in z]}


# ------------------------------------------------------------------ scene changes
def detect_cuts(d: np.ndarray, t: np.ndarray, min_gap: float = 0.15) -> list[float]:
    """Copied from tools/trend_edit/analyze_edit.py: a cut = a frame difference far above the local motion level."""
    cuts, last = [], -1.0
    for i in range(len(d)):
        lo, hi = max(0, i - 12), min(len(d), i + 13)
        local = float(np.median(np.delete(d[lo:hi], i - lo))) if hi - lo > 1 else 0.0
        if d[i] > max(18.0, 3.5 * local) and t[i] - last >= min_gap:
            cuts.append(round(float(t[i]), 3))
            last = t[i]
    return cuts


_SCD = re.compile(rb"lavfi\.scd\.score:\s*([\d.]+),\s*lavfi\.scd\.time:\s*([\d.]+)")


def scene_changes(path: str | Path, threshold: float = 10.0, analysis_fps: int = 5) -> dict:
    """ffmpeg scdet at low resolution/fps; falls back to the analyze_edit frame-difference detector."""
    try:
        r = run(["ffmpeg", "-v", "info", "-nostdin", "-threads", "2", "-i", str(path), "-an", "-vf",
                 f"fps={analysis_fps},scale=160:-2,scdet=threshold={threshold}", "-f", "null", "-"])
        cuts = [round(float(m.group(2)), 3) for m in _SCD.finditer(r.stderr)]
        return {"method": "ffmpeg-scdet", "threshold": threshold, "cuts": cuts}
    except RenderError:
        pass
    W, H = 160, 90
    raw = run(["ffmpeg", "-v", "error", "-nostdin", "-i", str(path), "-vf", f"fps={analysis_fps},scale={W}:{H}",
               "-f", "rawvideo", "-pix_fmt", "gray", "-"]).stdout
    f = np.frombuffer(raw, np.uint8).reshape(-1, H, W).astype(np.float32)
    if len(f) < 2:
        return {"method": "frame-diff", "cuts": []}
    d = np.abs(np.diff(f, axis=0)).mean(axis=(1, 2))
    return {"method": "frame-diff(analyze_edit)", "cuts": detect_cuts(d, np.arange(1, len(f)) / analysis_fps)}


# ------------------------------------------------------------------ face-centred framing
FACE_MODEL = Path(os.environ.get("SHORTS_FACE_MODEL", r"C:\Users\asd\Bossman\apps-local\facefusion\.assets\models\yoloface_8n.onnx"))
_yolo = {"sess": None}


def _yoloface(img_bgr: np.ndarray) -> list[tuple[float, float, float, float, float]]:
    """YOLOv8n-face (the detector FaceFusion already ships on this PC) via onnxruntime CPU, 2 threads.
    Returns boxes (x, y, w, h, score) in image pixels."""
    import onnxruntime as ort
    if _yolo["sess"] is None:
        so = ort.SessionOptions()
        so.intra_op_num_threads = 2
        _yolo["sess"] = ort.InferenceSession(str(FACE_MODEL), so, providers=["CPUExecutionProvider"])
    import cv2
    h, w = img_bgr.shape[:2]
    r = 640 / max(h, w)
    canvas = np.zeros((640, 640, 3), np.uint8)
    canvas[: int(h * r), : int(w * r)] = cv2.resize(img_bgr, (int(w * r), int(h * r)))
    x = (canvas[:, :, ::-1].astype(np.float32) / 255.0).transpose(2, 0, 1)[None]
    sess = _yolo["sess"]
    det = np.squeeze(sess.run(None, {sess.get_inputs()[0].name: x})[0]).T     # (8400, 20): cx, cy, w, h, score, landmarks
    keep = det[:, 4] > 0.5
    return [((cx - bw / 2) / r, (cy - bh / 2) / r, bw / r, bh / r, s) for cx, cy, bw, bh, s in det[keep][:, :5]]


def face_center_x(path: str | Path, start: float, dur: float, samples: int = 5) -> dict:
    """Median horizontal centre (0..1) of the largest face on a few frames. Detector: OpenCV Haar when this cv2
    build has it, else FaceFusion's yoloface_8n.onnx (CPU); none found -> centre."""
    try:
        import cv2
    except ImportError:
        return {"x": 0.5, "method": "center (opencv missing)", "faces": 0}
    casc = None
    if hasattr(cv2, "CascadeClassifier"):
        casc = cv2.CascadeClassifier(str(Path(cv2.data.haarcascades) / "haarcascade_frontalface_default.xml"))
    elif not FACE_MODEL.is_file():
        return {"x": 0.5, "method": "center (no face detector available)", "faces": 0}
    xs, W = [], 640
    for k in range(samples):
        t = start + dur * (k + 0.5) / samples
        try:
            raw = run(["ffmpeg", "-v", "error", "-nostdin", "-ss", f"{t:.3f}", "-i", str(path), "-frames:v", "1",
                       "-vf", f"scale={W}:-2", "-f", "image2pipe", "-vcodec", "png", "-"]).stdout
        except RenderError:
            continue
        img = cv2.imdecode(np.frombuffer(raw, np.uint8), cv2.IMREAD_COLOR)
        if img is None:
            continue
        if casc is not None:
            g = cv2.cvtColor(img, cv2.COLOR_BGR2GRAY)
            faces = [tuple(f) for f in casc.detectMultiScale(g, scaleFactor=1.1, minNeighbors=6, minSize=(max(24, g.shape[1] // 20),) * 2)]
        else:
            try:
                faces = [f[:4] for f in _yoloface(img)]
            except Exception:  # noqa: BLE001 - detector failure -> centre framing, reported in method
                return {"x": 0.5, "method": "center (face detector error)", "faces": 0}
        if len(faces):
            x, y, w, h = max(faces, key=lambda f: f[2] * f[3])
            xs.append((x + w / 2) / img.shape[1])
    method = "opencv-haar-largest-face" if casc is not None else "yoloface_8n-largest-face"
    if not xs:
        return {"x": 0.5, "method": f"center (no face found, {method})", "faces": 0}
    return {"x": round(float(np.median(xs)), 4), "method": method, "faces": len(xs)}


def even(v: float) -> int:
    return max(2, int(v) // 2 * 2)


def plan_frame(src_w: int, src_h: int, face_x: float = 0.5) -> dict:
    """Decide framing WITHOUT upscaling the content pixels and without stretching.

    cover   : source is large enough to fill 1080x1920 by downscaling (s<=1) -> scale + crop around the face.
    inset   : source is >=1080 wide but too small to fill 9:16 -> crop a native 1080-wide window around the face
              (scale 1:1) and lay it over a blurred, darkened fill (decoration only, like fx.blur_fill).
    native  : source narrower than 1080 -> keep native pixels, crop to 9:16 at native size, output smaller than
              1080x1920 and say so (owner rule: no soft upscales).
    """
    s_cover = max(TARGET_W / src_w, TARGET_H / src_h)
    if s_cover <= 1.0:
        sw, sh = even(src_w * s_cover + 0.999), even(src_h * s_cover + 0.999)
        sw, sh = max(sw, TARGET_W), max(sh, TARGET_H)
        x = int(round(min(max(face_x * sw - TARGET_W / 2, 0), sw - TARGET_W)))
        return {"mode": "cover", "out_w": TARGET_W, "out_h": TARGET_H, "scale": round(s_cover, 4),
                "filter": f"scale={sw}:{sh}:flags=lanczos,crop={TARGET_W}:{TARGET_H}:{x}:{(sh - TARGET_H) // 2},setsar=1",
                "note": "downscale + crop to 9:16 around the face"}
    if src_w >= TARGET_W:
        cw, ch = TARGET_W, even(min(src_h, TARGET_H))
        x = int(round(min(max(face_x * src_w - cw / 2, 0), src_w - cw)))
        y = (src_h - ch) // 2
        oy = (TARGET_H - ch) // 2 - 120 if ch < TARGET_H - 400 else (TARGET_H - ch) // 2   # leave room for captions
        graph = (f"split=2[bgsrc][fgsrc];"
                 f"[bgsrc]scale={TARGET_W}:{TARGET_H}:force_original_aspect_ratio=increase,crop={TARGET_W}:{TARGET_H},"
                 f"gblur=sigma=28,eq=brightness=-0.18[bg];"
                 f"[fgsrc]crop={cw}:{ch}:{x}:{y}[fg];[bg][fg]overlay=0:{max(0, oy)},setsar=1")
        return {"mode": "inset", "out_w": TARGET_W, "out_h": TARGET_H, "scale": 1.0, "graph": graph,
                "content_px": f"{cw}x{ch}", "note": "native 1:1 window around the face on a blurred fill (no upscale)"}
    # native: crop to 9:16 at native resolution
    if src_h / src_w >= 16 / 9:
        cw, ch = even(src_w), even(src_w * 16 / 9)
    else:
        cw, ch = even(src_h * 9 / 16), even(src_h)
    x = int(round(min(max(face_x * src_w - cw / 2, 0), src_w - cw)))
    return {"mode": "native", "out_w": cw, "out_h": ch, "scale": 1.0,
            "filter": f"crop={cw}:{ch}:{x}:{(src_h - ch) // 2},setsar=1",
            "note": f"источник уже {src_w}px < 1080: оставлено нативное {cw}x{ch}, апскейла нет"}


# ------------------------------------------------------------------ subtitles (ASS)
def _ass_time(t: float) -> str:
    t = max(0.0, t)
    h, rem = divmod(t, 3600)
    m, s = divmod(rem, 60)
    return f"{int(h)}:{int(m):02d}:{s:05.2f}"


def ass_escape(text: str) -> str:
    return text.replace("\\", "/").replace("{", "(").replace("}", ")").replace("\n", " ").strip()


def chunk_words(words: list[dict], max_chars: int = 26, max_words: int = 5, max_gap: float = 0.8) -> list[dict]:
    """Group word timestamps into short caption chunks (typical Shorts style: 2-5 words on screen)."""
    out, cur = [], []

    def flush():
        if cur:
            out.append({"start": cur[0]["start"], "end": cur[-1]["end"], "text": " ".join(w["word"].strip() for w in cur).strip()})
            cur.clear()
    for w in words:
        word = w["word"].strip()
        if not word:
            continue
        if cur and (len(" ".join(x["word"].strip() for x in cur)) + 1 + len(word) > max_chars or len(cur) >= max_words
                    or w["start"] - cur[-1]["end"] > max_gap):
            flush()
        cur.append(w)
        if word[-1:] in ".!?…":
            flush()
    flush()
    for i in range(len(out) - 1):                       # no overlaps, no flicker gaps < 0.15 s
        if out[i + 1]["start"] - out[i]["end"] < 0.15:
            out[i]["end"] = out[i + 1]["start"]
    return out


def write_ass(path: Path, chunks: list[dict], play_w: int, play_h: int, *, font: str = "Arial", size_frac: float = 0.036,
              margin_v_frac: float = 0.22, header: str | None = None, footer: str | None = None, total: float | None = None) -> Path:
    fs = max(16, int(play_h * size_frac))
    lines = ["[Script Info]", "ScriptType: v4.00+", f"PlayResX: {play_w}", f"PlayResY: {play_h}", "WrapStyle: 0",
             "ScaledBorderAndShadow: yes", "", "[V4+ Styles]",
             "Format: Name, Fontname, Fontsize, PrimaryColour, SecondaryColour, OutlineColour, BackColour, Bold, Italic, Underline, "
             "StrikeOut, ScaleX, ScaleY, Spacing, Angle, BorderStyle, Outline, Shadow, Alignment, MarginL, MarginR, MarginV, Encoding",
             f"Style: Cap,{font},{fs},&H00FFFFFF,&H00FFFFFF,&H00000000,&H64000000,-1,0,0,0,100,100,0,0,1,{max(2, fs // 12)},1,2,"
             f"{int(play_w * 0.07)},{int(play_w * 0.07)},{int(play_h * margin_v_frac)},1",
             f"Style: Head,{font},{int(fs * 0.95)},&H00FFFFFF,&H00FFFFFF,&H00000000,&H96000000,-1,0,0,0,100,100,0,0,3,{max(2, fs // 10)},0,8,"
             f"{int(play_w * 0.06)},{int(play_w * 0.06)},{int(play_h * 0.07)},1",
             f"Style: Foot,{font},{int(fs * 0.62)},&H00E0E0E0,&H00E0E0E0,&H00000000,&H96000000,0,0,0,0,100,100,0,0,3,{max(1, fs // 16)},0,2,"
             f"{int(play_w * 0.05)},{int(play_w * 0.05)},{int(play_h * 0.045)},1",
             "", "[Events]", "Format: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text"]
    end_all = total if total is not None else (chunks[-1]["end"] if chunks else 1.0)
    if header:
        lines.append(f"Dialogue: 1,{_ass_time(0)},{_ass_time(end_all)},Head,,0,0,0,,{ass_escape(header)}")
    if footer:
        lines.append(f"Dialogue: 1,{_ass_time(0)},{_ass_time(end_all)},Foot,,0,0,0,,{ass_escape(footer)}")
    for c in chunks:
        if c["end"] > c["start"] and c["text"]:
            lines.append(f"Dialogue: 0,{_ass_time(c['start'])},{_ass_time(c['end'])},Cap,,0,0,0,,{ass_escape(c['text'])}")
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return path


# ------------------------------------------------------------------ render + validation
def render_clip(src: str | Path, out: str | Path, *, start: float, dur: float, src_w: int, src_h: int, face_x: float,
                ass_name: str | None, work: Path, crf: int = 20) -> dict:
    """Cut [start, start+dur) from src into a 9:16 H.264/AAC MP4 with burned captions and EBU R128 loudness
    normalisation (-14 LUFS, original audio kept, no music added)."""
    if dur <= 0 or dur > MAX_S + 1e-6:
        raise RenderError(f"длительность {dur:.2f} с вне 0..{MAX_S:.0f} с")
    plan = plan_frame(src_w, src_h, face_x)
    subs = f",subtitles={ass_name}" if ass_name else ""      # relative name, ffmpeg runs with cwd=work (no path escaping)
    if plan["mode"] == "inset":
        vgraph = f"[0:v]{plan['graph']}{subs}[v]"
    else:
        vgraph = f"[0:v]{plan['filter']}{subs}[v]"
    out = Path(out)
    cmd = ["ffmpeg", "-v", "error", "-nostdin", "-y", "-ss", f"{start:.3f}", "-i", str(Path(src).resolve()), "-t", f"{dur:.3f}",
           "-filter_complex", vgraph + ";[0:a]loudnorm=I=-14:TP=-1.5:LRA=11,aresample=48000[a]",
           "-map", "[v]", "-map", "[a]", "-c:v", "libx264", "-preset", "medium", "-crf", str(crf), "-pix_fmt", "yuv420p",
           "-profile:v", "high", "-threads", FFMPEG_THREADS, "-c:a", "aac", "-b:a", "192k", "-ar", "48000",
           "-movflags", "+faststart", str(out.resolve())]
    run(cmd, cwd=work)
    return plan


def validate_output(path: str | Path, *, expected_w: int, expected_h: int, expected_s: float, src_w: int, tol_s: float = 0.35) -> dict:
    """Measure with ffprobe and reject anything that is not what we promised."""
    p = probe(path)
    problems = []
    if (p["width"], p["height"]) != (expected_w, expected_h):
        problems.append(f"разрешение {p['width']}x{p['height']} != {expected_w}x{expected_h}")
    if p["height"] and p["width"] and p["height"] <= p["width"]:
        problems.append("не вертикальное видео")
    if p["duration"] > MAX_S + 0.5:
        problems.append(f"длительность {p['duration']} > {MAX_S} с")
    if abs(p["duration"] - expected_s) > tol_s:
        problems.append(f"длительность {p['duration']} != ожидаемой {expected_s:.2f}±{tol_s}")
    if not p["has_audio"]:
        problems.append("нет аудиодорожки")
    if p["video_codec"] != "h264":
        problems.append(f"кодек {p['video_codec']} != h264")
    if src_w < TARGET_W and p["width"] and p["width"] > src_w:
        problems.append("апскейл выше разрешения источника")
    return {"ok": not problems, "problems": problems, "ffprobe": p,
            "native_below_1080": src_w < TARGET_W}


def poster(video: str | Path, out: str | Path, at: float = 1.0) -> Path:
    run(["ffmpeg", "-v", "error", "-nostdin", "-y", "-ss", f"{at:.2f}", "-i", str(video), "-frames:v", "1",
         "-vf", "scale=360:-2", "-q:v", "4", str(out)])
    return Path(out)
