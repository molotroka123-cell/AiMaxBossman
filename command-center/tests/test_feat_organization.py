"""ORG-01 (TZ-04 §2.1): Organization Layer как фича Command Center за флагами.
Приёмка §6.1–6.2: выключено по умолчанию; при включении — маршруты и снимок == store."""
from __future__ import annotations

import pytest

from .conftest import client_for, make_settings, start_app


async def test_org_feature_off_by_default(env, monkeypatch):
    monkeypatch.delenv("BOSSMAN_V3_ENABLED", raising=False)
    assert env.svc.organization is None
    r = await env.client.get("/api/org/snapshot")
    assert r.status_code == 503 and "disabled" in r.json()["error"]["message"]
    for path, body in (("/api/org/missions", {"department_id": "x", "goal": "y"}),
                       ("/api/org/departments", {"department_id": "x"}), ("/api/org/agents", {"agent_id": "a"})):
        r = await env.client.post(path, json=body)
        assert r.status_code == 503, path            # и без bossman_v3 рядом: 503 до импорта ядра


@pytest.fixture
async def org_env(tmp_path, monkeypatch):
    pytest.importorskip("bossman_v3", reason="bossman-core не установлен рядом с Command Center")
    monkeypatch.setenv("BOSSMAN_V3_ENABLED", "1")
    monkeypatch.setenv("BOSSMAN_V3_ORGANIZATION", "1")
    monkeypatch.setenv("BOSSMAN_EVIDENCE_KEY_FILE", str(tmp_path / "keys" / "evidence.key"))
    settings = make_settings(tmp_path)
    app, svc = await start_app(settings, start_workers=False)
    async with client_for(app, svc) as client:
        yield app, svc, client
    await svc.stop()


async def test_org_routes_when_enabled_and_snapshot_matches_store(org_env, tmp_path):
    app, svc, client = org_env
    assert svc.organization is not None and (tmp_path / "data" / "organization").exists() or svc.organization.root.exists()

    r = await client.post("/api/org/departments", json={
        "department_id": "engineering", "purpose": "код", "capabilities": ["terminal.run"],
        "budget": {"usd": 5, "compute_seconds": 600}})
    assert r.status_code == 200 and r.json()["department_id"] == "engineering"
    r = await client.post("/api/org/agents", json={
        "agent_id": "coder", "department_id": "engineering", "roles": ["executor"], "capabilities": ["terminal.run"],
        "tier": "local_small", "model": "glm"})
    assert r.status_code == 200
    r = await client.post("/api/org/agents", json={"agent_id": "x", "department_id": "nope", "roles": [], "capabilities": []})
    assert r.status_code == 400

    # миссия из цели без плана: организация не выдумывает шагов → BLOCKED/no_executable_steps + approval владельцу
    r = await client.post("/api/org/missions", json={"mission_id": "m1", "title": "t", "department_id": "engineering",
                                                     "goal": "сделай всё красиво", "required_capability": "terminal.run",
                                                     "evidence_required": [{"kind": "file", "target": str(tmp_path / "a.txt")}]})
    assert r.status_code == 200 and r.json()["mission_id"] == "m1", r.text
    r = await client.post("/api/org/missions/m1/run")
    body = r.json()
    assert r.status_code == 200 and body["state"] == "blocked" and body["blockers"]
    assert "no_executable_steps" in body["blockers"][0]["reason"]
    pending = await svc.approvals.list(status="pending")
    assert any(a["kind"] == "org_review" and "no_executable_steps" in a["preview"] for a in pending)

    snap = (await client.get("/api/org/snapshot")).json()
    assert snap == svc.organization.runtime.snapshot().to_dict()
    assert (await client.get("/api/org/missions")).json()[0]["mission_id"] == "m1"
    assert (await client.post("/api/org/missions/none/run")).status_code == 404
    assert (await client.post("/api/org/resume")).status_code == 200
    assert (await client.get("/api/org/learning")).status_code == 200
    # ничего не исполнено и ни одной задачи V2 не создано: до делегирования дело не дошло
    assert (await client.get("/api/tasks")).json() in ([], {"tasks": []}) or not any(
        t.get("kind") == "organization" for t in (await client.get("/api/tasks")).json() if isinstance(t, dict))


@pytest.fixture
async def fleet_env(tmp_path, monkeypatch):
    pytest.importorskip("bossman_v3", reason="bossman-core не установлен рядом с Command Center")
    for k in ("BOSSMAN_V3_ENABLED", "BOSSMAN_V3_ORGANIZATION", "BOSSMAN_V3_FLEET"):
        monkeypatch.setenv(k, "1")
    monkeypatch.setenv("BOSSMAN_EVIDENCE_KEY_FILE", str(tmp_path / "keys" / "evidence.key"))
    settings = make_settings(tmp_path)
    app, svc = await start_app(settings, start_workers=False)
    async with client_for(app, svc) as client:
        yield app, svc, client
    await svc.stop()


async def test_fleet_appears_in_control_plane_when_enabled(fleet_env):
    """§15: флот в /api/control-plane — только durable-сводка, локальный узел = этот хост,
    удалённый транспорт честно NO."""
    app, svc, client = fleet_env
    org = svc.organization
    assert org.fleet is not None and org.node_id.startswith("local-")
    org.heartbeat()
    body = (await client.get("/api/control-plane")).json()
    fleet = body["fleet"]
    assert fleet["enabled"] is True and fleet["remote_transport_production_ready"] is False
    assert fleet["node_auth_production_ready"] is False
    assert [n["node_id"] for n in fleet["nodes"]] == [org.node_id] and fleet["health"]["online"] == [org.node_id]
    assert fleet["nodes"][0]["capabilities"] > 0 and fleet["queue_depth"] == 0 and fleet["active_leases"] == []
    assert "secret" not in str(fleet).lower() and "api_key" not in str(fleet)
    def _stable(v):
        """Сводка одна и та же в обеих ручках — с точностью до величин, которые
        по построению текут между двумя запросами (метки времени, возраст, доли
        секунды до истечения аренды). Сравнивать их значило бы проверять часы."""
        drop = ("_ts", "_at", "_s", "_ms", "_seconds")
        if isinstance(v, dict):
            return {k: _stable(x) for k, x in v.items()
                    if not (k.endswith(drop) or k in ("now", "since", "uptime", "age"))}
        if isinstance(v, list):
            return [_stable(x) for x in v]
        return v
    assert _stable((await client.get("/api/org/fleet")).json()) == _stable(fleet)
    # без флота — честно выключено
    assert body["organization"]["enabled"] is True
