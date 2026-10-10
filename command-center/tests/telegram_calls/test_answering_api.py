"""/api/telegram/calls/answering/*: auth, owner-only settings, the REAL offline worker (loopback line, scripted engines, no Telegram),
the report outbox the Telegram companion polls, STOP and the owner stop-all.

Every call here is the loopback one: nothing proves a real incoming Telegram call (ACCEPTANCE rows 13/14 stay BLOCKED).
"""
from __future__ import annotations

import asyncio
import json
import time

import httpx
import pytest

from bcc.telegram_calls.answering_store import REPORT_ID

from .test_api_calls import PREFIX, connect, home, mgr, status, until

SHORT_GREETING = "Это ИИ-ассистент владельца. Приму сообщение."
ANS = f"{PREFIX}/answering"


@pytest.fixture(autouse=True)
def offline_mode(monkeypatch):
    monkeypatch.setenv("BOSSMAN_CALLS_MODE", "offline_test")
    monkeypatch.delenv("BOSSMAN_VAULT_KEY", raising=False)
    monkeypatch.delenv("BOSSMAN_TELEGRAM_CALLS_HOME", raising=False)


async def arm(env, **extra) -> dict:
    await connect(env)
    body = {"answering_machine": True, "answer_ring_delay_s": 0, "answer_greeting": SHORT_GREETING, **extra}
    r = await env.client.put(f"{PREFIX}/settings", json=body)
    assert r.status_code == 200, r.text
    return r.json()


async def reports(env, **params) -> list[dict]:
    return (await env.client.get(f"{ANS}/reports", params=params)).json()["items"]


async def wait_reports(env, n=1, timeout=60.0, **params) -> list[dict]:
    end = time.monotonic() + timeout
    while time.monotonic() < end:
        items = await reports(env, **params)
        if len(items) >= n:
            return items
        await asyncio.sleep(0.2)
    raise AssertionError("reports not produced")


# ------------------------------------------------------------------ auth and defaults
async def test_the_new_routes_need_the_owners_token(env):
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=env.app), base_url="http://test") as anon:
        for method, path, payload in (("GET", "/answering", None), ("POST", "/answering/start", None), ("POST", "/answering/stop", None),
                                      ("POST", "/answering/simulate", {}), ("GET", "/answering/reports", None),
                                      ("GET", "/answering/reports/ar-0123456789ab", None),
                                      ("POST", "/answering/reports/ar-0123456789ab/delivered", None),
                                      ("PUT", "/settings", {"answering_machine": True})):
            r = await anon.request(method, PREFIX + path, json=payload)
            assert r.status_code == 401, (method, path)
    assert not home(env).exists() and mgr(env).running is False


async def test_the_answering_machine_is_off_by_default_and_a_status_read_starts_nothing(env):
    r = await env.client.get(ANS)
    assert r.status_code == 200
    body = r.json()
    assert body["enabled"] is False and body["armed"] is False and body["listening"] is False and body["pending_reports"] == 0
    assert body["ring_delay_s"] == 12 and body["max_call_s"] == 180 and body["allow_unknown"] is True
    assert (await env.client.get(f"{PREFIX}/settings")).json()["answering_machine"] is False
    assert mgr(env).running is False and (await reports(env)) == []


async def test_starting_without_the_setting_or_without_a_login_is_refused_and_the_setting_stays_saved(env):
    r = await env.client.post(f"{ANS}/start")
    assert r.status_code == 409 and r.json()["error"]["code"] == "ANSWERING_NOT_ENABLED"
    saved = await env.client.put(f"{PREFIX}/settings", json={"answering_machine": True})
    assert saved.status_code == 200 and saved.json()["answering_machine"] is True
    assert saved.json()["answering"]["armed"] is False and saved.json()["answering"]["error"]["code"] == "NO_CREDENTIALS"
    assert mgr(env).running is False, "a refused start must not spawn the worker"
    assert (await env.client.get(f"{PREFIX}/settings")).json()["answering_machine"] is True


@pytest.mark.parametrize("body", [{"answer_ring_delay_s": 61}, {"answer_max_call_s": 10}, {"answer_allow_ids": [0]}, {"answer_deny_ids": [3, 3]},
                                  {"answer_greeting": "Привет, это владелец, говорите."}, {"answer_greeting": "Слушаю вас."},
                                  {"answer_greeting": "x" * 241}, {"answering_machine": "yes"}, {"answer_allow_unknown": "no"},
                                  {"peer_user_id": 5}])
async def test_bad_answering_settings_are_refused_and_change_nothing(env, body):
    before = (await env.client.get(f"{PREFIX}/settings")).json()
    r = await env.client.put(f"{PREFIX}/settings", json=body)
    assert r.status_code == 422, (body, r.text)
    assert (await env.client.get(f"{PREFIX}/settings")).json() == before


