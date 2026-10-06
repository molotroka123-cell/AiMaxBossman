import json

import pytest

from pokervision.model_registry import ProfileRegistry


def metrics(wrong=0, unknown=0, n=100):
    one = {"ok": n - wrong - unknown, "wrong": wrong, "unknown": unknown}
    return {f: dict(one) for f in ("hero_cards", "board", "pot", "hero_stack", "street")}


@pytest.fixture()
def reg(tmp_path):
    p = tmp_path / "base.json"; p.write_text("{}")
    r = ProfileRegistry(tmp_path / "reg"); r.bootstrap("v1", p)
    return r, tmp_path


def propose(r, tmp, name, table):
    f = tmp / f"{name}.json"; f.write_text(json.dumps({"n": name}))
    return r.propose(name, f, lambda path: table[path.name])


def test_better_candidate_is_promoted_and_can_be_rolled_back(reg):
    r, tmp = reg
    d = propose(r, tmp, "v2", {"v1": metrics(wrong=4, unknown=10), "v2": metrics(wrong=1, unknown=10)})
    assert d.promoted and r.active() == "v2"
    assert r.rollback() == "v1" and r.active() == "v1"
    with pytest.raises(RuntimeError):
        r.rollback()


def test_candidate_with_a_critical_regression_is_rejected_even_if_it_answers_more(reg):
    r, tmp = reg
    d = propose(r, tmp, "v2", {"v1": metrics(wrong=0, unknown=30), "v2": metrics(wrong=3, unknown=0)})
    assert not d.promoted and "wrong rate" in d.reason and r.active() == "v1"
    assert "v2" not in r.versions()                                   # rejected candidate leaves no active trace


def test_candidate_that_abstains_everywhere_is_rejected(reg):
    r, tmp = reg
    d = propose(r, tmp, "v2", {"v1": metrics(wrong=1, unknown=5), "v2": metrics(wrong=0, unknown=80)})
    assert not d.promoted and "collapsed" in d.reason


def test_equal_candidate_is_not_promoted(reg):
    r, tmp = reg
    assert not propose(r, tmp, "v2", {"v1": metrics(wrong=1), "v2": metrics(wrong=1)}).promoted
