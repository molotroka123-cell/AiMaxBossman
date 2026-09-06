"""M1 contract red-team tests; local BCC verifier integration is explicitly scoped."""
import asyncio
from copy import deepcopy
from dataclasses import FrozenInstanceError
import hashlib
import json

import pytest

from bossman_shared.mission_ir import MissionIR, MissionIRValidationError


def candidate(path="/tmp/epoch4-result.txt"):
    return {
        "schema_version": 1, "owner_id": "owner-a", "project_id": "project-a",
        "mission_id": "mission-a", "goal_id": "goal-a", "revision": 1,
        "previous_digest": None, "goal": "Write the expected result", "side_effect": True,
        "effects": [{"effect_id": "write-result", "kind": "IDEMPOTENT_WRITE",
                     "description": "Expected file is independently readable",
                     "capabilities": ["terminal.run"], "depends_on": [],
                     "verifiers": [{"kind": "file", "target": path,
                                    "expect": {"exists": True, "sha256": hashlib.sha256(b"expected").hexdigest()},
                                    "max_age_seconds": 60}]}],
        "privacy": "local_only", "risk": "low", "authorized_scope_refs": [],
        "budget": {"max_cost_usd": 0, "max_tokens": 4096, "max_wall_seconds": 60},
        "reservation_refs": [], "recovery": {"max_attempts_per_effect": 2, "max_attempts_total": 3},
        "success_conditions": ["Exact expected bytes exist"], "artifact_refs": [],
        "provenance": {"source": "owner_goal", "source_ref": "request-a"},
    }


def test_identity_survives_serialization_and_is_deeply_immutable():
    raw = candidate()
    ir = MissionIR.from_dict(raw)
    original = ir.digest
    raw["effects"][0]["verifiers"][0]["expect"]["exists"] = False
    exported = ir.to_dict()
    exported["budget"]["max_cost_usd"] = 100
    effects = ir.required_effects()
    effects[0]["expect"]["sha256"] = "0" * 64
    assert ir.digest == original
    assert ir.required_effects()[0]["expect"]["sha256"] == hashlib.sha256(b"expected").hexdigest()
    assert MissionIR.from_json(ir.to_json()) == ir
    assert MissionIR.from_dict(dict(reversed(list(ir.to_dict().items())))).digest == original
    with pytest.raises(FrozenInstanceError):
        ir._json = "{}"
    with pytest.raises(TypeError):
        MissionIR()


@pytest.mark.parametrize("field,value", [("goal", "Changed goal"), ("privacy", "public"),
                                         ("owner_id", "other"), ("mission_id", "other")])
def test_identity_binds_policy_relevant_fields(field, value):
    raw = candidate()
    first = MissionIR.from_dict(raw)
    raw[field] = value
    assert MissionIR.from_dict(raw).digest != first.digest


@pytest.mark.parametrize("value", [float("nan"), float("inf"), -float("inf"), -1, True, "2", None, 10 ** 1000])
@pytest.mark.parametrize("key", ["max_cost_usd", "max_tokens", "max_wall_seconds"])
def test_budget_fails_closed(key, value):
    raw = candidate()
    raw["budget"][key] = value
    with pytest.raises(MissionIRValidationError):
        MissionIR.from_dict(raw)


