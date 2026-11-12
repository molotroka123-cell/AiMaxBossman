"""Architecture P0 step 2: pc_operator is the single operator entry; the other implementations are legacy-hidden by
config (still mounted), the pult resolves only the pc-operator role agent."""
from __future__ import annotations

import json

import sqlalchemy as sa

from bcc import operator_registry as reg
from bcc.db import agents as agents_t
from bcc.features import pc_operator as po

from .helpers import make_stack


def test_registry_defaults_mark_the_three_others_legacy():
    cfg = reg.load()
    assert cfg["entry"] == "pc_operator"
    assert reg.legacy_names() == ["agent_opencode", "pc_agent", "qwen_operator"]
    assert reg.is_pult_resolvable("pc_operator")
    for name in reg.legacy_names():
        assert not reg.is_pult_resolvable(name)


def test_entry_cannot_be_hidden_and_toggle_is_config_driven(tmp_path, monkeypatch):
    f = tmp_path / "r.json"
    f.write_text(json.dumps({"entry": "pc_operator", "implementations": {
        "pc_operator": {"legacy": True}, "qwen_operator": {"legacy": True}}}), encoding="utf-8")
    monkeypatch.setenv("BCC_OPERATOR_REGISTRY_FILE", str(f))
    assert reg.legacy_names() == ["qwen_operator"]            # negative control: the entry stays visible
    monkeypatch.setenv("BCC_OPERATOR_LEGACY_VISIBLE", "qwen_operator")
    assert reg.legacy_names() == [] and reg.is_pult_resolvable("qwen_operator")


def test_modules_named_in_registry_exist_and_stay_mounted():
    import importlib
    for name, spec in reg.load()["implementations"].items():
        if spec.get("module"):
            importlib.import_module(spec["module"])           # nothing deleted, still importable


async def test_pult_operator_path_resolves_only_to_pc_operator(env):
    await make_stack(env.client)
    # a rival agent that looks like an operator (computer tools, local model) must never be returned by /api/operator
    async with env.svc.db.session() as s:
        first = (await s.execute(sa.select(agents_t).limit(1))).first()._mapping
        rival = {k: v for k, v in first.items() if k != "id"}
        rival.update(name="Qwen Оператор (lab)", role="qwen-operator", tools=["computer.observe", "terminal.run"])
        rid = (await s.execute(sa.insert(agents_t).values(**rival))).inserted_primary_key[0]
        await s.commit()
    view = (await env.client.get("/api/operator")).json()
    assert view["agent"]["id"] != rid
    assert view["entry"] == "pc_operator" and "qwen_operator" in view["legacy_hidden"]
    async with env.svc.db.session() as s:
        role = (await s.execute(sa.select(agents_t.c.role).where(agents_t.c.id == view["agent"]["id"]))).scalar_one()
    assert role == po.ROLE
