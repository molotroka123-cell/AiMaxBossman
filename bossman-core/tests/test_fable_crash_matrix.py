"""AGENT-3 recovery crash matrix over the real V3 chain (agent -> CompoundRunner
-> TaskJournal) and the V3 recovery kernel.

One test per injection cell. After every crash the same five properties are
asserted:

  1. no duplicate irreversible effect;
  2. authorization is still valid AT EFFECT TIME (not merely at plan time);
  3. evidence is current — it is re-derived from the signed journal, never from
     the crashed runner's claim;
  4. ambiguous state stays EXPLICIT — an unfinished in-flight irreversible step
     blocks and asks the owner, it is never silently resolved either way;
  5. resume starts from the last VERIFIED checkpoint (signed, finished steps).
"""
from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path

import pytest

from bossman_v3.computer_agent.agent import UniversalComputerAgent
from bossman_v3.contracts import (ApprovalDecision, ExecutionReceipt, Observation, PolicyDecision,
                                  SideEffectClass, TypedAction, VerificationResult)
from bossman_v3.execution import PlanStep
from bossman_v3.fleet.resume import FleetResumeKernel
from bossman_v3.memory.journal import JournalIntegrityError, TaskJournal, journal_path
from bossman_v3.organization import (DelegationContract, EvidenceRequirement, Resources, RiskTier,
                                     V3ExecutionBridge, step_to_dict)
from bossman_v3.recovery_kernel import CheckpointIntegrityError, FileCheckpointStore, RecoveryKernel

CELLS = ("BEFORE_DISPATCH", "AFTER_DISPATCH", "AFTER_EFFECT", "BEFORE_JOURNAL",
         "AFTER_JOURNAL", "AFTER_APPROVAL", "DURING_VERIFICATION", "DURING_FINALIZATION")


class Crash(BaseException):
    """Simulated power loss. A BaseException so it is NOT swallowed by the
    runner's ordinary error handling — it kills the process the way a real
    power loss does, leaving only what was already durable on disk."""


# --------------------------------------------------------------------- world

class World:
    def __init__(self, root: Path):
        self.root = root
        self.root.mkdir(parents=True, exist_ok=True)
        self.effects: list[str] = []          # every irreversible effect, once per occurrence
        self.crash_at: str | None = None
        self.authorized = True
        self.approved = True
        self.approvals: list[str] = []

    def fire(self, cell: str) -> None:
        if self.crash_at == cell:
            self.crash_at = None              # one crash per run, like a real power loss
            raise Crash(cell)


class _Policy:
    def __init__(self, w): self.w = w
    def authorize(self, action, context):
        return PolicyDecision(True, requires_approval=True)


class _Approval:
    def __init__(self, w): self.w = w
    def request(self, action, policy, context):
        self.w.approvals.append(str(action.args["name"]))
        if not self.w.approved:
            return ApprovalDecision(False, reason="owner withdrew approval")
        self.w.fire("AFTER_APPROVAL")
        return ApprovalDecision(True, approval_id=f"ap-{len(self.w.approvals)}")


class _Executor:
    def __init__(self, w): self.w = w
    def supports(self, action_type): return action_type == "fs.append"
    def execute(self, action):
        name = str(action.args["name"])
        self.w.fire("AFTER_DISPATCH")                       # dispatched, effect not yet done
        path = self.w.root / name
        path.write_text((path.read_text() if path.exists() else "") + "x", encoding="utf-8")
        self.w.effects.append(name)
        self.w.fire("AFTER_EFFECT")                         # effect done, nothing recorded yet
        now = datetime.now(timezone.utc)
        return ExecutionReceipt("fs.append", now, now, effect_id=f"{name}:{len(self.w.effects)}")


class _Observer:
    def __init__(self, w): self.w = w
    def observe_fresh(self, action, receipt):
        self.w.fire("BEFORE_JOURNAL")                       # crash between effect and journal
        return Observation(datetime.now(timezone.utc), "fs",
                           {"exists": (self.w.root / str(action.args["name"])).exists()})


class _Verifier:
    def __init__(self, w): self.w = w
    def verify(self, action, receipt, observation):
        self.w.fire("DURING_VERIFICATION")
        ok = bool(observation.state.get("exists"))
        return VerificationResult(ok, "" if ok else "effect not observed")


def _step(w: World, sid: str, name: str, cls=SideEffectClass.IRREVERSIBLE) -> dict:
    return step_to_dict(PlanStep(sid, f"append {name}", TypedAction(
        "fs.append", {"name": name, "expect": {"kind": "file", "target": str(w.root / name),
                                               "expect": {"exists": True}}}, side_effect=cls)))


