"""V4 uses real BCC SQLite, canonical approvals and actual file post-state.

No model/provider inference. A host guard deterministically revokes permissions
between initial approval and dispatch, reproducing a stale-agent snapshot.
"""
from contextlib import contextmanager
from dataclasses import replace

import pytest
from bossman_v3.adapters.command_center import _preview
from bossman_v3.computer_agent.agent import ApprovalDeniedError, PolicyDeniedError
from bossman_v3.contracts import TypedAction
from bossman_v3.execution import CompoundRunner, PlanStep
from bossman_v3.memory import TaskJournal
from bossman_v3.memory.anchor import SQLiteJournalAnchor
from bossman_v3.organization.store import OrganizationStore


@pytest.fixture
def live(request):
    from test_v3_command_center_adapters import live as existing
    fixture = existing.__wrapped__(request.getfixturevalue("tmp_path"))
    try:
        yield next(fixture)
    finally:
        try:
            next(fixture)
        except StopIteration:
            pass


def action(live):
    return TypedAction("terminal.run", {
        "command": "python -c \"open('boundary.txt','w').write('accepted')\"",
        "mode": "project_host", "cwd": str(live.work),
        "expect": {"kind": "file", "target": str(live.work / "boundary.txt"),
                   "expect": {"exists": True}},
    })


def prepare(live):
    agent = live.agent_()
    a = action(live)
    with pytest.raises(ApprovalDeniedError):
        agent.run(a)
    assert live.approve_all_pending() == 1
    return agent, a


def test_current_agent_disabled_after_approval_never_executes(live):
    import sqlalchemy as sa
    from bcc.db import agents
    agent, a = prepare(live)
    @contextmanager
    def revoke():
        async def update():
            async with live.svc.db.session() as s:
                await s.execute(sa.update(agents).where(agents.c.id == live.agent["id"]).values(enabled=False))
                await s.commit()
        live.rt.call(update())
        yield
    with pytest.raises(PolicyDeniedError, match="disabled"):
        agent.run(a, {"execution_guard": revoke})
    assert not (live.work / "boundary.txt").exists()
    assert live.tool_calls() == []


def test_consumed_approval_revoked_at_boundary_is_not_reused(live):
    import sqlalchemy as sa
    from bcc.db import approvals
    agent, a = prepare(live)
    @contextmanager
    def revoke():
        async def update():
            async with live.svc.db.session() as s:
                result = await s.execute(sa.update(approvals).where(
                    approvals.c.task_id == live.task["id"], approvals.c.status == "consumed"
                ).values(status="revoked"))
                assert result.rowcount == 1
                await s.commit()
        live.rt.call(update())
        yield
    with pytest.raises(ApprovalDeniedError, match="revoked"):
        agent.run(a, {"execution_guard": revoke})
    assert not (live.work / "boundary.txt").exists()
    assert live.tool_calls() == []


def test_bound_approval_cannot_cross_runs(live):
    agent, a = prepare(live)
    async def another_run():
        import sqlalchemy as sa
        from bcc.db import task_runs
        async with live.svc.db.session() as s:
            row = await s.execute(sa.insert(task_runs).values(task_id=live.task["id"], attempt=2))
            await s.commit()
            return row.inserted_primary_key[0]
    agent.approval.run_id = live.rt.call(another_run())
    with pytest.raises(ApprovalDeniedError):
        agent.run(a)
    assert not (live.work / "boundary.txt").exists()
    assert live.tool_calls() == []


def test_same_preview_prefix_cannot_hide_changed_arguments(live):
    a = action(live)
    a = replace(a, args={**a.args, "command": "A" * 3000 + "first"})
    b = replace(a, args={**a.args, "command": "A" * 3000 + "second"})
    args = dict(task_id=live.task["id"], run_id=live.run_id, agent_id=live.agent["id"])
    assert len(_preview(a, **args)) == 500
    assert _preview(a, **args) != _preview(b, **args)


@pytest.mark.parametrize("change", ["expect", "scope", "run", "agent", "generation"])
def test_approval_identity_binds_all_authority_and_obligations(live, monkeypatch, change):
    a = action(live)
    kw = dict(task_id=live.task["id"], run_id=live.run_id, agent_id=live.agent["id"])
    before = _preview(a, **kw)
    if change == "expect":
        a = replace(a, args={**a.args, "expect": {"kind": "file", "target": "/other"}})
    elif change == "scope":
        a = replace(a, scopes=("different-scope",))
    elif change in {"run", "agent"}:
        kw[change + "_id"] += 1
    else:
        from bcc.tools import REGISTRY
        spec = REGISTRY.get(a.action_type)
        monkeypatch.setattr(spec, "generation", spec.generation + 1)
    assert _preview(a, **kw) != before


def test_live_bcc_chain_uses_anchor_and_restarts_without_duplicate_effect(live, tmp_path):
    store = OrganizationStore(tmp_path / "organization.sqlite")
    witness = SQLiteJournalAnchor(store._connect, namespace="org")
    a = action(live)
    journal = TaskJournal.start(task_id="bcc-anchored", plan=[("write", "write exact file")],
                                root=tmp_path / "journals", anchor=witness)
    plan = [PlanStep("write", "write exact file", a)]
    result = CompoundRunner(live.agent_(), journal).run(plan)
    assert not result.completed and not journal.steps[0].in_flight
    assert live.approve_all_pending() == 1
    journal = TaskJournal.load(task_id=journal.task_id, root=journal.root, anchor=witness)
    assert CompoundRunner(live.agent_(), journal).run(plan).completed
    assert (live.work / "boundary.txt").read_text() == "accepted"
    assert len(live.tool_calls()) == 1
    journal = TaskJournal.load(task_id=journal.task_id, root=journal.root,
                              anchor=SQLiteJournalAnchor(store._connect, namespace="org"))
    assert CompoundRunner(live.agent_(), journal).run(plan).completed
    assert len(live.tool_calls()) == 1
