"""PASS 2 acceptance: hermetic Claude Code teacher (TEACHER-ISO-001..005, TEACHER-LIVE-001)."""
from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
import textwrap
from pathlib import Path

import pytest

from bossman.apprentice.claude_code_client import ClaudeCodeClient, ClaudeProcessPolicy
from bossman.apprentice.live_workspace import LiveWorkspace, WorkspaceRefused
from bossman.apprentice.teacher import AcceptanceBinding, build_bundle
from bossman.apprentice.teacher_sandbox import CONTRACT_FILE, TeacherWorkspaceRefused, hermetic_workspace, isolation_level, scrubbed_env

pytestmark = pytest.mark.timeout(300)


def _bundle():
    return build_bundle(bug_description="add() subtracts", files={"app/calc.py": "def add(a, b):\n    return a - b\n"},
                        failing_test="tests/test_calc.py::test_add", constraints=("keep signature",), allowed_paths=("app/",),
                        acceptance_tests=("tests/test_calc.py",))


def _stub(tmp_path: Path, canary: Path) -> Path:
    """A real subprocess 'claude' that reports what it can see instead of answering."""
    stub = tmp_path / "claude"
    stub.write_text(textwrap.dedent(f"""\
        #!{sys.executable}
        import json, os, sys
        def probe(p):
            try:
                open(p).read(); return "READ"
            except Exception as exc:
                return type(exc).__name__
        print(json.dumps({{"cwd": os.getcwd(), "listing": sorted(os.listdir(".")),
            "argv": sys.argv[1:], "env_secret_keys": [k for k in os.environ if "TOKEN" in k or "SECRET" in k or "KEY" in k],
            "env_bossman_keys": [k for k in os.environ if k.startswith("BOSSMAN_") or k in ("PYTHONPATH", "GIT_DIR")],
            "canary_relative": probe("../canary.txt"), "canary_repo_relative": probe("../../canary.txt"),
            "canary_absolute": probe({str(canary)!r}),
            "diff": "--- a/app/calc.py\\n+++ b/app/calc.py\\n@@ -1,2 +1,2 @@\\n def add(a, b):\\n-    return a - b\\n+    return a + b\\n"}}))
        """), encoding="utf-8")
    stub.chmod(0o755)
    return stub


def test_teacher_iso_001_teacher_sees_only_the_bundle(tmp_path, monkeypatch):
    workspace = tmp_path / "repo"; workspace.mkdir()
    canary = workspace / "canary.txt"; canary.write_text("owner-secret-canary", encoding="utf-8")
    (workspace / ".env").write_text("BOSSMAN_TEST_SECRET_X=1", encoding="utf-8")
    monkeypatch.setenv("BOSSMAN_ROOT", str(workspace)); monkeypatch.setenv("MY_API_KEY", "k"); monkeypatch.setenv("PYTHONPATH", str(workspace))
    client = ClaudeCodeClient(workspace, command=(str(_stub(tmp_path, canary)),))
    out = client.run(_bundle().as_dict())
    seen = out["isolation"]
    assert seen["level"] == isolation_level() and seen["cwd"] != str(workspace) and "bossman-teacher-" in seen["cwd"]
    assert seen["files"] == ["TEACHER_CONTRACT.md", "app/calc.py"]
    assert not Path(seen["cwd"]).exists()                                          # destroyed after the call
    argv = out["argv"]
    assert "--disallowedTools" in argv and all(t in argv[argv.index("--disallowedTools") + 1] for t in ("Read", "Bash", "Glob", "Grep"))
    assert out["env_secret_keys"] == [] and out["env_bossman_keys"] == []
    assert out["canary_relative"] != "READ" and out["canary_repo_relative"] != "READ"
    assert sorted(out["listing"]) == ["TEACHER_CONTRACT.md", "app"]
    if isolation_level() == "bwrap":
        assert out["canary_absolute"] != "READ"
    else:  # host without bubblewrap: absolute reads are prevented by tool denial on the real CLI, not by the OS
        assert seen["denied_tools"]


def test_hermetic_workspace_refuses_escaping_bundle_paths(tmp_path):
    with pytest.raises(TeacherWorkspaceRefused):
        with hermetic_workspace({"files": {"../x.py": "1"}, "constraints": [], "failing_test": ""}):
            pass
    with pytest.raises(TeacherWorkspaceRefused):
        with hermetic_workspace({"files": {"/etc/passwd": "1"}, "constraints": [], "failing_test": ""}):
            pass
    with hermetic_workspace({"files": {"a/b.py": "x"}, "constraints": ["c"], "failing_test": "t"}) as hw:
        assert (hw.path / CONTRACT_FILE).exists() and not (hw.path / ".git").exists() and hw.files == ("TEACHER_CONTRACT.md", "a/b.py")
        assert not any(k.startswith("BOSSMAN_") or "KEY" in k for k in hw.env)
    assert not hw.path.exists()