@pytest.mark.parametrize("mutation", [
    lambda r: r.update(schema_version=2),
    lambda r: r.update(schema_version=True),
    lambda r: r.update(approved=True),
    lambda r: r.update(effects=[]),
    lambda r: r.update(side_effect="false"),
    lambda r: r.update(side_effect=False),
    lambda r: r["effects"][0].update(verifiers=[]),
    lambda r: r["effects"][0].update(kind="PAYMENT_APPROVED"),
    lambda r: r["effects"][0].update(depends_on=["missing"]),
    lambda r: r["effects"][0].update(depends_on=["write-result"]),
    lambda r: r["effects"][0].update(capabilities=[]),
    lambda r: r["effects"][0].update(verified=True),
    lambda r: r["effects"][0]["verifiers"][0].update(kind="tool_result"),
    lambda r: r["effects"][0]["verifiers"][0].update(kind="terminal"),
    lambda r: r["effects"][0]["verifiers"][0].update(kind="browser"),
    lambda r: r["effects"][0]["verifiers"][0].update(expect={"approved": True}),
    lambda r: r["effects"][0]["verifiers"][0].update(expect={}),
    lambda r: r["effects"][0]["verifiers"][0].update(expect={"exists": "false"}),
    lambda r: r["effects"][0]["verifiers"][0].update(expect={"exists": False, "contains": "ignored"}),
    lambda r: r["effects"][0]["verifiers"][0].update(expect={"sha256": "abc"}),
    lambda r: r["effects"][0]["verifiers"][0].update(expect={"contains": ""}),
    lambda r: r["effects"][0]["verifiers"][0].update(expect={"min_bytes": 0}),
    lambda r: r["effects"][0]["verifiers"][0].update(max_age_seconds=0),
    lambda r: r["effects"][0]["verifiers"][0].update(max_age_seconds=86401),
    lambda r: r["recovery"].update(max_attempts_per_effect=4),
    lambda r: r["recovery"].update(max_attempts_total=11),
    lambda r: r.update(authorized_scope_refs=["scope", "scope"]),
    lambda r: r.update(artifact_refs=("tuple-is-not-json",)),
    lambda r: r.update(provenance={1: "not a JSON key"}),
    lambda r: r.update(revision=0),
])
def test_untrusted_candidate_attacks(mutation):
    raw = candidate()
    mutation(raw)
    with pytest.raises(MissionIRValidationError):
        MissionIR.from_dict(raw)


def test_cross_effect_cycle_and_duplicate_ids_rejected():
    raw = candidate()
    second = deepcopy(raw["effects"][0])
    raw["effects"].append(second)
    with pytest.raises(MissionIRValidationError, match="duplicate effect"):
        MissionIR.from_dict(raw)
    second["effect_id"] = "second"
    second["depends_on"] = ["write-result"]
    raw["effects"][0]["depends_on"] = ["second"]
    with pytest.raises(MissionIRValidationError, match="cycle"):
        MissionIR.from_dict(raw)
    raw["effects"][0]["depends_on"] = []
    assert len(MissionIR.from_dict(raw).required_effects()) == 2


def test_revision_requires_prior_identity_and_preserves_obligations():
    original = MissionIR.from_dict(candidate())
    raw = original.to_dict()
    raw.update(revision=2, previous_digest=original.digest, goal="Also produce a second file")
    second = deepcopy(raw["effects"][0])
    second.update(effect_id="second", depends_on=["write-result"])
    second["verifiers"][0]["target"] = "/tmp/second-result.txt"
    raw["effects"].append(second)
    revised = MissionIR.from_dict(raw, previous=original)
    assert revised.digest != original.digest
    assert all(b["mission_digest"] == revised.digest for b in revised.effect_bindings())
    assert MissionIR.from_json(revised.to_json(), previous=original) == revised
    for attack in (lambda r: r.update(previous_digest="0" * 64),
                   lambda r: r.update(revision=3),
                   lambda r: r.update(owner_id="attacker"),
                   lambda r: r.update(effects=[second]),
                   lambda r: r["effects"][0]["verifiers"][0].update(expect={"exists": True})):
        bad = deepcopy(raw)
        attack(bad)
        with pytest.raises(MissionIRValidationError):
            MissionIR.from_dict(bad, previous=original)
    with pytest.raises(MissionIRValidationError, match="previous revision"):
        MissionIR.from_dict(raw)


def test_json_parser_rejects_duplicate_nonfinite_oversized_and_deep_payloads():
    for text in ('{"schema_version":1,"schema_version":2}', '{"cost":NaN}', "x" * 1_048_577,
                 "[" * 2000 + "]" * 2000):
        with pytest.raises(MissionIRValidationError):
            MissionIR.from_json(text)
    raw = candidate()
    raw["provenance"]["cycle"] = raw
    with pytest.raises(MissionIRValidationError, match="nesting"):
        MissionIR.from_dict(raw)


