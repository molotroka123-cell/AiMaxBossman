"""The Windows archive's embeddable Python ignores PYTHONPATH and keeps the cwd
off sys.path. ``python -I`` has the same two properties, so it emulates that
runtime here: every test process the evolution code starts must still import
the checkout's own code and report a real verdict under it.
"""
from __future__ import annotations

import subprocess
import sys

import pytest

from test_evolution_runner import host_run, project, proposal  # noqa: F401 — fixture
from test_evolution_verifier import GOOD, MONEY, check, make_diff, repo  # noqa: F401 — fixture
from bossman.apprentice import local_sidecar
from bossman_v3.self_improvement import gate_fixture as fx
from bossman_v3.self_improvement import verifier as v


@pytest.fixture
def embedded(monkeypatch):
    monkeypatch.setattr(local_sidecar, "_interpreter", lambda: [sys.executable, "-I"])


def test_emulation_is_real_a_bare_module_run_cannot_import_the_checkout(repo, tmp_path):
    src, _sha = repo
    res = subprocess.run([sys.executable, "-I", "-m", "unittest", "tests.test_money"], cwd=src,
                         capture_output=True, text=True, timeout=120)
    assert res.returncode != 0 and "No module named" in res.stderr


@pytest.mark.parametrize("runner", ["unittest", "pytest"])
def test_verifier_passes_a_real_fix_under_the_embedded_runtime(embedded, repo, tmp_path, runner):
    if runner == "pytest":
        pytest.importorskip("pytest")
    src, sha = repo
    rec = check(src, sha, tmp_path, make_diff(src, sha, tmp_path, **GOOD), runner=v.GuardedHostRunner(runner=runner))
    assert rec["runner"]["python"].endswith("-I")
    assert rec["verdict"] == "PASS", rec["reasons"]
    # the baseline really imported money.py and really failed on the defect
    assert "FAIL" in rec["checks"]["baseline"]["tests"].values() or "ERROR" in rec["checks"]["baseline"]["tests"].values()
    assert rec["checks"]["negative_control_failing"]


def test_verifier_rejects_bad_patches_under_the_embedded_runtime(embedded, repo, tmp_path):
    src, sha = repo
    vacuous = "import unittest\n\n\nclass Nothing(unittest.TestCase):\n    def test_ok(self):\n        self.assertEqual(1, 1)\n"
    rec = check(src, sha, tmp_path / "a", make_diff(src, sha, tmp_path / "a", edits=[fx.MONEY_FIX],
                                                    writes={"tests/test_regress_money.py": vacuous}))
    assert rec["verdict"] == "INVALID_TEST", rec["reasons"]
    fake = {"path": "money.py", "old": '"""Money amounts', "new": '"""Fixed! Money amounts'}
    rec = check(src, sha, tmp_path / "b", make_diff(src, sha, tmp_path / "b", edits=[fake],
                                                    writes={"tests/test_regress_money.py": fx.REGRESS_MONEY}))
    assert rec["verdict"] == "FAIL" and rec["checks"]["candidate"]["status"] == "FAIL"


def test_aster_host_executor_imports_the_checkout_under_the_embedded_runtime(embedded, project):
    pytest.importorskip("pytest")
    repo_, suite, work = project
    result = host_run(repo_, suite, work, proposer=lambda *a: (proposal(), 0.1), iterations=1)
    assert result["status"] == "CANDIDATE_PASSES", result.get("last_baseline")
    detail = result["last_baseline"]["repair"]["detail"]
    assert "ModuleNotFoundError" not in detail
