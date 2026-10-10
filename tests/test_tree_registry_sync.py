"""tools/tree_registry_sync.py: leaves are added only for real files; evidence levels are separate and never inferred."""
from __future__ import annotations

import importlib.util
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location("tree_registry_sync", ROOT / "tools" / "tree_registry_sync.py")
trs = importlib.util.module_from_spec(spec)
sys.modules["tree_registry_sync"] = trs
spec.loader.exec_module(trs)


def _repo(tmp_path, monkeypatch):
    (tmp_path / "command-center" / "bcc").mkdir(parents=True)
    (tmp_path / "pkg").mkdir()
    (tmp_path / "pkg" / "mod.py").write_text('"""Does the thing."""\n', encoding="utf-8")
    (tmp_path / "pkg" / "t.py").write_text("", encoding="utf-8")
    seed = tmp_path / "command-center" / "bcc" / "capability_tree_seed.json"
    seed.write_text(json.dumps({"nodes": [{"id": "bossman", "parent": "", "status": "code", "label": "B", "sources": []},
                                          {"id": "media", "parent": "bossman", "status": "code", "label": "M", "sources": []}]}, indent=2) + "\n", encoding="utf-8")
    monkeypatch.setattr(trs, "ROOT", tmp_path)
    monkeypatch.setattr(trs, "SEED", seed)
    return seed


def _manifest(tmp_path, **kw):
    e = {"slug": "x", "label": "X", "parent": "media", "source": "pkg/mod.py", "tests": ["pkg/t.py"], **kw}
    m = tmp_path / "m.json"
    m.write_text(json.dumps([e]), encoding="utf-8")
    return m


def test_add_takes_the_detail_from_the_docstring_and_is_idempotent(tmp_path, monkeypatch):
    seed = _repo(tmp_path, monkeypatch)
    m = _manifest(tmp_path)
    assert trs.cmd_add(m) == 0 and trs.cmd_add(m) == 0
    nodes = json.loads(seed.read_text(encoding="utf-8"))["nodes"]
    n = [x for x in nodes if x["id"] == "reg-x"]
    assert len(n) == 1 and n[0]["status"] == "code" and n[0]["detail"].startswith("Does the thing.")
    assert "не доказаны" in n[0]["detail"] and n[0]["sources"][0]["path"] == "pkg/mod.py"


def test_add_refuses_a_missing_source_a_missing_test_and_an_unknown_zone(tmp_path, monkeypatch, capsys):
    seed = _repo(tmp_path, monkeypatch)
    before = seed.read_text(encoding="utf-8")
    assert trs.cmd_add(_manifest(tmp_path, source="pkg/nope.py")) == 3
    assert trs.cmd_add(_manifest(tmp_path, tests=["pkg/none.py"])) == 3
    assert trs.cmd_add(_manifest(tmp_path, parent="ghost-zone")) == 3
    assert seed.read_text(encoding="utf-8") == before            # nothing is written when anything is refused


def test_add_takes_an_honest_non_green_status_with_a_reason_and_never_green(tmp_path, monkeypatch, capsys):
    seed = _repo(tmp_path, monkeypatch)
    before = seed.read_text(encoding="utf-8")
    assert trs.cmd_add(_manifest(tmp_path, status="reported", detail="x" * 30)) == 3      # green only from receipts
    assert trs.cmd_add(_manifest(tmp_path, status="working", detail="x" * 30)) == 3
    assert trs.cmd_add(_manifest(tmp_path, status="blocked")) == 3                         # a block needs its reason
    assert seed.read_text(encoding="utf-8") == before
    assert trs.cmd_add(_manifest(tmp_path, status="blocked", detail="HIP launch failure on the 8060S after 8.5 min")) == 0
    n = [x for x in json.loads(seed.read_text(encoding="utf-8"))["nodes"] if x["id"] == "reg-x"][0]
    assert n["status"] == "blocked" and n["detail"] == "HIP launch failure on the 8060S after 8.5 min"


ROW = {"id": "a", "verdict": "covered", "passed": 3, "failed": 0, "skipped": 0, "tests": ["t/test_a.py"], "basis": "import-or-path"}


def test_a_passing_test_is_not_ci_and_not_the_owners_pc():
    lv = trs.level_states(ROW, {}, {}, "a", True)
    assert lv["tests"]["state"] == "PASSED_RECORDED_RUN" and lv["ci"]["state"] == "NOT_RUN" and lv["owner_pc"]["state"] == "NOT_RUN"
    assert trs.proven_through(lv) == "tests"


def test_ci_needs_an_evidence_file_naming_every_test_of_the_leaf():
    ci = {"sha": "abc", "run_url": "https://x/run/1", "green_tests": ["t/test_a.py"]}
    assert trs.level_states(ROW, ci, {}, "a", True)["ci"]["state"] == "GREEN_ON_SHA"
    assert trs.level_states({**ROW, "tests": ["t/test_a.py", "t/test_b.py"]}, ci, {}, "a", True)["ci"]["state"] == "NOT_RUN"   # one test missing from CI


def test_a_later_level_never_counts_without_the_earlier_ones():
    owner = {"a": "https://evidence/1"}
    failing = trs.level_states({**ROW, "failed": 1}, {}, owner, "a", True)
    assert failing["tests"]["state"] == "FAILED" and trs.proven_through(failing) == "code"      # owner level present, tests failed: stops at code
    assert trs.proven_through(trs.level_states(ROW, {"sha": "s", "green_tests": ["t/test_a.py"]}, owner, "a", True)) == "owner_pc"


def test_missing_file_untested_and_unknown_leaf_are_reported_as_such():
    assert trs.level_states(None, {}, {}, "z", False)["code"]["state"] == "MISSING"
    assert trs.proven_through(trs.level_states(None, {}, {}, "z", False)) == "none"
    assert trs.level_states({**ROW, "verdict": "untested", "passed": 0, "tests": []}, {}, {}, "a", True)["tests"]["state"] == "NO_TEST"


def test_registry_rows_carry_every_required_field(tmp_path, monkeypatch):
    seed = _repo(tmp_path, monkeypatch)
    trs.cmd_add(_manifest(tmp_path))
    audit = tmp_path / "audit.json"
    audit.write_text(json.dumps({"rows": [{**ROW, "id": "reg-x"}]}), encoding="utf-8")
    out = tmp_path / "reg.json"
    assert trs.cmd_registry(audit, out, None, None) == 0
    reg = json.loads(out.read_text(encoding="utf-8"))
    row = [r for r in reg["leaves"] if r["id"] == "reg-x"][0]
    assert {"id", "source", "sha", "integration_status", "levels", "proven_through", "evidence"} <= set(row)
    assert row["proven_through"] == "tests" and row["levels"]["ci"]["state"] == "NOT_RUN"
    assert "bossman" not in {r["id"] for r in reg["leaves"]}                 # the root is not a leaf