def _repo(tmp_path: Path) -> LiveWorkspace:
    root = tmp_path / "verifier"; (root / "app").mkdir(parents=True); (root / "tests").mkdir()
    (root / "app" / "calc.py").write_text("def add(a, b):\n    return a - b\n", encoding="utf-8")
    (root / "tests" / "test_calc.py").write_text("from app.calc import add\n\n\ndef test_add():\n    assert add(2, 2) == 4\n", encoding="utf-8")
    subprocess.run(["git", "init", "-q"], cwd=root, check=True)
    return LiveWorkspace(root, allowed_paths=("app/", "tests/"), protected_paths=("tests/test_calc.py",))


GOOD = "--- a/app/calc.py\n+++ b/app/calc.py\n@@ -1,2 +1,2 @@\n def add(a, b):\n-    return a - b\n+    return a + b\n"
BAD_TEST = "--- a/tests/test_calc.py\n+++ b/tests/test_calc.py\n@@ -1,5 +1,5 @@\n from app.calc import add\n \n \n def test_add():\n-    assert add(2, 2) == 4\n+    assert True\n"


def test_teacher_iso_002_allowed_app_file_can_be_patched(tmp_path):
    ws = _repo(tmp_path)
    ws.apply(GOOD)
    assert "a + b" in ws.read("app/calc.py")


def test_teacher_iso_003_acceptance_tests_remain_immutable(tmp_path):
    ws = _repo(tmp_path)
    binding = AcceptanceBinding.bind(ws, ("tests/test_calc.py",))
    with pytest.raises(WorkspaceRefused):
        ws.apply(BAD_TEST)
    with pytest.raises(WorkspaceRefused):
        ws.apply({"tests/test_calc.py": "def test_add():\n    assert True\n"})
    assert binding.tampered(ws) == []


def test_teacher_iso_004_protected_path_rejected_at_workspace_layer(tmp_path):
    ws = _repo(tmp_path)
    with pytest.raises(WorkspaceRefused):
        ws.write("tests/test_calc.py", "def test_add():\n    assert True\n")          # direct write, not via PatchVerifier
    binding = AcceptanceBinding.bind(ws, ("tests/test_calc.py",))
    (ws.root / "tests" / "test_calc.py").write_text("tampered", encoding="utf-8")          # out-of-band tampering
    assert binding.tampered(ws) == ["tests/test_calc.py"]
    binding.restore(ws)                                                               # only the bound content, via restore=True
    assert binding.tampered(ws) == []


def test_teacher_iso_005_failed_patch_restores_verifier_worktree(tmp_path):
    ws = _repo(tmp_path)
    token = ws.snapshot()
    ws.apply(GOOD)
    (ws.root / "app" / "extra.py").write_text("junk", encoding="utf-8")
    ws.restore(token)
    assert "a - b" in ws.read("app/calc.py") and not (ws.root / "app" / "extra.py").exists()


def test_teacher_live_001_real_claude_smoke_or_blocked(tmp_path):
    exe = shutil.which("claude")
    if not exe:
        pytest.skip("BLOCKED_BY_ENVIRONMENT: no local `claude` executable")
    if os.environ.get("BOSSMAN_TEACHER_LIVE_SMOKE") != "1":
        pytest.skip("BLOCKED_BY_ENVIRONMENT: set BOSSMAN_TEACHER_LIVE_SMOKE=1 (owner-authorised paid smoke) to run the real teacher once")
    client = ClaudeCodeClient(tmp_path, command=(exe,), policy=ClaudeProcessPolicy(timeout_s=120, max_attempts=1))
    out = client.run(_bundle().as_dict())
    errors = " ".join(out.get("attempt_errors") or [])
    if any(word in (errors + out.get("log_text", "")).lower() for word in ("not logged in", "login", "auth", "api key", "unauthorized", "credit")):
        pytest.skip(f"BLOCKED_BY_ENVIRONMENT: claude present but not authenticated ({errors[:120]})")
    assert out["isolation"]["level"] == isolation_level() and "+++ b/app/calc.py" in (out.get("diff") or "")


def test_client_unwraps_claude_cli_result_envelope():
    envelope = json.dumps({"type": "result", "subtype": "success", "total_cost_usd": 0.0593, "duration_api_ms": 21623,
                           "stop_reason": "end_turn", "session_id": "s-1",
                           "result": "Here you go:\n```json\n{\"root_cause\": \"wrong operator\", \"diff\": \"--- a/app/calc.py\\n+++ b/app/calc.py\\n@@ -1,2 +1,2 @@\\n def add(a, b):\\n-    return a - b\\n+    return a + b\\n\", \"chain_of_thought\": \"hidden\"}\n```"})
    parsed = ClaudeCodeClient._json_or_text(envelope)
    assert "+++ b/app/calc.py" in parsed["diff"] and parsed["root_cause"] == "wrong operator"
    assert parsed["cost_usd"] == 0.0593 and parsed["duration_ms"] == 21623 and parsed["cli_session_id"] == "s-1"
