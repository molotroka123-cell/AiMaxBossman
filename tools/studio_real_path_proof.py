"""Live proof of the REAL Studio preset path on the owner's PC (owner item 6, 01.11 «ЧЕСТНО ДОВЕДИ STUDIO»).

    python tools/studio_real_path_proof.py --work <temp dir> [--port 8893] [--width 272 --height 480 --duration 2]
           [--steps 20] [--out docs/audits/2026-11-01-studio-real-path]

It never touches the owner's backend (:8800/:8801 are refused) or data: it starts its OWN backend (own port, temp
BCC_DATA_DIR and LOCALAPPDATA, no worker loops, BOSSMAN_OLLAMA_URL pointed at a closed port so nothing can unload the
owner's Ollama models) with this checkout's code, and the real model files on D: read-only. Synthetic object prompt
only (a plain ceramic mug, no people, no logos).

Phases (each result goes into receipt.json; nothing is assumed):
  A. machine state: foreign sd-cli / ComfyUI / WanGP processes, Ollama /api/ps, free RAM vs what Wan2.2 5B needs.
     Busy or short of memory -> wait (--wait-min), then BLOCKED with the exact reason. No render is faked.
  B. negative, engine missing: a backend whose media root is empty -> render-preset 503 render_engine_missing.
  C. real backend: negatives via HTTP (default 1080x1920 6 s preset -> 422; face line -> 409; an unrendered job
     cannot pass QC -> 409), then ONE real render through POST /api/studio/orders/{id}/render-preset.
  D. independent checks of the delivered file and of Direct Gen's raw output: ffprobe JSON, sha256, wall time,
     decoded-frame measurements (bcc.direct_gen.video_measure) and bcc.art_v2.vlm_critic bound to the file.
  E. file-level negatives: a PNG renamed .mp4, a 1-frame mp4 -> not a video; constants / another file -> critic
     REFUSED.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import shutil
import subprocess
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

import httpx

ROOT = Path(__file__).resolve().parents[1]
CC = ROOT / "command-center"
sys.path.insert(0, str(CC))

from bcc.art_v2.vlm_critic import VLMArtCritic  # noqa: E402
from bcc.direct_gen import sdcli  # noqa: E402
from bcc.direct_gen.orders import probe_video  # noqa: E402
from bcc.direct_gen.store import JobStore  # noqa: E402
from bcc.direct_gen.video_measure import measure_video, sha256_file  # noqa: E402

CLIENT = hashlib.sha256(b"studio-real-path-proof-client-20261101").hexdigest()
BRIEF = "a plain matte white ceramic coffee mug, no people, no hands, no text, no logos"
FFMPEG, FFPROBE = shutil.which("ffmpeg"), shutil.which("ffprobe")
GPU_NAMES = ("sd-cli", "sd-server", "comfyui", "wangp", "wan2gp", "facefusion")


def now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def machine_state() -> dict:
    import psutil
    busy = []
    for p in psutil.process_iter(["pid", "name", "cmdline"]):
        try:
            name = (p.info["name"] or "").lower()
            cmd = " ".join(p.info["cmdline"] or []).lower()
        except Exception:  # noqa: BLE001
            continue
        if name.startswith(("sd-cli", "sd-server")) or (
                "python" in name and any(k in cmd for k in ("comfyui\\main.py", "comfyui/main.py", "wgp.py",
                                                             "wan2gp", "facefusion.py run"))):
            busy.append({"pid": p.info["pid"], "name": name})
    try:
        ps = httpx.get("http://127.0.0.1:11434/api/ps", timeout=5).json().get("models") or []
        ollama = [{"name": m.get("name"), "size_gb": round((m.get("size") or 0) / 2**30, 1)} for m in ps]
    except Exception as exc:  # noqa: BLE001
        ollama = [{"error": type(exc).__name__}]
    spec = sdcli.BY_ID["wan2.2-ti2v-5b"]
    need = sdcli.required_bytes(spec, sdcli.default_media_root())
    free = psutil.virtual_memory().available
    return {"at": now(), "gpu_jobs": busy, "ollama_loaded": ollama, "free_ram_gb": round(free / 2**30, 1),
            "wan22_5b_needs_gb": round(need / 2**30, 1), "enough_ram": free >= need}


class Backend:
    """This checkout's app on its own port and temp data (no worker loops). Stopped by the tool itself."""

    def __init__(self, work: Path, port: int, *, media_dir: str | None):
        self.work, self.port = work, port
        self.data = work / "data"
        self.data.mkdir(parents=True, exist_ok=True)
        env = dict(os.environ)
        env.update({"PYTHONPATH": str(CC), "BCC_DATA_DIR": str(self.data), "BCC_PORT": str(port),
                    "LOCALAPPDATA": str(work / "localappdata"), "BCC_PROJECTS_ROOT": str(work / "projects"),
                    "BOSSMAN_TELEGRAM_CONFIG": str(work / "tg" / "config.json"),
                    "BOSSMAN_COMPANION_CONFIG": str(work / "tg" / "config.json"),
                    "BOSSMAN_OLLAMA_URL": "http://127.0.0.1:9", "BOSSMAN_CHAT_VISION": "off",
                    "BOSSMAN_DIRECT_GEN_RESIDENT": "off"})
        if media_dir is not None:
            env["BOSSMAN_MEDIA_MODELS"] = media_dir
        (work / "localappdata").mkdir(parents=True, exist_ok=True)
        code = ("import bcc, uvicorn; from bcc.api import create_app; print('BCC_FILE', bcc.__file__, flush=True); "
                f"uvicorn.run(create_app(start_workers=False), host='127.0.0.1', port={port}, log_level='warning')")
        self.log = (work / f"backend-{port}.log").open("w", encoding="utf-8")
        self.proc = subprocess.Popen([sys.executable, "-c", code], env=env, cwd=str(work), stdout=self.log,
                                     stderr=subprocess.STDOUT)
        self.base = f"http://127.0.0.1:{port}"
        t0 = time.monotonic()
        deadline = t0 + 900            # first start measured > 180 s on the owner PC (01.11)
        while True:
            try:       # without worker loops /health is 503 "not ready" by design; the server is up when alive
                if httpx.get(self.base + "/health", timeout=3).json().get("liveness") is True:
                    break
            except (httpx.HTTPError, ValueError):
                pass
            if self.proc.poll() is not None or time.monotonic() > deadline:
                self.stop()            # never leave our own backend behind
                raise SystemExit(f"temp backend did not start, see {self.log.name}")
            time.sleep(1)
        self.startup_s = round(time.monotonic() - t0, 1)
        token = (self.data / "token").read_text(encoding="utf-8").strip()
        self.c = httpx.Client(base_url=self.base, headers={"X-BCC-Token": token, "X-Participant": "owner"},
                              timeout=httpx.Timeout(30, read=None))
        self.health = self.c.get("/health").json()

    def bcc_file(self) -> str:
        self.log.flush()
        for line in (self.work / f"backend-{self.port}.log").read_text(encoding="utf-8").splitlines():
            if line.startswith("BCC_FILE"):
                return line.split(" ", 1)[1]
        return "unknown"

    def preset(self, **body) -> dict:
        r = self.c.post(f"/api/studio/orders/preset?client={CLIENT}",
                        json={"preset": "product_spin", "brief": BRIEF, "seed": 20261101, **body})
        r.raise_for_status()
        oid = r.json()["order_id"]
        self.c.post(f"/api/studio/orders/{oid}/accept?client={CLIENT}").raise_for_status()
        return r.json()

    def render(self, oid: str, max_s: float | None = None) -> httpx.Response:
        return self.c.post(f"/api/studio/orders/{oid}/render-preset?client={CLIENT}",
                           timeout=httpx.Timeout(30, read=max_s))

    def order(self, oid: str) -> dict:
        return self.c.get(f"/api/studio/orders/{oid}?client={CLIENT}").json()

    def stop(self) -> None:
        if self.proc.poll() is None:
            self.proc.terminate()
            try:
                self.proc.wait(30)
            except subprocess.TimeoutExpired:
                self.proc.kill()
        if not self.log.closed:
            self.log.close()


