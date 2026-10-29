"""Parity audit 24.10 (docs/audits/2026-10-24-mobile-parity): phone functions that existed only in the full panel.

* global STOP from the phone = the dashboard's owner STOP (/api/control-plane/stop-all), preview = /control-plane/active
* Studio orders, read-only summary (no briefs / notes / client data on the phone)
* niche research run status, read-only counters straight from its SQLite (mode=ro)
* /m/api/coding-tasks/status was BROKEN (Bossman has no such path -> 404); now counts the task list
Every positive test has a negative control (no session, no CSRF, missing confirm, bad id, absent run).
"""
from __future__ import annotations

import json
import sqlite3
import sys
import time
from pathlib import Path

import httpx
import pytest

from conftest import SID, Env, FakeBossman

REPO = Path(__file__).resolve().parents[3]
OID = "0123456789abcdef0123456789abcdef"
CLIENT = "f" * 64
ORDER = {"order_id": OID, "status": "quoted", "participant": "owner", "brief": "SECRET-BRIEF", "note": "SECRET-NOTE",
         "items": [{"kind": "photo", "job_ids": ["a" * 32], "price": "40"}],
         "receipt": {"currency": "CZK", "price": "40", "cost": "1.20", "margin": "38.80", "cost_complete": True},
         "licence_warnings": [{"model": "x", "status": "unknown"}], "created_at": "2026-10-24T10:00:00Z",
         "updated_at": "2026-10-24T11:00:00Z", "timeline": [{"status": "quoted", "at": "2026-10-24T10:00:00Z"}]}
ACTIVE = {"active": {"tasks": [7, 8], "terminal": [], "coding": ["c1"], "command_bar": []}, "count": 3,
          "errors": []}
STOPPED = {"ok": True, "stopped": {"tasks": [7, 8], "terminal": []}, "requested": {"coding": ["c1"]},
           "remaining": {"tasks": []}, "errors": [], "provider_outcome_unknown": []}
CODING = {"items": [{"id": "c1", "status": "running"}, {"id": "c2", "status": "completed"},
                    {"id": "c3", "status": "failed"}, {"id": "c4", "status": "queued"}]}


@pytest.fixture
def penv(tmp_path, monkeypatch):
    orig = FakeBossman.handler

    def handler(self, request: httpx.Request) -> httpx.Response:
        path, method = request.url.path, request.method
        extra = {("GET", "/api/control-plane/active"): ACTIVE, ("POST", "/api/control-plane/stop-all"): STOPPED,
                 ("GET", "/api/studio/orders"): {"orders": [ORDER]}, ("GET", f"/api/studio/orders/{OID}"): ORDER,
                 ("POST", f"/api/studio/orders/{OID}/approve"): {**ORDER, "status": "approved"},
                 ("POST", f"/api/studio/orders/{OID}/rework"): {**ORDER, "status": "rework"},
                 ("GET", "/api/coding-tasks"): CODING}
        if (method, path) not in extra:
            return orig(self, request)
        self.calls.append({"method": method, "path": path, "query": dict(request.url.params),
                           "body": request.content.decode() if request.content else ""})
        if f"bcc_session={SID}" not in request.headers.get("cookie", ""):
            return httpx.Response(401, json={"error": {"message": "нужна аутентификация"}})
        return httpx.Response(200, json=extra[(method, path)])

    monkeypatch.setattr(FakeBossman, "handler", handler)
    e = Env(tmp_path)
    e.login()
    return e


def _calls(env, path):
    return [c for c in env.fake.calls if c["path"] == path]


# ------------------------------------------------------------------ global STOP
def test_stop_active_preview_counts_only(penv):
    r = penv.c.get("/m/api/stop/active")
    assert r.status_code == 200
    b = r.json()
    assert b["count"] == 3 and b["planes"] == {"tasks": 2, "terminal": 0, "coding": 1, "command_bar": 0}
    assert b["stop"]["active"] is False and "7" not in json.dumps(b["planes"])


