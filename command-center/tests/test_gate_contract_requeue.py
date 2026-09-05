"""EH-05 (TZ-01 §2.5): результат gate_completion с verdict=FAIL обязан нести `requeue`.
Без него — CriticalHookFailure (fail-closed), а не молчаливый повтор run'а."""
from __future__ import annotations

import pytest

from bcc.engine import CriticalHookFailure, TaskEngine


def test_fail_without_requeue_is_malformed():
    assert TaskEngine._malformed_hook_result("gate_completion", {"verdict": "FAIL"}) is not None
    assert TaskEngine._malformed_hook_result("gate_completion", {"verdict": "FAIL", "requeue": False}) is None
    assert TaskEngine._malformed_hook_result("gate_completion", {"verdict": "FAIL", "requeue": True}) is None
    assert TaskEngine._malformed_hook_result("gate_completion", {"verdict": "PASS"}) is None
    assert TaskEngine._malformed_hook_result("gate_completion", {"verdict": "NOT_APPLICABLE"}) is None


async def test_engine_raises_on_fail_without_requeue(env):
    engine = env.svc.engine

    async def bad_gate(task, run_id, answer, messages):
        return {"verdict": "FAIL", "feedback": "нет"}

    engine.add_hook("gate_completion", bad_gate)
    with pytest.raises(CriticalHookFailure):
        await engine._call_hooks("gate_completion", {"id": 1}, 1, "x", [])
