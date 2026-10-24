"""tools/three_model_audit.py: vote parsing and the decision rule (no network)."""
import importlib.util
from pathlib import Path

import pytest

_spec = importlib.util.spec_from_file_location("tma", Path(__file__).resolve().parents[1] / "tools" / "three_model_audit.py")
tma = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(tma)
KEYS = ["A", "B"]


def test_parse_takes_last_valid_choice_after_reasoning():
    text = 'думаю... {"choice": "B", "why": "черновик"} итог: {"choice": "A", "why": "безопаснее"}'
    assert tma.parse_choice(text, KEYS) == ("A", "безопаснее")


def test_parse_rejects_unknown_option_and_garbage():
    assert tma.parse_choice('{"choice": "C", "why": "x"}', KEYS) == (None, "")
    assert tma.parse_choice("без json", KEYS) == (None, "")
    assert tma.parse_choice("", KEYS) == (None, "")


def test_majority_wins_and_tie_goes_to_first_model():
    assert tma.decide([{"choice": "B"}, {"choice": "A"}, {"choice": "A"}], KEYS) == "A"
    assert tma.decide([{"choice": "B"}, {"choice": "A"}], KEYS) == "B"


def test_no_valid_votes_is_no_decision():
    assert tma.decide([{"choice": None}, {"choice": "Z"}, {"error": "x"}], KEYS) is None


def test_secret_in_question_is_refused_before_any_call(monkeypatch):
    monkeypatch.setattr(tma, "_chat", lambda *a, **k: pytest.fail("network call made"))
    with pytest.raises(SystemExit):
        tma.audit("цель", "ключ sk-" + "a" * 24, {"A": "x", "B": "y"})
