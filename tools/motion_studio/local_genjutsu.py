"""Local "Genjutsu Light"-style motion transfer: real video + 1-3 character photos -> the same video with the character replaced.

Recipe proven on 26.10.2026 (see D:\\Bossman\\work\\genjutsu-local-20261026\\receipt.json):
  1. ffmpeg: source -> 16 fps, 480x832 (scale+centre-crop) guide video, audio kept aside
  2. Robust Video Matting (onnx, CPU) -> person mask video (white = person)
  3. WanGP Wan2.2 Animate 14B (quanto int8 + lightx2v 6 steps), REPLACE mode PVBAIH#1 (mask + reference + relighting)
     = background / camera / motion of the real clip are kept, only the person is regenerated from the reference photo
  4. optional face pass (FaceFusion hyperswap_1a_256, stock NSFW analyser kept ON, no filter patching) -> fixes identity of tiny faces
  5. ffmpeg: honest lanczos upscale to 720x1280 + original audio

Everything is local: no cloud, no pod. Photos of people stay on this PC. Needs ~55 GB free RAM for WanGP; run it detached.

    python tools/motion_studio/local_genjutsu.py --video in.mp4 --refs a.jpg [b.jpg c.png] --prompt "The person ... wearing ..." \
        --out out.mp4 [--start 0 --seconds 3 --face-pass] [--dry-run]
"""
from __future__ import annotations

import argparse
import json
import os
import signal
import subprocess
import sys
import time
from pathlib import Path

WGP = Path(r"C:\Users\asd\Bossman\media-runtime\WanGP-viggle")
PY = WGP / "env_venv" / "Scripts" / "python.exe"
RVM = r"D:\Bossman\models\media\rvm\rvm_mobilenetv3_fp32.onnx"
FF_DIR = Path(r"C:\Users\asd\Bossman\apps-local\facefusion")
NEG = ("blurry, ghosting, smeared limbs, extra limbs, extra fingers, deformed hands, fused fingers, distorted face, "
       "bad anatomy, duplicate person, flicker, low quality")
MODE_REPLACE = "PVBAIH#1"


def sh(cmd, **kw):
    return subprocess.run([str(c) for c in cmd], check=True, **kw)


def prep(video: Path, work: Path, start: float, seconds: float | None, w=480, h=832):
    guide = work / "guide.mp4"
    cmd = ["ffmpeg", "-v", "error", "-y"]
    if start:
        cmd += ["-ss", str(start)]
    if seconds:
        cmd += ["-t", str(seconds)]
    cmd += ["-i", video, "-vf", f"fps=16,scale={w}:{int(w * 16 / 9) + 1}:flags=lanczos,crop={w}:{h}", "-an",
            "-c:v", "libx264", "-crf", "10", "-pix_fmt", "yuv420p", guide]
    sh(cmd)
    mask = work / "mask.mp4"
    code = f'''
import sys, cv2, numpy as np, onnxruntime as ort, subprocess, os
src, dst = r"{guide}", r"{mask}"
sess = ort.InferenceSession(r"{RVM}", providers=["CPUExecutionProvider"])
cap = cv2.VideoCapture(src); fps = cap.get(5); w, h = int(cap.get(3)), int(cap.get(4))
out = cv2.VideoWriter(dst + ".avi", cv2.VideoWriter_fourcc(*"FFV1"), fps, (w, h), isColor=True)
rec = [np.zeros([1,1,1,1], np.float32)] * 4; dr = np.array([0.375], np.float32)
while True:
    ok, f = cap.read()
    if not ok: break
    x = cv2.cvtColor(f, cv2.COLOR_BGR2RGB).astype(np.float32).transpose(2,0,1)[None] / 255.
    fgr, pha, *rec = sess.run([], {{"src": x, "r1i": rec[0], "r2i": rec[1], "r3i": rec[2], "r4i": rec[3], "downsample_ratio": dr}})
    m = (pha[0,0] > 0.5).astype(np.uint8) * 255; out.write(cv2.merge([m, m, m]))
out.release()
subprocess.run(["ffmpeg","-v","error","-y","-i",dst+".avi","-c:v","libx264","-crf","0","-pix_fmt","yuv420p",dst], check=True); os.remove(dst+".avi")
'''
    sh([PY, "-c", code])
    n = int(subprocess.check_output(["ffprobe", "-v", "error", "-select_streams", "v:0", "-count_frames", "-show_entries",
                                     "stream=nb_read_frames", "-of", "csv=p=0", str(guide)]).decode().strip())
    return guide, mask, n


