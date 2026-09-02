"""Verification, shadow replay, promotion and rollback over Learning Guard."""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(Path(__file__).resolve().parent))

from learning import trace  # noqa: E402
from bossman.apprentice import flags  # noqa: E402
from bossman.apprentice.errors import FlagDisabled, VerificationFailed  # noqa: E402
from bossman.apprentice.models import ApprenticeTask  # noqa: E402
from bossman.apprentice.recording import ApprenticeMemory, EpisodeRecorder, skill_schema  # noqa: E402
from bossman.apprentice.skills import (EvidenceBinding, SelfVerificationRefused, SkillDegraded, SkillPromoter,  # noqa: E402
                                       ab_results_from_replays, attach_verification, degrade_skill, generalize,
                                       match_skill, plan_from_skill, shadow_replay)
from bossman.deep_fix import Evidence, Principal  # noqa: E402
from bossman.learning_guard.models import SecuritySnapshot  # noqa: E402
from bossman.learning_guard.promotion import MIN_SHADOW_RUNS  # noqa: E402
from fixtures.apprentice.sim import Element, SimObserver  # noqa: E402
import test_apprentice_core as core  # noqa: E402

PRODUCER = Principal("apprentice:planner", model_id="planner:sim", role="coder", run_id="run_1", independence_class="same_run")
VERIFIER = Principal("verifier:pytest", model_id="pytest", role="verifier", run_id="run_verify", independence_class="external_tool")
NOW = 5_000.0


@pytest.fixture
def on(monkeypatch):
    for f in (flags.MASTER, flags.SKILL_RECORDING, flags.SKILL_SHADOW_REPLAY, flags.SKILL_PROMOTION):
        monkeypatch.setenv(f, "1")


def _episode(n: int = 0, world=None):
    w = world or core._world()
    task = ApprenticeTask(task_id=f"ep_task_{n}", goal="save a note", run_id=f"run_{n}", session_id="sess",
                          head_sha="abc123", environment="env:notes-1.0", task_type="notes.save")
    rec = EpisodeRecorder(task=task, agent="apprentice", model="planner:sim", principal_id=PRODUCER.principal_id, app="Notes", app_version="1.0")
    eng, _, _ = core._engine(w, on_record=rec.on_record)
    res = eng.run(task)
    assert res.ok
    return task, rec.finish(res)


def _evidence(task_id: str, run_id: str, *, passed=True, principal="verifier:pytest", at=NOW - 10, head="abc123"):
    return Evidence(kind="observation", detail="checkpoint saved re-observed", passed=passed, source="verifier:pytest", at=at,
                    collected_at=at + 1, task_id=task_id, run_id=run_id, principal_id=principal, environment="env:notes-1.0",
                    head_sha=head, expected="summary == Saved", actual="summary == Saved")


def _verified_episode(n: int = 0):
    task, ep = _episode(n)
    binding = EvidenceBinding(task.task_id, task.run_id, "abc123", "env:notes-1.0")
    return task, attach_verification(ep, producer=PRODUCER, verifier=VERIFIER, evidence=[_evidence(task.task_id, task.run_id)],
                                     binding=binding, now=NOW)


def _skill(episodes):
    return generalize(episodes, skill_id="skill_notes_save", title="save a note in Notes", task_type="notes.save",
                      environment="env:notes-1.0", app="Notes", app_version="1.0", agent="apprentice", model="planner:sim",
                      principal_id=PRODUCER.principal_id, head_sha="abc123")


def _verify_skill(skill):
    """Skill evidence binds to the skill id + HEAD (the store's evidence invariant), observed by the independent verifier."""
    return attach_verification(skill, producer=PRODUCER, verifier=VERIFIER, evidence=[_evidence("skill_notes_save", "")],
                               binding=EvidenceBinding("skill_notes_save", "", "abc123", "env:notes-1.0"), now=NOW)


