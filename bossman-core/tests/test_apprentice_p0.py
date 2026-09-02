"""Audit P0 adversarial tests (failing-test-first): failed verification blocks the result;
write actions need an idempotency key AND a verified EffectReceipt; receipt action_type must
match; observations cannot come from the future and must be bound to task/run/session/action."""
from __future__ import annotations

import json
import sys
import time
from pathlib import Path

import jsonschema
import pytest

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(Path(__file__).resolve().parent))

from bossman.apprentice import flags  # noqa: E402
from bossman.apprentice.engine import DefaultVerifier, UniversalComputerApprentice  # noqa: E402
from bossman.apprentice.errors import UnverifiedEpisode  # noqa: E402
from bossman.apprentice.guards import SideEffectLedger  # noqa: E402
from bossman.apprentice.models import (ApprenticeState, ApprenticeTask, EffectReceipt, PlanStep, RiskClass, SemanticTarget,  # noqa: E402
                                       Verification)
from bossman.apprentice.recording import EpisodeRecorder  # noqa: E402
from bossman.apprentice.skills import generalize  # noqa: E402
from bossman.computer_operator.models import ActionKind, ExpectedState, Observation  # noqa: E402
from fixtures.apprentice.sim import SimActuator, SimObserver  # noqa: E402
import test_apprentice_core as core  # noqa: E402

SCHEMA = json.loads((ROOT / "schemas" / "apprentice_action_record.schema.json").read_text(encoding="utf-8"))


@pytest.fixture
def on(monkeypatch):
    monkeypatch.setenv(flags.MASTER, "1")


def _task(**kw):
    return ApprenticeTask.create("save a note", session_id="sess_1", run_id="run_1", head_sha="abc123",
                                 environment="env:notes-1.0", task_type="notes.save", **kw)


def _write_step(key: str | None, risk=RiskClass.REVERSIBLE_WRITE) -> PlanStep:
    kw = {"idempotency_key": key} if key is not None else {}
    return PlanStep("w1", ActionKind.CLICK, core.NOTES, SemanticTarget("button", "Save"), risk=risk, side_effecting=True,
                    expected=ExpectedState(contains_text="Saved"), checkpoint="saved", is_goal=True, **kw)


# ---------------------------------------------------------------- P0-1
def test_failed_verification_blocks_result_and_learning(on):
    w = core._world()
    task = _task(max_recoveries=2)
    rec = EpisodeRecorder(task=task, agent="a", model="m", principal_id="p", app="Notes")

    class NeverVerifier(DefaultVerifier):
        def verify(self, step, action, before, after):
            return Verification("always_false", False, "verifier says no", "verifier:x")
    planner = core.ScriptedPlanner(core._steps())
    eng = UniversalComputerApprentice(planner=planner, observer=SimObserver(w), actuator=SimActuator(w), verifier=NeverVerifier(),
                                      on_record=rec.on_record)
    res = eng.run(task)
    assert res.state is ApprenticeState.FAIL and "SUCCEED" not in [t[1] for t in eng.transitions]
    assert all(r.result == "verification_failed" for r in res.records if r.post_observation) and res.checkpoints_reached == []
    ep = rec.finish(res)
    assert ep["verified"] is False and ep["learning_status"] == "FAILED_EXPERIMENT"
    with pytest.raises(UnverifiedEpisode):
        generalize([ep], skill_id="s", title="t", task_type="notes.save", environment="e", app="Notes", app_version="1",
                   agent="a", model="m", principal_id="p", head_sha="abc123")


def test_clean_run_episode_is_marked_verified_by_default_verifier(on):
    w = core._world(); task = _task()
    rec = EpisodeRecorder(task=task, agent="a", model="m", principal_id="p", app="Notes")
    eng, _, _ = core._engine(w, on_record=rec.on_record)
    ep = rec.finish(eng.run(task))
    assert ep["verified"] is True


# ---------------------------------------------------------------- P0-2
@pytest.mark.parametrize("risk", [RiskClass.REVERSIBLE_WRITE, RiskClass.IRREVERSIBLE_WRITE])
def test_write_without_idempotency_key_is_refused_before_execution(on, risk):
    w = core._world()
    eng, _, act = core._engine(w, [core._steps()[0], _write_step(None, risk)])
    res = eng.run(_task(max_recoveries=0))
    assert res.state is ApprenticeState.FAIL and act.calls == [("TYPE", "textbox:Body")]
    assert res.records[-1].result.startswith("refused:idempotency_key_required") and w.summary != "Saved"


