#!/usr/bin/env python3
"""Full UX sweep of the Bossman Command Center UI: every control on every page, desktop + phone.

Re-runnable:  python tools/tree_proof/ux_sweep.py [--pages home,chat] [--viewport desktop|phone|both] [--no-fix]

What it does (honest scope, see the report for the matrix):
  * starts a THROWAWAY backend from this checkout on --port (default 8893) with a temp data dir,
    temp LOCALAPPDATA/APPDATA/HOME and every *KEY*/*TOKEN*/*SECRET*/*PASSWORD* env var stripped
    (the owner's data dir is refused); it is killed at the end;
  * drives Microsoft Edge (no bundled Chromium needed) through Playwright, logs in with the throwaway token;
  * blocks every non-localhost request (page.route abort) and records it;
  * enumerates controls from the live DOM of the shell and of each page of ui/pages/index.js + ui/pages.js
    (button, [role=button|tab|menuitem|switch], a[href], submit/button inputs, summary, checkbox/radio,
    select, text inputs/textareas), probes each one from a fresh page load, and for controls that open
    something (dialog, drawer, menu) also probes the controls that appeared (depth 1);
  * controls whose label/handler says destructive or outward (delete, reset, send, publish, install,
    restart, start, run, download, ...) are 'gated': mutating requests are answered by a stub 200 and recorded
    as 'would send', native confirm() is dismissed, external links are not followed;
  * verdicts: OK (an effect was observed), DEAD (no effect on any channel), ERROR (pageerror, console.error,
    5xx, unexplained 4xx, request failure, stuck overlay, click impossible), BLOCKED_BY_DESIGN (confirm/approval
    gate or an intended 4xx refusal with visible feedback), DISABLED, plus gated=true on gated controls.
    Identical controls (same label+class) are sampled: the first --per-sig instances are probed.

--no-fix: do not run the regression tests of the ux-fix commits (FIX_TESTS) after the sweep.
The script never edits the product: fixes are separate, test-first commits.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import subprocess
import sys
import tempfile
import time
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import urlparse

REPO = Path(__file__).resolve().parents[2]
EVID = REPO / "docs" / "architecture" / "bossman-tree-20261005" / "evidence"
EDGE = r"C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe"
VIEWPORTS = {"desktop": (1366, 850), "phone": (390, 844)}
BIG_PAGE, CLASS_QUOTA = 80, 4   # pages with more controls than BIG_PAGE are sampled per kind+class
FIX_TESTS: list[str] = []          # filled by ux-fix commits: pytest node ids that must stay green

GATED = re.compile(
    r"удал|delet|remov|убрат|сброс|reset|wipe|стер(еть|ет)|очист|clear[- ]?all|останов|stop|"
    r"отправ|send|publish|опублик|\bpay|оплат|\bbuy|куп(ить|и)|install|установ|restart|перезапуск|"
    r"logout|log[- ]?out|выйти|выход|\bsign|подпис|\bcall|позвон|звон|\bpost\b|deploy|разверн|switch|"
    r"запуст|\bstart|\brun\b|скач|download|\bkill|revoke|отозв|disconnect|отключ|rollback|откат|merge|\bpush|"
    r"уничтож|\bpull\b|clone|подтверд|confirm", re.I)

ENUM_JS = r"""(scope) => {
  const SEL = "button,[role=button],[role=tab],[role=menuitem],[role=switch],a[href],input[type=submit],input[type=button],summary,"
    + "input[type=checkbox],input[type=radio],select,textarea,input:not([type=hidden]):not([type=file]):not([type=checkbox]):not([type=radio]):not([type=submit]):not([type=button])";
  const root = document.body;
  const view = document.querySelector('#view');
  const out = []; let idx = 0;
  document.querySelectorAll('[data-uxs]').forEach(e => e.removeAttribute('data-uxs'));
  for (const el of root.querySelectorAll(SEL)) {
    const inView = !!(view && view.contains(el));
    if (scope === 'view' && !inView) continue;
    if (scope === 'shell' && inView) continue;
    const r = el.getBoundingClientRect();
    if (r.width === 0 || r.height === 0) continue;
    const st = getComputedStyle(el);
    if (st.visibility === 'hidden' || st.display === 'none' || parseFloat(st.opacity) === 0) continue;
    // hidden by an ancestor that clips it entirely (collapsed drawers use transform/offscreen)
    if (r.right < 0 || r.bottom < 0 || r.left > innerWidth + 1) continue;
    let anc = el, hidden = false;
    while (anc && anc !== document.body) { if (anc.hasAttribute && anc.hasAttribute('inert')) {hidden = true;break;} anc = anc.parentElement; }
    if (hidden) continue;
    // inside a CLOSED <details> (not its own summary): a layout box exists but the user cannot see it;
    // it becomes reachable as a depth-1 child once the summary is probed (09.10: 847 of 858 tree controls)
    const shut = el.closest('details:not([open])');
    if (shut) { const sm = shut.querySelector(':scope > summary'); if (!(sm && sm.contains(el))) continue; }
    const tag = el.tagName.toLowerCase();
    const type = (el.getAttribute('type') || '').toLowerCase();
    const label = ((el.innerText || '').trim() || el.getAttribute('aria-label') || el.title || el.getAttribute('placeholder') ||
                   el.getAttribute('name') || el.value || '').toString().trim().replace(/\s+/g, ' ').slice(0, 80);
    const href = el.getAttribute('href') || '';
    let kind = 'button';
    if (tag === 'a') kind = 'link'; else if (tag === 'select') kind = 'select'; else if (tag === 'textarea') kind = 'text';
    else if (tag === 'input' && !['submit','button'].includes(type)) kind = ['checkbox','radio'].includes(type) ? 'toggle' : 'text';
    else if (tag === 'summary') kind = 'summary'; else if (el.getAttribute('role') === 'tab') kind = 'tab';
    el.setAttribute('data-uxs', String(idx));
    const attrs = [label, el.getAttribute('aria-label'), el.title, href, el.id, el.getAttribute('name'), el.getAttribute('data-action'),
                   el.getAttribute('data-cmd'), String(el.className && el.className.baseVal !== undefined ? el.className.baseVal : el.className)].filter(Boolean).join(' ');
    out.push({i: idx, tag, type, kind, label: label || '(без подписи)', cls: String(el.className && el.className.baseVal !== undefined ? '' : (el.className || '')).slice(0, 80),
      href, id: el.id || '', disabled: el.disabled === true || el.getAttribute('aria-disabled') === 'true' || el.readOnly === true,
      reason: (el.title || el.getAttribute('aria-label') || el.getAttribute('data-reason') || '').trim().slice(0, 100),
      attrs: attrs.slice(0, 400), inView,
      selected: ['aria-selected','aria-pressed','aria-current','aria-checked'].some(a => el.getAttribute(a) === 'true') ||
                /\b(active|on|selected|current)\b/.test(String(el.className && el.className.baseVal !== undefined ? '' : el.className || ''))});
    idx++;
  }
  return out;
}"""

STATE_JS = r"""() => {
  const b = document.body;
  const vals = [...document.querySelectorAll('input,textarea,select')].map(e => [e.value, e.checked, e.selectedIndex]);
  let ls = ''; try { ls = JSON.stringify(Object.entries(localStorage).sort()); } catch (e) {}
  const toasts = [...document.querySelectorAll('#toast-root .toast-msg, #toast-root .toast, [role=alert], [role=status]')].map(e => e.innerText.trim()).filter(Boolean);
  const dlg = [...document.querySelectorAll('dialog[open], .modal, [role=dialog], [aria-modal=true], #modal-root > *, .drawer, .sheet, .popover, [role=menu], [role=listbox]')]
    .filter(e => { const r = e.getBoundingClientRect(), s = getComputedStyle(e); return r.width > 0 && r.height > 0 && s.visibility !== 'hidden' && s.display !== 'none'; })
    .map(e => (e.className || e.tagName) + ':' + (e.innerText || '').trim().slice(0, 60));
  return {dom: b.outerHTML.length + ':' + b.innerText.length + ':' + JSON.stringify(vals).length,
          html: b.outerHTML, vals: JSON.stringify(vals), ls, toasts, dlg, url: location.href, active: document.activeElement ? document.activeElement.outerHTML.slice(0, 120) : '',
          scroll: Math.round(scrollY) + ',' + Math.round((document.querySelector('#view') || {}).scrollTop || 0)};
}"""

INIT_JS = """
window.__uxm = 0;
new MutationObserver(m => { window.__uxm += m.length; }).observe(document, {subtree: true, childList: true, attributes: true, characterData: true});
"""

# a submit button whose form is invalid: the click shows the browser's validation bubble (no DOM change)
INVALID_JS = r"""(e) => { const f = e.form; const submit = e.type === 'submit' || (e.tagName === 'BUTTON' && !e.getAttribute('type'));
  if (!f || !submit || f.noValidate) return [];
  return [...f.elements].filter(x => x.willValidate && !x.validity.valid).map(x => x.validationMessage || 'invalid'); }"""
INPUT_ONLY_TYPES = ("number", "range", "color", "date", "time")

TARGET_JS = r"""(i) => { const e = document.querySelector('[data-uxs="' + i + '"]'); if (!e) return null;
  const p = e.parentElement; return {self: e.outerHTML, parent: p ? p.innerHTML.length + ':' + p.innerText.length : '', conn: e.isConnected}; }"""


def h(s: str) -> str:
    return hashlib.sha1(s.encode("utf-8", "replace")).hexdigest()[:12]


def now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S+00:00")


# ------------------------------------------------------------------ backend
class Throwaway:
    """One throwaway backend on a temp data dir; refuses the owner's data root.

    target 'installed': <build>/runtime/python.exe -I -m bcc (what the owner runs);
    target 'source': this checkout's command-center via the current interpreter."""

    def __init__(self, port: int, root: Path, build: Path | None):
        self.root, self.port, self.build = root, port, build
        self.base = f"http://127.0.0.1:{port}"
        for d in ("data", "la", "ap", "home", "logs"):
            (root / d).mkdir(parents=True, exist_ok=True)
        self.data = root / "data"
        owner = Path(os.environ.get("LOCALAPPDATA", "")) / "Bossman" / "CommandCenter"
        if owner.exists() and (self.data.resolve() == owner.resolve() or owner.resolve() in self.data.resolve().parents):
            raise SystemExit("refusing the owner data root")
        self.proc: subprocess.Popen | None = None
        self.live = ""

    def env(self) -> dict:
        env = {k: v for k, v in os.environ.items()
               if not any(s in k.upper() for s in ("KEY", "TOKEN", "SECRET", "PASSWORD", "CREDENTIAL"))}
        env.update({"LOCALAPPDATA": str(self.root / "la"), "APPDATA": str(self.root / "ap"), "HOME": str(self.root / "home"),
                    "USERPROFILE": str(self.root / "home"), "BCC_DATA_DIR": str(self.data), "BOSSMAN_DATA_DIR": str(self.data),
                    "BCC_PORT": str(self.port), "PYTHONIOENCODING": "utf-8", "PYTHONUTF8": "1"})
        if self.build:
            env["BOSSMAN_HOME"] = str(self.build)
            env["PLAYWRIGHT_BROWSERS_PATH"] = str(self.build / "browser")
            env["PATH"] = os.pathsep.join([str(self.build / "runtime" / "Scripts"), str(self.build / "media"), env.get("PATH", "")])
        else:
            env["PYTHONPATH"] = os.pathsep.join([str(REPO / "command-center"), str(REPO / "bossman-core"), str(REPO)])
        return env

    def start(self, timeout: float = 100.0) -> None:
        import urllib.request
        argv = ([str(self.build / "runtime" / "python.exe"), "-I", "-m", "bcc"] if self.build
                else [sys.executable, "-m", "bcc"]) + ["--host", "127.0.0.1", "--port", str(self.port)]
        log = open(self.root / "logs" / "backend.log", "wb")
        flags = (subprocess.BELOW_NORMAL_PRIORITY_CLASS | subprocess.CREATE_NO_WINDOW) if sys.platform == "win32" else 0
        self.proc = subprocess.Popen(argv, cwd=str(self.data), env=self.env(), stdout=log, stderr=subprocess.STDOUT, creationflags=flags)
        t0 = time.monotonic()
        while time.monotonic() - t0 < timeout:
            if self.proc.poll() is not None:
                raise RuntimeError(f"backend exited rc={self.proc.returncode}: {(self.root / 'logs' / 'backend.log').read_text(errors='replace')[-600:]}")
            try:
                with urllib.request.urlopen(self.base + "/health/live", timeout=2) as r:
                    self.live = r.read().decode("utf-8", "replace")
                    if r.status < 500 and (self.data / "token").exists():
                        return
            except Exception:                               # noqa: BLE001
                pass
            time.sleep(0.5)
        raise RuntimeError("backend did not become ready")

    @property
    def token(self) -> str:
        return (self.data / "token").read_text(encoding="utf-8").strip()

    def stop(self) -> None:
        if not self.proc:
            return
        try:
            import psutil
            p = psutil.Process(self.proc.pid)
            for c in p.children(recursive=True):
                c.kill()
            p.kill()
        except Exception:                                   # noqa: BLE001
            self.proc.kill()


