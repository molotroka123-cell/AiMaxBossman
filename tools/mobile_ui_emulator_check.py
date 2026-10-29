"""Emulator check of the mobile pult UI (iPhone 15 Pro viewport, light and dark): static shell only, no backend, no owner data.
Unhides the app shell, opens every tab through the app's own showTab, records console errors and takes screenshots."""
import json, subprocess, sys, time, threading, http.server, functools, socketserver
from pathlib import Path
from playwright.sync_api import sync_playwright

UI = Path(sys.argv[1]); OUT = Path(sys.argv[2]); OUT.mkdir(parents=True, exist_ok=True)
class Q(http.server.SimpleHTTPRequestHandler):
    def log_message(self, *a): pass
handler = functools.partial(Q, directory=str(UI.parent))      # served as /ui/...
srv = socketserver.TCPServer(("127.0.0.1", 8899), handler); threading.Thread(target=srv.serve_forever, daemon=True).start()
report = {"errors": {}, "fonts": {}, "tabbar": {}}
with sync_playwright() as p:
    b = p.chromium.launch()
    for scheme in ("dark", "light"):
        ctx = b.new_context(**p.devices["iPhone 15 Pro"], color_scheme=scheme)
        page = ctx.new_page(); errs = []
        page.on("pageerror", lambda e: errs.append(str(e)[:160]))
        page.goto("http://127.0.0.1:8899/ui/index.html", wait_until="load")
        page.wait_for_timeout(800)
        page.evaluate("""() => { document.querySelectorAll('section,.screen,[data-screen]').forEach(e=>{}); const a=document.getElementById('app'); if(a) a.hidden=false;
                         document.querySelectorAll('.auth, .login, #login, #pair').forEach(e=>e.hidden=true); }""")
        for tab in ("chat", "photo", "video", "panel", "swap", "calls", "jeff", "status"):
            page.evaluate("t => document.querySelector(`.tab[data-tab='${t}']`).click()", tab)
            page.wait_for_timeout(450)
            page.screenshot(path=str(OUT / f"{scheme}-{tab}.png"))
        report["fonts"][scheme] = page.evaluate("() => document.fonts.check('16px InterVar')")
        report["tabbar"][scheme] = page.evaluate("() => { const t=document.getElementById('tabbar'); const r=t.getBoundingClientRect(); return {w:r.width,h:r.height,i:t.style.getPropertyValue('--tab-i'), active:document.querySelector('.tab.active')?.dataset.tab}; }")
        report["errors"][scheme] = [e for e in errs if "fetch" not in e.lower() and "api" not in e.lower()][:10]
        ctx.close()
    b.close()
srv.shutdown()
(OUT / "report.json").write_text(json.dumps(report, ensure_ascii=False, indent=1), encoding="utf-8")
print(json.dumps(report, ensure_ascii=False))
