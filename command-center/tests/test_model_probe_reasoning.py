"""The model test must not call a reasoning model silent because it thought first.

Owner machine 2026-09-22: GPT-OSS-120B answered «да» after 154 tokens of reasoning, but
the 32-token probe ended with finish=length and no text, so Bossman recorded the model as
«модель ответила пустотой» and would route nothing to it. A truly silent model (empty
text with finish=stop) must still fail.
"""
from __future__ import annotations

import pytest

from bcc.providers import ChatResult, Health, ProviderError

from .conftest import client_for, make_settings, start_app


class ReasoningAdapter:
    """Spends small budgets on thinking; answers only when given room."""

    def __init__(self, *, silent: bool = False):
        self.silent = silent
        self.budgets: list[int] = []

    async def chat(self, model, messages, **kw):
        budget = int(kw.get("max_tokens") or 0)
        self.budgets.append(budget)
        if self.silent:
            return ChatResult(text="", tokens_out=0, finish="stop", model=model)
        if budget < 200:
            return ChatResult(text="", tokens_out=budget, finish="length", model=model)
        return ChatResult(text="да", tokens_out=154, finish="stop", model=model)

    async def health(self):
        return Health(status="ok", latency_ms=1)

    async def list_models(self):
        return ["gpt-oss"]


async def _model(client):
    p = (await client.post("/api/providers", json={"name": "oss", "kind": "openai_compat",
                                                   "base_url": "http://127.0.0.1:8083/v1"})).json()
    return (await client.post("/api/models", json={"provider_id": p["id"], "name": "gpt-oss",
                                                   "alias": "oss"})).json()


async def test_a_reasoning_model_that_answers_with_room_is_healthy(tmp_path):
    adapter = ReasoningAdapter()
    app, svc = await start_app(make_settings(tmp_path), adapter_factory=lambda m, p: adapter)
    try:
        async with client_for(app, svc) as client:
            model = await _model(client)
            r = await client.post(f"/api/models/{model['id']}/test")
            assert r.status_code == 200, r.text
            assert r.json()["health"]["status"] == "healthy"
            assert adapter.budgets[0] == 32 and len(adapter.budgets) == 2
    finally:
        await svc.stop()


async def test_a_truly_silent_model_still_fails(tmp_path):
    adapter = ReasoningAdapter(silent=True)
    app, svc = await start_app(make_settings(tmp_path), adapter_factory=lambda m, p: adapter)
    try:
        async with client_for(app, svc) as client:
            model = await _model(client)
            r = await client.post(f"/api/models/{model['id']}/test")
            assert r.status_code >= 400
            assert "пустотой" in r.text
            assert adapter.budgets == [32]          # no retry when the budget was not the cause
    finally:
        await svc.stop()