def wgp_settings(guide: Path, mask: Path, refs: list[Path], frames: int, prompt: str, seed: int, work: Path,
                 resolution: str = "480x832", n_refs: int = 1, name: str = "wgp.json") -> Path:
    t = {"model_type": "animate_14B_local_lightx2v", "prompt": prompt, "negative_prompt": NEG, "resolution": resolution,
         "video_length": frames, "sliding_window_size": 81, "sliding_window_overlap": 5, "num_inference_steps": 6, "seed": seed,
         "video_prompt_type": MODE_REPLACE, "video_guide": guide.as_posix(), "video_mask": mask.as_posix(),
         "image_refs": [r.as_posix() for r in refs[:n_refs]], "mask_expand": 20, "remove_background_images_ref": 0,
         "guidance_phases": 1, "guidance_scale": 1, "flow_shift": 5, "sample_solver": "euler", "force_fps": "16"}
    p = work / name
    p.write_text(json.dumps([t], indent=1), encoding="utf-8")
    return p


def _stop_wgp_child(process: subprocess.Popen) -> None:
    """Stop and reap only the WanGP process tree created by this runner."""
    if process.poll() is None:
        if os.name == "nt":
            try:
                subprocess.run(["taskkill", "/PID", str(process.pid), "/T", "/F"],
                               stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, timeout=5, check=False)
            except (OSError, subprocess.TimeoutExpired):
                pass
        else:
            try:
                os.killpg(process.pid, signal.SIGTERM)
            except (OSError, ProcessLookupError):
                process.terminate()
    try:
        process.wait(timeout=5)
    except subprocess.TimeoutExpired:
        process.kill()
        process.wait(timeout=5)


def _validate_wgp_output(path: Path) -> None:
    """Reject empty, damaged or undecodable output before reporting success."""
    try:
        probe = subprocess.run(
            ["ffprobe", "-v", "error", "-select_streams", "v:0", "-count_frames",
             "-show_entries", "stream=width,height,nb_read_frames", "-of", "json", str(path)],
            capture_output=True, check=True, timeout=120,
        )
        streams = json.loads(probe.stdout).get("streams", [])
        if not streams or any(int(streams[0].get(k, 0)) <= 0 for k in ("width", "height", "nb_read_frames")):
            raise ValueError("no decodable video frames")
        subprocess.run(["ffmpeg", "-v", "error", "-xerror", "-nostdin", "-i", str(path),
                        "-map", "0:v:0", "-f", "null", "-"],
                       stdout=subprocess.DEVNULL, stderr=subprocess.PIPE, check=True, timeout=120)
    except (OSError, ValueError, subprocess.SubprocessError) as exc:
        raise RuntimeError(f"Invalid or undecodable WanGP output: {path}") from exc


