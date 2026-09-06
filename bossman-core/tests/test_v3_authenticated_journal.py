"""E4-RT-001: real process death and independent irreversible-effect oracle."""
from __future__ import annotations
from dataclasses import asdict
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import subprocess
import sys
import pytest
from bossman_v3.computer_agent.agent import UniversalComputerAgent
from bossman_v3.contracts import (ApprovalDecision, ExecutionReceipt, Observation,
    PolicyDecision, SideEffectClass, TypedAction, VerificationResult)
from bossman_v3.execution import CompoundRunner, PlanStep
from bossman_v3.memory import TaskJournal
from bossman_v3.memory.journal import JournalIntegrityError

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


@pytest.mark.parametrize("attack", ["in_flight", "reset_pending", "remove_step", "plan",
                                  "strip_signature", "legacy_schema", "execution_binding"])
def test_cleared_inflight_cannot_repeat_irreversible_effect(tmp_path, attack):
    root = tmp_path / "external"
    assert _process(root, "after-effect").returncode == 86
    assert _ledger(root) == ["0/s1", "0/s2"]
    path = root / "journals" / "golden-crash.json"
    data = json.loads(path.read_text())
    if attack == "in_flight":
        data["steps"][1]["in_flight"] = False
    elif attack == "reset_pending":
        data["steps"][1].update(status="PENDING", in_flight=False, attempt_id="", effect_key="", by="")
    elif attack == "remove_step":
        data["steps"].pop(1)
    elif attack == "plan":
        data["plan_digest"] = "0" * 64
    elif attack == "strip_signature":
        for key in ("sig", "signer", "nonce", "issued_at"):
            data.pop(key, None)
    elif attack == "legacy_schema":
        data["schema_version"] = 2
    else:
        data["execution_binding"] = {"fence": 999}
    path.write_text(json.dumps(data))
    child = _process(root, "resume")
    assert _ledger(root) == ["0/s1", "0/s2"], child.stdout + child.stderr
    assert child.returncode != 0 and "JournalIntegrityError" in child.stderr


def test_untouched_ambiguous_effect_stays_blocked_across_processes(tmp_path):
    root = tmp_path / "external"
    assert _process(root, "after-effect").returncode == 86
    for _ in range(2):
        resumed = _resume(root)
        assert not resumed["completed"] and resumed["blocked_at"] == "s2"
        assert "unknown previous effect" in resumed["reason"]
        assert _ledger(root) == ["0/s1", "0/s2"]


def test_checkpoint_resume_preserves_completed_steps_and_finishes_once(tmp_path):
    root = tmp_path / "external"
    assert _process(root, "after-checkpoint").returncode == 86
    assert _ledger(root) == ["0/s1"]
    before = TaskJournal.load(task_id="golden-crash", root=root / "journals")
    signed = before.finished()[0].signed_record(before.task_id)
    resumed = _resume(root)
    assert resumed["completed"] and resumed["executed"] == ["s2", "s3"]
    assert _resume(root)["executed"] == []
    after = TaskJournal.load(task_id="golden-crash", root=root / "journals")
    assert after.finished()[0].signed_record(after.task_id) == signed
    assert _ledger(root) == ["0/s1", "0/s2", "0/s3"]


def test_pending_snapshot_is_authenticated_and_legacy_is_not_auto_migrated(tmp_path):
    j = TaskJournal.start(task_id="pending", plan=[("s1", "do work")], root=tmp_path)
    assert TaskJournal.load(task_id=j.task_id, root=tmp_path).next_step().step_id == "s1"
    path = tmp_path / "pending.json"
    raw = json.loads(path.read_text())
    raw["steps"][0]["intent"] = "replaced work"
    path.write_text(json.dumps(raw))
    with pytest.raises(JournalIntegrityError):
        TaskJournal.load(task_id=j.task_id, root=tmp_path)


def test_same_process_started_flag_contradiction_is_rejected(tmp_path):
    from dataclasses import replace
    j = TaskJournal.start(task_id="contradiction", plan=[("s1", "do work")], root=tmp_path)
    j.begin("s1")
    j.steps[0] = replace(j.steps[0], in_flight=False)
    with pytest.raises(JournalIntegrityError, match="in-flight"):
        j.validate()


@pytest.mark.parametrize("variant", ["unsigned-v2", "wrong-signer", "task-transplant"])
def test_untrusted_snapshot_requires_reconciliation_without_rewrite(tmp_path, variant):
    from bossman_v3 import evidence
    TaskJournal.start(task_id="original", plan=[("s1", "do work")], root=tmp_path)
    path = tmp_path / "original.json"
    raw = json.loads(path.read_text())
    target = "original"
    if variant == "unsigned-v2":
        raw["schema_version"] = 2
        for key in ("sig", "signer", "nonce", "issued_at", "record_type"):
            raw.pop(key, None)
    elif variant == "wrong-signer":
        raw.update(evidence.sign_fields(raw, signer=evidence.VERIFIER_SIGNER))
    else:
        target = "other"
        path = tmp_path / "other.json"
    path.write_text(json.dumps(raw))
    before = path.read_bytes()
    with pytest.raises(JournalIntegrityError):
        TaskJournal.load(task_id=target, root=tmp_path)
    assert path.read_bytes() == before