def test_stop_all_goes_to_the_dashboard_stop_and_sets_worker_stop(penv):
    r = penv.post("/m/api/stop", json={"confirm": True})
    assert r.status_code == 200, r.text
    b = r.json()
    assert b["ok"] is True and b["stopped"] == {"tasks": 2, "terminal": 0} and b["requested"] == {"coding": 1}
    assert len(_calls(penv, "/api/control-plane/stop-all")) == 1
    assert penv.app.state.workers.global_stop is True


@pytest.mark.parametrize("body", [{}, {"confirm": False}, {"confirm": "yes"}, {"confirm": True, "extra": 1}])
def test_stop_all_refused_without_explicit_confirm(penv, body):
    assert penv.post("/m/api/stop", json=body).status_code == 422
    assert not _calls(penv, "/api/control-plane/stop-all")
    assert penv.app.state.workers.global_stop is False


def test_stop_all_needs_session_and_csrf(env, penv):
    assert env.c.get("/m/api/stop/active").status_code == 401
    assert env.c.post("/m/api/stop", json={"confirm": True}).status_code == 401
    assert penv.c.post("/m/api/stop", json={"confirm": True}).status_code == 403      # session, no CSRF header
    assert not _calls(penv, "/api/control-plane/stop-all")


# ------------------------------------------------------------------ approvals list (the endpoint the new card uses)
def test_approvals_list_and_decide(authed):
    items = authed.c.get("/m/api/approvals").json()["items"]
    assert items == [{"id": 9, "kind": "tool", "preview": "computer.act click", "task_id": 7, "run_id": None,
                      "status": "pending", "created_at": None}]                  # «secret» field is not forwarded
    r = authed.post("/m/api/approvals/9", json={"approve": False})
    assert r.status_code == 200 and r.json()["status"] == "rejected"
    sent = [c for c in authed.fake.calls if c["path"] == "/api/approvals/9"]
    assert json.loads(sent[0]["body"]) == {"approve": False, "by": "owner"}


def test_approvals_need_session(env):
    assert env.c.get("/m/api/approvals").status_code == 401
    assert env.c.post("/m/api/approvals/9", json={"approve": True}).status_code == 401


def test_approvals_decide_negative(authed):
    assert authed.c.post("/m/api/approvals/9", json={"approve": True}).status_code == 403   # no CSRF
    assert authed.post("/m/api/approvals/9", json={}).status_code == 422                    # no decision
    assert not [c for c in authed.fake.calls if c["path"] == "/api/approvals/9"]


# ------------------------------------------------------------------ Studio orders
def test_studio_orders_summary_without_brief(penv):
    r = penv.c.get("/m/api/studio/orders")
    assert r.status_code == 200
    b = r.json()
    assert b["total"] == 1 and b["by_status"] == {"quoted": 1}
    o = b["orders"][0]
    assert o["order_id"] == OID and o["price"] == "40" and o["margin"] == "38.80" and o["items"] == 1
    assert o["licence_warnings"] == 1
    assert "SECRET" not in r.text and "brief" not in o and "participant" not in o
    one = penv.c.get(f"/m/api/studio/orders/{OID}")
    assert one.status_code == 200 and one.json()["status"] == "quoted" and "SECRET" not in one.text


def test_studio_orders_client_param_is_validated(penv):
    assert penv.c.get("/m/api/studio/orders", params={"client": CLIENT}).status_code == 200
    assert _calls(penv, "/api/studio/orders")[-1]["query"] == {"client": CLIENT}
    n = len(_calls(penv, "/api/studio/orders"))
    for bad in ("owner", "../x", "F" * 64, "f" * 63):
        assert penv.c.get("/m/api/studio/orders", params={"client": bad}).status_code == 422
    assert penv.c.get("/m/api/studio/orders/not-an-id").status_code == 404
    assert len(_calls(penv, "/api/studio/orders")) == n                       # nothing invalid reached Bossman


def test_studio_orders_need_session(env):
    assert env.c.get("/m/api/studio/orders").status_code == 401
    assert env.c.get(f"/m/api/studio/orders/{OID}").status_code == 401


def test_studio_order_mutations_are_not_on_the_phone_api(penv):
    for sub in ("quote", "accept", "start", "deliver", "cancel"):
        assert penv.post(f"/m/api/studio/orders/{OID}/{sub}").status_code in (404, 405)


