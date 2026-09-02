"""Audit P0: provider cache_read/cache_write evidence outranks the internal applied flag."""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from bossman_shared.cache_observation import TokenBuckets, build_observation, classify, normalize_anthropic_usage  # noqa: E402


def test_measured_cache_read_wins_over_applied_false():
    b = normalize_anthropic_usage({"input_tokens": 10, "cache_read_input_tokens": 900, "cache_creation_input_tokens": 0,
                                   "output_tokens": 5})
    assert classify(eligible=False, buckets=b) == "HIT"
    assert classify(eligible=False, buckets=TokenBuckets(10, 0, 500, 5)) == "WRITE"
    obs = build_observation(provider="anthropic", model="m", route="direct", eligible=False, buckets=b,
                            cache_control_applied=False).as_dict()
    assert obs["state"] == "HIT" and obs["cache_read_tokens"] == 900 and obs["cache_control_applied"] is False


def test_applied_true_without_provider_evidence_is_not_a_hit():
    assert classify(eligible=True, buckets=None) == "UNKNOWN"
    assert classify(eligible=True, buckets=TokenBuckets(1000, 0, 0, 5)) == "MISS"
    assert classify(eligible=False, buckets=TokenBuckets(1000, 0, 0, 5)) == "BYPASS"


def test_degraded_flag_cannot_hide_a_measured_hit():
    assert classify(eligible=True, buckets=TokenBuckets(10, 900, 0, 5), degraded=True) == "HIT"
    assert classify(eligible=True, buckets=TokenBuckets(1000, 0, 0, 5), degraded=True) == "DEGRADED"