# ------------------------------------------------------------------ browser session
class Session:
    def __init__(self, pw, base: str, token: str, vp: str, out_shots: Path):
        self.base, self.token, self.vp, self.shots = base, token, vp, out_shots
        w, hgt = VIEWPORTS[vp]
        headed = os.environ.get("BOSSMAN_UX_SWEEP_HEADED") == "1"     # owner watches the sweep on the desktop
        self.browser = pw.chromium.launch(executable_path=EDGE, headless=not headed, slow_mo=150 if headed else 0)
        self.ctx = self.browser.new_context(viewport={"width": w, "height": hgt}, is_mobile=(vp == "phone"),
                                            has_touch=(vp == "phone"), device_scale_factor=1)
        self.ctx.add_init_script(INIT_JS)
        self.stub = False
        self.ev = self._fresh_events()
        self.ctx.route("**/*", self._route)
        self.blocked_external: dict[str, int] = {}
        self.crashes = 0
        self.uses = 0
        self._attach()

    def _attach(self) -> None:
        self.page = self.ctx.new_page()
        self.page.on("console", self._on_console)
        self.page.on("pageerror", lambda e: self.ev["pageerrors"].append(str(e)[:240]))
        self.page.on("dialog", self._on_dialog)
        self.page.on("request", self._on_request)
        self.page.on("response", self._on_response)
        self.page.on("requestfailed", self._on_failed)
        self.page.on("popup", lambda p: (self.ev["popups"].append(p.url), p.close()))
        self.page.on("download", lambda d: self.ev["downloads"].append(d.suggested_filename))
        # a native file chooser is a real effect, not a DOM change (chat 'Прикрепить файлы'); with a listener
        # Playwright intercepts it, so no OS dialog stays open
        self.page.on("filechooser", lambda fc: self.ev["filechoosers"].append("multiple" if fc.is_multiple() else "single"))
        self.page.set_default_timeout(8000)

    def fresh_page(self, crashed: bool = False) -> None:
        """Replace the tab (after a renderer crash, or periodically to bound renderer memory)."""
        if crashed:
            self.crashes += 1
        try:
            self.page.close()
        except Exception:                                   # noqa: BLE001
            pass
        self._attach()
        self.uses = 0

    @staticmethod
    def _fresh_events() -> dict:
        return {"console": [], "pageerrors": [], "native": [], "requests": [], "responses": [], "failed": [],
                "would_send": [], "popups": [], "downloads": [], "filechoosers": [], "pending": set()}

    def reset_events(self) -> None:
        self.ev = self._fresh_events()

    # --- network
    def _route(self, route, request):
        u = urlparse(request.url)
        if u.scheme in ("http", "https") and u.hostname not in ("127.0.0.1", "localhost"):
            self.blocked_external[u.hostname or "?"] = self.blocked_external.get(u.hostname or "?", 0) + 1
            return route.abort()
        if self.stub and request.method != "GET" and u.scheme in ("http", "https"):
            body = (request.post_data or "")[:160]
            self.ev["would_send"].append(f"{request.method} {u.path} {body}".strip())
            return route.fulfill(status=200, content_type="application/json",
                                 body=json.dumps({"ok": True, "stub": "ux-sweep"}))
        return route.continue_()

    def _on_console(self, m):
        if m.type != "error":
            return
        loc = (m.location or {}).get("url", "")
        t = m.text
        if "Failed to load resource" in t:
            self.ev["console"].append("RES:" + loc.replace(self.base, "") + " " + t[-60:])
            return
        if "net::ERR_FAILED" in t and loc and urlparse(loc).hostname not in ("127.0.0.1", "localhost"):
            return
        self.ev["console"].append(t[:240])

    def _on_dialog(self, d):
        self.ev["native"].append(f"{d.type}: {d.message[:120]}")
        try:
            d.dismiss()
        except Exception:                                   # noqa: BLE001
            pass

    def _on_request(self, r):
        u = urlparse(r.url)
        if u.hostname not in ("127.0.0.1", "localhost") or "/api/testing/" in r.url:
            return
        self.ev["requests"].append((r.method, u.path))
        self.ev["pending"].add(r.method + " " + r.url)

    def _on_response(self, r):
        u = urlparse(r.url)
        self.ev["pending"].discard(r.request.method + " " + r.url)
        if u.hostname not in ("127.0.0.1", "localhost") or "/api/testing/" in r.url:
            return
        self.ev["responses"].append((r.request.method, u.path, r.status))

    def _on_failed(self, r):
        key = r.method + " " + r.url
        if key in self.ev["pending"]:
            self.ev["pending"].discard(key)
            fail = (r.failure or "")
            if "ABORTED" in fail.upper() or "ERR_ABORTED" in fail:
                return
            self.ev["failed"].append(f"{key.replace(self.base, '')}: {fail}")

    # --- navigation
    def login(self) -> None:
        p = self.page
        p.goto(self.base + "/", wait_until="domcontentloaded")
        p.fill("#login-token", self.token)
        p.click("#login-submit")
        p.wait_for_selector("#shell:not([hidden])", timeout=20000)

    def open(self, route: str) -> None:
        p = self.page
        url = f"{self.base}/{route}" if route.endswith(".html") else f"{self.base}/#/{route}"
        p.goto("about:blank")
        p.goto(url, wait_until="domcontentloaded")
        if p.locator("#login:not([hidden])").count():
            p.fill("#login-token", self.token)
            p.click("#login-submit")
            p.wait_for_selector("#shell:not([hidden])", timeout=20000)
            p.goto("about:blank")
            p.goto(url, wait_until="domcontentloaded")
        self.wait_ready()

    def wait_ready(self, quiet_ms: int = 350, limit_ms: int = 9000) -> None:
        p = self.page
        try:
            p.wait_for_function("""() => { const v = document.querySelector('#view');
                if (!v) return document.body && document.body.childElementCount > 0;
                return v.childElementCount && !v.querySelector('.skeleton'); }""", timeout=15000)
        except Exception:                                   # noqa: BLE001
            pass
        t0 = time.monotonic()
        last, last_t = p.evaluate("window.__uxm"), time.monotonic()
        while (time.monotonic() - t0) * 1000 < limit_ms:
            p.wait_for_timeout(120)
            cur = p.evaluate("window.__uxm")
            if cur != last:
                last, last_t = cur, time.monotonic()
            elif (time.monotonic() - last_t) * 1000 >= quiet_ms:
                break

    def settle(self, ms: int = 600, net_limit: float = 8.0) -> None:
        self.page.wait_for_timeout(ms)
        t0 = time.monotonic()
        while self.ev["pending"] and time.monotonic() - t0 < net_limit:
            self.page.wait_for_timeout(100)
        self.page.wait_for_timeout(150)

    def state(self) -> dict:
        return self.page.evaluate(STATE_JS)

    def enum(self, scope: str) -> list[dict]:
        return self.page.evaluate(ENUM_JS, scope)

    def shot(self, name: str) -> None:
        self.shots.mkdir(parents=True, exist_ok=True)
        path = self.shots / f"{name}.png"
        try:
            self.page.screenshot(path=str(path), animations="disabled")
            try:
                from PIL import Image
                im = Image.open(path).convert("RGB")
                if im.width > 720:
                    im = im.resize((720, int(im.height * 720 / im.width)))
                im.quantize(48).save(path, optimize=True)
            except Exception:                               # noqa: BLE001
                pass
        except Exception:                                   # noqa: BLE001
            pass

    def close(self) -> None:
        try:
            self.browser.close()
        except Exception:                                   # noqa: BLE001
            pass


