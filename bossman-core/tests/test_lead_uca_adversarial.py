"""Lead's independent bypass attempts against the Universal Computer Apprentice
(written by the reviewer, not the implementer)."""
from __future__ import annotations

import sys
import time
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(Path(__file__).resolve().parent))

from bossman.apprentice import flags  # noqa: E402
from bossman.apprentice.guards import SideEffectLedger  # noqa: E402
from bossman.apprentice.models import ApprenticeState, ApprenticeTask, EffectReceipt, PlanStep, RiskClass, SemanticTarget  # noqa: E402
from bossman.computer_operator.models import ActionKind, ExpectedState, Observation  # noqa: E402
from fixtures.apprentice.sim import SimObserver  # noqa: E402
import test_apprentice_core as core  # noqa: E402


@pytest.fixture
def on(monkeypatch):
    monkeypatch.setenv(flags.MASTER, "1")


def _task(**kw):
    return ApprenticeTask.create("save a note", session_id="sess_1", run_id=kw.pop("run_id", "run_1"), head_sha="abc123",
                                 environment="env:notes-1.0", task_type="notes.save", **kw)


def _write_step(key="note-save-1"):
    return PlanStep("w1", ActionKind.CLICK, core.NOTES, SemanticTarget("button", "Save"), risk=RiskClass.REVERSIBLE_WRITE,
                    side_effecting=True, expected=ExpectedState(contains_text="Saved"), checkpoint="saved", is_goal=True,
                    idempotency_key=key)


def test_receipt_for_another_action_id_is_refused(on):
    w = core._world(); ledger = SideEffectLedger()
    eng, _, act = core._engine(w, [core._steps()[0], _write_step()], ledger=ledger)
    real = act.act

    def foreign_action(s, obs, **kw):
        r = real(s, obs, **kw)
        if not isinstance(r, EffectReceipt):
            return r
        return EffectReceipt(side_effect_id=r.side_effect_id, action_id="act_someone_else", action_type=r.action_type,
                             observed_at=r.observed_at, evidence_source=r.evidence_source)
    act.act = foreign_action
    res = eng.run(_task(max_recoveries=0))
    assert res.state is ApprenticeState.FAIL
    assert res.records[-1].result.startswith("receipt_invalid") and not ledger.seen(res.records[-1].side_effect_id)


def test_receipt_with_foreign_side_effect_id_is_refused(on):
    w = core._world(); ledger = SideEffectLedger()
    eng, _, act = core._engine(w, [core._steps()[0], _write_step()], ledger=ledger)
    real = act.act

    def foreign_effect(s, obs, **kw):
        r = real(s, obs, **kw)
        if not isinstance(r, EffectReceipt):
            return r
        return EffectReceipt(side_effect_id="se_other", action_id=r.action_id, action_type=r.action_type,
                             observed_at=r.observed_at, evidence_source=r.evidence_source)
    act.act = foreign_effect
    res = eng.run(_task(max_recoveries=0))
    assert res.state is ApprenticeState.FAIL and res.records[-1].result.startswith("receipt_invalid")
    assert not ledger.seen("se_other")


def test_receipt_observed_before_the_action_is_refused(on):
    w = core._world(); ledger = SideEffectLedger()
    eng, _, act = core._engine(w, [core._steps()[0], _write_step()], ledger=ledger)
    real = act.act

    def stale_receipt(s, obs, **kw):
        r = real(s, obs, **kw)
        if not isinstance(r, EffectReceipt):
            return r
        return EffectReceipt(side_effect_id=r.side_effect_id, action_id=r.action_id, action_type=r.action_type,
                             observed_at=time.time() - 3600, evidence_source=r.evidence_source)
    act.act = stale_receipt
    res = eng.run(_task(max_recoveries=0))
    assert res.state is ApprenticeState.FAIL and res.records[-1].result.startswith("receipt_invalid")


def test_same_idempotency_key_is_not_executed_twice_across_runs(on):
    w = core._world(); ledger = SideEffectLedger()
    eng, _, act = core._engine(w, [core._steps()[0], _write_step("note-save-1")], ledger=ledger)
    r1 = eng.run(_task(run_id="run_1"))
    assert r1.ok, (r1.reason, [x.result for x in r1.records])
    first_calls = list(act.calls)
    w2 = core._world()
    eng2, _, act2 = core._engine(w2, [core._steps()[0], _write_step("note-save-1")], ledger=ledger)
    res2 = eng2.run(_task(run_id="run_2", max_recoveries=0))
    write_calls = [c for c in act2.calls if c[0] == "CLICK"]
    assert write_calls == [], (first_calls, act2.calls)      # duplicate external effect prevented
    assert res2.state is not ApprenticeState.SUCCEED or any("duplicate" in r.result for r in res2.records)


def test_post_observation_identical_to_pre_observation_is_not_fresh(on):
    w = core._world()
    eng, _, act = core._engine(w, [core._steps()[0], _write_step()])
    obs = SimObserver(w); real = obs.observe
    cache: dict[str, Observation] = {}

    def frozen(**kw):
        o = real(**kw)
        if "first" not in cache:
            cache["first"] = o
        f = cache["first"]
        return Observation(id=f.id, created_at=f.created_at, foreground=f.foreground, summary=f.summary, ui_tree=f.ui_tree,
                           sensitive=f.sensitive, generation=f.generation)
    obs.observe = frozen
    eng.observer = obs
    res = eng.run(_task(max_recoveries=0))
    assert res.state is ApprenticeState.FAIL
    assert not any(r.result == "verified" and r.post_observation for r in res.records)
