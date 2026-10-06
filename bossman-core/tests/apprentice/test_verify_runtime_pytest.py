"""Bossman's own check must not blame a candidate for the runtime's missing pytest.

Owner PC 06.10.2026, installed bundle d3fd6bcd: the free worker fixed `pit/discovery.py` correctly (the same two zone
tests pass under pytest, 66 passed), but the installed runtime has no pytest, `runner=auto` fell back to unittest, both
test modules died on `import pytest` and the task was recorded as `exit=1` - a false «candidate failed».
"""
from __future__ import annotations

import sys
import textwrap
import time
from pathlib import Path

import pytest

from bossman.apprentice import local_sidecar as ls

PYTEST_STYLE = textwrap.dedent("""
    import pytest

    def test_one():
        assert 1 + 1 == 2
    """)


def _run(root: Path, scratch: Path, name: str) -> dict:
    scratch.mkdir(exist_ok=True)
    return ls.tool_run_tests(ls.Workspace(root, ["."], []), {"paths": [name], "runner": "auto"},
                             scratch=scratch, deadline=time.monotonic() + 120, test_timeout=120)


def test_pytest_style_tests_without_pytest_are_reported_as_unrunnable_not_as_failed(tmp_path, monkeypatch):
    root = tmp_path / "work"
    root.mkdir()
    (root / "test_zone.py").write_text(PYTEST_STYLE, encoding="utf-8")
    monkeypatch.delenv("BOSSMAN_VERIFY_PYTHON", raising=False)
    monkeypatch.setattr(ls, "_pytest_available", lambda: False)
    with pytest.raises(ls.ToolError, match="pytest"):
        _run(root, tmp_path / "s", "test_zone.py")


def test_plain_unittest_tests_still_run_without_pytest(tmp_path, monkeypatch):
    root = tmp_path / "work"
    root.mkdir()
    (root / "test_plain.py").write_text(textwrap.dedent("""
        import unittest
        class T(unittest.TestCase):
            def test_ok(self):
                self.assertEqual(2, 2)
        """), encoding="utf-8")
    monkeypatch.delenv("BOSSMAN_VERIFY_PYTHON", raising=False)
    monkeypatch.setattr(ls, "_pytest_available", lambda: False)
    res = _run(root, tmp_path / "s", "test_plain.py")
    assert res["runner"] == "unittest" and res["passed"] is True, res["output_tail"]


def test_the_owner_can_point_the_check_at_an_interpreter_with_pytest(tmp_path, monkeypatch):
    if not ls._pytest_available():
        pytest.skip("this test process has no pytest to point at")
    root = tmp_path / "work"
    root.mkdir()
    (root / "test_zone.py").write_text(PYTEST_STYLE, encoding="utf-8")
    real = sys.executable
    # the product's own interpreter is unusable here; only the owner-chosen one can run the tests
    monkeypatch.setattr(sys, "executable", str(tmp_path / "no-such-python.exe"))
    monkeypatch.setenv("BOSSMAN_VERIFY_PYTHON", real)
    res = _run(root, tmp_path / "s", "test_zone.py")
    assert res["runner"] == "pytest" and res["passed"] is True, res["output_tail"]


def test_a_missing_override_interpreter_is_refused(tmp_path, monkeypatch):
    root = tmp_path / "work"
    root.mkdir()
    (root / "test_zone.py").write_text(PYTEST_STYLE, encoding="utf-8")
    monkeypatch.setenv("BOSSMAN_VERIFY_PYTHON", str(tmp_path / "missing" / "python.exe"))
    with pytest.raises(ls.ToolError, match="BOSSMAN_VERIFY_PYTHON"):
        _run(root, tmp_path / "s", "test_zone.py")