def run_wgp(settings: Path, outdir: Path, free_kill_gb=3.0, timeout_s=6 * 3600) -> Path:
    """Run one WanGP job and refuse timed-out, failed or stale output."""
    import psutil  # noqa: PLC0415
    outdir.mkdir(parents=True, exist_ok=True)
    env = dict(os.environ, MIOPEN_FIND_MODE="FAST", PYTHONUNBUFFERED="1", PYTHONIOENCODING="utf-8")
    before = {p: (p.stat().st_mtime_ns, p.stat().st_size) for p in outdir.glob("*.mp4")}
    log_path = outdir / "wgp.out.log"
    with log_path.open("wb") as log:
        kwargs = {} if os.name == "nt" else {"start_new_session": True}
        p = subprocess.Popen([str(PY), "wgp.py", "--process", str(settings), "--output-dir", str(outdir)],
                             cwd=str(WGP), env=env, stdout=log, stderr=subprocess.STDOUT, **kwargs)
        stopped_after_completion = False
        deadline = time.monotonic() + timeout_s
        try:
            while p.poll() is None:
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    raise TimeoutError("WanGP timeout: process stopped; output is not accepted")
                time.sleep(min(1.0, remaining))
                if psutil.virtual_memory().available / 2**30 < free_kill_gb:
                    raise RuntimeError("RAM guard: generator stopped; output is not accepted")
                with log_path.open("rb") as current_log:
                    current_log.seek(0, os.SEEK_END)
                    current_log.seek(max(0, current_log.tell() - 4000))
                    tail = current_log.read().decode("utf-8", "ignore")
                if "Queue completed" in tail:
                    grace = min(deadline, time.monotonic() + 15.0)
                    while p.poll() is None and time.monotonic() < grace:
                        time.sleep(min(1.0, grace - time.monotonic()))
                    if p.poll() is None:
                        if time.monotonic() >= deadline:
                            raise TimeoutError("WanGP timeout after completion marker; output is not accepted")
                        _stop_wgp_child(p)
                        stopped_after_completion = True
                    break
            if p.poll() != 0 and not stopped_after_completion:
                raise RuntimeError(f"WanGP failed with exit code {p.poll()}; see {log_path}")
        finally:
            if p.poll() is None:
                _stop_wgp_child(p)
    fresh = [v for v in outdir.glob("*.mp4")
             if (v.stat().st_mtime_ns, v.stat().st_size) != before.get(v)]
    if not fresh:
        raise RuntimeError("WanGP produced no new video, see " + str(log_path))
    output = max(fresh, key=lambda v: v.stat().st_mtime_ns)
    _validate_wgp_output(output)
    return output


