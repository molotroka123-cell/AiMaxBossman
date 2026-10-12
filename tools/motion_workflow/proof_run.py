"""Product-path proof for the Motion workflow (not a unit test): the REAL app over loopback HTTP.

    set PYTHONPATH=<repo>/command-center;<repo>/bossman-core;<repo>/bossman-shared
    python tools/motion_workflow/proof_run.py OUT_DIR

What it does, in order (each step is recorded with its wall time in OUT_DIR/PROOF.md):

  A  generate a synthetic package (lavfi, no personal media), start the backend (the same ``create_app`` the
     product uses, own data dir, 127.0.0.1) and drive every API step: upload -> analysis -> references ->
     edits (text, subtitles, soundtrack, cover, a face replacement this renderer cannot run) -> limits /
     estimate / readiness BEFORE the run -> preview -> full render -> result files -> a no-text control render
     -> objective text-presence measurement (pixel difference against the control).
  B  stop the backend, start a NEW one over the same data dir, reopen the project and the finished job and
     verify the file and its hash again.
  C  start a slow render, kill the backend process hard in the middle of it (the ffmpeg child survives),
     start a new backend: the job is ``interrupted``, the orphaned ffmpeg is killed, resume finishes and
     verifies it.

Nothing leaves this PC, no paid API, no GPU.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import platform
import shutil
import socket
import subprocess
import sys
import time
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
for rel in ("command-center", "bossman-core", "bossman-shared", "tools/motion_workflow"):
    sys.path.insert(0, str(REPO / rel))

NO_WINDOW = 0x08000000 if os.name == "nt" else 0
BASE = "/api/motion-workflow"


# ============================================================================================ backend process

def backend_main(data_dir: str, port: int) -> None:
    """Run the product backend (same factory as the product) on 127.0.0.1 until it is killed."""
    import uvicorn
    from bcc.api import create_app
    from bcc.config import Settings
    data = Path(data_dir)
    settings = Settings(data_dir=data, database_url=f"sqlite+aiosqlite:///{data / 'bcc.db'}", ui_dir=REPO / "command-center" / "ui")
    app = create_app(settings, announce_token=False, start_workers=False)
    uvicorn.run(app, host="127.0.0.1", port=port, log_level="warning")


def free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


class Backend:
    def __init__(self, data_dir: Path, log_dir: Path, label: str) -> None:
        self.data_dir, self.label = data_dir, label
        self.port = free_port()
        env = dict(os.environ)
        guard = data_dir.parent / "guard"
        env.update({"BCC_DATA_DIR": str(data_dir), "BOSSMAN_TELEGRAM_CONFIG": str(guard / "telegram-companion" / "config.json"),
                    "BOSSMAN_COMPANION_CONFIG": str(guard / "telegram-companion" / "config.json"),
                    "PYTHONPATH": os.pathsep.join(str(REPO / p) for p in ("command-center", "bossman-core", "bossman-shared")),
                    "PYTHONIOENCODING": "utf-8"})
        self.log = open(log_dir / f"backend-{label}.log", "wb")
        self.proc = subprocess.Popen([sys.executable, str(Path(__file__).resolve()), "backend", str(data_dir), str(self.port)],
                                     stdout=self.log, stderr=subprocess.STDOUT, env=env, creationflags=NO_WINDOW)
        self.url = f"http://127.0.0.1:{self.port}"
        self.started = time.time()
        self.token = ""

    def wait_ready(self, timeout: float = 90.0) -> float:
        import httpx
        end = time.time() + timeout
        while time.time() < end:
            if self.proc.poll() is not None:
                raise SystemExit(f"backend {self.label} exited early with code {self.proc.returncode}")
            token_file = self.data_dir / "token"
            if token_file.is_file() and not self.token:
                self.token = token_file.read_text(encoding="utf-8").strip()
            try:
                r = httpx.get(self.url + "/", timeout=1.0, trust_env=False)
                if r.status_code < 500 and self.token:
                    return time.time() - self.started
            except httpx.HTTPError:
                pass
            time.sleep(0.2)
        raise SystemExit(f"backend {self.label} did not start in {timeout}s")

    def stop(self, hard: bool = False) -> None:
        if self.proc.poll() is None:
            if hard:
                self.proc.kill()
            else:
                self.proc.terminate()
            try:
                self.proc.wait(timeout=20)
            except subprocess.TimeoutExpired:
                self.proc.kill()
                self.proc.wait()
        self.log.close()


class Api:
    """httpx client with the owner token; every call is traced (method, path, status, milliseconds)."""

    def __init__(self, backend: Backend, trace: list) -> None:
        import httpx
        from bcc.auth import HEADER
        self.client = httpx.Client(base_url=backend.url, headers={HEADER: backend.token}, timeout=300.0, trust_env=False)
        self.trace = trace

    def call(self, method: str, path: str, **kw):
        started = time.perf_counter()
        r = self.client.request(method, BASE + path, **kw)
        self.trace.append({"method": method, "path": BASE + path, "status": r.status_code,
                           "ms": round((time.perf_counter() - started) * 1000, 1), "ts": time.strftime("%H:%M:%S")})
        return r

    def json(self, method: str, path: str, expect: int | tuple = 200, **kw) -> dict:
        r = self.call(method, path, **kw)
        ok = (expect,) if isinstance(expect, int) else expect
        if r.status_code not in ok:
            raise SystemExit(f"{method} {path} -> {r.status_code}: {r.text[:500]}")
        return r.json()

    def close(self) -> None:
        self.client.close()


# ============================================================================================ helpers

def sha256_file(path: Path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for block in iter(lambda: f.read(1 << 20), b""):
            h.update(block)
    return h.hexdigest()


def write_json(path: Path, obj) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(obj, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")


def wait_job(api: Api, pid: str, jid: str, *, until=("done", "failed", "interrupted", "cancelled", "preview_done"),
             timeout: float = 600.0, samples: list | None = None, stop_when=None) -> dict:
    end = time.time() + timeout
    t0 = time.perf_counter()
    while time.time() < end:
        job = api.json("GET", f"/projects/{pid}/jobs/{jid}")
        if samples is not None:
            samples.append({"t": round(time.perf_counter() - t0, 2), "state": job["state"], "stage": job.get("stage"),
                            "progress": job.get("progress"), "scenes_preview": len(job["completed"]["preview"]),
                            "scenes_render": len(job["completed"]["render"])})
        if stop_when and stop_when(job):
            return job
        if job["state"] in until:
            return job
        time.sleep(0.15)
    raise SystemExit(f"job {jid} did not settle in {timeout}s")


def ffprobe_json(ffprobe: str, path: Path) -> dict:
    done = subprocess.run([ffprobe, "-v", "error", "-print_format", "json", "-show_format", "-show_streams", str(path)],
                          capture_output=True, creationflags=NO_WINDOW)
    return json.loads(done.stdout.decode("utf-8", "replace"))


def grab_frame(ffmpeg: str, video: Path, t: float, out: Path) -> Path:
    subprocess.run([ffmpeg, "-hide_banner", "-loglevel", "error", "-y", "-ss", f"{t:.3f}", "-i", str(video),
                    "-frames:v", "1", "-update", "1", str(out)], check=True, creationflags=NO_WINDOW)
    return out


def changed_ratio(a: Path, b: Path, band: tuple[float, float], delta: int = 32) -> float:
    from PIL import Image, ImageChops, ImageStat
    with Image.open(a) as ia, Image.open(b) as ib:
        diff = ImageChops.difference(ia.convert("L"), ib.convert("L"))
        w, h = diff.size
        crop = diff.crop((0, int(h * band[0]), w, int(h * band[1])))
        return ImageStat.Stat(crop.point(lambda v: 255 if v > delta else 0)).mean[0] / 255.0


def contact_sheet(frames: list[tuple[Path, str]], out: Path) -> None:
    from PIL import Image, ImageDraw, ImageFont
    thumbs = []
    for path, caption in frames:
        with Image.open(path) as im:
            im = im.convert("RGB")
            im.thumbnail((640, 360))
            thumbs.append((im.copy(), caption))
    cols = 3
    rows = (len(thumbs) + cols - 1) // cols
    cw, ch = 640, 360 + 28
    sheet = Image.new("RGB", (cols * cw, rows * ch), (24, 24, 28))
    draw = ImageDraw.Draw(sheet)
    try:
        font = ImageFont.truetype(r"C:\Windows\Fonts\arial.ttf", 18)
    except OSError:
        font = ImageFont.load_default()
    for i, (im, caption) in enumerate(thumbs):
        x, y = (i % cols) * cw, (i // cols) * ch
        sheet.paste(im, (x, y))
        draw.text((x + 8, y + 360 + 4), caption, fill=(230, 230, 230), font=font)
    sheet.save(out)


def machine() -> dict:
    info = {"platform": platform.platform(), "python": sys.version.split()[0], "processor": platform.processor()}
    try:
        import psutil
        info["logical_cpus"] = psutil.cpu_count()
        info["ram_gb"] = round(psutil.virtual_memory().total / 2**30, 1)
    except Exception:  # noqa: BLE001
        pass
    return info


def ffmpeg_children(folder_hint: str) -> list[int]:
    """pids of ffmpeg processes whose command line names ``folder_hint``."""
    import psutil
    found = []
    for proc in psutil.process_iter(["pid", "name", "cmdline"]):
        try:
            if "ffmpeg" in (proc.info["name"] or "").lower() and any(folder_hint.lower() in str(a).lower()
                                                                     for a in proc.info["cmdline"] or []):
                found.append(proc.info["pid"])
        except Exception:  # noqa: BLE001
            continue
    return found


# ============================================================================================ the proof

class Steps:
    def __init__(self) -> None:
        self.rows: list[dict] = []

    def add(self, name: str, seconds: float, result: str, **extra) -> None:
        self.rows.append({"step": name, "seconds": round(seconds, 2), "result": result, **extra})
        print(f"[{len(self.rows):02d}] {name}: {result} ({seconds:.2f}s)", flush=True)


def run_proof(out: Path) -> int:
    import synthetic_package
    from bcc.motion_workflow import render as render_mod

    started_all = time.time()
    if out.exists() and any(out.iterdir()):
        raise SystemExit(f"{out} is not empty: choose a new folder")
    out.mkdir(parents=True, exist_ok=True)
    steps = Steps()
    trace: list[dict] = []
    env = render_mod.environment()
    if not env["ready"]:
        raise SystemExit("renderer not ready: " + "; ".join(env["problems"]))
    ffmpeg, ffprobe = env["ffmpeg"], env["ffprobe"]
    data_dir = out / "data-dir" / "data"
    data_dir.mkdir(parents=True)
    logs = out / "logs"
    logs.mkdir()

    # ------------------------------------------------------------------ package
    t = time.perf_counter()
    package = out / "package"
    info = synthetic_package.make_package(package, size="1280x720", fps=30, scene_seconds=4.0, music_seconds=10.0)
    from PIL import Image
    Image.new("RGB", (96, 96), (200, 120, 90)).save(package / "actor.png")
    steps.add("generate synthetic package (ffmpeg lavfi)", time.perf_counter() - t,
              f"{info['size']} {info['fps']} fps {info['duration']} s, 3 scenes, voice-like tone, {info['music_seconds']} s music wav")
    source_probe = ffprobe_json(ffprobe, package / "source.mp4")
    write_json(out / "ffprobe_source.json", source_probe)

    # ------------------------------------------------------------------ A: the journey
    backend = Backend(data_dir, logs, "A")
    ready = backend.wait_ready()
    steps.add("start backend A (create_app, 127.0.0.1)", ready, f"ready on {backend.url}")
    api = Api(backend, trace)
    try:
        t = time.perf_counter()
        status = api.json("GET", "/status")
        limits = api.json("GET", "/limits")
        write_json(out / "status.json", status)
        write_json(out / "limits_before_any_run.json", limits)
        steps.add("GET /status, /limits (before any project)", time.perf_counter() - t,
                  f"renderer ready={status['renderer']['ready']}, ffmpeg {status['renderer']['ffmpeg_version']}, "
                  f"max {limits['limits']['max_duration_seconds']} s / {limits['limits']['max_scenes']} scenes, cloud spend {limits['cost_model']['cloud_spend_usd']}")

        t = time.perf_counter()
        with open(package / "source.mp4", "rb") as fh:
            created = api.json("POST", "/projects", expect=201,
                               files={"file": ("synthetic_source.mp4", fh, "video/mp4")},
                               data={"title": "Motion workflow proof", "category": "motion"})
        pid = created["project"]["project_id"]
        steps.add("POST /projects (multipart upload of the source video)", time.perf_counter() - t,
                  f"project {pid}, staged {created['source']['staged']['file']} {created['source']['staged']['bytes']} bytes")

        t = time.perf_counter()
        analyzed = api.json("POST", f"/projects/{pid}/analyze")
        plan = analyzed["plan"]
        scenes = plan["scenes"]
        steps.add("POST /analyze", time.perf_counter() - t,
                  f"{len(scenes)} scenes: " + ", ".join(f"{s['id']} {s['start']:.1f}-{s['end']:.1f}s cut={s['cut_in']['type']} camera={s['camera_motion']['class']}" for s in scenes)
                  + f"; UNKNOWN fields kept: {analyzed['unknowns']['count']}")
        write_json(out / "analysis_summary.json", {"scenes": [{k: s[k] for k in ("id", "start", "end", "cut_in", "camera_motion", "text", "audio")} for s in scenes],
                                                    "unknowns": analyzed["unknowns"], "warnings": plan["analysis"]["warnings"]})

        t = time.perf_counter()
        for name, kind, file, ctype in (("music", "audio", "music.wav", "audio/wav"), ("actor", "character", "actor.png", "image/png")):
            with open(package / file, "rb") as fh:
                api.json("POST", f"/projects/{pid}/references", expect=201, files={"file": (file, fh, ctype)},
                         data={"name": name, "kind": kind})
        api.json("POST", f"/projects/{pid}/references", expect=201, json={"name": "title", "text": info["title"]})
        refs = api.json("GET", f"/projects/{pid}/references")
        steps.add("POST /references (@music audio, @actor image, @title text)", time.perf_counter() - t,
                  ", ".join(f"@{r['name']}:{r['kind']}:{r['bytes']}B" for r in refs["references"]))

        before = api.json("GET", f"/projects/{pid}")
        write_json(out / "before_edit_readiness_estimate_limits.json",
                   {"readiness": before["readiness"], "estimate": before["estimate"], "estimate_executed": before["estimate_executed"],
                    "limits": before["limits"], "execution_preview": before["execution_preview"]})

        edits = [
            ("scene 1: text = @title", [{"op": "set_policy", "scene": "s001", "element": "text", "policy": "replace",
                                         "reason": "proof: title over scene 1", "with": "@title"}]),
            ("scene 2: text from the scene prompt", [
                {"op": "set_policy", "scene": "s002", "element": "text", "policy": "replace", "reason": "proof: text over scene 2"},
                {"op": "set_prompt", "scene": "s002", "prompt": "Вторая сцена: градиент"}]),
            ("subtitles: 3 cues", [{"op": "set_track", "lane": "subtitles", "settings": {"enabled": True, "cues": [
                {"start": 0.8, "end": 3.5, "text": "Первая реплика: цветные полосы"},
                {"start": 4.5, "end": 7.0, "text": "Вторая реплика: градиент"},
                {"start": 8.5, "end": 11.5, "text": "Третья реплика: фрактал"}]}}]),
            ("soundtrack: replace with @music (loudnorm, fades)", [{"op": "set_soundtrack", "mode": "replace", "reference": "music",
                                                                   "options": {"loudnorm": True, "fade_in": 0.5, "fade_out": 1.0}}]),
            ("cover: 5.0 s + title", [{"op": "set_cover", "time": 5.0, "title": "Bossman Motion", "position": "center"}]),
            ("scene 3: face_character replace with @actor (not executable here)", [
                {"op": "set_policy", "scene": "s003", "element": "face_character", "policy": "replace",
                 "reason": "proof: the renderer must report this honestly", "with": "@actor"}]),
        ]
        t = time.perf_counter()
        version = api.json("GET", f"/projects/{pid}/plan")["version"]
        for label, ops in edits:
            res = api.json("POST", f"/projects/{pid}/ops", json={"ops": ops, "expected_version": version, "summary": label})
            version = res["version"]
        steps.add("POST /ops x6 (each one a new immutable version)", time.perf_counter() - t,
                  f"plan version {version}; execution preview: {res['execution_preview']['summary']}")
        write_json(out / "execution_preview_before_run.json", res["execution_preview"])
        stale = api.call("POST", f"/projects/{pid}/ops", json={"ops": [{"op": "set_title", "title": "stale"}], "expected_version": 2})
        steps.add("optimistic conflict (stale expected_version)", 0.0, f"HTTP {stale.status_code} {stale.json()['error']['code']}")

        readiness = api.json("GET", f"/projects/{pid}/readiness")
        estimate = api.json("GET", f"/projects/{pid}/estimate")
        write_json(out / "readiness_before_run.json", readiness)
        write_json(out / "estimate_before_run.json", estimate)

        # ---- preview, then full
        t = time.perf_counter()
        job = api.json("POST", f"/projects/{pid}/jobs", expect=202, json={"mode": "preview"})
        jid = job["job_id"]
        samples_preview: list = []
        job = wait_job(api, pid, jid, samples=samples_preview)
        steps.add("POST /jobs {mode: preview} -> poll", time.perf_counter() - t,
                  f"state {job['state']}, {len([o for o in job['outputs'] if o.startswith('preview/')])} preview frames, cover.png "
                  f"{'cover.png' in job['outputs']}")
        t = time.perf_counter()
        api.json("POST", f"/projects/{pid}/jobs/{jid}/render", expect=202)
        samples_render: list = []
        job = wait_job(api, pid, jid, samples=samples_render)
        wall_full = time.perf_counter() - t
        if job["state"] != "done":
            raise SystemExit(f"full render did not finish: {job['state']} {job.get('error')}")
        result = job["result"]
        steps.add("POST /jobs/{id}/render -> poll until done", wall_full,
                  f"state {job['state']}, verified={result['verified']}, {result['execution']['applied']} applied / "
                  f"{result['execution']['not_executed']} not executed, render {result['timing']['render_seconds']} s")
        write_json(out / "progress_samples.json", {"preview": samples_preview, "render": samples_render})
        write_json(out / "job_view.json", job)

        # ---- result files
        t = time.perf_counter()
        for name in ("output.mp4", "cover.png", "render_report.json", "render.log", "job.log"):
            r = api.call("GET", f"/projects/{pid}/jobs/{jid}/file", params={"name": name})
            if r.status_code != 200:
                raise SystemExit(f"download {name} -> {r.status_code}")
            (out / name).write_bytes(r.content)
        preview_dir = out / "preview_frames"
        preview_dir.mkdir()
        for name in job["outputs"]:
            if name.startswith("preview/"):
                r = api.call("GET", f"/projects/{pid}/jobs/{jid}/file", params={"name": name})
                (preview_dir / Path(name).name).write_bytes(r.content)
        steps.add("GET /jobs/{id}/file (output, cover, report, log, preview frames)", time.perf_counter() - t,
                  f"output.mp4 {(out / 'output.mp4').stat().st_size} bytes, sha256 {sha256_file(out / 'output.mp4')[:16]}...")
        out_probe = ffprobe_json(ffprobe, out / "output.mp4")
        write_json(out / "ffprobe.json", out_probe)
        check = api.json("GET", f"/projects/{pid}/jobs/{jid}/check")
        write_json(out / "check_after_render.json", check)
        steps.add("GET /jobs/{id}/check (ffprobe + full decode + sha256 vs report)", 0.0,
                  f"verified={check['verified']} matches_report={check['matches_report']}")

        # ---- plan versions
        hist = api.json("GET", f"/projects/{pid}/history", params={"limit": 100})
        plan_dir = out / "plan_versions"
        for entry in sorted(hist["history"], key=lambda e: e["version"]):
            rec = api.json("GET", f"/projects/{pid}/versions/{entry['version']}")
            write_json(plan_dir / f"v{entry['version']:03d}.json", {"version": rec["version"], "summary": rec["summary"],
                                                                      "author": rec["author"], "ts": rec["ts"], "plan": rec["plan"]})
        steps.add("GET /history + /versions/{n}", 0.0, f"{len(hist['history'])} versions saved, integrity problems: {hist['problems']}")

        # ---- control render (same source, NO text) for the objective text measurement
        t = time.perf_counter()
        with open(package / "source.mp4", "rb") as fh:
            ctl = api.json("POST", "/projects", expect=201, files={"file": ("control.mp4", fh, "video/mp4")},
                           data={"title": "control (no text)"})
        cpid = ctl["project"]["project_id"]
        api.json("POST", f"/projects/{cpid}/analyze")
        cj = api.json("POST", f"/projects/{cpid}/jobs", expect=202, json={"mode": "full"})
        cjob = wait_job(api, cpid, cj["job_id"])
        if cjob["state"] != "done":
            raise SystemExit("control render failed")
        r = api.call("GET", f"/projects/{cpid}/jobs/{cj['job_id']}/file", params={"name": "output.mp4"})
        (out / "control_output_no_text.mp4").write_bytes(r.content)
        steps.add("control: same source, no text, full render", time.perf_counter() - t, f"state {cjob['state']}")

        # ---- objective text presence
        t = time.perf_counter()
        tmp = out / "_frames"
        tmp.mkdir()
        samples = [  # (time, label, top text expected, bottom cue expected)
            (1.5, "s001 title + cue 1", True, True), (3.2, "s001 title + cue 1", True, True),
            (5.5, "s002 text + cue 2", True, True), (6.5, "s002 text + cue 2", True, True),
            (9.0, "s003 cue 3, no scene text", False, True), (11.0, "s003 cue 3, no scene text", False, True),
            (4.2, "s002 text, between cues", True, False), (8.0, "s003, nothing", False, False),
        ]
        top_band, bottom_band = (0.05, 0.32), (0.60, 0.97)
        rows = []
        baseline_noise = []
        for ts, label, top_exp, bot_exp in samples:
            res_f = grab_frame(ffmpeg, out / "output.mp4", ts, tmp / f"res_{ts}.png")
            ctl_f = grab_frame(ffmpeg, out / "control_output_no_text.mp4", ts, tmp / f"ctl_{ts}.png")
            src_f = grab_frame(ffmpeg, package / "source.mp4", ts, tmp / f"src_{ts}.png")
            row = {"t": ts, "label": label,
                   "top_changed_vs_control": round(changed_ratio(res_f, ctl_f, top_band), 5),
                   "bottom_changed_vs_control": round(changed_ratio(res_f, ctl_f, bottom_band), 5),
                   "top_expected": top_exp, "bottom_expected": bot_exp,
                   "control_vs_source_top": round(changed_ratio(ctl_f, src_f, top_band), 5),
                   "control_vs_source_bottom": round(changed_ratio(ctl_f, src_f, bottom_band), 5)}
            rows.append(row)
            baseline_noise.extend([row["control_vs_source_top"], row["control_vs_source_bottom"]])
        threshold = 0.004
        for row in rows:
            row["top_text_measured"] = row["top_changed_vs_control"] >= threshold
            row["bottom_text_measured"] = row["bottom_changed_vs_control"] >= threshold
            row["agrees_with_plan"] = (row["top_text_measured"] == row["top_expected"]
                                       and row["bottom_text_measured"] == row["bottom_expected"])
        write_json(out / "text_presence_measurement.json", {"threshold_changed_pixel_ratio": threshold,
                                                             "pixel_delta": 32, "bands": {"top": top_band, "bottom": bottom_band},
                                                             "max_codec_noise_control_vs_source": max(baseline_noise),
                                                             "rows": rows})
        agree = sum(1 for r in rows if r["agrees_with_plan"])
        steps.add("objective text presence: result vs no-text control", time.perf_counter() - t,
                  f"{agree}/{len(rows)} sampled frames agree with the plan (text where planned, none where not); "
                  f"max codec noise {max(baseline_noise):.5f} vs threshold {threshold}")
        sheet_times = [(1.5, "t=1.5 s  scene 1: title + cue 1"), (3.2, "t=3.2 s  scene 1"), (5.5, "t=5.5 s  scene 2: text + cue 2"),
                       (6.5, "t=6.5 s  scene 2"), (9.0, "t=9.0 s  scene 3: cue 3"), (11.0, "t=11.0 s  scene 3")]
        contact_sheet([(tmp / f"res_{ts}.png" if (tmp / f"res_{ts}.png").exists() else grab_frame(ffmpeg, out / "output.mp4", ts, tmp / f"res_{ts}.png"), cap)
                       for ts, cap in sheet_times], out / "contact_sheet.png")
        shutil.rmtree(tmp)
        sha_first = sha256_file(out / "output.mp4")
    finally:
        api.close()
        backend.stop()
    steps.add("stop backend A", 0.0, f"process terminated by the driver (exit code {backend.proc.returncode})")

    # ------------------------------------------------------------------ B: restart over the same data dir
    backend_b = Backend(data_dir, logs, "B")
    ready = backend_b.wait_ready()
    steps.add("start backend B over the SAME data dir (a new app object)", ready, f"ready on {backend_b.url}")
    api = Api(backend_b, trace)
    try:
        t = time.perf_counter()
        listed = api.json("GET", "/projects")
        reopened = api.json("GET", f"/projects/{pid}")
        job_b = api.json("GET", f"/projects/{pid}/jobs/{jid}")
        check_b = api.json("GET", f"/projects/{pid}/jobs/{jid}/check")
        r = api.call("GET", f"/projects/{pid}/jobs/{jid}/file", params={"name": "output.mp4"})
        (out / "output_after_restart.mp4").write_bytes(r.content)
        status_b = api.json("GET", "/status")
        same = sha256_file(out / "output_after_restart.mp4") == sha_first
        write_json(out / "after_restart.json", {
            "projects": [{k: p[k] for k in ("project_id", "title", "version", "scenes", "has_output")} for p in listed["projects"]],
            "reopened_version": reopened["version"], "reopened_cover": reopened["plan"].get("cover"),
            "job_state": job_b["state"], "job_verified": job_b["result"]["verified"],
            "check": {k: check_b[k] for k in ("verified", "matches_report", "sha256", "bytes")},
            "recovery": status_b["recovery"], "output_sha256_same_as_before_restart": same})
        steps.add("restart: reopen project + finished job, re-verify the file", time.perf_counter() - t,
                  f"projects {len(listed['projects'])}, job {job_b['state']}, verified={check_b['verified']}, "
                  f"sha256 identical to before restart: {same}")
        if not (same and check_b["verified"] and job_b["state"] == "done"):
            raise SystemExit("restart check failed")

        # ------------------------------------------------------------------ C: hard kill in the middle of a render
        t = time.perf_counter()
        crash_pkg = out / "package_crash"
        synthetic_package.make_package(crash_pkg, size="1280x720", fps=30, scene_seconds=8.0, music_seconds=10.0)
        with open(crash_pkg / "source.mp4", "rb") as fh:
            cr = api.json("POST", "/projects", expect=201, files={"file": ("long_source.mp4", fh, "video/mp4")},
                          data={"title": "crash recovery (24 s, slow encode)"})
        cpid2 = cr["project"]["project_id"]
        api.json("POST", f"/projects/{cpid2}/analyze")
        api.json("POST", f"/projects/{cpid2}/ops", json={"ops": [{"op": "set_render", "settings": {"preset": "veryslow", "crf": 0}}]})
        cjob = api.json("POST", f"/projects/{cpid2}/jobs", expect=202, json={"mode": "full"})
        cjid = cjob["job_id"]
        killed_at = wait_job(api, cpid2, cjid, stop_when=lambda j: j.get("stage") == "render" and (j.get("progress") or 0) > 0.2)
        folder_hint = f"{cpid2}"
        ffmpeg_before = ffmpeg_children(folder_hint)
        pre_kill = {"state": killed_at["state"], "stage": killed_at["stage"], "progress": killed_at["progress"],
                    "scenes_rendered": killed_at["completed"]["render"], "ffmpeg_pids": ffmpeg_before}
        backend_b.stop(hard=True)
        time.sleep(0.5)
        ffmpeg_orphans = ffmpeg_children(folder_hint)
        steps.add("hard-kill backend B in the middle of a slow render", time.perf_counter() - t,
                  f"job was {pre_kill['state']}/{pre_kill['stage']} at {pre_kill['progress']:.2f}; ffmpeg children alive after the kill: {ffmpeg_orphans}")
    finally:
        api.close()
        backend_b.stop(hard=True)

    backend_c = Backend(data_dir, logs, "C")
    ready = backend_c.wait_ready()
    api = Api(backend_c, trace)
    try:
        steps.add("start backend C over the same data dir", ready, f"ready on {backend_c.url}")
        t = time.perf_counter()
        status_c = api.json("GET", "/status")
        jc = api.json("GET", f"/projects/{cpid2}/jobs/{cjid}")
        time.sleep(0.5)
        still = ffmpeg_children(folder_hint)
        write_json(out / "crash_recovery_after_restart.json", {
            "before_kill": pre_kill, "ffmpeg_alive_after_kill": ffmpeg_orphans, "ffmpeg_alive_after_restart": still,
            "recovery": status_c["recovery"], "job_after_restart": {k: jc[k] for k in ("state", "store_state", "phase", "interrupted", "next_scene", "completed", "attempt", "error")},
            "outputs_listed": jc["outputs"]})
        steps.add("restart recovery: job state, orphaned ffmpeg", time.perf_counter() - t,
                  f"job {jc['state']} ({jc['interrupted']['reason']}), orphans killed by startup: {status_c['recovery']['orphans_killed']}, "
                  f"ffmpeg still alive: {still}, output.mp4 listed: {'output.mp4' in jc['outputs']}")
        if jc["state"] != "interrupted" or still:
            raise SystemExit("crash recovery check failed")
        t = time.perf_counter()
        api.json("POST", f"/projects/{cpid2}/jobs/{cjid}/resume", expect=202)
        done = wait_job(api, cpid2, cjid, timeout=900)
        chk = api.json("GET", f"/projects/{cpid2}/jobs/{cjid}/check")
        write_json(out / "crash_recovery_resumed.json", {"state": done["state"], "attempt": done["attempt"],
                                                         "completed": done["completed"], "result_verified": done["result"]["verified"],
                                                         "check": {k: chk[k] for k in ("verified", "matches_report", "sha256", "bytes")}})
        steps.add("resume the interrupted job", time.perf_counter() - t,
                  f"state {done['state']}, attempt {done['attempt']}, verified={chk['verified']}, sha256 {chk['sha256'][:16]}...")
        if done["state"] != "done" or not chk["verified"]:
            raise SystemExit("resume did not finish a verified file")
    finally:
        api.close()
        backend_c.stop()

    write_json(out / "api_trace.json", trace)
    # the temporary instance's access token and database are not evidence and must not be kept or shared
    for leftover in [data_dir / "token", *data_dir.glob("bcc.db*")]:
        try:
            leftover.unlink()
        except OSError:
            pass
    shutil.rmtree(data_dir.parent / "guard", ignore_errors=True)
    write_proof(out, steps, info, status, result, check, readiness, estimate, rows, threshold, machine(), time.time() - started_all,
                source_probe, out_probe, pre_kill, ffmpeg_orphans)
    print(f"PROOF written to {out / 'PROOF.md'}")
    return 0


def write_proof(out: Path, steps: Steps, info: dict, status: dict, result: dict, check: dict, readiness: dict, estimate: dict,
                rows: list, threshold: float, mach: dict, wall: float, source_probe: dict, out_probe: dict, pre_kill: dict,
                orphans: list) -> None:
    def fnum(v, n=2):
        return "-" if v is None else f"{float(v):.{n}f}"

    vid = next(s for s in out_probe["streams"] if s["codec_type"] == "video")
    aud = next((s for s in out_probe["streams"] if s["codec_type"] == "audio"), {})
    svid = next(s for s in source_probe["streams"] if s["codec_type"] == "video")
    files = []
    for p in sorted(out.rglob("*")):
        if p.is_file() and "data-dir" not in p.parts and "logs" not in p.parts:
            rel = p.relative_to(out).as_posix()
            if p.stat().st_size < 400_000_000:
                files.append((rel, p.stat().st_size, sha256_file(p)[:16] if p.stat().st_size > 0 else ""))
    try:
        sha = subprocess.run(["git", "rev-parse", "HEAD"], cwd=str(REPO), capture_output=True, text=True,
                             creationflags=NO_WINDOW).stdout.strip()
        dirty = bool(subprocess.run(["git", "status", "--porcelain", "--", "command-center/bcc/motion_workflow",
                                     "command-center/bcc/features/motion_workflow_api.py"], cwd=str(REPO),
                                    capture_output=True, text=True, creationflags=NO_WINDOW).stdout.strip())
    except OSError:
        sha, dirty = "unknown", False
    lines = [
        "# Motion workflow: product-path proof",
        "",
        f"Run: {time.strftime('%Y-%m-%d %H:%M:%S')} · wall {wall:.0f} s · repo `{REPO}` @ `{sha[:12]}`"
        f"{' (renderer / API files modified since that commit)' if dirty else ''}",
        "",
        "Goal: input materials -> analysis / plan -> edit -> run -> progress -> result -> reopen and recovery after a backend",
        "restart, through the REAL app (`bcc.api.create_app`, own data dir, 127.0.0.1) with a REAL local ffmpeg renderer.",
        "Nothing leaves this PC, no paid API, no GPU. This extends Motion Studio; it is not a Katana parity claim.",
        "",
        "## Exact commands",
        "",
        "```",
        "set PYTHONPATH=<repo>\\command-center;<repo>\\bossman-core;<repo>\\bossman-shared",
        f"python tools/motion_workflow/proof_run.py \"{out}\"",
        "# the package alone:  python tools/motion_workflow/synthetic_package.py <dir> --size 1280x720 --fps 30 --scene-seconds 4",
        "# the tests:          python -m pytest command-center/tests/test_motion_workflow_render.py "
        "command-center/tests/test_motion_workflow_api.py command-center/tests/test_motion_workflow_ui.py",
        "```",
        "",
        "## Environment",
        "",
        f"- machine: {mach.get('platform')} · CPU {mach.get('processor')} · {mach.get('logical_cpus')} logical CPUs · {mach.get('ram_gb')} GB RAM · Python {mach.get('python')}",
        f"- ffmpeg: {status['renderer']['ffmpeg_version']} (font {status['renderer']['font']}, drawtext {status['renderer']['drawtext']})",
        f"- package: {info['size']} @ {info['fps']} fps, {info['duration']} s, 3 scenes (testsrc2 / gradients / mandelbrot), "
        f"voice-like tone, {info['music_seconds']} s music, title `{info['title']}`",
        "",
        "## Steps (wall time of each)",
        "",
        "| # | step | seconds | result |",
        "|---|---|---|---|",
    ]
    for i, row in enumerate(steps.rows, 1):
        lines.append(f"| {i} | {row['step']} | {row['seconds']} | {str(row['result']).replace('|', '/')} |")
    ex = result["execution"]
    lines += [
        "",
        "## Result of the full render (from `render_report.json`)",
        "",
        f"- state: **done**, verified: **{result['verified']}** (ffprobe facts vs the promise + full decode, then `GET /check` again: "
        f"verified={check['verified']}, sha256 matches report={check['matches_report']})",
        f"- source: {svid['codec_name']} {svid['width']}x{svid['height']} {svid['avg_frame_rate']} fps {source_probe['format']['duration'][:6]} s",
        f"- output: {vid['codec_name']} ({vid.get('profile')}) {vid['pix_fmt']} {vid['width']}x{vid['height']} {vid['avg_frame_rate']} fps, "
        f"{out_probe['format']['duration'][:6]} s, audio {aud.get('codec_name')} {aud.get('sample_rate')} Hz {aud.get('channels')} ch, "
        f"{out_probe['format']['size']} bytes",
        f"- operations: requested {ex['requested']}, applied {ex['applied']}, **not executed {ex['not_executed']}**, complete={ex['complete']}",
        "- audio: " + json.dumps({k: result['parameters']['audio'].get(k) for k in ("action", "reference", "applied_seconds", "padded_silence_seconds", "fade_in_applied", "fade_out_applied")}, ensure_ascii=False),
        "- loudnorm: " + json.dumps(result['parameters']['audio'].get('loudnorm'), ensure_ascii=False),
        f"- timing: {json.dumps(result['timing'], ensure_ascii=False)}",
        f"- estimate before the run (declared constants, not measured): preview {estimate['preview_seconds']} s / render {estimate['render_seconds']} s "
        f"for the full plan; only what this renderer executes: render {estimate['executed_only']['render_seconds']} s. "
        f"Actual render: {result['timing']['render_seconds']} s.",
        f"- cloud spend {result['cloud_spend_usd']} USD, paid calls {result['paid_calls']}",
        "",
        "### Not executed (reported per scene, with the reason)",
        "",
    ]
    for n in result["not_executed"]:
        lines.append(f"- scene {n['scene_id']} · {n['element']} · `{n['reason_code']}`: {n['reason']}")
    lines += [
        "",
        "## Text presence, measured (not eyeballed)",
        "",
        f"Result frames vs frames of a control render of the same source WITHOUT any text; a band counts as containing text when "
        f"the share of pixels that changed by more than 32 grey levels is >= {threshold}. The control itself differs from the "
        f"source only by codec noise (max {max(r['control_vs_source_top'] for r in rows):.5f} top / "
        f"{max(r['control_vs_source_bottom'] for r in rows):.5f} bottom).",
        "",
        "| t (s) | frame | top band changed | text expected | measured | bottom band changed | text expected | measured | agrees |",
        "|---|---|---|---|---|---|---|---|---|",
    ]
    for r in rows:
        lines.append(f"| {r['t']} | {r['label']} | {r['top_changed_vs_control']:.5f} | {r['top_expected']} | {r['top_text_measured']} | "
                     f"{r['bottom_changed_vs_control']:.5f} | {r['bottom_expected']} | {r['bottom_text_measured']} | {r['agrees_with_plan']} |")
    lines += [
        "",
        "Contact sheet of 6 result frames with the overlay text: `contact_sheet.png`.",
        "",
        "## Restart and recovery",
        "",
        "- B: a NEW backend process over the same data dir lists the project, reopens the finished job, `GET /check` verifies the file and "
        "its sha256 equals the one before the restart (`after_restart.json`).",
        f"- C: backend killed hard in the middle of a slow render ({pre_kill['state']}/{pre_kill['stage']} at {fnum(pre_kill['progress'])}); "
        f"ffmpeg children alive after the kill: {orphans}; a new backend marked the job `interrupted`, killed the orphan "
        "(`orphans_killed`), refused to list an output, and `resume` finished it (attempt 2) with a verified file "
        "(`crash_recovery_after_restart.json`, `crash_recovery_resumed.json`).",
        "",
        "## Files",
        "",
        "| file | bytes | sha256 (first 16) |",
        "|---|---|---|",
    ]
    for rel, size, sha in files:
        lines.append(f"| `{rel}` | {size} | `{sha}` |")
    lines += [
        "",
        "## What was NOT done (honest list)",
        "",
        "- Face / character, clothes, background and product replacement are NOT executed by this renderer (no face tool, no masks, "
        "no generative step, no GPU). The scene 3 face replacement above is reported `not_executed`; its picture is the source.",
        "- Speech and music were not separated: the original soundtrack is one mixed stream, so replacing or muting only one of the "
        "lanes is reported `not_executed`; here the whole soundtrack was replaced (speech lane muted by the same edit).",
        "- On-screen text of the SOURCE was not read (no OCR engine on this machine): `text.status` stays `UNKNOWN: ocr unavailable`; "
        "new text is drawn on top and never erases text already in the picture.",
        "- Speech / music detectors, beat tracking quality, transition and camera classification were not validated on real footage; "
        "only synthetic scenes were used. No real owner material, no real music, no real voice.",
        "- Only 3 scenes, 12 s and 24 s clips, 720p; the 180 s / 150 scene limits were not exercised end to end here.",
        "- Windows 11 only; one backend process per data dir; no concurrent-project stress; upload has no progress bar.",
        "- The render is a single ffmpeg pass: scene completion is derived from encoder progress and a resumed render re-encodes "
        "from the start (the interrupted attempt's output is discarded, never reused).",
        "- Cost / time estimate constants are declared, not calibrated; the table above compares them with the measured run.",
        "- No comparison with Higgsfield Katana was made; nothing here supports a parity or superiority claim.",
        "- The browser UI is covered by `command-center/tests/test_motion_workflow_ui.py` (real Chromium); this proof drives the API.",
        "- Backends here are started as separate `create_app` processes with `start_workers=False`; the installed Windows bundle "
        "(its own Python, its ffmpeg path) was not exercised.",
        "- The access token and database of the temporary instance were deleted from `data-dir` after the run; the project folders "
        "(`data-dir/data/motion-workflow`) are kept as evidence.",
        "",
    ]
    (out / "PROOF.md").write_text("\n".join(lines), encoding="utf-8")


def main() -> int:
    if len(sys.argv) >= 2 and sys.argv[1] == "backend":
        backend_main(sys.argv[2], int(sys.argv[3]))
        return 0
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("out", type=Path)
    args = ap.parse_args()
    return run_proof(args.out)


if __name__ == "__main__":
    sys.exit(main())