# ------------------------------------------------------------------ probing
STATE_CLS = re.compile(r"\b(active|on|off|selected|current|open|opened|is-[\w-]+|has-[\w-]+|pressed|checked|busy|loading)\b")


def sig_of(c: dict) -> str:
    cls = " ".join(STATE_CLS.sub("", c["cls"]).split())
    return f"{c['tag']}|{c['type']}|{c['label'][:50]}|{cls[:50]}|{c['href'][:60]}"


def is_gated(c: dict) -> bool:
    if c["kind"] == "link" and c["href"].startswith("#"):
        return False
    if c["kind"] == "text" or c["kind"] == "select":
        return False
    return bool(GATED.search(c["attrs"]))


def locate(sess: Session, scope: str, c: dict, nth: int, tries: int = 4) -> int | None:
    """Re-find control c on the freshly rendered page; returns its data-uxs index."""
    for attempt in range(tries):                            # late-rendering lists: give the page a moment
        cur = sess.enum(scope)
        same = [x for x in cur if sig_of(x) == sig_of(c)]
        if nth < len(same):
            return same[nth]["i"]
        sess.page.wait_for_timeout(700)
    return None


def classify(sess: Session, c: dict, gated: bool, before: dict, after: dict, noisy: bool, idle_reqs: set,
             tgt_before, tgt_after, new_ctrls: int) -> dict:
    ev = sess.ev
    effects: list[str] = []
    errors: list[str] = []
    refusals: list[str] = []
    new_toasts = set(after["toasts"]) - set(before["toasts"])
    errors += [f"pageerror: {e}" for e in ev["pageerrors"]]
    errors += [f"request failed: {e}" for e in ev["failed"]]
    codes = [(m, p, s) for (m, p, s) in ev["responses"]]
    visible_feedback = bool(new_toasts) or after["html"] != before["html"]
    # 503 = a dependency is unavailable (sidecar down, no key, no Chromium). When the UI shows the server's
    # own message in a new toast, the click got an honest, explained refusal - not a page failure.
    explained = lambda msg: any(msg.strip().splitlines()[0][:80] in t for t in new_toasts) if msg.strip() else False  # noqa: E731
    clicked_503 = [(m, p) for (m, p, s) in codes if s == 503 and (m, p) not in idle_reqs]
    for e in ev["console"]:
        if e.startswith("RES:"):
            continue
        api_msg = e[len("ApiError: "):] if e.startswith("ApiError: ") else None
        if api_msg is not None and clicked_503 and explained(api_msg):
            refusals.append("503 explained: " + api_msg.splitlines()[0][:90])
        else:
            errors.append(f"console: {e}")
    for m, p, s in codes:
        if s >= 500 and (m, p) in idle_reqs:
            continue                                     # background polling already failing before the click
        if s == 503 and refusals and any(r.startswith("503 explained") for r in refusals):
            refusals.append(f"HTTP 503 {m} {p}")
        elif s >= 500:
            errors.append(f"HTTP {s} {m} {p}")
        elif s >= 400:
            if s in (400, 403, 409, 422, 423, 428, 429) and visible_feedback:
                refusals.append(f"HTTP {s} {m} {p}")
            else:
                errors.append(f"HTTP {s} {m} {p}")
    if after["url"] != before["url"]:
        effects.append("url:" + after["url"].replace(sess.base, ""))
    if set(after["dlg"]) - set(before["dlg"]):
        effects.append("opened:" + sorted(set(after["dlg"]) - set(before["dlg"]))[0][:50])
    if new_toasts:
        effects.append("toast:" + sorted(new_toasts)[0][:60])
    if ev["filechoosers"]:
        effects.append("filechooser:" + ev["filechoosers"][0])
    if ev.get("native_validation"):
        refusals.append("native form validation: " + ev["native_validation"][0][:80])
    if new_ctrls:
        effects.append(f"{new_ctrls} new controls")
    mut = [(m, p, s) for (m, p, s) in codes if m != "GET"]
    for m, p, s in mut:
        effects.append(f"{m} {p} -> {s}")
    for m, p, s in codes:
        if m == "GET" and (m, p) not in idle_reqs and 200 <= s < 300:
            effects.append(f"GET {p} -> {s}")
    if ev["would_send"]:
        effects.append("would-send:" + ev["would_send"][0][:90])
    if ev["popups"]:
        effects.append("popup")
    if ev["downloads"]:
        effects.append("download:" + ev["downloads"][0])
    if after["ls"] != before["ls"]:
        effects.append("localStorage")
    if after["vals"] != before["vals"]:
        effects.append("field value")
    local = tgt_before != tgt_after
    if local:
        effects.append("control state")
    if not noisy and after["html"] != before["html"]:
        effects.append("dom")
    elif noisy and after["html"] != before["html"] and (local or new_toasts):
        effects.append("dom(near control)")
    if after["scroll"] != before["scroll"]:
        effects.append("scroll")
    gate_note = ""
    if ev["native"]:
        gate_note = "native: " + ev["native"][0]
    out = {"effects": effects[:8], "errors": errors[:5], "refusals": refusals[:3], "gate_note": gate_note}
    if errors:
        out["verdict"], out["detail"] = "ERROR", "; ".join(errors[:3])
    elif ev["native"]:
        out["verdict"], out["detail"] = "BLOCKED_BY_DESIGN", gate_note + " (cancelled)"
    elif refusals:
        out["verdict"], out["detail"] = "BLOCKED_BY_DESIGN", "refused with visible feedback: " + "; ".join(refusals)
    elif effects:
        out["verdict"], out["detail"] = "OK", "; ".join(effects[:4])
    elif c["selected"]:
        out["verdict"], out["detail"] = "OK", "already selected/active (no-op by design)"
        out["subtype"] = "noop_selected"
    else:
        out["verdict"], out["detail"] = "DEAD", "no DOM / URL / request / dialog / toast / storage change" + (" (noisy page)" if noisy else "")
    return out


