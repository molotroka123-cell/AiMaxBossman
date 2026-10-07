"""Settings, encrypted credentials, STOP/uncertainty state and the dial guard.

Negative controls: nothing secret on disk in the clear, nothing secret in repr/public(), calls off by default,
every guard rule refuses on its own and the legitimate path still passes.
"""
from __future__ import annotations

import json
import os
import stat

import pytest

from bcc.secrets import Vault
from bcc.telegram_calls.account.credentials import CredentialStore, Credentials, mask_phone
from bcc.telegram_calls.account.guard import DialContext, check_dial, check_peer_candidate
from bcc.telegram_calls.account.stopflag import CallState
from bcc.telegram_calls.settings import CallSettings, load_settings, save_settings
from bcc.telegram_calls.types import AccountState, CallError, Outcome

API_ID = 1234567
API_HASH = "0123456789abcdef" * 2                       # fixture shape only (32 hex), not a credential
SESSION = "1A" + "Qz9_x-" * 20                          # session-string-like fixture, built at runtime


@pytest.fixture
def home(tmp_path, monkeypatch):
    monkeypatch.delenv("BOSSMAN_VAULT_KEY", raising=False)
    return tmp_path / "calls"


def store_for(home, tmp_path):
    return CredentialStore(home, vault=Vault(tmp_path))


def test_credentials_roundtrip_and_nothing_secret_is_on_disk_in_the_clear(home, tmp_path):
    s = store_for(home, tmp_path)
    s.save_api(API_ID, API_HASH)
    s.save_session(SESSION, 777, "+7 900 123-45-67")
    c = s.load()
    assert (c.api_id, c.api_hash, c.session, c.me_id, c.phone_last4) == (API_ID, API_HASH, SESSION, 777, "4567")
    raw = b"".join(p.read_bytes() for p in home.iterdir() if p.is_file())
    for secret in (API_HASH, SESSION, "900 123", "9001234567"):
        assert secret.encode() not in raw
    assert not any(p.name.endswith(".tmp") for p in home.iterdir())
    if os.name != "nt":
        assert stat.S_IMODE((home / "credentials.enc").stat().st_mode) == 0o600


def test_public_view_and_repr_expose_only_masks(home, tmp_path):
    s = store_for(home, tmp_path)
    s.save_api(API_ID, API_HASH)
    s.save_session(SESSION, 777, "+79001234567")
    view = json.dumps(s.public())
    assert API_HASH not in view and SESSION not in view and "79001234567" not in view
    assert s.public()["phone"] == "+••••4567" and s.public()["has_session"] is True
    c = s.load()
    assert API_HASH not in repr(c) and SESSION not in repr(c) and API_HASH not in str(c)
    assert mask_phone("+7 (900) 123-45-67") == "+••••4567" and mask_phone("12") == ""


def test_saved_login_phone_is_encrypted_masked_and_validated(home, tmp_path):
    """eefc3705 (wt-calls-investor): the full login phone persists in credentials.enc, never in the clear."""
    s = store_for(home, tmp_path)
    assert s.saved_phone() == ""
    s.save_phone("+7 (900) 123-45-67")
    assert s.saved_phone() == "+79001234567" and s.load().phone_last4 == "4567"
    raw = b"".join(p.read_bytes() for p in home.iterdir() if p.is_file())
    assert b"79001234567" not in raw and b"900 123" not in raw
    view = json.dumps(s.public())
    assert "79001234567" not in view and s.public()["phone"] == "+••••4567"
    assert "79001234567" not in repr(s.load())
    with pytest.raises(CallError):
        s.save_phone("12")
    assert s.saved_phone() == "+79001234567"


@pytest.mark.parametrize("api_id,api_hash", [(0, API_HASH), (-5, API_HASH), (API_ID, "short"), (API_ID, "z" * 32), ("1", API_HASH)])
def test_bad_api_credentials_are_refused_without_echoing_them(home, tmp_path, api_id, api_hash):
    with pytest.raises(CallError) as ei:
        store_for(home, tmp_path).save_api(api_id, api_hash)
    assert ei.value.code == "NO_CREDENTIALS" and str(api_hash) not in json.dumps(ei.value.as_dict())


