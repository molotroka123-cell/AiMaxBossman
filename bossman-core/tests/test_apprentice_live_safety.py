"""Real filesystem/subprocess and restart boundaries for live Apprentice P0."""
from __future__ import annotations

import multiprocessing as mp
import os
import sys
from pathlib import Path

import pytest

from bossman.apprentice.durable import DurableSafetyStore
from bossman.apprentice.guards import ApprovalRegistry, SideEffectLedger
from bossman.apprentice.live_workspace import LiveWorkspace, WorkspaceRefused


def _claim_then_die(path: str, effect: str) -> None:
    store = DurableSafetyStore(path)
    assert store.claim_side_effect(effect)[0]
    # Do not close or abandon: process termination models a crash after claim.
    os._exit(0)


@pytest.mark.restart
def test_claim_survives_real_process_restart(tmp_path: Path):
    path = str(tmp_path / "safety.db")
    proc = mp.get_context("spawn").Process(target=_claim_then_die, args=(path, "send-1"))
    proc.start(); proc.join(20)
    assert proc.exitcode == 0
    second = DurableSafetyStore(path)
    assert second.claim_side_effect("send-1") == (False, None)


@pytest.mark.restart
def test_nonce_cooldown_pending_and_reliability_survive_recreation(tmp_path: Path):
    path = tmp_path / "safety.db"
    first = DurableSafetyStore(path, clock=lambda: 100.0)
    assert first.consume_nonce_once("n")
    first.set_cooldown("PUBLIC@example.test", 200.0)
    first.save_pending_approval("task", {"recipient": "public@example.test", "digest": "d"})
    assert first.record_teacher_outcome("claude@test", -0.25) == (0.25, 1)
    first.close()
    second = DurableSafetyStore(path)
    assert not second.consume_nonce_once("n")
    assert second.get_cooldown("public@example.test") == 200.0
    assert second.resume_pending_approval("task", consume=True) == {"digest": "d", "recipient": "public@example.test"}
    assert second.resume_pending_approval("task") is None
    assert second.teacher_outcome("claude@test") == (0.25, 1)


def test_durable_registry_rejects_replayed_nonce(tmp_path: Path):
    store = DurableSafetyStore(tmp_path / "safety.db")
    registry = ApprovalRegistry(store=store)
    from bossman.company.model import ApprovalDecision
    d = ApprovalDecision(True, "human:owner", digest="d", scope="task", nonce="n")
    assert registry.validate(d, digest="d", scope="task") == ""
    registry.consume(d)
    assert "already consumed" in registry.validate(d, digest="d", scope="task")


def test_live_workspace_applies_unified_diff_and_refuses_escape(tmp_path: Path):
    (tmp_path / "app").mkdir(); (tmp_path / "tests").mkdir()
    (tmp_path / "app" / "calc.py").write_text("def add(a, b):\n    return 0\n", encoding="utf-8")
    (tmp_path / "tests" / "test_calc.py").write_text("def test_add(): pass\n", encoding="utf-8")
    ws = LiveWorkspace(tmp_path, allowed_paths=("app", "tests"), protected_paths=("tests/test_calc.py",))
    ws.apply("--- a/app/calc.py\n+++ b/app/calc.py\n@@ -1,2 +1,2 @@\n def add(a, b):\n-    return 0\n+    return a + b\n")
    assert "return a + b" in ws.read("app/calc.py")
    with pytest.raises(WorkspaceRefused): ws.apply("--- a/tests/test_calc.py\n+++ b/tests/test_calc.py\n@@ -1 +1 @@\n-def test_add(): pass\n+def test_add(): assert True\n")
    with pytest.raises(WorkspaceRefused): ws.write("../outside.py", "x")
    outside = tmp_path.parent / "outside-target"
    outside.mkdir(exist_ok=True)
    try:
        (tmp_path / "app" / "escape").symlink_to(outside, target_is_directory=True)
    except OSError:
        pytest.skip("symlink creation unavailable on this Windows account")
    with pytest.raises(WorkspaceRefused): ws.write("app/escape/pwn.py", "x")


def test_claude_client_uses_real_stub_subprocess_and_discards_hidden_reasoning(tmp_path: Path):
    stub = tmp_path / "stub.py"
    stub.write_text("import json; print(json.dumps({'diff':'--- a/app/x.py\\n+++ b/app/x.py\\n@@ -1 +1 @@\\n-x=0\\n+x=1\\n','chain_of_thought':'never persist','root_cause':'bad operator'}))", encoding="utf-8")
    from bossman.apprentice.claude_code_client import ClaudeCodeClient
    client = ClaudeCodeClient(tmp_path, command=(sys.executable, str(stub)))
    result = client.run({"files": {"app/x.py": "x=0"}})
    assert "diff" in result and result["commands"] == [] and "chain_of_thought" not in result
