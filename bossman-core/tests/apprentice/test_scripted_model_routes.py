"""DETERMINISTIC TEST MODEL: several tasks on one server pick their turns by a marker."""
from __future__ import annotations

from bossman.apprentice.scripted_model import reply_for, turns_for

SCRIPT = {"name": "gate", "turns": [{"content": "default"}],
          "routes": [{"match": "Task id: money", "turns": [{"tool": "read_file", "args": {"path": "money.py"}}]},
                     {"match": "Task id: price", "turns": [{"tool": "finish", "args": {"summary": "x"}}]}]}


def _messages(user: str, assistants: int = 0) -> list[dict]:
    msgs = [{"role": "system", "content": "You are a coding agent"}, {"role": "user", "content": user}]
    msgs += [{"role": "assistant", "content": ""}] * assistants
    return msgs


def test_the_first_user_message_selects_the_route():
    assert turns_for(SCRIPT, _messages("Task id: price\nGoal: ...")) == SCRIPT["routes"][1]["turns"]
    reply = reply_for(SCRIPT, _messages("Task id: money\nGoal: ..."))
    assert reply["tool_calls"][0]["function"]["name"] == "read_file"


def test_no_route_falls_back_to_turns_and_exhaustion_is_plain_text():
    assert reply_for(SCRIPT, _messages("something else"))["content"] == "default"
    assert reply_for(SCRIPT, _messages("Task id: money", assistants=1))["content"] == "(script exhausted)"


def test_the_handshake_probe_ignores_routes():
    probe = [{"role": "system", "content": "Handshake probe. Call list_dir"}, {"role": "user", "content": "Task id: price"}]
    assert reply_for(SCRIPT, probe)["tool_calls"][0]["function"]["name"] == "list_dir"
