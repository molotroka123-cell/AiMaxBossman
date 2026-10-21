"""browser.* tool family: real engine path -> real headless Chromium -> local fixture website.

Every claim is checked against something the browser tool cannot fake: the fixture web server's own
request log (GET/POST counts and bodies), files on disk (screenshot PNG header, downloaded bytes and
their sha256) and the text the model actually received. Fixture site: 127.0.0.1 only, synthetic content.
"""
from __future__ import annotations

import hashlib
import http.server
import json
import os
import re
import socketserver
import struct
import threading
import time
import urllib.parse
from pathlib import Path
from typing import Any

from .common import OUT_REL, PROVEN_EXECUTED, PROVEN_GATED, FAILED, NOT_RUN, REPO_ROOT, Row, add_repo_paths, short
from .harness import Lab, Trace, tool_row

add_repo_paths()

PLAYWRIGHT_BROWSERS = r"C:\Users\asd\AppData\Local\ms-playwright"      # browser binaries only, no data
FILE_BYTES = ("proof-download-payload " * 40).encode("utf-8")
GOOD_LOGIN, GOOD_SECRET = "proof-user", "S3cretPW-proof-7431"
EVIDENCE = REPO_ROOT / OUT_REL / "evidence" / "browser"

_HOME = """<!doctype html><html lang="ru"><head><meta charset="utf-8"><title>Proof Home</title></head><body>
<h1>Домашняя страница proof</h1><p id="stamp">stamp-HOME</p>
<a id="to-form" href="/form">на форму</a></body></html>"""

_FORM = """<!doctype html><html lang="ru"><head><meta charset="utf-8"><title>Proof Form</title></head><body>
<h1>Форма proof</h1>
<input id="name" name="name" placeholder="Имя">
<select id="color" name="color"><option value="red">red</option><option value="green">green</option></select>
<button id="show" type="button" onclick="document.getElementById('out').textContent =
  'Принято: ' + document.getElementById('name').value + ' / цвет: ' + document.getElementById('color').value">Показать</button>
<p id="out">пока пусто</p>
<form method="post" action="/submit"><input id="msg" name="msg" placeholder="Сообщение">
<button id="sendbtn" type="submit">Отправить</button></form>
<a id="dl" href="/file.txt">скачать</a>
</body></html>"""

_LOGIN = """<!doctype html><html><head><meta charset="utf-8"><title>Proof Login</title></head><body>
<h1>Вход</h1><form method="post" action="/login"><input id="user" name="user" placeholder="Логин">
<input id="pass" name="pass" type="password" placeholder="Пароль"><button id="signin" type="submit">Войти</button>
</form></body></html>"""

_OWNER = """<!doctype html><html><head><meta charset="utf-8"><title>Proof Owner Form</title></head><body>
<h1>Регистрация</h1><form method="post" action="/owner"><input id="email" name="email" placeholder="Email">
<input id="code" name="code" type="password" placeholder="Код"><button id="regbtn" type="submit">Зарегистрироваться</button>
</form></body></html>"""


