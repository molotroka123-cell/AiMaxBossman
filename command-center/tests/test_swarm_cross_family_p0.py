"""P0 audit 31.10 — `cross_family_verified` must mean a verifier MODEL of another family was actually called.

Reproduction on 55d4d902: `SwarmDAGPlanner.run_dag(executor_fn=..., checker_fn=lambda p: True)` returned
`cross_family_verified=True` (and `independent: True` per node) without any model call, because run_dag computed
`checker_fn is not None and all(passed)`. These tests pin the fixed contract. All adapters are synthetic fakes:
no network, no real model.
"""
from __future__ import annotations

import hashlib
import json

import pytest

from bcc.v2.swarm_dag import (
    SwarmDAGPlanner,
    SwarmExecutionError,
    VerifierModelAdapter,
)


class FakeVerifier:
    """Counts calls; returns a scripted verdict or raises."""

    def __init__(self, verdict=None, *, raises: Exception | None = None, is_async: bool = True):
        self.verdict = verdict
        self.raises = raises
        self.calls: list[dict] = []
        self.is_async = is_async

    def _answer(self, request):
        self.calls.append(request)
        if self.raises is not None:
            raise self.raises
        return self.verdict

    def __call__(self, request):
        if self.is_async:
            async def _coro():
                return self._answer(request)
            return _coro()
        return self._answer(request)


def _adapter(fake, model_id="mistral-large-2411", provider="fake-provider", family=None):
    return VerifierModelAdapter(provider=provider, model_id=model_id, call=fake, family=family)


async def _executor(subtask):
    return {"output": f"did {subtask.id}"}


def _sha(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, default=str, ensure_ascii=False).encode("utf-8")).hexdigest()


# ---------------------------------------------------------------- negatives ---------------------------------------------

@pytest.mark.asyncio
async def test_lambda_checker_alone_is_never_cross_family_verified():
    planner = SwarmDAGPlanner(proposer_family="gemma", verifier_family="qwen", max_repairs=0)
    r = await planner.run_dag(goal="g", executor_fn=_executor, checker_fn=lambda p: True)
    assert r.executed is True and r.status == "SUCCEEDED"
    assert r.cross_family_verified is False
    assert r.verification_kind == "custom_checker" and r.structural_check_passed is True
    assert r.verifier_model is None and r.cross_family_receipts == []


@pytest.mark.asyncio
async def test_requested_same_family_with_lambda_is_not_verified():
    """On 55d4d902 proposer=qwen/verifier=qwen was silently relabelled to gemma and reported True."""
    planner = SwarmDAGPlanner(proposer_family="qwen", verifier_family="qwen", max_repairs=0)
    r = await planner.run_dag(goal="g", executor_fn=_executor, checker_fn=lambda p: True)
    assert r.cross_family_verified is False


@pytest.mark.asyncio
async def test_no_verifier_call_means_not_verified_even_with_label():
    planner = SwarmDAGPlanner(proposer_family="gemma", verifier_family="claude", max_repairs=0)
    r = await planner.run_dag(goal="g", executor_fn=_executor)
    assert r.cross_family_verified is False and r.verification_kind == "structural_only"
    assert r.structural_check_passed is True and r.cross_family_receipts == []


@pytest.mark.asyncio
@pytest.mark.parametrize("verdict, status", [
    (None, "empty_verdict"),
    ("", "empty_verdict"),
    ("   ", "empty_verdict"),
    ({"verdict": ""}, "empty_verdict"),
    ({"reason": "looks fine"}, "empty_verdict"),
    ("looks good to me", "invalid_verdict"),
    ({"verdict": "MAYBE"}, "invalid_verdict"),
])
async def test_empty_or_invalid_verdict_is_not_verified(verdict, status):
    fake = FakeVerifier(verdict)
    planner = SwarmDAGPlanner(proposer_family="gemma", max_repairs=0, verifier_adapter=_adapter(fake))
    r = await planner.run_dag(goal="g", executor_fn=_executor)
    assert len(fake.calls) == 3                              # the model WAS called, it just gave no verdict
    assert r.cross_family_verified is False and r.verification_kind == "structural_only"
    assert [c["status"] for c in r.cross_family_receipts] == [status] * 3
    assert all(c["invoked"] is True and c["cross_family_verified"] is False for c in r.cross_family_receipts)