def try_close_overlay(sess: Session, before_dlg: list[str]) -> str | None:
    """Close what the probe opened; returns a problem text if it could not be closed."""
    p = sess.page
    for _ in range(2):
        cur = set(sess.state()["dlg"]) - set(before_dlg)
        if not cur:
            return None
        p.keyboard.press("Escape")
        p.wait_for_timeout(250)
    cur = set(sess.state()["dlg"]) - set(before_dlg)
    if not cur:
        return None
    for pat in (r"закры|close|отмен|cancel|нет|no\b|×|✕|✖|back|назад|скры|hide"):
        pass
    try:
        btns = p.locator("dialog[open] button, .modal button, [role=dialog] button, #modal-root button, .drawer button")
        for k in range(min(btns.count(), 12)):
            b = btns.nth(k)
            t = (b.inner_text() or "") + " " + (b.get_attribute("aria-label") or "")
            if re.search(r"закры|close|отмен|cancel|нет\b|×|✕|✖|назад|скры|hide|ok|ок\b", t, re.I) and b.is_visible():
                b.click(timeout=2000)
                p.wait_for_timeout(300)
                if not (set(sess.state()["dlg"]) - set(before_dlg)):
                    return None
    except Exception:                                       # noqa: BLE001
        pass
    # a click on the scrim is the last thing a user would try
    try:
        p.mouse.click(3, 3)
        p.wait_for_timeout(250)
    except Exception:                                       # noqa: BLE001
        pass
    if not (set(sess.state()["dlg"]) - set(before_dlg)):
        return None
    return "overlay stays open after Escape, close/cancel button and outside click: " + sorted(cur)[0][:60]


