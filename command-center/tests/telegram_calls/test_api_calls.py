"""/api/telegram/calls/*: auth, empty state, guards, validation, STOP (own and global), UNKNOWN + confirm, bus events, secrets.

The worker is the REAL one in ``BOSSMAN_CALLS_MODE=offline_test`` (fake Telethon client, loopback line, scripted engines):
the whole product flow runs without Telegram. Nothing here proves a real Telegram call; every call carries transport
"loopback" and the status says ``mode: offline_test``.
"""
from __future__ import annotations

import asyncio
import json
import time
from pathlib import Path

import httpx
import pytest

from bcc.features import command_bar as cb
from bcc.telegram_calls.account.stopflag import CallState
from bcc.telegram_calls.call.offline_mode import OFFLINE_CODE, OFFLINE_PEER_ID, OFFLINE_PHONE
from bcc.tools import REGISTRY

API_ID = 1234567
API_HASH = "0123456789abcdef" * 2                          # fixture shape only, not a credential
PREFIX = "/api/telegram/calls"


@pytest.fixture(autouse=True)
def offline_mode(monkeypatch):
    monkeypatch.setenv("BOSSMAN_CALLS_MODE", "offline_test")
    monkeypatch.delenv("BOSSMAN_VAULT_KEY", raising=False)
    monkeypatch.delenv("BOSSMAN_TELEGRAM_CALLS_HOME", raising=False)


def mgr(env):
    return env.svc._calls.manager


def home(env) -> Path:
    return Path(env.settings.data_dir) / "telegram-calls"


async def connect(env) -> None:
    c = env.client
    assert (await c.post(f"{PREFIX}/credentials", json={"api_id": API_ID, "api_hash": API_HASH})).status_code == 200
    assert (await c.post(f"{PREFIX}/login/start", json={"phone": OFFLINE_PHONE})).status_code == 200
    r = await c.post(f"{PREFIX}/login/code", json={"code": OFFLINE_CODE})
    assert r.status_code == 200 and r.json()["state"] == "ready", r.text


async def ready(env, *, enable: bool = True) -> None:
    await connect(env)
    r = await env.client.put(f"{PREFIX}/peer", json={"user_id": OFFLINE_PEER_ID, "confirm": True})
    assert r.status_code == 200, r.text
    if enable:
        assert (await env.client.put(f"{PREFIX}/settings", json={"enabled": True})).status_code == 200


async def status(env) -> dict:
    return (await env.client.get(f"{PREFIX}/status")).json()


async def until(env, cond, timeout: float = 20.0) -> dict:
    end = time.monotonic() + timeout
    while time.monotonic() < end:
        st = await status(env)
        if cond(st):
            return st
        await asyncio.sleep(0.05)
    raise AssertionError("condition not reached; last status: " + json.dumps(st, ensure_ascii=False)[:600])


async def active(env) -> dict:
    return await until(env, lambda s: (s.get("call") or {}).get("state") == "active")


async def ended(env) -> dict:
    st = await until(env, lambda s: s.get("call") is None and s.get("last_call") is not None)
    await mgr(env).drain()
    return st


# ------------------------------------------------------------------ auth (same contract as the Telegram settings API)

async def test_unauthenticated_requests_are_rejected(env):
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=env.app), base_url="http://test") as anon:
        for method, path, payload in (
                ("GET", "/status", None), ("GET", "/settings", None), ("PUT", "/settings", {"enabled": True}),
                ("POST", "/credentials", {"api_id": API_ID, "api_hash": API_HASH}), ("POST", "/login/start", {"phone": "+70000000000"}),
                ("POST", "/logout", None), ("GET", "/contacts", None), ("PUT", "/peer", {"user_id": 5, "confirm": True}),
                ("DELETE", "/peer", None), ("POST", "/call", {}), ("POST", "/hangup", None), ("POST", "/stop", None),
                ("POST", "/resume", None), ("GET", "/events", None), ("GET", "/history", None),
                ("POST", "/history/c-1/save-memory", None), ("POST", "/history/c-1/draft-tasks", None),
                ("POST", "/selftest", {}), ("POST", "/doctor", None), ("GET", "/install", None), ("POST", "/install", None)):
            r = await anon.request(method, PREFIX + path, json=payload)
            assert r.status_code == 401, (method, path)
    assert not home(env).exists(), "unauthenticated calls must not create anything"
    assert mgr(env).running is False