def test_write_with_key_but_no_receipt_is_failed_and_not_completed(on):
    w = core._world(); ledger = SideEffectLedger()
    eng, _, act = core._engine(w, [core._steps()[0], _write_step("note-save-1")], ledger=ledger)
    act.receipts = False                                   # actuator answers with a plain dict ("trust me")
    res = eng.run(_task(max_recoveries=0))
    assert res.state is ApprenticeState.FAIL
    rec = res.records[-1]
    assert rec.result.startswith("receipt_invalid") and rec.side_effect_id and not ledger.seen(rec.side_effect_id)


def test_write_with_key_and_valid_receipt_succeeds_and_completes_ledger(on):
    w = core._world(); ledger = SideEffectLedger()
    eng, _, act = core._engine(w, [core._steps()[0], _write_step("note-save-1")], ledger=ledger)
    res = eng.run(_task())
    assert res.ok and ledger.seen(res.records[-1].side_effect_id) and res.records[-1].receipt["action_type"] == "CLICK"
    for r in res.records:
        jsonschema.validate(r.to_dict(), SCHEMA)


# ---------------------------------------------------------------- P0-3
def test_receipt_action_type_mismatch_fails_step_and_keeps_ledger_open(on):
    w = core._world(); ledger = SideEffectLedger()
    step = PlanStep("t1", ActionKind.TYPE, core.NOTES, SemanticTarget("textbox", "Body"), text="hello", risk=RiskClass.REVERSIBLE_WRITE,
                    side_effecting=True, idempotency_key="type-1", expected=ExpectedState(contains_text=None), checkpoint="typed", is_goal=True)
    eng, _, act = core._engine(w, [step], ledger=ledger)
    eng.verifier.checkpoints["typed"] = lambda o: (True, "")
    real = act.act

    def wrong_type(s, obs, **kw):
        r = real(s, obs, **kw)
        return EffectReceipt(side_effect_id=r.side_effect_id, action_id=r.action_id, action_type="CLICK", observed_at=r.observed_at,
                             evidence_source=r.evidence_source)
    act.act = wrong_type
    res = eng.run(_task(max_recoveries=0))
    assert res.state is ApprenticeState.FAIL
    rec = res.records[-1]
    assert rec.result.startswith("receipt_invalid") and "action_type" in rec.result and not ledger.seen(rec.side_effect_id)
    assert not ledger.seen(rec.side_effect_id) and rec.error_code == "receipt_invalid"


# ---------------------------------------------------------------- P0-4
def test_observation_from_the_future_is_rejected(on):
    w = core._world()
    eng, _, act = core._engine(w)
    obs = SimObserver(w); real = obs.observe

    def future(**kw):
        o = real(**kw)
        return Observation(id=o.id, created_at=time.time() + 3600, foreground=o.foreground, summary=o.summary, ui_tree=o.ui_tree,
                           sensitive=o.sensitive, generation=o.generation)
    obs.observe = future
    eng.observer = obs
    res = eng.run(_task(max_recoveries=1))
    assert res.state is ApprenticeState.FAIL and act.calls == []
    assert any(r.result.startswith("refused:invalid_observation") for r in res.records)


def test_observation_bound_to_foreign_run_or_unbound_is_rejected(on):
    w = core._world()
    eng, _, act = core._engine(w)
    obs = SimObserver(w)
    obs.binding_override = {"task_id": "other", "run_id": "other_run", "session_id": "sess_1"}
    eng.observer = obs
    res = eng.run(_task(max_recoveries=1))
    assert res.state is ApprenticeState.FAIL and act.calls == [] and any("another task/run/session" in r.result for r in res.records)
    w2 = core._world(); eng2, _, act2 = core._engine(w2)
    obs2 = SimObserver(w2); obs2.binding_override = {}
    eng2.observer = obs2
    res2 = eng2.run(_task(max_recoveries=1))
    assert res2.state is ApprenticeState.FAIL and act2.calls == []


def test_observation_records_carry_full_binding(on):
    w = core._world()
    eng, _, _ = core._engine(w, [core._steps()[0], _write_step("k1")])
    res = eng.run(_task())
    assert res.ok
    for r in res.records:
        d = r.to_dict()
        jsonschema.validate(d, SCHEMA)
        for k in ("task_id", "run_id", "session_id", "action_id"):
            assert d["pre_observation"][k], k
        assert d["pre_observation"]["task_id"] == res.task_id and d["pre_observation"]["run_id"] == "run_1"
        assert d["post_observation"]["action_id"] == d["pre_observation"]["action_id"]
    assert res.records[-1].to_dict()["post_observation"]["side_effect_id"] == res.records[-1].side_effect_id
