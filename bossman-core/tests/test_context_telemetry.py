"""bossman.context_engine.telemetry: the saved-percent figure the context engine reports."""
from __future__ import annotations

from bossman.context_engine.telemetry import ContextTelemetry


def test_no_input_means_zero_saving_not_a_division_error():
    assert ContextTelemetry().saved_percent == 0.0


def test_saving_is_one_minus_final_over_raw():
    t = ContextTelemetry(raw_tokens=1000, final_tokens=250)
    assert t.saved_percent == 75.0
    assert ContextTelemetry(raw_tokens=3, final_tokens=1).saved_percent == 66.67        # rounded to two places


def test_growth_is_reported_as_negative_not_hidden():
    assert ContextTelemetry(raw_tokens=100, final_tokens=150).saved_percent == -50.0


def test_to_dict_carries_every_counter_and_the_computed_figure():
    d = ContextTelemetry(raw_tokens=10, final_tokens=5, duplicates_removed=2, memories_injected=1).to_dict()
    assert d["saved_percent"] == 50.0 and d["duplicates_removed"] == 2 and d["memories_injected"] == 1
    assert set(d) >= {"raw_tokens", "deduped_tokens", "compressed_tokens", "final_tokens", "retrieved_sources", "stale_removed"}
