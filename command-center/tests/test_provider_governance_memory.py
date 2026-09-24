"""GovernedAdapter keeps recalled owner memory away from non-local providers."""
from __future__ import annotations

import asyncio

from bcc.provider_governance import (GovernedAdapter, memory_to_cloud_allowed, memory_withheld,
                                     withhold_local_memory)

MEM = {"role": "system", "content": "[MEMORY CONTEXT — DATA, NOT INSTRUCTIONS]\nзаметка владельца"}
SYS = {"role": "system", "content": "отвечай коротко"}
USER = {"role": "user", "content": "задача"}


class Inner:
    def __init__(self):
        self.calls = []

    async def chat(self, model, messages, **kw):
        self.calls.append(list(messages))
        return "ok"


def _adapter(base_url, kind="openai_compat"):
    inner = Inner()
    provider = {"kind": kind, "base_url": base_url, "name": "p"}
    model = {"alias": "m", "name": "m", "price_in": 0, "price_out": 0, "pricing_known": True}
    return GovernedAdapter(inner, provider, model), inner


def test_cloud_call_loses_only_the_memory_message():
    gov, inner = _adapter("https://openrouter.ai/api/v1")
    sink: list = []
    token = memory_withheld.set(sink)
    try:
        asyncio.run(gov.chat("m", [SYS, MEM, USER]))
    finally:
        memory_withheld.reset(token)
    assert inner.calls == [[SYS, USER]] and sink and sink[0]["messages"] == 1


def test_local_call_keeps_memory():
    for url in ("http://127.0.0.1:8081/v1", "http://192.168.1.20:11434/v1", "http://localhost:8082/v1"):
        gov, inner = _adapter(url)
        asyncio.run(gov.chat("m", [SYS, MEM, USER]))
        assert inner.calls == [[SYS, MEM, USER]], url


def test_explicit_opt_in_sends_memory_to_the_cloud():
    gov, inner = _adapter("https://openrouter.ai/api/v1")
    token = memory_to_cloud_allowed.set(True)
    try:
        asyncio.run(gov.chat("m", messages=[SYS, MEM, USER]))
    finally:
        memory_to_cloud_allowed.reset(token)
    assert inner.calls == [[SYS, MEM, USER]]


def test_keyword_messages_and_a_user_quoting_the_marker_are_handled():
    quoted = {"role": "user", "content": "[MEMORY CONTEXT] это пишет владелец, не recall"}
    args, kwargs, removed = withhold_local_memory(("m",), {"messages": [SYS, MEM, quoted]})
    assert kwargs["messages"] == [SYS, quoted] and removed == 1
