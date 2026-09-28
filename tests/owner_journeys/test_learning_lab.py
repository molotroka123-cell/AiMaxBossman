import asyncio
import json

import pytest

from tools.owner_journeys import learning_lab as lab
from tools.owner_journeys import triage_dataset as ds


def test_held_out_is_disjoint_and_labels_follow_policy():
    lab.assert_disjoint(ds.EXAMPLES, ds.HELD_OUT)
    assert len(ds.HELD_OUT) >= 50
    mapping = {"faq": {"answer_from_facts"}, "booking": {"request_booking", "forward_to_owner"},
               "exchange_request": {"queue_operator_review"}, "medical": {"safe_referral"},
               "complaint": {"forward_to_owner"}, "out_of_scope": {"decline"}}
    for item in ds.EXAMPLES + ds.HELD_OUT:
        assert item["action"] in mapping[item["intent"]], item
        assert item["needs_human"] == (item["action"] in ds.HUMAN_ACTIONS)
        if item["business"] == "none":
            assert item["intent"] == "out_of_scope"


def test_overlap_is_rejected():
    with pytest.raises(ValueError):
        lab.assert_disjoint([{"text": ds.HELD_OUT[0]["text"].upper()}], ds.HELD_OUT)


def test_score_and_safety():
    med = next(i for i in ds.HELD_OUT if i["intent"] == "medical")
    ok = ds.score(med, {"business": med["business"], "intent": "medical", "action": "safe_referral"})
    assert ok["exact"] and ok["safety_ok"]
    bad = ds.score(med, {"business": med["business"], "intent": "faq", "action": "answer_from_facts"})
    assert not bad["exact"] and not bad["safety_ok"]
    ex = next(i for i in ds.HELD_OUT if i["intent"] == "exchange_request")
    assert not ds.score(ex, {"action": "answer_from_facts"})["safety_ok"]


def test_parse_answer_tolerates_wrapping():
    assert lab.parse_answer('Sure:\n```json\n{"business":"dental","intent":"faq","action":"answer_from_facts"}\n```') \
        == {"business": "dental", "intent": "faq", "action": "answer_from_facts"}
    assert lab.parse_answer("no json") is None


def test_examples_export_and_tamper_detection(tmp_path):
    path = lab.export_examples(tmp_path)
    rows = lab.load_examples(tmp_path)
    assert len(rows) == len(ds.EXAMPLES) and all(r["privacy"] == "FAKE_NO_PII" for r in rows)
    lines = path.read_text(encoding="utf-8").splitlines()
    first = json.loads(lines[0])
    first["action"] = "decline"
    path.write_text("\n".join([json.dumps(first)] + lines[1:]) + "\n", encoding="utf-8")
    with pytest.raises(ValueError):
        lab.load_examples(tmp_path)


def test_candidate_prompt_contains_examples_baseline_does_not():
    base = lab.system_prompt("baseline", [])
    cand = lab.system_prompt("candidate", ds.EXAMPLES)
    assert ds.EXAMPLES[0]["text"] not in base and ds.EXAMPLES[0]["text"] in cand
    assert all(h["text"] not in cand for h in ds.HELD_OUT)


class _FakeRunner:
    name = "fake"

    def __init__(self, good_for):
        self.good_for = good_for

    async def ask(self, variant, system, text):
        item = next(i for i in ds.HELD_OUT if i["text"] == text)
        if variant in self.good_for:
            return json.dumps({k: item[k] for k in ds.FIELDS}), 0.01
        return json.dumps({"business": item["business"], "intent": "faq", "action": "answer_from_facts"}), 0.02


def test_evaluate_and_compare_detects_gain_and_safety_regression():
    items = ds.HELD_OUT[:10]
    base = asyncio.run(lab.evaluate(_FakeRunner({"candidate"}), "baseline", "", items))
    cand = asyncio.run(lab.evaluate(_FakeRunner({"candidate"}), "candidate", "", items))
    cmp = lab.compare(base, cand)
    assert cand["exact"] == 10 and cmp["delta_exact"] == 10 - base["exact"]
    assert not cmp["safety_regression"]
    assert lab.compare(cand, base)["safety_regression"] == bool(base["safety_violations"])
