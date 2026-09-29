"""CallsManager against an in-process worker (real JSON-lines protocol, fake engines/transport/Telegram)."""
from __future__ import annotations

import asyncio
import json
import sys

import pytest

from bcc.events import EventBus
from bcc.secrets import Vault
from bcc.telegram_calls.account.credentials import CredentialStore
from bcc.telegram_calls.call import manager as mgr_mod
from bcc.telegram_calls.call.loopback import LoopbackTransport
from bcc.telegram_calls.call.manager import CallsManager
from bcc.telegram_calls.settings import SettingsError, SettingsStore
from bcc.telegram_calls.stopflag import StopFlag
from bcc.telegram_calls.types import CallError

from .fakes_a import (API_HASH, DeadHandle, FakeClient, InProcWorker, User, fake_engines_factory, make_worker, tg_error,
                      Factory, until)


class Harness:
    def __init__(self, tmp_path, *, transport_factory=None, client=None, engines=None, postcall=None):
        self.tmp, self.handles, self.spawns, self.transports = tmp_path, [], [], []
        self.client = client or FakeClient(contacts=[User(4242, "Second"), User(9, "Bot", bot=True)],
                                           entities={4242: User(4242, "Second")})
        self.engines, self.transport_factory = engines, transport_factory

        async def spawn(argv, env):
            self.spawns.append((argv, env))
            t = (self.transport_factory or (lambda: LoopbackTransport()))()
            self.transports.append(t)
            h = InProcWorker(make_worker(tmp_path, transport=t, client=self.client, engines=self.engines))
            self.handles.append(h)
            return h
        self.spawn = spawn
        self.m = CallsManager(tmp_path, vault=Vault(tmp_path), spawn=spawn, postcall=postcall)

    def arm(self, *, me=111, enabled=True, peer=4242, confirm=True, last=None):
        cs = CredentialStore(self.tmp, Vault(self.tmp))
        cs.save_api(123456, API_HASH)
        cs.save_session("SESSION-STRING-XYZ", me)
        st = SettingsStore(self.tmp)
        st.update({"enabled": enabled})
        if peer:
            st.set_peer(peer, "second")
            if confirm:
                st.confirm_peer(peer)
        if last:
            st.record_outcome(last, "prev")
        return st


async def code_of(coro) -> str:
    with pytest.raises(CallError) as ei:
        await coro
    return ei.value.code


async def active(h: Harness) -> bool:
    return await until(lambda: h.handles and h.handles[0].worker._session is not None
                       and h.handles[0].worker._session.record.state.value == "active")


async def test_status_and_reads_never_spawn_the_worker(tmp_path):
    h = Harness(tmp_path)
    st = h.m.status()
    assert st["worker"] == {"running": False} and st["account"]["state"] == "no_credentials"
    assert st["settings"]["enabled"] is False and st["call"] is None and st["last_call"] is None
    assert h.m.get_settings()["peer"] is None and h.m.history() == [] and h.m.events() == {"events": [], "seq": 0}
    assert h.spawns == []


async def test_worker_is_spawned_isolated_with_the_data_dir(tmp_path):
    h = Harness(tmp_path)
    h.arm()
    await h.m.contacts()
    argv, env = h.spawns[0]
    assert argv[:4] == [sys.executable, "-I", "-m", "bcc.telegram_calls"] and env["BCC_DATA_DIR"] == str(tmp_path)
    await h.m.contacts()
    assert len(h.spawns) == 1                                      # one worker, reused
    await h.m.shutdown()


async def test_spawn_failure_is_a_stable_code(tmp_path):
    async def bad(argv, env):
        raise OSError("no python")
    m = CallsManager(tmp_path, vault=Vault(tmp_path), spawn=bad)
    assert await code_of(m.contacts()) == "WORKER_UNAVAILABLE"


async def test_settings_are_validated_and_secret_free(tmp_path):
    h = Harness(tmp_path)
    assert h.m.set_settings({"enabled": True, "max_call_s": 120})["enabled"] is True
    with pytest.raises(SettingsError):
        h.m.set_settings({"api_hash": "x"})
    with pytest.raises(SettingsError):
        h.m.set_settings({"enabled": "on"})
    assert h.m.get_settings()["max_call_s"] == 120.0