async def test_a_browser_session_without_csrf_cannot_change_anything(env):
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=env.app), base_url="http://test") as browser:
        login = await browser.post("/api/login", json={"token": env.svc.auth.token})
        csrf = login.json()["csrf"]
        assert (await browser.put(f"{PREFIX}/settings", json={"enabled": True})).status_code == 403
        assert (await browser.post(f"{PREFIX}/stop")).status_code == 403
        ok = await browser.put(f"{PREFIX}/settings", json={"enabled": True}, headers={"X-BCC-CSRF": csrf})
        assert ok.status_code == 200 and ok.json()["enabled"] is True


# ------------------------------------------------------------------ empty state

async def test_the_empty_state_is_200_unconfigured_and_starts_nothing(env):
    c = env.client
    st = await c.get(f"{PREFIX}/status")
    assert st.status_code == 200
    body = st.json()
    assert body["enabled"] is False and body["peer"] is None and body["call"] is None and body["call_active"] is False
    assert body["account"]["state"] == "no_credentials" and body["worker"]["running"] is False
    assert body["mode"] == "offline_test" and body["test_label"] is True
    for path in ("/settings", "/history", "/events", "/install"):
        r = await c.get(PREFIX + path)
        assert r.status_code == 200, (path, r.text)
    assert (await c.get(f"{PREFIX}/history")).json() == {"items": []}
    assert (await c.get(f"{PREFIX}/settings")).json()["enabled"] is False
    assert mgr(env).running is False and not home(env).exists()


async def test_no_agent_tool_can_place_a_call(env):
    names = REGISTRY.names()
    assert not [n for n in names if "call" in n.lower() and "telegram" in n.lower()], names
    assert not [n for n in names if n.lower().startswith(("calls.", "calls_", "telegram_call"))], names


async def test_the_command_bar_lists_but_never_runs_call_routes(env, monkeypatch):
    monkeypatch.setenv("BOSSMAN_COMMAND_BAR_ENABLED", "1")
    catalog = cb.build_catalog(env.app)
    calls = [cap for cap in catalog.values() if cap.path.startswith("/api/telegram/calls")]
    assert len(calls) >= 20, "the routes must be visible in the catalog (not silently hidden)"
    assert all(cap.runnable is False and cap.blocked_reason for cap in calls)
    tasks = [cap for cap in catalog.values() if cap.path == "/api/tasks" and cap.method == "GET"]
    assert tasks and all(cap.runnable for cap in tasks), "the block must not spread to ordinary routes"
    parsed = (await env.client.post("/api/command-bar/parse", json={"text": calls[0].id})).json()
    assert parsed["intent"]["runnable"] is False
    res = await env.client.post("/api/command-bar/run", json={"intent_id": parsed["intent_id"]})
    assert res.status_code == 400


# ------------------------------------------------------------------ credentials / login

async def test_credentials_are_stored_encrypted_and_never_come_back(env):
    c = env.client
    r = await c.post(f"{PREFIX}/credentials", json={"api_id": API_ID, "api_hash": API_HASH})
    assert r.status_code == 200 and r.json()["credentials"]["has_api"] is True
    assert API_HASH not in r.text and str(API_ID) not in r.text
    blob = (home(env) / "credentials.enc").read_text(encoding="utf-8")
    assert API_HASH not in blob and str(API_ID) not in blob
    for path in ("/status", "/settings", "/history", "/events"):
        assert API_HASH not in (await c.get(PREFIX + path)).text
    assert (await status(env))["account"]["state"] == "logged_out"


