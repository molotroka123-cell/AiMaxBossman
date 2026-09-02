"""Audit P0 reproductions (identity + evidence): alias of the same model is not an
independent verifier; evidence must carry principal/head/environment/collected_at
and cannot come from the future."""
from __future__ import annotations

import time

import pytest

from bossman.deep_fix import MAX_CLOCK_SKEW_S, DeepFixRun, Evidence, Principal, canonical_principal_id

CODER = Principal(principal_id="qwen-14b", model_id="qwen-14b", role="coder", run_id="run-1")
HUMAN = Principal(principal_id="human:owner", role="human", run_id="run-9", independence_class="human")


def _run() -> DeepFixRun:
    run = DeepFixRun(task_id="T-1", coder="qwen", allowed_paths=("bossman/",), run_id="run-1",
                     head_sha="abc123", environment="env-a", coder_principal=CODER)
    run.context_ready(repo_map=[], targeted=["bossman/x.py"])
    run.reproduced(Evidence("repro", "r", True, "pytest"))
    run.root_cause_proposed(["h"], [], "h")
    run.fix_planned("p")
    run.patched(["bossman/x.py"])
    run.focused_tested([Evidence("test", "t", True)])
    run.adversarial_tested(Evidence("repro", "blocked", False), [Evidence("variant", "v", False)])
    run.regression_tested(Evidence("regression", "ok", True))
    return run


def _obs(**over) -> Evidence:
    now = time.time()
    base = dict(kind="observation", detail="reopened", passed=True, source="pytest", task_id="T-1", run_id="run-1",
                principal_id=HUMAN.principal_id, environment="env-a", head_sha="abc123", at=now, collected_at=now,
                expected="contained", actual="contained")
    base.update(over)
    return Evidence(**base)


@pytest.mark.parametrize("alias", ["verifier:qwen-14b", "model:Qwen-14B", "  QWEN-14B ", "verifier:model:qwen-14b"])
def test_alias_of_same_principal_is_not_independent(alias):
    assert canonical_principal_id(alias) == "qwen-14b"
    v = Principal(principal_id=alias, model_id="other", role="verifier", run_id="run-2", independence_class="cross_model")
    ok, why = v.independent_of(CODER)
    assert not ok and "alias" in why


def test_same_model_under_external_tool_class_is_not_independent():
    v = Principal(principal_id="tool-x", model_id="QWEN-14B", role="verifier", run_id="run-2",
                  independence_class="external_tool")
    ok, why = v.independent_of(CODER)
    assert not ok and "same model" in why


def test_structured_independent_verifier_passes():
    ok, _ = HUMAN.independent_of(CODER)
    assert ok
    run = _run()
    run.verified(verifier=HUMAN, evidence=_obs())
    assert run.state == "VERIFIED"


@pytest.mark.parametrize("field,value,fragment", [
    ("principal_id", "", "principal_id"),
    ("head_sha", "", "head_sha"),
    ("environment", "", "environment"),
    ("collected_at", 0.0, "collected_at"),
])
def test_evidence_without_binding_fields_is_refused(field, value, fragment):
    run = _run()
    ev = _obs(**{field: value})
    with pytest.raises(Exception) as exc:
        run.verified(verifier=HUMAN, evidence=ev)
    assert fragment in str(exc.value)


def test_evidence_from_the_future_is_refused():
    run = _run()
    future = time.time() + MAX_CLOCK_SKEW_S + 60
    with pytest.raises(Exception) as exc:
        run.verified(verifier=HUMAN, evidence=_obs(at=future, collected_at=future))
    assert "future" in str(exc.value)
    with pytest.raises(Exception) as exc2:
        run.verified(verifier=HUMAN, evidence=_obs(collected_at=future))
    assert "future" in str(exc2.value)


def test_small_clock_skew_is_tolerated():
    run = _run()
    soon = time.time() + MAX_CLOCK_SKEW_S / 2
    run.verified(verifier=HUMAN, evidence=_obs(at=soon, collected_at=soon))
    assert run.state == "VERIFIED"