@pytest.mark.parametrize("kind,target,expect", [("app", "editor", {"running": True}),
                                                ("process", "123", {"running": False})])
def test_supported_verifier_shapes(kind, target, expect):
    raw = candidate()
    raw["effects"][0]["verifiers"][0].update(kind=kind, target=target, expect=expect)
    assert MissionIR.from_dict(raw).required_effects() == [{"kind": kind, "target": target, "expect": expect}]


def test_bridge_preserves_all_expectations_and_has_no_effects(tmp_path):
    path = tmp_path / "proof.txt"
    ir = MissionIR.from_dict(candidate(str(path)))
    assert not path.exists()
    effects = ir.required_effects()
    bindings = ir.effect_bindings()
    assert len(effects) == len(bindings) == 1
    assert bindings[0]["effect_id"] == "write-result"
    assert bindings[0]["max_age_seconds"] == 60
    assert not path.exists()
    assert "approved" not in json.dumps(ir.to_dict())


def verify_bcc_bridge_against_real_filesystem(tmp_path):
    # Explicit integration probe, not root-CI collection: root CI intentionally
    # does not install BCC. Run with BCC dependencies and PYTHONPATH configured.
    # Real filesystem + production verifier; no engine dispatch, grants, DB or
    # live model is exercised here. The separately owned golden suite tests API.
    from bcc.v2.verification import parse_expected, verify_all
    path = tmp_path / "proof.txt"
    ir = MissionIR.from_dict(candidate(str(path)))
    expected = parse_expected(ir.required_effects())
    assert len(expected) == 1
    async def status():
        outcome, _, _ = await verify_all(expected, svc=None, task={"result": "I did it"}, roots=[tmp_path])
        return outcome
    assert asyncio.run(status()) == "FAILED"
    path.write_bytes(b"wrong")
    assert asyncio.run(status()) == "FAILED"
    path.write_bytes(b"expected")
    assert asyncio.run(status()) == "VERIFIED"
    path.unlink()
    assert asyncio.run(status()) == "FAILED"


def test_bounded_deep_dag_and_informational_mission():
    raw = candidate()
    prototype = raw["effects"][0]
    raw["effects"] = []
    for index in range(1000):
        effect = deepcopy(prototype)
        effect["effect_id"] = f"e-{index}"
        effect["depends_on"] = [f"e-{index - 1}"] if index else []
        raw["effects"].append(effect)
    assert len(MissionIR.from_dict(raw).required_effects()) == 1000
    raw["effects"].append(deepcopy(prototype))
    with pytest.raises(MissionIRValidationError, match="bounded array"):
        MissionIR.from_dict(raw)
    raw["effects"] = []
    raw["side_effect"] = False
    assert MissionIR.from_dict(raw).required_effects() == []


@pytest.mark.parametrize("invalid", ["\ud800", "\udfff", "valid-prefix\ud800suffix"])
def test_lone_surrogates_are_rejected_at_both_input_boundaries(invalid):
    with pytest.raises(MissionIRValidationError, match="Unicode"):
        MissionIR.from_json(invalid)
    raw = candidate()
    raw["effects"][0]["verifiers"][0]["target"] = invalid
    with pytest.raises(MissionIRValidationError, match="Unicode"):
        MissionIR.from_dict(raw)
    with pytest.raises(MissionIRValidationError, match="Unicode"):
        MissionIR.from_json(json.dumps(raw))
    raw = candidate()
    raw[invalid] = "bad key"
    with pytest.raises(MissionIRValidationError, match="Unicode"):
        MissionIR.from_dict(raw)


def test_valid_multilingual_unicode_roundtrips_without_changing_identity():
    raw = candidate("/tmp/результат-😀.txt")
    raw["goal"] = "Создать результат 😀"
    ir = MissionIR.from_dict(raw)
    assert MissionIR.from_json(json.dumps(raw, ensure_ascii=False)).digest == ir.digest
    assert MissionIR.from_json(json.dumps(raw, ensure_ascii=True)).digest == ir.digest