@pytest.mark.parametrize("body", [
    {"api_id": 0, "api_hash": API_HASH}, {"api_id": -3, "api_hash": API_HASH}, {"api_id": "abc", "api_hash": API_HASH},
    {"api_id": API_ID, "api_hash": "short"}, {"api_id": API_ID, "api_hash": "z" * 32}, {"api_id": API_ID, "api_hash": API_HASH, "extra": 1},
    {"api_id": API_ID}, {}])
async def test_bad_credentials_are_rejected_and_nothing_is_written(env, body):
    r = await env.client.post(f"{PREFIX}/credentials", json=body)
    assert r.status_code == 422, r.text
    assert API_HASH not in r.text
    assert not (home(env) / "credentials.enc").exists()


async def test_the_login_flow_reaches_ready_and_hides_every_secret(env):
    c = env.client
    await c.post(f"{PREFIX}/credentials", json={"api_id": API_ID, "api_hash": API_HASH})
    started = await c.post(f"{PREFIX}/login/start", json={"phone": OFFLINE_PHONE})
    assert started.status_code == 200 and started.json()["state"] == "code_sent"
    mid = await status(env)
    assert mid["account"]["state"] == "code_sent" and mid["account"]["pending_phone"] == "+••••0000"
    done = await c.post(f"{PREFIX}/login/code", json={"code": OFFLINE_CODE})
    assert done.json()["state"] == "ready"
    st = await status(env)
    assert st["account"]["state"] == "ready" and st["account"]["has_session"] is True
    everything = started.text + done.text + json.dumps(st) + (await c.get(f"{PREFIX}/events")).text
    for secret in (OFFLINE_PHONE, OFFLINE_PHONE[1:], OFFLINE_CODE, API_HASH, "OFFLINE-TEST-SESSION"):
        assert secret not in everything, f"leaked {secret[:4]}…"


async def test_wrong_code_and_wrong_phone_are_owner_readable_422s(env):
    c = env.client
    await c.post(f"{PREFIX}/credentials", json={"api_id": API_ID, "api_hash": API_HASH})
    bad_phone = await c.post(f"{PREFIX}/login/start", json={"phone": "+15551234567"})
    assert bad_phone.status_code == 422 and bad_phone.json()["error"]["code"] == "LOGIN_PHONE_INVALID"
    assert bad_phone.json()["error"]["message"] and bad_phone.json()["error"]["hint"]
    assert "15551234567" not in bad_phone.text
    await c.post(f"{PREFIX}/login/start", json={"phone": OFFLINE_PHONE})
    bad_code = await c.post(f"{PREFIX}/login/code", json={"code": "99999"})
    assert bad_code.status_code == 422 and bad_code.json()["error"]["code"] == "LOGIN_CODE_INVALID" and "99999" not in bad_code.text
    assert (await status(env))["account"]["state"] == "code_sent", "a wrong code must not log anybody in"


async def test_login_without_credentials_is_a_409_not_a_crash(env):
    r = await env.client.post(f"{PREFIX}/login/start", json={"phone": OFFLINE_PHONE})
    assert r.status_code == 409 and r.json()["error"]["code"] == "NO_CREDENTIALS"


# ------------------------------------------------------------------ peer and settings