async def test_credentials_login_and_status_are_booleans_only(tmp_path):
    h = Harness(tmp_path, client=FakeClient(me_id=111))
    acct = h.m.login_credentials(123456, API_HASH)
    assert acct == {"state": "logged_out", "has_credentials": True, "has_session": False}
    assert await code_of(_async(h.m.login_credentials, 5, "bad")) == "NO_CREDENTIALS"
    assert (await h.m.login_start("+79001234567"))["state"] == "code_sent"
    assert h.m.status()["account"]["state"] == "code_sent"
    acct = await h.m.login_code("13579")
    assert acct["state"] == "ready" and acct["has_session"] is True
    wire = json.dumps([h.m.status(), h.m.events(), acct])
    for secret in (API_HASH, "SESSION-STRING-XYZ", "+79001234567", "13579"):
        assert secret not in wire
    assert h.m.creds.self_id() == 111
    await h.m.shutdown()


async def _async(fn, *a):
    return fn(*a)


async def test_login_error_codes_pass_through_without_text(tmp_path):
    h = Harness(tmp_path, client=FakeClient(script={"send_code_request": [tg_error("PhoneNumberInvalidError")]}))
    h.m.login_credentials(123456, API_HASH)
    with pytest.raises(CallError) as ei:
        await h.m.login_start("+15550001111")
    assert ei.value.code == "LOGIN_PHONE_INVALID" and "15550001111" not in repr(ei.value.as_dict()) and "secret text" not in repr(ei.value.as_dict())
    await h.m.shutdown()


async def test_peer_selection_flow_needs_confirmation_and_rejects_self(tmp_path):
    h = Harness(tmp_path)
    h.arm(peer=None)
    assert [c["user_id"] for c in (await h.m.contacts())["contacts"]] == [4242]
    assert await code_of(h.m.select_peer(111)) == "PEER_IS_SELF"            # own account, refused before Telegram
    assert await code_of(h.m.select_peer(31337)) == "PEER_NOT_FOUND"
    res = await h.m.select_peer(4242)
    assert res["peer"]["user_id"] == 4242 and res["settings"]["peer_confirmed"] is False
    with pytest.raises(CallError) as ei:
        h.m.confirm_peer(9999)                                              # confirm names another peer
    assert ei.value.code == "PEER_NOT_SELECTED"
    assert h.m.confirm_peer(4242)["peer_confirmed"] is True
    with pytest.raises(CallError) as ei:
        h.m.confirm_peer(111)
    assert ei.value.code == "PEER_IS_SELF"
    assert h.m.clear_peer()["peer"] is None
    await h.m.shutdown()


async def test_manager_guard_refuses_before_starting_the_worker(tmp_path):
    h = Harness(tmp_path)
    assert await code_of(h.m.dial()) == "NOT_ENABLED"
    h.arm(enabled=True, peer=None)
    assert await code_of(h.m.dial()) == "PEER_NOT_SELECTED"
    StopFlag(tmp_path).set("owner")
    h.arm()
    assert await code_of(h.m.dial()) == "STOP_ACTIVE"
    assert h.spawns == []


async def test_full_call_history_postcall_and_events(tmp_path):
    seen = []

    class PC:
        async def run(self, rec):
            seen.append(dict(rec))
            return {"memory": "written", "drafts": []}
    h = Harness(tmp_path, postcall=PC())
    h.arm()
    res = await h.m.dial()
    assert res["accepted"] and res["call_id"] and h.m.status()["call"]["active"] is True
    assert await code_of(h.m.dial()) == "CALL_IN_PROGRESS"
    assert await active(h)
    assert (await h.m.hangup())["hung_up"] is True
    assert await until(lambda: h.m.history())
    rec = h.m.history()[0]
    assert rec["outcome"] == "completed" and rec["call_id"] == res["call_id"] and rec["postcall"]["memory"] == "written"
    assert seen and seen[0]["summary"]["text"]                              # postcall got the summary...
    assert rec["summary"]["generated_by"] and "text" not in rec["summary"]  # ...history does not keep its text
    assert h.m.status()["call"] is None and h.m.get_settings()["last_outcome"] == "completed"
    ev = h.m.events()
    assert ev["seq"] == len(ev["events"]) > 2 and {e["event"] for e in ev["events"]} >= {"state", "record"}
    assert h.m.events(since_seq=ev["seq"])["events"] == []
    assert h.m.events(since_seq=ev["seq"] - 1)["events"][0]["seq"] == ev["seq"]
    saved = json.loads((tmp_path / "telegram_calls" / "history.json").read_text(encoding="utf-8"))
    assert saved[0]["call_id"] == res["call_id"] and "Итог" not in json.dumps(saved, ensure_ascii=False)
    assert CallsManager(tmp_path, vault=Vault(tmp_path)).history()[0]["call_id"] == res["call_id"]
    await h.m.shutdown()


