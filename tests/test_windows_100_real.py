"""tools/windows_100_real.py decides PASS per nodeid, never by count alone.

Release blocker 2026-09-24: the gate collected from one rootdir and executed
from another, so every nodeid was "file not found". These tests pin the verdict
logic that makes such a mismatch — or a skip, a missing or an extra test — red.
"""
from __future__ import annotations

import importlib.util
import pathlib
from collections import OrderedDict

import pytest

ROOT = pathlib.Path(__file__).resolve().parents[1]
_spec = importlib.util.spec_from_file_location("windows_100_real", ROOT / "tools" / "windows_100_real.py")
w100 = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(w100)

IDS = [f"command-center/tests/test_x.py::test_{i}" for i in range(100)]


def _passed(ids):
    return [{"nodeid": n, "when": w, "outcome": "passed"} for n in ids for w in ("setup", "call", "teardown")]


def test_all_hundred_passed_is_pass():
    res = w100.verify(IDS, _passed(IDS))
    assert res["verdict"] == "PASS" and res["passed"] == 100


def test_nothing_ran_is_fail():
    # exactly the CI failure: pytest aborted on "file or directory not found"
    res = w100.verify(IDS, [])
    assert res["verdict"] == "FAIL" and len(res["missing"]) == 100


def test_skip_is_not_a_pass():
    recs = _passed(IDS[1:]) + [{"nodeid": IDS[0], "when": "setup", "outcome": "skipped"}]
    res = w100.verify(IDS, recs)
    assert res["verdict"] == "FAIL" and IDS[0] in res["not_passed"]


def test_failed_teardown_is_not_a_pass():
    recs = _passed(IDS) + [{"nodeid": IDS[5], "when": "teardown", "outcome": "failed"}]
    assert w100.verify(IDS, recs)["verdict"] == "FAIL"


def test_extra_or_renamed_nodeid_is_fail():
    ran = IDS[:99] + ["command-center/tests/test_x.py::test_other"]
    res = w100.verify(IDS, _passed(ran))
    assert res["verdict"] == "FAIL" and res["missing"] == [IDS[99]] and res["extra"] == [ran[-1]]


def test_duplicates_do_not_count_twice():
    ids = IDS[:99] + [IDS[0]]
    assert w100.verify(ids, _passed(ids))["verdict"] == "FAIL"


def test_round_robin_selection_covers_every_listed_file():
    per = OrderedDict([("a", ["a::1", "a::2", "a::3", "a::4"]), ("b", ["b::1"]), ("c", ["c::1", "c::2"])])
    assert w100.select(per, 5) == ["a::1", "b::1", "c::1", "a::2", "c::2"]
    assert len(w100.select(per, 99)) == 7          # short group is visible to the caller


def test_groups_are_repo_root_paths_that_exist_with_the_declared_quotas():
    assert [q for _, q, _ in w100.GROUPS] == [25, 20, 20, 20, 15]
    for _, _, paths in w100.GROUPS:
        for p in paths:
            project, rel = w100.split_project(p)
            assert project in w100.PROJECTS and (ROOT / p).exists(), p


def test_path_outside_a_project_is_refused():
    with pytest.raises(w100.GateError):
        w100.split_project("tests/telegram_contracts/test_companion.py")