async def test_the_peer_needs_confirmation_and_is_revalidated_by_the_worker(env):
    await connect(env)
    c = env.client
    listed = (await c.get(f"{PREFIX}/contacts", params={"q": "втор"})).json()["contacts"]
    assert [x["id"] for x in listed] == [OFFLINE_PEER_ID] and set(listed[0]) == {"id", "label", "username"}
    assert (await c.get(f"{PREFIX}/contacts", params={"q": "нет-такого"})).json()["contacts"] == []
    no_confirm = await c.put(f"{PREFIX}/peer", json={"user_id": OFFLINE_PEER_ID})
    assert no_confirm.status_code == 422 and no_confirm.json()["error"]["code"] == "PEER_NOT_CONFIRMED"
    assert (await status(env))["peer"] is None
    both = await c.put(f"{PREFIX}/peer", json={"user_id": OFFLINE_PEER_ID, "username": "x", "confirm": True})
    neither = await c.put(f"{PREFIX}/peer", json={"confirm": True})
    assert both.status_code == neither.status_code == 422
    unknown = await c.put(f"{PREFIX}/peer", json={"user_id": 999, "confirm": True})
    assert unknown.status_code >= 400 and (await status(env))["peer"] is None
    ok = await c.put(f"{PREFIX}/peer", json={"username": "@offline_second", "confirm": True})
    assert ok.status_code == 200 and ok.json()["peer"]["user_id"] == OFFLINE_PEER_ID
    assert ok.json()["enabled"] is False, "choosing the peer must not switch calls on"
    assert (await status(env))["peer"]["user_id"] == OFFLINE_PEER_ID
    assert (await c.delete(f"{PREFIX}/peer")).json() == {"peer": None}
    assert (await status(env))["peer"] is None


async def test_settings_roundtrip_and_what_they_refuse(env):
    c = env.client
    r = await c.put(f"{PREFIX}/settings", json={"enabled": True, "max_call_s": 120, "auto_save_to_bossman_memory": True})
    assert r.status_code == 200 and r.json()["enabled"] is True and r.json()["max_call_s"] == 120
    got = (await c.get(f"{PREFIX}/settings")).json()
    assert got["auto_save_to_bossman_memory"] is True and "extra" not in got
    assert json.loads((home(env) / "config.json").read_text(encoding="utf-8"))["enabled"] is True
    for bad in ({"peer_user_id": 5}, {"peer_label": "x"}, {"record_audio": True}, {"enabled": "yes"}, {"echo_mode": "loud"},
                {"max_call_s": 5}, {"idle_prompt_s": 100, "idle_hangup_s": 50}, {"unknown_field": 1}):
        rejected = await c.put(f"{PREFIX}/settings", json=bad)
        assert rejected.status_code == 422, (bad, rejected.text)
    after = (await c.get(f"{PREFIX}/settings")).json()
    assert after["max_call_s"] == 120 and after["record_audio"] is False and after["peer_user_id"] is None


async def test_calls_are_off_by_default_and_dialing_needs_every_step(env):
    c = env.client
    assert (await c.post(f"{PREFIX}/call", json={})).json()["error"]["code"] == "NOT_ENABLED"
    await c.put(f"{PREFIX}/settings", json={"enabled": True})
    assert (await c.post(f"{PREFIX}/call", json={})).json()["error"]["code"] == "NO_CREDENTIALS"
    await connect(env)
    r = await c.post(f"{PREFIX}/call", json={})
    assert r.status_code == 409 and r.json()["error"]["code"] == "PEER_NOT_SELECTED"


# ------------------------------------------------------------------ the call itself

async def test_a_dial_takes_no_peer_and_unknown_fields_are_rejected(env):
    await ready(env)
    c = env.client
    for body in ({"peer": 5}, {"user_id": 5}, {"peer_user_id": 5}, {"username": "x"}, {"confirm_unknown": "maybe"}, {"phone": "+70000000001"}):
        r = await c.post(f"{PREFIX}/call", json=body)
        assert r.status_code == 422, (body, r.text)
    assert mgr(env).active_call is None and (await status(env))["call"] is None
    assert (await c.get(f"{PREFIX}/history")).json()["items"] == [], "a rejected request must never ring the phone"
    assert not [e for e in (await c.get(f"{PREFIX}/events")).json()["events"] if e["kind"] == "dial"]


