"""Feature 07 Terminal (политика/режимы) + 06 Agent Map (граф из данных)."""
from pathlib import Path

import sqlalchemy as sa

from bcc.db import agents as agents_t, orchestras as orch_t, orchestra_members as members_t
from bcc.v2.terminal_control import TerminalPolicy

from .conftest import FakeAdapter
from .helpers import make_stack


# ---------- Terminal policy (чистая логика) ----------

def test_policy_denies_outside_roots(tmp_path):
    pol = TerminalPolicy(allowed_roots=[tmp_path], mode="project_host")
    assert pol.decision("ls", Path("/etc")) == "deny"


def test_policy_denies_destructive(tmp_path):
    pol = TerminalPolicy(allowed_roots=[tmp_path], mode="sandbox")
    assert pol.decision("rm -rf /", tmp_path) == "deny"
    assert pol.decision("git push --force", tmp_path) == "deny"


def test_policy_auto_and_ask(tmp_path):
    pol = TerminalPolicy(allowed_roots=[tmp_path], mode="project_host")
    assert pol.decision("git status", tmp_path) == "auto"
    assert pol.decision("pip install requests", tmp_path) == "ask"


def test_system_admin_never_auto(tmp_path):
    pol = TerminalPolicy(allowed_roots=[tmp_path], mode="system_admin")
    assert pol.decision("git status", tmp_path) == "ask"   # даже безобидное — ask


# ---------- Terminal API ----------

async def test_terminal_preview_decisions(env):
    r = (await env.client.post("/api/terminal/preview",
                               json={"command": "git status", "mode": "sandbox"})).json()
    assert r["decision"] == "auto"
    r2 = (await env.client.post("/api/terminal/preview",
                                json={"command": "rm -rf /", "mode": "sandbox"})).json()
    assert r2["decision"] == "deny"


async def test_terminal_ask_creates_approval(env):
    # project_host + install → ask → должен вернуть 202 и approval
    await env.client.post("/api/terminal/roots",
                          json={"roots": [str(env.settings.data_dir)]})
    r = await env.client.post("/api/terminal/run",
                              json={"command": "pip install x", "mode": "project_host",
                                    "cwd": str(env.settings.data_dir)})
    assert r.status_code == 202
    approvals = (await env.client.get("/api/approvals?status=pending")).json()
    assert any(a["kind"] == "terminal" for a in approvals)


async def test_terminal_denies_outside_root(env):
    await env.client.post("/api/terminal/roots", json={"roots": [str(env.settings.data_dir)]})
    r = await env.client.post("/api/terminal/run",
                              json={"command": "ls", "mode": "project_host", "cwd": "/etc"})
    assert r.status_code == 403


async def test_terminal_runs_in_project_host(env):
    """project_host запускает реальную безобидную команду (subprocess) и стримит вывод."""
    import asyncio
    d = env.settings.data_dir
    d.mkdir(parents=True, exist_ok=True)
    await env.client.post("/api/terminal/roots", json={"roots": [str(d)]})
    # echo не в AUTO-списке project_host → нужен approved (имитируем нажатие)
    r = (await env.client.post("/api/terminal/run",
                               json={"command": "echo привет-терминал", "mode": "project_host",
                                     "cwd": str(d), "approved": True})).json()
    sid = r["session_id"]
    for _ in range(50):
        st = (await env.client.get(f"/api/terminal/sessions/{sid}")).json()
        if st["finished"]:
            break
        await asyncio.sleep(0.05)
    assert st["finished"] and st["exit_code"] == 0
    assert any("привет-терминал" in line for line in st["output_tail"])


# ---------- Agent Map ----------

async def test_agentmap_reflects_agents_and_status(env):
    env.svc.registry.adapter_factory = lambda m, p: FakeAdapter("ок")
    stack = await make_stack(env.client)
    graph = (await env.client.get("/api/agentmap")).json()
    assert graph["counts"]["nodes"] >= 1
    node = next(n for n in graph["nodes"] if n["id"] == f"agent:{stack['agent']['id']}")
    assert node["status"] in ("idle", "queued", "working", "failed")


async def test_agentmap_orchestra_edges(env):
    stack = await make_stack(env.client)
    # менеджер + 2 воркера напрямую в БД (NL-оркестрация делает это своим API)
    async with env.svc.db.session() as s:
        oid = int((await s.execute(sa.insert(orch_t).values(name="team", mode="manager"))
                   ).inserted_primary_key[0])
        mgr = stack["agent"]["id"]
        w1 = int((await s.execute(sa.insert(agents_t).values(name="w1"))).inserted_primary_key[0])
        w2 = int((await s.execute(sa.insert(agents_t).values(name="w2"))).inserted_primary_key[0])
        await s.execute(sa.insert(members_t).values(orchestra_id=oid, agent_id=mgr, role="manager", position=0))
        await s.execute(sa.insert(members_t).values(orchestra_id=oid, agent_id=w1, role="worker", position=1))
        await s.execute(sa.insert(members_t).values(orchestra_id=oid, agent_id=w2, role="worker", position=2))
        await s.commit()
    graph = (await env.client.get(f"/api/agentmap?orchestra_id={oid}")).json()
    assert graph["counts"]["nodes"] == 3
    assert graph["counts"]["edges"] == 2      # manager → w1, manager → w2
    orchestras = (await env.client.get("/api/orchestras")).json()
    assert orchestras[0]["members"]
