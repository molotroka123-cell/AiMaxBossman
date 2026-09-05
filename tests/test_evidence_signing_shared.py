"""bossman_shared.evidence (EH-01): каноничность, подпись, проверка, ключ 0600."""
from __future__ import annotations

import pytest

from bossman_shared import evidence as ev

KEY = b"\x01" * 32


def test_canonical_is_order_independent_and_excludes_sig():
    a = ev.canonical({"b": 1, "a": [1, 2], "sig": "x"})
    b = ev.canonical({"a": [1, 2], "b": 1})
    assert a == b == b'{"a":[1,2],"b":1}'


def test_sign_verify_and_tamper():
    body = {"kind": "file", "ref": "/x", "verified": True}
    sig = ev.sign(body, key=KEY)
    assert ev.verify(body, sig, key=KEY)
    assert not ev.verify({**body, "ref": "/y"}, sig, key=KEY)
    assert not ev.verify(body, sig, key=b"\x02" * 32)
    assert not ev.verify(body, "", key=KEY)


def test_sign_fields_requires_trusted_signer_and_verifies_as_record():
    body = {"kind": "file"}
    f = ev.sign_fields(body, signer="bossman_v3.verifier", key=KEY)
    assert set(f) == {"sig", "signer", "nonce", "issued_at"}
    assert ev.verify_signed({**body, **f}, key=KEY)
    assert not ev.verify_signed({**body, **f, "signer": "model:x"}, key=KEY)
    with pytest.raises(ValueError):
        ev.sign_fields(body, signer="model:x", key=KEY)


def test_key_created_in_env_path_with_0600(tmp_path, monkeypatch):
    import os, stat
    monkeypatch.setenv(ev.ENV_KEY_FILE, str(tmp_path / "k" / "evidence.key"))
    ev.reset_cache()
    key = ev.load_or_create_key()
    assert len(key) == 32 and stat.S_IMODE(os.stat(tmp_path / "k" / "evidence.key").st_mode) == 0o600
    ev.reset_cache()


def test_binary_key_and_signature_survive_restart(tmp_path, monkeypatch):
    # Force LF/CRLF/control bytes: Windows text descriptors must not translate
    # random key material. Random tests only hit this failure intermittently.
    expected = b"\n\r\n\x1a" + b"x" * 28
    target = tmp_path / "binary.key"
    monkeypatch.setenv(ev.ENV_KEY_FILE, str(target))
    monkeypatch.setattr(ev.secrets, "token_bytes", lambda count: expected)
    ev.reset_cache()
    try:
        first = ev.load_or_create_key()
        payload = {"kind": "controlled-file", "verified": True}
        signature = ev.sign(payload)
        assert first == expected
        assert target.read_bytes() == expected
        ev.reset_cache()
        assert ev.load_or_create_key() == expected
        assert ev.verify(payload, signature)
    finally:
        ev.reset_cache()
