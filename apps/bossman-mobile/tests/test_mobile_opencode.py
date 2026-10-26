"""Tests for Bossman Mobile OpenCode Agent & Web Terminal gateway routes (`/m/api/agent/*`, `/m/api/terminal/*`) and UI."""
from __future__ import annotations

import pathlib


def test_mobile_agent_and_terminal_routes(authed):
    # 1. Tool registry & Model Router catalog
    r_tools = authed.c.get("/m/api/agent/tools")
    assert r_tools.status_code == 200
    assert "Computer" in r_tools.json()["groups"]

    r_models = authed.c.get("/m/api/agent/models")
    assert r_models.status_code == 200
    assert r_models.json()["runpod_policy"] == "DENY_WITHOUT_EXPLICIT_OWNER_APPROVAL"

    r_sel = authed.post("/m/api/agent/router/select", json={"mode": "CODE"})
    assert r_sel.status_code == 200
    assert r_sel.json()["tier"] == 1

    # 2. Agent sessions & messages
    r_create = authed.post("/m/api/agent/sessions", json={"title": "Phone OpenCode", "mode": "CODE"})
    assert r_create.status_code == 201
    assert r_create.json()["id"] == "S1"

    r_list = authed.c.get("/m/api/agent/sessions")
    assert r_list.status_code == 200
    assert r_list.json()["total"] == 1

    r_msg = authed.post("/m/api/agent/sessions/S1/messages", json={"text": "Run pytest", "mode": "CODE", "agent_id": 1})
    assert r_msg.status_code == 200
    assert r_msg.json()["task_id"] == 7

    r_ev = authed.c.get("/m/api/agent/sessions/S1/events?after=0")
    assert r_ev.status_code == 200
    assert len(r_ev.json()["events"]) == 1

    r_stop = authed.post("/m/api/agent/sessions/S1/stop", json={})
    assert r_stop.status_code == 200
    assert r_stop.json()["ok"] is True

    r_app = authed.post("/m/api/agent/tasks/7/approve", json={"decision": "approve"})
    assert r_app.status_code == 200
    assert r_app.json()["ok"] is True

    # 3. Controlled Self-Improvement (/improve)
    r_imp = authed.post("/m/api/agent/improve", json={"problem": "Fix mobile terminal scroll"})
    assert r_imp.status_code == 200
    assert len(r_imp.json()["steps"]) == 16

    r_imp_list = authed.c.get("/m/api/agent/improve")
    assert r_imp_list.status_code == 200
    assert len(r_imp_list.json()["items"]) == 1

    # 4. Interactive Web Terminal routes
    assert authed.c.get("/m/api/terminal/capabilities").status_code == 200
    assert authed.c.get("/m/api/terminal/roots").status_code == 200
    r_trun = authed.post("/m/api/terminal/run", json={"command": "git status", "mode": "project_host"})
    assert r_trun.status_code == 200
    assert r_trun.json()["session_id"] == "term-01"

    assert authed.c.get("/m/api/terminal/sessions").status_code == 200
    assert authed.c.get("/m/api/terminal/sessions/term-01").status_code == 200
    assert authed.c.get("/m/api/terminal/sessions/term-01/log").status_code == 200
    assert authed.post("/m/api/terminal/sessions/term-01/stdin", json={"text": "y\n"}).status_code == 200
    assert authed.post("/m/api/terminal/sessions/term-01/sigint", json={}).status_code == 200
    assert authed.post("/m/api/terminal/sessions/term-01/kill", json={}).status_code == 200
    assert authed.c.get("/m/api/terminal/files").status_code == 200
    assert authed.c.get("/m/api/terminal/diff").status_code == 200


def test_mobile_ui_contains_opencode_mode_bar_and_terminal_workbench():
    ui_dir = pathlib.Path(__file__).resolve().parents[1] / "bossmobile" / "ui"
    html = (ui_dir / "index.html").read_text(encoding="utf-8")
    js = (ui_dir / "app.js").read_text(encoding="utf-8")

    # Verify 6 OpenCode modes (including AUTO Bypass Permission) and Web Terminal workbench controls in index.html
    for mode in (
        'data-mode="CHAT"',
        'data-mode="AGENT"',
        'data-mode="AUTO"',
        'data-mode="CODE"',
        'data-mode="COMPUTER"',
        'data-mode="SELF-IMPROVE"',
    ):
        assert mode in html, f"Missing {mode} in index.html"
    assert 'id="oc-workbench"' in html
    assert 'id="oc-term-out"' in html
    assert 'id="btn-term-sigint"' in html
    for pc_btn in ('id="btn-pc-observe"', 'id="btn-pc-windows"', 'id="btn-pc-sysinfo"', 'id="btn-pc-status"', 'id="btn-pc-hibernate"'):
        assert pc_btn in html, f"Missing {pc_btn} in index.html"

    # Verify security invariant: no innerHTML in app.js
    assert "innerHTML" not in js
