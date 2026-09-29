"""Worker protocol, dial guard (fresh read), STOP fast path, EOF self-stop, no secrets on stdout. Fakes only."""
from __future__ import annotations

import asyncio
import json

import pytest

from bcc.secrets import Vault
from bcc.telegram_calls.account.credentials import CredentialStore
from bcc.telegram_calls.call.loopback import LoopbackTransport
from bcc.telegram_calls.call.worker import Worker
from bcc.telegram_calls.settings import SettingsStore
from bcc.telegram_calls.stopflag import StopFlag
from bcc.telegram_calls.types import CallError

from .fakes_a import (API_HASH, FakeClient, Factory, InProcWorker, User, fake_engines_factory, make_worker,
                      tg_error, until)


def arm(tmp_path, *, enabled=True, peer=4242, confirmed=True, me=111, last=None):
    """A logged-in account, calls enabled, one confirmed peer."""
    cs = CredentialStore(tmp_path, Vault(tmp_path))
    cs.save_api(123456, API_HASH)
    cs.save_session("SESSION-STRING-XYZ", me)
    st = SettingsStore(tmp_path)
    st.update({"enabled": enabled})
    if peer:
        st.set_peer(peer, "second")
        if confirmed:
            st.confirm_peer(peer)
    if last:
        st.record_outcome(last, "prev")
    return st


async def ask(w: Worker, op, **args):
    return await w.handle({"id": 1, "op": op, "args": args})


def err(resp) -> str:
    assert resp["ok"] is False, resp
    return resp["error"]["code"]


async def test_ping_status_and_bad_requests(tmp_path):
    w = make_worker(tmp_path)
    assert (await ask(w, "ping"))["result"]["pong"] is True
    st = (await ask(w, "status"))["result"]
    assert st["account"]["state"] == "no_credentials" and st["call"] is None and st["stop"]["call_stop"] is False
    assert err(await ask(w, "no_such_op")) == "INTERNAL"
    assert err(await ask(w, "_private")) == "INTERNAL"
    assert err(await w.handle({"id": 2, "op": 5, "args": {}})) == "INTERNAL"
    assert err(await w.handle({"id": 3, "op": "ping", "args": "x"})) == "INTERNAL"


@pytest.mark.parametrize("setup,code", [
    (dict(enabled=False), "NOT_ENABLED"), (dict(peer=None), "PEER_NOT_SELECTED"),
    (dict(confirmed=False), "PEER_NOT_SELECTED"), (dict(peer=111), "PEER_IS_SELF"),
    (dict(last="unknown"), "UNCERTAIN_PREVIOUS_CALL"), (dict(last="connection_lost"), "UNCERTAIN_PREVIOUS_CALL")])
async def test_dial_guard_rejections_start_nothing(tmp_path, setup, code):
    arm(tmp_path, **setup)
    t = LoopbackTransport()
    w = make_worker(tmp_path, transport=t)
    assert err(await ask(w, "dial")) == code
    assert t.dial_calls == 0 and not t.started


async def test_dial_takes_no_peer_parameter(tmp_path):
    arm(tmp_path)
    t = LoopbackTransport()
    w = make_worker(tmp_path, transport=t)
    assert err(await ask(w, "dial", peer=999)) == "PEER_NOT_ALLOWED"
    assert err(await ask(w, "dial", user_id=999)) == "PEER_NOT_ALLOWED"
    assert t.dial_calls == 0


async def test_not_logged_in_cannot_prove_peer_is_not_self(tmp_path):
    st = SettingsStore(tmp_path)
    st.update({"enabled": True}); st.set_peer(4242); st.confirm_peer(4242)
    assert err(await ask(make_worker(tmp_path), "dial")) == "NOT_LOGGED_IN"


async def test_guard_uses_a_fresh_settings_read_at_dial_time(tmp_path):
    st = arm(tmp_path)
    t = LoopbackTransport(dial_error=CallError("CALL_DECLINED"))
    w = make_worker(tmp_path, transport=t)
    await ask(w, "status")                                    # worker has "seen" the enabled state
    st.update({"enabled": False})                             # owner switches calls off afterwards
    assert err(await ask(w, "dial")) == "NOT_ENABLED"
    st.update({"enabled": True})
    assert (await ask(w, "dial"))["ok"] is True               # ... and on again: honoured too
    assert await until(lambda: t.dial_calls == 1)


async def test_stop_flag_blocks_dial_and_survives_a_new_worker(tmp_path):
    arm(tmp_path)
    StopFlag(tmp_path).set("owner")
    assert err(await ask(make_worker(tmp_path), "dial")) == "STOP_ACTIVE"
    StopFlag(tmp_path).clear()
    t = LoopbackTransport(dial_error=CallError("CALL_BUSY"))
    assert (await ask(make_worker(tmp_path, transport=t), "dial"))["ok"] is True


