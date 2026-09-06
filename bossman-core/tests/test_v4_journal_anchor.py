"""V4 M3: canonical SQLite witness + real files/process death, no mocked effects."""
from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from contextlib import contextmanager
from dataclasses import asdict
import hashlib
import json
import os
from pathlib import Path
import sqlite3
import subprocess
import sys

import pytest

from bossman_v3.memory.anchor import AnchorConflict, SQLiteJournalAnchor
from bossman_v3.memory.journal import JournalIntegrityError, TaskJournal
from bossman_v3.organization.store import OrganizationStore


def witness(root, namespace="organization"):
    store = OrganizationStore(root / "canonical.sqlite")
    return SQLiteJournalAnchor(store._connect, namespace=namespace)


def start(root, anchor):
    return TaskJournal.start(task_id="mission", plan=[("one", "append")],
                             root=root / "journals", anchor=anchor)


def test_anchored_restart_and_downlevel_reader_denial(tmp_path):
    anchor = witness(tmp_path)
    journal = start(tmp_path, anchor)
    journal.begin("one")
    raw = json.loads((tmp_path / "journals/mission.json").read_text())
    assert raw["schema_version"] == 4
    assert TaskJournal.load(task_id="mission", root=journal.root, anchor=anchor).steps[0].in_flight
    with pytest.raises(JournalIntegrityError, match="requires its canonical"):
        TaskJournal.load(task_id="mission", root=journal.root)


def test_valid_signed_snapshot_rollback_is_rejected(tmp_path):
    anchor = witness(tmp_path)
    journal = start(tmp_path, anchor)
    path = tmp_path / "journals/mission.json"
    old = path.read_bytes()
    journal.begin("one")
    assert old != path.read_bytes()
    # Do not modify a single byte of the previous signature or payload.
    path.write_bytes(old)
    with pytest.raises(JournalIntegrityError, match="high-water"):
        TaskJournal.load(task_id="mission", root=journal.root, anchor=witness(tmp_path))
    assert path.read_bytes() == old


def test_deleting_journal_cannot_recreate_an_anchored_identity(tmp_path):
    anchor = witness(tmp_path)
    start(tmp_path, anchor)
    (tmp_path / "journals/mission.json").unlink()
    with pytest.raises(JournalIntegrityError, match="already anchored"):
        start(tmp_path, anchor)
    assert not (tmp_path / "journals/mission.json").exists()


def test_legacy_requires_reconciliation_not_automatic_enrollment(tmp_path):
    journal = start(tmp_path, None)
    before = (journal.root / "mission.json").read_bytes()
    with pytest.raises(JournalIntegrityError, match="legacy journal"):
        TaskJournal.load(task_id="mission", root=journal.root, anchor=witness(tmp_path))
    assert (journal.root / "mission.json").read_bytes() == before
    assert TaskJournal.load(task_id="mission", root=journal.root).task_id == "mission"


def test_namespace_does_not_import_someone_elses_witness(tmp_path):
    journal = start(tmp_path, witness(tmp_path, "owner-a"))
    with pytest.raises(JournalIntegrityError, match="high-water"):
        TaskJournal.load(task_id="mission", root=journal.root, anchor=witness(tmp_path, "owner-b"))


def test_anchor_before_publish_failure_blocks_old_valid_file(tmp_path, monkeypatch):
    anchor = witness(tmp_path)
    journal = start(tmp_path, anchor)
    path = journal.root / "mission.json"
    before = path.read_bytes()
    def failed_replace(*args):
        raise OSError("injected replace failure after anchor commit")
    monkeypatch.setattr("bossman_v3.memory.journal.os.replace", failed_replace)
    with pytest.raises(OSError, match="injected"):
        journal.begin("one")
    assert path.read_bytes() == before
    with pytest.raises(JournalIntegrityError, match="high-water"):
        TaskJournal.load(task_id="mission", root=journal.root, anchor=anchor)


def test_unavailable_anchor_does_not_publish(tmp_path):
    anchor = witness(tmp_path)
    journal = start(tmp_path, anchor)
    before = (journal.root / "mission.json").read_bytes()
    with sqlite3.connect(tmp_path / "canonical.sqlite") as con:
        con.execute("DROP TABLE org_journal_heads")
    with pytest.raises(JournalIntegrityError, match="unavailable"):
        journal.begin("one")
    assert (journal.root / "mission.json").read_bytes() == before


