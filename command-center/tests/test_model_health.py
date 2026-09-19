"""B5 — a model that answers nothing is not healthy.

Cloud QA pinned `cohere/north-mini-code:free`, probed it, got empty responses,
and it still counted as usable. Two paths produced that: a provider-endpoint
check standing in for model capability, and a chat call that was called a
success whenever it did not raise — which an HTTP 200 carrying "" does not.

The master doc asks for negative controls on the silent, partial, slow,
malformed, quota and recovered cases. Each has one below.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

from bcc import model_health as mh
from bcc.providers import ChatResult, Health, ProviderError

from .conftest import FakeAdapter
from .helpers import make_stack


NOW = datetime(2026, 9, 8, 12, 0, tzinfo=timezone.utc)


# ------------------------------------------------------- classify the ANSWER

def test_a_real_answer_is_healthy():
    assert mh.classify_answer("OK")[0] == mh.HEALTHY


@pytest.mark.parametrize("text", ["", "   ", "\n", "\t\n  "])
def test_an_empty_answer_is_silent_not_healthy(text):
    """The exact `cohere/north-mini-code:free` case: HTTP 200, empty body."""
    status, detail = mh.classify_answer(text, tokens_out=0)
    assert status == mh.SILENT
    assert "пустот" in detail and "tokens_out=0" in detail


@pytest.mark.parametrize("value", [None, 42, {"content": "x"}, [], b"bytes"])
def test_a_non_string_answer_is_malformed(value):
    assert mh.classify_answer(value)[0] == mh.MALFORMED


def test_a_partial_answer_is_still_an_answer():
    """Negative control: "short" is not "silent". A one-word reply is a working
    model, and treating brevity as failure would blacklist the cheap models the
    router is supposed to prefer."""
    assert mh.classify_answer("4")[0] == mh.HEALTHY
    assert mh.classify_answer(" ok ")[0] == mh.HEALTHY


# ------------------------------------------------------- classify the FAILURE

@pytest.mark.parametrize("code,expected", [
    (200, mh.HEALTHY), (204, mh.HEALTHY),
    (401, mh.UNAUTHORIZED), (403, mh.UNAUTHORIZED),
    (402, mh.THROTTLED), (429, mh.THROTTLED),
    (500, mh.PROVIDER_DOWN), (503, mh.PROVIDER_DOWN),
    (400, mh.MALFORMED), (404, mh.MALFORMED),
])
def test_status_codes_split_by_who_must_act(code, expected):
    """4xx and 5xx are not one bucket: a rejected key needs the owner, a 429
    needs time, a 500 needs the provider. Merging them sends every one of those
    to the same wrong remedy."""
    assert mh.classify_status_code(code)[0] == expected


def test_a_timeout_is_not_a_provider_error():
    assert mh.classify_exception(TimeoutError("read timed out"))[0] == mh.TIMEOUT


def test_provider_error_kinds_are_respected():
    assert mh.classify_exception(ProviderError("bad key", kind="auth"))[0] == mh.UNAUTHORIZED
    assert mh.classify_exception(ProviderError("slow", kind="rate_limit"))[0] == mh.THROTTLED
    assert mh.classify_exception(ProviderError("boom", kind="network"))[0] == mh.PROVIDER_DOWN


# ------------------------------------------------------------ cooldown/backoff

def test_transient_failures_back_off_exponentially_but_are_bounded():
    seconds = [mh.cooldown_for(mh.PROVIDER_DOWN, n).total_seconds() for n in range(1, 10)]
    assert seconds[0] == mh.BASE_COOLDOWN_SECONDS
    assert seconds == sorted(seconds)                       # monotonic
    assert max(seconds) <= mh.MAX_COOLDOWN_SECONDS          # an unbounded wait is a removal


def test_a_silent_model_waits_much_longer_than_a_flaky_one():
    """A model that answers nothing will keep answering nothing; re-asking it
    every minute is how the free tier ate the probe budget."""
    assert (mh.cooldown_for(mh.SILENT, 1).total_seconds()
            > mh.cooldown_for(mh.PROVIDER_DOWN, 1).total_seconds())


def test_a_rejected_key_is_never_hammered():
    assert mh.cooldown_for(mh.UNAUTHORIZED, 1).total_seconds() == mh.MAX_COOLDOWN_SECONDS


def test_a_healthy_observation_has_no_cooldown():
    assert mh.cooldown_for(mh.HEALTHY, 0) == timedelta(0)


# --------------------------------------------------------------- the record

def test_an_unmeasured_model_is_not_usable_and_not_confident():
    rec = mh.HealthRecord()
    assert rec.status == mh.UNMEASURED
    assert rec.usable() is False
    assert rec.confidence == mh.CONFIDENCE_UNKNOWN


def test_a_measured_success_is_usable_and_records_when():
    rec = mh.record_observation(None, mh.HEALTHY, "", latency_ms=42, now=NOW)
    assert rec.usable(NOW) is True
    assert rec.last_success_at == NOW and rec.latency_ms == 42
    assert rec.consecutive_failures == 0


def test_a_silent_observation_is_not_usable_however_many_times_it_repeats(env):
    rec = None
    for _ in range(5):
        rec = mh.record_observation(rec, mh.SILENT, "пусто", now=NOW)
        assert rec.usable(NOW) is False
    assert rec.consecutive_failures == 5 and rec.successes == 0


def test_recovery_clears_the_failure_streak_and_the_cooldown():
    """The 'recovered' negative control: a model that comes back must become
    usable again, or a transient blip is a permanent ban."""
    rec = mh.record_observation(None, mh.PROVIDER_DOWN, "500", now=NOW)
    assert rec.usable(NOW) is False and rec.in_cooldown(NOW)
    back = mh.record_observation(rec, mh.HEALTHY, "", now=NOW + timedelta(minutes=30))
    assert back.usable(NOW + timedelta(minutes=30)) is True
    assert back.consecutive_failures == 0 and back.cooldown_until is None
    assert back.successes == 1


def test_last_success_survives_a_later_failure():
    ok = mh.record_observation(None, mh.HEALTHY, "", now=NOW)
    bad = mh.record_observation(ok, mh.TIMEOUT, "slow", now=NOW + timedelta(minutes=1))
    assert bad.last_success_at == NOW               # history is not erased by a bad probe


def test_a_healthy_model_inside_its_cooldown_is_not_routable():
    rec = mh.HealthRecord(status=mh.HEALTHY, checked_at=NOW, samples=1,
                          cooldown_until=NOW + timedelta(minutes=5))
    assert rec.usable(NOW) is False
    assert rec.usable(NOW + timedelta(minutes=6)) is True


def test_confidence_is_a_band_not_a_fabricated_decimal():
    rec = mh.HealthRecord(status=mh.HEALTHY, checked_at=mh._now(), samples=1)
    assert rec.confidence == mh.CONFIDENCE_LOW
    rec.samples = 3
    assert rec.confidence == mh.CONFIDENCE_MEDIUM
    rec.samples = 9
    assert rec.confidence == mh.CONFIDENCE_HIGH


def test_an_old_measurement_loses_confidence_without_becoming_a_lie():
    old = mh._now() - timedelta(seconds=mh.STALE_AFTER_SECONDS + 60)
    rec = mh.HealthRecord(status=mh.HEALTHY, checked_at=old, samples=20)
    assert rec.stale is True and rec.confidence == mh.CONFIDENCE_LOW


def test_a_corrupt_record_reads_as_unmeasured_never_as_healthy():
    """Fail-closed on deserialization: guessing `healthy` from an unreadable row
    is exactly the mistake B5 is about."""
    for bad in [None, "healthy", 7, [], {"status": "totally-fine"},
                {"status": mh.HEALTHY, "checked_at": "not-a-date"}]:
        rec = mh.HealthRecord.from_dict(bad)
        assert rec.status in (mh.UNMEASURED, mh.HEALTHY)
        if rec.status == mh.HEALTHY:
            assert rec.checked_at is None            # unusable without a timestamp


def test_a_record_round_trips_through_json():
    rec = mh.record_observation(None, mh.HEALTHY, "ok", latency_ms=11, now=NOW)
    again = mh.HealthRecord.from_dict(rec.to_dict())
    assert again.status == rec.status and again.checked_at == rec.checked_at
    assert again.last_success_at == rec.last_success_at


# ------------------------------------------------------------------ ranking

def test_unmeasured_ranks_below_healthy_and_above_broken():
    """The gateway sorted unchecked targets as "optimistically usable", which is
    how a silent model beats a proven one. But ranking unknown WITH broken would
    mean a never-probed model is never tried and health is never measured."""
    healthy = mh.record_observation(None, mh.HEALTHY, "", now=NOW)
    unknown = mh.HealthRecord()
    broken = mh.record_observation(None, mh.SILENT, "", now=NOW)
    order = mh.rank([("broken", broken), ("unknown", unknown), ("healthy", healthy)], NOW)
    assert order == ["healthy", "unknown", "broken"]


def test_fallback_skips_the_model_that_just_failed():
    healthy = mh.record_observation(None, mh.HEALTHY, "", now=NOW)
    other = mh.record_observation(None, mh.HEALTHY, "", now=NOW)
    pick = mh.select_fallback([("a", healthy), ("b", other)], exclude={"a"}, now=NOW)
    assert pick == "b"


def test_fallback_returns_none_rather_than_a_known_bad_model():
    """None is a real answer. Handing back a model already measured silent, just
    to return something, is how a failing run burns its budget."""
    silent = mh.record_observation(None, mh.SILENT, "", now=NOW)
    down = mh.record_observation(None, mh.PROVIDER_DOWN, "", now=NOW)
    assert mh.select_fallback([("a", silent), ("b", down)], now=NOW) is None


def test_fallback_will_try_an_unmeasured_model_when_nothing_is_proven():
    assert mh.select_fallback([("a", mh.HealthRecord())], now=NOW) == "a"


# ------------------------------------------------------------ registry wiring

async def test_an_empty_answer_never_marks_a_model_online(env):
    """End to end, the headline B5 case: a provider that returns HTTP 200 with
    an empty string must not leave the model looking usable."""
    stack = await make_stack(env.client)
    mid = stack["model"]["id"]
    env.svc.registry.adapter_factory = lambda m, p: FakeAdapter("", tokens=(5, 0))

    with pytest.raises(ProviderError):
        await env.svc.registry.test_model(mid)

    record = await env.svc.registry.model_health(mid)
    assert record.status == mh.SILENT and record.usable() is False
    listed = {m["id"]: m for m in (await env.client.get("/api/models")).json()}
    assert listed[mid]["status"] != "online"


async def test_a_real_answer_marks_the_model_healthy(env):
    stack = await make_stack(env.client)
    mid = stack["model"]["id"]
    env.svc.registry.adapter_factory = lambda m, p: FakeAdapter("готово", tokens=(5, 3))
    await env.svc.registry.test_model(mid)
    record = await env.svc.registry.model_health(mid)
    assert record.status == mh.HEALTHY and record.usable() is True
    assert record.last_success_at is not None


async def test_a_live_endpoint_alone_never_makes_a_model_healthy(env):
    """`check_model` asks the PROVIDER whether it is up. That is not evidence
    that this model answers, and it must not be recorded as if it were."""
    stack = await make_stack(env.client)
    mid = stack["model"]["id"]

    class OkEndpoint(FakeAdapter):
        async def health(self):
            return Health(status="ok", latency_ms=1)

    env.svc.registry.adapter_factory = lambda m, p: OkEndpoint()
    await env.svc.registry.check_model(mid)
    record = await env.svc.registry.model_health(mid)
    assert record.status == mh.UNMEASURED       # reachable != usable
    assert record.usable() is False


async def test_a_dead_endpoint_does_count_against_the_model(env):
    """The implication only runs one way: a provider that is down means this
    model will not answer right now, and that IS worth recording."""
    stack = await make_stack(env.client)
    mid = stack["model"]["id"]

    class DeadEndpoint(FakeAdapter):
        async def health(self):
            return Health(status="offline", detail="connection refused")

    env.svc.registry.adapter_factory = lambda m, p: DeadEndpoint()
    await env.svc.registry.check_model(mid)
    record = await env.svc.registry.model_health(mid)
    assert record.status == mh.PROVIDER_DOWN and record.usable() is False


async def test_usable_models_orders_by_measured_health(env):
    stack = await make_stack(env.client)
    good = stack["model"]["id"]
    silent = (await env.client.post("/api/models", json={
        "provider_id": stack["provider"]["id"], "name": "silent-7b",
        "alias": "silent-7b"})).json()["id"]
    unknown = (await env.client.post("/api/models", json={
        "provider_id": stack["provider"]["id"], "name": "never-probed",
        "alias": "never-probed"})).json()["id"]

    await env.svc.registry.record_model_health(good, mh.HEALTHY, "")
    await env.svc.registry.record_model_health(silent, mh.SILENT, "пусто")

    ordered = [m["id"] for m in await env.svc.registry.usable_models()]
    assert ordered.index(good) < ordered.index(unknown) < ordered.index(silent)


async def test_a_quota_failure_is_transient_and_recovers(env):
    """The quota + recovered controls together: 429 must not be permanent."""
    stack = await make_stack(env.client)
    mid = stack["model"]["id"]
    await env.svc.registry.record_model_health(mid, mh.THROTTLED, "HTTP 429")
    assert (await env.svc.registry.model_health(mid)).usable() is False
    await env.svc.registry.record_model_health(mid, mh.HEALTHY, "")
    record = await env.svc.registry.model_health(mid)
    assert record.usable() is True and record.consecutive_failures == 0
