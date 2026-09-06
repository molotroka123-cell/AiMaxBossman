"""One canonical witness propagated through Organization, Fleet and skills.

Local real file effects; the Fleet fixture represents two in-process nodes,
not remote transport or hardware qualification.
"""
import os

import pytest
from bossman_v3.memory import TaskJournal
from bossman_v3.memory.journal import JournalIntegrityError
from bossman_v3.memory.anchor import SQLiteJournalAnchor
from bossman_v3.organization.store import OrganizationStore
from bossman_v3.organization.bridges import V3ExecutionBridge
from bossman_v3.computer_agent.agent import UniversalComputerAgent
from bossman_v3.skill_factory.verified_trace import candidate_from_journal, UntrustedTrace


def test_anchor_is_used_by_both_fleet_and_node_bridges(tmp_path):
    from test_v3_fleet_e2e import Stack, _contract
    stack = Stack(tmp_path)
    witness = SQLiteJournalAnchor(stack.org.store._connect, namespace="fleet-org")
    stack.org.execution.journal_anchor = witness
    for node in stack.transport._runtimes.values():
        node.inner.journal_anchor = witness
    contract = _contract(stack.world, "work", ["one.txt"])
    stack.org.receive_mission("m1", title="anchored", department_id="engineering", contracts=[contract])
    status = stack.org.run_mission("m1")
    assert status.done and stack.world.side_effects() == 1
    root = tmp_path / "journals"
    journal = TaskJournal.load(task_id="m1__work", root=root, anchor=witness)
    assert len(journal.finished_signed()) == 1
    with pytest.raises(JournalIntegrityError, match="requires its canonical"):
        TaskJournal.load(task_id="m1__work", root=root)
    # Restart preserves the canonical row and does not dispatch a completed task.
    stack.boot()
    stack.org.execution.journal_anchor = SQLiteJournalAnchor(stack.org.store._connect, namespace="fleet-org")
    assert stack.org.run_mission("m1").done
    assert stack.world.side_effects() == 1


def test_skill_extraction_rechecks_the_same_canonical_anchor(tmp_path):
    from test_epoch4_verified_skills import completed
    kwargs, old, ports = completed.__wrapped__(tmp_path)
    store = OrganizationStore(tmp_path / "organization.sqlite")
    witness = SQLiteJournalAnchor(store._connect, namespace="skill-org")
    bridge = V3ExecutionBridge(
        agent_factory=lambda *_: UniversalComputerAgent(ports, ports, ports, ports, ports),
        journal_root=tmp_path / "anchored-journals", journal_anchor=witness)
    contract = kwargs["current_contract"]
    result = bridge.execute(contract, agent_id="fixture")
    assert result.claims["runner_completed"]
    journal = TaskJournal.load(task_id=bridge.journal_id(contract), root=bridge.journal_root, anchor=witness)
    kwargs.update(journal_root=bridge.journal_root, journal_anchor=witness,
                  current_attempts={s.step_id: s.attempt_id for s in journal.steps})
    before = ports.executions
    if os.name == "posix":
        assert candidate_from_journal(**kwargs).candidate.stage.value == "EXPERIMENTAL"
    else:
        with pytest.raises(UntrustedTrace, match="safe local readback unavailable"):
            candidate_from_journal(**kwargs)
    assert ports.executions == before  # extraction is not execution or promotion
    path = journal.root / f"{journal.task_id}.json"
    earlier = path.read_bytes()
    journal.note("later trusted state")
    path.write_bytes(earlier)
    with pytest.raises(JournalIntegrityError, match="high-water"):
        candidate_from_journal(**kwargs)
