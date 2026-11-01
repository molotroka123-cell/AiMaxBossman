"""Studio preset render engine = the EXISTING Direct Gen sd-cli video path (owner item 6, 01.11 «честно доведи Studio»).

No new generator and no new queue: ``DirectGenStudioEngine`` submits the Studio job as an ordinary Direct Gen job
(``DirectGenService.create``: same queue, GPU lock, memory preflight, STOP, history and timings as the pult window),
waits for it, and hands the finished file to ``OrderBook.render_preset``, which verifies it (magic bytes + ffprobe) and
measures QC on decoded frames itself. The engine never claims anything it did not do:

  * ``preflight(job, item, licences)`` runs BEFORE any state change and refuses
      - a model this engine does not run (only sd-cli VIDEO models of Direct Gen),
      - a model whose licence is not recorded as commercial (Wan2.2 TI2V 5B = Apache-2.0; LTX-2.5 = UNKNOWN -> refused),
      - any face line / FaceLock / reference picture (no face QA on this path; faces are refused, consent or not),
      - weights or sd-cli missing on disk -> 503 ``render_engine_missing`` (with the catalog's own reason),
      - a size/duration outside the model's catalog limits (e.g. the 1080x1920 6 s preset on Wan 5B: max side 1280,
        max 5 s) -> 422 ``preset_exceeds_engine_limits``; nothing is silently clamped;
  * a Direct Gen job that fails, is cancelled (STOP) or does not finish in time raises: the order goes to
    ``reconcile_required`` with the reason, never to ``completed``;
  * the visible watermark/disclosure «AI-generated · Bossman Motion Studio» is burned in by ffmpeg ``drawtext`` and
    declared ONLY when the decoded frame really changed in the caption band (measured, not assumed); otherwise the
    raw file is handed over without a watermark declaration and delivery stays blocked.
"""
from __future__ import annotations

import asyncio
import shutil
import subprocess
import time
from pathlib import Path
from typing import Any

from . import catalog, sdcli
from .orders import DISCLOSURE
from .video_measure import MeasureError, decode_gray, sha256_file

TERMINAL = ("completed", "failed", "cancelled", "error", "interrupted")
FONT_CANDIDATES = ("C:/Windows/Fonts/arial.ttf", "C:/Windows/Fonts/segoeui.ttf",
                   "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf", "/System/Library/Fonts/Supplemental/Arial.ttf")
WATERMARK_BAND = 0.16          # bottom share of the frame where the caption is drawn
WATERMARK_MIN_GAIN = 2.0       # band must change >= 2 luma levels more than the rest of the frame


def _font() -> str | None:
    return next((f for f in FONT_CANDIDATES if Path(f).is_file()), None)


def _drawtext(text: str, font: str | None) -> str:
    safe = text.replace("\\", "\\\\").replace("'", "").replace(":", "\\:")
    face = "fontfile='" + font.replace(":", "\\:") + "':" if font else ""
    return (f"drawtext={face}text='{safe}':fontcolor=white@0.9:fontsize=h/26:box=1:boxcolor=black@0.45:"
            f"boxborderw=8:x=(w-text_w)/2:y=h-text_h-h/30")


