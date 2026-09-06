"""Context/measurement correctness; these are NOT real LLM retention scores."""
import pytest
from bossman_v3.data_guardian.guardian import ContextDataGuardian
from bossman_v3.data_guardian.models import ContextItem, GuardianConfig


@pytest.mark.parametrize("priority", [0, 1, 2])
def test_duplicate_optional_text_cannot_erase_later_mandatory_record(priority):
    optional = ContextItem("optional", "history", content="same", source="project", priority=9,
                           token_count=10, savings_utility=1.0)
    mandatory = ContextItem("must_keep", "history", content="same", source="project", priority=priority,
                            token_count=10, savings_utility=1.0)
    report = ContextDataGuardian(GuardianConfig(token_budget=1)).select([optional, mandatory])
    assert mandatory in report.selected
    assert optional in report.omitted


METRICS = dict(raw_verified_success=.8, filtered_verified_success=.8, raw_quality=.9,
               filtered_quality=.9, raw_effective_cost=1.0, filtered_effective_cost=1.0)


@pytest.mark.parametrize("field", list(METRICS))
@pytest.mark.parametrize("bad", [float("nan"), float("inf"), -1, True, 10**1000])
def test_invalid_metric_never_produces_a_promotion(field, bad):
    with pytest.raises(ValueError):
        ContextDataGuardian().retention_gate(**{**METRICS, field: bad})


@pytest.mark.parametrize("field", ["raw_verified_success", "raw_quality", "raw_effective_cost"])
def test_zero_baseline_is_not_intelligence_preservation_proof(field):
    result = ContextDataGuardian().retention_gate(**{**METRICS, field: 0.0})
    assert not result.production_allowed


def test_measured_no_regression_is_preserved():
    result = ContextDataGuardian().retention_gate(**METRICS)
    assert result.production_allowed and result.intelligence_retention == 1.0


def test_tiny_baseline_cannot_produce_infinite_retention():
    with pytest.raises(ValueError, match="unbounded"):
        ContextDataGuardian().retention_gate(**{**METRICS, "raw_verified_success": 1e-320})
