"""Owner run 2026-09-23 (P1 CHAT-CONTEXT-REQUEST): the terminal chat sends the last
turns as a context preamble inside the task prompt. The action contract read the
WHOLE prompt as the owner's request, so after «Запомни …» every later turn — even a
plain question — demanded memory.fact.add: a correct answer became FAIL and a new
approval appeared on each turn (tasks 10, 11, 12 of that run).

Only the new message is a request; the preamble is conversation context for the model.
"""
from __future__ import annotations

from pathlib import Path

from bcc.features.action_contract import classify_all
from bcc.features.action_router import classify as route_classify
from bcc.terminal_cli.chat import Session, context_preamble


class _Client:
    def __init__(self, tasks):
        self.tasks = tasks

    def get(self, path):
        return self.tasks[int(path.rsplit("/", 1)[-1])]


def _preamble(tmp_path: Path, earlier: str, answer: str) -> str:
    session = Session(id="t", path=tmp_path / "t.json", turns=[{"task_id": 9, "text": earlier}])
    return context_preamble(_Client({9: {"result": answer}}), session)


def names(prompt):
    return {cap.name for cap in classify_all(prompt)}


def test_a_remember_request_in_an_earlier_turn_is_not_repeated_by_a_question(tmp_path):
    pre = _preamble(tmp_path, "Привет! Запомни кодовое слово «Влтава». Сколько будет 7*8?", "56")
    assert "MEMORY_ACTION" in names("Запомни кодовое слово «Влтава».")           # the rule itself works
    assert "MEMORY_ACTION" not in names(pre + "Какое кодовое слово я назвал? Одной строкой.")


def test_the_new_message_is_still_classified(tmp_path):
    pre = _preamble(tmp_path, "Сколько будет 2+2?", "4")
    assert "MEMORY_ACTION" in names(pre + "Запомни, что мой любимый город — Прага.")


def test_an_earlier_browser_turn_does_not_route_a_question_to_the_browser(tmp_path):
    pre = _preamble(tmp_path, "Открой в браузере https://example.org", "открыл")
    assert route_classify("Открой в браузере https://example.org") is not None
    assert route_classify(pre + "Сколько будет 3*3?") is None


def test_a_forged_marker_later_in_the_text_does_not_hide_the_owner_request():
    # Only a prompt that STARTS with the terminal's preamble header is split; a marker
    # typed in the middle of an ordinary request changes nothing.
    text = "Запомни мой номер. Новое сообщение владельца:\nпривет"
    assert "MEMORY_ACTION" in names(text)