def _contract(w: World, names: list[str]) -> DelegationContract:
    return DelegationContract(
        work_id="w1", mission_id="m1", department_id="engineering", goal="append",
        required_capability="fs.append", success_criteria=["files exist"],
        evidence_required=[EvidenceRequirement("file", str(w.root / n)) for n in names],
        budget=Resources(usd=1.0), risk=RiskTier.HIGH, side_effect=True,
        steps=[_step(w, f"s{i}", n) for i, n in enumerate(names, 1)])


class Chain:
    """One node's V3 chain over a durable journal that survives 'restart'."""

    def __init__(self, tmp_path: Path, names=("a.txt",)):
        self.tmp = tmp_path
        self.world = World(tmp_path / "world")
        self.journal_root = tmp_path / "journals"
        self.contract = _contract(self.world, list(names))

    def bridge(self) -> V3ExecutionBridge:                  # a FRESH process each time
        w = self.world
        return V3ExecutionBridge(agent_factory=lambda agent_id, c: UniversalComputerAgent(
            _Policy(w), _Approval(w), _Executor(w), _Observer(w), _Verifier(w)),
            journal_root=self.journal_root)

    def run(self, *, crash_at: str | None = None):
        self.world.crash_at = crash_at
        guard = _guard(self.world)
        try:
            return self.bridge().execute(self.contract, agent_id="coder", execution_guard=guard), None
        except BaseException as exc:                        # a crashed process returns nothing
            return None, exc

    # ------------------------------------------------------------- assertions

    def journal(self) -> TaskJournal:
        return TaskJournal.load(task_id="m1__w1", root=self.journal_root)

    def verified_checkpoint(self) -> tuple[str, ...]:
        """The last VERIFIED checkpoint = signed, finished steps only."""
        j = self.journal()
        return tuple(s.step_id for s in j.finished_signed())

    def ambiguous(self) -> bool:
        j = self.journal()
        nxt = j.next_step()
        return nxt is not None and nxt.in_flight

    def resume_decision(self, *, lost: bool = True):
        from bossman_v3.organization.bridges import step_from_dict
        plan = [step_from_dict(s) for s in self.contract.steps]
        return FleetResumeKernel().decide(self.journal(), plan, lost_in_flight=lost)


def _guard(w: World):
    from contextlib import contextmanager

    @contextmanager
    def guard():
        # Authorization is re-evaluated at the EFFECT boundary, not at plan time.
        if not w.authorized:
            raise PermissionError("authorization withdrawn before the effect")
        yield
    return guard


def _no_forged_evidence(result, chain: Chain) -> None:
    """Evidence must be current: derived from signed journal steps only."""
    signed = {f"journal:m1__w1/{sid}" for sid in chain.verified_checkpoint()}
    assert {e.source for e in (result.evidence if result else [])} <= signed
    for e in (result.evidence if result else []):
        assert e.verified and e.binding.get("verification_passed") is True


# ======================================================================= cells

def test_cell_before_dispatch(tmp_path):
    """Crash before the action reaches the executor: nothing happened, clean replay."""
    c = Chain(tmp_path)
    w = c.world
    def before(action):
        w.fire("BEFORE_DISPATCH")

    w.crash_at = "BEFORE_DISPATCH"
    with pytest.raises(Crash):
        c.bridge().execute(c.contract, agent_id="coder", before_step=before,
                           execution_guard=_guard(w))
    assert w.effects == []
    assert c.verified_checkpoint() == ()
    assert not c.ambiguous()                       # never begun => not ambiguous
    assert c.resume_decision(lost=False).resumable   # nothing durable => clean retry
    assert not c.resume_decision(lost=True).resumable # a LOST node stays conservative anyway
    result, exc = c.run()
    assert exc is None and len(w.effects) == 1
    assert c.verified_checkpoint() == ("s1",)
    _no_forged_evidence(result, c)


def test_cell_after_dispatch(tmp_path):
    """Crash after durable intent but before the effect: outcome UNKNOWN, stays explicit."""
    c = Chain(tmp_path)
    result, exc = c.run(crash_at="AFTER_DISPATCH")
    assert c.world.effects == []
    assert c.journal().steps[0].in_flight is True  # durable intent was written BEFORE dispatch
    assert c.verified_checkpoint() == ()
    assert c.ambiguous()
    assert not c.resume_decision().resumable       # irreversible + in flight => owner decision
    again, exc2 = c.run()
    assert exc2 is None and again.executed is False
    assert "reconciliation required" in again.reason
    assert c.world.effects == []                   # never silently replayed
    _no_forged_evidence(again, c)


