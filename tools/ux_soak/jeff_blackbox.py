"""Black-box long use of the Jeff participant window (RC 1.9, workstream F).

Starts Jeff exactly like its shortcut would (``bcc.jeff_desktop``: shared backend +
``--app`` window of the system browser, own throwaway profile) on an isolated data dir and
drives THAT window over CDP. Nothing under ``bcc/pit`` is modified; defects go to the lead.

    python tools/ux_soak/jeff_blackbox.py --port 8871 --cdp-port 8876 --data-dir <dir> \
        --out <evidence dir> [--jeff-module <path to jeff_desktop.py>] [--ui-dir <ui with jeff.*>]
"""
from __future__ import annotations

import argparse
import importlib.util
import json
import os
import subprocess
import sys
import time
from pathlib import Path

import httpx

sys.path.insert(0, str(Path(__file__).resolve().parent))
from backend import REPO, DesktopBackend  # noqa: E402

WRAPPER = r'''
import importlib.util, sys
sys.argv = ["bossman-jeff"] + sys.argv[1:]
from bcc import desktop
if {module!r}:
    spec = importlib.util.spec_from_file_location("bcc.jeff_desktop", {module!r})
    mod = importlib.util.module_from_spec(spec); sys.modules["bcc.jeff_desktop"] = mod; spec.loader.exec_module(mod)
else:
    import bcc.jeff_desktop as mod
def launcher(browser, url, profile, **kw):
    extra = tuple(kw.pop("extra", ())) + ("--remote-debugging-port={cdp}",
             "--disable-background-timer-throttling", "--disable-renderer-backgrounding", "--mute-audio")
    return desktop.launch_window(browser, url, profile, extra=extra, **kw)
raise SystemExit(mod.run(sys.argv[1:], launcher=launcher))
'''


class JeffLauncher(DesktopBackend):
    def __init__(self, *a, module: str = "", **kw):
        super().__init__(*a, **kw)
        self.module = module

    def start(self, timeout: float = 120.0) -> float:
        self.data_dir.mkdir(parents=True, exist_ok=True)
        self.log_dir.mkdir(parents=True, exist_ok=True)
        self.starts += 1
        wrapper = self.data_dir / "jeff_launch_wrapper.py"
        wrapper.write_text(WRAPPER.format(module=self.module, cdp=self.cdp_port), encoding="utf-8")
        log = open(self.log_dir / f"jeff-{self.starts:02d}.log", "wb")
        t0 = time.monotonic()
        self.proc = subprocess.Popen([sys.executable, str(wrapper), "--port", str(self.port), "--profile", str(self.profile)],
                                     cwd=str(self.data_dir), env=self.env(), stdin=subprocess.PIPE, stdout=log,
                                     stderr=subprocess.STDOUT,
                                     creationflags=subprocess.BELOW_NORMAL_PRIORITY_CLASS | subprocess.CREATE_NO_WINDOW)
        while time.monotonic() - t0 < timeout:
            try:
                if self._port_up() and httpx.get(f"http://127.0.0.1:{self.cdp_port}/json/version", timeout=2).status_code == 200:
                    return time.monotonic() - t0
            except httpx.HTTPError:
                pass
            if self.proc.poll() is not None:
                raise RuntimeError(f"jeff launcher exited rc={self.proc.returncode}")
            time.sleep(0.4)
        raise RuntimeError("jeff launcher not ready")


