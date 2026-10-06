"""S7: the owner STOP (`POST /api/control-plane/stop-all`, the dashboard «Остановить всё», `bossman stop --all`) sees a live Telegram call.

Skipped until the `calls` plane is in `bcc/features/control_plane.py` (a cross-lane patch; the skip is reported, never a PASS).
Without the plane the call is still ended by the `computer.stop` bus event (test_global_stop_watcher / e2e), but the owner STOP
neither lists it in «active», nor counts it in stopped / remaining, so `ok` could be claimed while a call is still ringing.
"""
from __future__ import annotations

from types import SimpleNamespace

import pytest

from bcc.features import control_plane

from .test_api_calls import PREFIX, active, ended, mgr, offline_mode, ready, status  # noqa: F401 - the autouse fixture is re-used

pytestmark = pytest.mark.skipif(not hasattr(control_plane, "_calls_inventory"),
                                reason="S7 patch (the `calls` plane of the owner STOP) is not applied in features/control_plane.py")


async def test_a_live_call_is_listed_and_stopped_by_the_owner_stop_all(env):
    await ready(env)
    assert (await env.client.post(f"{PREFIX}/call", json={})).status_code == 200
    await active(env)
    preview = (await env.client.get("/api/control-plane/active")).json()
    assert preview["active"]["calls"] == ["call"], "the live call is in the owner's inventory"

    result = (await env.client.post("/api/control-plane/stop-all")).json()
    # The bus event computer.stop (sent first by the same request) may already have hung the call up before the inventory is taken:
    # both orders are correct. What must hold: nothing of the call remains, it is never reported as an error, and it never outlives.
    assert result["remaining"]["calls"] == [] and result["stopped"]["calls"] in ([], ["call"]), result
    assert not [e for e in result["errors"] if e["plane"] == "calls"]
    st = await ended(env)
    assert st["last_call"]["outcome"] == "stopped" and st["stop"]["call"] is True
    refused = await env.client.post(f"{PREFIX}/call", json={})
    assert refused.status_code == 409 and refused.json()["error"]["code"] == "STOP_ACTIVE"


async def test_the_calls_inventory_counts_a_live_call_or_a_dial_in_flight_but_not_an_idle_worker():
    class M:
        def __init__(self, active=None, pending=False):
            self.active_call, self.dial_pending = active, pending

    def svc(manager):
        return SimpleNamespace(_calls=SimpleNamespace(manager=manager))

    assert control_plane._calls_inventory(svc(M({"call_id": "c-1"}))) == ["call"]
    assert control_plane._calls_inventory(svc(M(None, True))) == ["call"], "a dial in flight may ring at any moment"
    assert control_plane._calls_inventory(svc(M())) == [], "an idle (but connected) worker is not owner work"
    assert control_plane._calls_inventory(SimpleNamespace()) == [], "no calls runtime yet: nothing to stop"


async def test_an_idle_worker_is_not_owner_work_and_the_owner_stop_still_blocks_dialing(env):
    """Paired control: a connected but idle account is not listed (it would make `ok` impossible), STOP still sets the flag."""
    await ready(env)
    await env.client.post(f"{PREFIX}/hangup")                 # starts nothing; the worker is running after login
    assert mgr(env).running is True and mgr(env).active_call is None
    preview = (await env.client.get("/api/control-plane/active")).json()
    assert preview["active"]["calls"] == []
    result = (await env.client.post("/api/control-plane/stop-all")).json()
    assert result["stopped"]["calls"] == [] and result["remaining"]["calls"] == []
    import asyncio
    for _ in range(100):                                      # the computer.stop bus event sets the calls STOP (the watcher)
        if (await status(env))["stop"]["call"]:
            break
        await asyncio.sleep(0.1)
    assert (await status(env))["stop"]["call"] is True