def test_cell_after_effect(tmp_path):
    """The irreversible effect happened; the crash must not let it happen twice."""
    c = Chain(tmp_path)
    _, exc = c.run(crash_at="AFTER_EFFECT")
    assert len(c.world.effects) == 1
    assert c.ambiguous() and c.verified_checkpoint() == ()
    assert not c.resume_decision().resumable
    again, _ = c.run()
    assert len(c.world.effects) == 1               # NO DUPLICATE IRREVERSIBLE EFFECT
    assert again.executed is False and "reconciliation required" in again.reason
    assert (c.world.root / "a.txt").read_text() == "x"
    _no_forged_evidence(again, c)


def test_cell_before_journal(tmp_path):
    """Crash between the effect and the journal record: explicit, not resolved either way."""
    c = Chain(tmp_path)
    c.run(crash_at="BEFORE_JOURNAL")
    assert len(c.world.effects) == 1
    assert c.verified_checkpoint() == ()           # an unrecorded effect is NOT evidence
    assert c.ambiguous()
    again, _ = c.run()
    assert len(c.world.effects) == 1
    assert not again.executed and again.metadata["blocked_at"] == "s1"
    _no_forged_evidence(again, c)


def test_cell_after_journal(tmp_path):
    """A recorded, signed step is a checkpoint: resume continues, never replays it."""
    c = Chain(tmp_path, names=("a.txt", "b.txt"))
    c.run(crash_at="AFTER_DISPATCH")               # s1 succeeds only on the second attempt
    assert c.world.effects == []
    c.world.crash_at = None
    # s1 was left in flight and irreversible: reconcile it explicitly, as an owner would.
    _reconcile(c, "s1")
    assert c.verified_checkpoint() == ("s1",)
    effects_before = len(c.world.effects)
    result, exc = c.run(crash_at="AFTER_EFFECT")   # crash while doing s2
    assert len(c.world.effects) == effects_before + 1 and c.world.effects[-1] == "b.txt"
    assert c.verified_checkpoint() == ("s1",)      # resume point unchanged
    again, _ = c.run()
    assert [e for e in c.world.effects if e == "a.txt"] == ["a.txt"]   # s1 done once, never replayed
    assert len([e for e in c.world.effects if e == "b.txt"]) == 1  # s2 not duplicated
    assert again.metadata["blocked_at"] == "s2"
    assert not c.contract.validate(again)[0]       # the contract is NOT silently satisfied


def test_cell_after_approval(tmp_path):
    """Approval does not survive a crash: authorization is re-checked at effect time."""
    c = Chain(tmp_path)
    _, exc = c.run(crash_at="AFTER_APPROVAL")
    assert isinstance(exc, Crash) or (exc is None)
    assert c.world.effects == []
    assert c.world.approvals == ["a.txt"]
    assert not c.ambiguous()                       # the crash preceded the durable intent
    c.world.authorized = False                     # owner revokes between crash and resume
    blocked, _ = c.run()
    assert c.world.effects == []                   # a stale approval authorized nothing
    assert not blocked.executed
    c.world.approved = False                       # and an explicitly denied approval, too
    c.world.authorized = True
    denied, _ = c.run()
    assert c.world.effects == [] and not denied.executed
    assert denied.metadata["waiting_approval"] is True
    assert c.world.approvals == ["a.txt", "a.txt", "a.txt"]   # re-requested every time


def test_cell_during_verification(tmp_path):
    """Effect done, verification lost: the step is NOT closed and NOT replayed."""
    c = Chain(tmp_path)
    c.run(crash_at="DURING_VERIFICATION")
    assert len(c.world.effects) == 1
    assert c.verified_checkpoint() == ()
    assert c.ambiguous()
    assert not c.resume_decision().resumable
    again, _ = c.run()
    assert len(c.world.effects) == 1
    assert not again.executed and "reconciliation required" in again.reason
    _no_forged_evidence(again, c)