# ---------------------------------------------------------------- verification
def test_self_verification_is_refused(on):
    task, ep = _episode()
    binding = EvidenceBinding(task.task_id, task.run_id, "abc123", "env:notes-1.0")
    with pytest.raises(SelfVerificationRefused):
        attach_verification(ep, producer=PRODUCER, verifier=PRODUCER, evidence=[_evidence(task.task_id, task.run_id)], binding=binding, now=NOW)
    same_run = Principal("verifier:other", model_id="x", role="verifier", run_id="run_1", independence_class="external_tool")
    with pytest.raises(SelfVerificationRefused):
        attach_verification(ep, producer=PRODUCER, verifier=same_run, evidence=[_evidence(task.task_id, task.run_id)], binding=binding, now=NOW)
    with pytest.raises(SelfVerificationRefused):       # evidence observed by the producer itself
        attach_verification(ep, producer=PRODUCER, verifier=VERIFIER,
                            evidence=[_evidence(task.task_id, task.run_id, principal=PRODUCER.principal_id)], binding=binding, now=NOW)


def test_evidence_must_be_fresh_and_bound_to_task_run_head(on):
    task, ep = _episode()
    binding = EvidenceBinding(task.task_id, task.run_id, "abc123", "env:notes-1.0")
    for bad in (_evidence("other_task", task.run_id), _evidence(task.task_id, "other_run"),
                _evidence(task.task_id, task.run_id, head="deadbeef"), _evidence(task.task_id, task.run_id, at=NOW - 10 * 24 * 3600),
                _evidence(task.task_id, task.run_id, passed=False)):
        with pytest.raises(VerificationFailed):
            attach_verification(ep, producer=PRODUCER, verifier=VERIFIER, evidence=[bad], binding=binding, now=NOW)
    good = attach_verification(ep, producer=PRODUCER, verifier=VERIFIER, evidence=[_evidence(task.task_id, task.run_id)], binding=binding, now=NOW)
    assert good["learning_status"] == "VERIFIED" and trace.validate(good, schema=skill_schema()) == []


# ---------------------------------------------------------------- generalization + matching
def test_generalized_skill_is_semantic_and_unverified_until_verified(on, tmp_path):
    _, ep = _verified_episode()
    skill = _skill([ep])
    assert skill["learning_status"] == "UNVERIFIED" and skill["skill_state"] == "CANDIDATE"
    assert [a["kind"] for a in skill["semantic_actions"]] == ["TYPE", "CLICK"] and skill["expected_outcomes"] == ["saved"]
    assert all("x" not in a["target"] for a in skill["semantic_actions"])
    mem = ApprenticeMemory(tmp_path / "mem")
    stored = mem.store_skill(skill)
    assert mem.skills() == [] and mem.skills(verified_only=False)[0]["skill_id"] == "skill_notes_save"
    obs = SimObserver(core._world()).observe()
    with pytest.raises(SkillDegraded):
        plan_from_skill(stored, ApprenticeTask.create("g", session_id="s"), obs)


def test_stale_skill_never_replayed_blindly(on):
    task, ep = _verified_episode()
    skill = _verify_skill(_skill([ep]))
    assert skill["skill_state"] == "SHADOW" and trace.validate(skill, schema=skill_schema()) == []
    fresh = SimObserver(core._world()).observe()
    assert match_skill(skill, fresh).state == "READY"
    plan = plan_from_skill(skill, ApprenticeTask.create("g", session_id="s"), fresh)
    assert plan.source == "skill:skill_notes_save" and plan.steps[-1].is_goal
    # UI changed: Save -> Store  => DEGRADED, adaptation required, no plan
    changed = core._world(); changed.elements[1] = Element("button", "Store")
    m = match_skill(skill, SimObserver(changed).observe())
    assert m.state == "DEGRADED" and m.unmatched == ["button:Save"]
    with pytest.raises(SkillDegraded):
        plan_from_skill(skill, ApprenticeTask.create("g", session_id="s"), SimObserver(changed).observe())
    other = core._world(); other.app = "Calc"
    assert match_skill(skill, SimObserver(other).observe()).state == "INAPPLICABLE"


def test_selector_drift_marks_skill_degraded(on, tmp_path):
    task, ep = _verified_episode()
    mem = ApprenticeMemory(tmp_path / "mem")
    skill = mem.store_skill(_verify_skill(_skill([ep])))
    changed = core._world(); changed.elements[1] = Element("button", "Store")
    m = match_skill(skill, SimObserver(changed).observe())
    d = degrade_skill(mem, skill, m.reason)
    assert d["skill_state"] == "DEGRADED" and d["version"] == 2 and d["supersedes_version"] == 1
    assert mem.skills()[0]["skill_state"] == "DEGRADED"
    with pytest.raises(SkillDegraded):
        plan_from_skill(mem.skills()[0], ApprenticeTask.create("g", session_id="s"), SimObserver(core._world()).observe())