def test_studio_order_preview_approve_and_rework(penv):
    prev = penv.c.get(f"/m/api/studio/orders/{OID}/preview")
    assert prev.status_code == 200 and prev.json()["cost"] == "1.20" and "SECRET" not in prev.text
    ap = penv.post(f"/m/api/studio/orders/{OID}/approve", json={})
    assert ap.status_code == 200 and ap.json()["status"] == "approved"
    assert json.loads(_calls(penv, f"/api/studio/orders/{OID}/approve")[-1]["body"]) == {"answer": "yes"}
    rw = penv.post(f"/m/api/studio/orders/{OID}/rework", json={"reason": "fix lighting"})
    assert rw.status_code == 200 and rw.json()["status"] == "rework"
    assert json.loads(_calls(penv, f"/api/studio/orders/{OID}/rework")[-1]["body"]) == {"reason": "fix lighting"}



# ------------------------------------------------------------------ niche research
def _niche_store(dirpath: Path):
    sys.path.insert(0, str(REPO))
    store_mod = pytest.importorskip("niche_research.store")
    s = store_mod.Store(dirpath / "niche_research.sqlite3")
    key = s.issue_orchestrator_key()
    now = time.time()
    s.set_meta("start_ts", now - 2.5 * 86400)
    for i, role in enumerate(("scout", "analyst", "critic")):
        s.enqueue(key, task_key=f"d01:{role}", day=1, week=1, niche="SECRET-NICHE", role=role, payload={})
    t = s.claim_next("w", now, 60)
    s.finish_task(t["id"], now)
    s.log_spend(provider="7api", model="glm", tokens_in=10, tokens_out=10, cost_usd=0.25, paid=True, ok=True, now=now)
    s.log_spend(provider="openrouter", model="free", tokens_in=1, tokens_out=1, cost_usd=0.0, paid=False, ok=True, now=now)
    s.log_error("fetch", "SECRET-ERROR-TEXT", now=now)
    (dirpath / "config.json").write_text(json.dumps({"paid_usd_limit": 5}), encoding="utf-8")
    return s


def test_niche_status_counters_from_the_run_db(penv):
    d = penv.app.state.settings.niche_dir
    d.mkdir(parents=True)
    s = _niche_store(d)
    try:
        r = penv.c.get("/m/api/niche/status")
    finally:
        s.close()
    assert r.status_code == 200
    b = r.json()
    assert b["present"] and b["readable"] and b["started"] and b["day"] == 3 and b["run_days"] == 28
    assert b["queue"] == {"pending": 2, "running": 0, "done": 1, "failed": 0}
    assert b["paid_spend_usd"] == 0.25 and b["errors"] == 1 and b["stopped"] is None
    assert b["paid_daily_limit_usd"] == 2.0                     # config 5 is clamped to the owner's $2/day cap
    assert "SECRET" not in r.text


def test_niche_status_read_only_after_close_and_absent_run(penv):
    d = penv.app.state.settings.niche_dir
    assert penv.c.get("/m/api/niche/status").json() == {"present": False, "started": False,
                                                       "reason": "исследование ниш на этом ПК не запускалось"}
    assert not d.exists(), "status must not create the run directory"
    d.mkdir(parents=True)
    _niche_store(d).close()
    db = d / "niche_research.sqlite3"
    before = db.stat().st_mtime_ns, db.stat().st_size
    b = penv.c.get("/m/api/niche/status").json()
    assert b["readable"] is True and b["queue"]["done"] == 1
    assert (db.stat().st_mtime_ns, db.stat().st_size) == before
    with sqlite3.connect(db) as conn:                           # the mobile status never wrote a row
        assert conn.execute("SELECT COUNT(*) FROM run_log").fetchone()[0] == 0


def test_niche_status_needs_session(env):
    assert env.c.get("/m/api/niche/status").status_code == 401


