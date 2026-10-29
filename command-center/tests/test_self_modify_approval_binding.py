"""Self-modify approval binding (audit 28.10 night, found by re-checking the swarm-2 fix with independent models).

The swarm-2 check called `svc.approvals.get`, which the real Approvals manager does not have: in production no approval ever
verified (the green test used a test double), and a record without a `tool` field defaulted to «pc.self_modify». These tests use
the REAL row shape of the approvals table (kind, preview, task_id, status) read through the database path, plus negative controls.
"""
from __future__ import annotations

import asyncio
from contextlib import asynccontextmanager
from types import SimpleNamespace

import pytest

from bcc.features import pc_agent


def _svc(rows: dict[int, dict]):
    @asynccontextmanager
    async def session():
        yield SimpleNamespace()
    return SimpleNamespace(db=SimpleNamespace(session=session), approvals=SimpleNamespace(), rows=rows)


def _row(aid: int, *, status="approved", kind="tool", tool_in_preview="pc.self_modify", task_id=7):
    return {"id": aid, "status": status, "kind": kind, "task_id": task_id,
            "preview": f"[Argon PC-Agent · T2 · self_modify] Задача #{task_id}: {tool_in_preview} {{}} — reason"}


def _verify(svc, rt, aid, task_id=7, monkeypatch=None):
    async def fake_fetch_one(_s, _table, row_id):
        return svc.rows.get(int(row_id))
    monkeypatch.setattr(pc_agent, "fetch_one", fake_fetch_one)
    return asyncio.run(pc_agent._verified_self_modify_approval(svc, rt, aid, task_id))


def test_real_row_for_self_modify_verifies_once(monkeypatch):
    svc, rt = _svc({11: _row(11)}), SimpleNamespace()
    assert _verify(svc, rt, 11, monkeypatch=monkeypatch) is True
    assert _verify(svc, rt, 11, monkeypatch=monkeypatch) is False          # spent: no replay


@pytest.mark.parametrize("row", [
    _row(12, tool_in_preview="pc.write_file"),                              # approval of another tool
    _row(12, kind="prompt"),                                                # not a PC-agent tool approval
    _row(12, status="pending"),                                             # not decided
    _row(12, status="rejected"),
    _row(12, task_id=99),                                                   # another task
])
def test_foreign_or_undecided_approvals_do_not_verify(monkeypatch, row):
    svc, rt = _svc({12: row}), SimpleNamespace()
    assert _verify(svc, rt, 12, task_id=7, monkeypatch=monkeypatch) is False


def test_missing_row_and_bad_ids_do_not_verify(monkeypatch):
    svc, rt = _svc({}), SimpleNamespace()
    assert _verify(svc, rt, 404, monkeypatch=monkeypatch) is False
    assert _verify(svc, rt, "not-a-number", monkeypatch=monkeypatch) is False
    assert _verify(svc, rt, None, monkeypatch=monkeypatch) is False


def test_explicit_tool_field_must_be_self_modify(monkeypatch):
    async def get_other(_aid):
        return {"id": 5, "status": "approved", "tool": "pc.delete_file", "task_id": 7}
    svc = SimpleNamespace(approvals=SimpleNamespace(get=get_other))
    assert asyncio.run(pc_agent._verified_self_modify_approval(svc, SimpleNamespace(), 5, 7)) is False


def test_no_database_means_no_approval():
    svc = SimpleNamespace(approvals=SimpleNamespace())                      # real manager: no `get`, and no db here
    assert asyncio.run(pc_agent._verified_self_modify_approval(svc, SimpleNamespace(), 3, 7)) is False
