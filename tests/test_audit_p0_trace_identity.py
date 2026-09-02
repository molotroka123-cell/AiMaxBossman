"""Audit P0 reproductions for the learning trace: alias verifiers, missing evidence
bindings and future timestamps never yield VERIFIED."""
from __future__ import annotations

import copy
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from learning.trace import MAX_CLOCK_SKEW_S, canonical_principal_id, validate  # noqa: E402

sys.path.insert(0, str(ROOT / "tests"))
from test_learning_trace import _case  # noqa: E402


def _verified_case() -> dict:
    c = _case()
    assert validate(c) == [], validate(c)
    return c


def test_alias_verifier_is_not_independent():
    c = _verified_case()
    me = c.get("principal_id") or c.get("agent")
    c["verifiers"] = [{"principal_id": f"verifier:{me}", "role": "verifier", "independence_class": "human",
                       "run_id": "other-run"}]
    errs = validate(c)
    assert errs and "independent verifier" in errs[0]
    assert canonical_principal_id(f"verifier:{me}") == canonical_principal_id(me)


def test_same_model_under_external_tool_is_not_independent():
    c = _verified_case()
    c["model"] = "qwen-14b"
    c["verifiers"] = [{"principal_id": "tool-x", "model_id": "QWEN-14B", "role": "verifier",
                       "independence_class": "external_tool", "run_id": "other-run"}]
    assert any("independent verifier" in e for e in validate(c))


def test_evidence_record_requires_bindings():
    for key in ("principal_id", "head_sha", "environment", "collected_at"):
        c = copy.deepcopy(_verified_case())
        c["evidence_records"][0][key] = "" if key != "collected_at" else 0
        errs = validate(c)
        assert errs and key in errs[0], (key, errs)


def test_evidence_record_from_the_future_is_refused():
    c = _verified_case()
    future = time.time() + MAX_CLOCK_SKEW_S + 60
    c["evidence_records"][0]["observed_at"] = future
    c["evidence_records"][0]["collected_at"] = future
    errs = validate(c)
    assert errs and "future" in errs[0]