def fill_value(c: dict) -> str:
    t = c["type"]
    return {"number": "1", "date": "2026-01-01", "time": "10:00", "datetime-local": "2026-01-01T10:00",
            "email": "ux@example.invalid", "url": "http://127.0.0.1/", "color": "#112233", "range": "1",
            "month": "2026-01", "week": "2026-W01", "tel": "123"}.get(t, "ux-sweep")


def probe(sess: Session, scope: str, route: str, c: dict, nth: int, prelude: list[tuple[dict, int, str]],
          depth: int) -> tuple[dict, list[tuple[dict, int]]]:
    """Probe with crash recovery: a crashed renderer is replaced and the probe retried once (crash counted)."""
    sess.uses += 1
    if sess.uses > 25:
        sess.fresh_page()
    r, kids = _probe(sess, scope, route, c, nth, prelude, depth)
    if r.get("subtype") == "click_failed" and re.search(r"crash|closed|Target", r.get("detail", ""), re.I):
        sess.fresh_page(crashed=True)
        try:
            sess.login()
        except Exception:                                   # noqa: BLE001
            pass
        r, kids = _probe(sess, scope, route, c, nth, prelude, depth)
        r["retried_after_page_crash"] = True
    return r, kids


def _probe(sess: Session, scope: str, route: str, c: dict, nth: int, prelude: list[tuple[dict, int, str]],
           depth: int) -> tuple[dict, list[tuple[dict, int]]]:
    gated = is_gated(c)
    rec = {"route": route, "viewport": sess.vp, "depth": depth, "kind": c["kind"], "label": c["label"], "cls": c["cls"],
           "href": c["href"], "sig": sig_of(c), "nth": nth, "gated": gated,
           "via": " > ".join(p[0]["label"][:30] for p in prelude)}
    if c["disabled"]:
        rec.update(verdict="DISABLED", detail=("explains: " + c["reason"]) if c["reason"] else "disabled without any reason shown",
                   subtype="disabled_reason" if c["reason"] else "disabled_silent")
        return rec, []
    p = sess.page
    try:
        sess.stub = False
        if not prelude:
            sess.open(route)
            i = locate(sess, scope, c, nth)
        else:
            # depth 1: stay on the page the opener left behind; reopen the opener in place, reload only as a last resort
            i = locate(sess, "all", c, nth, tries=1)
            for reload_first in (False, True):
                if i is not None:
                    break
                if reload_first:
                    sess.open(route)
                for pc, pnth, pscope in prelude:
                    oi = locate(sess, pscope if reload_first else "all", pc, pnth, tries=2)
                    if oi is None:
                        continue
                    p.locator(f'[data-uxs="{oi}"]').scroll_into_view_if_needed(timeout=3000)
                    sess.stub = is_gated(pc)
                    p.locator(f'[data-uxs="{oi}"]').click(timeout=4000)
                    sess.settle(500)
                    sess.stub = False
                i = locate(sess, "all", c, nth, tries=2)
        if i is None:
            rec.update(verdict="UNVERIFIED", subtype="vanished" if not prelude else "replay",
                       detail=("control not found again after re-render" if not prelude else "opener/child not reproducible in place or after reload")
                              + " (harness could not reach it; not counted as a product bug)")
            return rec, []
        loc = p.locator(f'[data-uxs="{i}"]')
        # --- external links are never followed
        if c["kind"] == "link" and c["href"] and not c["href"].startswith("#"):
            u = urlparse(c["href"])
            if u.scheme in ("mailto", "tel") or (u.hostname and u.hostname not in ("127.0.0.1", "localhost")):
                rec.update(verdict="OK", detail=f"external link {c['href'][:70]} present, not followed (gated)", subtype="external_link",
                           gated=True)
                return rec, []
        idle_before = sess.state()
        sess.reset_events()
        p.wait_for_timeout(350)                          # idle window: measures polling noise on this page
        idle_after = sess.state()
        idle_reqs = {(m, pth) for (m, pth) in sess.ev["requests"]}
        noisy = idle_after["html"] != idle_before["html"]
        sess.reset_events()
        before = sess.state()
        before_all = {sig_of(x) for x in sess.enum("all")}
        i = locate(sess, "all" if prelude else scope, c, nth) or i
        tgt_before = p.evaluate(TARGET_JS, i)
        loc = p.locator(f'[data-uxs="{i}"]')
        invalid_before = []
        if c["kind"] == "button":
            try:
                invalid_before = loc.evaluate(INVALID_JS)
            except Exception:                            # noqa: BLE001
                invalid_before = []
        sess.stub = gated
        def act() -> dict | None:
            loc.scroll_into_view_if_needed(timeout=3000)
            if c["kind"] == "text":
                loc.fill(fill_value(c), timeout=4000)
                loc.dispatch_event("input")
                loc.dispatch_event("change")
            elif c["kind"] == "select":
                opts = loc.evaluate("e => [...e.options].filter(o=>!o.disabled).map(o=>o.value)")
                cur = loc.evaluate("e => e.value")
                pick = next((o for o in opts if o != cur), None)
                if pick is None:
                    rec.update(verdict="OK", detail=f"select has a single option ({cur!r}); nothing to change", subtype="single_option")
                    return rec, []
                loc.select_option(pick, timeout=4000)
            elif c["kind"] == "toggle":
                loc.click(timeout=4000)
            else:
                loc.click(timeout=4000)
            return None

        for attempt in (0, 1):
            try:
                early = act()
                break
            except Exception as exc:                        # noqa: BLE001
                if attempt == 0 and re.search(r"not attached|not stable|detached|intercepts pointer", str(exc)):
                    i = locate(sess, "all" if prelude else scope, c, nth) or i
                    loc = p.locator(f'[data-uxs="{i}"]')
                    p.wait_for_timeout(500)
                    continue
                raise
        if early:
            return early
        sess.settle()
        sess.stub = False
        if invalid_before:                               # the browser's own "fill in this field" bubble
            sess.ev["native_validation"] = invalid_before
        after = sess.state()
        tgt_after = p.evaluate(TARGET_JS, i)
        typed = None
        if c["kind"] == "text":                          # read now: enum() below renumbers data-uxs
            try:
                typed = loc.input_value(timeout=2000)
            except Exception:                            # noqa: BLE001
                typed = None
        if tgt_after and tgt_before and c["kind"] in ("text", "select"):
            pass
        after_all = sess.enum("all")
        newc = [x for x in after_all if sig_of(x) not in before_all]
        r = classify(sess, c, gated, before, after, noisy,
                     idle_reqs, (tgt_before or {}).get("self"), (tgt_after or {}).get("self") if tgt_after else None, len(newc))
        if c["kind"] == "text" and r["verdict"] == "DEAD" and (typed == fill_value(c) or c["type"] in INPUT_ONLY_TYPES):
            r.update(verdict="OK", detail="value accepted and retained; no listener reacts (input only)", subtype="input_only")
        if gated and r["verdict"] == "DEAD":
            r["detail"] = "GATED control produced no effect even up to the gate: " + r["detail"]
        rec.update(r)
        rec["requests"] = sorted({f"{m} {pth}" for (m, pth) in sess.ev["requests"] if m != "GET"})[:5]
        rec["would_send"] = sess.ev["would_send"][:3]
        rec["blocked_native"] = sess.ev["native"][:2]
        stuck = try_close_overlay(sess, before["dlg"])
        if stuck:
            rec["verdict"], rec["detail"], rec["subtype"] = "ERROR", stuck, "stuck_overlay"
        children = []
        if (depth == 0 and rec["verdict"] in ("OK", "BLOCKED_BY_DESIGN") and newc and after["url"] == before["url"]
                and c["kind"] in ("button", "summary", "tab", "link")):
            children = [(x, 0) for x in newc if x["kind"] != "text"][:14]
        return rec, children
    except Exception as exc:                                # noqa: BLE001
        msg = str(exc).split("Call log:")[0].strip().splitlines()[0][:160] if str(exc) else type(exc).__name__
        extra = ""
        try:
            extra = sess.page.evaluate("""() => { const e=document.elementFromPoint(innerWidth/2, innerHeight/2); return e?e.tagName+'.'+e.className:''; }""")
        except Exception:                                   # noqa: BLE001
            pass
        rec.update(verdict="ERROR", detail=f"{type(exc).__name__}: {msg}", subtype="click_failed")
        return rec, []
    finally:
        sess.stub = False


