"""Audit-round fixes on the HTTP surface: dial idempotency (request_id) and record_audio not yet implemented."""
from __future__ import annotations

from bcc.telegram_calls.types import CallError

from .test_api_calls import BASE, FakeManager
from .test_manager import Harness, active, code_of
from .fakes_a import until


class IdManager(FakeManager):
    async def dial(self, confirm_unknown=False, request_id=None):
        return self._do("dial", confirm_unknown, request_id, result={"call_id": "c1", "accepted": True})


async def test_request_id_is_forwarded_and_optional_on_the_http_surface(env):
    fake = env.svc.calls_manager = IdManager()
    r = await env.client.post(BASE + "/dial", json={"confirm_unknown": False, "request_id": "abc-123"})
    assert r.status_code == 200
    assert fake.calls == [("dial", False, "abc-123")]
    bad = await env.client.post(BASE + "/dial", json={"request_id": "x" * 500})
    assert bad.status_code == 422


async def test_duplicate_dial_request_maps_to_409_with_a_stable_code(env):
    env.svc.calls_manager = IdManager(error="DUPLICATE_DIAL_REQUEST")
    r = await env.client.post(BASE + "/dial", json={"request_id": "abc"})
    assert r.status_code == 409 and r.json()["error"]["code"] == "DUPLICATE_DIAL_REQUEST"


async def test_manager_rejects_a_repeated_request_id_after_the_call_ended(tmp_path):
    h = Harness(tmp_path)
    h.arm()
    res = await h.m.dial(request_id="rid-1")
    assert res["accepted"]
    assert await active(h)
    await h.m.hangup()
    assert await until(lambda: h.m.history())
    assert await code_of(h.m.dial(request_id="rid-1")) == "DUPLICATE_DIAL_REQUEST"
    assert len(h.spawns) == 1 and len(h.transports) == 1 and h.transports[0].dial_calls == 1   # no second ring


async def test_a_fresh_request_id_and_a_missing_one_are_still_allowed(tmp_path):
    h = Harness(tmp_path)
    h.arm()
    await h.m.dial(request_id="rid-1")
    assert await active(h)
    await h.m.hangup()
    assert await until(lambda: h.m.history())
    res = await h.m.dial(request_id="rid-2")
    assert res["accepted"]
    assert h.m.status()["call"]["active"] is True
    await h.m.stop("owner")


async def test_record_audio_true_is_rejected_with_a_stable_code_and_false_is_accepted(env):
    r = await env.client.put(BASE + "/settings", json={"record_audio": True})
    assert r.status_code == 501 and r.json()["error"]["code"] == "FEATURE_NOT_AVAILABLE"
    assert (await env.client.get(BASE + "/settings")).json()["record_audio"] is False
    ok = await env.client.put(BASE + "/settings", json={"record_audio": False, "keep_transcript": True})
    assert ok.status_code == 200 and ok.json()["record_audio"] is False and ok.json()["keep_transcript"] is True