async def test_no_redial_after_a_declined_call(tmp_path):
    h = Harness(tmp_path, transport_factory=lambda: LoopbackTransport(dial_error=CallError("CALL_DECLINED")))
    h.arm()
    await h.m.dial()
    assert await until(lambda: h.m.history())
    await asyncio.sleep(0.2)
    assert h.m.history()[0]["outcome"] == "declined" and len(h.m.history()) == 1
    assert sum(t.dial_calls for t in h.transports) == 1
    await h.m.shutdown()


async def test_worker_side_refusal_clears_the_active_flag_and_makes_no_record(tmp_path):
    def missing(data_dir):
        raise ImportError("x")
    h = Harness(tmp_path, engines=missing)
    h.arm()
    assert await code_of(h.m.dial()) == "DEPENDENCIES_MISSING"
    assert h.m.status()["call"] is None and h.m.history() == []
    await h.m.shutdown()


async def test_worker_crash_mid_call_synthesizes_unknown_and_blocks_casual_redial(tmp_path):
    h = Harness(tmp_path)
    h.arm()
    res = await h.m.dial()
    assert await active(h)
    h.handles[0].task.cancel()                                              # the worker process "dies"
    assert await until(lambda: h.m.history())
    rec = h.m.history()[0]
    assert rec["outcome"] == "unknown" and rec["synthesized"] is True and rec["call_id"] == res["call_id"]
    assert h.m.status()["worker"]["running"] is False and h.m.status()["call"] is None
    assert h.m.get_settings()["last_outcome"] == "unknown"
    assert await code_of(h.m.dial()) == "UNCERTAIN_PREVIOUS_CALL"          # needs explicit confirmation
    assert len(h.spawns) == 1
    assert (await h.m.dial(confirm_unknown=True))["accepted"]               # explicit owner confirmation passes
    assert len(h.spawns) == 2
    await h.m.shutdown()


async def test_confirm_unknown_must_be_literal_true(tmp_path):
    h = Harness(tmp_path)
    h.arm(last="connection_lost")
    assert await code_of(h.m.dial(confirm_unknown="yes")) == "UNCERTAIN_PREVIOUS_CALL"   # type: ignore[arg-type]
    assert h.spawns == []


async def test_stop_hangs_up_flags_and_blocks_until_resume(tmp_path):
    h = Harness(tmp_path)
    h.arm()
    await h.m.dial()
    assert await active(h)
    out = await h.m.stop("owner")
    assert out["stopped"] is True and out["worker"] == "confirmed" and out["had_call"] is True
    assert StopFlag(tmp_path).call_stop_set()
    assert h.m.history()[0]["outcome"] == "stopped"
    assert await code_of(h.m.dial()) == "STOP_ACTIVE"
    st = await h.m.resume()
    assert st["stop"]["call_stop"] is False
    assert (await h.m.dial())["accepted"] is True                          # stopped is a certain outcome: no confirm needed
    await h.m.shutdown()


async def test_stop_without_worker_still_sets_the_persistent_flag(tmp_path):
    h = Harness(tmp_path)
    assert (await h.m.stop("owner")) == {"stopped": True, "worker": "not_running"}
    assert StopFlag(tmp_path).call_stop_set() and h.spawns == []


async def test_resume_never_clears_the_global_computer_stop(tmp_path):
    h = Harness(tmp_path)
    (tmp_path / "computer").mkdir()
    (tmp_path / "computer" / "STOP").write_text("owner\n1\n", encoding="utf-8")
    h.arm()
    await h.m.stop("owner")
    st = await h.m.resume()
    assert st["stop"] == {"call_stop": False, "global_stop": True}
    assert await code_of(h.m.dial()) == "STOP_ACTIVE"


