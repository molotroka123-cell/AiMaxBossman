"""CallsManager: lazy worker, STOP order, guard re-evaluation, death handling, secret hygiene, diagnostics.

The worker is replaced by ``fake_worker.py`` (same stdio protocol) for the failure modes that cannot be forced on the real
one. Every tightening has a paired case: the legitimate path passes, the bad one is refused.
"""
from __future__ import annotations

import asyncio
import json
import logging
import os
import sys
import time
from pathlib import Path

import pytest

from bcc.secrets import Vault
from bcc.telegram_calls.call.manager import CallsManager, outcome_error, scrub_event
from bcc.telegram_calls.settings import CallSettings
from bcc.telegram_calls.types import CallError, Outcome

FAKE = Path(__file__).with_name("fake_worker.py")
API_ID = 1234567
API_HASH = "0123456789abcdef" * 2                          # fixture shape only, not a credential
SESSION = "1A" + "Qz9_x-" * 20


@pytest.fixture
async def make(tmp_path, monkeypatch):
    monkeypatch.delenv("BOSSMAN_VAULT_KEY", raising=False)
    monkeypatch.delenv("BOSSMAN_TELEGRAM_CALLS_HOME", raising=False)
    monkeypatch.setenv("BOSSMAN_CALLS_MODE", "offline_test")
    made: list[CallsManager] = []

    def factory(mode: str = "normal", **kw) -> CallsManager:
        env = dict(os.environ)
        env.update({"FAKE_WORKER_MODE": mode, "FAKE_WORKER_LOG": str(tmp_path / "fake.log"),
                    "FAKE_WORKER_STARTED": str(tmp_path / "started")})
        data = tmp_path / "data"
        m = CallsManager(data, vault=Vault(data), worker_argv=[sys.executable, "-I", str(FAKE)], env=env, **kw)
        made.append(m)
        return m

    yield factory
    for m in made:
        await m.shutdown()


def prep(m: CallsManager, *, peer: int = 222, enabled: bool = True) -> None:
    m.store.save_api(API_ID, API_HASH)
    m.store.save_session(SESSION, 111, "+70000000000")
    m.save_settings(CallSettings(enabled=enabled, peer_user_id=peer or None, peer_label="second account"))


def fake_ops(tmp_path: Path) -> list[dict]:
    log = tmp_path / "fake.log"
    return [json.loads(line) for line in log.read_text(encoding="utf-8").splitlines()] if log.exists() else []


async def wait_until(cond, timeout: float = 8.0) -> bool:
    end = time.monotonic() + timeout
    while time.monotonic() < end:
        if cond():
            return True
        await asyncio.sleep(0.02)
    return False


# ------------------------------------------------------------------ lazy start

async def test_status_history_and_events_never_start_the_worker(make, tmp_path):
    m = make()
    st = await m.status()
    m.history()
    m.events(0)
    assert not m.running and not (tmp_path / "started").exists()
    assert st["worker"]["running"] is False and st["account"]["state"] == "no_credentials"
    assert st["enabled"] is False and st["peer"] is None and st["call"] is None
    assert not (tmp_path / "data" / "telegram-calls").exists(), "a read-only status must not create the calls directory"


async def test_an_owner_action_starts_the_worker_and_only_then(make, tmp_path):
    m = make()
    assert not (tmp_path / "started").exists()
    res = await m.contacts()
    assert m.running and (tmp_path / "started").exists()
    assert res["contacts"][0]["id"] == 5


async def test_the_default_worker_command_is_isolated_python_with_the_package_module():
    from bcc.telegram_calls.call.manager import DEFAULT_ARGV
    assert list(DEFAULT_ARGV) == [sys.executable, "-I", "-m", "bcc.telegram_calls"]


# ------------------------------------------------------------------ STOP

async def test_stop_writes_the_durable_file_before_the_worker_hears_about_it(make, tmp_path):
    m = make()
    await m.contacts()
    out = await m.stop("owner")
    entry = [e for e in fake_ops(tmp_path) if e["op"] == "stop"][0]
    assert entry["stop_file_present"] is True, "the STOP file must exist before the fast stop op is sent"
    assert m.state.stop_is_set() and out["stop_flag"] is True and out["hangup_confirmed"] is True


async def test_stop_without_a_running_worker_still_persists_and_blocks_dialing(make):
    m = make()
    prep(m)
    out = await m.stop("owner")
    assert out["worker"] == "not_running" and m.state.stop_is_set() and not m.running
    with pytest.raises(CallError) as exc:
        await m.dial()
    assert exc.value.code == "STOP_ACTIVE"


