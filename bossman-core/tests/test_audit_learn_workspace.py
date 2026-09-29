"""Audit 2026-09-28 (learning/runtime): LiveWorkspace + PatchVerifier regressions.

1. a unified diff may not delete/rename/copy/binary-patch a path its `+++` lines do
   not name (the old check read only `+++`, so `+++ /dev/null` deleted a guard file);
2. protected paths are compared case-insensitively and Windows name aliases
   (trailing dot/space, alternate streams) are refused;
3. run_tests reaps the whole process tree on timeout, decodes UTF-8, and
   PatchVerifier.verify restores the worktree on ANY exception after apply.
"""
from __future__ import annotations

import subprocess
import sys
import time
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from bossman.apprentice.live_workspace import LiveWorkspace, WorkspaceRefused, diff_target_paths  # noqa: E402
from bossman.apprentice.skills import EvidenceBinding  # noqa: E402
from bossman.apprentice.teacher import (AcceptanceBinding, PatchVerifier, build_bundle, observe_teacher,  # noqa: E402
                                        patch_paths, security_findings)
from bossman.deep_fix import Principal  # noqa: E402

PROT = "from app.calc import add\n\n\ndef test_add():\n    assert add(2, 2) == 4\n"
GOOD = ("diff --git a/app/calc.py b/app/calc.py\n--- a/app/calc.py\n+++ b/app/calc.py\n@@ -1,2 +1,2 @@\n"
        " def add(a, b):\n-    return a - b\n+    return a + b\n")
DELETE_POLICY = ("diff --git a/bossman/policy.py b/bossman/policy.py\ndeleted file mode 100644\n"
                 "--- a/bossman/policy.py\n+++ /dev/null\n@@ -1 +0,0 @@\n-DENY = True\n")


def _repo(tmp_path: Path, **kw) -> LiveWorkspace:
    root = tmp_path / "repo"
    (root / "app").mkdir(parents=True); (root / "tests").mkdir(); (root / "bossman").mkdir()
    (root / "app" / "calc.py").write_text("def add(a, b):\n    return a - b\n", encoding="utf-8")
    (root / "tests" / "test_calc.py").write_text(PROT, encoding="utf-8")
    (root / "bossman" / "policy.py").write_text("DENY = True\n", encoding="utf-8")
    subprocess.run(["git", "init", "-q"], cwd=root, check=True)
    return LiveWorkspace(root, allowed_paths=kw.pop("allowed", ("app/", "tests/")),
                         protected_paths=("tests/test_calc.py",), **kw)


# ---------------------------------------------------------------- 1. every touched path
def test_diff_paths_include_deleted_renamed_copied_and_binary_sources():
    rename = "diff --git a/tests/test_calc.py b/app/moved.py\nsimilarity index 90%\nrename from tests/test_calc.py\nrename to app/moved.py\n"
    binary = "diff --git a/bossman/policy.py b/bossman/policy.py\nindex 1..2 100644\nGIT binary patch\nliteral 1\nIcmZQz00001\n\n"
    assert diff_target_paths(GOOD + DELETE_POLICY) == ["app/calc.py", "bossman/policy.py"]
    assert set(diff_target_paths(rename)) == {"tests/test_calc.py", "app/moved.py"}
    assert diff_target_paths(binary) == ["bossman/policy.py"]
    assert patch_paths(GOOD + DELETE_POLICY) == ["app/calc.py", "bossman/policy.py"]


def test_security_scan_sees_a_deleted_guard_file():
    found = security_findings(GOOD + DELETE_POLICY, allowed_paths=("app/",))
    assert any("protected path touched: bossman/policy.py" in f for f in found)
    assert any("out of scope: bossman/policy.py" in f for f in found)


def test_out_of_scope_deletion_is_refused_and_nothing_is_applied(tmp_path):
    ws = _repo(tmp_path, allowed=("app/",))
    with pytest.raises(WorkspaceRefused):
        ws.apply(GOOD + DELETE_POLICY)
    assert (ws.root / "bossman" / "policy.py").read_text(encoding="utf-8") == "DENY = True\n"
    assert "a - b" in ws.read("app/calc.py")


def test_rename_away_from_a_protected_test_is_refused(tmp_path):
    ws = _repo(tmp_path)
    patch = ("diff --git a/tests/test_calc.py b/app/moved.py\nsimilarity index 100%\n"
             "rename from tests/test_calc.py\nrename to app/moved.py\n")
    with pytest.raises(WorkspaceRefused):
        ws.apply(GOOD + patch)
    assert (ws.root / "tests" / "test_calc.py").read_text(encoding="utf-8") == PROT


