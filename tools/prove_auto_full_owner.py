"""Live Auto Full proof AS THE OWNER through the mobile pult API (owner 31.10: «отработай ауто фулл и проверь от моего лица»).

    python tools/prove_auto_full_owner.py [--agent 52]

Steps (every one through http://127.0.0.1:8840/m/api with the owner's pairing login, like the phone):
  1. card reads (no «Not Found»);  2. C:\\Users and the Python install are refused;  3. enable on a fresh test folder;
  4. a real agent task runs terminal commands INSIDE the folder with no approval asked (calls carry the Auto Full lease);
  5. a command that leaves the folder is not auto-approved;  6. revoke → the next in-folder command asks again.
Writes a JSON receipt to docs/audits/<date>-auto-full-owner/receipt.json. Nothing outside the test folder is written.
"""
from __future__ import annotations

import argparse
import contextlib
import json
import os
import sqlite3
import sys
import time
import urllib.error
import urllib.request
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "apps" / "bossman-mobile"))
sys.path.insert(0, str(ROOT / "tools"))
from bossmobile.auth import load_or_create_pairing  # noqa: E402
from bossmobile.config import Settings as MobileSettings  # noqa: E402
import prove_chat_attachments as cc  # noqa: E402  (owner token API to the command center)

PULT = "http://127.0.0.1:8840"
TEST_ROOT = Path(r"C:\Users\asd\Bossman\wt-agent-proof-20261025")
DB = Path(os.environ.get("BCC_DATA_DIR") or Path(os.environ["LOCALAPPDATA"]) / "Bossman" / "CommandCenter") / "bcc.db"


def http(method, path, body=None, headers=None):
    data = json.dumps(body).encode() if body is not None else None
    req = urllib.request.Request(PULT + path, data=data, method=method,
                                 headers={"Content-Type": "application/json", **(headers or {})})
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
    try:
        with opener.open(req, timeout=60) as r:
            return r.status, json.loads(r.read() or b"{}"), dict(r.headers)
    except urllib.error.HTTPError as e:
        try:
            return e.code, json.loads(e.read() or b"{}"), dict(e.headers)
        except ValueError:
            return e.code, {}, {}


def login():
    code, body, hdrs = http("POST", "/m/api/login", {"token": load_or_create_pairing(MobileSettings().data_dir)})
    if code != 200 or not body.get("ok"):
        raise SystemExit(f"owner login failed: {code}")
    raw = hdrs.get("set-cookie") or hdrs.get("Set-Cookie") or ""
    cookie = next((p.strip().split("=", 1)[1] for p in raw.split(";") if "bm_session=" in p), "")
    return {"Cookie": f"bm_session={cookie}", "X-BM-CSRF": str(body.get("csrf") or "")}


def calls(task_id):
    db = sqlite3.connect(f"file:{DB}?mode=ro", uri=True)
    try:
        return db.execute("select tool,status,approved_by,lease_id,approval_id from tool_calls where task_id=? order by id",
                          (task_id,)).fetchall()
    finally:
        db.close()


def pending_for(task_id):
    res = cc.api("GET", "/api/approvals?status=pending")
    res = res if isinstance(res, list) else res.get("approvals", [])
    return [x for x in res if x.get("task_id") == task_id]


def wait(task_id, minutes=15):
    t0 = time.time()
    while time.time() - t0 < minutes * 60:
        st = cc.api("GET", f"/api/tasks/{task_id}")["task"]["status"]
        pend = pending_for(task_id)
        if st in ("completed", "failed", "stopped", "blocked") or pend:
            return st, pend
        time.sleep(8)
    return "timeout", []