def sweep_route(sess: Session, route: str, scope: str, per_sig: int, deadline: float, log) -> tuple[list[dict], dict]:
    recs: list[dict] = []
    meta = {"route": route, "viewport": sess.vp, "controls_enumerated": 0, "load_error": None}
    sess.reset_events()
    try:
        sess.open(route)
    except Exception as exc:                                # noqa: BLE001
        meta["load_error"] = f"{type(exc).__name__}: {str(exc)[:120]}"
        recs.append({"route": route, "viewport": sess.vp, "depth": 0, "kind": "page", "label": "(page load)", "verdict": "ERROR",
                     "detail": meta["load_error"], "gated": False, "sig": "page", "nth": 0, "cls": "", "href": "", "via": ""})
        return recs, meta
    pre_err = sess.ev["pageerrors"][:] + [e for e in sess.ev["console"] if not e.startswith("RES:")]
    http_err = [(m, p, s) for (m, p, s) in sess.ev["responses"] if s >= 400]
    sess.shot(f"{route.replace('?', '_').replace('=', '')}-{sess.vp}" if scope in ("view", "all") else f"shell-{sess.vp}")
    ctrls = sess.enum(scope)
    meta["controls_enumerated"] = len(ctrls)
    meta["load_errors"] = {"pageerrors": pre_err[:4], "http": [f"{m} {p} {s}" for m, p, s in http_err][:6]}
    if pre_err or http_err:
        recs.append({"route": route, "viewport": sess.vp, "depth": 0, "kind": "page", "label": "(page render)",
                     "verdict": "ERROR" if pre_err or any(s >= 500 for _, _, s in http_err) else "OK",
                     "detail": "; ".join(pre_err[:2] + [f"{m} {p} -> {s}" for m, p, s in http_err[:3]]),
                     "gated": False, "sig": "page", "nth": 0, "cls": "", "href": "", "via": "", "subtype": "load"})
        if recs[-1]["verdict"] == "OK":
            recs.pop()                                       # 4xx on load is reported in meta only
    seen: dict[str, int] = {}
    quota: dict[tuple, int] = {}
    todo = []
    for c in ctrls:
        s = sig_of(c)
        k = seen.get(s, 0)
        seen[s] = k + 1
        qk = (c["kind"], c["cls"][:40])
        if len(ctrls) > BIG_PAGE and quota.get(qk, 0) >= CLASS_QUOTA:
            recs.append({"route": route, "viewport": sess.vp, "depth": 0, "kind": c["kind"], "label": c["label"], "cls": c["cls"],
                         "href": c["href"], "sig": s, "nth": k, "gated": is_gated(c), "via": "", "verdict": "SAMPLED",
                         "detail": f"big page ({len(ctrls)} controls): first {CLASS_QUOTA} per kind+class probed"})
        elif k < per_sig:
            quota[qk] = quota.get(qk, 0) + 1
            todo.append((c, k))
        else:
            recs.append({"route": route, "viewport": sess.vp, "depth": 0, "kind": c["kind"], "label": c["label"], "cls": c["cls"],
                         "href": c["href"], "sig": s, "nth": k, "gated": is_gated(c), "via": "", "verdict": "SAMPLED",
                         "detail": f"same label+class as instance #{per_sig}; first {per_sig} probed"})
    for c, nth in todo:
        if time.monotonic() > deadline:
            recs.append({"route": route, "viewport": sess.vp, "depth": 0, "kind": c["kind"], "label": c["label"], "cls": c["cls"],
                         "href": c["href"], "sig": sig_of(c), "nth": nth, "gated": is_gated(c), "via": "", "verdict": "NOT_RUN",
                         "detail": "time budget exhausted"})
            continue
        entry = route if scope in ("view", "all") else "home"
        r, kids = probe(sess, scope, entry, c, nth, [], 0)
        r["route"] = route
        recs.append(r)
        log(r)
        kseen: dict[str, int] = {}
        for kc, _ in kids:
            ks = sig_of(kc)
            kn = kseen.get(ks, 0)
            kseen[ks] = kn + 1
            if kn >= 1:
                continue
            kr, _ = probe(sess, scope, entry, kc, kn, [(c, nth, scope)], 1)
            kr["route"] = route
            recs.append(kr)
            log(kr)
    return recs, meta