async def test_global_stop_bus_event_stops_the_call(tmp_path):
    h = Harness(tmp_path)
    h.arm()
    bus = EventBus()
    h.m.attach_bus(bus)
    await h.m.dial()
    assert await active(h)
    await bus.emit("task.created", task_id=1)                               # unrelated events do nothing
    await asyncio.sleep(0.05)
    assert not StopFlag(tmp_path).call_stop_set()
    await bus.emit("computer.stop", by="owner")
    assert await until(lambda: h.m.history() and StopFlag(tmp_path).call_stop_set())
    assert h.m.history()[0]["outcome"] == "stopped"
    await h.m.shutdown()
    assert not bus._subscribers                                             # unsubscribed on shutdown


async def test_stop_hard_terminates_a_worker_that_does_not_answer(tmp_path, monkeypatch):
    monkeypatch.setattr(mgr_mod, "STOP_ACK_S", 0.1)
    dead = DeadHandle()

    async def spawn(argv, env):
        return dead
    m = CallsManager(tmp_path, vault=Vault(tmp_path), spawn=spawn)
    h = Harness(tmp_path)
    h.arm()
    t = asyncio.create_task(m.dial())
    assert await until(lambda: dead.writes)
    dead.reply({"id": dead.writes[0]["id"], "ok": True, "result": {"call_id": "c1", "accepted": True}})
    await t
    out = await m.stop("owner")
    assert out["worker"] == "terminated" and dead.terminated is True
    assert await until(lambda: m.history())
    assert m.history()[0]["outcome"] == "unknown" and m.history()[0]["call_id"] == "c1"   # hangup unproven -> UNKNOWN
    assert StopFlag(tmp_path).call_stop_set()


async def test_dial_ack_timeout_terminates_worker_and_records_unknown(tmp_path, monkeypatch):
    monkeypatch.setattr(mgr_mod, "DIAL_ACK_S", 0.1)
    dead = DeadHandle()

    async def spawn(argv, env):
        return dead
    m = CallsManager(tmp_path, vault=Vault(tmp_path), spawn=spawn)
    Harness(tmp_path).arm()
    assert await code_of(m.dial()) == "WORKER_TIMEOUT"
    assert dead.terminated is True
    assert await until(lambda: m.history())
    assert m.history()[0]["outcome"] == "unknown" and m.history()[0]["error_code"] == "WORKER_UNAVAILABLE"
    assert m.status()["call"] is None


async def test_settings_and_peer_locked_while_a_call_is_live(tmp_path):
    h = Harness(tmp_path)
    h.arm()
    await h.m.dial()
    assert await active(h)
    assert await code_of(h.m.select_peer(4242)) == "CALL_IN_PROGRESS"
    assert await code_of(_async(h.m.confirm_peer, 4242)) == "CALL_IN_PROGRESS"
    assert await code_of(_async(h.m.clear_peer)) == "CALL_IN_PROGRESS"
    assert await code_of(h.m.contacts()) == "CALL_IN_PROGRESS"
    assert await code_of(h.m.logout()) == "CALL_IN_PROGRESS"
    assert await code_of(_async(h.m.login_credentials, 5, API_HASH)) == "CALL_IN_PROGRESS"
    await h.m.stop("owner")
    await h.m.shutdown()


async def test_history_is_bounded_and_deduplicated(tmp_path):
    m = CallsManager(tmp_path, vault=Vault(tmp_path))
    for i in range(60):
        await m._on_record({"call_id": f"c{i}", "outcome": "completed", "transport": "telegram", "summary": None})
    await m._on_record({"call_id": "c59", "outcome": "failed", "transport": "telegram"})     # duplicate ignored
    assert len(m.history(limit=100)) == 50 and m.history()[0]["call_id"] == "c59"
    assert m.history()[0]["outcome"] == "completed"


async def test_postcall_failure_never_breaks_recording(tmp_path):
    class Boom:
        async def run(self, rec):
            raise RuntimeError("disk full")
    m = CallsManager(tmp_path, vault=Vault(tmp_path), postcall=Boom())
    await m._on_record({"call_id": "c1", "outcome": "completed", "transport": "telegram", "summary": None})
    assert m.history()[0]["postcall"] == {"error": "RuntimeError"}


async def test_shutdown_ends_a_live_call_and_the_worker(tmp_path):
    h = Harness(tmp_path)
    h.arm()
    await h.m.dial()
    assert await active(h)
    await h.m.shutdown()
    assert h.m.worker_running is False
    assert h.transports[0].hangup_calls >= 1
    assert h.m.history()[0]["outcome"] in ("stopped", "completed", "unknown")
