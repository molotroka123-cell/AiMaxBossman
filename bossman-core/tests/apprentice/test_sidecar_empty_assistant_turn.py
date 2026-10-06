"""A model turn with neither text nor a tool call must not poison the conversation for strict providers.

Owner PC 06.10.2026, tree task 5a07caa7c1a3 (cohere/north-mini-code:free): after one empty reply the sidecar stored
{"role": "assistant", "content": ""} and the next request failed with HTTP 400 «invalid message provided at index 3:
must have non-empty content or tool calls»; the worker stopped with model_error after 2 steps.
"""
from __future__ import annotations

import inspect

from bossman.apprentice import local_sidecar as ls


def test_an_empty_reply_without_tool_calls_is_stored_with_text():
    msg = ls._assistant_message({"content": "", "tool_calls": None})
    assert msg["role"] == "assistant" and msg["content"].strip() and "tool_calls" not in msg


def test_a_tool_call_reply_keeps_its_calls_and_text_as_is():
    calls = [{"id": "c1", "type": "function", "function": {"name": "read_file", "arguments": "{}"}}]
    msg = ls._assistant_message({"content": "", "tool_calls": calls})
    assert msg["tool_calls"] == calls and msg["content"] == ""


def test_a_text_reply_is_kept_verbatim():
    assert ls._assistant_message({"content": "thinking aloud"})["content"] == "thinking aloud"


def test_the_agent_loop_uses_it():
    assert "_assistant_message(msg)" in inspect.getsource(ls)
