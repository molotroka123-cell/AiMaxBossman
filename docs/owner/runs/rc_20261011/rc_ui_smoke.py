"""RC UI smoke for an unpacked BOSSMAN-Windows-x64-* folder (run with the bundle's own runtime).

    <bundle>\\runtime\\python.exe rc_ui_smoke.py <bundle_home> <port> <data_dir> <out_dir>

Starts the bundled backend on a throwaway data dir and a free port, drives it with the
bundle's Chromium (headless): login with the token file, open key pages, create a task,
press STOP, probe safe defaults. Nothing of the owner's data dir or services is touched:
HOME/LOCALAPPDATA/APPDATA/USERPROFILE point inside <data_dir>.
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
import time
from pathlib import Path

import httpx

home, port, data_dir, out_dir = Path(sys.argv[1]), int(sys.argv[2]), Path(sys.argv[3]), Path(sys.argv[4])
out_dir.mkdir(parents=True, exist_ok=True)
data_dir.mkdir(parents=True, exist_ok=True)
fake = data_dir.parent / (data_dir.name + "-home")
fake.mkdir(parents=True, exist_ok=True)
base = f"http://127.0.0.1:{port}"
report: dict = {"bundle": str(home), "port": port, "checks": {}, "pages": {}, "defaults": {}}


def check(name: str, ok: bool, detail=None) -> None:
    report["checks"][name] = {"ok": bool(ok), "detail": detail}
    print(("PASS " if ok else "FAIL ") + name, "" if detail is None else str(detail)[:200], flush=True)


env = {k: v for k, v in os.environ.items() if k.upper() in {"SYSTEMROOT", "WINDIR", "COMSPEC", "PATHEXT", "TEMP", "TMP", "NUMBER_OF_PROCESSORS", "PROCESSOR_ARCHITECTURE"}}
env.update({
    "BCC_DATA_DIR": str(data_dir), "BCC_PORT": str(port), "BCC_HOST": "127.0.0.1", "BCC_TOKEN_STDOUT": "0",
    "PYTHONUTF8": "1", "PYTHONIOENCODING": "utf-8",
    "USERPROFILE": str(fake), "HOME": str(fake), "LOCALAPPDATA": str(fake / "Local"), "APPDATA": str(fake / "Roaming"),
    "PLAYWRIGHT_BROWSERS_PATH": str(home / "browser"),
    "PATH": os.pathsep.join([str(home / "runtime" / "Scripts"), str(home / "media"), env_sys := os.environ.get("SystemRoot", r"C:\Windows") + r"\System32"]),
})
(fake / "Local").mkdir(exist_ok=True)
(fake / "Roaming").mkdir(exist_ok=True)
log = (out_dir / "server.log").open("wb")
server = subprocess.Popen([str(home / "runtime" / "python.exe"), "-m", "bcc", "--host", "127.0.0.1", "--port", str(port)],
                          env=env, stdout=log, stderr=subprocess.STDOUT, cwd=str(home))
verdict = "FAIL"
try:
    http = httpx.Client(base_url=base, trust_env=False, timeout=15)
    t0 = time.monotonic()
    alive = False
    while time.monotonic() - t0 < 120:
        if server.poll() is not None:
            break
        try:
            r = http.get("/health/live")
            if r.status_code == 200 and r.json().get("alive") is True:
                alive = True
                break
        except Exception:
            pass
        time.sleep(0.5)
    report["boot_seconds"] = round(time.monotonic() - t0, 1)
    check("boot_alive", alive, report["boot_seconds"])
    if not alive:
        raise SystemExit("server did not start")
    check("fresh_data_dir_created_db", (data_dir / "bcc.db").exists(), sorted(p.name for p in data_dir.iterdir())[:20])
    unauth = http.get("/api/tasks")
    check("api_requires_auth", unauth.status_code in (401, 403), unauth.status_code)
    token = (data_dir / "token").read_text(encoding="utf-8").strip()

    from playwright.sync_api import sync_playwright

    with sync_playwright() as pw:
        browser = pw.chromium.launch(headless=True)
        ctx = browser.new_context(viewport={"width": 1440, "height": 900})
        page = ctx.new_page()
        problems: list = []
        page.on("pageerror", lambda e: problems.append({"pageerror": str(e)[:300]}))
        page.on("console", lambda m: problems.append({"console": m.text[:300]}) if m.type == "error" else None)
        page.on("response", lambda r: problems.append({"http": r.status, "url": r.url[:160]}) if r.status >= 500 else None)
        page.goto(base + "/", wait_until="domcontentloaded")
        page.wait_for_selector("#login-token", timeout=30000, state="visible")
        page.screenshot(path=str(out_dir / "00_login.png"))
        check("login_screen_shown", True)
        page.fill("#login-token", token)
        page.press("#login-token", "Enter")
        page.wait_for_selector("#nav a, #nav button", timeout=60000)
        page.wait_for_timeout(1500)
        page.screenshot(path=str(out_dir / "01_after_login.png"))
        check("login_ok_nav_built", page.locator("#nav a, #nav button").count() > 0, page.locator("#nav a, #nav button").count())

        pages = ["home-v3", "mission_console", "apps", "control", "video-studio", "router", "skills", "resources", "capability-tree", "openrouter"]
        for i, pid in enumerate(pages, start=2):
            before = len(problems)
            page.evaluate("p => { location.hash = '#/' + p }", pid)
            page.wait_for_timeout(2500)
            body = page.evaluate("() => (document.querySelector('#view')||document.body).innerText.slice(0,400)")
            shot = out_dir / f"{i:02d}_{pid}.png"
            page.screenshot(path=str(shot))
            new = problems[before:]
            report["pages"][pid] = {"text_len": len(body), "problems": new[:6], "shot": shot.name, "head": body[:120]}
            check(f"page_{pid}", len(body.strip()) > 10 and not any("pageerror" in x or "http" in x for x in new), len(body))

        # defaults probe (authenticated API client sharing the UI session via login)
        login = http.post("/api/login", json={"token": token, "label": "rc-smoke"})
        check("api_login", login.status_code == 200, login.status_code)
        http.headers["X-BCC-CSRF"] = login.json().get("csrf", "")
        for name, path in [("identity", "/api/identity"), ("offline", "/api/offline"), ("models", "/api/models"),
                           ("providers", "/api/providers"), ("apps", "/api/apps"), ("agents", "/api/agents"),
                           ("tasks", "/api/tasks"), ("health", "/api/health")]:
            try:
                r = http.get(path)
                try:
                    body = r.json()
                except Exception:
                    body = r.text[:200]
                report["defaults"][name] = {"status": r.status_code, "body": json.dumps(body, ensure_ascii=False)[:9000 if name in ("health", "offline") else 1500]}
            except Exception as exc:  # noqa: BLE001
                report["defaults"][name] = {"error": repr(exc)[:200]}

        # create provider -> model -> agent -> task (a clean install has none of them; the provider
        # points at a closed loopback port, so the run must end in a readable failure, never a 500)
        prov = http.post("/api/providers", json={"name": "rc-smoke-closed-port", "kind": "openai_compat",
                                                 "base_url": "http://127.0.0.1:9/v1", "api_key": "rc-smoke-placeholder"})
        check("provider_created", prov.status_code in (200, 201), prov.status_code)
        mod = http.post("/api/models", json={"provider_id": prov.json().get("id"), "name": "rc-smoke", "alias": "rc-smoke"})
        check("model_created", mod.status_code in (200, 201), mod.status_code)
        ag = http.post("/api/agents", json={"name": "rc-smoke-agent", "system_prompt": "short answers",
                                            "model_id": mod.json().get("id"), "max_steps": 1})
        check("agent_created", ag.status_code in (200, 201), ag.status_code)
        agent_id = ag.json().get("id")
        r = http.post("/api/tasks", json={"title": "RC smoke task", "agent_id": agent_id, "run_now": True,
                                          "max_retries": 0, "prompt": "Reply with the number 4. Do not use tools."})
        task_ok = r.status_code in (200, 201)
        task_id = r.json().get("task", {}).get("id") if task_ok else None
        check("task_created", task_ok and task_id is not None, {"status": r.status_code, "id": task_id})
        bad = http.post("/api/tasks", json={"title": "x", "prompt": "hi", "agent_id": 424242, "run_now": False})
        check("task_unknown_agent_is_4xx", bad.status_code in (404, 422), bad.status_code)
        if task_id:
            final = None
            for _ in range(60):
                g = http.get(f"/api/tasks/{task_id}").json()
                final = g
                if g["task"]["status"] in {"completed", "failed", "stopped", "blocked", "paused", "waiting_approval"}:
                    break
                time.sleep(0.5)
            report["task_terminal"] = {"status": final["task"]["status"], "error": str(final.get("error"))[:300]}
            check("task_reaches_readable_terminal_state", final["task"]["status"] in {"failed", "blocked", "stopped", "paused"} and bool(final.get("error")), report["task_terminal"])

        # STOP via the UI control page, then verify through the API
        page.evaluate("() => { location.hash = '#/control' }")
        page.wait_for_timeout(2500)
        stop_btn = page.locator("button", has_text="STOP").first
        if stop_btn.count() == 0:
            stop_btn = page.locator("button", has_text="Стоп").first
        if stop_btn.count() == 0:
            stop_btn = page.locator("button", has_text="Остановить").first
        ui_stop = stop_btn.count() > 0
        report["stop_button_found_in_ui"] = ui_stop
        if ui_stop:
            stop_btn.click()
            page.wait_for_timeout(1500)
            # a confirmation dialog may follow
            for label in ("Подтвердить", "Да", "Остановить всё", "Confirm", "OK"):
                b = page.locator("button", has_text=label).first
                if b.count() > 0 and b.is_visible():
                    b.click()
                    page.wait_for_timeout(1500)
                    break
            page.screenshot(path=str(out_dir / "20_after_stop_ui.png"))
        r = http.post("/api/control-plane/stop-all", json={})
        report["stop_all_api"] = {"status": r.status_code, "body": r.text[:600]}
        check("stop_all_api", r.status_code == 200, r.status_code)
        page.screenshot(path=str(out_dir / "21_after_stop_api.png"))
        report["problems_total"] = len(problems)
        report["problems_sample"] = problems[:15]
        browser.close()
    verdict = "PASS" if all(c["ok"] for c in report["checks"].values()) else "FAIL"
finally:
    try:
        server.terminate()
        server.wait(timeout=20)
    except Exception:
        server.kill()
    log.close()
    report["verdict"] = verdict
    (out_dir / "ui_smoke.json").write_text(json.dumps(report, indent=1, ensure_ascii=False), encoding="utf-8")
    print("UI_SMOKE=" + verdict, flush=True)
sys.exit(0 if verdict == "PASS" else 1)