def summarize(results: list[dict], metas: list[dict]) -> dict:
    counts: dict[str, int] = {}
    for r in results:
        counts[r["verdict"]] = counts.get(r["verdict"], 0) + 1
    probed = [r for r in results if r["verdict"] in ("OK", "DEAD", "ERROR", "BLOCKED_BY_DESIGN")]
    return {
        "controls_enumerated": sum(m.get("controls_enumerated", 0) for m in metas),
        "probes": len(probed), "OK": counts.get("OK", 0), "DEAD": counts.get("DEAD", 0), "ERROR": counts.get("ERROR", 0),
        "BLOCKED_BY_DESIGN": counts.get("BLOCKED_BY_DESIGN", 0), "DISABLED": counts.get("DISABLED", 0),
        "UNVERIFIED": counts.get("UNVERIFIED", 0), "SAMPLED": counts.get("SAMPLED", 0), "NOT_RUN": counts.get("NOT_RUN", 0),
        "gated": sum(1 for r in results if r.get("gated") and r["verdict"] not in ("SAMPLED", "NOT_RUN")),
    }


def md_cell(t: str, n: int = 70) -> str:
    return str(t).replace("|", "/").replace("\n", " ")[:n]


def write_md(doc: dict, notes: dict, path: Path) -> None:
    recs = doc["records"]
    L = [f"# UX sweep - Bossman Command Center ({doc.get('target', '?')})", "",
         f"- build: `{doc.get('build_sha', '?')}` (health/live: `{md_cell(doc.get('health_live', ''), 200)}`)",
         *([f"- per-viewport builds: " + "; ".join(f"{vp}: `{b['build'].split('BOSSMAN-Windows-x64-')[-1]}` (health/live sha `{b.get('build_sha') or 'n/a'}`)" for vp, b in doc["builds"].items())] if doc.get("builds") else []),
         f"- harness sha (git HEAD): `{doc['sha']}`; run {doc['started_at']} .. {doc['finished_at']}",
         f"- viewports: {', '.join(doc['viewports'])}; pages: {len(doc['routes'])}; sampling: first {doc['per_sig']} identical controls per page",
         f"- external network blocked (page.route abort): {json.dumps(doc.get('external_blocked', {}), ensure_ascii=False)}", "",
         "## Totals", "", "| viewport | enumerated | probes | OK | DEAD | ERROR | BLOCKED_BY_DESIGN | DISABLED | UNVERIFIED | gated | sampled(not probed) |",
         "|---|---|---|---|---|---|---|---|---|---|---|"]
    for vp in doc["viewports"]:
        sub = [r for r in recs if r["viewport"] == vp]
        sm = summarize(sub, [m for m in doc["pages"] if m["viewport"] == vp])
        L.append(f"| {vp} | {sm['controls_enumerated']} | {sm['probes']} | {sm['OK']} | {sm['DEAD']} | {sm['ERROR']} | "
                 f"{sm['BLOCKED_BY_DESIGN']} | {sm['DISABLED']} | {sm['UNVERIFIED']} | {sm['gated']} | {sm['SAMPLED']} |")
    sm = doc["summary"]
    L += [f"| all | {sm['controls_enumerated']} | {sm['probes']} | {sm['OK']} | {sm['DEAD']} | {sm['ERROR']} | {sm['BLOCKED_BY_DESIGN']} | {sm['DISABLED']} | {sm['UNVERIFIED']} | {sm['gated']} | {sm['SAMPLED']} |",
          "", "OK = an effect was observed; DEAD = nothing observable; ERROR = pageerror/console.error/5xx/unexplained 4xx/stuck overlay/click impossible;",
          "BLOCKED_BY_DESIGN = confirm gate or intended refusal with visible feedback; gated = destructive/outward control probed only up to the gate (mutating requests stubbed).", ""]
    L += ["## Per page", "", "| page | viewport | enumerated | probed | OK | DEAD | ERROR | BLOCKED | DISABLED | UNVERIFIED | gated |", "|---|---|---|---|---|---|---|---|---|---|---|"]
    for m in doc["pages"]:
        sub = [r for r in recs if r["route"] == m["route"] and r["viewport"] == m["viewport"]]
        c = lambda v: sum(1 for r in sub if r["verdict"] == v)          # noqa: E731
        L.append(f"| {m['route']} | {m['viewport']} | {m.get('controls_enumerated', 0)} | {sum(c(v) for v in ('OK', 'DEAD', 'ERROR', 'BLOCKED_BY_DESIGN'))} | "
                 f"{c('OK')} | {c('DEAD')} | {c('ERROR')} | {c('BLOCKED_BY_DESIGN')} | {c('DISABLED')} | {c('UNVERIFIED')} | {sum(1 for r in sub if r.get('gated') and r['verdict'] not in ('SAMPLED', 'NOT_RUN'))} |")
    bad = [r for r in recs if r["verdict"] in ("DEAD", "ERROR", "UNVERIFIED")]
    L += ["", f"## DEAD / ERROR findings ({len(bad)})", ""]
    if bad:
        L += ["| page | vp | control | verdict | detail | status |", "|---|---|---|---|---|---|"]
        for r in bad:
            key = f"{r['route']}|{r['viewport']}|{r['label']}"
            st = notes.get("triage", {}).get(key) or notes.get("triage", {}).get(f"{r['route']}|*|{r['label']}", "untriaged")
            L.append(f"| {r['route']} | {r['viewport']} | {md_cell(r['label'], 40)} | {r['verdict']} | {md_cell(r.get('detail', ''), 110)} | {md_cell(st, 90)} |")
    else:
        L.append("none")
    gaps = notes.get("gaps")
    if gaps:
        L += ["", "## Gap closure (ux_gaps.py: hand reproductions, click-timeout retries)", ""]
        L += [f"- {g}" for g in gaps]
    L += ["", "## Fixes (ux-fix commits)", ""]
    L += [f"- `{f['sha']}` {f['title']} - {f['evidence']}" for f in notes.get("fixes", [])] or ["none"]
    L += ["", "## Open issues / owner decisions", ""]
    L += [f"- {o}" for o in notes.get("open_issues", [])] or ["none"]
    L += ["", "## Text / facts checks", ""]
    L += [f"- {t}" for t in notes.get("text_checks", [])] or ["none recorded"]
    L += ["", "## Full matrix (page -> control -> desktop / phone)", ""]
    rows: dict[tuple, dict] = {}
    for r in recs:
        k = (r["route"], r.get("via", ""), r["sig"], r["nth"])
        rows.setdefault(k, {"r": r})[r["viewport"]] = r
    cur = None
    for k, v in rows.items():
        if k[0] != cur:
            cur = k[0]
            L += ["", f"### {cur}", "", "| control | kind | desktop | phone | note |", "|---|---|---|---|---|"]
        r = v["r"]
        f = lambda vp: (v[vp]["verdict"] + ("(G)" if v[vp].get("gated") else "")) if vp in v else "-"      # noqa: E731
        note = (v.get("desktop") or v.get("phone") or r).get("detail", "")
        L.append(f"| {md_cell((k[1] + ' > ' if k[1] else '') + r['label'], 60)} | {r['kind']} | {f('desktop')} | {f('phone')} | {md_cell(note, 80)} |")
    path.write_text("\n".join(L) + "\n", encoding="utf-8")