class DirectGenStudioEngine:
    """engine(job, result_dir) for ``OrderBook.render_preset`` backed by ``DirectGenService`` (see module doc)."""

    name = "direct_gen.sdcli"

    def __init__(self, direct_gen: Any, loop: asyncio.AbstractEventLoop, *, poll_s: float = 2.0,
                 timeout_s: float | None = None, ffmpeg: str | None = None, ffprobe: str | None = None):
        self.dg = direct_gen
        self.loop = loop
        self.poll_s = poll_s
        self.timeout_s = timeout_s if timeout_s is not None else float(getattr(direct_gen, "timeout_seconds", 3 * 3600)) + 3600
        self.ffmpeg = ffmpeg or shutil.which("ffmpeg")
        self.ffprobe = ffprobe or shutil.which("ffprobe")

    # ---------- before any state change ----------

    def preflight(self, job: dict, item: dict, licences: dict[str, dict]) -> list[dict]:
        jid, model = job.get("job_id"), str(job.get("model") or "")
        spec = sdcli.BY_ID.get(model)
        if spec is None or spec.kind != "video":
            return [{"job_id": jid, "code": "engine_model_unsupported", "status": 422, "model": model,
                     "message": "this engine runs only Direct Gen sd-cli video models"}]
        lic = licences.get(model) or {}
        if lic.get("commercial") is not True:
            return [{"job_id": jid, "code": "engine_licence_not_commercial", "status": 409, "model": model,
                     "licence": lic.get("licence", "unknown"),
                     "message": "the model's licence is not recorded as commercial; not used for client work"}]
        if item.get("face") or (job.get("facelock") or {}).get("requested") or (job.get("inputs") or {}).get("image"):
            return [{"job_id": jid, "code": "face_render_refused", "status": 409,
                     "message": "faces/reference pictures are refused on the Studio preset path (no face QA here)"}]
        info = sdcli.describe(spec, self.dg.media_dir, self.dg.sd_bin, catalog.overrides(self.dg.data_dir))
        if not info["available"]:
            return [{"job_id": jid, "code": "render_engine_missing", "status": 503, "model": model,
                     "engine": self.name, "reason": info["reason"], "demo": False}]
        w, h = int(job.get("width") or 0), int(job.get("height") or 0)
        try:
            catalog.parse_resolution(spec, f"{w}x{h}")
            duration = float(job.get("duration_s") or 0)
            if not 0 < duration <= spec.max_seconds:
                raise ValueError(f"duration must be >0 and <= {spec.max_seconds} s")
        except (TypeError, ValueError) as exc:
            return [{"job_id": jid, "code": "preset_exceeds_engine_limits", "status": 422, "model": model,
                     "requested": {"width": w, "height": h, "duration_s": job.get("duration_s")},
                     "limits": {"min_side": spec.min_side, "max_side": spec.max_side,
                                "side_multiple": spec.side_multiple, "max_seconds": spec.max_seconds},
                     "message": str(exc)}]
        steps = (job.get("params") or {}).get("steps")
        if steps is not None and not (isinstance(steps, int) and 1 <= steps <= spec.max_steps):
            return [{"job_id": jid, "code": "preset_exceeds_engine_limits", "status": 422, "model": model,
                     "message": f"steps must be 1..{spec.max_steps}"}]
        return []

    # ---------- the render ----------

    def _submit(self, job: dict) -> dict:
        p = job.get("params") or {}
        body = {"model": job["model"], "mode": "DIRECT", "prompt": job["effective_prompt"],
                "negative": job.get("negative") or "", "resolution": f"{job['width']}x{job['height']}",
                "duration": float(job["duration_s"]), "seed": int(job.get("seed") or p.get("seed") or 0)}
        if p.get("steps"):
            body["steps"] = int(p["steps"])
        fut = asyncio.run_coroutine_threadsafe(self.dg.create(job["participant"], body), self.loop)
        return fut.result(timeout=120)

    def _wait(self, participant: str, dg_id: str) -> dict:
        deadline = time.monotonic() + self.timeout_s
        while True:
            rec = self.dg.store.load(participant, dg_id)
            if rec.get("status") in TERMINAL:
                return rec
            if time.monotonic() > deadline:
                raise TimeoutError(f"direct_gen job {dg_id} not finished after {self.timeout_s:.0f} s "
                                   f"(status {rec.get('status')}); it keeps its own state, the owner reconciles")
            time.sleep(self.poll_s)

    def _burn_watermark(self, src: Path, out: Path) -> dict:
        """Re-encode with the visible caption; return a measured record of whether the caption is really there."""
        if not self.ffmpeg:
            return {"burned": False, "reason": "ffmpeg_missing"}
        vf = _drawtext(DISCLOSURE, _font())
        proc = subprocess.run([self.ffmpeg, "-v", "error", "-y", "-i", str(src), "-map", "0:v:0", "-vf", vf,
                               "-c:v", "libx264", "-preset", "medium", "-crf", "16", "-pix_fmt", "yuv420p",
                               "-movflags", "+faststart", str(out)], capture_output=True, text=True, timeout=1800,
                              check=False)
        if proc.returncode != 0 or not out.is_file():
            out.unlink(missing_ok=True)
            return {"burned": False, "reason": f"ffmpeg_drawtext_failed:{(proc.stderr or '').strip()[-200:]}"}
        try:
            raw, marked = decode_gray(src, self.ffmpeg, self.ffprobe), decode_gray(out, self.ffmpeg, self.ffprobe)
        except MeasureError as exc:
            out.unlink(missing_ok=True)
            return {"burned": False, "reason": f"watermark_check_decode_failed:{exc}"}
        if raw.shape[1:] != marked.shape[1:] or not len(raw) or not len(marked):
            out.unlink(missing_ok=True)
            return {"burned": False, "reason": "watermark_check_shape_mismatch"}
        i = min(len(raw), len(marked)) // 2
        a, b = raw[i].astype("int16"), marked[i].astype("int16")
        cut = int(a.shape[0] * (1 - WATERMARK_BAND))
        band = float(abs(b[cut:] - a[cut:]).mean())
        rest = float(abs(b[:cut] - a[:cut]).mean())
        rec = {"mark": DISCLOSURE, "method": "ffmpeg drawtext (libx264 crf 16)", "checked_frame_index": i,
               "band_mean_abs_diff": round(band, 3), "rest_mean_abs_diff": round(rest, 3),
               "min_gain": WATERMARK_MIN_GAIN}
        if band < rest + WATERMARK_MIN_GAIN:
            out.unlink(missing_ok=True)
            return {"burned": False, "reason": "caption_not_visible_in_decoded_frame", **rec}
        return {"burned": True, **rec}

    def __call__(self, job: dict, result_dir: Path) -> dict:
        participant = job["participant"]
        view = self._submit(job)
        dg_id = view["job_id"]
        rec = self._wait(participant, dg_id)
        if rec.get("status") != "completed":
            err = rec.get("error") or {}
            code = err.get("code") if isinstance(err, dict) else str(err)
            raise RuntimeError(f"direct_gen job {dg_id} ended {rec.get('status')}: {code or 'no error recorded'} "
                               f"{(err.get('message') if isinstance(err, dict) else '') or ''}"[:500])
        res = rec.get("result") or {}
        src = self.dg.store.job_dir(participant, dg_id) / "result" / str(res.get("file") or "")
        if not res.get("file") or not src.is_file():
            raise RuntimeError(f"direct_gen job {dg_id} completed without its result file")
        prov = rec.get("provenance") or {}
        engine_job = {"direct_gen_job_id": dg_id, "model": rec.get("model"), "params": rec.get("params"),
                      "file": src.name, "sha256": sha256_file(src), "bytes": src.stat().st_size,
                      "engine_seconds": prov.get("engine_seconds"), "peak_rss_mb": prov.get("peak_rss_mb"),
                      "engine_returncode": prov.get("engine_returncode"), "command": prov.get("command"),
                      "licence": prov.get("license"), "started_at": rec.get("started_at"),
                      "finished_at": rec.get("finished_at")}
        out = Path(result_dir) / "video.mp4"
        mark = self._burn_watermark(src, out)
        engine_job["watermark_check"] = mark
        if mark.get("burned"):
            return {"path": out, "watermark": DISCLOSURE, "engine_job": engine_job}
        raw_copy = Path(result_dir) / f"video{src.suffix or '.webm'}"
        shutil.copyfile(src, raw_copy)
        return {"path": raw_copy, "engine_job": engine_job}
