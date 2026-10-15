"""Offline invariants for the Agent15 synthetic evaluation."""

import json
from pathlib import Path

from tools import agent15_eval as agent15


ROOT = Path(__file__).resolve().parents[1]
DATA = ROOT / "docs" / "learning" / "agent15"


def _data():
    curriculum = json.loads((DATA / "curriculum.json").read_text(encoding="utf-8"))
    heldout = json.loads((DATA / "heldout.json").read_text(encoding="utf-8"))
    return curriculum, heldout


def test_every_zone_has_two_unseen_cases_and_disjoint_splits():
    curriculum, heldout = _data()
    zones = curriculum["zones"]
    cases = heldout["cases"]
    assert len(zones) == 15
    assert len(cases) == 30
    assert len({row["id"] for row in cases}) == 30
    balanced = agent15.select_cases(cases, zones, "balanced")
    transfer = agent15.select_cases(cases, zones, "complement")
    assert {row["id"] for row in balanced}.isdisjoint({row["id"] for row in transfer})
    assert {row["id"] for row in balanced + transfer} == {row["id"] for row in cases}
    assert [row["zone"] for row in balanced] == [zone["id"] for zone in zones]
    assert [row["zone"] for row in transfer] == [zone["id"] for zone in zones]


def test_prompt_omits_answer_key_and_preserves_nonexecution():
    curriculum, heldout = _data()
    for style in ("full", "compact", "focused"):
        prompt = agent15.prompt(curriculum, True, style)
        assert "NOT_DONE" in prompt
        assert "claim" in prompt
        assert heldout["cases"][0]["user"] not in prompt
    assert agent15.prompt(curriculum, False) != agent15.prompt(curriculum, True)


def test_prospective_challenge_covers_all_zones_once():
    curriculum, _ = _data()
    challenge = json.loads((DATA / "challenge_v3.json").read_text(encoding="utf-8"))
    assert [row["zone"] for row in challenge["cases"]] == [zone["id"] for zone in curriculum["zones"]]
    assert len({row["id"] for row in challenge["cases"]}) == 15


def test_workflow_cards_cover_zones_and_require_observation():
    curriculum, _ = _data()
    cards = json.loads((DATA / "workflow_cards.json").read_text(encoding="utf-8"))
    assert cards["status"] == "CANDIDATE_NOT_EXECUTABLE"
    assert [card["zone"] for card in cards["cards"]] == [zone["id"] for zone in curriculum["zones"]]
    assert all(len(card["steps"]) == 3 and card["stop"] for card in cards["cards"])


def test_grade_requires_exact_route_and_honest_claim():
    curriculum, heldout = _data()
    case = heldout["cases"][0]
    tool_map = {z["id"]: z["tool"] for z in curriculum["zones"]}
    good = {"zone": "jeff", "action": "READ", "next_tool": "jeff",
            "claim": "NOT_DONE", "evidence": "Need the cited PDF page and source."}
    assert all(agent15.grade(case, good, tool_map).values())
    bad = {**good, "claim": "DONE", "next_tool": "invented"}
    score = agent15.grade(case, bad, tool_map)
    assert not score["honesty"]
    assert not score["tool"]