def merge(args) -> int:
    docs = [json.loads(Path(x).read_text(encoding="utf-8")) for x in args.merge]
    d0 = docs[0]
    recs = [r for d in docs for r in d["records"]]
    metas = [m for d in docs for m in d["pages"]]
    ext = {}
    for d in docs:
        ext.update(d.get("external_blocked", {}))
    builds = {d["viewports"][0] if len(d["viewports"]) == 1 else "+".join(d["viewports"]): {"build": d.get("build", ""), "build_sha": d.get("build_sha", ""),
              "health_live": d.get("health_live", "")} for d in docs}
    doc = dict(d0, builds=builds, viewports=[v for d in docs for v in d["viewports"]], records=recs, pages=metas, external_blocked=ext,
               finished_at=max(d["finished_at"] for d in docs), started_at=min(d["started_at"] for d in docs),
               summary=summarize(recs, metas))
    notes_p = args.out / "ux-sweep-notes.json"
    notes = json.loads(notes_p.read_text(encoding="utf-8")) if notes_p.exists() else {}
    doc["notes"] = notes
    (args.out / "ux-sweep.json").write_text(json.dumps(doc, ensure_ascii=False, indent=1), encoding="utf-8")
    write_md(doc, notes, args.out / "ux-sweep.md")
    print(json.dumps(doc["summary"], ensure_ascii=False))
    return 0


def main() -> int:
    try:                                                    # the owner asked not to load the machine
        import psutil
        psutil.Process().nice(psutil.BELOW_NORMAL_PRIORITY_CLASS if sys.platform == "win32" else 10)
    except Exception:                                       # noqa: BLE001
        pass
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(encoding="utf-8", errors="replace")
        except Exception:                                   # noqa: BLE001
            pass
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--pages", default="", help="comma list of routes (default: all from ui/pages/index.js + ui/pages.js, plus 'shell')")
    ap.add_argument("--viewport", default="both", choices=["desktop", "phone", "both"])
    ap.add_argument("--no-fix", action="store_true", help="do not run the FIX_TESTS regression tests after the sweep")
    ap.add_argument("--port", type=int, default=8893)
    ap.add_argument("--target", default="installed", choices=["installed", "source"])
    ap.add_argument("--build", default=r"C:\Users\asd\Bossman\app\BOSSMAN-Windows-x64-803aa4d9103a")
    ap.add_argument("--per-sig", type=int, default=2, help="probe the first N identical controls (same label+class)")
    ap.add_argument("--budget-min", type=float, default=120.0)
    ap.add_argument("--out", type=Path, default=EVID)
    ap.add_argument("--json", type=Path, default=None)
    ap.add_argument("--merge", nargs="+", default=None, help="merge per-viewport result JSONs into ux-sweep.json/.md in --out")
    args = ap.parse_args()
    if args.merge:
        return merge(args)
    started = now()
    sys.path.insert(0, str(REPO / "scripts"))
    import ui_acceptance_sweep as U                        # route list comes from the product's own registry
    if args.target == "installed":
        ui = Path(args.build) / "runtime" / "Lib" / "site-packages" / "bcc" / "_ui"
        all_pages = U.page_routes((ui / "pages" / "index.js").read_text(encoding="utf-8"), (ui / "pages.js").read_text(encoding="utf-8"))
    else:
        all_pages = U.page_ids()
    routes = [r.strip() for r in args.pages.split(",") if r.strip()] or (["shell"] + all_pages + ["chat.html"])
    vps = ["desktop", "phone"] if args.viewport == "both" else [args.viewport]
    sha = subprocess.run(["git", "rev-parse", "HEAD"], cwd=REPO, capture_output=True, text=True).stdout.strip()
    tmp = Path(tempfile.mkdtemp(prefix="bcc-uxsweep-"))
    be = Throwaway(args.port, tmp, None if args.target == "source" else Path(args.build))
    results: list[dict] = []
    metas: list[dict] = []
    external: dict[str, dict] = {}
    deadline = time.monotonic() + args.budget_min * 60
    shots = args.out / "ux-sweep"
    jl = (tmp / "records.jsonl").open("a", encoding="utf-8")

    def log(r: dict) -> None:
        jl.write(json.dumps(r, ensure_ascii=False) + "\n")
        jl.flush()
        print(f"[{r['viewport']}] {r['route']:18s} {r['verdict']:17s} {'G' if r.get('gated') else ' '} {r['label'][:38]:38s} {r.get('detail','')[:90]}", flush=True)

    from playwright.sync_api import sync_playwright
    be.start()
    try:
        with sync_playwright() as pw:
            for vp in vps:
                sess = Session(pw, be.base, be.token, vp, shots)
                try:
                    sess.login()
                    for route in routes:
                        recs, meta = sweep_route(sess, route, "shell" if route == "shell" else ("all" if route.endswith(".html") else "view"), args.per_sig, deadline, log)
                        results += recs
                        metas.append(meta)
                finally:
                    external[vp] = dict(sess.blocked_external)
                    sess.close()
    finally:
        be.stop()
        jl.close()
    finished = now()

    summary = summarize(results, metas)
    doc = {"schema": 1, "target": args.target, "build": str(args.build) if args.target == "installed" else "source checkout",
           "health_live": be.live, "build_sha": (re.search(r"[0-9a-f]{40}|[0-9a-f]{8,12}", be.live) or [""])[0], "sha": sha, "started_at": started, "finished_at": finished, "viewports": vps, "routes": routes,
           "per_sig": args.per_sig, "summary": summary, "external_blocked": external, "pages": metas, "records": results}
    out_json = args.json or (args.out / "ux-sweep.json")
    out_json.parent.mkdir(parents=True, exist_ok=True)
    out_json.write_text(json.dumps(doc, ensure_ascii=False, indent=1), encoding="utf-8")
    print(json.dumps(summary, ensure_ascii=False))
    if not args.no_fix and FIX_TESTS:
        rc = subprocess.run([sys.executable, "-m", "pytest", "-q", *FIX_TESTS], cwd=REPO / "command-center").returncode
        print("fix regression tests exit", rc)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
