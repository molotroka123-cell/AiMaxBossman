"""/key from the pult must reach the backend provider rows (owner 01.11.2026: «не вставляется ключ OpenRouter»)."""
from __future__ import annotations

import asyncio

from bcc.telegram_companion.adapters import Core


class _FakeCore(Core):
    def __init__(self, responses):
        self.calls = []
        self._responses = responses

    async def _request(self, method, path, payload=None, timeout=10):
        self.calls.append((method, path, payload))
        return self._responses.get((method, path), {})


def test_openrouter_key_goes_to_connect_endpoint():
    core = _FakeCore({("POST", "/api/openrouter/connect"): {"ok": True, "provider_id": 1}})
    assert asyncio.run(core.apply_provider_key("OPENROUTER_API_KEY", "sk-or-new")) == "applied"
    assert core.calls == [("POST", "/api/openrouter/connect", {"api_key": "sk-or-new"})]


def test_named_provider_key_patches_matching_row_only():
    core = _FakeCore({("GET", "/api/providers"): [{"id": 7, "name": "NVIDIA NIM"}, {"id": 8, "name": "Groq"}]})
    assert asyncio.run(core.apply_provider_key("GROQ_API_KEY", "gsk-x")) == "applied"
    assert core.calls[-1] == ("PATCH", "/api/providers/8/key", {"api_key": "gsk-x"})


def test_unknown_names_stay_file_only_and_missing_provider_is_reported():
    core = _FakeCore({("GET", "/api/providers"): []})
    assert asyncio.run(core.apply_provider_key("APIFY_API_TOKEN", "x")) == "file_only"
    assert asyncio.run(core.apply_provider_key("MISTRAL_API_KEY", "x")) == "no_provider"