async def test_a_call_runs_through_the_offline_worker_and_ends_in_history(env):
    await ready(env)
    c = env.client
    r = await c.post(f"{PREFIX}/call", json={})
    assert r.status_code == 200, r.text
    dial = r.json()
    assert dial["accepted"] is True and dial["transport"] == "loopback" and dial["call_id"].startswith("c-")
    st = await active(env)
    assert st["mode"] == "offline_test" and st["test_label"] is True and st["transport"] == "loopback"
    assert st["call"]["call_id"] == dial["call_id"] and st["call_active"] is True and st["models"]
    assert (await c.post(f"{PREFIX}/call", json={})).json()["error"]["code"] == "CALL_IN_PROGRESS"
    events = (await c.get(f"{PREFIX}/events")).json()
    assert events["events"] and [e["seq"] for e in events["events"]] == sorted(e["seq"] for e in events["events"])
    assert any(e["kind"] == "state" and e.get("state") == "active" for e in events["events"])
    hang = await c.post(f"{PREFIX}/hangup")
    assert hang.status_code == 200 and hang.json()["ended"] is True
    st = await ended(env)
    assert st["last_call"]["outcome"] == "completed" and st["last_call"]["transport"] == "loopback"
    assert st["latency"]["n"] >= 0 and st["uncertain_previous"] is False
    item = (await c.get(f"{PREFIX}/history")).json()["items"][0]
    assert item["call_id"] == dial["call_id"] and item["transport"] == "loopback" and item["recorded_audio"] is False
    assert "postcall" in item and "transcript" not in item and "audio" not in json.dumps(item).lower().replace("recorded_audio", "")


async def test_no_automatic_redial_after_any_outcome(env):
    await ready(env)
    c = env.client
    await c.post(f"{PREFIX}/call", json={})
    await active(env)
    await c.post(f"{PREFIX}/hangup")
    await ended(env)
    assert mgr(env).active_call is None
    await asyncio.sleep(1.5)
    st = await status(env)
    assert st["call"] is None and len((await c.get(f"{PREFIX}/history")).json()["items"]) == 1, "nothing may dial by itself"


async def test_stop_ends_the_call_persists_and_blocks_dialing_until_resume(env):
    await ready(env)
    c = env.client
    await c.post(f"{PREFIX}/call", json={})
    await active(env)
    stop = await c.post(f"{PREFIX}/stop")
    assert stop.status_code == 200 and stop.json()["stopped"] is True and stop.json()["persisted"] is True
    st = await ended(env)
    assert st["last_call"]["outcome"] == "stopped" and st["stop"]["call"] is True and st["call"] is None
    assert (home(env) / "STOP").is_file(), "the STOP marker is a file: it survives a restart"
    blocked = await c.post(f"{PREFIX}/call", json={})
    assert blocked.status_code == 409 and blocked.json()["error"]["code"] == "STOP_ACTIVE"
    assert CallState(home(env)).stop_is_set(), "a brand-new state object (a restart) still sees STOP"
    assert (await c.post(f"{PREFIX}/resume")).json()["stop_flag"] is False
    assert (await c.post(f"{PREFIX}/call", json={})).status_code == 200
    await c.post(f"{PREFIX}/stop")
    await ended(env)


async def test_stop_is_never_silent_even_when_nothing_is_running(env):
    r = await env.client.post(f"{PREFIX}/stop")
    assert r.status_code == 200 and r.json()["stopped"] is True and r.json()["persisted"] is True
    assert (await status(env))["stop"]["call"] is True


