"""Black-box long use of the RC 1.9 Jeff window (loopback participant web, ``bcc.jeff_desktop``).

Runs the Jeff shortcut path from a given checkout (``--checkout``, e.g. a worktree of C's
branch) on an isolated data dir. The participant model is the local stub
(tools/ux_soak/fake_model.py) so long use costs no model calls and the harness can see
what context Jeff sends to the model. Nothing under ``bcc/pit`` is modified here.

    python tools/ux_soak/jeff_web_blackbox.py --checkout <worktree> --data-dir <dir> --out <dir>
"""
from __future__ import annotations

import argparse
import json
import os
import secrets
import subprocess
import sys
import time
from pathlib import Path

import httpx
import psutil

sys.path.insert(0, str(Path(__file__).resolve().parent))
import fake_model  # noqa: E402

WRAPPER = r'''
import sys
from bcc import desktop, jeff_desktop
def launcher(browser, url, profile, **kw):
    extra = tuple(kw.pop("extra", ())) + ("--remote-debugging-port={cdp}", "--mute-audio",
             "--disable-background-timer-throttling", "--disable-renderer-backgrounding")
    return desktop.launch_window(browser, url, profile, extra=extra, **kw)
raise SystemExit(jeff_desktop.run(sys.argv[1:], launcher=launcher))
'''


