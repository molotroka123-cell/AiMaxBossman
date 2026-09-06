"""Bounded G01/G02/G06/G07 foundation; not whole-epoch certification.

G01/G02 reuse the real BCC fixture (SQLite, tool registry, approvals, terminal
subprocess, post-state verifier). G06/G07 use a deliberately constrained local
append service with real subprocess death and signed journals. No model runs.
Neither path certifies Mission IR intake, BCC finalization, HTTP deployment,
live Windows desktop, or an external service's exactly-once semantics.
"""
from __future__ import annotations

from dataclasses import asdict
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import subprocess
import sys

import pytest

from bossman_v3.computer_agent.agent import UniversalComputerAgent
from bossman_v3.contracts import (
    ApprovalDecision, ExecutionReceipt, Observation, PolicyDecision,
    SideEffectClass, TypedAction, VerificationResult,
)
from bossman_v3.execution import CompoundRunner, PlanStep
from bossman_v3.memory import TaskJournal
from bossman_v3.memory.journal import JournalIntegrityError


@pytest.fixture
def bcc_live(request):
    # Import only in the BCC tests: standalone Core must still run crash tests.
    pytest.importorskip("bcc", reason="G01/G02 require the Command Center package")
    from test_v3_command_center_adapters import live
    fixture = live.__wrapped__(request.getfixturevalue("tmp_path"))
    try:
        yield next(fixture)
    finally:
        try:
            next(fixture)
        except StopIteration:
            pass


def _bcc_plan(live, content: str, *, write: bool = True):
    expected = b"continuity-golden-exact-bytes"
    command = (f"python -c \"open('golden.bin','wb').write(b'{content}')\""
               if write else 'python -c "print(\'Done: golden.bin created\')"')
    action = TypedAction(
        "terminal.run",
        {"command": command, "mode": "project_host", "cwd": str(live.work),
         "expect": {"kind": "file", "target": str(live.work / "golden.bin"),
                    "expect": {"exists": True, "sha256": hashlib.sha256(expected).hexdigest()}}},
        side_effect=SideEffectClass.IDEMPOTENT_WRITE,
    )
    return [PlanStep("create", "create exact golden bytes", action)]


def _approved_bcc_run(live, tmp_path, plan):
    journal = TaskJournal.start(task_id="golden-bcc", root=tmp_path / "journal",
                                plan=[(s.step_id, s.intent) for s in plan])
    first = CompoundRunner(live.agent_(), journal).run(plan)
    assert not first.completed and "ApprovalDenied" in first.reason
    assert not (live.work / "golden.bin").exists()
    assert live.tool_calls() == []
    assert live.approve_all_pending() == 1
    result = CompoundRunner(live.agent_(), journal).run(plan)
    assert len(live.tool_calls()) == 1
    assert live.tool_calls()[0]["status"] == "executed"
    return journal, result


def test_g01_real_bcc_file_bytes_and_signed_receipt(bcc_live, tmp_path):
    plan = _bcc_plan(bcc_live, "continuity-golden-exact-bytes")
    journal, result = _approved_bcc_run(bcc_live, tmp_path, plan)
    assert result.completed
    # Independent oracle: reopen the expected file; do not trust tool text.
    assert (bcc_live.work / "golden.bin").read_bytes() == b"continuity-golden-exact-bytes"
    loaded = TaskJournal.load(task_id=journal.task_id, root=journal.root)
    assert loaded.plan_digest and len(loaded.finished_signed()) == 1
    receipt = loaded.finished_signed()[0].receipt
    assert receipt["verification_status"] == "VERIFIED"
    assert receipt["observation_type"] == "post_state"
    assert receipt["observation_ref"] == "bcc.v2.verification"


@pytest.mark.parametrize("write,content", [(False, ""), (True, "wrong-bytes")],
                         ids=["success-text-without-effect", "file-exists-wrong-bytes"])
def test_g02_success_text_or_wrong_bytes_cannot_complete(bcc_live, tmp_path, write, content):
    journal, result = _approved_bcc_run(bcc_live, tmp_path, _bcc_plan(bcc_live, content, write=write))
    assert not result.completed and result.blocked_at == "create"
    target = bcc_live.work / "golden.bin"
    assert target.read_bytes() == b"wrong-bytes" if write else not target.exists()
    assert not TaskJournal.load(task_id=journal.task_id, root=journal.root).finished_signed()