async def test_the_global_stop_blocks_dialing_and_ends_a_running_call(env):
    await ready(env)
    c = env.client
    assert (await c.post("/api/computer/stop")).status_code == 200
    blocked = await c.post(f"{PREFIX}/call", json={})
    assert blocked.status_code == 409 and blocked.json()["error"]["code"] == "STOP_ACTIVE"
    st = await status(env)
    assert st["stop"]["global"] is True and st["stop"]["active"] is True
    assert (await c.post("/api/computer/resume")).status_code == 200
    # the calls STOP is its own durable marker: the global STOP set it too (any surface persists), the owner clears it
    assert (await c.post(f"{PREFIX}/resume")).json()["stop_flag"] is False
    assert (await c.post(f"{PREFIX}/call", json={})).status_code == 200
    await active(env)
    assert (await c.post("/api/computer/stop")).status_code == 200
    st = await until(env, lambda s: s["call"] is None and s["last_call"] and s["last_call"]["outcome"] == "stopped")
    assert st["stop"]["call"] is True, "the bus event computer.stop must have stopped the call and set the calls STOP"
    await c.post("/api/computer/resume")
    await c.post(f"{PREFIX}/resume")


async def test_owner_stop_all_hangs_up_a_live_call_and_never_redials(env):
    """S7: the calls plane is part of the one owner STOP (dashboard, CLI and Telegram channel all call stop-all)."""
    await ready(env)
    c = env.client
    assert (await c.get("/api/control-plane/active")).json()["active"]["calls"] == []
    assert (await c.post(f"{PREFIX}/call", json={})).status_code == 200
    await active(env)
    assert (await c.get("/api/control-plane/active")).json()["active"]["calls"] == ["call"]
    body = (await c.post("/api/control-plane/stop-all")).json()
    assert body["stopped"]["calls"] == ["call"] and body["remaining"]["calls"] == [], body
    assert not [e for e in body["errors"] if e["plane"] == "calls"], body["errors"]
    st = await until(env, lambda s: s["call"] is None and s["last_call"] and s["last_call"]["outcome"] == "stopped")
    assert st["stop"]["call"] is True
    blocked = await c.post(f"{PREFIX}/call", json={})
    assert blocked.status_code == 409 and blocked.json()["error"]["code"] == "STOP_ACTIVE", "no redial after STOP"
    await c.post("/api/computer/resume")
    await c.post(f"{PREFIX}/resume")


async def test_there_is_one_api_prefix_and_no_calls_alias(env):
    assert (await env.client.get(f"{PREFIX}/status")).status_code == 200
    assert (await env.client.get("/api/calls/status")).status_code == 404


async def test_a_dead_worker_means_unknown_and_the_next_dial_needs_a_confirmation(env):
    await ready(env)
    c = env.client
    await c.post(f"{PREFIX}/call", json={})
    await active(env)
    mgr(env)._proc.kill()
    st = await until(env, lambda s: s["call"] is None and s["last_call"] is not None)
    await mgr(env).drain()
    assert st["last_call"]["outcome"] == "unknown" and st["last_call"]["synthesized"] is True and st["uncertain_previous"] is True
    assert st["last_error"]["code"] == "WORKER_UNAVAILABLE" or st["last_error"]["code"] == "UNCERTAIN_PREVIOUS_CALL"
    refused = await c.post(f"{PREFIX}/call", json={})
    assert refused.status_code == 409 and refused.json()["error"]["code"] == "UNCERTAIN_PREVIOUS_CALL"
    assert mgr(env).running is False, "the refusal must not respawn the worker and there is no automatic redial"
    accepted = await c.post(f"{PREFIX}/call", json={"confirm_unknown": True})
    assert accepted.status_code == 200, accepted.text
    await active(env)
    await c.post(f"{PREFIX}/hangup")
    await ended(env)


async def test_logout_forgets_the_peer_and_switches_calls_off(env):
    await ready(env)
    r = await env.client.post(f"{PREFIX}/logout")
    assert r.status_code == 200 and r.json()["peer_cleared"] is True
    st = await status(env)
    assert st["account"]["state"] == "logged_out" and st["peer"] is None and st["enabled"] is False
    assert (await env.client.post(f"{PREFIX}/call", json={})).json()["error"]["code"] == "NOT_ENABLED"


