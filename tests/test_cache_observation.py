"""PASS3 shared cache observation — golden provider fixtures, no double counting,
provider-evidence classification, schema validation, content-free."""
from __future__ import annotations

import json
from decimal import Decimal
from pathlib import Path

import pytest

from bossman_shared.cache_observation import (ObservationLog, TokenBuckets, build_observation, classify,
                                              cost_pair, normalize_anthropic_usage,
                                              normalize_openai_style_usage, opaque, validate_observation)

ROOT = Path(__file__).resolve().parents[1]
SCHEMA = json.loads((ROOT / "schemas" / "cache_observation.schema.json").read_text(encoding="utf-8"))

GOLDEN_ANTHROPIC = {
    "fresh_only": ({"input_tokens": 100, "output_tokens": 5}, TokenBuckets(100, 0, 0, 5), "MISS"),
    "write": ({"input_tokens": 12, "cache_creation_input_tokens": 900, "cache_read_input_tokens": 0,
               "output_tokens": 7}, TokenBuckets(12, 0, 900, 7), "WRITE"),
    "read": ({"input_tokens": 12, "cache_creation_input_tokens": 0, "cache_read_input_tokens": 900,
              "output_tokens": 7}, TokenBuckets(12, 900, 0, 7), "HIT"),
    "read_and_write": ({"input_tokens": 3, "cache_creation_input_tokens": 200, "cache_read_input_tokens": 900,
                        "output_tokens": 1}, TokenBuckets(3, 900, 200, 1), "HIT"),
}


@pytest.mark.parametrize("name", sorted(GOLDEN_ANTHROPIC))
def test_anthropic_golden_fixtures(name):
    usage, buckets, state = GOLDEN_ANTHROPIC[name]
    got = normalize_anthropic_usage(usage)
    assert got == buckets and got.total_input == buckets.fresh_input + buckets.cache_read + buckets.cache_write
    assert classify(eligible=True, buckets=got) == state


def test_openai_style_usage_is_not_double_counted():
    usage = {"prompt_tokens": 1000, "completion_tokens": 20, "prompt_tokens_details": {"cached_tokens": 900}}
    b = normalize_openai_style_usage(usage)
    assert b == TokenBuckets(100, 900, 0, 20) and b.total_input == 1000
    assert classify(eligible=True, buckets=b) == "HIT"


def test_cache_control_alone_is_never_hit_or_write():
    b = normalize_anthropic_usage({"input_tokens": 50, "output_tokens": 1})
    obs = build_observation(provider="anthropic", model="m", route="direct", eligible=True, buckets=b,
                            cache_control_applied=True)
    assert obs.state == "MISS" and obs.cache_control_applied is True
    unknown = build_observation(provider="anthropic", model="m", route="direct", eligible=True,
                                buckets=normalize_anthropic_usage(None), cache_control_applied=True)
    assert unknown.state == "UNKNOWN" and "no provider usage" in unknown.miss_reason
    assert build_observation(provider="x", model="m", route="gateway", eligible=False, buckets=b).state == "BYPASS"
    assert build_observation(provider="x", model="m", route="gateway", eligible=True, buckets=b,
                             degraded=True).state == "DEGRADED"


def test_cost_pair_marks_unknown_prices_instead_of_fake_savings():
    b = TokenBuckets(100, 900, 0, 50)
    actual, baseline, est = cost_pair(b, fresh_per_m=Decimal("5"), read_per_m=Decimal("0.5"),
                                      write_per_m=Decimal("6.25"), output_per_m=Decimal("25"))
    assert actual == Decimal("0.0022") and baseline == Decimal("0.00625") and est is True
    actual, baseline, _ = cost_pair(b, fresh_per_m=Decimal("5"), read_per_m=None,
                                    write_per_m=None, output_per_m=Decimal("25"))
    assert actual is None and baseline == Decimal("0.00625")           # UNKNOWN, не «экономия»
    assert cost_pair(b, fresh_per_m=None, read_per_m=None, write_per_m=None, output_per_m=None)[0] is None