class _LocalAppendService:
    """Test-only typed effect; durable ledger intentionally has NO deduplication.

    Replaying an effect produces another line, exposing unsafe retry directly.
    Authority here is a fixed temp-directory fixture, not production policy.
    """

    def __init__(self, root: Path, crash: str):
        self.root, self.crash = root, crash

    def authorize(self, action, context):
        return PolicyDecision(action.action_type == "golden.append"
                              and action.args["target"] == str(self.root / "effects.jsonl"))

    def request(self, action, policy, context):
        return ApprovalDecision(False, reason="fixture grants no additional authority")

    def supports(self, action_type):
        return action_type == "golden.append"

    def execute(self, action):
        started = datetime.now(timezone.utc)
        # Binary mode and fsync make the ledger independent of a dying process.
        with (self.root / "effects.jsonl").open("ab") as stream:
            stream.write(json.dumps({"effect": action.args["effect"]}).encode() + b"\n")
            stream.flush()
            os.fsync(stream.fileno())
        if self.crash == "after-effect" and action.args["effect"].endswith("/s2"):
            os._exit(86)  # no unwind, no receipt, no journal failure handler
        return ExecutionReceipt(action.action_type, started, datetime.now(timezone.utc),
                                effect_id=action.args["effect"])

    def observe_fresh(self, action, receipt):
        # Actual file reopen after the effect; no copied executor metadata.
        rows = [json.loads(x) for x in (self.root / "effects.jsonl").read_bytes().splitlines()]
        count = sum(row["effect"] == action.args["effect"] for row in rows)
        return Observation(datetime.now(timezone.utc), "fs", {"count": count})

    def verify(self, action, receipt, observation):
        return VerificationResult(observation.state["count"] == 1,
                                  reason="external append count must equal one")


def _worker(root: Path, mode: str, seed: int):
    """Runs in a fresh interpreter, including on every resume."""
    root.mkdir(parents=True, exist_ok=True)
    service = _LocalAppendService(root, mode)
    plan = [PlanStep(sid, f"append {sid}", TypedAction(
        "golden.append", {"target": str(root / "effects.jsonl"), "effect": f"{seed}/{sid}"},
        side_effect=SideEffectClass.IRREVERSIBLE)) for sid in ("s1", "s2", "s3")]
    journal_root = root / "journals"
    journal = (TaskJournal.load(task_id="golden-crash", root=journal_root)
               if mode == "resume" else TaskJournal.start(task_id="golden-crash", root=journal_root,
                                                           plan=[(s.step_id, s.intent) for s in plan]))

    def before_step(action):
        if mode == "after-checkpoint" and action.args["effect"].endswith("/s2"):
            # s1 has been signed and persisted; s2 has not begun dispatch.
            os._exit(86)

    agent = UniversalComputerAgent(service, service, service, service, service)
    result = CompoundRunner(agent, journal, model="deterministic-fixture").run(
        plan, {"before_step": before_step})
    print(json.dumps(asdict(result)), flush=True)


def _process(root: Path, mode: str, seed: int = 0):
    # Explicit import path avoids relying on installed editable-checkout order.
    code = ("import importlib.util, pathlib, sys; "
            "s=importlib.util.spec_from_file_location('golden_fixture',sys.argv[1]); "
            "m=importlib.util.module_from_spec(s); s.loader.exec_module(m); "
            "m._worker(pathlib.Path(sys.argv[2]),sys.argv[3],int(sys.argv[4]))")
    project = Path(__file__).resolve().parents[2]
    env = dict(os.environ)
    env["PYTHONPATH"] = os.pathsep.join([str(project), str(project / "bossman-core"),
                                         str(project / "command-center"), env.get("PYTHONPATH", "")])
    return subprocess.run([sys.executable, "-c", code, str(Path(__file__).resolve()),
                           str(root), mode, str(seed)], env=env, capture_output=True,
                          text=True, timeout=30, check=False)