async def test_stop_kills_a_worker_that_does_not_acknowledge(make):
    m = make("hang_on_stop", stop_ack_s=0.6)
    await m.contacts()
    proc = m._proc
    t0 = time.monotonic()
    out = await m.stop("owner")
    assert out["terminated"] is True and m.state.stop_is_set()
    assert proc.returncode is not None and not m.running
    assert time.monotonic() - t0 < 8


async def test_stop_with_an_acknowledgement_does_not_kill_the_worker(make):
    m = make(stop_ack_s=2.0)
    await m.contacts()
    out = await m.stop("owner")
    assert out["terminated"] is False and m.running


async def test_an_unconfirmed_hangup_with_the_call_still_active_terminates_the_worker(make):
    m = make("unconfirmed_stop", stop_ack_s=2.0)
    prep(m)
    await m.dial()
    assert await wait_until(lambda: m.active_call is not None and m.active_call["state"] == "active")
    out = await m.stop("owner")
    assert out["terminated"] is True and out["hangup_confirmed"] is False
    await m.drain()
    entry = m.history()[0]
    assert entry["outcome"] == Outcome.UNKNOWN.value and entry["synthesized"] is True


async def test_resume_clears_only_the_stop_flag(make):
    m = make()
    prep(m)
    await m.stop("owner")
    out = await m.resume()
    assert out["stop_flag"] is False and not m.state.stop_is_set()
    res = await m.dial()
    assert res["accepted"] is True


# ------------------------------------------------------------------ dial guard, evaluated in the manager

def _no_creds(m): m.save_settings(CallSettings(enabled=True, peer_user_id=222, peer_label='second account'))
def _disabled(m): prep(m, enabled=False)
def _no_session(m): prep(m); m.store.clear_session()
def _no_peer(m): prep(m, peer=0)
def _self_peer(m): prep(m, peer=111)
def _stop_file(m): prep(m); m.state.set_stop("owner")
def _uncertain(m): prep(m); m.state.note_call_finished("c-prev", Outcome.UNKNOWN)


@pytest.mark.parametrize("setup,code", [
    (_no_creds, "NO_CREDENTIALS"), (_disabled, "NOT_ENABLED"), (_no_session, "NOT_LOGGED_IN"), (_no_peer, "PEER_NOT_SELECTED"),
    (_self_peer, "PEER_IS_SELF"), (_stop_file, "STOP_ACTIVE"), (_uncertain, "UNCERTAIN_PREVIOUS_CALL")])
async def test_dial_is_refused_before_anything_reaches_the_worker(make, tmp_path, setup, code):
    m = make()
    setup(m)
    with pytest.raises(CallError) as exc:
        await m.dial()
    assert exc.value.code == code
    assert not m.running and not (tmp_path / "started").exists(), "a refused dial must not even start the worker"
    assert [e for e in fake_ops(tmp_path) if e["op"] == "dial"] == []


async def test_the_global_stop_blocks_dialing_and_lifting_it_allows_it(make, tmp_path):
    m = make()
    prep(m)
    with pytest.raises(CallError) as exc:
        await m.dial(global_stop=True)
    assert exc.value.code == "STOP_ACTIVE" and not m.running
    res = await m.dial(global_stop=False)
    assert res["accepted"] is True


async def test_a_corrupt_settings_file_is_never_treated_as_enabled(make, tmp_path):
    m = make()
    prep(m)
    (m.home / "config.json").write_text("{not json", encoding="utf-8")
    with pytest.raises(CallError) as exc:
        await m.dial()
    assert exc.value.code == "NOT_ENABLED" and not m.running


async def test_a_legitimate_dial_reaches_the_worker_without_any_peer_argument(make, tmp_path):
    m = make()
    prep(m)
    res = await m.dial()
    assert res["accepted"] and res["call_id"] == "c-fake000001" and res["transport"] == "loopback"
    dial_ops = [e for e in fake_ops(tmp_path) if e["op"] == "dial"]
    assert len(dial_ops) == 1
    assert dial_ops[0]["args_keys"] == ["confirm_unknown", "global_stop"], "the worker must never be told WHO to call"


async def test_a_second_dial_while_a_call_is_active_is_refused_locally(make, tmp_path):
    m = make()
    prep(m)
    await m.dial()
    with pytest.raises(CallError) as exc:
        await m.dial()
    assert exc.value.code == "CALL_IN_PROGRESS"
    assert len([e for e in fake_ops(tmp_path) if e["op"] == "dial"]) == 1


# ------------------------------------------------------------------ worker death: UNKNOWN, never a redial