# ---------------------------------------------------------------- shadow replay
def test_shadow_replay_is_dry_run_and_flagged(on, monkeypatch):
    task, ep = _verified_episode()
    skill = _skill([ep])
    w = core._world(); obs = SimObserver(w)
    monkeypatch.delenv(flags.SKILL_SHADOW_REPLAY)
    with pytest.raises(FlagDisabled):
        shadow_replay(skill, [obs.observe()])
    monkeypatch.setenv(flags.SKILL_SHADOW_REPLAY, "1")
    rep = shadow_replay(skill, [obs.observe(), obs.observe()])
    assert rep["ok"] and rep["screens"] == 2 and w.log == []          # nothing was clicked
    w2 = core._world(); w2.elements[1] = Element("button", "Store")
    rep2 = shadow_replay(skill, [SimObserver(w2).observe()])
    assert not rep2["ok"] and rep2["degraded_screens"] == 1


# ---------------------------------------------------------------- promotion + rollback
def test_promotion_goes_through_learning_guard_and_rollback_restores(on, tmp_path, monkeypatch):
    eps = [_verified_episode(i)[1] for i in range(3)]
    task0 = eps[0]["task_id"]
    mem = ApprenticeMemory(tmp_path / "mem")
    skill = mem.store_skill(_verify_skill(_skill(eps)))
    promoter = SkillPromoter(mem)
    # insufficient evidence: 2 episodes < 3
    assert promoter.evaluate(skill, eps[:2]).status == "CANDIDATE"
    cand = promoter.evaluate(skill, eps)
    assert cand.status == "SHADOW", cand.reasons
    # unverified episode (self-reported) is rejected as a source
    unverified = _episode(9)[1]
    weak = promoter.evaluate(skill, eps[:2] + [unverified])
    assert weak.status == "CANDIDATE" and any("rejected episode" in r for r in weak.reasons)
    replays = [{"task_id": f"notes.save-{i}", "ok": True} for i in range(MIN_SHADOW_RUNS)]
    ab = ab_results_from_replays("notes.save", replays, {f"notes.save-{i}": i % 2 == 0 for i in range(MIN_SHADOW_RUNS)})
    snap = SecuritySnapshot(leaks=0, bypasses=0, containment_rate=1.0)
    # shadow runs below the Learning Guard minimum -> not promoted
    _, few = promoter.promote(skill, cand, ab[:5], security_before=snap, security_after=snap, shadow_runs=5,
                              owner_approved=True, rollback_tested=True)
    assert few.status == "SHADOW" and mem.skills()[0]["skill_state"] == "SHADOW"
    # security regression -> QUARANTINED
    _, q = promoter.promote(skill, cand, ab, security_before=snap, security_after=SecuritySnapshot(leaks=1),
                            shadow_runs=MIN_SHADOW_RUNS, owner_approved=True, rollback_tested=True)
    assert q.status == "QUARANTINED"
    # without owner / rollback test -> not promoted
    _, no_owner = promoter.promote(skill, cand, ab, security_before=snap, security_after=snap, shadow_runs=MIN_SHADOW_RUNS,
                                   owner_approved=False, rollback_tested=True)
    assert no_owner.status != "PROMOTED"
    _, no_rb = promoter.promote(skill, cand, ab, security_before=snap, security_after=snap, shadow_runs=MIN_SHADOW_RUNS,
                                owner_approved=True, rollback_tested=False)
    assert no_rb.status != "PROMOTED" and "rollback" in no_rb.reasons[0]
    stored, final = promoter.promote(skill, cand, ab, security_before=snap, security_after=snap, shadow_runs=MIN_SHADOW_RUNS,
                                     owner_approved=True, rollback_tested=True)
    assert final.status == "PROMOTED" and stored["skill_state"] == "READY" and stored["version"] == 2
    assert stored["rollback"]["prev_ref"] == "skill_notes_save@v1"
    back = promoter.rollback("skill_notes_save", "owner: regression in production")
    assert back["version"] == 3 and back["skill_state"] == "SHADOW" and back["rollback"]["prev_ref"] == "skill_notes_save@v2"
    assert mem.skills()[0]["version"] == 3
    monkeypatch.delenv(flags.SKILL_PROMOTION)
    with pytest.raises(FlagDisabled):
        promoter.evaluate(skill, eps)