def _ledger(root):
    return [json.loads(row)["effect"] for row in (root / "effects.jsonl").read_bytes().splitlines()]


def _resume(root, seed=0):
    child = _process(root, "resume", seed)
    assert child.returncode == 0, child.stderr
    return json.loads(child.stdout)


def test_g06_actual_process_restart_preserves_verified_irreversible_effect(tmp_path):
    root = tmp_path / "external"
    child = _process(root, "after-checkpoint")
    assert child.returncode == 86, child.stderr
    assert _ledger(root) == ["0/s1"]
    journal = TaskJournal.load(task_id="golden-crash", root=root / "journals")
    assert [s.step_id for s in journal.finished_signed()] == ["s1"]
    signed_before = journal.finished_signed()[0].signed_record(journal.task_id)
    resumed = _resume(root)
    assert resumed["completed"] and resumed["executed"] == ["s2", "s3"]
    assert _ledger(root) == ["0/s1", "0/s2", "0/s3"]
    assert _resume(root)["executed"] == []
    reloaded = TaskJournal.load(task_id="golden-crash", root=root / "journals")
    assert reloaded.finished_signed()[0].signed_record(reloaded.task_id) == signed_before
    assert _ledger(root) == ["0/s1", "0/s2", "0/s3"]


@pytest.mark.parametrize("seed", range(10))
def test_g07_death_after_effect_before_receipt_never_resends(tmp_path, seed):
    root = tmp_path / "external"
    child = _process(root, "after-effect", seed)
    assert child.returncode == 86, child.stderr
    expected = [f"{seed}/s1", f"{seed}/s2"]
    assert _ledger(root) == expected
    journal = TaskJournal.load(task_id="golden-crash", root=root / "journals")
    assert [s.step_id for s in journal.finished_signed()] == ["s1"]
    ambiguous = journal.steps[1]
    assert ambiguous.in_flight and ambiguous.receipt is None and ambiguous.attempt_id
    for _ in range(2):
        resumed = _resume(root, seed)
        assert not resumed["completed"] and resumed["blocked_at"] == "s2"
        assert "unknown previous effect" in resumed["reason"]
        assert resumed["executed"] == []
        assert _ledger(root) == expected


@pytest.mark.parametrize("attack", ["receipt", "task-id", "expectation"])
def test_redteam_resume_tampering_never_dispatches(tmp_path, attack):
    root = tmp_path / "external"
    assert _process(root, "after-checkpoint").returncode == 86
    path = root / "journals" / "golden-crash.json"
    data = json.loads(path.read_text())
    if attack == "receipt":
        data["steps"][0]["receipt"]["verification_status"] = "FORGED"
    elif attack == "task-id":
        data["task_id"] = "another-mission"
    else:
        data["plan_digest"] = "0" * 64
    path.write_text(json.dumps(data))
    child = _process(root, "resume")
    # The authenticated envelope now rejects every mutation at load, before
    # CompoundRunner can inspect a changed plan or dispatch an effect.
    assert child.returncode != 0 and "JournalIntegrityError" in child.stderr
    with pytest.raises(JournalIntegrityError):
        TaskJournal.load(task_id="golden-crash", root=root / "journals")
    assert _ledger(root) == ["0/s1"]


def test_redteam_clearing_unsigned_inflight_cannot_replay_irreversible_effect(tmp_path):
    root = tmp_path / "external"
    assert _process(root, "after-effect").returncode == 86
    path = root / "journals" / "golden-crash.json"
    data = json.loads(path.read_text())
    data["steps"][1]["in_flight"] = False
    path.write_text(json.dumps(data))
    tampered_bytes = path.read_bytes()
    for _ in range(2):
        child = _process(root, "resume")
        assert child.returncode != 0 and "JournalIntegrityError" in child.stderr
        with pytest.raises(JournalIntegrityError):
            TaskJournal.load(task_id="golden-crash", root=root / "journals")
        assert path.read_bytes() == tampered_bytes, "rejected journal must remain available for reconciliation"
        assert _ledger(root) == ["0/s1", "0/s2"], "unsigned in_flight edit replayed irreversible effect"
