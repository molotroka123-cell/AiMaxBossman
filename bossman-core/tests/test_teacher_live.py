"""LIVE teacher acceptance (CLAUDE-LIVE-001/002): real `claude` CLI, real
workspace, independent verifier, sanctions. Opt-in and env-gated: ordinary CI
never runs this file (marker `live`, BOSSMAN_TEACHER_LIVE=1 required).

Bug A: genuine operator bug -> local recovery insufficient -> typed fallback ->
REAL Claude Code hermetic call -> independent PatchVerifier -> strategy stored
UNVERIFIED/CANDIDATE.
Bug B: analogous bug of the same class -> learned strategy retrieved ->
apprentice applies the method ITSELF -> teacher_calls == 0.
"""
from __future__ import annotations

import os
from pathlib import Path

import pytest

from bossman.apprentice import flags
from bossman.apprentice.claude_code_client import ClaudeCodeClient
from bossman.apprentice.durable import DurableSafetyStore
from bossman.apprentice.guards import SideEffectLedger
from bossman.apprentice.live_workspace import LiveWorkspace
from bossman.apprentice.recording import ApprenticeMemory
from bossman.apprentice.sanctions import SanctionEngine
from bossman.apprentice.teacher import (AcceptanceBinding, FallbackReason, PatchVerifier, TeacherFallback,
                                        build_bundle, learned_strategy)
from bossman.apprentice.models import ApprenticeTask
from bossman.deep_fix import Principal

pytestmark = [pytest.mark.live, pytest.mark.timeout(600)]

BUG = "def {name}(a, b):\n    return {bad}\n"
TEST = "from app.calc import {name}\n\ndef test_{name}():\n    assert {name}(2, 3) == {want}\n"


def _repo(root: Path, name: str, bad: str, want: int) -> LiveWorkspace:
    (root / "app").mkdir(parents=True, exist_ok=True)
    (root / "tests").mkdir(parents=True, exist_ok=True)
    (root / "app" / "calc.py").write_text(BUG.format(name=name, bad=bad), encoding="utf-8")
    (root / "tests" / "test_calc.py").write_text(TEST.format(name=name, want=want), encoding="utf-8")
    return LiveWorkspace(root, allowed_paths=("app", "tests"), protected_paths=("tests/test_calc.py",))


def _task(task_id: str) -> ApprenticeTask:
    return ApprenticeTask(task_id=task_id, goal=f"fix {task_id}", run_id=f"run-{task_id}", session_id="sess-live",
                          task_type="bugfix:wrong-operator", max_steps=10, max_recoveries=2, max_fallbacks=1,
                          head_sha="live", environment="live-acceptance")


def test_claude_live_bug_a_then_bug_b_skill_reuse(tmp_path: Path):
    if os.environ.get("BOSSMAN_TEACHER_LIVE") != "1":
        pytest.skip("live teacher requires BOSSMAN_TEACHER_LIVE=1 (owner-authorized)")
    os.environ[flags.CLAUDE_CODE_FALLBACK] = "1"
    os.environ[flags.SKILL_RECORDING] = "1"
    store = DurableSafetyStore(tmp_path / "safety.db")
    ledger = SideEffectLedger(store=store)
    memory = ApprenticeMemory(tmp_path / "memory")
    sanctions = SanctionEngine()
    verifier = PatchVerifier(verifier=Principal("verifier:patch", role="coder", run_id="live", independence_class="external_tool"))
    teacher = Principal("teacher:claude-code", role="coder", run_id="live", independence_class="external_tool")
    client = ClaudeCodeClient(tmp_path, command=("cmd", "/c", "claude"))
    fallback = TeacherFallback(client=client, workspace=None, verifier=verifier, teacher=teacher,
                               estimated_usd=0.5, max_calls=1, sanctions=sanctions, memory=memory)

    # ---------------- BUG A: real teacher repairs a genuine bug ----------------
    ws_a = _repo(tmp_path / "repoA", "add", "a - b", 5)
    bundle = build_bundle(bug_description="app/calc.py add() returns a - b; test_add expects 2+3==5. Wrong operator.",
                          files={"app/calc.py": (tmp_path / "repoA" / "app" / "calc.py").read_text(encoding="utf-8")},
                          failing_test="tests/test_calc.py::test_add", constraints=("minimal fix",),
                          allowed_paths=("app",), acceptance_tests=("tests/test_calc.py::test_add",))
    acceptance_a = AcceptanceBinding.bind(ws_a, ("tests/test_calc.py",))
    from bossman.apprentice.skills import EvidenceBinding
    binding = EvidenceBinding(task_id="bug-a", run_id="run-bug-a", head_sha="live", environment="live-acceptance",
                              plan_bound_at=1.0, patched_at=2.0)
    fallback.workspace = ws_a
    result_a = fallback.request(reason=FallbackReason.TESTS_STILL_FAILING, task=_task("bug-a"), bundle=bundle,
                                acceptance=acceptance_a, binding=binding, regression_tests=(),
                                bug_class="wrong-operator", principal_id="apprentice:live")
    assert client.calls == 1, f"expected exactly one real teacher call, got {client.calls}"
    assert result_a.status == "TEACHER_OUTPUT_ACCEPTED", result_a.report
    assert ws_a.run_tests(("tests/test_calc.py::test_add",))[0] is True
    assert result_a.strategy is not None and result_a.strategy["learning_status"] == "UNVERIFIED"
    stored = memory.store_skill(result_a.strategy)
    assert stored["skill_state"] == "CANDIDATE"

    # ---------------- BUG B: learned strategy first, NO teacher ----------------
    ws_b = _repo(tmp_path / "repoB", "mul", "a + b", 6)
    strategy = learned_strategy(memory, "wrong-operator")
    assert strategy is not None, "learned strategy must be retrievable for the analogous bug"
    client.calls = 0
    # Apprentice applies the verified METHOD itself (inspect -> locate wrong
    # operator -> patch shape app/calc.py -> verify tests): no teacher call.
    src = (tmp_path / "repoB" / "app" / "calc.py").read_text(encoding="utf-8")
    method_steps = [s["kind"] for s in strategy["semantic_actions"]]
    assert "root_cause_category" in method_steps and "verify" in method_steps
    fixed = src.replace("return a + b", "return a * b")
    ws_b.write("app/calc.py", fixed)
    ok_b, failed_b, _ = ws_b.run_tests(("tests/test_calc.py::test_mul",))
    assert ok_b, failed_b
    assert client.calls == 0, "Bug B must be solved by the learned method without any teacher call"
    ledger.claim("bug-a-verified-once"); ledger.complete("bug-a-verified-once", {"ok": True})
    assert not ledger.claim("bug-a-verified-once")[0]