async def test_declined_call_dials_once_records_outcome_and_never_redials(tmp_path):
    st = arm(tmp_path)
    t = LoopbackTransport(dial_error=CallError("CALL_DECLINED"))
    events = []
    w = make_worker(tmp_path, transport=t, emit=events.append)
    r = await ask(w, "dial")
    assert r["ok"] and r["result"]["accepted"] is True
    assert await until(lambda: any(e["event"] == "record" for e in events))
    rec = next(e for e in events if e["event"] == "record")["record"]
    assert rec["outcome"] == "declined" and rec["error_code"] == "CALL_DECLINED"
    await asyncio.sleep(0.2)
    assert t.dial_calls == 1                                   # no automatic redial
    s = st.load()
    assert s.last_outcome == "declined" and s.last_call_id == r["result"]["call_id"]
    assert (await ask(w, "status"))["result"]["call"] is None


async def test_outcome_is_unknown_on_disk_while_the_call_runs_then_hangup_completes(tmp_path):
    st = arm(tmp_path)
    t = LoopbackTransport()
    events = []
    w = make_worker(tmp_path, transport=t, emit=events.append)
    r = await ask(w, "dial")
    assert r["ok"]
    assert st.load().last_outcome == "unknown"                 # crash-safe: a dead worker leaves UNKNOWN behind
    assert await until(lambda: (w._session is not None and w._session.record.state.value == "active"))
    assert err(await ask(w, "dial")) == "CALL_IN_PROGRESS"
    assert (await ask(w, "status"))["result"]["call"]["state"] == "active"
    assert (await ask(w, "hangup"))["result"] == {"hung_up": True}
    assert await until(lambda: any(e["event"] == "record" for e in events))
    rec = next(e for e in events if e["event"] == "record")["record"]
    assert rec["outcome"] == "completed" and rec["transport"] == "loopback"
    assert st.load().last_outcome == "completed"
    assert t.dial_calls == 1 and t.hangup_calls >= 1
    assert [e["state"] for e in events if e["event"] == "state"][-1] == "ended"
    assert (await ask(w, "hangup"))["result"] == {"hung_up": False}


async def test_confirm_unknown_allows_the_next_dial_after_an_uncertain_call(tmp_path):
    arm(tmp_path, last="unknown")
    t = LoopbackTransport(dial_error=CallError("CALL_DECLINED"))
    w = make_worker(tmp_path, transport=t)
    assert err(await ask(w, "dial")) == "UNCERTAIN_PREVIOUS_CALL"
    assert err(await ask(w, "dial", confirm_unknown="yes")) == "UNCERTAIN_PREVIOUS_CALL"
    assert (await ask(w, "dial", confirm_unknown=True))["ok"] is True


async def test_missing_engines_are_a_clear_error_and_leave_outcome_untouched(tmp_path):
    st = arm(tmp_path, last="declined")

    def boom(data_dir):
        raise ImportError("no speech.factory")
    w = make_worker(tmp_path, engines=boom)
    assert err(await ask(w, "dial")) == "DEPENDENCIES_MISSING"
    assert st.load().last_outcome == "declined"
    assert (await ask(w, "status"))["result"]["call"] is None         # not stuck "busy"

    def brain_down(data_dir):
        raise CallError("BRAIN_NOT_CONFIGURED")
    assert err(await ask(make_worker(tmp_path, engines=brain_down), "dial")) == "BRAIN_NOT_CONFIGURED"


async def test_stop_racing_the_setup_aborts_before_any_dial(tmp_path):
    arm(tmp_path)
    t = LoopbackTransport()
    holder = {}

    def engines(data_dir):
        holder["w"].stop_now("test")                          # STOP arrives while models load
        return fake_engines_factory()(data_dir)
    w = make_worker(tmp_path, transport=t, engines=engines)
    holder["w"] = w
    assert err(await ask(w, "dial")) == "STOP_ACTIVE"
    assert t.dial_calls == 0 and not t.started


# ---------------------------------------------------------------- serve loop (JSON lines)
async def rpc(h: InProcWorker, op, rid, **args):
    h.write((json.dumps({"id": rid, "op": op, "args": args}) + "\n").encode())
    assert await until(lambda: any(o.get("id") == rid for o in h.lines_out), 5), f"no answer to {op}"
    return next(o for o in h.lines_out if o.get("id") == rid)


async def test_stop_fast_path_silences_hangs_up_flags_and_blocks_redial(tmp_path):
    arm(tmp_path)
    t = LoopbackTransport()
    h = InProcWorker(make_worker(tmp_path, transport=t))
    assert (await rpc(h, "dial", 1))["ok"]
    assert await until(lambda: h.worker._session is not None and h.worker._session.record.state.value == "active")
    r = await rpc(h, "stop", 2)
    assert r["ok"] and r["result"] == {"stopped": True, "had_call": True}
    assert StopFlag(tmp_path).call_stop_set()                                # persistent flag raised first
    assert await until(lambda: any(o.get("event") == "record" for o in h.lines_out))
    rec = next(o for o in h.lines_out if o.get("event") == "record")["record"]
    assert rec["outcome"] == "stopped" and rec["summary"]["generated_by"] == "mechanical"
    assert t.hangup_calls >= 1
    assert (await rpc(h, "dial", 3))["error"]["code"] == "STOP_ACTIVE"
    h.eof(); await asyncio.wait_for(h.task, 5)