async def test_worker_death_during_a_call_synthesizes_unknown_and_never_redials(make, tmp_path):
    seen: list[dict] = []
    m = make("die_after_dial", on_record=lambda rec: seen.append(rec))
    prep(m)
    await m.dial()
    assert await wait_until(lambda: not m.running and m.active_call is None)
    await m.drain()
    entry = m.history()[0]
    assert entry["outcome"] == "unknown" and entry["error_code"] == "WORKER_UNAVAILABLE" and entry["synthesized"] is True
    assert entry["call_id"] == "c-fake000001" and entry["recorded_audio"] is False and entry["summary"] is None
    assert m.state.is_uncertain(), "note_call_finished(id, UNKNOWN) must have been written"
    assert [r["call_id"] for r in seen] == ["c-fake000001"], "the post-call hook receives the synthesized record"
    dials = len([e for e in fake_ops(tmp_path) if e["op"] == "dial"])
    with pytest.raises(CallError) as exc:
        await m.dial()
    assert exc.value.code == "UNCERTAIN_PREVIOUS_CALL"
    assert len([e for e in fake_ops(tmp_path) if e["op"] == "dial"]) == dials and not m.running, "no automatic redial"
    again = await m.dial(confirm_unknown=True)                          # the owner's explicit confirmation is honoured
    assert again["accepted"] is True


async def test_a_worker_that_dies_before_answering_a_dial_is_settled_as_unknown(make, tmp_path):
    m = make("die_before_reply")
    prep(m)
    with pytest.raises(CallError) as exc:
        await m.dial()
    assert exc.value.code == "WORKER_UNAVAILABLE"
    await m.drain()
    entry = m.history()[0]
    assert entry["outcome"] == "unknown" and entry["call_id"] == "c-fake000001"
    assert m.state.is_uncertain() and m.active_call is None


async def test_a_worker_that_exits_without_a_call_leaves_no_record_and_no_uncertainty(make):
    m = make()
    await m.contacts()
    await m.shutdown()
    await m.drain()
    assert m.history() == [] and not m.state.is_uncertain() and not m.running


async def test_a_request_that_gets_no_answer_times_out_with_a_stable_code(make):
    m = make()
    await m.contacts()
    with pytest.raises(CallError) as exc:
        await m.request("slow", {}, timeout=0.4)
    assert exc.value.code == "WORKER_TIMEOUT"
    await m._terminate()                                                # the fake is asleep; do not wait for it


async def test_a_missing_worker_command_is_a_stable_error_not_a_crash(tmp_path, monkeypatch):
    monkeypatch.delenv("BOSSMAN_VAULT_KEY", raising=False)
    m = CallsManager(tmp_path / "d", vault=Vault(tmp_path / "d"), worker_argv=[str(tmp_path / "nonexistent-binary")])
    with pytest.raises(CallError) as exc:
        await m.contacts()
    assert exc.value.code == "WORKER_UNAVAILABLE" and not m.running


# ------------------------------------------------------------------ events and secrets

def test_scrub_event_keeps_scalars_and_drops_text_and_secret_keys():
    clean = scrub_event({"seq": 9, "kind": "turn", "at": 1.0, "turn": 3, "user_chars": 41, "text": "секретный разговор",
                         "transcript": "x", "prompt": "p", "phone": "+79001234567", "reason": "call +79001234567 now",
                         "nested": {"a": 1}, "ok": True, "none": None})
    assert clean == {"turn": 3, "user_chars": 41, "reason": "call [REDACTED] now", "ok": True, "none": None}


async def test_the_event_ring_is_bounded_ordered_and_carries_a_seq(make):
    m = make()
    for i in range(650):
        m._dispatch({"event": "call_event", "data": {"seq": i, "kind": "log", "msg": f"m{i}"}})
    got = m.events(0, limit=500)
    seqs = [e["seq"] for e in got["events"]]
    assert len(seqs) == 500 and seqs == sorted(seqs) and got["last_seq"] == 650
    assert m.events(640)["events"][0]["seq"] == 641
    assert m.events(650)["events"] == []
    assert all(set(e) == {"seq", "at", "kind", "msg"} for e in got["events"]), "the worker's own seq must not leak into ours"


async def test_call_events_are_scrubbed_on_the_way_in(make):
    m = make()
    m._dispatch({"event": "call_event", "data": {"seq": 1, "kind": "turn", "at": 1.0, "turn": 1, "text": "привет, вот мой пароль",
                                                 "transcript": "…"}})
    ev = m.events(0)["events"][0]
    assert ev["kind"] == "turn" and "text" not in ev and "transcript" not in ev and ev["turn"] == 1


