"""Tests: model intelligence foundation (spec Part H, §39/§41/§42/§43)."""
from __future__ import annotations

import json

import pytest

from bcc.v2.model_intelligence import (
    CONFIDENCE_LEVELS,
    REASONING_LEVELS,
    Confidence,
    ModelCapabilityRecord,
    ModelScorecardEvent,
    TaskComplexityFeatures,
    capability_from_checks,
    capability_from_models_row,
    classify_reasoning,
    classify_reasoning_level,
    recommend_escalation,
)


def test_capability_from_models_row_tolerant():
    rec = capability_from_models_row({})
    assert isinstance(rec, ModelCapabilityRecord)
    assert rec.model_id == ""
    assert rec.capabilities == {"coding": "UNKNOWN", "planning": "UNKNOWN",
                                "tool_use": "UNKNOWN", "structured_output": "UNKNOWN"}
    assert rec.context_window is None
    row = {"id": 7, "alias": "qwen-coder", "context_window": 32768,
           "caps": json.dumps({"tool_use": True, "vision": True}),
           "local": False, "provider": "openrouter"}
    rec = capability_from_models_row(row)
    assert rec.model_id == "7"
    assert rec.capabilities["tool_use"] == "UNKNOWN"   # advertised ≠ verified
    assert rec.vision is True
    assert rec.context_window == 32768
    assert rec.cost_class == "cloud"
    rec2 = capability_from_models_row({"model_id": "m1",
                                       "caps": {"structured_output": "NO"}})
    assert rec2.capabilities["structured_output"] == "NO"


def test_capability_from_checks_merge():
    rows = [
        {"model_id": 1, "capability": "tools", "advertised": True, "verified": True},
        {"model_id": 1, "capability": "vision", "advertised": True, "verified": False},
        {"model_id": 1, "capability": "coding", "advertised": True, "verified": None},
        {"model_id": 1, "capability": "planning", "advertised": False, "verified": None},
    ]
    merged = capability_from_checks(rows)
    assert merged["tools"] == "YES"
    assert merged["vision"] == "NO"
    assert merged["coding"] == "UNKNOWN"   # advertised без пробы = UNKNOWN
    assert merged["planning"] == "UNKNOWN"
    assert capability_from_checks([]) == {}
    assert capability_from_checks([{"capability": ""}]) == {}


def test_reasoning_matrix_security_and_trivial():
    # security_impact >= 0.7 → минимум L3
    sec = TaskComplexityFeatures(security_impact=0.9)
    level, reasons = classify_reasoning(sec)
    assert REASONING_LEVELS.index(level) >= REASONING_LEVELS.index("L3")
    assert reasons and "security" in reasons[0]
    # mutation_impact >= 0.7 → минимум L3
    mut = TaskComplexityFeatures(mutation_impact=0.8)
    assert REASONING_LEVELS.index(classify_reasoning_level(mut)) >= 3
    # тривиальная задача → L0/L1
    trivial = TaskComplexityFeatures()
    level2, reasons2 = classify_reasoning(trivial)
    assert level2 in ("L0", "L1")
    assert reasons
    # детерминированность
    assert classify_reasoning(sec) == classify_reasoning(sec)


def test_reasoning_verification_with_failures_l3_l4():
    f = TaskComplexityFeatures(requires_verification=True, previous_failures=2)
    level = classify_reasoning_level(f)
    assert REASONING_LEVELS.index(level) >= REASONING_LEVELS.index("L3")
    heavy = TaskComplexityFeatures(requires_verification=True, previous_failures=3)
    assert classify_reasoning_level(heavy) == "L4"
    _, reasons = classify_reasoning(heavy)
    assert any("L4" in r or "failures" in r for r in reasons)


def test_classify_reasoning_level_matches_tuple():
    f = TaskComplexityFeatures(dependent_steps=5, tool_count=4)
    level = classify_reasoning_level(f)
    assert level in REASONING_LEVELS
    assert level == classify_reasoning(f)[0]


def test_confidence_levels_valid_and_strict():
    assert set(CONFIDENCE_LEVELS) == {"HIGH", "MEDIUM", "LOW", "UNKNOWN"}
    for lvl in CONFIDENCE_LEVELS:
        c = Confidence(level=lvl)
        assert c.level == lvl and c.value is None
    with pytest.raises(ValueError):
        Confidence(level="SURE")
    c = Confidence("LOW", 0.2, "2 verification failures")
    assert c.value == 0.2 and c.basis


def test_recommend_escalation_is_recommendation_not_authorization():
    doc = recommend_escalation.__doc__ or ""
    assert "authoriz" in doc.lower() or "авториз" in doc.lower()
    assert "policy" in doc.lower()
    assert recommend_escalation(Confidence("LOW"), "L1") is True
    assert recommend_escalation(Confidence("HIGH"), "L1") is False
    assert recommend_escalation(Confidence("UNKNOWN"), "L1") is None
    assert recommend_escalation(Confidence("MEDIUM"), "L1") is None
    assert recommend_escalation(Confidence("MEDIUM"), "L3") is True
    # форма: чистая функция, ничего не исполняет — только скаляр-рекомендация
    assert recommend_escalation(Confidence("MEDIUM", 0.5, "checks"), "L2") is None


def test_scorecard_to_dict_keys():
    ev = ModelScorecardEvent(
        model="qwen2.5-coder", task_class="code_edit", reasoning_level="L2",
        latency_ms=812.0, tokens_in=900, tokens_out=140, cost_usd=0.0,
        structured_output_valid=True, tool_call_valid=False, retries=1,
        task_success=True, verification_result="UNKNOWN")
    d = ev.to_dict()
    assert set(d) == {"model", "task_class", "reasoning_level", "latency_ms",
                      "tokens_in", "tokens_out", "cost_usd",
                      "structured_output_valid", "tool_call_valid", "retries",
                      "task_success", "verification_result"}
    assert d["verification_result"] == "UNKNOWN"  # UNKNOWN валиден (spec §39)


def test_reasoning_levels_constants():
    assert REASONING_LEVELS == ("L0", "L1", "L2", "L3", "L4")
    assert CONFIDENCE_LEVELS == ("HIGH", "MEDIUM", "LOW", "UNKNOWN")
    rec = ModelCapabilityRecord(model_id="m")
    assert rec.local is True and rec.vision is False and rec.cost_class == "local"
