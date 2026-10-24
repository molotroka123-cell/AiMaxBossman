"""CallSettings.llm_model: a call-only local model override (voice latency 24.10: 8.7-11.2 s -> 4.8 s median).

The override is accepted only from Jeff's LOOPBACK local route and only when that runner lists the model; empty keeps
Jeff's own choice. Jeff's chats are untouched (the pin lives on the call's runtime only).
"""
from __future__ import annotations

import asyncio

import pytest

from bcc.telegram_calls.settings import CallSettings
from bcc.telegram_calls.speech import jeff_engines as je


def test_default_is_empty_and_bad_values_are_refused():
    assert CallSettings().llm_model == ""
    assert CallSettings(llm_model="bossman-uncensored-chat:latest").llm_model == "bossman-uncensored-chat:latest"
    for bad in ("x" * 201, "a\nb", 5):
        with pytest.raises(ValueError):
            CallSettings(llm_model=bad)


def test_a_non_loopback_route_is_never_accepted():
    for url in ("https://api.example.com/v1", "http://192.168.1.5:11434/v1", ""):
        assert asyncio.run(je._installed_on_local_route(url, "any-model")) is False


def test_a_loopback_route_accepts_only_a_listed_model(monkeypatch):
    class Resp:
        def json(self):
            return {"models": [{"name": "bossman-uncensored-chat:latest", "model": "bossman-uncensored-chat:latest"}]}

    class Client:
        def __init__(self, *a, **k):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, *a):
            return False

        async def get(self, url):
            assert url == "http://127.0.0.1:11434/api/tags"
            return Resp()

    import httpx
    monkeypatch.setattr(httpx, "AsyncClient", Client)
    assert asyncio.run(je._installed_on_local_route("http://127.0.0.1:11434/v1", "bossman-uncensored-chat:latest"))
    assert not asyncio.run(je._installed_on_local_route("http://127.0.0.1:11434/v1", "not-installed:latest"))
