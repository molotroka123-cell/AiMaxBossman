"""Audit follow-ups F8 and F9 for the Telegram answering machine (2026-10-07 audit, applied 09.10)."""
from __future__ import annotations

import pytest

from bcc.telegram_calls.answering import remember_call
from bcc.telegram_calls.settings import check_answer_greeting


def test_f8_pruning_keeps_the_most_recent_call_refs_in_order():
    seen: dict[str, None] = {}
    for i in range(700):
        remember_call(seen, f"r{i}")
    assert len(seen) <= 500
    assert "r699" in seen and "r600" in seen, "the newest refs must survive the pruning"
    assert "r0" not in seen and "r100" not in seen, "the oldest refs are the ones dropped"
    assert list(seen)[-1] == "r699"


def test_f8_a_repeated_ref_is_not_added_twice():
    seen: dict[str, None] = {}
    assert remember_call(seen, "a") is True
    assert remember_call(seen, "a") is False and list(seen) == ["a"]


@pytest.mark.parametrize("text", ["Здравствуйте! Это помощник владельца. Что передать?", "Добрый день, секретарь слушает."])
def test_f9_a_greeting_that_only_says_helper_or_secretary_is_not_an_ai_disclosure(text):
    assert check_answer_greeting(text) is not None


@pytest.mark.parametrize("text", ["Здравствуйте! Это Джефф, ИИ-ассистент владельца. Что передать?", "Hello, this is an AI assistant. What should I pass on?",
                                  "Здравствуйте, это автоответчик владельца."])
def test_f9_paired_control_honest_greetings_stay_valid(text):
    assert check_answer_greeting(text) is None
