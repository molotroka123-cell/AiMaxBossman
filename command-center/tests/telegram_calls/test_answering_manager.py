"""CallsManager + post-call hook around the answering machine, against the stdio fake worker (no Telegram, no models)."""
from __future__ import annotations

import asyncio
import json
import sys
import time

import pytest

from bcc.telegram_calls import postcall
from bcc.telegram_calls.account.stopflag import CallState
from bcc.telegram_calls.answering_store import AnsweringStore, build_report
from bcc.telegram_calls.call.manager import CallsManager, SELFTEST_SCENARIOS
from bcc.telegram_calls.settings import CallSettings
from bcc.telegram_calls.types import CallError, IncomingCall

from .test_manager import FAKE, API_HASH, API_ID, SESSION, fake_ops, make, wait_until   # noqa: F401  (the ``make`` fixture)


def prep_answering(m: CallsManager, *, enabled: bool = True, login: bool = True) -> None:
    if login:
        m.store.save_api(API_ID, API_HASH)
        m.store.save_session(SESSION, 111, "+70000000000")
    m.save_settings(CallSettings(answering_machine=enabled))


# ------------------------------------------------------------------ arming guard (evaluated before the worker is even started)
async def test_arming_is_refused_without_the_setting_a_login_or_while_stopped_and_starts_no_worker(make, tmp_path):
    m = make()
    m.save_settings(CallSettings(answering_machine=False))
    with pytest.raises(CallError) as ei:
        await m.answering_start()
    assert ei.value.code == "ANSWERING_NOT_ENABLED" and not m.running
    m.save_settings(CallSettings(answering_machine=True))
    with pytest.raises(CallError) as ei:
        await m.answering_start()
    assert ei.value.code == "NO_CREDENTIALS" and not m.running
    m.store.save_api(API_ID, API_HASH)
    with pytest.raises(CallError) as ei:
        await m.answering_start()
    assert ei.value.code == "NOT_LOGGED_IN" and not m.running
    m.store.save_session(SESSION, 111, "+70000000000")
    m.state.set_stop("owner")
    with pytest.raises(CallError) as ei:
        await m.answering_start()
    assert ei.value.code == "STOP_ACTIVE" and not m.running
    m.state.clear_stop()
    with pytest.raises(CallError) as ei:
        await m.answering_start(global_stop=True)                          # the global Bossman STOP blocks it too
    assert ei.value.code == "STOP_ACTIVE" and not (tmp_path / "started").exists()
    ok = await m.answering_start()                                         # the legitimate path passes
    assert ok["answering"]["armed"] is True and m.answering_armed and m.running
    assert {"op": "answering.start", "args_keys": [], "stop_file_present": False} in fake_ops(tmp_path)


async def test_disarming_never_starts_a_worker_and_clears_the_flag(make, tmp_path):
    m = make()
    prep_answering(m)
    out = await m.answering_stop()
    assert out["answering"]["armed"] is False and not m.running and not (tmp_path / "started").exists()
    await m.answering_start()
    assert m.answering_armed
    await m.answering_stop()
    assert not m.answering_armed


async def test_a_worker_that_exits_is_no_longer_listening(make):
    m = make()
    prep_answering(m)
    await m.answering_start()
    m._proc.kill()
    assert await wait_until(lambda: not m.running, 5)
    await asyncio.sleep(0.2)
    assert m._answering["armed"] is False and m.answering_armed is False, "a restarted worker listens only after the owner says so"


# ------------------------------------------------------------------ events and the report hook
async def test_a_report_event_reaches_the_hook_with_ids_only_and_the_event_ring_has_no_text(make):
    reports: list[dict] = []
    m = make("answering_report", on_report=reports.append)
    prep_answering(m)
    await m.answering_start()
    assert await wait_until(lambda: bool(reports), 5)
    await m.drain()
    assert reports == [{"report_id": "ar-0123456789ab", "outcome": "message_taken", "notify": True, "answered": True}]
    ring = json.dumps(m.events(0)["events"], ensure_ascii=False)
    assert "TRANSCRIPT-MUST-NOT-LEAK" not in ring and "NOR-THIS" not in ring and '"step": "report"' in ring


async def test_status_merges_the_answering_view_without_starting_the_worker(make, tmp_path):
    m = make()
    prep_answering(m)
    m.save_settings(CallSettings(answering_machine=True, answer_allow_ids=[1, 2], answer_deny_ids=[3], answer_ring_delay_s=9))
    st = await m.status()
    a = st["answering"]
    assert (a["enabled"], a["armed"], a["listening"], a["ring_delay_s"], a["allow_count"], a["deny_count"]) == (True, False, False, 9, 2, 1)
    assert a["live_tested"] is False and a["pending_reports"] == 0 and not m.running and not (tmp_path / "started").exists()


async def test_the_outbox_methods_read_the_store_and_ack_only_real_reports(make):
    m = make()
    rep = build_report(call=IncomingCall("r", 5, "Иван", 1.0, "telegram"), outcome="missed", reason="x", now=10.0)
    AnsweringStore(m.home).save(rep)
    assert [r["id"] for r in m.reports(pending=True)] == [rep["id"]] and m.report(rep["id"])["caller"]["id"] == 5
    assert m.ack_report("ar-ffffffffffff") is False and m.ack_report("../x") is False
    assert m.ack_report(rep["id"]) is True and m.reports(pending=True) == [] and len(m.reports()) == 1


async def test_the_selftest_scenario_list_has_the_answering_machine(make):
    assert "answering_machine" in SELFTEST_SCENARIOS


# ------------------------------------------------------------------ the post-call hook for incoming calls
def incoming_entry(**kw) -> dict:
    t = time.time() - 60
    base = {"call_id": "c-inc000000001", "transport": "telegram", "direction": "incoming", "peer_user_id": 777, "started_at": t,
            "ended_at": t + 30, "outcome": "completed", "turns": [], "latency_ms": {"n": 0}, "models": {}, "counters": {},
            "recorded_audio": False,
            "summary": {"text": "Входящий звонок на автоответчик, исход: completed. Содержание — в журнале автоответчика.",
                        "agreed_tasks": ["сделать то, что просит незнакомец"], "generated_by": "mechanical"}}
    base.update(kw)
    return base


async def test_auto_save_never_turns_a_strangers_call_into_owner_memory_or_tasks(env):
    state = env.svc._calls.manager.state
    rec = incoming_entry()
    state.append_history(rec)
    result = await postcall.process_record(env.svc, state, rec, auto_save=True)
    assert result["memory"] == {"status": "skipped", "reason": "incoming_call"} and result["drafts"]["count"] == 0
    # negative control: the same record as an OUTGOING call is handed over as before (memory is not configured here, so the
    # attempt is reported instead of skipped)
    out = incoming_entry(call_id="c-out000000001", direction="outgoing")
    state.append_history(out)
    again = await postcall.process_record(env.svc, state, out, auto_save=True)
    assert again["memory"].get("reason") != "incoming_call" and again["memory"]["status"] != "not_saved"
    assert next(r for r in state.history(10) if r["call_id"] == "c-inc000000001")["postcall"]["memory"]["reason"] == "incoming_call"