def test_concurrent_compare_and_swap_has_one_winner(tmp_path):
    anchor = witness(tmp_path)
    h0 = "0" * 64
    anchor.advance("task", None, h0)
    def advance(i):
        try:
            anchor.advance("task", h0, hashlib.sha256(str(i).encode()).hexdigest())
            return True
        except AnchorConflict:
            return False
    with ThreadPoolExecutor(max_workers=8) as pool:
        assert sum(pool.map(advance, range(16))) == 1
    with sqlite3.connect(tmp_path / "canonical.sqlite") as con:
        assert con.execute("SELECT generation FROM org_journal_heads").fetchone()[0] == 2


@pytest.mark.parametrize("bad", ["", "x", "A" * 64, True, 1, "0" * 63])
def test_invalid_hashes_rejected(tmp_path, bad):
    anchor = witness(tmp_path)
    with pytest.raises(AnchorConflict):
        anchor.advance("task", None, bad)


def test_caller_transaction_cannot_fake_durable_acknowledgement(tmp_path):
    witness(tmp_path)
    def existing_transaction():
        con = sqlite3.connect(tmp_path / "canonical.sqlite")
        con.execute("BEGIN IMMEDIATE")
        return con
    anchor = SQLiteJournalAnchor(existing_transaction, namespace="organization")
    with pytest.raises(AnchorConflict, match="independent committed"):
        anchor.advance("task", None, "a" * 64)


def _worker(root: Path, mode: str):
    from datetime import datetime, timezone
    from bossman_v3.computer_agent.agent import UniversalComputerAgent
    from bossman_v3.contracts import (TypedAction, SideEffectClass, PolicyDecision,
                                     ExecutionReceipt, Observation, VerificationResult)
    from bossman_v3.execution import CompoundRunner, PlanStep
    anchor = witness(root)
    action = TypedAction("test.append", {"path": str(root / "effect.log")},
                         side_effect=SideEffectClass.IRREVERSIBLE)
    class Ports:
        def authorize(self, action, context): return PolicyDecision(True)
        def supports(self, action_type): return action_type == "test.append"
        def execute(self, action):
            with open(action.args["path"], "ab") as stream:
                stream.write(b"one\n"); stream.flush(); os.fsync(stream.fileno())
            if mode == "crash": os._exit(86)
            now = datetime.now(timezone.utc)
            return ExecutionReceipt(action.action_type, now, now)
        def observe_fresh(self, action, receipt):
            return Observation(datetime.now(timezone.utc), "fs", {"data": (root / "effect.log").read_bytes()})
        def verify(self, action, receipt, observation):
            return VerificationResult(observation.state["data"] == b"one\n")
    j = (TaskJournal.load(task_id="mission", root=root / "journals", anchor=anchor)
         if mode == "resume" else start(root, anchor))
    if mode == "crash":
        (root / "previous-signed-snapshot").write_bytes((root / "journals/mission.json").read_bytes())
    ports = Ports()
    result = CompoundRunner(UniversalComputerAgent(ports, ports, ports, ports, ports), j).run(
        [PlanStep("one", "append", action)])
    print(json.dumps(asdict(result)))


def test_process_death_then_full_signed_rollback_never_duplicates_effect(tmp_path):
    code = ("import importlib.util, pathlib, sys; "
            "s=importlib.util.spec_from_file_location('anchor_probe',sys.argv[1]); "
            "m=importlib.util.module_from_spec(s); s.loader.exec_module(m); "
            "m._worker(pathlib.Path(sys.argv[2]),sys.argv[3])")
    def run(mode):
        return subprocess.run([sys.executable, "-c", code, __file__, str(tmp_path), mode],
                              capture_output=True, text=True, timeout=20, check=False)
    assert run("crash").returncode == 86
    path = tmp_path / "journals/mission.json"
    path.write_bytes((tmp_path / "previous-signed-snapshot").read_bytes())
    for _ in range(2):
        result = run("resume")
        assert result.returncode != 0 and "JournalIntegrityError" in result.stderr
        assert (tmp_path / "effect.log").read_bytes() == b"one\n"


def test_canonical_connection_failure_is_fail_closed():
    def unavailable():
        raise sqlite3.OperationalError("fixture disk unavailable")
    anchor = SQLiteJournalAnchor(unavailable, namespace="org")
    with pytest.raises(AnchorConflict, match="connection unavailable"):
        anchor.check("mission", "a" * 64)