async def test_a_new_api_id_drops_the_session_and_the_peer(env):
    await ready(env)
    r = await env.client.post(f"{PREFIX}/credentials", json={"api_id": API_ID + 1, "api_hash": API_HASH})
    assert r.status_code == 200 and r.json()["credentials"]["has_session"] is False
    st = await status(env)
    assert st["account"]["state"] == "logged_out" and st["peer"] is None and st["enabled"] is False


# ------------------------------------------------------------------ bus events

async def test_bus_events_are_text_free_and_rate_limited(env):
    await ready(env)
    q = env.svc.bus.subscribe()
    await env.client.post(f"{PREFIX}/call", json={})
    await active(env)
    await env.client.post(f"{PREFIX}/hangup")
    await ended(env)
    seen = []
    while not q.empty():
        msg = q.get_nowait()
        if str(msg.get("kind", "")).startswith("telegram_call."):
            seen.append(msg)
    kinds = {m["kind"] for m in seen}
    assert kinds <= {"telegram_call.state", "telegram_call.ended"} and "telegram_call.ended" in kinds and "telegram_call.state" in kinds
    allowed = {"kind", "ts", "seq", "state", "phase", "call_id", "transport", "outcome", "error_code", "turns", "latency_p50_ms", "trace_id"}
    for m in seen:
        assert set(m) <= allowed, set(m) - allowed
    blob = json.dumps(seen)
    for secret in (OFFLINE_PHONE, OFFLINE_CODE, API_HASH):
        assert secret not in blob
    assert [m for m in seen if m["kind"] == "telegram_call.ended"][0]["outcome"] == "completed"


async def test_a_burst_of_state_changes_is_coalesced_to_about_five_per_second(env):
    rt = env.svc._calls
    q = env.svc.bus.subscribe()
    t0 = time.monotonic()
    for i in range(200):
        rt._on_state("phase", {"state": "active", "phase": "speaking" if i % 2 else "listening", "call_id": "c-x", "transport": "loopback"})
        await asyncio.sleep(0.002)
    await asyncio.sleep(0.5)
    got = []
    while not q.empty():
        m = q.get_nowait()
        if m.get("kind") == "telegram_call.state":
            got.append(m)
    elapsed = time.monotonic() - t0
    assert 1 <= len(got) <= elapsed * 5 + 2, (len(got), elapsed)
    assert got[-1]["phase"] in ("speaking", "listening"), "the LAST state must always be delivered"


# ------------------------------------------------------------------ checks

async def test_selftest_runs_without_telegram_and_says_so(env):
    bad = await env.client.post(f"{PREFIX}/selftest", json={"scenario": "everything"})
    assert bad.status_code == 422
    r = await env.client.post(f"{PREFIX}/selftest", json={"scenario": "no_redial"})
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["verdict"] == "PASS" and body["evidence_level"] == "loopback" and body["label"] == "ТЕСТ БЕЗ TELEGRAM"
    assert [x["scenario"] for x in body["results"]] == ["no_redial"]
    assert (await status(env))["selftest_running"] is False


async def test_selftest_is_refused_during_a_call_and_dialing_during_a_selftest(env):
    await ready(env)
    await env.client.post(f"{PREFIX}/call", json={})
    await active(env)
    r = await env.client.post(f"{PREFIX}/selftest", json={"scenario": "no_redial"})
    assert r.status_code == 409 and r.json()["error"]["code"] == "CALL_IN_PROGRESS"
    await env.client.post(f"{PREFIX}/hangup")
    await ended(env)
    mgr(env)._selftest_running = True
    try:
        blocked = await env.client.post(f"{PREFIX}/call", json={})
        assert blocked.status_code == 409 and blocked.json()["error"]["code"] == "CALL_IN_PROGRESS"
    finally:
        mgr(env)._selftest_running = False