def err(resp: httpx.Response) -> dict:
    try:
        e = resp.json().get("error") or resp.json().get("detail") or {}
    except ValueError:
        e = {}
    return {"http_status": resp.status_code, "code": e.get("code"),
            "problems": [{k: v for k, v in p.items() if k in ("code", "reason", "limits", "requested", "engine")}
                         for p in (e.get("problems") or [])]}


def ffprobe_json(path: Path) -> dict:
    out = subprocess.run([FFPROBE, "-v", "error", "-count_frames", "-print_format", "json", "-show_format",
                          "-show_streams", str(path)], capture_output=True, text=True, timeout=300, check=True)
    info = json.loads(out.stdout)
    for s in info.get("streams") or []:
        s.pop("disposition", None)
    info.get("format", {}).pop("filename", None)
    return info


def critic_view(r) -> dict:
    return {"status": r.status, "score": r.score, "refusal": r.refusal, "non_black_pass": r.non_black_pass,
            "sharpness_pass": r.sharpness_pass, "motion_pass": r.motion_pass, "face_cosine_pass": r.face_cosine_pass,
            "artifacts_clean_pass": r.artifacts_clean_pass, "media_sha256": r.media_sha256,
            "receipt_id": r.receipt_id, "signature": r.signature}


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--work", required=True)
    ap.add_argument("--port", type=int, default=8893)
    ap.add_argument("--width", type=int, default=272)
    ap.add_argument("--height", type=int, default=480)
    ap.add_argument("--duration", type=float, default=2.0)
    ap.add_argument("--steps", type=int, default=20)
    ap.add_argument("--wait-min", type=float, default=30.0)
    ap.add_argument("--max-render-min", type=float, default=120.0)
    ap.add_argument("--out", default=str(ROOT / "docs" / "audits" / "2026-11-01-studio-real-path"))
    a = ap.parse_args()
    if a.port in (8800, 8801):
        raise SystemExit("refusing the owner's backend ports; use a temp port")
    work, out = Path(a.work), Path(a.out)
    out.mkdir(parents=True, exist_ok=True)
    git_sha = subprocess.run(["git", "-C", str(ROOT), "rev-parse", "HEAD"], capture_output=True, text=True).stdout.strip()
    receipt: dict = {"topic": "studio real path (owner item 6)", "started_at": now(), "code_sha": git_sha,
                     "inputs": {"preset": "product_spin", "brief": BRIEF, "width": a.width, "height": a.height,
                                "duration_s": a.duration, "steps": a.steps, "seed": 20261101,
                                "model": "wan2.2-ti2v-5b", "synthetic": True, "faces": False},
                     "temp_backend": {"port": a.port, "data_dir": "temp under %TEMP% (not the owner's data)",
                                      "workers": False,
                                      "ollama_url": "http://127.0.0.1:9 (closed: owner's Ollama untouched)"}}

    def finish(status: str, reason: str = "") -> int:
        receipt.update(status=status, finished_at=now(), **({"reason": reason} if reason else {}))
        (out / "receipt.json").write_text(json.dumps(receipt, indent=1, ensure_ascii=False) + "\n", encoding="utf-8")
        print(json.dumps({"status": status, "reason": reason}, ensure_ascii=False))
        return 0 if status == "PASS" else 1

    if not (FFMPEG and FFPROBE):
        return finish("BLOCKED", "ffmpeg/ffprobe not installed")
    # ---- A. machine state (wait while another GPU job runs or memory is short) ----
    deadline = time.monotonic() + a.wait_min * 60
    while True:
        state = machine_state()
        if not state["gpu_jobs"] and state["enough_ram"]:
            break
        if time.monotonic() > deadline:
            receipt["machine_before"] = state
            return finish("BLOCKED", "machine busy: " + json.dumps({k: state[k] for k in ("gpu_jobs", "free_ram_gb",
                                                                                         "wan22_5b_needs_gb")}))
        time.sleep(30)
    receipt["machine_before"] = state

    # ---- B. negative: engine missing (empty media root) -> 503 ----
    empty = work / "empty-media"
    empty.mkdir(parents=True, exist_ok=True)
    be = Backend(work / "b-missing", a.port, media_dir=str(empty))
    try:
        o = be.preset(width=a.width, height=a.height, duration_s=a.duration, steps=a.steps)
        r = be.render(o["order_id"])
        receipt["negative_engine_missing"] = {**err(r), "order_status_after": be.order(o["order_id"])["status"]}
    finally:
        be.stop()

    # ---- C. real backend ----
    be = Backend(work / "b-real", a.port, media_dir=None)
    try:
        receipt["temp_backend"].update(bcc_imported_from=be.bcc_file(), build_sha=be.health.get("build_sha"),
                                       startup_s=be.startup_s)
        neg = {}
        o = be.preset()                                                  # the preset as defined: 1080x1920, 6 s
        neg["default_preset_1080x1920_6s"] = {**err(be.render(o["order_id"])),
                                             "order_status_after": be.order(o["order_id"])["status"]}
        o = be.preset(width=a.width, height=a.height, duration_s=a.duration, face=True)
        neg["face_line"] = {**err(be.render(o["order_id"])), "order_status_after": be.order(o["order_id"])["status"]}
        o = be.preset(width=a.width, height=a.height, duration_s=a.duration)
        be.c.post(f"/api/studio/orders/{o['order_id']}/start?client={CLIENT}").raise_for_status()
        q = be.c.post(f"/api/studio/orders/{o['order_id']}/qc?client={CLIENT}", json={"passed": True})
        neg["queued_job_cannot_pass_qc"] = {**err(q), "job_status": "queued"}
        receipt["negative_api"] = neg

        state = machine_state()
        if state["gpu_jobs"] or not state["enough_ram"]:
            receipt["machine_before_render"] = state
            return finish("BLOCKED", "machine became busy before the render: " + json.dumps(state["gpu_jobs"]))
        receipt["machine_before_render"] = state
        o = be.preset(width=a.width, height=a.height, duration_s=a.duration, steps=a.steps)
        oid, jid = o["order_id"], o["items"][0]["job_ids"][0]
        t0 = time.monotonic()
        try:
            resp = be.render(oid, max_s=a.max_render_min * 60)
        except httpx.ReadTimeout:
            wall = round(time.monotonic() - t0, 2)
            dg = [j for j in JobStore(be.data).list(CLIENT) if j.get("stage")]
            receipt["render_call"] = {"timed_out_after_s": wall, "direct_gen_jobs": [
                {k: j.get(k) for k in ("job_id", "status", "stage", "progress", "timeline")} for j in dg]}
            be.stop()                  # our backend owns its sd-cli (job object): stopping it ends the render
            receipt["machine_after_stop"] = machine_state()
            return finish("BLOCKED", f"the render did not finish within --max-render-min {a.max_render_min}; "
                                     "stopped (no result, nothing faked)")
        wall = round(time.monotonic() - t0, 2)
        receipt["machine_after_render"] = machine_state()
        body = resp.json()
        receipt["render_call"] = {"http_status": resp.status_code, "wall_s_measured_by_tool": wall,
                                  "order_status": body.get("status"), "error": body.get("error"),
                                  "error_detail": body.get("error_detail")}
        store = JobStore(be.data)
        job = store.load(CLIENT, jid)
        if resp.status_code != 200 or job.get("status") != "completed":
            receipt["job"] = {k: job.get(k) for k in ("status", "error", "engine_error_detail", "verification")}
            return finish("BLOCKED", f"real render did not complete: http {resp.status_code}, order "
                                     f"{body.get('status')}, job {job.get('status')}: "
                                     f"{job.get('engine_error_detail') or job.get('error')}")
        res = job["result"]
        final = store.job_dir(CLIENT, jid) / "result" / res["file"]
        ej = job["provenance"]["engine_job"]
        raw = store.job_dir(CLIENT, ej["direct_gen_job_id"]) / "result" / ej["file"]
        dg_job = store.load(CLIENT, ej["direct_gen_job_id"])
        receipt["direct_gen_job"] = {
            "job_id": ej["direct_gen_job_id"], "model": dg_job.get("model"), "status": dg_job.get("status"),
            "params": dg_job.get("params"), "command": (dg_job.get("provenance") or {}).get("command"),
            "engine_seconds": (dg_job.get("provenance") or {}).get("engine_seconds"),
            "peak_rss_mb": (dg_job.get("provenance") or {}).get("peak_rss_mb"),
            "licence": (dg_job.get("provenance") or {}).get("license"),
            "weights": (dg_job.get("provenance") or {}).get("weights"),
            "started_at": dg_job.get("started_at"), "finished_at": dg_job.get("finished_at"),
            "timeline": dg_job.get("timeline")}
        order = be.order(oid)
        receipt["studio_job"] = {"job_id": jid, "status": job["status"], "engine": job.get("engine"),
                                 "timing": job.get("timing"), "result": {k: v for k, v in res.items()
                                                                         if k != "verification"},
                                 "verification_by_render_preset": res.get("verification"),
                                 "watermark": (job.get("provenance") or {}).get("watermark"),
                                 "watermark_check": ej.get("watermark_check")}
        receipt["order_after_render"] = {"order_id": oid, "status": order.get("status"), "qc": order.get("qc"),
                                         "qc_metrics": {k: v for k, v in (order.get("qc_metrics") or {}).items()
                                                        if k != "measurement"},
                                         "receipt": order.get("receipt"), "licence_warnings": order.get("licence_warnings")}
        # ---- D. independent checks ----
        indep = {}
        for label, path in (("delivered_mp4", final), ("direct_gen_raw", raw)):
            m = measure_video(path, FFMPEG, FFPROBE)
            crit = VLMArtCritic().inspect_and_certify(project_id=oid, media_input=m["keyframes"], media_path=path)
            indep[label] = {"file": path.name, "bytes": path.stat().st_size, "sha256": sha256_file(path),
                            "magic_hex": path.read_bytes()[:12].hex(), "ffprobe": ffprobe_json(path),
                            "measurement": m, "art_critic": critic_view(crit)}
        receipt["independent"] = indep
        shutil.copyfile(final, out / "studio_render.mp4")
        cs = order.get("contact_sheet_path")
        if cs and Path(cs).is_file():
            shutil.copyfile(cs, out / "contact_sheet.png")
        # ---- E. file-level negatives ----
        negf: dict = {}
        png = work / "frame.png"
        subprocess.run([FFMPEG, "-v", "error", "-y", "-i", str(final), "-frames:v", "1", str(png)], check=True)
        fake = work / "frame_renamed.mp4"
        shutil.copyfile(png, fake)
        negf["png_renamed_mp4"] = probe_video(fake, FFPROBE)
        one = work / "one_frame.mp4"
        subprocess.run([FFMPEG, "-v", "error", "-y", "-i", str(final), "-frames:v", "1", "-c:v", "libx264",
                        "-pix_fmt", "yuv420p", str(one)], check=True)
        negf["one_frame_mp4"] = probe_video(one, FFPROBE)
        kf = indep["delivered_mp4"]["measurement"]["keyframes"]
        legacy = [{**k, "brightness": 0.58, "contrast": 0.82, "laplacian_sharpness": 12.4, "non_black_ratio": 0.985,
                   "face_cosine_score": 0.845} for k in kf]
        negf["critic_legacy_constants"] = critic_view(VLMArtCritic().inspect_and_certify(
            project_id="neg", media_input=legacy, media_path=final))
        same = [{**kf[0], "position_pct": k["position_pct"], "frame_sha256": k["frame_sha256"]} for k in kf]
        negf["critic_identical_rows"] = critic_view(VLMArtCritic().inspect_and_certify(
            project_id="neg", media_input=same, media_path=final))
        negf["critic_other_file"] = critic_view(VLMArtCritic().inspect_and_certify(
            project_id="neg", media_input=kf, media_path=one))
        negf["critic_unbound"] = critic_view(VLMArtCritic().inspect_and_certify(project_id="neg", media_input=kf))
        receipt["negative_files"] = negf
    finally:
        be.stop()

    nb = receipt["negative_engine_missing"]
    na = receipt["negative_api"]
    nf = receipt["negative_files"]
    checks = {
        "engine_missing_503": nb["http_status"] == 503 and nb["code"] == "render_engine_missing"
                              and nb["order_status_after"] == "accepted",
        "default_preset_refused_422": na["default_preset_1080x1920_6s"]["http_status"] == 422,
        "face_refused_409": na["face_line"]["http_status"] == 409 and na["face_line"]["code"] == "face_render_refused",
        "queued_job_not_completed": na["queued_job_cannot_pass_qc"]["http_status"] == 409,
        "real_video_verified": receipt["studio_job"]["verification_by_render_preset"]["verified"] is True,
        "time_measured": (receipt["studio_job"]["timing"] or {}).get("measured") is True,
        "moving_measured": receipt["independent"]["delivered_mp4"]["measurement"]["moving"] is True,
        "png_renamed_refused": nf["png_renamed_mp4"]["reason"] == "not_a_video_container",
        "one_frame_refused": nf["one_frame_mp4"]["reason"] == "not_a_moving_video",
        "critic_constants_refused": nf["critic_legacy_constants"]["status"] == "REFUSED"
                                    and nf["critic_identical_rows"]["status"] == "REFUSED",
        "critic_other_file_refused": nf["critic_other_file"]["status"] == "REFUSED",
        "critic_unbound_not_pass": nf["critic_unbound"]["status"] != "PASS",
    }
    receipt["checks"] = checks
    receipt["commercial_qc"] = {"order_status": receipt["order_after_render"]["status"],
                                "passed_signals": receipt["order_after_render"]["qc_metrics"].get("passed_signals"),
                                "resolution_ok": receipt["order_after_render"]["qc_metrics"].get("resolution_ok")}
    return finish("PASS" if all(checks.values()) else "FAIL",
                  "" if all(checks.values()) else "failed: " + ", ".join(k for k, v in checks.items() if not v))


if __name__ == "__main__":
    sys.exit(main())