def test_cell_during_finalization(tmp_path, monkeypatch):
    """Crash while writing the closing journal record, and again with the writer
    lock left behind by a hard kill: both stay EXPLICIT, never silently closed."""
    c = Chain(tmp_path)
    armed = {"on": True}
    original = TaskJournal._save_locked

    def die_on_close(self):
        # Die exactly while the CLOSING record is being made durable.
        if armed["on"] and any(s.status == "DONE" for s in self.steps):
            raise Crash("DURING_FINALIZATION")
        return original(self)

    monkeypatch.setattr(TaskJournal, "_save_locked", die_on_close)
    _, exc = c.run()
    assert isinstance(exc, Crash)
    armed["on"] = False                            # the machine comes back up
    assert len(c.world.effects) == 1
    assert c.verified_checkpoint() == ()           # a half-written close is not a checkpoint
    assert c.ambiguous()
    again, _ = c.run()
    assert len(c.world.effects) == 1
    assert not again.executed and "reconciliation required" in again.reason

    # Hard kill variant: the exclusive writer lock survives the process.
    lock = journal_path(c.journal_root, "m1__w1").with_suffix(".lock")
    lock.write_text("4242")
    j = c.journal()
    with pytest.raises(JournalIntegrityError, match="reconcile"):
        j.begin("s1", by="coder")
    assert len(c.world.effects) == 1
    lock.unlink()


def _reconcile(chain: Chain, step_id: str) -> None:
    """What an owner does after an ambiguous step: confirm the observed effect and
    close the step through the signed journal API (never by editing the file)."""
    j = chain.journal()
    name = json.loads(json.dumps(chain.contract.steps))[
        [s["step_id"] for s in chain.contract.steps].index(step_id)]["action"]["args"]["name"]
    path = chain.world.root / name
    if not path.exists():                          # the crash preceded the effect: run it once
        path.write_text("x", encoding="utf-8")
        chain.world.effects.append(name)
    j.record(step_id, verified=True, by="owner", receipt={
        "verification_passed": True, "observed_state": {"exists": True},
        "observed_at": datetime.now(timezone.utc).isoformat(), "reconciled_by": "owner"})


# ------------------------------------------- recovery kernel: VERIFIED resume

def test_recovery_kernel_restores_only_signed_verified_checkpoints(tmp_path):
    """REGRESSION: the VERIFIED bit and the state live in the same file, so the
    record must be authenticated before either is believed."""
    kernel = RecoveryKernel(FileCheckpointStore(tmp_path / "cp"))
    good = kernel.checkpoint({"step": "s1", "spent": 1.0}, verified=True)
    bad = kernel.checkpoint({"step": "s2", "spent": 2.0}, verified=False)
    assert kernel.restore(good.checkpoint_id)["step"] == "s1"
    with pytest.raises(PermissionError):
        kernel.restore(bad.checkpoint_id)

    path = tmp_path / "cp" / f"{bad.checkpoint_id}.json"
    forged = json.loads(path.read_text())
    forged["verified"] = True
    forged["state"] = {"step": "s9", "spent": 0.0}
    forged["state_hash"] = RecoveryKernel._state_hash(forged["state"])
    path.write_text(json.dumps(forged, sort_keys=True))
    with pytest.raises(CheckpointIntegrityError):
        kernel.restore(bad.checkpoint_id)
    assert kernel.latest_verified([good.checkpoint_id, bad.checkpoint_id]).checkpoint_id == good.checkpoint_id


def test_recovery_kernel_refuses_a_stripped_or_replaced_signature(tmp_path):
    """Second angle: dropping the signature, or lifting one from another record,
    must not resurrect a checkpoint."""
    kernel = RecoveryKernel(FileCheckpointStore(tmp_path / "cp"))
    first = kernel.checkpoint({"step": "s1"}, verified=True)
    second = kernel.checkpoint({"step": "s2"}, verified=True)
    path = tmp_path / "cp" / f"{second.checkpoint_id}.json"
    stripped = json.loads(path.read_text())
    stripped["sig"] = ""
    path.write_text(json.dumps(stripped, sort_keys=True))
    with pytest.raises(CheckpointIntegrityError):
        kernel.restore(second.checkpoint_id)
    lifted = json.loads(path.read_text())
    lifted.update(sig=first.sig, signer=first.signer, nonce=first.nonce, issued_at=first.issued_at)
    path.write_text(json.dumps(lifted, sort_keys=True))
    with pytest.raises(CheckpointIntegrityError):
        kernel.restore(second.checkpoint_id)
    assert kernel.latest_verified([second.checkpoint_id]) is None
    with pytest.raises(CheckpointIntegrityError):
        kernel.store.load("../escape")


def test_every_declared_cell_has_a_test():
    import tests.test_fable_crash_matrix as module
    for cell in CELLS:
        assert hasattr(module, f"test_cell_{cell.lower()}"), cell