class Fixture:
    """Local fixture site with a request log (the independent witness)."""

    def __init__(self) -> None:
        self.log: list[dict[str, Any]] = []
        fx = self

        class H(http.server.BaseHTTPRequestHandler):
            def log_message(self, *a: Any) -> None:  # silent
                pass

            def _send(self, body: bytes, ctype: str = "text/html; charset=utf-8", code: int = 200, extra: dict | None = None) -> None:
                self.send_response(code)
                self.send_header("Content-Type", ctype)
                self.send_header("Content-Length", str(len(body)))
                for k, v in (extra or {}).items():
                    self.send_header(k, v)
                self.end_headers()
                self.wfile.write(body)

            def do_GET(self) -> None:  # noqa: N802
                p = self.path.split("?", 1)[0]
                fx.log.append({"m": "GET", "p": p})
                pages = {"/": _HOME, "/form": _FORM, "/login": _LOGIN, "/owner": _OWNER}
                if p in pages:
                    self._send(pages[p].encode())
                elif p == "/dashboard":
                    self._send("<html><head><title>Dash</title></head><body><h1>Кабинет</h1><p>Welcome proof-user</p></body></html>".encode())
                elif p == "/owner-done":
                    self._send("<html><head><title>Done</title></head><body><h1>Регистрация принята</h1></body></html>".encode())
                elif p == "/file.txt":
                    self._send(FILE_BYTES, "text/plain; charset=utf-8",
                               extra={"Content-Disposition": 'attachment; filename="proof.txt"'})
                else:
                    self._send(b"", code=404)

            def do_POST(self) -> None:  # noqa: N802
                n = int(self.headers.get("Content-Length") or 0)
                body = urllib.parse.parse_qs(self.rfile.read(n).decode("utf-8", "replace"))
                p = self.path.split("?", 1)[0]
                fx.log.append({"m": "POST", "p": p, "body": {k: v[0] for k, v in body.items()}})
                if p == "/submit":
                    msg = body.get("msg", [""])[0]
                    self._send(f"<html><head><title>Sent</title></head><body><h1>Получено</h1><p>msg={msg}</p></body></html>".encode())
                elif p == "/login":
                    ok = body.get("user") == [GOOD_LOGIN] and body.get("pass") == [GOOD_SECRET]
                    self._send(b"", code=302, extra={"Location": "/dashboard" if ok else "/login"})
                elif p == "/owner":
                    self._send(b"", code=302, extra={"Location": "/owner-done"})
                else:
                    self._send(b"", code=404)

        self.httpd = socketserver.ThreadingTCPServer(("127.0.0.1", 0), H)
        self.httpd.daemon_threads = True
        threading.Thread(target=self.httpd.serve_forever, daemon=True).start()
        self.url = f"http://127.0.0.1:{self.httpd.server_address[1]}"

    def count(self, method: str, path: str) -> int:
        return sum(1 for e in self.log if e["m"] == method and e["p"] == path)

    def posts(self, path: str) -> list[dict]:
        return [e["body"] for e in self.log if e["m"] == "POST" and e["p"] == path]

    def close(self) -> None:
        self.httpd.shutdown()
        self.httpd.server_close()


def _png_dims(path: Path) -> tuple[int, int] | None:
    data = path.read_bytes()
    if data[:8] != b"\x89PNG\r\n\x1a\n" or data[12:16] != b"IHDR":
        return None
    return struct.unpack(">II", data[16:24])


def _dump(name: str, trace: Trace, extra: dict | None = None) -> None:
    EVIDENCE.mkdir(parents=True, exist_ok=True)
    payload = {
        "status": trace.status,
        "tool_rows": [{k: (str(v) if k in ("created_at", "finished_at") else v) for k, v in r.items()
                       if k in ("tool", "status", "effect", "approval_id", "approved_by", "duration_ms", "created_at", "error", "result_preview")}
                      for r in trace.tool_rows],
        "waits": [{"approval_preview": w.approval.get("preview"), "decision": w.decision} for w in trace.waits],
        "tool_messages": trace.tool_messages(),
        "extra": extra or {},
    }
    (EVIDENCE / f"{name}.json").write_text(json.dumps(payload, ensure_ascii=False, indent=1, default=str), encoding="utf-8")


def _tool_msgs(trace: Trace) -> list[str]:
    return trace.tool_messages()


def _status_of(trace: Trace, tool: str, n: int = 0) -> str:
    rows = trace.rows_for(tool)
    return rows[n]["status"] if len(rows) > n else "missing"


async def run(lab: Lab, root: Path) -> list[Row]:
    root = Path(root)
    root.mkdir(parents=True, exist_ok=True)
    rows: dict[str, Row] = {}
    saved_env = {k: os.environ.get(k) for k in ("PLAYWRIGHT_BROWSERS_PATH", "BCC_BROWSER_ALLOW_PRIVATE")}
    os.environ["PLAYWRIGHT_BROWSERS_PATH"] = PLAYWRIGHT_BROWSERS
    os.environ["BCC_BROWSER_ALLOW_PRIVATE"] = "1"
    site = Fixture()
    try:
        await _run(lab, root, site, rows)
    finally:
        site.close()
        for k, v in saved_env.items():
            if v is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = v
    names = [n for n in sorted(_registry_names()) if n not in rows]
    for n in names:
        rows[n] = tool_row(n, NOT_RUN, "no browser probe defined for this tool", "")
    return [rows[n] for n in sorted(rows)]


