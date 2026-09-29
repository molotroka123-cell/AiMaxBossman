"""Credentials are encrypted in the real Vault, restricted to the owner and never returned as values."""
from __future__ import annotations

import pytest

from bcc.secrets import Vault
from bcc.telegram_calls.account.credentials import CredentialStore
from bcc.telegram_calls.types import CallError

from .fakes_a import API_HASH


def store(tmp_path):
    return CredentialStore(tmp_path, Vault(tmp_path))


def test_empty_store_status_is_false_booleans(tmp_path):
    assert store(tmp_path).status() == {"has_credentials": False, "has_session": False}


def test_roundtrip_and_status_is_booleans_only(tmp_path):
    s = store(tmp_path)
    s.save_api(123456, API_HASH)
    st = s.status()
    assert st == {"has_credentials": True, "has_session": False}
    s.save_session("SESSION-STRING-XYZ", 111)
    assert s.status() == {"has_credentials": True, "has_session": True}
    got = s.get()
    assert (got.api_id, got.api_hash, got.session, got.self_id) == (123456, API_HASH, "SESSION-STRING-XYZ", 111)
    assert s.self_id() == 111


def test_nothing_readable_on_disk_and_repr_hides_values(tmp_path):
    s = store(tmp_path)
    s.save_api(123456, API_HASH)
    s.save_session("SESSION-STRING-XYZ", 111)
    raw = s.path.read_bytes()
    for needle in (API_HASH.encode(), b"SESSION-STRING-XYZ", b"123456"):
        assert needle not in raw
    text = repr(s.get())
    assert API_HASH not in text and "SESSION-STRING" not in text and "123456" not in text
    assert not list(s.path.parent.glob("*.tmp"))


def test_uses_the_existing_vault_key_file(tmp_path):
    s = store(tmp_path)
    s.save_api(123456, API_HASH)
    assert (tmp_path / "secret.key").is_file()
    # a fresh instance with its own Vault on the same dir reads it back (same key file)
    assert CredentialStore(tmp_path).get().api_hash == API_HASH


def test_restrict_to_owner_is_applied_to_the_file(tmp_path, monkeypatch):
    seen = []
    monkeypatch.setattr("bcc.auth._restrict_to_owner", lambda p: seen.append(p))
    s = store(tmp_path)
    s.save_api(123456, API_HASH)
    assert seen and seen[0].parent == s.path.parent


@pytest.mark.parametrize("api_id,api_hash", [(0, API_HASH), (-3, API_HASH), ("123", API_HASH), (True, API_HASH),
                                             (123456, "short"), (123456, "z" * 32), (123456, None)])
def test_invalid_api_pair_rejected_without_echoing_it(tmp_path, api_id, api_hash):
    s = store(tmp_path)
    with pytest.raises(CallError) as ei:
        s.save_api(api_id, api_hash)
    assert ei.value.code == "NO_CREDENTIALS"
    assert str(api_hash) not in repr(ei.value) or api_hash in (None, True)
    assert not s.path.exists()


def test_session_requires_api_and_changed_api_drops_session(tmp_path):
    s = store(tmp_path)
    with pytest.raises(CallError) as ei:
        s.save_session("S", 5)
    assert ei.value.code == "NO_CREDENTIALS"
    s.save_api(123456, API_HASH)
    s.save_session("S", 5)
    s.save_api(123456, API_HASH)                     # same pair: session kept
    assert s.has_session()
    s.save_api(999999, API_HASH)                     # different pair: old session is not valid any more
    assert not s.has_session() and s.has_api()


def test_tampered_or_foreign_key_blob_reads_as_absent(tmp_path):
    s = store(tmp_path)
    s.save_api(123456, API_HASH)
    s.path.write_text("gAAAAAnot-a-token", encoding="utf-8")
    assert s.status() == {"has_credentials": False, "has_session": False}
    with pytest.raises(CallError):
        s.get()


def test_clear_session_and_clear_all(tmp_path):
    s = store(tmp_path)
    s.save_api(123456, API_HASH)
    s.save_session("S", 5)
    s.clear_session()
    assert s.has_api() and not s.has_session() and s.self_id() is None
    s.clear_all()
    assert not s.path.exists() and s.status()["has_credentials"] is False