@pytest.mark.asyncio
async def test_verifier_exception_is_recorded_and_not_verified():
    fake = FakeVerifier(raises=TimeoutError("provider down"))
    planner = SwarmDAGPlanner(proposer_family="gemma", max_repairs=0, verifier_adapter=_adapter(fake))
    r = await planner.run_dag(goal="g", executor_fn=_executor)
    assert len(fake.calls) == 3
    assert r.status == "SUCCEEDED" and r.cross_family_verified is False
    for c in r.cross_family_receipts:
        assert c["status"] == "verifier_error" and c["invoked"] is False and "provider down" in c["reason"]


@pytest.mark.asyncio
@pytest.mark.parametrize("model_id, family", [
    ("gemma-3-27b-it", None),          # same family by model id
    ("gemma-3-27b-it", "gemma"),
    ("Gemma3:12b", None),               # case / tag variants still the same family
])
async def test_same_family_verifier_is_not_called(model_id, family):
    fake = FakeVerifier("PASS")
    planner = SwarmDAGPlanner(proposer_family="gemma", verifier_family="qwen", max_repairs=0,
                              verifier_adapter=_adapter(fake, model_id=model_id, family=family))
    r = await planner.run_dag(goal="g", executor_fn=_executor)
    assert fake.calls == []
    assert r.cross_family_verified is False
    assert {c["status"] for c in r.cross_family_receipts} == {"same_family"}


@pytest.mark.asyncio
async def test_declared_family_cannot_disguise_a_same_family_model():
    """An adapter claiming family «qwen» for a gemma model id is refused (and not called)."""
    fake = FakeVerifier("PASS")
    planner = SwarmDAGPlanner(proposer_family="gemma", max_repairs=0,
                              verifier_adapter=_adapter(fake, model_id="gemma-3-27b", family="qwen"))
    r = await planner.run_dag(goal="g", executor_fn=_executor)
    assert fake.calls == [] and r.cross_family_verified is False
    assert {c["status"] for c in r.cross_family_receipts} == {"verifier_family_mismatch"}


@pytest.mark.asyncio
@pytest.mark.parametrize("model_id, family, provider, status", [
    ("some-unknown-model", None, "p", "unknown_verifier_family"),
    ("some-unknown-model", "acme", "p", "unknown_verifier_family"),
    ("", "qwen", "p", "verifier_identity_missing"),
    ("qwen3-32b", None, "", "verifier_identity_missing"),
])
async def test_unprovable_verifier_identity_is_not_called(model_id, family, provider, status):
    fake = FakeVerifier("PASS")
    planner = SwarmDAGPlanner(proposer_family="gemma", max_repairs=0,
                              verifier_adapter=_adapter(fake, model_id=model_id, provider=provider, family=family))
    r = await planner.run_dag(goal="g", executor_fn=_executor)
    assert fake.calls == [] and r.cross_family_verified is False
    assert {c["status"] for c in r.cross_family_receipts} == {status}


@pytest.mark.asyncio
async def test_plan_only_never_calls_the_verifier_and_claims_nothing():
    fake = FakeVerifier("PASS")
    planner = SwarmDAGPlanner(proposer_family="gemma", verifier_adapter=_adapter(fake))
    r = await planner.run_dag(goal="g", checker_fn=lambda p: True)
    assert fake.calls == []
    assert r.status == "PLAN_ONLY" and r.executed is False and r.completed_nodes == 0
    assert r.cross_family_verified is False and r.verification_kind == "none"
    assert r.structural_check_passed is None and r.cross_family_receipts == []
    assert r.verifier_model == {"provider": "fake-provider", "model_id": "mistral-large-2411", "family": "mistral"}