def test_observation_matches_schema_and_carries_no_content():
    obs = build_observation(provider="anthropic", model="claude-x", route="direct", eligible=True,
                            buckets=TokenBuckets(10, 900, 0, 3), ttl="5m", cache_control_applied=True,
                            task_id_hash=opaque(42), session_id_hash=opaque("s"), prefix_hash="ab" * 8,
                            prefix_tokens=1200, security_context_hash=opaque("policy-v1"))
    d = obs.as_dict()
    assert validate_observation(d) == []
    assert set(d) <= set(SCHEMA["properties"]) and all(k in d for k in SCHEMA["required"])
    assert d["state"] == "HIT" and d["task_id_hash"] != "42"
    assert not any(k in d for k in ("prompt", "messages", "system", "content"))
    bad = dict(d); bad["prompt"] = "secret text"
    assert any("forbidden" in e for e in validate_observation(bad))
    fake_hit = dict(d); fake_hit["cache_read_tokens"] = 0
    assert any("HIT without" in e for e in validate_observation(fake_hit))


def test_observation_log_counts_and_summary_separate_measured_from_estimated():
    log = ObservationLog(capacity=4)
    b_hit = TokenBuckets(10, 900, 0, 3)
    log.record(build_observation(provider="p", model="m", route="gateway", eligible=True, buckets=b_hit,
                                 actual_cost_usd=0.002, baseline_cost_usd=0.006))
    log.record(build_observation(provider="p", model="m", route="direct", eligible=True,
                                 buckets=TokenBuckets(50, 0, 0, 1)))                 # MISS, cost unknown
    log.record(build_observation(provider="p", model="m", route="direct", eligible=True, buckets=None,
                                 cache_control_applied=True))                           # UNKNOWN
    log.record(build_observation(provider="p", model="m", route="local", eligible=False, buckets=None))
    s = log.summary()
    assert s["counts"]["HIT"] == 1 and s["counts"]["MISS"] == 1 and s["counts"]["UNKNOWN"] == 1 and s["counts"]["BYPASS"] == 1
    assert s["eligible_requests"] == 2 and s["hit_rate_percent"] == 50.0
    assert s["measured_actual_cost_usd"] == 0.002 and s["estimated_baseline_cost_usd"] == 0.006
    assert s["unknown_cost_requests"] == 1 and s["cache_control_without_usage"] == 1
    log.record(build_observation(provider="p", model="m", route="local", eligible=False, buckets=None))
    assert len(log.items) == 4                                                          # bounded


# ---------------------------------------------------------------------------
# AUDIT-ONLY-001 open item: ObservationLog.record had no idempotency key.
# Harmless while the log is rebuilt per poll from a bounded window; a durable
# observation stream would double-count every redelivered event.
def _obs(**kw):
    return build_observation(provider="anthropic", model="m", route="direct", eligible=True,
                             buckets=TokenBuckets(10, 90, 0, 5), **kw)


def test_a_redelivered_observation_is_counted_once():
    log = ObservationLog()
    first = log.record(_obs(observation_id="req-1"))
    again = log.record(_obs(observation_id="req-1"))
    assert again is first
    assert log.duplicates_dropped == 1
    assert log.counts["HIT"] == 1 and log.tokens["cache_read"] == 90
    assert len(log.items) == 1


def test_distinct_ids_are_still_counted_separately():
    log = ObservationLog()
    log.record(_obs(observation_id="req-1"))
    log.record(_obs(observation_id="req-2"))
    assert log.counts["HIT"] == 2 and log.tokens["cache_read"] == 180
    assert log.duplicates_dropped == 0


def test_observations_without_an_id_are_not_silently_merged():
    """Two genuinely different calls can be field-identical. Without an explicit
    key the log counts both rather than guessing they are the same event."""
    log = ObservationLog()
    log.record(_obs())
    log.record(_obs())
    assert log.counts["HIT"] == 2 and log.duplicates_dropped == 0


def test_the_dedup_window_is_bounded_by_the_retention_window():
    log = ObservationLog(capacity=2)
    log.record(_obs(observation_id="req-1"))
    log.record(_obs(observation_id="req-2"))
    log.record(_obs(observation_id="req-3"))        # evicts req-1 from the ring
    assert set(log._seen) == {"req-2", "req-3"}
    assert log.record(_obs(observation_id="req-2")) is log.items[0]
    assert log.duplicates_dropped == 1


def test_the_id_is_validated_and_stays_content_free():
    assert "observation_id" in validate_observation({**_obs().as_dict(), "observation_id": ""})[0]
    assert validate_observation({**_obs().as_dict(), "observation_id": "x" * 129})
    assert validate_observation({**_obs().as_dict(), "observation_id": 7})
    assert validate_observation({**_obs(observation_id="req-1").as_dict()}) == []
    assert SCHEMA["properties"]["observation_id"]["maxLength"] == 128
