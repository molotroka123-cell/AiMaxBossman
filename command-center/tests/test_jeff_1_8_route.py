"""Jeff 1.8: a cloud route exists only while the LIVE catalog says price 0/0.

Fakes only. A ':free' suffix is a name, never proof; a price flip after the catalog
was built must end in an honest refusal, never in a paid route, Claude or OpenAI.
"""
from __future__ import annotations

import asyncio
import dataclasses

from bcc.pit import model_route
from bcc.pit import runtime as rt
from bcc.pit.model_route import JEFF_MODEL_ROUTE_SCHEMA, route_verdict
from bcc.providers import ChatResult, ProviderError

from .test_pit_runtime import FakeAdapter, make_runtime, make_settings, message, warm

FREE = {"prompt": 0.0, "completion": 0.0}


def _runtime(tmp_path, adapter, models):
    settings = dataclasses.replace(make_settings(tmp_path), chat_models=tuple(models))
    return make_runtime(tmp_path, adapter=adapter, settings=settings)


def _ask(runtime, text="Привет", message_id=1):
    person = runtime.settings.people[0]
    warm(runtime, runtime.vault.key_for_telegram(person.user_id))
    return asyncio.run(runtime.handle(person, message(text, message_id=message_id)))


def test_verdict_names_the_reason_and_only_zero_price_free_id_passes():
    assert route_verdict("a/m:free", True, FREE) == ""
    assert route_verdict("a/m:free", False, FREE) == "not_listed"
    assert route_verdict("a/m:free", True, None) == "price_unknown"
    assert route_verdict("a/m:free", True, {"prompt": 0.0, "completion": None}) == "price_unknown"
    assert route_verdict("a/m:free", True, {"prompt": 0.0, "completion": 1e-7}) == "price_positive"
    assert route_verdict("a/m", True, FREE) == "not_free_id"


def test_catalog_records_why_each_route_was_rejected(tmp_path):
    pricing = {"v/real:free": FREE, "v/renamed:free": {"prompt": 1e-6, "completion": 0.0},
               "v/zero": FREE}
    runtime = _runtime(tmp_path, FakeAdapter(pricing=pricing), pricing)
    try:
        catalog = asyncio.run(runtime.refresh_catalog())
        assert set(catalog) == {"v/real:free"}
        status = runtime.model_route_status()
        assert status["schema"] == JEFF_MODEL_ROUTE_SCHEMA == model_route.JEFF_MODEL_ROUTE_SCHEMA
        assert status["rejected"] == {"v/renamed:free": "price_positive", "v/zero": "not_free_id"}
        assert status["free_remote"] == ["v/real:free"] and status["refusal"] == ""
    finally:
        asyncio.run(runtime.close())


def test_price_flip_after_verification_refuses_honestly_and_sends_nothing(tmp_path):
    adapter = FakeAdapter(pricing={"free/model:free": FREE})
    runtime = _runtime(tmp_path, adapter, ("free/model:free",))
    try:
        assert "готово" in _ask(runtime, message_id=1)
        assert len(adapter.calls) == 1
        adapter.pricing["free/model:free"] = {"prompt": 2e-6, "completion": 4e-6}   # the flip
        runtime.prices_verified_at -= model_route.PRICE_RECHECK_SECONDS + 1
        answer = _ask(runtime, message_id=2)
        assert answer == rt.NO_MODEL_RU
        assert len(adapter.calls) == 1, "a flipped-price model must not be called"
        status = runtime.model_route_status()
        assert status["free_remote"] == [] and status["refusal"] == "no_free_route"
        assert status["rejected"] == {"free/model:free": "price_positive"}
    finally:
        asyncio.run(runtime.close())


def test_price_flip_back_to_free_restores_the_route(tmp_path):
    adapter = FakeAdapter(pricing={"free/model:free": {"prompt": 1e-6, "completion": 1e-6}})
    runtime = _runtime(tmp_path, adapter, ("free/model:free",))
    try:
        assert _ask(runtime, message_id=1) == rt.NO_MODEL_RU
        adapter.pricing["free/model:free"] = FREE
        runtime.prices_verified_at -= model_route.PRICE_RECHECK_SECONDS + 1
        runtime.catalog_checked_at = 0.0
        assert "готово" in _ask(runtime, message_id=2)
    finally:
        asyncio.run(runtime.close())


def test_unreachable_live_catalog_fails_closed_for_remote_routes(tmp_path):
    adapter = FakeAdapter(pricing={"free/model:free": FREE})
    runtime = _runtime(tmp_path, adapter, ("free/model:free",))
    try:
        assert "готово" in _ask(runtime, message_id=1)

        async def down():
            raise ProviderError("нет связи", kind="network")
        adapter.list_model_info = down
        runtime.prices_verified_at -= model_route.PRICE_RECHECK_SECONDS + 1
        assert _ask(runtime, message_id=2) == rt.NO_MODEL_RU
        assert len(adapter.calls) == 1
        assert runtime.model_route_status()["refusal"] == "catalog_unreachable"
    finally:
        asyncio.run(runtime.close())


def test_fresh_verification_is_not_rechecked_every_turn(tmp_path):
    adapter = FakeAdapter(pricing={"free/model:free": FREE})
    lookups = []
    runtime = _runtime(tmp_path, adapter, ("free/model:free",))
    original = adapter.list_model_pricing

    async def counted():
        lookups.append(1)
        return await original()
    adapter.list_model_pricing = counted
    try:
        _ask(runtime, message_id=1)
        _ask(runtime, message_id=2)
        _ask(runtime, message_id=3)
        assert len(lookups) == 1
    finally:
        asyncio.run(runtime.close())


def test_provider_payment_required_blocks_that_model_and_never_moves_to_paid(tmp_path):
    calls = []

    class Billing(FakeAdapter):
        async def chat(self, model, messages, **kw):
            calls.append(model)
            if model == "free/a:free":
                raise ProviderError("провайдер отказал (402): payment required", kind="http")
            return ChatResult(text="paid answer", model=model)

    pricing = {"free/a:free": FREE, "paid/x": {"prompt": 1e-6, "completion": 1e-6}}
    runtime = _runtime(tmp_path, Billing(pricing=pricing), pricing)
    try:
        answer = _ask(runtime, message_id=1)
        assert "paid answer" not in answer
        assert calls == ["free/a:free"]
        assert runtime.model_route_status()["rejected"].get("free/a:free") == "payment_required"
        calls.clear()
        assert _ask(runtime, message_id=2) == rt.NO_MODEL_RU
        assert calls == []
    finally:
        asyncio.run(runtime.close())


def test_refusal_never_names_a_paid_vendor_route(tmp_path):
    adapter = FakeAdapter(pricing={"free/model:free": {"prompt": 1e-6, "completion": 1e-6}})
    runtime = _runtime(tmp_path, adapter, ("free/model:free",))
    try:
        answer = _ask(runtime)
        assert answer == rt.NO_MODEL_RU
        low = answer.lower()
        assert "claude" not in low and "openai" not in low and "gpt" not in low
        assert adapter.calls == []
    finally:
        asyncio.run(runtime.close())