def task(agent, title, prompt):
    t = cc.api("POST", "/api/tasks", {"agent_id": agent, "run_now": True, "title": title, "prompt": prompt})
    return t.get("id") or t["task"]["id"]


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--agent", type=int, default=52)
    a = ap.parse_args(argv)
    R = {"started": datetime.now().isoformat(timespec="seconds"), "checks": {}}
    h = login()
    code, card, _ = http("GET", "/m/api/auto-full", headers=h)
    R["checks"]["card_reads"] = {"pass": code == 200, "http": code}
    for name, bad in (("refuse_users_root", "C:\\Users"), ("refuse_python", str(Path(sys.base_prefix) / "Lib"))):
        code, body, _ = http("POST", "/m/api/auto-full", {"folder": bad, "ttl_seconds": 600, "max_uses": 5}, h)
        R["checks"][name] = {"pass": code >= 400, "http": code, "reason": str(body)[:160]}
    folder = TEST_ROOT / f"autofull-owner-{time.strftime('%Y%m%d-%H%M%S')}"
    folder.mkdir(parents=True)
    code, body, _ = http("POST", "/m/api/auto-full", {"folder": str(folder), "ttl_seconds": 900, "max_uses": 50}, h)
    R["checks"]["enable"] = {"pass": code == 200, "http": code, "folder": str(folder)}

    tid = task(a.agent, "Auto Full owner proof (inside)",
               f"Рабочая папка: {folder}. Через terminal.run с cwd ровно этой папки (mode project_host) выполни: "
               f"python -c \"open('hello.txt','w').write('auto full ok')\", затем python -c \"print(open('hello.txt').read())\". "
               f"Больше ничего.")
    st, pend = wait(tid)
    terminal = [r for r in calls(tid) if r[0] == "terminal.run"]
    covered = [r for r in terminal if r[3] is not None]
    hello = folder / "hello.txt"
    R["checks"]["inside_no_approval"] = {
        "pass": st == "completed" and not pend and bool(terminal) and len(covered) == len(terminal)
                and hello.exists() and hello.read_text(encoding="utf-8") == "auto full ok",
        "task": tid, "status": st, "terminal_calls": len(terminal), "covered_by_auto_full": len(covered),
        "approved_by": sorted({str(r[2]) for r in terminal}), "pending_asks": len(pend)}
    for p in pend:
        cc.api("POST", f"/api/approvals/{p['id']}", {"approve": False, "by": "owner-proof:unexpected-ask"})

    outside = TEST_ROOT / "outside-autofull.txt"
    otid = task(a.agent, "Auto Full owner proof (outside)",
                f"Через terminal.run (mode project_host) с cwd {TEST_ROOT} выполни: "
                f"python -c \"open('outside-autofull.txt','w').write('x')\". Больше ничего.")
    st2, pend2 = wait(otid, 10)
    orows = [r for r in calls(otid) if r[0] == "terminal.run"]
    leaked = outside.exists()
    R["checks"]["outside_not_auto"] = {
        "pass": not leaked and not any(r[3] is not None and r[1] in ("executed", "ok") for r in orows),
        "task": otid, "status": st2, "asked_owner": len(pend2), "file_written_outside": leaked}
    for p in pend2:
        cc.api("POST", f"/api/approvals/{p['id']}", {"approve": False, "by": "owner-proof:refuse-outside"})
    with contextlib.suppress(Exception):
        cc.api("POST", f"/api/tasks/{otid}/stop")

    cur = (http("GET", "/m/api/auto-full", headers=h)[1] or {}).get("current") or {}
    code, _, _ = http("POST", "/m/api/auto-full/revoke", {"lease_id": cur.get("id")}, h)
    after = http("GET", "/m/api/auto-full", headers=h)[1] or {}
    t3id = task(a.agent, "Auto Full owner proof (after revoke)",
                f"Через terminal.run с cwd {folder} (mode project_host) выполни: python -c \"print(1)\". Больше ничего.")
    st3, pend3 = wait(t3id, 10)
    rows3 = [r for r in calls(t3id) if r[0] == "terminal.run"]
    R["checks"]["after_revoke_asks_again"] = {
        "pass": code == 200 and not after.get("active") and (bool(pend3) or not any(r[3] for r in rows3)),
        "revoke_http": code, "active_after": after.get("active"), "task": t3id, "asked_owner": len(pend3), "status": st3}
    for p in pend3:
        cc.api("POST", f"/api/approvals/{p['id']}", {"approve": False, "by": "owner-proof:after-revoke"})
    with contextlib.suppress(Exception):
        cc.api("POST", f"/api/tasks/{t3id}/stop")

    R["finished"] = datetime.now().isoformat(timespec="seconds")
    R["verdict"] = "PASS" if all(c["pass"] for c in R["checks"].values()) else "FAIL"
    out = ROOT / "docs" / "audits" / f"{time.strftime('%Y-%m-%d')}-auto-full-owner"
    out.mkdir(parents=True, exist_ok=True)
    (out / "receipt.json").write_text(json.dumps(R, ensure_ascii=False, indent=1), encoding="utf-8")
    print(json.dumps(R, ensure_ascii=False, indent=1))
    return 0 if R["verdict"] == "PASS" else 1


if __name__ == "__main__":
    raise SystemExit(main())