def test_changing_api_id_drops_the_old_session_but_same_api_keeps_it(home, tmp_path):
    s = store_for(home, tmp_path)
    s.save_api(API_ID, API_HASH)
    s.save_session(SESSION, 5, "+79000000001")
    s.save_api(API_ID, API_HASH)
    assert s.load().session == SESSION                     # negative control: identical credentials keep the login
    s.save_api(API_ID + 1, API_HASH)
    assert s.load().session == "" and s.public()["has_session"] is False


def test_logout_keeps_api_and_clear_all_removes_everything(home, tmp_path):
    s = store_for(home, tmp_path)
    s.save_api(API_ID, API_HASH)
    s.save_session(SESSION, 5, "+79000000001")
    s.clear_session()
    assert s.load().api_id == API_ID and s.load().session == ""
    s.clear_all()
    assert s.load() == Credentials() and not (home / "credentials.enc").exists()


def test_undecryptable_credentials_do_not_crash_status_and_are_never_treated_as_logged_in(home, tmp_path):
    s = store_for(home, tmp_path)
    s.save_api(API_ID, API_HASH)
    other = CredentialStore(home, vault=Vault(tmp_path / "other-key-dir"))
    assert other.public()["unreadable"] is True
    with pytest.raises(CallError) as ei:
        other.load()
    assert ei.value.code == "NOT_LOGGED_IN"


def test_settings_default_to_calls_off_and_persist_without_secrets(home):
    d = load_settings(home)
    assert d.enabled is False and d.peer is None and d.record_audio is False and d.barge_in is True
    assert d.auto_save_to_bossman_memory is False                       # nothing reaches the owner's memory/tasks without a click
    save_settings(CallSettings(enabled=True, peer_user_id=42, peer_label="Второй"), home)
    text = (home / "config.json").read_text(encoding="utf-8")
    assert load_settings(home).peer.user_id == 42
    for forbidden in ("api_hash", "session", "phone", "token"):
        assert forbidden not in text.lower().replace("peer_user_id", "")


@pytest.mark.parametrize("kw", [dict(max_call_s=5), dict(ring_timeout_s=500), dict(echo_mode="loud"), dict(vad="cloud"),
                                dict(idle_prompt_s=50, idle_hangup_s=40), dict(peer_user_id=-1), dict(enabled="yes"),
                                dict(record_audio=1), dict(auto_save_to_bossman_memory="no"), dict(stt_model_path="a\x00b")])
def test_invalid_settings_are_rejected(kw):
    with pytest.raises(ValueError):
        CallSettings(**kw)


def test_corrupt_config_is_an_error_not_a_silent_default_of_enabled(home):
    home.mkdir(parents=True)
    (home / "config.json").write_text("{not json", encoding="utf-8")
    with pytest.raises(ValueError):
        load_settings(home)


def test_unknown_config_keys_are_ignored_not_executed(home):
    home.mkdir(parents=True)
    (home / "config.json").write_text(json.dumps({"enabled": True, "peer_user_id": 9, "shell": "rm -rf /"}), encoding="utf-8")
    assert load_settings(home).enabled is True


# ---------------------------------------------------------------- state / stop / history

def test_stop_flag_survives_restart_and_only_clear_removes_it(home):
    CallState(home).set_stop("global")
    assert CallState(home).stop_is_set()
    CallState(home).clear_stop()
    assert not CallState(home).stop_is_set()


def test_interrupted_call_leaves_the_next_dial_uncertain(home):
    st = CallState(home)
    assert st.is_uncertain() is False
    st.note_call_started("c1")                              # process dies here: no note_call_finished
    assert CallState(home).is_uncertain() is True
    CallState(home).acknowledge_uncertain()
    assert CallState(home).is_uncertain() is False


@pytest.mark.parametrize("outcome,uncertain", [(Outcome.COMPLETED, False), (Outcome.DECLINED, False), (Outcome.BUSY, False),
                                               (Outcome.NO_ANSWER, False), (Outcome.STOPPED, False),
                                               (Outcome.UNKNOWN, True), (Outcome.CONNECTION_LOST, True), (None, True)])
def test_only_unprovable_outcomes_make_the_next_dial_uncertain(home, outcome, uncertain):
    st = CallState(home)
    st.note_call_started("c")
    st.note_call_finished("c", outcome)
    assert st.is_uncertain() is uncertain


