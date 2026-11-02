from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

import pytest

from bcc.db import Database
from bcc.features.antigravity_skills_audit import (
    audit_all_registered_tools,
    audit_all_skills,
    run_skills_and_tools_audit_session,
)
from bcc.tools import REGISTRY, ToolContext
from bcc.v2.memory.facts import FactStore


@pytest.mark.asyncio
async def test_audit_all_skills_and_tools_and_teach_antigravity(tmp_path: Path) -> None:
    s_rep = audit_all_skills()
    assert s_rep["ok"] is True
    assert s_rep["agent_skills_verified"] >= 2
    assert s_rep["catalog_skills_verified"] >= 1

    t_rep = await audit_all_registered_tools()
    assert t_rep["ok"] is True
    assert t_rep["verified_tools_count"] >= 20
    assert t_rep["facade_or_broken"] == []

    db_path = tmp_path / "skills_audit.db"
    db = Database(f"sqlite+aiosqlite:///{db_path}")
    await db.create_all()

    receipt_file = tmp_path / "receipt.json"
    res = await run_skills_and_tools_audit_session(
        db,
        receipt_path=receipt_file,
    )
    assert res["all_verified"] is True
    assert res["proof_passed"] is True
    assert len(res["proof_sha256"]) == 64
    assert res["antigravity_mastery"]["competencies_count"] == 6
    assert res["antigravity_mastery"]["average_mastery_score"] == 1.0

    store = FactStore(SimpleNamespace(db=db))
    facts = await store.search(query="Antigravity", caller_scope="work")
    assert len(facts) == 6

    spec = REGISTRY.get("antigravity.audit_skills_and_tools")
    assert spec is not None
    t_res = await spec.handler(
        {},
        ToolContext(svc=SimpleNamespace(db=db), run_id=1, task={"id": 1}, agent={"id": "bossman"}),
    )
    assert t_res.error is False
    assert t_res.data["all_verified"] is True

    await db.close()


def test_run_without_receipt_path_never_writes_into_the_repo(tmp_path: Path) -> None:
    """02.11.2026: the default receipt overwrote a tracked repo file and made /health UNHEALTHY (dirty checkout)."""
    from bcc.features.antigravity_skills_audit import _repo_root, default_receipt_path

    svc = SimpleNamespace(settings=SimpleNamespace(data_dir=tmp_path))
    target = default_receipt_path(svc)
    assert target == tmp_path / "audits" / "skills-tools" / "receipt.json"
    assert _repo_root() not in target.parents
    no_settings = default_receipt_path(object())
    assert _repo_root() not in no_settings.parents