@pytest.mark.asyncio
async def test_structural_failure_does_not_spend_a_model_call():
    fake = FakeVerifier("PASS")
    planner = SwarmDAGPlanner(proposer_family="gemma", max_repairs=1, verifier_adapter=_adapter(fake))
    with pytest.raises(SwarmExecutionError):
        await planner.run_dag(goal="g", executor_fn=_executor, checker_fn=lambda p: False)
    assert fake.calls == []


@pytest.mark.asyncio
async def test_other_family_fail_verdict_triggers_repair_then_fails():
    fake = FakeVerifier({"verdict": "FAIL", "reason": "postcondition not met"})
    planner = SwarmDAGPlanner(proposer_family="gemma", max_repairs=1, verifier_adapter=_adapter(fake))
    with pytest.raises(SwarmExecutionError):
        await planner.run_dag(goal="g", executor_fn=_executor)
    assert len(fake.calls) == 2                              # first node: attempt + 1 repair, both judged FAIL


@pytest.mark.asyncio
async def test_receipt_tampered_output_is_not_verified():
    """If the node output changes after it was hashed, the stored hash no longer matches and the run is not verified."""
    calls = []

    def tamper(request):
        calls.append(request["subtask_id"])
        request["proposal"]["output"] = "changed after hashing"   # mutates the node's output object in place
        return "PASS"

    planner = SwarmDAGPlanner(proposer_family="gemma", max_repairs=0, verifier_adapter=_adapter(tamper))
    r = await planner.run_dag(goal="g", executor_fn=_executor)
    assert len(calls) == 3
    assert all(c["verdict"] == "PASS" for c in r.cross_family_receipts)
    assert r.cross_family_verified is False


# ---------------------------------------------------------------- positive ----------------------------------------------

@pytest.mark.asyncio
@pytest.mark.parametrize("is_async", [True, False])
async def test_other_family_verifier_actually_called_gives_verified_with_receipt(is_async):
    fake = FakeVerifier({"verdict": "pass", "reason": "postcondition holds"}, is_async=is_async)
    planner = SwarmDAGPlanner(proposer_family="gemma", verifier_family="qwen", max_repairs=0)
    r = await planner.run_dag(goal="g", executor_fn=_executor, checker_fn=lambda p: True,
                              verifier_adapter=_adapter(fake, model_id="mistral-large-2411", provider="fake-provider"))
    assert len(fake.calls) == 3
    assert [c["subtask_id"] for c in fake.calls] == ["task_1_prep", "task_2_execute", "task_3_finalize"]
    assert all(c["worker_family"] == "gemma" for c in fake.calls)

    assert r.executed is True and r.status == "SUCCEEDED"
    assert r.cross_family_verified is True and r.verification_kind == "cross_family_model"
    assert r.structural_check_passed is True
    assert r.verifier_model == {"provider": "fake-provider", "model_id": "mistral-large-2411", "family": "mistral"}
    assert len(r.cross_family_receipts) == 3
    for rec in r.cross_family_receipts:
        assert rec["status"] == "verified_pass" and rec["invoked"] is True and rec["verdict"] == "PASS"
        assert rec["verifier_provider"] == "fake-provider" and rec["verifier_model_id"] == "mistral-large-2411"
        assert rec["verifier_family"] == "mistral" and rec["worker_family"] == "gemma"
        assert rec["checked_output_sha256"] == _sha({"output": f"did {rec['subtask_id']}"})
        assert len(rec["sha256_receipt"]) == 64 and rec["reason"] == "postcondition holds"


@pytest.mark.asyncio
async def test_node_level_fields_are_separate():
    fake = FakeVerifier("PASS")
    planner = SwarmDAGPlanner(proposer_family="gemma", max_repairs=0, verifier_adapter=_adapter(fake, model_id="qwen3-32b"))
    sub = planner.phase1_decompose("g")[0]
    await planner.execute_subtask(sub, executor_fn=_executor, checker_fn=lambda p: True)
    assert sub.structural_check["kind"] == "custom_checker" and sub.structural_check["cross_family"] is False
    assert sub.cross_family_verified is True and sub.cross_family_receipt["verifier_family"] == "qwen"
    assert sub.verification_receipt == {"structural_check": sub.structural_check, "cross_family": sub.cross_family_receipt}
    assert len(fake.calls) == 1
