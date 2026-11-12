"""/api/operator: one local PC operator, swappable local model, total control = PC permissions only (owner 11.11)."""
from __future__ import annotations

import sqlalchemy as sa

from bcc.db import agents as agents_t
from bcc.features import pc_operator as po

from .helpers import make_stack


async def _cloud_model(env) -> int:
    prov = (await env.client.post("/api/providers", json={
        "name": "облако", "kind": "openai_compat", "base_url": "https://api.example.com/v1", "api_key": "sk-test-cloud"})).json()
    return (await env.client.post("/api/models", json={"provider_id": prov["id"], "name": "big-cloud", "alias": "big-cloud"})).json()["id"]


async def test_get_creates_one_local_operator_with_many_steps(env):
    stack = await make_stack(env.client)
    a = (await env.client.get("/api/operator")).json()
    b = (await env.client.get("/api/operator")).json()
    assert a["agent"]["id"] == b["agent"]["id"] and a["agent"]["name"] == po.NAME
    assert a["model"]["id"] == stack["model"]["id"] and a["ready"] is True and a["total_control"] is False
    async with env.svc.db.session() as s:
        rows = (await s.execute(sa.select(agents_t).where(agents_t.c.role == po.ROLE))).fetchall()
    assert len(rows) == 1 and rows[0]._mapping["max_steps"] >= po.MIN_STEPS


async def test_cloud_models_are_never_candidates_and_cannot_be_set(env):
    await make_stack(env.client)
    cloud = await _cloud_model(env)
    view = (await env.client.get("/api/operator")).json()
    assert cloud not in [c["id"] for c in view["candidates"]]
    r = await env.client.put("/api/operator", json={"model_id": cloud})
    assert r.status_code == 422 and "BM_OPERATOR_NOT_LOCAL" in r.text


async def test_total_control_grants_only_pc_permissions_and_is_reversible(env):
    await make_stack(env.client)
    on = (await env.client.put("/api/operator", json={"total_control": True})).json()
    assert on["total_control"] is True
    async with env.svc.db.session() as s:
        perms = (await s.execute(sa.select(agents_t.c.permissions).where(agents_t.c.role == po.ROLE))).scalar_one()
    assert set(k for k, v in perms.items() if v) == set(po.TOTAL_CONTROL) | {po.READ_PERM}   # read-only grant is default ON
    for never in ("email.send", "channel.send", "invoice.create", "deploy.production", "settings.write", "cli.bypass"):
        assert never not in perms
    off = (await env.client.put("/api/operator", json={"total_control": False})).json()
    assert off["total_control"] is False


async def test_empty_put_is_rejected_and_extra_keys_forbidden(env):
    await make_stack(env.client)
    assert (await env.client.put("/api/operator", json={})).status_code == 422
    assert (await env.client.put("/api/operator", json={"provider": "x"})).status_code == 422


async def test_no_local_model_is_an_honest_not_ready(env):
    await _cloud_model(env)
    v = (await env.client.get("/api/operator")).json()
    assert v["agent"] is None and v["ready"] is False and v["reason"]


def test_qwen3_coder_xml_tool_call_text_becomes_a_real_call_only_for_granted_tools():
    from types import SimpleNamespace
    from bcc.engine import parse_text_tool_calls
    spec = SimpleNamespace(name="terminal.run", api_name="terminal_run")
    text = ("<function=terminal_run>\n<parameter=command>\nhostname\n</parameter>\n<parameter=timeout>\n30\n</parameter>\n"
            "</function>\n</tool_call>")
    calls = parse_text_tool_calls(text, [spec], step=2)
    assert len(calls) == 1 and calls[0].name == "terminal_run" and calls[0].arguments == {"command": "hostname", "timeout": 30}
    assert parse_text_tool_calls("<function=filesystem_delete>\n<parameter=path>\nC:/\n</parameter>\n</function>", [spec]) == []


def test_total_control_lifts_only_the_generic_terminal_asks():
    from bcc.features.tools_terminal import SPECS
    from bcc.tools import decide_effect
    run = next(s for s in SPECS if s.name == "terminal.run")
    perms = {k: True for k in po.TOTAL_CONTROL}
    op = {"role": po.ROLE, "permissions": perms}
    other = {"role": "worker", "permissions": perms}
    assert decide_effect(run, {"command": "hostname"}, op)[0] == "auto"
    assert decide_effect(run, {"command": "hostname", "mode": "project_host"}, op)[0] == "auto"
    assert decide_effect(run, {"command": "hostname", "mode": "project_host"}, other)[0] == "ask"
    assert decide_effect(run, {"command": "hostname"}, {"role": po.ROLE, "permissions": {"terminal.run": True}})[0] == "ask"
    assert decide_effect(run, {"command": "git push origin main", "mode": "project_host"}, op)[0] == "ask"
    assert decide_effect(run, {"command": "rm -rf /", "mode": "project_host"}, op)[0] == "deny"


async def test_warm_reports_an_error_instead_of_hanging(env):
    await make_stack(env.client)     # provider 127.0.0.1:8080 is not running in tests
    r = (await env.client.post("/api/operator/warm")).json()
    assert r["ok"] is False and r["loaded"] is False and r["error"]


async def test_pc_recipes_rule_added_once_and_names_the_disk_tool(env):
    """12.11 bench: the operator got D: free space from the system-disk tool; the recipe names disk_free once."""
    await make_stack(env.client)
    await env.client.get("/api/operator")
    await env.client.get("/api/operator")
    async with env.svc.db.session() as s:
        row = (await s.execute(sa.select(agents_t).where(agents_t.c.role == po.ROLE))).fetchall()[0]._mapping
    prompt = row["system_prompt"]
    assert prompt.count(po.RECIPES_MARK) == 1 and prompt.count(po.SELFEXT_MARK) == 1
    assert "disk_free" in prompt and "pc.system_info видит только C:" in prompt
