"""The owner STOP (`bossman stop --all`, palette, Telegram) is honest about two more planes: the autonomy loop and Agentic
Rave. Both are stopped and counted in ok / remaining synchronously, inside the request - not left to an event."""
from __future__ import annotations

import asyncio
import json
import os
import time

import pytest

from bcc.autonomy import stop as stopmod
from bcc.autonomy.lease import EngineeringLease
from bcc.autonomy.service import AutonomyService
from bcc.features import autonomy as autonomy_feature
from bcc.features import control_plane as cp

from .test_autonomy_api import DIFF, GID, SHA, pinned, to_user_approval


class FakeRave:
    """Stands in for RaveService: `list()` and an awaitable `stop_all()` like the real one."""

    def __init__(self, *, stops: bool = True, boom: bool = False):
        self.status, self.stops, self.boom, self.calls = "running", stops, boom, 0

    def list(self):
        return [{"id": "rv-1", "status": self.status}, {"id": "rv-0", "status": "done"}]

    async def stop_all(self):
        self.calls += 1
        if self.boom:
            raise RuntimeError("rave exploded")
        if self.stops:
            self.status = "stopped"
        return {"ok": True, "stopped": {"rv-1": ["claude"]} if self.stops else {}}


@pytest.fixture
async def api(env):
    env.svc.autonomy = AutonomyService(env.settings.data_dir, constitution_status=pinned())
    return env


async def test_stop_all_stops_rave_inside_the_request_and_counts_it(api):
    api.svc.rave = rave = FakeRave()
    preview = (await api.client.get("/api/control-plane/active")).json()
    assert preview["active"]["rave"] == ["rv-1"] and "autonomy" in preview["active"]      # finished raves are not live
    body = (await api.client.post("/api/control-plane/stop-all")).json()
    assert rave.calls >= 1 and body["stopped"]["rave"] == ["rv-1"] and body["remaining"]["rave"] == []
    assert body["requested"]["rave"] == [] and body["ok"] is True, body


async def test_a_rave_that_keeps_running_makes_the_stop_unconfirmed(api):
    api.svc.rave = FakeRave(stops=False)
    body = (await api.client.post("/api/control-plane/stop-all")).json()
    assert body["ok"] is False and body["remaining"]["rave"] == ["rv-1"] and body["requested"]["rave"] == ["rv-1"]


async def test_a_rave_stop_failure_is_reported_not_swallowed(api):
    api.svc.rave = FakeRave(boom=True)
    body = (await api.client.post("/api/control-plane/stop-all")).json()
    assert body["ok"] is False and any(e["plane"] == "rave" and "exploded" in e["error"] for e in body["errors"])
    assert body["remaining"]["rave"] == ["rv-1"]
    assert body["computer"]["persisted"] is True                               # the durable STOP was set first


async def test_no_rave_service_yet_means_nothing_to_stop(api):
    api.svc.rave = None
    body = (await api.client.post("/api/control-plane/stop-all")).json()
    assert body["stopped"]["rave"] == [] and body["remaining"]["rave"] == [] and body["ok"] is True


async def test_stop_all_sets_the_autonomy_stop_and_waits_for_the_lease_holder_to_let_go(api):
    auto = autonomy_feature.service(api.svc)
    token = auto.lease.acquire(GID, "claude-writer:JEFF-0042-w1-claude", 600)
    released = []

    async def cycle_like():                        # the loop process: sees the AUTONOMY STOP and releases its lease
        while not stopmod.autonomy_stop_path(auto.root).exists():
            await asyncio.sleep(0.05)
        auto.lease.release(token)
        released.append(True)

    task = asyncio.create_task(cycle_like())
    try:
        assert (await api.client.get("/api/control-plane/active")).json()["active"]["autonomy"] == [GID]
        body = (await api.client.post("/api/control-plane/stop-all")).json()
    finally:
        await asyncio.wait_for(task, 10)
    assert released and body["stopped"]["autonomy"] == [GID] and body["remaining"]["autonomy"] == []
    assert body["requested"]["autonomy"] == [] and body["ok"] is True, body
    stop = json.loads(stopmod.autonomy_stop_path(auto.root).read_text(encoding="utf-8"))
    assert stop["by"] == "stop-all" and [e for e in auto.journal.entries(kind="autonomy.stop")]


async def test_a_lease_holder_that_does_not_stop_is_reported_as_remaining(api, monkeypatch):
    monkeypatch.setattr(cp, "AUTONOMY_SETTLE_S", 0.5)
    auto = autonomy_feature.service(api.svc)
    token = auto.lease.acquire(GID, "codex-writer:stuck", 600)
    try:
        body = (await api.client.post("/api/control-plane/stop-all")).json()
    finally:
        auto.lease.release(token)
    assert body["ok"] is False and body["remaining"]["autonomy"] == [GID] and body["requested"]["autonomy"] == [GID]


async def test_a_dead_lease_holder_is_not_live_work(api, tmp_path):
    auto = autonomy_feature.service(api.svc)
    EngineeringLease(auto.root, pid=2_000_000_000).acquire(GID, "gone", 600)        # holder pid does not exist
    active = (await api.client.get("/api/control-plane/active")).json()["active"]
    assert active["autonomy"] == []                                                # negative control: stale != live