async def test_the_answering_settings_round_trip_and_the_caller_lists_are_the_owners(env):
    body = {"answer_ring_delay_s": 7, "answer_max_call_s": 90, "answer_allow_ids": [11, 12], "answer_deny_ids": [13],
            "answer_allow_unknown": False, "answer_greeting": SHORT_GREETING}
    r = await env.client.put(f"{PREFIX}/settings", json=body)
    assert r.status_code == 200
    got = (await env.client.get(f"{PREFIX}/settings")).json()
    assert {k: got[k] for k in body} == body and got["answering_machine"] is False, "lists do not switch the machine on"


# ------------------------------------------------------------------ the whole flow through the real offline worker
async def test_an_incoming_call_is_answered_logged_and_delivered_through_the_outbox(env):
    q = env.svc.bus.subscribe()
    switched = await arm(env)
    assert switched["answering"]["armed"] is True
    st = await status(env)
    assert st["answering"]["armed"] is True and st["test_label"] is True and st["answering"]["transport"] == "loopback"
    assert st["answering"]["live_tested"] is False, "the loopback line is never claimed to be a live-tested engine"
    sim = await env.client.post(f"{ANS}/simulate", json={"caller_id": 4001, "label": "Тестовый Иван"})
    assert sim.status_code == 200 and sim.json()["call_ref"]
    [report] = await wait_reports(env, 1, 90)
    assert REPORT_ID.match(report["id"]) and report["outcome"] == "message_taken" and report["test"] is True and report["answered"] is True
    assert report["caller"] == {"id": 4001, "label": "Тестовый Иван", "known": True}
    assert report["turns"] >= 2 and len(report["transcript"]) >= 4 and 2 <= len(report["summary"]) <= 4
    assert report["callback"]["requested"] is True and report["notify"] is True and report["delivered"] is False
    assert report["transport"] == "loopback"
    # the history entry of the call is the incoming kind and still carries no words
    hist = (await env.client.get(f"{PREFIX}/history")).json()["items"][0]
    assert hist["direction"] == "incoming" and "transcript" not in hist
    assert "Здравствуйте" not in json.dumps(hist, ensure_ascii=False) and "договор" not in json.dumps(hist, ensure_ascii=False)
    assert hist["postcall"]["answering"]["report_id"] == report["id"], "the call history points at its report"
    # the outbox: pending until the companion acknowledges
    pending = await reports(env, pending="true")
    assert [r["id"] for r in pending] == [report["id"]]
    assert "Автоответчик Джефф" in pending[0]["notice"] and "ТЕСТ БЕЗ TELEGRAM" in pending[0]["notice"], "the outbox carries the finished text"
    assert "notice" not in (await reports(env))[0], "only the outbox consumer is given a rendered text"
    assert (await status(env))["answering"]["pending_reports"] == 1
    one = await env.client.get(f"{ANS}/reports/{report['id']}")
    assert one.status_code == 200 and one.json()["id"] == report["id"]
    ack = await env.client.post(f"{ANS}/reports/{report['id']}/delivered")
    assert ack.status_code == 200 and ack.json() == {"id": report["id"], "delivered": True}
    assert await reports(env, pending="true") == [] and len(await reports(env)) == 1, "delivered: out of the outbox, still in the log"
    await mgr(env).drain()
    events = []
    while not q.empty():
        events.append(q.get_nowait())
    reports_evt = [e for e in events if e.get("kind") == "telegram_call.report"]
    assert reports_evt and reports_evt[0]["report_id"] == report["id"] and "transcript" not in json.dumps(reports_evt)
    assert "Тестовый Иван" not in json.dumps(events, ensure_ascii=False), "bus events carry no caller name"
    polled = (await env.client.get(f"{PREFIX}/events")).json()["events"]
    assert any(e["kind"] == "answering" and e.get("step") == "report" for e in polled)
    assert "Тестовый Иван" not in json.dumps(polled, ensure_ascii=False) and "перезвоните" not in json.dumps(polled, ensure_ascii=False).lower()


async def test_unknown_and_malformed_report_ids_are_404_and_create_nothing(env):
    for rid in ("ar-0123456789ab", "ar-xyz", "..%2F..%2FSTOP", "ar-0123456789abc"):
        assert (await env.client.get(f"{ANS}/reports/{rid}")).status_code == 404, rid
        assert (await env.client.post(f"{ANS}/reports/{rid}/delivered")).status_code == 404, rid
    assert not (home(env) / "answering").exists()


async def test_the_simulation_exists_only_in_the_offline_test_mode(env, monkeypatch):
    monkeypatch.setenv("BOSSMAN_CALLS_MODE", "telegram")                   # a real-Telegram backend: no way to fake a ring
    r = await env.client.post(f"{ANS}/simulate", json={})
    assert r.status_code == 409 and r.json()["error"]["code"] == "NOT_AVAILABLE"
    assert mgr(env).running is False


