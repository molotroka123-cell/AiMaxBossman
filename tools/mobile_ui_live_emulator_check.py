"""Live emulator check of the mobile pult: a TEST pult (never the owner's :8840) talking to a candidate backend, iPhone 15 Pro, light+dark.

    python tools/mobile_ui_live_emulator_check.py [pult url] [test pult data dir] [out dir]

Logs in with the test pult's own pairing token (read in-process, never printed), opens all 8 tabs through the real tab buttons,
records page errors and API answers >= 400, takes a screenshot per tab and theme, writes report.json. Candidate data only.
"""
import json
import sys
from pathlib import Path

from playwright.sync_api import sync_playwright

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "apps" / "bossman-mobile"))
from bossmobile.auth import load_or_create_pairing  # noqa: E402

URL = sys.argv[1] if len(sys.argv) > 1 else "http://127.0.0.1:8842/m/"
DATA = Path(sys.argv[2]) if len(sys.argv) > 2 else Path(r"D:\Bossman\work\testrun\mobile-data")
OUT = Path(sys.argv[3]) if len(sys.argv) > 3 else Path(r"D:\Bossman\work\ux-emulator\live")
TABS = ("chat", "photo", "video", "panel", "swap", "calls", "jeff", "status")


def main() -> int:
    if ":8840" in URL:
        raise SystemExit("refused: :8840 is the owner's pult; use a test pult")
    OUT.mkdir(parents=True, exist_ok=True)
    token = load_or_create_pairing(DATA)
    rep = {"url": URL, "errors": {}, "api_5xx": {}, "api_4xx": {}, "logged_in": {}}
    with sync_playwright() as p:
        browser = p.chromium.launch()
        for scheme in ("dark", "light"):
            ctx = browser.new_context(**p.devices["iPhone 15 Pro"], color_scheme=scheme)
            page = ctx.new_page()
            errs: list[str] = []
            bad5: list[str] = []
            bad4: list[str] = []

            def on_response(r, bad5=bad5, bad4=bad4):
                if "/m/api/" in r.url and r.status >= 400:
                    (bad5 if r.status >= 500 else bad4).append(f"{r.status} {r.url.split('/m/api/')[-1][:70]}")
            page.on("pageerror", lambda e, errs=errs: errs.append(str(e)[:200]))
            page.on("response", on_response)
            page.goto(URL, wait_until="load")
            page.wait_for_timeout(1200)
            if page.is_visible("#login-token"):
                page.fill("#login-token", token)
                page.press("#login-token", "Enter")
                page.wait_for_timeout(2500)
            rep["logged_in"][scheme] = page.evaluate("() => !document.getElementById('app').hidden")
            for tab in TABS:
                page.click(f".tab[data-tab='{tab}']")
                page.wait_for_timeout(1800)
                page.screenshot(path=str(OUT / f"{scheme}-{tab}.png"))
            rep["errors"][scheme] = errs[:10]
            rep["api_5xx"][scheme] = sorted(set(bad5))[:20]
            rep["api_4xx"][scheme] = sorted(set(bad4))[:20]
            ctx.close()
        browser.close()
    (OUT / "report.json").write_text(json.dumps(rep, ensure_ascii=False, indent=1), encoding="utf-8")
    print(json.dumps(rep, ensure_ascii=False))
    ok = all(rep["logged_in"].values()) and not any(rep["errors"].values()) and not any(rep["api_5xx"].values())
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
