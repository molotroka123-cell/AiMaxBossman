"""authored_by_lane agcloud: behavior tests for bcc.features.agentmap and bcc.v2.agent_graph."""
from __future__ import annotations

import sqlalchemy as sa

from bcc.db import agents as agents_t, orchestra_members as members_t, orchestras as orch_t
from bcc.features import agentmap
from bcc.v2 import agent_graph
from bcc.v2.agent_graph import AgentEdge, AgentNode, graph_payload


def test_graph_payload_drops_dangling_edges_and_serializes_slots_dataclasses():
    nodes = [AgentNode(id="a", label="A", status="idle"), AgentNode(id="b", label="B", status="working", model="m")]
    edges = [AgentEdge("a", "b"), AgentEdge("a", "ghost"), AgentEdge("ghost", "b", kind="reviews")]
    p = graph_payload(nodes, edges)
    assert [n["id"] for n in p["nodes"]] == ["a", "b"]
    assert p["edges"] == [{"source": "a", "target": "b", "kind": "delegates"}]
    assert p["nodes"][1]["model"] == "m" and p["nodes"][0]["task"] == ""
    assert agent_graph.graph_payload is graph_payload


def test_feature_registered():
    assert agentmap.FEATURE.name == "agentmap"
    assert {"/agentmap", "/orchestras"} <= {r.path for r in agentmap.router.routes}


async def test_agentmap_empty_then_idle_agent(env):
    g = (await env.client.get("/api/agentmap")).json()
    assert g["counts"] == {"nodes": 0, "edges": 0}
    async with env.svc.db.session() as s:
        aid = int((await s.execute(sa.insert(agents_t).values(name="solo"))).inserted_primary_key[0])
        await s.commit()
    g = (await env.client.get("/api/agentmap")).json()
    node = next(n for n in g["nodes"] if n["id"] == f"agent:{aid}")
    assert node["label"] == "solo" and node["status"] == "idle"     # no tasks => idle, not invented activity


async def test_orchestra_edges_delegate_and_review(env):
    async with env.svc.db.session() as s:
        oid = int((await s.execute(sa.insert(orch_t).values(name="team", mode="manager"))).inserted_primary_key[0])
        ids = {}
        for nm in ("mgr", "wrk", "rev"):
            ids[nm] = int((await s.execute(sa.insert(agents_t).values(name=nm))).inserted_primary_key[0])
        for pos, (nm, role) in enumerate((("mgr", "manager"), ("wrk", "worker"), ("rev", "reviewer"))):
            await s.execute(sa.insert(members_t).values(orchestra_id=oid, agent_id=ids[nm], role=role, position=pos))
        await s.commit()
    g = (await env.client.get(f"/api/agentmap?orchestra_id={oid}")).json()
    kinds = {(e["source"], e["target"]): e["kind"] for e in g["edges"]}
    assert kinds == {(f"agent:{ids['mgr']}", f"agent:{ids['wrk']}"): "delegates",
                     (f"agent:{ids['mgr']}", f"agent:{ids['rev']}"): "reviews"}
    lst = (await env.client.get("/api/orchestras")).json()
    assert [m["role"] for m in lst[0]["members"]] == ["manager", "worker", "reviewer"]