class Jeff:
    def __init__(self, a):
        self.a = a
        self.out = Path(a.out)
        (self.out / "shots").mkdir(parents=True, exist_ok=True)
        self.data = Path(a.data_dir)
        self.data.mkdir(parents=True, exist_ok=True)
        self.base = f"http://127.0.0.1:{a.port}"
        self.proc = None
        self.findings: list[dict] = []
        self.steps: list[dict] = []
        self.console: list[str] = []
        self.t0 = time.monotonic()

    # -------------------------------------------------------------- plumbing
    def env(self) -> dict:
        c = Path(self.a.checkout)
        env = dict(os.environ)
        env["PYTHONPATH"] = os.pathsep.join([str(c / "command-center"), str(c / "bossman-core"), str(c)])
        env["BCC_DATA_DIR"] = str(self.data)
        env["PYTHONIOENCODING"] = "utf-8"
        return env

    def pit(self, *argv, stdin: str | None = None):
        r = subprocess.run([sys.executable, "-m", "bcc.pit.cli", *argv], input=stdin, capture_output=True,
                           text=True, encoding="utf-8", errors="replace", env=self.env(), timeout=120)
        self.step("pit " + argv[0], rc=r.returncode, out=(r.stdout + r.stderr)[-300:])
        return r

    def start(self):
        wrapper = self.data / "jeff_wrapper.py"
        wrapper.write_text(WRAPPER.format(cdp=self.a.cdp_port), encoding="utf-8")
        log = open(self.data / f"jeff-launch-{int(time.time())}.log", "wb")
        self.proc = subprocess.Popen([sys.executable, str(wrapper), "--data-dir", str(self.data), "--port", str(self.a.port),
                                      "--profile", str(self.a.profile)], env=self.env(), stdin=subprocess.PIPE,
                                     stdout=log, stderr=subprocess.STDOUT,
                                     creationflags=subprocess.BELOW_NORMAL_PRIORITY_CLASS | subprocess.CREATE_NO_WINDOW)
        t = time.monotonic()
        while time.monotonic() - t < 90:
            try:
                if httpx.get(self.base + "/api/jeff/identity", timeout=2).status_code == 200:
                    return time.monotonic() - t
            except httpx.HTTPError:
                pass
            if self.proc.poll() is not None:
                raise RuntimeError(f"jeff launcher exited rc={self.proc.returncode}")
            time.sleep(0.5)
        raise RuntimeError("jeff not ready")

    def kill(self):
        if self.proc is not None:
            try:
                psutil.Process(self.proc.pid).kill()
            except psutil.Error:
                pass
            self.proc.wait(10)
            self.proc = None

    def window_pids(self):
        return [p.pid for p in psutil.process_iter(["cmdline"])
                if str(self.a.profile) in " ".join(p.info.get("cmdline") or [])]

    def close_all(self):
        self.kill()
        for pid in self.window_pids():
            try:
                psutil.Process(pid).kill()
            except psutil.Error:
                pass

    def connect(self):
        for _ in range(60):
            try:
                self.br = self.pw.chromium.connect_over_cdp(f"http://127.0.0.1:{self.a.cdp_port}")
                pages = [p for c in self.br.contexts for p in c.pages if "jeff.html" in p.url]
                if pages:
                    self.page = pages[0]
                    self.page.on("console", lambda m: self.console.append(m.text) if m.type == "error" else None)
                    self.page.on("pageerror", lambda e: self.console.append(f"pageerror: {e}"))
                    return pages
            except Exception:  # noqa: BLE001
                pass
            time.sleep(1)
        raise RuntimeError("no Jeff window over CDP")

    def step(self, name, **kw):
        rec = {"t": round(time.monotonic() - self.t0, 1), "step": name, **kw}
        self.steps.append(rec)
        print("step", json.dumps(rec, ensure_ascii=False)[:300], flush=True)

    def finding(self, sev, what, repro, evidence=""):
        shot = f"shots/w{len(self.findings):02d}.png"
        try:
            self.page.screenshot(path=str(self.out / shot))
        except Exception:  # noqa: BLE001
            shot = ""
        self.findings.append({"severity": sev, "what": what, "repro": repro, "evidence": str(evidence)[:600], "shot": shot})
        print(f"[{sev}] {what} :: {str(evidence)[:200]}", flush=True)

    def bubbles(self):
        return self.page.evaluate("() => [...document.querySelectorAll('#chat .bubble')].map(b => ({cls: b.className, text: b.innerText}))")

    def status(self):
        return self.page.evaluate("() => (document.getElementById('connection-status') || {}).innerText || ''")

    def say(self, text, wait=60):
        n = len(self.bubbles())
        box = self.page.locator("#message")
        box.fill(text)
        box.press("Enter")
        t = time.monotonic()
        while time.monotonic() - t < wait:
            b = self.bubbles()
            if len(b) >= n + 2 and "pending" not in b[-1]["cls"]:
                return b[-1]["text"], time.monotonic() - t
            time.sleep(0.3)
        b = self.bubbles()
        return (b[-1]["text"] if b else ""), None

    # ------------------------------------------------------------------ run
    def run(self):
        from playwright.sync_api import sync_playwright
        a = self.a
        stub = fake_model.start(a.stub_port)
        password = secrets.token_urlsafe(18)          # throwaway, never printed
        self.pit("web-setup", "--data-dir", str(self.data), "--local-model", "soak-fast",
                 "--local-url", f"http://127.0.0.1:{a.stub_port}/v1", "--no-cloud")
        self.pit("web-user", "add", "tester", "--data-dir", str(self.data), "--password-stdin", stdin=password + "\n")
        with sync_playwright() as pw:
            self.pw = pw
            try:
                self.step("launch", ready_s=round(self.start(), 1))
                self.connect()
                p = self.page
                p.wait_for_selector("#login:not([hidden]), #app:not([hidden])", timeout=20000)
                p.fill("#login-username", "tester")
                p.fill("#login-password", password)
                p.click("#login-submit")
                p.wait_for_selector("#app:not([hidden])", timeout=30000)
                p.wait_for_timeout(1500)
                self.step("logged-in", status=self.status(), build=p.evaluate("() => (document.getElementById('build')||{}).innerText"),
                          bubbles=len(self.bubbles()))
                # context retention: does the second turn carry the first to the model?
                r1, s1 = self.say("Меня зовут Алиса. Запомни это.")
                r2, s2 = self.say("Как меня зовут?")
                sent = json.dumps(stub.last_messages, ensure_ascii=False)
                self.step("context", reply1=r1[:120], reply2=r2[:120], secs=[s1, s2], model_saw_name="Алиса" in sent)
                if "Алиса" not in sent:
                    self.finding("medium", "Jeff does not send the previous turn to the model (context not retained)",
                                 "Jeff window → 'Меня зовут Алиса. Запомни это.' → 'Как меня зовут?'", sent[:400])
                if s2 is None:
                    self.finding("high", "Jeff reply never arrived (60 s)", "send a message", r2)
                # long use
                lat = []
                for i in range(a.turns):
                    _r, s = self.say(f"сообщение номер {i}", wait=45)
                    lat.append(s)
                ok = [x for x in lat if x is not None]
                self.step("long-use", turns=a.turns, answered=len(ok),
                          p50=sorted(ok)[len(ok) // 2] if ok else None, max=max(ok) if ok else None,
                          bubbles=len(self.bubbles()))
                if len(ok) < a.turns:
                    self.finding("high", f"{a.turns - len(ok)} of {a.turns} Jeff turns got no reply", "long chat", lat)
                before = [b["text"] for b in self.bubbles()]
                # reload keeps the server-side history
                p.reload(wait_until="domcontentloaded")
                p.wait_for_selector("#app:not([hidden])", timeout=30000)
                p.wait_for_timeout(2000)
                after = [b["text"] for b in self.bubbles()]
                self.step("reload", before=len(before), after=len(after))
                if len(after) < min(len(before), 10):
                    self.finding("medium", "Jeff history shrinks after a window reload", "chat → F5", f"{len(before)}→{len(after)}")
                # model down → readable error; model back → recovers
                stub.shutdown()
                stub.server_close()
                r, s = self.say("модель сейчас выключена?", wait=150)
                self.step("model-down", reply=r[:200], secs=s)
                if s is None:
                    self.finding("high", "With the model down a Jeff message hangs without an answer (150 s)",
                                 "stop the local model → send a message", r)
                elif any(w in r.lower() for w in ("traceback", "exception", "error:", "connecterror")):
                    self.finding("medium", "Jeff shows a raw technical error when the model is down",
                                 "stop the local model → send a message", r)
                stub = fake_model.start(a.stub_port)
                r, s = self.say("а теперь?", wait=60)
                self.step("model-back", reply=r[:120], secs=s)
                if s is None or "soak-ok" not in r:
                    self.finding("high", "Jeff does not recover after the model is back", "model down → up → send", r)
                # Jeff server dies while the window is open; the shortcut is started again
                self.kill()
                p.wait_for_timeout(6000)
                down = self.status()
                self.step("server-killed", status=down)
                self.step("relaunch", ready_s=round(self.start(), 1))
                p.wait_for_timeout(8000)
                pages = [pg for c in self.br.contexts for pg in c.pages if "jeff.html" in pg.url]
                self.step("after-relaunch", windows=len(pages), status=self.status(), bubbles=len(self.bubbles()))
                if len(pages) > 1:
                    self.finding("medium", f"{len(pages)} Jeff windows after the shortcut is started again", "kill Jeff server → start shortcut")
                r, s = self.say("после перезапуска ты тут?", wait=60)
                self.step("after-restart-turn", reply=r[:120], secs=s)
                if s is None:
                    self.finding("high", "Jeff window does not work after the server restart (no reply)",
                                 "Jeff open → server restarted by shortcut → send", r)
                hist = httpx.Client(base_url=self.base)
                # voice: TTS + ASR fallbacks (no Piper/ASR model configured in this data dir)
                p.locator("#voice-preview").click() if p.locator("#voice-preview").count() else None
                p.wait_for_timeout(3000)
                vs = p.evaluate("() => ({ status: (document.getElementById('voice-status')||{}).innerText, "
                                "note: (document.getElementById('voice-note')||{}).innerText })")
                self.step("voice-preview", **vs)
                p.locator("#mic").click()
                p.wait_for_timeout(5000)
                vs2 = p.evaluate("() => ({ status: (document.getElementById('voice-status')||{}).innerText, "
                                 "note: (document.getElementById('voice-note')||{}).innerText, "
                                 "banner: (document.getElementById('error-banner')||{}).innerText })")
                self.step("mic", **vs2)
                p.locator("#mic").click()
                p.wait_for_timeout(1500)
                # authority: the Jeff server exposes no owner endpoints
                probes = {u: hist.get(u).status_code for u in ("/api/tasks", "/api/approvals", "/api/computer/status")}
                self.step("owner-endpoints", **probes)
                if any(v == 200 for v in probes.values()):
                    self.finding("high", "Jeff web server answers an owner endpoint", "GET " + ", ".join(probes), probes)
            finally:
                try:
                    self.br.close()
                except Exception:  # noqa: BLE001
                    pass
                self.close_all()
                stub.shutdown()
        for name, obj in (("jeffweb_findings.json", self.findings), ("jeffweb_steps.json", self.steps),
                          ("jeffweb_console.json", self.console)):
            (self.out / name).write_text(json.dumps(obj, ensure_ascii=False, indent=1), encoding="utf-8")
        print(json.dumps({"findings": len(self.findings), "console_errors": len(self.console)}))
        return 0


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--checkout", required=True)
    ap.add_argument("--data-dir", required=True)
    ap.add_argument("--out", required=True)
    ap.add_argument("--port", type=int, default=8873)
    ap.add_argument("--cdp-port", type=int, default=8876)
    ap.add_argument("--stub-port", type=int, default=8875)
    ap.add_argument("--profile", default=r"C:\Users\asd\Bossman\rc19-ux-profile\jeff-web-window")
    ap.add_argument("--turns", type=int, default=30)
    return Jeff(ap.parse_args()).run()


if __name__ == "__main__":
    sys.exit(main())