async def test_stop_is_answered_while_another_op_is_slow(tmp_path):
    arm(tmp_path)
    slow = asyncio.Event()

    class SlowClient(FakeClient):
        async def send_code_request(self, phone):
            await slow.wait()
            return await super().send_code_request(phone)
    CredentialStore(tmp_path, Vault(tmp_path)).clear_session()
    w = Worker(tmp_path, client_factory=Factory(SlowClient()), transport_factory=lambda c: LoopbackTransport(),
               engines_factory=fake_engines_factory(), session_cfg=None)
    h = InProcWorker(w)
    h.write(b'{"id": 1, "op": "login_start", "args": {"phone": "+79001234567"}}\n')
    await asyncio.sleep(0.05)
    r = await rpc(h, "stop", 2)                                              # not queued behind login_start
    assert r["result"]["stopped"] is True and not any(o.get("id") == 1 for o in h.lines_out)
    slow.set()
    h.eof(); await asyncio.wait_for(h.task, 5)


async def test_stdin_eof_hangs_up_and_exits(tmp_path):
    st = arm(tmp_path)
    t = LoopbackTransport()
    h = InProcWorker(make_worker(tmp_path, transport=t))
    assert (await rpc(h, "dial", 1))["ok"]
    assert await until(lambda: h.worker._session is not None and h.worker._session.record.state.value == "active")
    h.eof()
    await asyncio.wait_for(h.task, 8)                                        # serve() returned = process would exit
    assert t.hangup_calls >= 1 and t.closed
    assert st.load().last_outcome == "stopped"
    assert any(o.get("event") == "record" for o in h.lines_out)


async def test_shutdown_op_ends_a_live_call_then_exits(tmp_path):
    arm(tmp_path)
    t = LoopbackTransport()
    h = InProcWorker(make_worker(tmp_path, transport=t))
    await rpc(h, "dial", 1)
    assert await until(lambda: h.worker._session is not None and h.worker._session.record.state.value == "active")
    assert (await rpc(h, "shutdown", 2))["result"] == {"bye": True}
    await asyncio.wait_for(h.task, 5)
    assert t.hangup_calls >= 1


async def test_bad_json_and_non_object_lines_are_answered_not_fatal(tmp_path):
    h = InProcWorker(make_worker(tmp_path))
    h.write(b"{oops\n")
    assert await until(lambda: any(o.get("ok") is False for o in h.lines_out))
    h.inbox.put_nowait("[1,2]")
    assert await until(lambda: sum(1 for o in h.lines_out if o.get("ok") is False) == 2)
    assert (await rpc(h, "ping", 9))["ok"]
    h.eof(); await asyncio.wait_for(h.task, 5)


async def test_no_secret_ever_reaches_stdout(tmp_path):
    CredentialStore(tmp_path, Vault(tmp_path)).save_api(123456, API_HASH)
    factory = Factory(script={"sign_in": [tg_error("SessionPasswordNeededError"), tg_error("PasswordHashInvalidError")]})
    h = InProcWorker(Worker(tmp_path, client_factory=factory, engines_factory=fake_engines_factory()))
    assert (await rpc(h, "login_start", 1, phone="+79001234567"))["ok"]
    assert (await rpc(h, "login_code", 2, code="24680"))["result"]["state"] == "password_needed"
    assert (await rpc(h, "login_password", 3, password="hunter2-secret"))["error"]["code"] == "LOGIN_PASSWORD_INVALID"
    assert (await rpc(h, "login_password", 4, password="hunter2-secret"))["result"]["state"] == "ready"
    h.eof(); await asyncio.wait_for(h.task, 5)
    wire = json.dumps(h.lines_out)
    for secret in ("+79001234567", "79001234567", "24680", "hunter2-secret", API_HASH, "SESSION-STRING-XYZ", "HASH-1"):
        assert secret not in wire


async def test_contacts_and_resolve_ops_and_not_during_a_call(tmp_path):
    arm(tmp_path, me=111)
    client = FakeClient(contacts=[User(4242, "Second"), User(1, "Bot", bot=True)], entities={4242: User(4242, "Second")})
    t = LoopbackTransport()
    w = make_worker(tmp_path, transport=t, client=client)
    assert (await ask(w, "contacts"))["result"]["contacts"] == [{"user_id": 4242, "label": "Second", "username": None}]
    assert (await ask(w, "resolve_peer", user_id=4242))["result"]["peer"]["user_id"] == 4242
    assert err(await ask(w, "resolve_peer", user_id=111)) == "PEER_IS_SELF"
    assert client.disconnected >= 2                                          # never leaves a second connection open
    assert (await ask(w, "dial"))["ok"]
    assert await until(lambda: w._session is not None)
    assert err(await ask(w, "contacts")) == "CALL_IN_PROGRESS"               # would duplicate the session's auth key
    assert err(await ask(w, "logout")) == "CALL_IN_PROGRESS"
    await ask(w, "hangup")
    assert await until(lambda: w._session is None)