async def test_doctor_is_local_only_and_reports_rows(env):
    r = await env.client.post(f"{PREFIX}/doctor")
    assert r.status_code == 200
    body = r.json()
    assert body["verdict"] in ("PASS", "WARN", "BLOCKED") and body["rows"]
    assert all(set(row) == {"check", "status", "detail", "remedy"} for row in body["rows"])
    assert mgr(env).running is False, "the doctor must not start the worker (no Telegram, no call)"


async def test_install_reports_progress_and_result_only_when_the_owner_starts_it(env, monkeypatch):
    from bcc.telegram_calls import addon
    started = env.client
    idle = (await started.get(f"{PREFIX}/install")).json()
    assert idle["state"] == "idle" and "complete" in idle["addon"]
    calls = []
    release = asyncio.Event()

    def fake_install(data_dir=None, *, progress=None, **kw):
        calls.append(data_dir)
        progress("download example==1.0")
        while not release_flag["go"]:
            time.sleep(0.01)
        progress("unpack example")
        return {"status": "installed", "installed": ["example==1.0"], "path": "x"}

    release_flag = {"go": False}
    monkeypatch.setattr(addon, "install", fake_install)
    r1 = await started.post(f"{PREFIX}/install")
    r2 = await started.post(f"{PREFIX}/install")
    assert r1.json()["state"] == "running" and r2.json()["state"] == "running"
    release_flag["go"] = True
    for _ in range(100):
        cur = (await started.get(f"{PREFIX}/install")).json()
        if cur["state"] != "running":
            break
        await asyncio.sleep(0.05)
    assert cur["state"] == "done" and cur["result"]["status"] == "installed" and cur["progress"][:1] == ["download example==1.0"]
    assert len(calls) == 1, "a second click while running must not start a second install"


async def test_install_failure_is_reported_with_a_stable_code(env, monkeypatch):
    from bcc.telegram_calls import addon
    from bcc.telegram_calls.types import CallError

    def boom(data_dir=None, **kw):
        raise CallError("DEPENDENCIES_MISSING", detail="sha256_mismatch:x")
    monkeypatch.setattr(addon, "install", boom)
    await env.client.post(f"{PREFIX}/install")
    for _ in range(100):
        cur = (await env.client.get(f"{PREFIX}/install")).json()
        if cur["state"] != "running":
            break
        await asyncio.sleep(0.05)
    assert cur["state"] == "error" and cur["error"]["code"] == "DEPENDENCIES_MISSING" and cur["error"]["hint"]


# ------------------------------------------------------------------ nothing secret anywhere on disk or in answers

async def test_no_secret_reaches_any_file_answer_or_event_after_a_full_flow(env):
    await ready(env)
    c = env.client
    answers = []
    for method, path in (("GET", "/status"), ("GET", "/settings"), ("GET", "/events"), ("GET", "/history"), ("GET", "/install")):
        answers.append((await c.request(method, PREFIX + path)).text)
    await c.post(f"{PREFIX}/call", json={})
    await active(env)
    answers.append((await c.get(f"{PREFIX}/status")).text)
    await c.post(f"{PREFIX}/hangup")
    await ended(env)
    answers.append((await c.get(f"{PREFIX}/history")).text)
    answers.append((await c.get(f"{PREFIX}/events")).text)
    answers.append(json.dumps((await c.post(f"{PREFIX}/doctor")).json()))
    secrets = (API_HASH, OFFLINE_PHONE, OFFLINE_PHONE[1:], OFFLINE_CODE, "OFFLINE-TEST-SESSION", str(OFFLINE_PHONE[-4:]) + OFFLINE_CODE)
    blob = "\n".join(answers)
    for s in secrets[:5]:
        assert s not in blob, f"answer leaked {s[:4]}…"
    for path in home(env).rglob("*"):
        if path.is_file() and path.name != "credentials.enc":
            text = path.read_text(encoding="utf-8", errors="replace")
            for s in secrets[:5]:
                assert s not in text, f"{path.name} leaked {s[:4]}…"
    assert (home(env) / "credentials.enc").exists()
