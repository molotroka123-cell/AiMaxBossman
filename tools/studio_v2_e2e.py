#!/usr/bin/env python3
"""Motion Studio V2 acceptance run: real backend (own port, throwaway data dir) + headless Chromium.

One process, low CPU: one backend subprocess, one browser, one mask pass on one frame, two short Motion Studio renders.
It never touches the owner's :8801 and never sends a frame anywhere but 127.0.0.1 (every request is checked).

    python tools/studio_v2_e2e.py --evidence C:\\Users\\asd\\Bossman\\evidence\\studio-v2-20261011 \\
        --json docs/owner/runs/STUDIO_V2_E2E_20261011.json [--source-clip PATH] [--only boot,import_image,...]

Source frames/clips come from the owner's local materials (never committed): --source-clip, default the first
ref clip of C:\\Users\\asd\\Bossman\\video-testset-private. A synthetic ffmpeg clip is used (and flagged) when none exists.
"""
from __future__ import annotations

import argparse
import copy
import hashlib
import json
import os
import shutil
import socket
import subprocess
import sys
import tempfile
import time
import zipfile
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
CC = REPO / "command-center"
DEFAULT_CLIP = Path(r"C:\Users\asd\Bossman\video-testset-private\ref-clip-05.mp4")
STEPS = ["boot", "import_image", "import_video", "masks", "preview", "frame_step", "clean_export", "presets",
         "motion_render", "crash_recovery", "session_recovery"]


def free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


class Backend:
    """`python -m bcc` as a separate process so that a crash can be real (taskkill), not a thread stop."""

    def __init__(self, data_dir: Path, port: int) -> None:
        self.data_dir, self.port, self.proc = data_dir, port, None

    @property
    def url(self) -> str:
        return f"http://127.0.0.1:{self.port}/"

    @property
    def token(self) -> str:
        return (self.data_dir / "token").read_text(encoding="utf-8").strip()

    def start(self) -> "Backend":
        env = dict(os.environ, BCC_DATA_DIR=str(self.data_dir), PYTHONPATH=str(CC), PYTHONUTF8="1",
                   BCC_TOKEN_STDOUT="0")
        log = open(self.data_dir.parent / f"backend-{self.port}-{int(time.time())}.log", "wb")
        self.proc = subprocess.Popen([sys.executable, "-m", "bcc", "--host", "127.0.0.1", "--port", str(self.port)],
                                     cwd=str(CC), env=env, stdout=log, stderr=subprocess.STDOUT)
        import httpx
        deadline = time.time() + 60
        while time.time() < deadline:
            if self.proc.poll() is not None:
                raise RuntimeError(f"backend exited rc={self.proc.returncode}")
            try:
                with httpx.Client(trust_env=False, timeout=1.5) as c:
                    if c.get(self.url).status_code < 500:
                        return self
            except httpx.HTTPError:
                time.sleep(0.3)
        raise RuntimeError("backend did not start")

    def kill(self, tree: bool = False) -> None:
        """Hard kill. tree=False leaves the render child alive (a backend crash); tree=True kills it too."""
        if self.proc and self.proc.poll() is None:
            args = ["taskkill", "/F", "/PID", str(self.proc.pid)] + (["/T"] if tree else [])
            subprocess.run(args, capture_output=True)
            self.proc.wait(timeout=20)

    def api(self, path: str, method: str = "GET", **kw):
        import httpx
        with httpx.Client(trust_env=False, timeout=60) as c:
            r = c.request(method, self.url.rstrip("/") + path, headers={"X-BCC-Token": self.token}, **kw)
            return r


def pixel_diff_outside(before_png: bytes, after_png: bytes, mask_png: bytes | None) -> dict:
    """Changed pixels overall and outside the (soft) mask: a recolour must never leave its zone."""
    import io

    import numpy as np
    from PIL import Image
    a = np.asarray(Image.open(io.BytesIO(before_png)).convert("RGB")).astype(int)
    b = np.asarray(Image.open(io.BytesIO(after_png)).convert("RGB")).astype(int)
    changed = np.abs(a - b).sum(axis=2) > 0
    out = {"changed_px": int(changed.sum()), "total_px": int(changed.size)}
    if mask_png is not None:
        m = np.asarray(Image.open(io.BytesIO(mask_png)).convert("L"))
        out["mask_px"] = int((m > 0).sum())
        out["outside_mask_changed_px"] = int((changed & (m == 0)).sum())
    return out