def test_history_is_bounded_newest_first_and_tolerates_garbage_lines(home):
    st = CallState(home)
    for i in range(230):
        st.append_history({"call_id": f"c{i}"})
    (home / "history.jsonl").write_text((home / "history.jsonl").read_text() + "garbage\n", encoding="utf-8")
    h = st.history(5)
    assert h[0]["call_id"] == "c229" and len(h) == 4          # 5 lines read, the garbage one skipped
    assert len((home / "history.jsonl").read_text().splitlines()) <= 201


# ---------------------------------------------------------------- guard

def ready(**kw):
    base = dict(account=AccountState.READY, me_id=1, active_call=False, stop_active=False, uncertain_previous=False)
    base.update(kw)
    return DialContext(**base)


def cfg(**kw):
    base = dict(enabled=True, peer_user_id=2, peer_label="Второй")
    base.update(kw)
    return CallSettings(**base)


def test_legitimate_dial_passes_and_returns_only_the_configured_peer():
    peer = check_dial(cfg(), ready())
    assert peer.user_id == 2


@pytest.mark.parametrize("settings_kw,ctx_kw,code", [
    (dict(enabled=False), {}, "NOT_ENABLED"),
    ({}, dict(account=AccountState.NO_CREDENTIALS), "NO_CREDENTIALS"),
    ({}, dict(account=AccountState.LOGGED_OUT), "NOT_LOGGED_IN"),
    ({}, dict(account=AccountState.CODE_SENT), "NOT_LOGGED_IN"),
    (dict(peer_user_id=None), {}, "PEER_NOT_SELECTED"),
    (dict(peer_user_id=1), {}, "PEER_IS_SELF"),
    ({}, dict(stop_active=True), "STOP_ACTIVE"),
    ({}, dict(active_call=True), "CALL_IN_PROGRESS"),
    ({}, dict(uncertain_previous=True), "UNCERTAIN_PREVIOUS_CALL"),
    ({}, dict(deps_ok=False), "DEPENDENCIES_MISSING"),
])
def test_every_guard_rule_refuses_on_its_own(settings_kw, ctx_kw, code):
    with pytest.raises(CallError) as ei:
        check_dial(cfg(**settings_kw), ready(**ctx_kw))
    assert ei.value.code == code


def test_uncertain_previous_call_needs_an_explicit_confirmation_and_only_that():
    assert check_dial(cfg(), ready(uncertain_previous=True), confirm_unknown=True).user_id == 2
    with pytest.raises(CallError):                                    # the confirmation does not bypass STOP
        check_dial(cfg(), ready(uncertain_previous=True, stop_active=True), confirm_unknown=True)


def test_dial_signature_has_no_peer_parameter():
    import inspect
    assert "peer" not in inspect.signature(check_dial).parameters and "user_id" not in inspect.signature(check_dial).parameters


@pytest.mark.parametrize("user,code", [
    ({"id": 1, "label": "я"}, "PEER_IS_SELF"), ({"id": 5, "bot": True}, "PEER_INVALID"), ({"id": 5, "deleted": True}, "PEER_INVALID"),
    ({"id": 0}, "PEER_INVALID"), ({"id": "5"}, "PEER_INVALID"), ({"id": 5, "is_user": False}, "PEER_INVALID")])
def test_peer_candidates_that_are_not_a_real_other_person_are_refused(user, code):
    with pytest.raises(CallError) as ei:
        check_peer_candidate(user, me_id=1)
    assert ei.value.code == code


def test_peer_candidate_label_is_normalised():
    assert check_peer_candidate({"id": 5, "label": "  Вторая \n  учётка "}, me_id=1).label == "Вторая учётка"


def test_the_stop_flag_is_replaced_when_the_old_file_cannot_be_written(tmp_path, monkeypatch):
    """An older build left files with an empty DACL (unreadable, unwritable): STOP must still become durable."""
    from pathlib import Path
    from bcc.telegram_calls.account.stopflag import CallState
    state = CallState(tmp_path / "telegram-calls")
    state.set_stop("first")
    real = Path.write_text
    denied = {"n": 0}

    def write_text(self, *a, **kw):
        if self == state.stop_path and denied["n"] == 0:
            denied["n"] += 1
            raise PermissionError(13, "Permission denied")
        return real(self, *a, **kw)
    monkeypatch.setattr(Path, "write_text", write_text)
    state.set_stop("second")
    assert denied["n"] == 1 and state.stop_is_set() and "second" in state.stop_path.read_text(encoding="utf-8")