async def test_login_secrets_never_reach_logs_events_results_or_status(make, caplog):
    caplog.set_level(logging.DEBUG)
    m = make()
    phone, code, twofa = "+79001234567", "48151", "correct horse battery"
    m.store.save_api(API_ID, API_HASH)
    replies = [await m.login_start(phone), await m.login_code(code)]
    with pytest.raises(CallError):
        await m.login_password(twofa)                                 # the fake worker does not implement it: an error, still no echo
    blob = json.dumps([replies, m.events(0), await m.status()], ensure_ascii=False, default=str) + caplog.text
    for secret in (phone, "9001234567", code, twofa, API_HASH):
        assert secret not in blob, f"a secret leaked: {secret[:3]}…"


async def test_a_worker_that_is_not_running_gets_no_status_request(make, tmp_path):
    m = make()
    await m.status()
    assert fake_ops(tmp_path) == []


# ------------------------------------------------------------------ status

async def test_status_merges_local_facts_flags_and_labels_the_test_mode(make):
    m = make()
    prep(m)
    m.state.note_call_finished("c-old", Outcome.CONNECTION_LOST)
    st = await m.status(global_stop=True)
    assert st["mode"] == "offline_test" and st["test_label"] is True and st["transport"] == "loopback"
    assert st["enabled"] is True and st["peer"] == {"user_id": 222, "label": "second account"}
    assert st["account"]["state"] == "ready" and st["account"]["has_session"] is True
    assert st["account"]["phone"] == "+••••0000" and "111" not in json.dumps(st["account"]), "no raw phone, no own user id"
    assert st["uncertain_previous"] is True and st["stop"] == {"call": False, "global": True, "active": True}
    assert st["last_error"] is None or isinstance(st["last_error"], dict)
    assert API_HASH not in json.dumps(st) and SESSION not in json.dumps(st)
    m.state.set_stop("owner")
    assert (await m.status())["stop"]["call"] is True


async def test_a_live_call_is_not_reported_as_an_uncertain_previous_call(make):
    """Found in the browser acceptance: state.json carries `in_flight` for the whole call, so the panel said «исход предыдущего звонка
    неизвестен» (and asked for the extra confirmation) in the middle of a perfectly normal call."""
    m = make()
    prep(m)
    m.state.note_call_started("c-live0000001")                   # what the worker writes BEFORE the phone rings
    assert (await m.status())["uncertain_previous"] is True, "no call known to this manager and in_flight left behind: a dead worker"
    m._active_call = {"call_id": "c-live0000001", "state": "active", "phase": None, "transport": "loopback", "models": {},
                      "started_at": time.time(), "latencies": []}
    live = await m.status()
    assert live["call_active"] is True and live["uncertain_previous"] is False, "the call in progress is not the PREVIOUS call"
    m._active_call = None
    m.state.note_call_finished("c-live0000001", Outcome.UNKNOWN)
    assert (await m.status())["uncertain_previous"] is True, "a real uncertain outcome still is (paired control)"
    m.state.note_call_finished("c-live0000002", Outcome.COMPLETED)
    assert (await m.status())["uncertain_previous"] is False


async def test_status_merges_the_worker_status_when_the_worker_runs(make, tmp_path):
    m = make()
    prep(m)
    await m.contacts()
    st = await m.status()
    assert st["worker"]["running"] is True and st["worker"]["version"] == "fake"
    assert [e["op"] for e in fake_ops(tmp_path)].count("status") == 1


def test_outcome_error_speaks_the_owner_language():
    assert outcome_error("declined", None)["code"] == "CALL_DECLINED"
    assert outcome_error("completed", None) is None
    assert outcome_error("unknown", None)["code"] == "UNCERTAIN_PREVIOUS_CALL"
    err = outcome_error("failed", "PEER_PRIVACY")
    assert err["message"] and err["hint"]


# ------------------------------------------------------------------ doctor

async def test_doctor_rows_have_the_documented_shape(make):
    m = make()
    rows = await m.doctor()
    assert rows and all(set(r) == {"check", "status", "detail", "remedy"} for r in rows)
    assert {r["status"] for r in rows} <= {"PASS", "WARN", "BLOCKED"}
    assert all(r["remedy"] for r in rows if r["status"] == "BLOCKED")
    checks = {r["check"] for r in rows}
    assert {"Зависимости", "Права доступа к файлам", "STOP", "Журнал процесса звонков", "Распознавание речи (Whisper)",
            "Голос (Piper)", "Локальная модель Jeff"} <= checks


def _voice_files(data: Path) -> None:
    voice = data / "voice"
    (voice / "piper").mkdir(parents=True)
    (voice / "piper" / "piper.exe").write_bytes(b"x")
    (voice / "ru_RU-denis-medium.onnx").write_bytes(b"x")
    (voice / "ru_RU-denis-medium.onnx.json").write_text("{}", encoding="utf-8")


