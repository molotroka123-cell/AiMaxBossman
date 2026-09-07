from __future__ import annotations

from bossman_shared.action_receipt import ActionReceipt


def _receipt(source: str) -> ActionReceipt:
    return ActionReceipt.from_v3(
        task_id="task-1",
        step_id="step-1",
        action_type="filesystem.write",
        effect_type="IDEMPOTENT_WRITE",
        args={"path": "x"},
        started_at="2026-09-07T00:00:00+00:00",
        finished_at="2026-09-07T00:00:01+00:00",
        observed_at="2026-09-07T00:00:02+00:00",
        executor_status="executed",
        observation_type="post_state",
        observation_ref=source,
        verification_status="VERIFIED",
        verification_reason="ok",
    )


def test_generic_tool_claim_is_downgraded_before_canonical_receipt():
    receipt = _receipt("tool_result")
    assert receipt.observation_type == "tool_result_only"
    assert receipt.verified() is False


def test_test_only_fake_source_cannot_satisfy_production_evidence():
    receipt = _receipt("fake")
    assert receipt.observation_type == "tool_result_only"
    assert receipt.verified() is False


def test_independent_verifier_source_remains_valid_post_state():
    receipt = _receipt("bcc.v2.verification")
    assert receipt.observation_type == "post_state"
    assert receipt.verified() is True