class Run:
    def __init__(self, args):
        self.args = args
        self.out = Path(args.out)
        (self.out / "shots").mkdir(parents=True, exist_ok=True)
        extra = {"BCC_UI_DIR": args.ui_dir} if args.ui_dir else {}
        self.b = JeffLauncher(args.port, Path(args.data_dir), Path(args.data_dir) / "soak-logs",
                              profile=Path(args.profile), cdp_port=args.cdp_port, module=args.jeff_module,
                              extra_env=extra)
        self.findings: list[dict] = []
        self.console: list[str] = []
        self.steps: list[dict] = []

    def finding(self, sev, what, repro, evidence=""):
        name = f"shots/j{len(self.findings):02d}.png"
        try:
            self.page.screenshot(path=str(self.out / name))
        except Exception:  # noqa: BLE001
            name = ""
        self.findings.append({"severity": sev, "what": what, "repro": repro, "evidence": str(evidence)[:600], "shot": name})
        print(f"[{sev}] {what} :: {str(evidence)[:160]}", flush=True)

    def step(self, name, **kw):
        self.steps.append({"t": round(time.monotonic() - self.t0, 1), "step": name, **kw})
        print("step", name, kw, flush=True)

    def connect(self):
        for _ in range(60):
            try:
                self.br = self.pw.chromium.connect_over_cdp(f"http://127.0.0.1:{self.args.cdp_port}")
                pages = [p for c in self.br.contexts for p in c.pages if "/jeff.html" in p.url or p.url.startswith(self.b.base)]
                if pages:
                    self.page = pages[0]
                    self.page.on("console", lambda m: self.console.append(f"{m.type}: {m.text}") if m.type == "error" else None)
                    self.page.on("pageerror", lambda e: self.console.append(f"pageerror: {e}"))
                    self.page.wait_for_load_state("domcontentloaded")
                    return
            except Exception:  # noqa: BLE001
                pass
            time.sleep(1)
        raise RuntimeError("Jeff window not reachable")

    def status(self) -> str:
        return self.page.evaluate("() => (document.querySelector('#connection-status') || {}).innerText || ''")

    def bubbles(self) -> list[str]:
        return self.page.evaluate("() => [...document.querySelectorAll('#chat .bubble')].map(b => b.innerText)")

    def send(self, text: str):
        box = self.page.locator("#message")
        box.click()
        box.fill(text)
        self.page.locator("#composer button.send").click()

    def run(self) -> int:
        from playwright.sync_api import sync_playwright
        self.t0 = time.monotonic()
        a = self.args
        with sync_playwright() as pw:
            self.pw = pw
            self.b.start()
            self.connect()
            p = self.page
            self.step("opened", url=p.url)
            # 1. first start: token form (owner types the token, as the owner does)
            p.wait_for_function("() => !document.getElementById('login').hidden || !document.getElementById('app').hidden", timeout=20000)
            if p.locator("#login").is_visible():
                p.fill("#login-token", self.b.token)
                p.locator("#login-form button[type=submit]").click()
            p.wait_for_selector("#app:not([hidden])", timeout=20000)
            p.wait_for_function("() => document.querySelector('#connection-status').innerText.includes('На связи')", timeout=20000)
            self.step("logged-in", status=self.status(), build=p.evaluate("() => document.getElementById('build').innerText"))
            # 2. conversation + context retention
            p.evaluate("() => { const b = document.getElementById('clear-preview'); b && b.click(); }")
            # chat convention: Enter sends
            p.locator("#message").fill("привет")
            p.locator("#message").press("Enter")
            p.wait_for_timeout(500)
            if not self.bubbles()[1:]:
                self.finding("low", "Enter in the Jeff composer does not send (only the ➜ button does)",
                             "Jeff window → type a message → press Enter", p.evaluate("() => document.getElementById('message').value"))
                p.locator("#message").fill("")
            self.send("Меня зовут Алиса. Запомни это.")
            self.send("Как меня зовут?")
            b = self.bubbles()
            self.step("conversation", bubbles=len(b), last=b[-1][:160] if b else "")
            if b and "Алиса" not in b[-1]:
                self.finding("medium", "Jeff window chat is a canned UX preview: no reply uses the conversation (context "
                             "is not retained; every answer is the same fixed text)",
                             "Open Jeff window → type 'Меня зовут Алиса. Запомни это.' → send → type 'Как меня зовут?' → send",
                             b[-1][:300])
            # 3. HTML is shown as text
            self.send('<img src=x onerror="window.__jx=1">')
            if p.evaluate("() => window.__jx === 1"):
                self.finding("high", "HTML in a Jeff message executes", "send <img src=x onerror=...>")
            # 4. long use: 40 more messages, then history size
            for i in range(40):
                self.send(f"сообщение {i}")
            n_user = p.evaluate("() => document.querySelectorAll('#chat .bubble.user').length")
            self.step("long-use", user_bubbles=n_user)
            if n_user < 43:
                self.finding("low", f"Jeff window keeps only the last 30 messages silently (user bubbles visible: {n_user} of 43 sent)",
                             "send 43 messages → scroll up: the first ones are gone without notice",
                             f"visible user bubbles={n_user}")
            before = self.bubbles()
            # 5. reload: history survives (browser profile)
            p.reload(wait_until="domcontentloaded")
            p.wait_for_selector("#app:not([hidden])", timeout=20000)
            after = self.bubbles()
            self.step("reload", same=after == before)
            if after != before:
                self.finding("medium", "Jeff history changed across window reload", "send messages → F5", f"{len(before)}→{len(after)}")
            # 6. backend dies while the window is open
            self.b.kill()
            t = time.monotonic()
            seen = ""
            while time.monotonic() - t < 40:
                seen = self.status()
                if "На связи" not in seen:
                    break
                time.sleep(0.5)
            self.step("backend-killed", status=seen, secs=round(time.monotonic() - t, 1))
            if "На связи" in seen:
                self.finding("medium", "Jeff window keeps saying 'На связи' after the backend died",
                             "open Jeff → stop Bossman (close its console) → watch the status pill", seen)
            # the composer while offline
            self.send("пишу, пока нет связи")
            off = self.bubbles()
            self.step("offline-send", last=off[-1][:120] if off else "")
            # voice controls while offline / TTS availability
            voice = p.evaluate("""() => ({ tts: 'speechSynthesis' in window,
                voices: 'speechSynthesis' in window ? speechSynthesis.getVoices().length : -1,
                asr: !!(window.SpeechRecognition || window.webkitSpeechRecognition),
                previewDisabled: document.getElementById('voice-preview').disabled,
                select: document.getElementById('voice-select').innerText.slice(0, 120) })""")
            self.step("voice-state", **voice)
            note_before = p.evaluate("() => document.getElementById('voice-note').innerText")
            p.locator("#mic").click()
            p.wait_for_timeout(4000)
            note_after = p.evaluate("() => document.getElementById('voice-note').innerText")
            self.step("mic", before=note_before, after=note_after)
            if note_after.strip() == "Слушаю…":
                self.finding("medium", "Mic stays at 'Слушаю…' with no result/error (no microphone permission or ASR service)",
                             "Jeff window → click 🎙 on a machine/profile without microphone permission", note_after)
            p.locator("#mic").click()
            p.locator("#mic").click()
            p.wait_for_timeout(1500)
            p.locator("#voice-preview").click()
            p.locator("#speak-last").click()
            p.wait_for_timeout(1000)
            speaking = p.evaluate("() => 'speechSynthesis' in window ? speechSynthesis.speaking || speechSynthesis.pending : null")
            self.step("tts", speaking=speaking, voices=voice["voices"])
            if voice["tts"] and voice["voices"] == 0:
                self.finding("low", "'Прослушать голос' / 'Озвучить' do nothing and say nothing when the system has no TTS voices",
                             "profile/OS without speech voices → Jeff → Личность → Прослушать голос", json.dumps(voice, ensure_ascii=False))
            # 7. backend back
            time.sleep(3)
            self.b.start()                      # the owner starts Bossman/Jeff again
            t = time.monotonic()
            while time.monotonic() - t < 60 and "На связи" not in self.status():
                time.sleep(0.5)
            self.step("backend-back", status=self.status(), secs=round(time.monotonic() - t, 1))
            if "На связи" not in self.status():
                self.finding("high", "Jeff window does not recover after Bossman restarts",
                             "open Jeff → restart Bossman → wait 60 s", self.status())
            pages = [pg for c in self.br.contexts for pg in c.pages if "jeff.html" in pg.url]
            self.step("windows-after-relaunch", jeff_windows=len(pages))
            if len(pages) > 1:
                self.finding("medium", f"starting Jeff again while its window is open leaves {len(pages)} Jeff windows",
                             "Jeff open → Bossman died → start Jeff shortcut again", "")
                for pg in pages:
                    if pg is not p:
                        pg.close()
            if self.bubbles() != off:
                self.finding("medium", "Jeff history differs after Bossman restart", "restart Bossman while Jeff is open", "")
            # 8. what the 'Command Center' button gives a participant window
            p.locator("#open-command-center").click()
            p.wait_for_timeout(4000)
            owner_ui = p.evaluate("() => !!document.getElementById('shell') && !document.getElementById('shell').hidden")
            self.step("command-center-button", url=p.url, owner_shell_visible=owner_ui)
            if owner_ui:
                self.finding("medium", "The participant-labelled Jeff window opens the full owner Command Center "
                             "without a new login (it runs on the owner's session)",
                             "Jeff window → top bar 'Command Center'", p.url)
            p.goto(self.b.base + "/jeff.html", wait_until="domcontentloaded")
            p.wait_for_selector("#app:not([hidden])", timeout=20000)
            # 9. 'Выйти' in Jeff, then reopen
            p.locator("#logout").click()
            p.wait_for_timeout(3000)
            state = p.evaluate("() => ({ login: !document.getElementById('login').hidden, app: !document.getElementById('app').hidden })")
            self.step("after-logout", **state)
            if not state["login"]:
                self.finding("medium", "'Выйти' in Jeff does not return to the token form", "Jeff → Выйти", json.dumps(state))
            self.close()
        (self.out / "jeff_findings.json").write_text(json.dumps(self.findings, ensure_ascii=False, indent=1), encoding="utf-8")
        (self.out / "jeff_steps.json").write_text(json.dumps(self.steps, ensure_ascii=False, indent=1), encoding="utf-8")
        (self.out / "jeff_console.json").write_text(json.dumps(self.console, ensure_ascii=False, indent=1), encoding="utf-8")
        print(json.dumps({"findings": len(self.findings), "console_errors": len(self.console)}, ensure_ascii=False))
        return 0

    def close(self):
        try:
            self.br.close()
        except Exception:  # noqa: BLE001
            pass
        self.b.kill()
        self.b.close_windows()


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--port", type=int, default=8871)
    ap.add_argument("--cdp-port", type=int, default=8876)
    ap.add_argument("--data-dir", required=True)
    ap.add_argument("--out", required=True)
    ap.add_argument("--profile", default=r"C:\Users\asd\Bossman\rc19-ux-profile\jeff-window")
    ap.add_argument("--jeff-module", default="", help="jeff_desktop.py from C's branch (if not in this checkout)")
    ap.add_argument("--ui-dir", default="", help="UI dir that contains jeff.html/js/css (if not in this checkout)")
    args = ap.parse_args()
    r = Run(args)
    try:
        return r.run()
    finally:
        r.close()


if __name__ == "__main__":
    sys.exit(main())
