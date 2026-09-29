"""HTTP surface of Telegram calls: real app + real CallsManager (no worker is ever started), fakes where a worker
would be needed. No Telegram, no network."""
from __future__ import annotations

import httpx
import pytest

from bcc.features import telegram_calls as api_mod
from bcc.telegram_calls.types import CallError

from ..conftest import client_for, make_settings, start_app

BASE = "/api/telegram/calls"
SECRET_HASH = "0123456789abcdef0123456789abcdef"      # fixture shape only
SECRET_CODE = "48151"


class FakeManager:
    """Records calls; raises a chosen CallError."""

    def __init__(self, error: str | None = None):
        self.error = error
        self.calls: list[tuple] = []
        self.attached = None
        self.down = False

    def _do(self, name, *args, result=None):
        self.calls.append((name, *args))
        if self.error:
            raise CallError(self.error)
        return result if result is not None else {"ok": True}

    def attach_bus(self, bus):
        self.attached = bus

    async def shutdown(self):
        self.down = True

    def status(self): return self._do("status", result={"account": {"state": "ready"}})
    async def dial(self, confirm_unknown=False): return self._do("dial", confirm_unknown, result={"call_id": "c1", "accepted": True})
    async def hangup(self): return self._do("hangup", result={"hung_up": True})
    async def stop(self, by="owner"): return self._do("stop", by, result={"stopped": True})
    async def login_start(self, phone): return self._do("login_start", phone, result={"state": "code_sent"})
    def login_credentials(self, api_id, api_hash): return self._do("creds", api_id, api_hash, result={"state": "logged_out"})


# ---------------------------------------------------------------- empty state, auth

async def test_unconfigured_module_answers_200_everywhere_it_reads(env):
    for path in ("/status", "/settings", "/history", "/events", "/doctor"):
        r = await env.client.get(BASE + path)
        assert r.status_code == 200, (path, r.text)
    st = (await env.client.get(BASE + "/status")).json()
    assert st["account"]["state"] == "no_credentials"
    assert st["settings"]["enabled"] is False and st["call"] is None
    assert st["worker"] == {"running": False}          # a read never starts the worker
    assert (await env.client.get(BASE + "/history")).json() == {"calls": []}


async def test_router_needs_the_normal_token(env):
    bare = httpx.AsyncClient(transport=httpx.ASGITransport(app=env.app), base_url="http://test")
    async with bare:
        assert (await bare.get(BASE + "/status")).status_code == 401
        assert (await bare.post(BASE + "/stop", json={})).status_code == 401


# ---------------------------------------------------------------- settings

async def test_owner_can_toggle_and_bad_settings_are_rejected(env):
    r = await env.client.put(BASE + "/settings", json={"enabled": True, "max_call_s": 120})
    assert r.status_code == 200 and r.json()["enabled"] is True and r.json()["max_call_s"] == 120
    assert (await env.client.get(BASE + "/settings")).json()["enabled"] is True
    bad = await env.client.put(BASE + "/settings", json={"max_call_s": 5})
    assert bad.status_code == 400 and bad.json()["error"]["code"] == "SETTINGS_INVALID"
    assert (await env.client.put(BASE + "/settings", json={"peer": 1})).status_code == 422   # peer is not a setting


# ---------------------------------------------------------------- dial has no peer

async def test_dial_body_accepts_only_confirm_unknown(env):
    for extra in ({"peer": 4242}, {"user_id": 4242}, {"phone": "+79001234567"}, {"username": "x"}):
        r = await env.client.post(BASE + "/dial", json=extra)
        assert r.status_code == 422, extra
    # legit shape reaches the guard, which refuses because calls are OFF (409 with the stable code)
    r = await env.client.post(BASE + "/dial", json={"confirm_unknown": False})
    assert r.status_code == 409 and r.json()["error"]["code"] == "NOT_ENABLED"
    assert (await env.client.post(BASE + "/dial", json={})).status_code == 409


async def test_dial_is_forwarded_without_any_peer(env):
    fake = env.svc.calls_manager = FakeManager()
    r = await env.client.post(BASE + "/dial", json={"confirm_unknown": True})
    assert r.status_code == 200 and r.json() == {"call_id": "c1", "accepted": True}
    assert fake.calls == [("dial", True)]


async def test_call_errors_map_to_status_and_stable_body(env):
    for code, status in (("STOP_ACTIVE", 409), ("WORKER_UNAVAILABLE", 503), ("PEER_IS_SELF", 400), ("TELEGRAM_NETWORK", 502)):
        env.svc.calls_manager = FakeManager(error=code)
        r = await env.client.post(BASE + "/dial", json={})
        assert r.status_code == status, code
        body = r.json()["error"]
        assert body["code"] == code and body["message"] and "hint" in body


# ---------------------------------------------------------------- secrets never come back

async def test_secrets_are_forwarded_but_never_echoed(env):
    fake = env.svc.calls_manager = FakeManager()
    r = await env.client.post(BASE + "/login/credentials", json={"api_id": 123456, "api_hash": SECRET_HASH})
    assert r.status_code == 200 and SECRET_HASH not in r.text
    r = await env.client.post(BASE + "/login/start", json={"phone": "+79001234567"})
    assert "79001234567" not in r.text
    assert ("creds", 123456, SECRET_HASH) in fake.calls
    # a malformed body is refused without repeating the input
    bad = await env.client.post(BASE + "/login/code", json={"code": SECRET_CODE, "surplus": SECRET_CODE})
    assert bad.status_code == 422 and SECRET_CODE not in bad.text
    bad = await env.client.post(BASE + "/login/credentials", json={"api_id": "not-a-number", "api_hash": SECRET_HASH})
    assert bad.status_code == 422 and SECRET_HASH not in bad.text


