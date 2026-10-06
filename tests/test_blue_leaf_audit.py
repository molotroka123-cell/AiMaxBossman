"""tools/blue_leaf_audit.py: path -> module mapping, test attribution, JUnit reading (incl. re-run replacement), verdicts."""
from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location("blue_leaf_audit", ROOT / "tools" / "blue_leaf_audit.py")
bla = importlib.util.module_from_spec(spec)
sys.modules["blue_leaf_audit"] = bla
spec.loader.exec_module(bla)


def test_dotted_maps_source_paths_to_import_names():
    assert bla.dotted("command-center/bcc/pit/secret_filter.py") == "bcc.pit.secret_filter"
    assert bla.dotted("bossman-core/bossman/benchmark/engine.py") == "bossman.benchmark.engine"
    assert bla.dotted("command-center/bcc/pit/__init__.py") == "bcc.pit"
    assert bla.dotted("apps/poker-vision/pokervision/coach.py") == "pokervision.coach"
    assert bla.dotted("command-center/ui/pages/poker_vision.js") is None


def test_a_test_covers_a_module_it_imports_and_not_a_sibling():
    imports = {"t/test_a.py": {"bcc.pit.secret_filter", "os"}, "t/test_b.py": {"bcc.pit.router"},
               "t/test_c.py": {"bcc.pit.secret_filter.helpers"}}
    got = bla.tests_for("command-center/bcc/pit/secret_filter.py", imports, {})
    assert got == ["t/test_a.py", "t/test_c.py"]            # the sibling-importing test is not attributed
    assert bla.tests_for("command-center/bcc/pit/unused.py", imports, {}) == []


def test_scripts_are_attributed_by_path_mention():
    texts = {"tests/test_x.py": 'spec_from_file_location("x", ROOT / "tools/owner_one_bossman.py")', "tests/test_y.py": "nothing"}
    assert bla.tests_for("tools/owner_one_bossman.ps1", {}, texts) == []      # extension matters
    assert bla.tests_for("tools/owner_one_bossman.py", {}, texts) == ["tests/test_x.py"]


def _junit(path: Path, cases):
    body = "".join(f'<testcase classname="{c}" name="{n}">{extra}</testcase>' for c, n, extra in cases)
    path.write_text(f'<?xml version="1.0"?><testsuites><testsuite>{body}</testsuite></testsuites>', encoding="utf-8")


def test_junit_is_resolved_to_repo_paths_and_failures_are_counted(tmp_path, monkeypatch):
    (tmp_path / "command-center" / "tests").mkdir(parents=True)
    (tmp_path / "command-center" / "tests" / "test_m.py").write_text("", encoding="utf-8")
    monkeypatch.setattr(bla, "ROOT", tmp_path)
    j = tmp_path / "cc.xml"
    _junit(j, [("tests.test_m", "a", ""), ("tests.test_m.TestK", "b", "<failure/>"), ("tests.test_m", "c", "<skipped/>"),
               ("tests.not_a_file", "d", "")])
    res = bla.read_junit([j])
    assert dict(res) == {"command-center/tests/test_m.py": {"passed": 1, "failed": 1, "skipped": 1}}


def test_a_rerun_replaces_the_original_result_of_the_same_file(tmp_path, monkeypatch):
    (tmp_path / "bossman-core" / "tests").mkdir(parents=True)
    (tmp_path / "bossman-core" / "tests" / "test_b.py").write_text("", encoding="utf-8")
    monkeypatch.setattr(bla, "ROOT", tmp_path)
    first, again = tmp_path / "core.xml", tmp_path / "core-rerun.xml"
    _junit(first, [("tests.test_b", "x", "<failure/>"), ("tests.test_b", "y", "<failure/>")])
    _junit(again, [("tests.test_b", "x", ""), ("tests.test_b", "y", "")])
    assert dict(bla.read_junit([first]))["bossman-core/tests/test_b.py"]["failed"] == 2       # without the re-run it stays red
    assert dict(bla.read_junit([first, again]))["bossman-core/tests/test_b.py"] == {"passed": 2, "failed": 0, "skipped": 0}
    assert dict(bla.read_junit([again, first]))["bossman-core/tests/test_b.py"]["failed"] == 0  # file order does not matter


def test_verdicts_never_claim_more_than_the_run_shows():
    leaf = {"sources": ["command-center/bcc/x.py"]}
    ok = {"t.py": {"passed": 3, "failed": 0, "skipped": 0}}
    assert bla.classify(leaf, ["t.py"], ok)["verdict"] == "covered"
    assert bla.classify(leaf, ["t.py"], {"t.py": {"passed": 2, "failed": 1, "skipped": 0}})["verdict"] == "fix"
    assert bla.classify(leaf, ["t.py"], {"t.py": {"passed": 0, "failed": 0, "skipped": 4}})["verdict"] == "covered-skipped"   # all skipped is not a pass
    assert bla.classify(leaf, ["t.py"], {})["verdict"] == "covered-skipped"          # tests exist, none executed
    assert bla.classify(leaf, [], {})["verdict"] == "untested"
    assert bla.classify({"sources": ["x/app.js"]}, [], {})["verdict"] == "non-python"
    assert bla.classify({"sources": []}, [], {})["verdict"] == "unclear"


def test_a_module_re_exported_by_its_package_is_attributed_to_tests_that_import_the_package(tmp_path, monkeypatch):
    pkg = tmp_path / "bossman-core" / "bossman" / "guard"
    pkg.mkdir(parents=True)
    (pkg / "__init__.py").write_text("from .service import run\nfrom . import other\n", encoding="utf-8")
    (pkg / "service.py").write_text("def run(): ...\n", encoding="utf-8")
    (pkg / "hidden.py").write_text("x = 1\n", encoding="utf-8")
    monkeypatch.setattr(bla, "ROOT", tmp_path)
    imports = {"t/test_guard.py": {"bossman.guard"}}
    assert bla.tests_for("bossman-core/bossman/guard/service.py", imports, {}) == ["t/test_guard.py"]
    assert bla.tests_for("bossman-core/bossman/guard/hidden.py", imports, {}) == []          # not re-exported: still untested
    assert bla.reexported_by_package("bossman-core/bossman/guard/__init__.py") is None