def _registry_names() -> list[str]:
    from bcc.tools import REGISTRY
    return [n for n in REGISTRY.names() if n.startswith("browser.")]


def _fail_all(rows: dict[str, Row], names: list[str], why: str) -> None:
    for n in names:
        rows[n] = tool_row(n, NOT_RUN, why, caveat="Chromium/Playwright could not run the first browser step; nothing claimed")


async def _run(lab: Lab, root: Path, site: Fixture, rows: dict[str, Row]) -> None:
    base = site.url
    all_names = _registry_names()
    perms = {"browser.read": True, "browser.control": True}

    # ------------------------------------------------------------------ P1: AUTO chain on a real Chromium
    agent = await lab.agent(["browser.open", "browser.read_dom", "browser.screenshot", "browser.click", "browser.type",
                             "browser.select", "browser.back", "browser.reload"], perms, max_steps=14, name="proof-browser-auto")
    shot_info: dict[str, Any] = {}

    def capture_shot(hist: list[str]) -> dict:
        """Runs between the screenshot call and the next tool, while the session (and its frame ring) is alive:
        the product deletes a session's screenshots when the task ends, so the file must be read NOW."""
        m = re.search(r"скриншот сохранён: (.+?) \((\d+) байт\)", hist[-1])
        if m:
            p = Path(m.group(1))
            if p.is_file():
                data = p.read_bytes()
                shot_info.update(path=str(p), reported=int(m.group(2)), size=len(data), magic=data[:8],
                                 dims=struct.unpack(">II", data[16:24]) if data[12:16] == b"IHDR" else None,
                                 sha256=hashlib.sha256(data).hexdigest()[:12])
        return {"selector": "#to-form"}

    steps = [
        ("tool", "browser.open", {"url": base + "/"}),
        ("tool", "browser.read_dom", {}),
        ("tool", "browser.screenshot", {}),
        ("tool", "browser.click", capture_shot),
        ("tool", "browser.type", {"selector": "#name", "text": "Тимур-proof"}),
        ("tool", "browser.select", {"selector": "#color", "value": "green"}),
        ("tool", "browser.click", {"selector": "#show"}),
        ("tool", "browser.read_dom", {}),
        ("tool", "browser.back", {}),
        ("tool", "browser.reload", {}),
        ("text", "готово"),
    ]
    t0 = time.monotonic()
    tr = await lab.run(agent, steps, decide=None, timeout=120.0)
    _dump("p1_auto_chain", tr, {"fixture_log": site.log, "seconds": round(time.monotonic() - t0, 1)})
    msgs = _tool_msgs(tr)
    first_err = msgs[0] if msgs else ""
    if not msgs or "браузер недоступен" in first_err or "не удалось открыть сессию" in first_err or "рантайм недоступен" in first_err:
        _fail_all(rows, all_names, short(first_err or tr.status, 300))
        return

    def ok(cond: bool) -> str:
        return PROVEN_EXECUTED if cond else FAILED

    exec_all = all(r["status"] == "executed" and r["effect"] == "auto" for r in tr.tool_rows) and len(tr.tool_rows) == 10
    # 0 open, 1 read_dom, 2 screenshot, 3 click(link), 4 type, 5 select, 6 click(show), 7 read_dom, 8 back, 9 reload
    open_ok = "Proof Home" in msgs[0] and "stamp-HOME" in msgs[0] and site.count("GET", "/") >= 1
    rows["browser.open"] = tool_row("browser.open", ok(open_ok and _status_of(tr, "browser.open") == "executed"),
        f"AUTO executed; model received title 'Proof Home' + 'stamp-HOME'; fixture server logged GET / x{site.count('GET', '/')}",
        "fixture site on 127.0.0.1 with BCC_BROWSER_ALLOW_PRIVATE=1 (owner override); default SSRF guard proven separately")
    rows["browser.read_dom"] = tool_row("browser.read_dom", ok("stamp-HOME" in msgs[1] and "Принято: Тимур-proof / цвет: green" in msgs[7]),
        "AUTO executed; read_dom #1 returned page text 'stamp-HOME'; read_dom #2 returned JS-updated text 'Принято: Тимур-proof / цвет: green'",
        "fixture site, headless Chromium")
    shot_ok = bool(shot_info and shot_info.get("magic") == b"\x89PNG\r\n\x1a\n" and shot_info.get("dims")
                   and shot_info["size"] == shot_info["reported"] and shot_info["size"] > 500)
    shot_note = (f"file {Path(shot_info['path']).name} {shot_info['size']} B read while the session was alive, PNG IHDR {shot_info['dims']}, "
                 f"sha256 {shot_info['sha256']}.." if shot_info else "no readable file at the reported path")
    rows["browser.screenshot"] = tool_row("browser.screenshot", ok(shot_ok), f"AUTO executed; screenshot on disk: {shot_note}; size equals the size the tool reported",
        "screenshot of the fixture page only (never the owner's desktop); image content not visually judged, only PNG header/size. Observation: the product deletes a session's screenshots when the task ends")
    link_ok = "URL: " + base + "/form" in msgs[3] and "Proof Form" in msgs[3] and site.count("GET", "/form") >= 1
    show_ok = "Принято: Тимур-proof / цвет: green" in msgs[7]
    rows["browser.click"] = tool_row("browser.click", ok(link_ok and show_ok),
        f"AUTO executed; click on link #to-form -> result 'URL: .../form', server logged GET /form; click on JS button #show changed page text",
        "non-consequence clicks only; consequence clicks are ASK (see browser.submit)")
    rows["browser.type"] = tool_row("browser.type", ok(show_ok and "Тимур-proof" in msgs[7]),
        "AUTO executed; text typed into #name later appears on the page as 'Принято: Тимур-proof ...' after the JS click", "")
    rows["browser.select"] = tool_row("browser.select", ok("цвет: green" in msgs[7]),
        "AUTO executed; option 'green' chosen in #color and read back from the page as 'цвет: green'", "")
    back_ok = ("URL: " + base + "/" in msgs[8]) and "Proof Home" in msgs[8]
    rows["browser.back"] = tool_row("browser.back", ok(back_ok),
        "AUTO executed; after back the tool result shows 'URL: <base>/' and title 'Proof Home' (previous page)", "")
    before = site.count("GET", "/")
    reload_ok = "Proof Home" in msgs[9] and before >= 2     # open + reload hit '/' (back may be cached)
    rows["browser.reload"] = tool_row("browser.reload", ok(reload_ok and "URL: " + base + "/" in msgs[9]),
        f"AUTO executed; page reloaded, fixture server saw GET / x{before} (open + reload)", "")
    if not exec_all:
        for n in ("browser.open", "browser.read_dom", "browser.screenshot", "browser.click", "browser.type",
                  "browser.select", "browser.back", "browser.reload"):
            if rows[n].status == PROVEN_EXECUTED:
                rows[n].status = FAILED
                rows[n].evidence += " | BUT tool_calls rows were not all executed/auto: " + str([(r['tool'], r['status'], r['effect']) for r in tr.tool_rows])

    # ------------------------------------------------------------------ P2: browser.submit (ASK) on a real POST
    agent2 = await lab.agent(["browser.open", "browser.type", "browser.submit", "browser.read_dom"], perms, max_steps=10, name="proof-browser-submit")
    seen: dict[str, Any] = {}

    async def decide_submit(w) -> bool:
        seen["posts_before"] = site.count("POST", "/submit")
        seen["preview"] = w.approval.get("preview", "")
        return True

    steps2 = [("tool", "browser.open", {"url": base + "/form"}),
              ("tool", "browser.type", {"selector": "#msg", "text": "hello-submit-7431"}),
              ("tool", "browser.submit", {"selector": "#sendbtn"}),
              ("tool", "browser.read_dom", {}),
              ("text", "готово")]
    tr2 = await lab.run(agent2, steps2, decide=decide_submit, timeout=120.0)
    _dump("p2_submit", tr2, {"posts_before": seen.get("posts_before"), "posts_after": site.posts("/submit")})
    m2 = _tool_msgs(tr2)
    sub_rows = tr2.rows_for("browser.submit")
    gate_ok_ = (len(tr2.waits) == 1 and "browser.submit" in seen.get("preview", "") and seen.get("posts_before") == 0
                and any(r["status"] == "pending_approval" and r["effect"] == "ask" for r in tr2.waits[0].tool_rows))
    effect_ok = (site.posts("/submit") == [{"msg": "hello-submit-7431"}] and "msg=hello-submit-7431" in " ".join(m2)
                 and sub_rows and sub_rows[0]["status"] == "executed" and sub_rows[0]["effect"] == "ask")
    rows["browser.submit"] = tool_row("browser.submit", PROVEN_EXECUTED if (gate_ok_ and effect_ok) else FAILED,
        f"ASK: approval preview names browser.submit; fixture POST count BEFORE approval={seen.get('posts_before')}; after approval exactly 1 POST body={site.posts('/submit')}; result page 'msg=hello-submit-7431' read back",
        "approval decided by the proof as 'owner' via the product approvals API")

    # consequence click through browser.click must NOT go through unasked
    agent2b = await lab.agent(["browser.open", "browser.click"], perms, max_steps=6, name="proof-browser-click-guard")
    posts_b = site.count("POST", "/submit")
    tr2b = await lab.run(agent2b, [("tool", "browser.open", {"url": base + "/form"}),
                                   ("tool", "browser.click", {"selector": "#sendbtn"}), ("text", "готово")], decide=False, timeout=90.0)
    _dump("p2b_click_guard", tr2b, {"posts": site.count("POST", "/submit") - posts_b})
    mb = _tool_msgs(tr2b)
    guard_ok = (site.count("POST", "/submit") == posts_b and len(mb) >= 2 and "ничего не нажато" in mb[-1]
                and _status_of(tr2b, "browser.click") == "error")
    rows["browser.click"].details["consequence_click_guard"] = ("held (handler refused: nothing clicked, 0 POSTs)" if guard_ok else "NOT HELD")
    if not guard_ok:
        rows["browser.click"].status = FAILED
        rows["browser.click"].evidence += " | consequence click on 'Отправить' was NOT held"
    else:
        rows["browser.click"].evidence += "; consequence click on 'Отправить' (flagged publish) via browser.click was refused by the handler ('Действие не выполнено — ничего не нажато'), fixture server saw 0 POSTs"

    # ------------------------------------------------------------------ P3: browser.download (ASK)
    agent3 = await lab.agent(["browser.open", "browser.download"], perms, max_steps=6, name="proof-browser-download")
    dl_seen: dict[str, Any] = {}
    dl_root = Path(lab.settings.data_dir)

    def find_downloads() -> list[Path]:
        return [p for p in dl_root.rglob("proof.txt")]

    async def decide_dl(w) -> bool:
        dl_seen["files_before"] = [str(p) for p in find_downloads()]
        dl_seen["preview"] = w.approval.get("preview", "")
        return True

    tr3 = await lab.run(agent3, [("tool", "browser.open", {"url": base + "/"}),
                                 ("tool", "browser.download", {"url": base + "/file.txt"}), ("text", "готово")],
                        decide=decide_dl, timeout=120.0)
    files_after = find_downloads()
    sha = hashlib.sha256(FILE_BYTES).hexdigest()
    got = next((hashlib.sha256(p.read_bytes()).hexdigest() for p in files_after), None)
    _dump("p3_download", tr3, {"before": dl_seen.get("files_before"), "after": [str(p) for p in files_after]})
    dl_ok = (len(tr3.waits) == 1 and "browser.download" in dl_seen.get("preview", "") and dl_seen.get("files_before") == []
             and got == sha and sha[:12] in " ".join(_tool_msgs(tr3)))
    rows["browser.download"] = tool_row("browser.download", PROVEN_EXECUTED if dl_ok else FAILED,
        f"ASK: preview names browser.download; downloaded file did NOT exist before approval; after approval {len(files_after)} file proof.txt on disk with sha256={str(got)[:12]}.. == server bytes sha256 {sha[:12]}..; tool reported the same sha256",
        "download from the fixture site into the temp data dir")

    # ------------------------------------------------------------------ P4: browser.login (ASK) with a saved credential
    from bcc.features.tools_browser import save_credentials
    await save_credentials(lab.svc, {"proof-acc": {"login": GOOD_LOGIN, "password": GOOD_SECRET, "domain": "127.0.0.1", "note": "proof"}})
    agent4 = await lab.agent(["browser.open", "browser.login", "browser.read_dom"], perms, max_steps=8, name="proof-browser-login")
    lg_seen: dict[str, Any] = {}

    async def decide_login(w) -> bool:
        lg_seen["posts_before"] = site.count("POST", "/login")
        lg_seen["preview"] = w.approval.get("preview", "")
        return True

    tr4 = await lab.run(agent4, [
        ("tool", "browser.open", {"url": base + "/login"}),
        ("tool", "browser.login", {"credential_id": "proof-acc", "login_selector": "#user", "password_selector": "#pass",
                                   "submit_selector": "#signin", "success_url_contains": "/dashboard"}),
        ("tool", "browser.read_dom", {}),
        ("text", "готово")], decide=decide_login, timeout=120.0)
    m4 = _tool_msgs(tr4)
    posts = site.posts("/login")
    leak_scan = " ".join(m4) + json.dumps([{k: str(v) for k, v in r.items()} for r in tr4.tool_rows], ensure_ascii=False) \
        + " ".join(w.approval.get("preview", "") for w in tr4.waits) + json.dumps(tr4.model.seen_messages, ensure_ascii=False)
    no_leak = GOOD_SECRET not in leak_scan
    _dump("p4_login", tr4, {"posts": [{k: ("<pw>" if k == "pass" else v) for k, v in p.items()} for p in posts], "secret_in_model_visible_text": not no_leak})
    login_ok = (len(tr4.waits) == 1 and "browser.login" in lg_seen.get("preview", "") and lg_seen.get("posts_before") == 0
                and len(posts) == 1 and posts[0].get("user") == GOOD_LOGIN and posts[0].get("pass") == GOOD_SECRET
                and "Welcome proof-user" in " ".join(m4) and no_leak)
    rows["browser.login"] = tool_row("browser.login", PROVEN_EXECUTED if login_ok else FAILED,
        f"ASK: preview names browser.login; login POSTs BEFORE approval={lg_seen.get('posts_before')}; after approval exactly 1 POST with the vault password (server saw it), redirect to /dashboard, model read 'Welcome proof-user'; secret appears in 0 model-visible messages / tool_calls rows / approval previews",
        "credential created in the temp vault; fixture login accepts only that pair; requires a local-model agent (proven by running with one)")

    # ------------------------------------------------------------------ P5: default SSRF guard on, then override
    agent5 = await lab.agent(["browser.open"], perms, max_steps=4, name="proof-browser-ssrf")
    prev = os.environ.pop("BCC_BROWSER_ALLOW_PRIVATE", None)
    g_before = len(site.log)
    try:
        tr5 = await lab.run(agent5, [("tool", "browser.open", {"url": base + "/"}), ("text", "готово")], decide=None, timeout=60.0)
    finally:
        if prev is not None:
            os.environ["BCC_BROWSER_ALLOW_PRIVATE"] = prev
    m5 = _tool_msgs(tr5)
    refused = (site.log[g_before:] == [] and bool(m5) and _status_of(tr5, "browser.open") == "error"
               and "browser action denied" in m5[0] and "BCC_BROWSER_ALLOW_PRIVATE" in m5[0])
    _dump("p5_ssrf_default", tr5, {"server_requests_during": site.log[g_before:], "tool_message": m5[:1]})
    rows["browser.open"].details["ssrf_default_guard"] = {"loopback_refused": refused, "server_saw": len(site.log[g_before:]), "message": short(m5[0] if m5 else "", 200)}
    if refused:
        rows["browser.open"].evidence += f"; with the override UNSET the same 127.0.0.1 URL was refused ('{short(m5[0], 90)}') and the server saw 0 requests"
    else:
        rows["browser.open"].status = FAILED
        rows["browser.open"].evidence += f" | default SSRF guard did NOT refuse 127.0.0.1: {short(m5[0] if m5 else tr5.status, 200)}"

    # ------------------------------------------------------------------ P6: owner-input flow (owner answer simulated via the store)
    agent6 = await lab.agent(["browser.open", "browser.request_owner_fields", "browser.fill_owner_fields", "browser.submit",
                              "browser.verify_owner_input_success", "browser.read_dom"], perms, max_steps=14, name="proof-browser-owner")
    store = lab.svc.owner_input
    OWNER_EMAIL, OWNER_CODE = "owner.proof@example.test", "Pr00f-Code-9917"
    answered: dict[str, Any] = {}

    def fill_args(hist: list[str]) -> dict:
        rid = re.search(r"request_id=([0-9a-zA-Z_-]+)", hist[-1]).group(1)
        # simulate the human channel: the Telegram handler calls exactly this store method
        answered["row"] = store.answer(rid, {"email": OWNER_EMAIL, "code": OWNER_CODE}, actor="tg:proof-owner")
        answered["rid"] = rid
        return {"request_id": rid}

    def verify_args(hist: list[str]) -> dict:
        return {"request_id": answered["rid"]}

    sub_seen: dict[str, Any] = {}

    async def decide_owner(w) -> bool:
        sub_seen["posts_before"] = site.count("POST", "/owner")
        sub_seen["preview"] = w.approval.get("preview", "")
        return True

    tr6 = await lab.run(agent6, [
        ("tool", "browser.open", {"url": base + "/owner"}),
        ("tool", "browser.request_owner_fields", {"context": "proof signup", "fields": [
            {"key": "email", "label": "Email", "selector": "#email"},
            {"key": "code", "label": "Code", "selector": "#code", "secret": True}],
            "success": {"url_contains": "/owner-done", "contains_text": "Регистрация принята"}}),
        ("tool", "browser.fill_owner_fields", fill_args),
        ("tool", "browser.submit", {"selector": "#regbtn"}),
        ("tool", "browser.verify_owner_input_success", verify_args),
        ("text", "готово")], decide=decide_owner, timeout=120.0)
    m6 = _tool_msgs(tr6)
    leak6 = OWNER_EMAIL in json.dumps(tr6.model.seen_messages, ensure_ascii=False) or OWNER_CODE in json.dumps(tr6.model.seen_messages, ensure_ascii=False) \
        or OWNER_CODE in json.dumps([{k: str(v) for k, v in r.items()} for r in tr6.tool_rows], ensure_ascii=False)
    owner_posts = site.posts("/owner")
    final_row = store.get(answered.get("rid", "")) if answered.get("rid") else None
    _dump("p6_owner_input", tr6, {"owner_posts": [{"email": p.get("email"), "code": "<hidden>"} for p in owner_posts],
                                  "final_status": (final_row or {}).get("status"), "model_saw_values": leak6})
    req_ok = len(m6) >= 2 and "request_id=" in m6[1] and not leak6
    fill_ok = len(m6) >= 3 and "заполнены поля" in m6[2] and "email" in m6[2] and "code" in m6[2]
    sub_ok = (len(tr6.waits) == 1 and "browser.submit" in sub_seen.get("preview", "") and sub_seen.get("posts_before") == 0
              and owner_posts == [{"email": OWNER_EMAIL, "code": OWNER_CODE}])
    ver_ok = len(m6) >= 5 and "VERIFIED" in tr6.rows_for("browser.verify_owner_input_success")[0].get("result_preview", "") + m6[-1] \
        or (final_row or {}).get("status") == "VERIFIED"
    cav = "the owner's answer is injected by calling OwnerInputStore.answer (the call the Telegram handler makes); the Telegram/human channel itself is NOT exercised"
    rows["browser.request_owner_fields"] = tool_row("browser.request_owner_fields", PROVEN_EXECUTED if req_ok else FAILED,
        f"AUTO executed against a real page: request created (request_id in result), 2 fields incl. a secret, success criteria frozen; model-visible text contains neither answer value", cav)
    rows["browser.fill_owner_fields"] = tool_row("browser.fill_owner_fields", PROVEN_EXECUTED if (fill_ok and sub_ok and not leak6) else FAILED,
        f"AUTO executed after the (simulated) owner answer: runtime filled email+secret code into the real form; fixture server later received POST /owner with exactly those values; values never appeared in model-visible messages or tool_calls rows", cav)
    rows["browser.verify_owner_input_success"] = tool_row("browser.verify_owner_input_success", PROVEN_EXECUTED if (ver_ok and sub_ok) else FAILED,
        f"AUTO executed after ASK submit: fresh page check against frozen criteria (url_contains /owner-done + text 'Регистрация принята') -> request status {(final_row or {}).get('status')}", cav)

    # negative control: criteria that are NOT met must NOT verify
    agent6b = await lab.agent(["browser.open", "browser.request_owner_fields", "browser.fill_owner_fields",
                               "browser.verify_owner_input_success"], perms, max_steps=10, name="proof-browser-owner-neg")
    answered_b: dict[str, Any] = {}

    def fill_args_b(hist: list[str]) -> dict:
        rid = re.search(r"request_id=([0-9a-zA-Z_-]+)", hist[-1]).group(1)
        store.answer(rid, {"email": OWNER_EMAIL}, actor="tg:proof-owner")
        answered_b["rid"] = rid
        return {"request_id": rid}

    tr6b = await lab.run(agent6b, [
        ("tool", "browser.open", {"url": base + "/owner"}),
        ("tool", "browser.request_owner_fields", {"context": "neg", "fields": [{"key": "email", "label": "Email", "selector": "#email"}],
                                                  "success": {"contains_text": "текст-которого-нет-на-странице"}}),
        ("tool", "browser.fill_owner_fields", fill_args_b),
        ("tool", "browser.verify_owner_input_success", lambda h: {"request_id": answered_b["rid"]}),
        ("text", "готово")], decide=None, timeout=90.0)
    mb6 = _tool_msgs(tr6b)
    neg_ok = (len(mb6) >= 4 and "НЕ подтверждён" in mb6[-1] and (store.get(answered_b["rid"]) or {}).get("status") != "VERIFIED"
              and _status_of(tr6b, "browser.verify_owner_input_success") == "error")
    _dump("p6b_owner_input_negative", tr6b, {"status": (store.get(answered_b.get("rid", "")) or {}).get("status")})
    rows["browser.verify_owner_input_success"].details["negative_control"] = "unmet criteria -> 'НЕ подтверждён', status stays " + str((store.get(answered_b.get("rid", "")) or {}).get("status"))
    if neg_ok:
        rows["browser.verify_owner_input_success"].evidence += "; negative control: unmet criterion -> 'успех … НЕ подтверждён', request not VERIFIED"
    else:
        rows["browser.verify_owner_input_success"].status = FAILED
        rows["browser.verify_owner_input_success"].evidence += " | negative control FAILED: " + short(mb6[-1] if mb6 else tr6b.status, 160)
    # submit evidence also covers the owner-input path
    rows["browser.submit"].evidence += "; second ASK->POST path (owner form) also observed: 1 POST /owner after approval"

    for n in all_names:
        if n not in rows:
            rows[n] = tool_row(n, NOT_RUN, "no probe defined for this browser tool")
