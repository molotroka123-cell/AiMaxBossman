"""bcc.features.nl_orchestra: the deterministic text -> orchestra draft helpers (nothing is created before confirm)."""
from __future__ import annotations

import pytest

from bcc.features import nl_orchestra as nl


@pytest.mark.parametrize("text,kw,expected", [
    ("максимум 5 шагов", ["шагов"], 5),
    ("шагов не больше 12", ["шагов"], 12),
    ("no numbers here", ["шагов"], None),
    ("3 retries then stop", ["retries", "попыт"], 3),
])
def test_number_next_to_a_keyword(text, kw, expected):
    assert nl._num_before(text, kw) == expected


@pytest.mark.parametrize("text,expected", [
    ("бюджет $2.5 на всё", 2.5), ("budget 10", 10.0), ("бюджет: 3,75", 3.75), ("без денег", None),
])
def test_budget_is_read_with_dollar_sign_or_word(text, expected):
    assert nl._budget(text) == expected


def test_role_comes_from_the_words_around_the_name_manager_wins():
    assert nl._role_for("главный — Alpha") == "manager"
    assert nl._role_for("fallback: Beta") == "reviewer"
    assert nl._role_for("Gamma просто работает") == "worker"
    assert nl._role_for("главный и fallback") == "manager"        # manager keywords are checked first


def test_each_fragment_decides_the_role_of_its_own_name_only():
    roles = nl._parse("главный — Alpha, fallback — Beta; Gamma, Delta", ["Alpha", "Beta", "Gamma", "Delta"])
    assert roles == {"manager": "Alpha", "reviewer": "Beta", "workers": ["Gamma", "Delta"]}


def test_a_name_missing_from_the_registry_is_never_invented():
    roles = nl._parse("Создай команду: главный Ghost, Максимум 3", ["Alpha"])
    assert roles == {"manager": None, "reviewer": None, "workers": []}


def test_repeated_worker_is_listed_once():
    roles = nl._parse("Gamma, Gamma, ещё Gamma", ["Gamma"])
    assert roles["workers"] == ["Gamma"]


def test_hyphenated_unknown_names_next_to_a_role_word_are_blockers_but_plain_words_are_not():
    assert nl._unknown_names("главный ghost-model", ["alpha"]) == ["ghost-model"]
    assert nl._unknown_names("главный Alpha-1", ["alpha-1"]) == []             # known (case-insensitive)
    assert nl._unknown_names("используй state-of-the-art подход", ["alpha"]) == []   # no role word near it


def test_fuzzy_match_by_substring_both_ways_and_none_when_absent():
    known = {"gpt-mini": {"id": 1}, "claude": {"id": 2}}
    assert nl._match("GPT-Mini", known) == {"id": 1}
    assert nl._match("claude-haiku", known) == {"id": 2}            # the registry key is a substring of the typed name
    assert nl._match("mini", known) == {"id": 1}
    assert nl._match("nothing", known) is None
    assert nl._match("   ", known) is None