async def test_malformed_api_pair_is_400_not_a_missing_state(env):
    r = await env.client.post(BASE + "/login/credentials", json={"api_id": 7, "api_hash": "short"})
    assert r.status_code == 400 and r.json()["error"]["code"] == "NO_CREDENTIALS" and "short" not in r.text
    ok = await env.client.post(BASE + "/login/credentials", json={"api_id": 7, "api_hash": SECRET_HASH})
    assert ok.status_code == 200 and SECRET_HASH not in ok.text
    assert (await env.client.get(BASE + "/status")).json()["account"]["state"] == "logged_out"


# ---------------------------------------------------------------- STOP / resume

async def test_stop_sets_the_flag_and_resume_clears_only_it(env):
    assert (await env.client.post(BASE + "/stop", json={})).json()["stopped"] is True
    assert (await env.client.get(BASE + "/status")).json()["stop"]["call_stop"] is True
    st = (await env.client.post(BASE + "/resume", json={})).json()
    assert st["stop"]["call_stop"] is False


async def test_resume_does_not_clear_the_global_stop(env):
    stop_file = env.settings.data_dir / "computer" / "STOP"
    stop_file.parent.mkdir(parents=True, exist_ok=True)
    stop_file.write_text("owner\n", encoding="utf-8")
    await env.client.post(BASE + "/stop", json={})
    st = (await env.client.post(BASE + "/resume", json={})).json()
    assert st["stop"] == {"call_stop": False, "global_stop": True}
    assert stop_file.is_file()


# ---------------------------------------------------------------- doctor / selftest / install

async def test_install_needs_explicit_confirm_and_calls_the_addon_installer(env, monkeypatch):
    seen = []
    monkeypatch.setattr(api_mod, "_later", lambda module, func: (lambda data_dir: seen.append((module, func)) or {"status": "PASS", "ok": True}))
    r = await env.client.post(BASE + "/install", json={})
    assert r.status_code == 400 and r.json()["error"]["code"] == "CONFIRM_REQUIRED" and not seen
    r = await env.client.post(BASE + "/install", json={"confirm": True})
    assert r.status_code == 200 and r.json()["ok"] is True and seen == [("addons", "install")]


async def test_doctor_and_selftest_delegate_and_survive_a_crash(env, monkeypatch):
    monkeypatch.setattr(api_mod, "_later", lambda module, func: (lambda data_dir: [{"id": "x", "status": "PASS"}] if func == "run_checks" else {"status": "PASS", "ok": True}))
    assert (await env.client.get(BASE + "/doctor")).json() == {"checks": [{"id": "x", "status": "PASS"}]}
    assert (await env.client.post(BASE + "/selftest", json={})).json()["ok"] is True

    def boom(data_dir):
        raise RuntimeError("secret-looking detail")
    monkeypatch.setattr(api_mod, "_later", lambda module, func: boom)
    r = await env.client.get(BASE + "/doctor")
    assert r.status_code == 200 and "secret-looking" not in r.text and r.json()["checks"][0]["status"] == "BLOCKED"
    r = await env.client.post(BASE + "/selftest", json={})
    assert r.status_code == 200 and "secret-looking" not in r.text and r.json()["ok"] is False


async def test_missing_later_module_gives_not_implemented(env, monkeypatch):
    monkeypatch.setattr(api_mod, "_later", lambda module, func: None)
    assert (await env.client.get(BASE + "/doctor")).json()["checks"][0]["code"] == "NOT_IMPLEMENTED_YET"
    assert (await env.client.post(BASE + "/selftest", json={})).json()["status"] == "NOT_IMPLEMENTED_YET"
    assert (await env.client.post(BASE + "/install", json={"confirm": True})).json()["status"] == "NOT_IMPLEMENTED_YET"


# ---------------------------------------------------------------- lifecycle

async def test_setup_attaches_the_bus_and_shutdown_runs_when_the_app_stops(tmp_path, monkeypatch):
    fake = FakeManager()
    monkeypatch.setattr(api_mod, "_build", lambda svc: setattr(svc, "calls_manager", fake) or fake)
    app, svc = await start_app(make_settings(tmp_path), start_workers=False)
    try:
        assert fake.attached is svc.bus and fake.down is False
    finally:
        await svc.stop()
    assert fake.down is True


async def test_real_manager_is_built_from_services(tmp_path):
    app, svc = await start_app(make_settings(tmp_path), start_workers=False)
    try:
        async with client_for(app, svc) as c:
            assert (await c.get(BASE + "/status")).status_code == 200
        assert svc.calls_manager.data_dir == svc.settings.data_dir
    finally:
        await svc.stop()


# ---------------------------------------------------------------- command bar + global stop reach

async def test_command_bar_refuses_every_calls_route(env):
    from bcc.features import command_bar as cb
    catalog = cb.build_catalog(env.app)
    calls = [c for c in catalog.values() if c.path.startswith(BASE)]
    assert len(calls) >= 20
    assert all(not c.runnable and "звонки" in c.blocked_reason for c in calls), [c.path for c in calls if c.runnable]
    other = [c for c in catalog.values() if c.path == "/api/health"]
    assert other and all(c.runnable for c in other)     # the block is narrow, not global
