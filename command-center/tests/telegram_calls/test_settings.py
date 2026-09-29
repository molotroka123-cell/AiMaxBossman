"""Settings: off by default, one peer, atomic persistence in the data dir, fail-closed on tampering, no secrets."""
from __future__ import annotations

import json

import pytest

from bcc.telegram_calls.settings import CallsSettings, SettingsError, SettingsStore
from bcc.telegram_calls.types import PeerRef


def test_defaults_are_off_and_empty(tmp_path):
    s = SettingsStore(tmp_path).load()
    assert s.enabled is False and s.peer is None and s.peer_confirmed is False
    assert s.record_audio is False and s.keep_transcript is False and s.last_outcome is None


def test_update_persists_atomically_and_leaves_no_temp_files(tmp_path):
    st = SettingsStore(tmp_path)
    st.update({"enabled": True, "max_call_s": 300})
    again = SettingsStore(tmp_path).load()
    assert again.enabled is True and again.max_call_s == 300.0
    assert st.path.parent == tmp_path / "telegram_calls"
    assert [p.name for p in st.path.parent.iterdir()] == ["settings.json"]


@pytest.mark.parametrize("patch", [{"enabled": "yes"}, {"max_call_s": 5}, {"max_call_s": True},
                                   {"ring_timeout_s": 9999}, {"api_hash": "x"}, {"last_outcome": "completed"},
                                   {"allowed_peers": []}])
def test_bad_patch_rejected_and_nothing_written(tmp_path, patch):
    st = SettingsStore(tmp_path)
    with pytest.raises(SettingsError):
        st.update(patch)
    assert not st.path.exists()


def test_peer_selection_needs_confirmation_and_resets_on_change(tmp_path):
    st = SettingsStore(tmp_path)
    s = st.set_peer(4242, "Второй")
    assert s.peer == PeerRef(4242, "Второй") and s.peer_confirmed is False
    assert st.confirm_peer(4242).peer_confirmed is True
    s2 = st.set_peer(5151, "Другой")            # a different peer replaces the first one, unconfirmed again
    assert len(s2.allowed_peers) == 1 and s2.peer.user_id == 5151 and s2.peer_confirmed is False


def test_confirm_of_a_different_peer_is_rejected(tmp_path):
    st = SettingsStore(tmp_path)
    st.set_peer(4242)
    with pytest.raises(SettingsError):
        st.confirm_peer(9999)
    assert st.load().peer_confirmed is False
    with pytest.raises(SettingsError):
        st.set_peer(-5)


def test_more_than_max_allowed_peers_in_file_is_treated_as_none(tmp_path):
    st = SettingsStore(tmp_path)
    st.set_peer(4242)
    st.confirm_peer(4242)
    raw = json.loads(st.path.read_text(encoding="utf-8"))
    raw["allowed_peers"].append({"user_id": 777, "label": "extra"})
    st.path.write_text(json.dumps(raw), encoding="utf-8")
    s = st.load()
    assert s.peer is None and s.peer_confirmed is False          # fail closed, not "the first one"


def test_single_peer_in_file_is_accepted(tmp_path):
    st = SettingsStore(tmp_path)
    st.set_peer(4242)
    st.confirm_peer(4242)
    assert st.load().peer.user_id == 4242 and st.load().peer_confirmed is True


def test_corrupt_file_fails_closed(tmp_path):
    st = SettingsStore(tmp_path)
    st.path.parent.mkdir(parents=True)
    st.path.write_text("{not json", encoding="utf-8")
    s = st.load()
    assert s == CallsSettings()
    st.path.write_text(json.dumps({"enabled": "true", "peer_confirmed": True, "max_call_s": 10 ** 9}), encoding="utf-8")
    s = st.load()
    assert s.enabled is False and s.peer_confirmed is False and s.max_call_s == 900.0


def test_outcome_recording_and_validation(tmp_path):
    st = SettingsStore(tmp_path)
    st.record_outcome("unknown", "c1")
    s = st.load()
    assert s.last_outcome == "unknown" and s.last_call_id == "c1" and s.last_call_at
    with pytest.raises(SettingsError):
        st.record_outcome("weird")


def test_file_never_contains_secret_like_fields(tmp_path):
    st = SettingsStore(tmp_path)
    st.update({"enabled": True})
    st.set_peer(4242, "x")
    text = st.path.read_text(encoding="utf-8").lower()
    for word in ("api_hash", "api_id", "session", "password", "phone", "token"):
        assert word not in text