def test_symlink_mode_is_refused(tmp_path):
    ws = _repo(tmp_path)
    patch = ("diff --git a/app/link b/app/link\nnew file mode 120000\n--- /dev/null\n+++ b/app/link\n"
             "@@ -0,0 +1 @@\n+../../outside\n\\ No newline at end of file\n")
    with pytest.raises(WorkspaceRefused, match="symlink"):
        ws.apply(patch)


def test_restore_undoes_an_in_scope_deletion(tmp_path):
    ws = _repo(tmp_path)
    token = ws.snapshot()
    ws.apply("diff --git a/app/calc.py b/app/calc.py\ndeleted file mode 100644\n--- a/app/calc.py\n+++ /dev/null\n"
             "@@ -1,2 +0,0 @@\n-def add(a, b):\n-    return a - b\n")
    assert not (ws.root / "app" / "calc.py").exists()
    ws.restore(token)
    assert "a - b" in ws.read("app/calc.py")


# ---------------------------------------------------------------- 2. protected-path aliases
@pytest.mark.parametrize("alias", ["tests/TEST_calc.py", "tests/test_calc.py.", "tests/test_calc.py ",
                                   "tests/test_calc.py:stream", "tests/test_calc.py::$DATA"])
def test_protected_path_aliases_are_refused(tmp_path, alias):
    ws = _repo(tmp_path)
    with pytest.raises(WorkspaceRefused):
        ws.write(alias, "def test_add():\n    pass\n")
    assert (ws.root / "tests" / "test_calc.py").read_text(encoding="utf-8") == PROT


def test_protected_case_alias_in_a_diff_is_refused(tmp_path):
    ws = _repo(tmp_path)
    patch = ("--- a/tests/TEST_calc.py\n+++ b/tests/TEST_calc.py\n@@ -1,5 +1,5 @@\n from app.calc import add\n \n \n"
             " def test_add():\n-    assert add(2, 2) == 4\n+    assert True\n")
    with pytest.raises(WorkspaceRefused, match="protected"):
        ws.apply(patch)


# ---------------------------------------------------------------- 3. tests subprocess
def test_run_tests_timeout_reaps_grandchild_holding_the_pipe(tmp_path):
    ws = _repo(tmp_path, test_command=(sys.executable,), timeout_s=2)
    (ws.root / "app" / "hang.py").write_text(
        "import subprocess, sys, time\n"
        "subprocess.Popen([sys.executable, '-c', 'import time; time.sleep(60)'])\n"
        "time.sleep(60)\n", encoding="utf-8")
    started = time.monotonic()
    passed, failed, excerpt = ws.run_tests(("app/hang.py",))
    assert time.monotonic() - started < 30
    assert passed is False and failed == ["app/hang.py"] and "timed out" in excerpt


def test_run_tests_decodes_utf8_output_on_any_locale(tmp_path):
    ws = _repo(tmp_path, test_command=(sys.executable,), timeout_s=30)
    (ws.root / "app" / "say.py").write_text(
        "import sys\nsys.stdout.buffer.write('Иван 😀'.encode('utf-8'))\n", encoding="utf-8")
    passed, _failed, excerpt = ws.run_tests(("app/say.py",))
    assert passed is True and "Иван" in excerpt


class _ExplodingWorkspace(LiveWorkspace):
    def run_tests(self, ids):
        raise RuntimeError("verifier crashed mid-run")


def test_verify_restores_worktree_when_the_test_run_raises(tmp_path):
    root = _repo(tmp_path).root
    ws = _ExplodingWorkspace(root, allowed_paths=("app/", "tests/"), protected_paths=("tests/test_calc.py",))
    bundle = build_bundle(bug_description="add() subtracts", files={"app/calc.py": ws.read("app/calc.py")},
                          failing_test="tests/test_calc.py::test_add", constraints=("keep signature",),
                          allowed_paths=("app/",), acceptance_tests=("tests/test_calc.py",))
    obs = observe_teacher({"diff": GOOD, "model_id": "claude-code"})
    verifier = PatchVerifier(verifier=Principal("verifier:pytest", model_id="pytest", role="verifier",
                                                run_id="v", independence_class="external_tool"))
    teacher = Principal("teacher:claude-code", model_id="claude-code", role="coder", run_id="t",
                        independence_class="external_tool")
    with pytest.raises(RuntimeError, match="verifier crashed"):
        verifier.verify(bundle, obs, workspace=ws, teacher=teacher,
                        acceptance=AcceptanceBinding.bind(ws, ("tests/test_calc.py",)),
                        binding=EvidenceBinding("t1", "r1", "abc", "env"))
    assert "a - b" in ws.read("app/calc.py")          # the teacher patch did not stay applied