class Run:
    def __init__(self, args) -> None:
        self.args = args
        self.results: dict[str, dict] = {}
        self.evidence = Path(args.evidence)
        self.evidence.mkdir(parents=True, exist_ok=True)
        self.tmp = Path(tempfile.mkdtemp(prefix="studio-v2-"))
        self.requests_external: list[str] = []
        self.page_errors: list[str] = []
        self.notes: dict = {}

    # ---------------------------------------------------------------- helpers
    def rec(self, name: str, ok: bool, **detail) -> None:
        self.results[name] = {"status": "PASS" if ok else "FAIL", **detail}
        print(f"[{'PASS' if ok else 'FAIL'}] {name}: {json.dumps(detail, ensure_ascii=False, default=str)[:400]}", flush=True)

    def shot(self, page, name: str) -> str:
        path = self.evidence / name
        page.screenshot(path=str(path), full_page=False)
        return str(path)

    def prepare_media(self) -> None:
        clip = Path(self.args.source_clip) if self.args.source_clip else DEFAULT_CLIP
        ffmpeg = shutil.which("ffmpeg")
        self.webm = self.tmp / "clip.webm"
        self.still = self.tmp / "still.png"
        self.synthetic = not clip.is_file()
        if self.synthetic:
            src = ["-f", "lavfi", "-i", "testsrc2=s=480x640:r=25:d=3"]
        else:
            src = ["-ss", "1.4", "-t", "2", "-i", str(clip)]
        # Chromium shipped with Playwright has no H.264: transcode a 2 s window to VP8, 1 thread (low CPU).
        subprocess.run([ffmpeg, "-y", "-loglevel", "error", *src, "-vf", "scale=-2:640,fps=25", "-an", "-c:v", "libvpx",
                        "-b:v", "1500k", "-threads", "1", str(self.webm)], check=True)
        subprocess.run([ffmpeg, "-y", "-loglevel", "error", "-i", str(self.webm), "-vf", "select=eq(n\\,10)",
                        "-vframes", "1", str(self.still)], check=True)
        self.fps = 25
        self.notes["source"] = {"synthetic": self.synthetic, "clip_sha256_16": hashlib.sha256(
            (clip if not self.synthetic else self.webm).read_bytes()).hexdigest()[:16],
            "webm_bytes": self.webm.stat().st_size}

    def canvas_stats(self, page) -> dict:
        return page.evaluate("""() => {
            const c = document.querySelectorAll('.gj-canvas')[0];
            const d = c.getContext('2d').getImageData(0, 0, c.width, c.height).data;
            let sum = 0, nz = 0;
            for (let i = 0; i < d.length; i += 4) { const v = (d[i] + d[i + 1] + d[i + 2]) / 3; sum += v; if (v > 8) nz += 1; }
            return { w: c.width, h: c.height, mean: sum / (d.length / 4), nonblack_share: nz / (d.length / 4) };
        }""")

    # ---------------------------------------------------------------- UI plumbing
    def login(self, page, be: Backend) -> None:
        page.goto(be.url, wait_until="domcontentloaded")
        page.wait_for_timeout(1500)
        if page.locator("#shell:not([hidden])").count() == 0:     # a stored session survives a backend restart
            page.fill("#login-token", be.token)
            page.click("#login-submit")
        page.wait_for_selector("#shell:not([hidden])", timeout=20000)

    def open_page(self, page, be: Backend, page_id: str) -> None:
        page.goto(f"{be.url}#/{page_id}", wait_until="domcontentloaded")
        page.wait_for_function(
            "!document.querySelector('#view .skeleton') && document.getElementById('view').childElementCount > 0",
            timeout=30000)
        page.wait_for_timeout(500)

    def watch(self, page) -> None:
        def on_request(req):
            url = req.url
            if not (url.startswith("http://127.0.0.1") or url.startswith("data:") or url.startswith("blob:")
                    or url.startswith("about:")):
                self.requests_external.append(url)
        page.on("request", on_request)
        page.on("pageerror", lambda e: self.page_errors.append(str(e)))

    # ---------------------------------------------------------------- steps
    def step_boot(self, page, be):
        self.login(page, be)
        self.open_page(page, be, "direct-gen")
        page.wait_for_selector("[data-testid=gj-panel]", timeout=20000)
        self.shot(page, "01-boot.png")
        info = be.api("/api/direct-gen/genjutsu").json()
        engines = {e["id"]: e["available"] for e in info["engines"]}
        self.rec("boot", not self.page_errors and not self.requests_external, port=be.port,
                 page_errors=self.page_errors[:5], external_requests=self.requests_external[:5], engines=engines,
                 default_engine=info.get("default_engine"))

    def step_import_image(self, page, be):
        consent = page.locator("#gj-consent")
        mask_btn = page.locator("[data-testid=gj-masks]")
        page.set_input_files("#gj-file", str(self.still))
        page.wait_for_selector("[data-testid=gj-stage]", timeout=20000)
        dims = page.evaluate("[document.querySelectorAll('.gj-canvas')[0].width, document.querySelectorAll('.gj-canvas')[0].height]")
        blocked = mask_btn.is_disabled()
        consent.check()
        page.wait_for_timeout(200)
        enabled = not mask_btn.is_disabled()
        consent.uncheck()
        self.shot(page, "02-import-image.png")
        self.rec("import_image", blocked and enabled and dims[0] > 0, canvas=dims, blocked_without_consent=blocked,
                 enabled_with_consent=enabled)

    def step_import_video(self, page, be):
        page.set_input_files("#gj-file", str(self.webm))
        page.wait_for_function("document.querySelector('video.gj-video') && document.querySelector('video.gj-video').videoWidth > 0",
                               timeout=30000)
        page.wait_for_selector("[data-testid=gj-step-row]:not([hidden])", timeout=10000)
        page.wait_for_function("document.querySelector('[data-testid=gj-frame-info]').textContent.includes('Кадр')", timeout=10000)
        info = page.locator("[data-testid=gj-frame-info]").inner_text()
        page.fill("#gj-fps", str(self.fps))        # the browser cannot read a clip's fps; the owner types it, the harness knows it
        page.wait_for_timeout(100)
        stats = self.canvas_stats(page)
        self.shot(page, "03-import-video.png")
        self.rec("import_video", "Кадр" in info and stats["mean"] > 3, frame_info=info, canvas=stats)

    def step_masks(self, page, be):
        page.locator("#gj-consent").check()
        t0 = time.time()
        page.locator("[data-testid=gj-masks]").click()
        page.wait_for_function(
            "document.querySelector('[data-testid=gj-mask-info]').textContent.includes('Зоны найдены') "
            "|| document.querySelector('[data-testid=gj-error]').childElementCount > 0", timeout=240000)
        err = page.locator("[data-testid=gj-error]").inner_text()
        info = page.locator("[data-testid=gj-mask-info]").inner_text()
        self.shot(page, "04-masks.png")
        self.rec("masks", "Зоны найдены" in info and not err, seconds=round(time.time() - t0, 1), info=info[:400], error=err[:300])

    def step_preview(self, page, be):
        if "Зоны найдены" not in page.locator("[data-testid=gj-mask-info]").inner_text():
            self.rec("preview", False, reason="masks not found: preview cannot be judged")
            return
        before = page.evaluate("document.querySelectorAll('.gj-canvas')[0].toDataURL('image/png')")
        page.locator("[data-preset=hair-chocolate]").click()
        page.wait_for_timeout(800)
        after = page.evaluate("document.querySelectorAll('.gj-canvas')[1].toDataURL('image/png')")
        import base64
        b = base64.b64decode(before.split(",", 1)[1])
        a = base64.b64decode(after.split(",", 1)[1])
        # Same frame, same engine, asked once more through the API: the hair mask decides what may change.
        res = be.api("/api/direct-gen/genjutsu/masks", "POST",
                     json={"image_b64": base64.b64encode(b).decode(), "engine": "segformer", "consent": True}).json()
        mask_png = base64.b64decode(res["regions"]["hair"]["png_b64"])
        d = pixel_diff_outside(b, a, mask_png)
        info = page.locator(".dg-small.dg-num").all_inner_texts()
        page.locator("#gj-split").fill("40")
        self.shot(page, "05-preview.png")
        self.rec("preview", d["changed_px"] > 0 and d["outside_mask_changed_px"] == 0, **d,
                 render_info=[t for t in info if "Перекраска" in t][:1])

    def step_frame_step(self, page, be):
        def cur():
            return page.evaluate("[document.querySelector('video.gj-video').currentTime, document.querySelector('#gj-frame-no').value]")
        t_a, n_a = cur()
        page.locator("[data-testid=gj-step-fwd]").click()
        page.wait_for_timeout(700)
        t_b, n_b = cur()
        masks_after_fwd = page.locator("[data-testid=gj-mask-info]").inner_text()
        page.locator("[data-testid=gj-step-back]").click()
        page.wait_for_timeout(700)
        t_c, n_c = cur()
        page.fill("#gj-frame-no", "20")
        page.locator("[data-testid=gj-goto]").click()
        page.wait_for_timeout(700)
        t_d, n_d = cur()
        fps = float(page.input_value("#gj-fps"))
        ok = (int(n_b) == int(n_a) + 1 and int(n_c) == int(n_a) and int(n_d) == 20
              and abs((t_b - t_c) - 1 / fps) < 0.25 / fps and "Зоны ещё не найдены" in masks_after_fwd)
        self.shot(page, "06-frame-step.png")
        self.rec("frame_step", ok, fps=fps, frames=[n_a, n_b, n_c, n_d], times=[round(t, 4) for t in (t_a, t_b, t_c, t_d)],
                 masks_reset_after_step="Зоны ещё не найдены" in masks_after_fwd)

    def step_clean_export(self, page, be):
        # Re-find masks on the current frame so the effect variant exists, then export clean + effect + manifest.
        page.locator("#gj-consent").check()
        page.locator("[data-testid=gj-masks]").click()
        page.wait_for_function(
            "document.querySelector('[data-testid=gj-mask-info]').textContent.includes('Зоны найдены') "
            "|| document.querySelector('[data-testid=gj-error]').childElementCount > 0", timeout=240000)
        page.locator("[data-preset=hair-auburn]").click()
        page.wait_for_timeout(800)
        with page.expect_download(timeout=30000) as dl:
            page.locator("[data-testid=gj-variants]").click()
        zpath = self.evidence / dl.value.suggested_filename
        dl.value.save_as(str(zpath))
        self.shot(page, "07-clean-export.png")
        with zipfile.ZipFile(zpath) as z:
            names = z.namelist()
            manifest = json.loads(z.read(next(n for n in names if n.endswith("manifest.json"))))
            hashes = {n: hashlib.sha256(z.read(n)).hexdigest() for n in names if n.endswith(".png")}
        roles = {f["role"]: f for f in manifest["files"]}
        ok = ("clean" in roles and "effect" in roles and roles["clean"]["sha256"] != roles["effect"]["sha256"]
              and hashes.get(roles["clean"]["name"]) == roles["clean"]["sha256"]
              and hashes.get(roles["effect"]["name"]) == roles["effect"]["sha256"] and manifest["local_only"])
        self.rec("clean_export", ok, zip=zpath.name, files=names, clean_sha=roles.get("clean", {}).get("sha256"),
                 effect_sha=roles.get("effect", {}).get("sha256"), frame_index=manifest["source"]["frame_index"])

    def step_presets(self, page, be):
        ids = page.evaluate("[...document.querySelectorAll('[data-preset]')].map(b => b.dataset.preset)")
        need = ["hair-jet-black", "hair-chocolate", "hair-auburn", "hair-copper", "hair-ash-blonde", "hair-platinum"]
        self.rec("presets", all(n in ids for n in need), present=[n for n in need if n in ids], total=len(ids))

    def step_motion_render(self, page, be):
        self.open_page(page, be, "motion-studio")
        page.wait_for_selector("[data-testid=motion-studio]", timeout=20000)
        page.wait_for_function("document.querySelector('[name=ms-spec]').value.length > 50", timeout=15000)
        spec = json.loads(page.input_value("[name=ms-spec]"))
        short = copy.deepcopy(spec)
        short["scenes"] = short["scenes"][:2]
        short["meta"]["duration"] = short["scenes"][-1]["end"]
        self.short_spec = short
        # preview
        page.get_by_role("button", name="Предпросмотр кадров").click()
        page.wait_for_selector("[data-testid=motion-job] img", timeout=180000)
        page.wait_for_function("/готово и проверено/i.test(document.querySelector('[data-testid=motion-job]').textContent)",
                               timeout=180000)
        self.shot(page, "10-motion-preview.png")
        previews = page.locator("[data-testid=motion-job] img").count()
        # full render of the shortened spec
        page.fill("[name=ms-spec]", json.dumps(short))
        t0 = time.time()
        page.get_by_role("button", name="Полный рендер").click()
        page.wait_for_function(
            "[...document.querySelectorAll('[data-testid=motion-job]')].some(j => /полный рендер/.test(j.textContent) && /готово и проверено/.test(j.textContent))",
            timeout=600000)
        secs = round(time.time() - t0, 1)
        self.shot(page, "10-motion-done.png")
        jobs = be.api("/api/motion-studio/jobs").json()["jobs"]
        full = next(j for j in jobs if j["mode"] == "full")
        check = be.api(f"/api/motion-studio/jobs/{full['id']}/check").json()
        man = be.api(f"/api/motion-studio/jobs/{full['id']}/file", params={"name": "manifest.json"})
        manifest = man.json() if man.status_code == 200 else {}
        by = {f["name"]: f for f in manifest.get("files", [])}
        ok = (previews >= 1 and check.get("verified") and "video.mp4" in by and by["video.mp4"]["sha256"] == check.get("sha256")
              and manifest.get("verified") is True)
        self.rec("motion_render", ok, previews=previews, render_seconds=secs, duration_s=check.get("duration_s"),
                 video=check.get("video"), audio=check.get("audio"), sha256=check.get("sha256"),
                 manifest_files=sorted(by), manifest_status=man.status_code)
        self.full_job = full["id"]

    def step_crash_recovery(self, page, be):
        # A: backend dies hard while the render child lives on -> after restart the finished render must be recovered.
        # B: whole tree dies -> after restart the job is «interrupted» (not stuck «running») and a new render can start.
        outcome: dict = {}
        for label, tree in (("A_orphan_survives", False), ("B_tree_killed", True)):
            self.open_page(page, be, "motion-studio")
            page.wait_for_selector("[data-testid=motion-studio]", timeout=20000)
            page.fill("[name=ms-spec]", json.dumps(self.short_spec))
            before_ids = {j["id"] for j in be.api("/api/motion-studio/jobs").json()["jobs"]}
            page.get_by_role("button", name="Полный рендер").click()
            deadline = time.time() + 60
            job = None
            while time.time() < deadline and job is None:
                time.sleep(0.5)
                for j in be.api("/api/motion-studio/jobs").json()["jobs"]:
                    if j["id"] not in before_ids and j["state"] == "running":
                        job = j
            if job is None:
                outcome[label] = {"error": "render did not reach running (finished too fast or failed to start)"}
                continue
            self.shot(page, f"12-{label}-running.png")
            be.kill(tree=tree)
            time.sleep(1.0)
            be = Backend(be.data_dir, be.port).start()
            self.backend = be
            self.login(page, be)
            self.open_page(page, be, "motion-studio")
            # wait: orphan may still render (A) -> poll the API until the job leaves «running»
            end = time.time() + 300
            state = "running"
            rec = {}
            while time.time() < end:
                rec = next((j for j in be.api("/api/motion-studio/jobs").json()["jobs"] if j["id"] == job["id"]), {})
                state = rec.get("state", "missing")
                if state != "running":
                    break
                time.sleep(2)
            page.reload()
            page.wait_for_timeout(2500)
            self.shot(page, f"12-{label}-after.png")
            card = page.locator(f"[data-job='{job['id']}']").inner_text() if page.locator(f"[data-job='{job['id']}']").count() else ""
            outcome[label] = {"job": job["id"], "state_after_restart": state, "recovered": rec.get("recovered"),
                              "error": rec.get("error"), "ui_card": card[:200]}
        a, b = outcome.get("A_orphan_survives", {}), outcome.get("B_tree_killed", {})
        ok_a = a.get("state_after_restart") in ("done",) and a.get("recovered") is True
        ok_b = b.get("state_after_restart") == "interrupted"
        # a new render must be accepted after recovery (no job stuck in «running» blocking the queue)
        again = be.api("/api/motion-studio/jobs", "POST", json={"mode": "preview", "spec": self.short_spec, "times": [1.0]})
        outcome["new_job_status"] = again.status_code
        self.rec("crash_recovery", ok_a and ok_b and again.status_code == 202, **outcome)

    def step_session_recovery(self, page, be):
        self.open_page(page, be, "direct-gen")
        page.wait_for_selector("[data-testid=gj-panel]", timeout=20000)
        page.locator("[data-preset=hair-copper]").click()
        page.wait_for_timeout(700)                 # persist is debounced
        page.reload()
        page.wait_for_selector("[data-testid=gj-panel]", timeout=20000)
        page.wait_for_timeout(800)
        restored = page.locator("[data-testid=gj-mask-info]").get_attribute("data-restored")
        text = page.locator("[data-testid=gj-mask-info]").inner_text()
        page.locator("#gj-tab-hair").click()
        color = page.input_value("#gj-color")
        self.shot(page, "13-session-recovery.png")
        self.rec("session_recovery", restored == "1" and color.lower() == "#c46e3d", restored=restored, hair_color=color, note=text[:200])

    # ---------------------------------------------------------------- driver
    def run(self) -> int:
        from playwright.sync_api import sync_playwright
        only = set(self.args.only.split(",")) if self.args.only else set(STEPS)
        self.prepare_media()
        data = self.tmp / "data"
        data.mkdir()
        be = Backend(data, free_port()).start()
        self.backend = be
        try:
            with sync_playwright() as pw:
                browser = pw.chromium.launch()
                ctx = browser.new_context(viewport={"width": 1400, "height": 1000}, accept_downloads=True)
                page = ctx.new_page()
                self.watch(page)
                for name in STEPS:
                    if name not in only:
                        continue
                    try:
                        getattr(self, f"step_{name}")(page, self.backend)
                    except Exception as exc:  # noqa: BLE001 — one failed row must not hide the others
                        try:
                            self.shot(page, f"ERR-{name}.png")
                        except Exception:  # noqa: BLE001
                            pass
                        self.rec(name, False, exception=f"{type(exc).__name__}: {str(exc)[:500]}")
                browser.close()
        finally:
            self.backend.kill(tree=True)
        # local-only over the WHOLE session
        self.results["local_only"] = {"status": "PASS" if not self.requests_external else "FAIL",
                                      "external_requests": self.requests_external[:10]}
        out = {"generated": time.strftime("%Y-%m-%dT%H:%M:%S%z"), "python": sys.version.split()[0], "notes": self.notes,
               "results": self.results}
        if self.args.json:
            Path(self.args.json).parent.mkdir(parents=True, exist_ok=True)
            Path(self.args.json).write_text(json.dumps(out, ensure_ascii=False, indent=1, default=str), encoding="utf-8")
        (self.evidence / "results.json").write_text(json.dumps(out, ensure_ascii=False, indent=1, default=str), encoding="utf-8")
        failed = [k for k, v in self.results.items() if v["status"] != "PASS"]
        print("FAILED:", failed or "none")
        shutil.rmtree(self.tmp, ignore_errors=True)
        return 1 if failed else 0


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--evidence", default=r"C:\Users\asd\Bossman\evidence\studio-v2-20261011")
    ap.add_argument("--json", default="")
    ap.add_argument("--source-clip", default="")
    ap.add_argument("--only", default="")
    return Run(ap.parse_args()).run()


if __name__ == "__main__":
    raise SystemExit(main())