async def test_a_running_supervisor_heartbeat_is_live_autonomy_work(api):
    auto = autonomy_feature.service(api.svc)
    hb = auto.root / "heartbeat.json"
    hb.write_text(json.dumps({"pid": os.getpid(), "status": "RUNNING", "at": time.time()}), encoding="utf-8")
    assert (await api.client.get("/api/control-plane/active")).json()["active"]["autonomy"] == [f"supervisor:{os.getpid()}"]
    hb.write_text(json.dumps({"pid": os.getpid(), "status": "FINISHED", "at": time.time()}), encoding="utf-8")
    assert (await api.client.get("/api/control-plane/active")).json()["active"]["autonomy"] == []
    hb.write_text(json.dumps({"pid": os.getpid(), "status": "RUNNING", "at": time.time() - 600}), encoding="utf-8")
    assert (await api.client.get("/api/control-plane/active")).json()["active"]["autonomy"] == []    # stale heartbeat


# ------------------------------------------------------------------ /api/autonomy/stop|resume and the bus events


def drain(queue: asyncio.Queue) -> list[dict]:
    out = []
    while not queue.empty():
        out.append(queue.get_nowait())
    return out


async def test_autonomy_stop_and_resume_endpoints_persist_journal_and_publish_events(api):
    queue = api.svc.bus.subscribe()
    r = (await api.client.post("/api/autonomy/stop", json={"reason": "owner panel"})).json()
    auto = autonomy_feature.service(api.svc)
    assert r["state"]["active"] is True and r["stop"]["by"] == "owner-api" and r["stop"]["reason"] == "owner panel"
    assert stopmod.stop_reason(auto.root, api.settings.data_dir)
    st = (await api.client.get("/api/autonomy/status")).json()
    assert st["loop"] == "STOPPED" and st["stop"]["active"] is True and "owner panel" in st["reason"]
    r = (await api.client.post("/api/autonomy/resume")).json()
    assert r["cleared"] is True and r["state"]["active"] is False and r["note"] == ""
    events = [e for e in drain(queue) if e["kind"] == "autonomy.stop"]
    assert [e["active"] for e in events] == [True, False] and all(e["by"] == "owner-api" for e in events)
    assert (await api.client.get("/api/autonomy/status")).json()["loop"] == "READY"


async def test_autonomy_resume_does_not_clear_the_owners_global_stop(api):
    (api.settings.data_dir / "computer").mkdir(parents=True, exist_ok=True)
    (api.settings.data_dir / "computer" / "STOP").write_text("{}", encoding="utf-8")
    r = (await api.client.post("/api/autonomy/resume")).json()
    assert r["cleared"] is False and r["state"]["active"] is True and "global STOP" in r["note"]
    assert (await api.client.get("/api/autonomy/status")).json()["loop"] == "STOPPED"


async def test_release_decisions_are_published_on_the_bus(api):
    to_user_approval(api.svc.autonomy)
    queue = api.svc.bus.subscribe()
    await api.client.post(f"/api/autonomy/goals/{GID}/apply", json={"sha": SHA, "diff_sha256": DIFF})
    await api.client.post(f"/api/autonomy/goals/{GID}/confirm", json={"sha": SHA, "diff_sha256": DIFF})
    decisions = [e for e in drain(queue) if e["kind"] == "autonomy.goal.decision"]
    assert [(e["decision"], e["goal_id"]) for e in decisions] == [("apply", GID), ("confirm", GID)]
    assert decisions[-1]["state"] == "DEPLOYED"
    # a refused decision publishes nothing
    queue = api.svc.bus.subscribe()
    assert (await api.client.post(f"/api/autonomy/goals/{GID}/reject", json={})).status_code == 409
    assert [e for e in drain(queue) if e["kind"] == "autonomy.goal.decision"] == []


async def test_autonomy_status_carries_mode_budget_promotion_and_weights_labels(api):
    st = (await api.client.get("/api/autonomy/status")).json()
    assert st["mode"]["autonomous_apply"] == "OFF" and st["mode"]["max_level"] == "L2"
    assert st["weights"] == "WEIGHTS_UNCHANGED" and st["learning_kind"] == "retrieval_context"
    assert st["budget"]["limits"]["usd_per_day"] == 0.0 and st["promotion"]["eligible"] is False
    assert st["stop"] == {"active": False, "sources": {}, "reason": ""}


async def test_evolution_refuses_to_start_while_the_owner_stop_is_set(api):
    (api.settings.data_dir / "computer").mkdir(parents=True, exist_ok=True)
    (api.settings.data_dir / "computer" / "STOP").write_text("{}", encoding="utf-8")
    r = await api.client.post("/api/evolution/start", json={})
    assert r.status_code == 409 and "OWNER_STOP_ACTIVE" in r.text
    r = await api.client.post("/api/evolution/resume")
    assert r.status_code in (404, 409)                                               # no campaign, or the STOP
