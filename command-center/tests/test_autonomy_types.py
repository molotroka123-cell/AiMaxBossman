"""Autonomy shared types + wire schemas (docs/autonomy/AUTONOMY_CONTRACT.md)."""
from __future__ import annotations

import dataclasses
import json

import pytest

from bcc.autonomy import schemas
from bcc.autonomy.types import (GOAL_STATES, RISK_TIERS, Budget, Goal, HandRequest, HandResult, Review)

SHA = "a" * 40
DIFF = "b" * 64


def goal(**kw) -> Goal:
    base = dict(goal_id="JEFF-0042", problem="Jeff names the wrong model", desired_result="Jeff says Bossman",
                constraints=("no prompt bloat",), acceptance_tests=("pytest:tests/test_identity.py::test_name",),
                budget=Budget(max_minutes=30, max_agent_turns=6), risk_tier="prompts_models",
                target_metric="identity_redteam.leaks", protected_metrics=("task_success", "latency_ms"))
    base.update(kw)
    return Goal(**base)


def hand(**kw) -> HandRequest:
    base = dict(goal_id="JEFF-0042", requested_by="claude", action="run_tests", target="isolated_worktree",
                arguments={"argv": ["python", "-m", "pytest", "-q"]}, expected_evidence=("exit_code", "stdout"),
                risk_class="low", timeout_s=120, rollback="none: read-only")
    base.update(kw)
    return HandRequest(**base)


def test_goal_states_are_the_owner_list_in_order():
    assert GOAL_STATES == ("PROPOSED", "PLANNED", "BUILDING", "TESTING", "CLAUDE_REVIEW", "CODEX_REVIEW", "STAGING",
                           "USER_APPROVAL", "DEPLOYED", "MONITORING", "COMPLETE", "ROLLED_BACK", "BLOCKED")
    assert RISK_TIERS == ("docs_tests", "prompts_models", "memory_keys_telegram_services", "critical_runtime")


def test_contract_field_order():
    names = lambda cls: [f.name for f in dataclasses.fields(cls)]  # noqa: E731
    assert names(Budget) == ["max_minutes", "max_agent_turns", "max_cost_usd"]
    assert names(Goal) == ["goal_id", "problem", "desired_result", "constraints", "acceptance_tests", "budget",
                           "risk_tier", "target_metric", "protected_metrics"]
    assert names(HandRequest) == ["goal_id", "requested_by", "action", "target", "arguments", "expected_evidence",
                                  "risk_class", "timeout_s", "rollback"]
    assert names(HandResult) == ["request_hash", "ok", "exit_code", "started_at", "finished_at", "artifacts",
                                 "refused_reason"]
    assert names(Review) == ["goal_id", "reviewer", "sha", "diff_sha256", "verdict", "notes", "evidence_sha256"]


def test_frozen():
    with pytest.raises(dataclasses.FrozenInstanceError):
        goal().goal_id = "X-1"  # type: ignore[misc]


def test_hand_request_requires_timeout_and_rollback():
    with pytest.raises(TypeError):
        HandRequest(goal_id="JEFF-1", requested_by="claude", action="a", target="t", arguments={},  # type: ignore
                    expected_evidence=(), risk_class="low")


def test_round_trip_through_schemas():
    g = goal()
    assert schemas.goal_from_json(json.loads(json.dumps(schemas.to_json(g)))) == g
    h = hand()
    assert schemas.hand_request_from_json(schemas.to_json(h)) == h
    r = Review(goal_id="JEFF-0042", reviewer="codex", sha=SHA, diff_sha256=DIFF, verdict="APPROVE", notes="ok")
    assert schemas.review_from_json(schemas.to_json(r)) == r
    r2 = Review("JEFF-0042", "claude", SHA, DIFF, "APPROVE", "ok", evidence_sha256=DIFF)
    assert schemas.review_from_json(schemas.to_json(r2)) == r2
    old = {k: v for k, v in schemas.to_json(r).items() if k != "evidence_sha256"}
    assert schemas.review_from_json(old) == r                      # optional on the wire
    assert schemas.errors_for("review", {**old, "evidence_sha256": "nothex"})
    res = HandResult(request_hash=DIFF, ok=True, exit_code=0, started_at="t0", finished_at="t1",
                     artifacts={"stdout": DIFF})
    assert schemas.hand_result_from_json(schemas.to_json(res)) == res


@pytest.mark.parametrize("name,mutate,needle", [
    ("task", lambda d: d.pop("budget"), "budget: required"),
    ("task", lambda d: d.update(acceptance_tests=[]), "fewer than 1"),
    ("task", lambda d: d["budget"].update(max_minutes=0), "below 1"),
    ("task", lambda d: d.update(risk_tier="anything"), "not in"),
    ("task", lambda d: d.update(goal_id="jeff 42"), "does not match"),
    ("task", lambda d: d.update(extra=1), "not allowed"),
    ("task", lambda d: d["budget"].update(max_agent_turns=True), "expected integer"),
])
def test_task_schema_refusals(name, mutate, needle):
    data = schemas.to_json(goal())
    mutate(data)
    errors = schemas.errors_for(name, data)
    assert any(needle in e for e in errors), errors
    with pytest.raises(schemas.SchemaError):
        schemas.goal_from_json(data)


@pytest.mark.parametrize("field,value", [("timeout_s", 0), ("rollback", ""), ("requested_by", "gpt"),
                                         ("risk_class", "extreme"), ("action", "Run Tests")])
def test_action_schema_refusals(field, value):
    data = schemas.to_json(hand())
    data[field] = value
    assert schemas.errors_for("action", data)


def test_review_schema_binds_full_sha_and_diff_hash():
    data = {"goal_id": "JEFF-0042", "reviewer": "claude", "sha": "abc1234", "diff_sha256": DIFF,
            "verdict": "APPROVE", "notes": ""}
    assert any("sha" in e for e in schemas.errors_for("review", data))
    data["sha"] = SHA
    assert schemas.errors_for("review", data) == []


def test_result_artifacts_must_be_sha256():
    data = {"request_hash": DIFF, "ok": True, "exit_code": None, "started_at": "a", "finished_at": "b",
            "artifacts": {"stdout": "nothex"}, "refused_reason": ""}
    assert any("artifacts.stdout" in e for e in schemas.errors_for("result", data))


def test_unknown_schema_keyword_fails_closed():
    errors: list[str] = []
    schemas._check("x", {"type": "string", "format": "email"}, "$", errors)
    assert errors and "unsupported" in errors[0]


def test_mini_validator_agrees_with_jsonschema():
    jsonschema = pytest.importorskip("jsonschema")
    samples = {"task": schemas.to_json(goal()), "action": schemas.to_json(hand())}
    bad = {"task": {**schemas.to_json(goal()), "acceptance_tests": []},
           "action": {**schemas.to_json(hand()), "timeout_s": 0}}
    for name in samples:
        schema = schemas.load(name)
        jsonschema.Draft202012Validator.check_schema(schema)
        assert list(jsonschema.Draft202012Validator(schema).iter_errors(samples[name])) == []
        assert schemas.errors_for(name, samples[name]) == []
        assert list(jsonschema.Draft202012Validator(schema).iter_errors(bad[name]))
        assert schemas.errors_for(name, bad[name])