# ------------------------------------------------------------------ coding tasks status (was 404)
def test_coding_status_counts_the_list(penv):
    r = penv.c.get("/m/api/coding-tasks/status")
    assert r.status_code == 200, r.text
    assert r.json() == {"total": 4, "active": 2, "by_status": {"running": 1, "completed": 1, "failed": 1, "queued": 1}}
    assert _calls(penv, "/api/coding-tasks") and not _calls(penv, "/api/coding-tasks/status")
    assert not [c for c in penv.fake.calls if "readiness" in c["path"]]       # never the model-loading probe


def test_coding_status_needs_session(env):
    assert env.c.get("/m/api/coding-tasks/status").status_code == 401


def test_approve_all_button_confirms_and_decides_each_approval_separately():
    """Owner 29.10 «кнопку принять все»: one confirm, then the SAME per-approval owner endpoint for every item
    (no bulk endpoint that could skip the per-payload binding), failures reported."""
    from pathlib import Path
    js = (Path(__file__).resolve().parents[1] / "bossmobile" / "ui" / "app.js").read_text(encoding="utf-8")
    start = js.index("approveAll = btn(")
    block = js[start:start + 900]
    assert "Разрешить все (" in block and "window.confirm(" in block
    assert "for (const a of items)" in block and "api('POST', `/approvals/${a.id}`, { approve: true })" in block
    assert "не прошли" in block
    assert "/approvals/all" not in js and "approvals/bulk" not in js


def test_auto_mode_never_auto_approves_or_bypasses_for_a_cloud_agent():
    """Owner 29.10 screenshot: AUTO stopped asking while a cloud model (DeepSeek, PC access) was selected."""
    from pathlib import Path
    js = (Path(__file__).resolve().parents[1] / "bossmobile" / "ui" / "app.js").read_text(encoding="utf-8")
    assert "const curAgentCloud = () => !!(agentOf(S.agentId) || {}).cloud;" in js
    assert "!cloud && !curAgentCloud()) decide(true, true)" in js
    assert "bypass_permissions: S.agentMode === 'AUTO'," not in js and "bypass_permissions: S.agentMode === 'AUTO' }" not in js
    assert js.count("bypass_permissions: S.agentMode === 'AUTO' && !curAgentCloud()") >= 2


def test_approval_card_offers_dont_ask_again_in_this_chat_except_dangerous():
    """Owner 29.10: «Сделай так же варианты. В чате больше не спрашивать за текущую операцию»."""
    import re
    import shutil
    import subprocess
    from pathlib import Path
    import pytest
    js = (Path(__file__).resolve().parents[1] / "bossmobile" / "ui" / "app.js").read_text(encoding="utf-8")
    assert "'Разрешить один раз'" in js and "Не спрашивать в этом чате: " in js
    assert "if (sticky && chatAllowed(op)) { decide(true, 'chat'); return; }" in js
    assert "if (remember) chatAllow(op);" in js
    node = shutil.which("node")
    if not node:
        pytest.skip("node not installed")
    helpers = "\n".join(re.search(p, js, re.S).group(0) for p in (
        r"const flat = .*?\n", r"function approvalOp\(ev\) \{.*?\n  \}\n", r"function hardAsk\(ev\) .*?\n",
        r"const chatAllowKey = .*?\n", r"function chatAllow\(op\) .*?\n", r"function chatAllowed\(op\) .*?\n"))
    script = "const S = {threadId: 7};\n" + helpers + r"""
const ev = {approval_kind: 'tool', preview: 'Агент «X» хочет выполнить terminal.run\nnpm test'};
const op = approvalOp(ev);
const out = [op, chatAllowed(op)];
chatAllow(op); out.push(chatAllowed(op));
out.push(chatAllowed(approvalOp({approval_kind: 'tool', preview: 'хочет выполнить code.write'})));
S.threadId = 8; out.push(chatAllowed(op));
out.push(hardAsk({preview: 'деструктивная команда: доступ к кошельку'}), hardAsk(ev));
console.log(JSON.stringify(out));
"""
    res = subprocess.run([node, "-e", script], capture_output=True, text=True, encoding="utf-8", timeout=30)
    assert res.returncode == 0, res.stderr
    assert json_loads(res.stdout) == ["tool|terminal.run", False, True, False, False, True, False]


def json_loads(s):
    import json
    return json.loads(s.strip())
