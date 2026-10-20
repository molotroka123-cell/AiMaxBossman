"""Telegram Calls in Bossman Mobile (/m/api/calls/* + #view-calls UI): auth, CSRF, settings, peer, dial, stop, doctor, selftest."""
from __future__ import annotations

from pathlib import Path


def test_calls_endpoints_require_session_and_csrf(env):
    assert env.c.get("/m/api/calls/status").status_code == 401
    assert env.c.post("/m/api/calls/call", json={}).status_code == 401
    env.login()
    assert env.c.post("/m/api/calls/call", json={}).status_code == 403


def test_calls_setup_and_control_flow(authed):
    st = authed.c.get("/m/api/calls/status").json()
    assert st["mode"] == "telegram" and st["account"]["state"] == "ready"
    assert st["peer"]["user_id"] == 7459432800

    # Settings update
    put_st = authed.c.put(
        "/m/api/calls/settings",
        headers={"X-BM-CSRF": authed.csrf},
        json={"enabled": True, "barge_in": True, "answering_machine": True, "answer_ring_delay_s": 15},
    )
    assert put_st.status_code == 200 and put_st.json()["answer_ring_delay_s"] == 15

    # Credentials + login steps
    cred = authed.post("/m/api/calls/credentials", json={"api_id": 123456, "api_hash": "a" * 32})
    assert cred.status_code == 200 and cred.json()["ok"] is True
    assert authed.post("/m/api/calls/login/start", json={"phone": "+420123456789"}).status_code == 200
    assert authed.post("/m/api/calls/login/code", json={"code": "12345"}).status_code == 200
    assert authed.post("/m/api/calls/login/password", json={"password": "secret-2fa"}).status_code == 200

    # Peer selection and clear
    peer = authed.c.put(
        "/m/api/calls/peer",
        headers={"X-BM-CSRF": authed.csrf},
        json={"username": "bushidoprague", "confirm": True},
    )
    assert peer.status_code == 200 and peer.json()["enabled"] is True
    assert authed.c.delete("/m/api/calls/peer", headers={"X-BM-CSRF": authed.csrf}).status_code == 200

    # Dial, hangup, stop (also triggers global worker stop), resume
    dial = authed.post("/m/api/calls/call", json={"confirm_unknown": True})
    assert dial.status_code == 200 and dial.json()["accepted"] is True
    assert authed.post("/m/api/calls/hangup", json={}).json()["ended"] is True
    stop = authed.post("/m/api/calls/stop", json={})
    assert stop.status_code == 200 and stop.json()["stopped"] is True
    assert authed.c.get("/m/api/workers").json()["global_stop"] is True
    assert authed.post("/m/api/calls/resume", json={}).json()["stop_flag"] is False

    # Answering machine, doctor, selftest, history
    assert authed.post("/m/api/calls/answering/start", json={}).json()["answering"]["listening"] is True
    assert authed.post("/m/api/calls/answering/stop", json={}).json()["answering"]["armed"] is False
    assert len(authed.c.get("/m/api/calls/answering/reports").json()["items"]) == 1
    assert len(authed.c.get("/m/api/calls/history").json()["items"]) == 1
    assert authed.post("/m/api/calls/selftest", json={"scenario": "all"}).json()["passed"] is True
    assert authed.post("/m/api/calls/doctor", json={}).json()["verdict"] == "PASS"


def test_calls_ui_present_in_shell():
    ui_dir = Path(__file__).resolve().parents[1] / "bossmobile" / "ui"
    html = (ui_dir / "index.html").read_text(encoding="utf-8")
    js = (ui_dir / "app.js").read_text(encoding="utf-8")
    assert 'id="view-calls"' in html and 'data-tab="calls"' in html
    assert 'id="calls-dial-btn"' in html and 'id="calls-settings-form"' in html
    assert "loadCallsView" in js and "innerHTML" not in js