def face_pass(video: Path, ref: Path, out: Path) -> Path:
    """FaceFusion hyperswap_1a_256 on the generated clip (stock content filter stays enabled)."""
    sh([FF_DIR / ".venv" / "Scripts" / "python.exe", "facefusion.py", "headless-run", "-s", ref, "-t", video, "-o", out,
        "--processors", "face_swapper", "--face-swapper-model", "hyperswap_1a_256", "--face-selector-mode", "one",
        "--execution-providers", "cpu", "--output-video-quality", "90"], cwd=str(FF_DIR),
       stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    return out


def finish(video: Path, source: Path, start: float, seconds: float | None, out: Path):
    cmd = ["ffmpeg", "-v", "error", "-y", "-i", video]
    if start:
        cmd += ["-ss", str(start)]
    if seconds:
        cmd += ["-t", str(seconds)]
    cmd += ["-i", source, "-vf", "scale=720:1248:flags=lanczos,pad=720:1280:0:16", "-map", "0:v", "-map", "1:a?", "-c:v", "libx264",
            "-crf", "18", "-pix_fmt", "yuv420p", "-c:a", "aac", "-af", "apad", "-shortest", "-movflags", "+faststart", out]
    sh(cmd)


HEAD_PROMPT = ("Close-up of the head and shoulders of the person from the reference image, natural skin, sharp eyes, "
               "same head motion as the guide, same lighting.")


def head_zoom_pass(gen: Path, video: Path, mask: Path, refs: list[Path], start: float, n: int, seed: int, work: Path,
                   swap_face: bool, quality_e: bool = False) -> Path:
    """Second Animate run on a tracked 512 head crop of the ORIGINAL video, pasted over the 720x1280 upscaled base (see head_zoom.py).
    Returns a silent 720x1280 mp4; the caller muxes the original audio."""
    import head_zoom  # noqa: PLC0415

    size = 768 if quality_e else 512
    hz = head_zoom.crop_heads(str(video), str(mask), start, start + n / 16, str(work / "hz"), mask_t0=start, size=size)
    settings = wgp_settings(Path(hz["guide"]), Path(hz["mask"]), refs, hz["frames"], HEAD_PROMPT, seed + 10, work,
                            resolution=f"{size}x{size}", n_refs=2, name="wgp_head.json")
    head = run_wgp(settings, work / "wgp_head_out")
    if swap_face:
        head = face_pass(head, refs[0], work / "head_facepass.mp4")
    base = work / "base720.mp4"
    sh(["ffmpeg", "-v", "error", "-y", "-i", gen, "-vf", "scale=720:1248:flags=lanczos,pad=720:1280:0:16", "-an",
        "-c:v", "libx264", "-crf", "12", "-pix_fmt", "yuv420p", base])
    pasted = work / "head_pasted.mp4"
    if quality_e:
        if __package__:
            from .genjutsu_composite_e import RVMForeground
            from .genjutsu_e_video import render_composite_e_video
        else:
            from genjutsu_composite_e import RVMForeground
            from genjutsu_e_video import render_composite_e_video
        render_composite_e_video(base=base, head=head, source=video, mask=mask,
                                 boxes=hz["boxes"], start=start, output=pasted,
                                 matte=RVMForeground(RVM))
    else:
        head_zoom.paste_back(str(base), str(head), hz["boxes"], str(pasted), hz["mask"])
    return pasted


def mux_audio(video: Path, source: Path, start: float, seconds: float | None, out: Path):
    cmd = ["ffmpeg", "-v", "error", "-y", "-i", video]
    if start:
        cmd += ["-ss", str(start)]
    if seconds:
        cmd += ["-t", str(seconds)]
    cmd += ["-i", source, "-map", "0:v", "-map", "1:a?", "-c:v", "libx264", "-crf", "16", "-pix_fmt", "yuv420p", "-c:a", "aac",
            "-af", "apad", "-shortest", "-movflags", "+faststart", out]
    sh(cmd)


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--video", required=True)
    ap.add_argument("--refs", nargs="+", required=True, help="1-3 photos; the first goes to Animate, the first is also the face-pass source")
    ap.add_argument("--prompt", required=True, help="describe the person's outfit as on the reference photo")
    ap.add_argument("--out", required=True)
    ap.add_argument("--start", type=float, default=0.0)
    ap.add_argument("--seconds", type=float, default=None)
    ap.add_argument("--seed", type=int, default=2601)
    ap.add_argument("--face-pass", action="store_true")
    ap.add_argument("--head-zoom", action="store_true",
                    help="second Animate run on a tracked 512 head crop pasted over the upscaled base (sharper, more recognisable face)")
    ap.add_argument("--quality-e", action="store_true", help="experimental 768px head pass with RVM composite; requires aligned 9:16 video")
    ap.add_argument("--work", default=None)
    ap.add_argument("--dry-run", action="store_true", help="prepare guide, mask and settings only")
    a = ap.parse_args(argv)
    out = Path(a.out)
    work = Path(a.work) if a.work else out.with_suffix("").parent / (out.stem + "-work")
    work.mkdir(parents=True, exist_ok=True)
    video = Path(a.video)
    refs = [Path(r) for r in a.refs]
    if a.quality_e:
        if not Path(RVM).is_file():
            raise FileNotFoundError(f"RVM model required for quality-e: {RVM}")
        if (work / "head_pasted.mp4").exists():
            raise FileExistsError("quality-e requires a fresh work directory")
        if __package__:
            from .genjutsu_e_video import _probe
        else:
            from genjutsu_e_video import _probe
        source = _probe(video)
        if source["width"] * 16 != source["height"] * 9:
            raise ValueError("quality-e requires a constant-frame-rate 9:16 source")
    t0 = time.time()
    guide, mask, n = prep(video, work, a.start, a.seconds)
    settings = wgp_settings(guide, mask, refs, n, a.prompt, a.seed, work)
    print(json.dumps({"guide": str(guide), "mask": str(mask), "frames": n, "settings": str(settings)}))
    if a.dry_run:
        return 0
    gen = run_wgp(settings, work / "wgp_out")
    if a.head_zoom or a.quality_e:
        pasted = head_zoom_pass(gen, video, mask, refs, a.start, n, a.seed, work, a.face_pass, quality_e=a.quality_e)
        mux_audio(pasted, video, a.start, a.seconds, out)
    else:
        if a.face_pass:
            gen = face_pass(gen, refs[0], work / "facepass.mp4")
        finish(gen, video, a.start, a.seconds, out)
    print(json.dumps({"out": str(out), "seconds": round(time.time() - t0)}))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