async def test_doctor_voice_tract_is_checked_by_real_files_and_missing_is_a_warning_never_blocked(make, monkeypatch, tmp_path):
    from bcc.oss import whisper
    for name in ("BOSSMAN_PIT_TTS_EXECUTABLE", "BOSSMAN_PIT_TTS_MODEL_PATH", "BOSSMAN_WHISPER_MODEL_PATH"):
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setattr(whisper, "status", lambda: {"status": "unavailable", "reason": "Whisper model is incomplete"})
    rows = {r["check"]: r for r in await make().doctor()}
    assert rows["Голос (Piper)"]["status"] == "WARN" and "BOSSMAN_PIT_TTS_EXECUTABLE" in rows["Голос (Piper)"]["detail"]
    assert rows["Распознавание речи (Whisper)"]["status"] == "WARN" and rows["Распознавание речи (Whisper)"]["remedy"]
    # env VARIABLES alone are not enough any more: a path that does not exist is still a WARN (the old check said PASS)
    for name in ("BOSSMAN_PIT_TTS_EXECUTABLE", "BOSSMAN_PIT_TTS_MODEL_PATH"):
        monkeypatch.setenv(name, "/somewhere/absent")
    assert {r["check"]: r for r in await make().doctor()}["Голос (Piper)"]["status"] == "WARN"
    # the legitimate state: Jeff's voice in <data>/voice (the default Jeff's window uses) and a configured Whisper
    for name in ("BOSSMAN_PIT_TTS_EXECUTABLE", "BOSSMAN_PIT_TTS_MODEL_PATH"):
        monkeypatch.delenv(name)
    _voice_files(tmp_path / "data")
    monkeypatch.setattr(whisper, "status", lambda: {"status": "configured"})
    ok = {r["check"]: r for r in await make().doctor()}
    assert ok["Голос (Piper)"]["status"] == "PASS" and ok["Распознавание речи (Whisper)"]["status"] == "PASS"


async def test_doctor_tightens_loose_permissions_and_says_so(make):
    if os.name == "nt":
        return                                                          # POSIX mode bits; the Windows ACL parser is covered in test_hardening
    m = make()
    prep(m)
    path = m.home / "credentials.enc"
    os.chmod(path, 0o644)
    row = next(r for r in await m.doctor() if r["check"] == "Права доступа к файлам")
    assert row["status"] == "WARN" and "credentials.enc" in row["detail"]
    assert (os.stat(path).st_mode & 0o077) == 0, "the file must really be owner-only afterwards"


async def test_doctor_blocks_when_permissions_cannot_be_tightened(make, monkeypatch):
    if os.name == "nt":
        return
    m = make()
    prep(m)
    os.chmod(m.home / "credentials.enc", 0o644)
    monkeypatch.setattr(m, "heal_permissions", lambda: [])
    row = next(r for r in await m.doctor() if r["check"] == "Права доступа к файлам")
    assert row["status"] == "BLOCKED" and "credentials.enc" in row["detail"] and row["remedy"]


async def test_doctor_blocks_when_a_secret_value_is_in_the_worker_log(make):
    m = make()
    prep(m)
    log = m.home / "worker.log"
    log.write_text("2026 INFO worker: connected\n", encoding="utf-8")
    os.chmod(log, 0o600)
    clean = next(r for r in await m.doctor() if r["check"] == "Журнал процесса звонков")
    assert clean["status"] == "PASS"
    log.write_text(f"2026 ERROR worker: boom {API_HASH}\n", encoding="utf-8")
    dirty = next(r for r in await m.doctor() if r["check"] == "Журнал процесса звонков")
    assert dirty["status"] == "BLOCKED" and API_HASH not in json.dumps(dirty)


async def test_doctor_stop_row_reflects_both_stop_flags(make):
    m = make()
    assert next(r for r in await m.doctor() if r["check"] == "STOP")["status"] == "PASS"
    assert next(r for r in await m.doctor(global_stop=True) if r["check"] == "STOP")["status"] == "WARN"
    m.state.set_stop("owner")
    assert next(r for r in await m.doctor() if r["check"] == "STOP")["status"] == "WARN"


async def test_doctor_confirms_backups_do_not_carry_the_login_data(make):
    row = next(r for r in await make().doctor() if r["check"] == "Резервные копии")
    assert row["status"] == "PASS"


async def test_doctor_never_starts_the_worker_or_touches_the_network(make, tmp_path):
    m = make()
    await m.doctor()
    assert not m.running and not (tmp_path / "started").exists()