async def test_switching_the_setting_off_disarms_and_a_later_ring_is_ignored(env):
    await arm(env)
    off = await env.client.put(f"{PREFIX}/settings", json={"answering_machine": False})
    assert off.status_code == 200 and off.json()["answering"]["armed"] is False
    assert (await status(env))["answering"]["armed"] is False
    await env.client.post(f"{ANS}/simulate", json={})
    await asyncio.sleep(1.5)
    assert await reports(env) == [] and (await status(env))["call"] is None, "OFF: nothing answered, nothing logged"


async def test_stop_during_the_ring_declines_it_blocks_answering_and_resume_restores_it(env):
    await arm(env, answer_ring_delay_s=8)
    await env.client.post(f"{ANS}/simulate", json={"caller_id": 4002, "label": "Звонящий"})
    await until(env, lambda s: s["answering"]["ringing"] is True, 8)
    stop = await env.client.post(f"{PREFIX}/stop")
    assert stop.status_code == 200 and stop.json()["persisted"] is True
    [report] = await wait_reports(env, 1, 10)
    assert report["outcome"] == "stopped" and report["notify"] is False and "declined" in report["reason"]
    assert (await status(env))["answering"]["armed"] is True and (home(env) / "STOP").is_file()
    again = await env.client.post(f"{ANS}/start")
    assert again.status_code == 409 and again.json()["error"]["code"] == "STOP_ACTIVE"
    await env.client.post(f"{ANS}/simulate", json={"caller_id": 4003})
    await asyncio.sleep(1.2)
    assert [r["outcome"] for r in await reports(env)].count("stopped") == 2 and (await status(env))["call"] is None
    assert (await env.client.post(f"{PREFIX}/resume")).json()["stop_flag"] is False
    await env.client.put(f"{PREFIX}/settings", json={"answer_ring_delay_s": 0})
    await env.client.post(f"{ANS}/simulate", json={"caller_id": 4004})
    await wait_reports(env, 3, 90)
    assert any(r["outcome"] == "message_taken" for r in await reports(env)), "after resume the machine answers again"


async def test_owner_stop_all_switches_the_answering_machine_off_until_resume(env):
    await arm(env)
    c = env.client
    active = (await c.get("/api/control-plane/active")).json()["active"]["calls"]
    assert active == ["call"], "an armed answering machine is autonomous work that stop-all must reach"
    body = (await c.post("/api/control-plane/stop-all")).json()
    assert body["remaining"]["calls"] == [] and not [e for e in body["errors"] if e["plane"] == "calls"], body
    assert (home(env) / "STOP").is_file()
    await c.post(f"{ANS}/simulate", json={})
    await asyncio.sleep(1.2)
    assert [r["outcome"] for r in await reports(env)] == ["stopped"] and (await status(env))["call"] is None
    await c.post("/api/computer/resume")
    await c.post(f"{PREFIX}/resume")


async def test_an_armed_machine_is_not_an_active_plane_before_it_is_armed(env):
    assert (await env.client.get("/api/control-plane/active")).json()["active"]["calls"] == []         # negative control of the plane
    await connect(env)
    await env.client.put(f"{PREFIX}/settings", json={"answering_machine": True, "answer_greeting": SHORT_GREETING})
    assert (await env.client.get("/api/control-plane/active")).json()["active"]["calls"] == ["call"]
    await env.client.put(f"{PREFIX}/settings", json={"answering_machine": False})
    assert (await env.client.get("/api/control-plane/active")).json()["active"]["calls"] == []


async def test_the_doctor_says_the_incoming_path_is_not_verified_live(env):
    rows = (await env.client.post(f"{PREFIX}/doctor")).json()["rows"]
    row = next(r for r in rows if r["check"] == "Автоответчик")
    assert row["status"] == "PASS" and "выключен" in row["detail"]
    await env.client.put(f"{PREFIX}/settings", json={"answering_machine": True, "answer_greeting": SHORT_GREETING})
    row = next(r for r in (await env.client.post(f"{PREFIX}/doctor")).json()["rows"] if r["check"] == "Автоответчик")
    assert row["status"] == "WARN" and "тестовом режиме" in row["detail"]


async def test_the_selftest_route_runs_the_answering_machine_scenario_in_the_worker(env):
    r = await env.client.post(f"{PREFIX}/selftest", json={"scenario": "answering_machine"})
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["verdict"] == "PASS" and body["label"] == "ТЕСТ БЕЗ TELEGRAM" and [x["scenario"] for x in body["results"]] == ["answering_machine"]
    assert (await env.client.post(f"{PREFIX}/selftest", json={"scenario": "answering"})).status_code == 422
