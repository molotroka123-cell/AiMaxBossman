"""Security Hardening V1.1 (H6): источник ключа Vault — env/секрет-стор + файл."""
import os
from pathlib import Path

from cryptography.fernet import Fernet

from bcc.secrets import KEY_ENV, Vault


def test_env_key_is_used_and_not_persisted(tmp_path, monkeypatch):
    key = Fernet.generate_key().decode()
    monkeypatch.setenv(KEY_ENV, key)
    v = Vault(tmp_path)
    blob = v.encrypt("super-token")
    assert v.decrypt(blob) == "super-token"
    # ключ из env НЕ пишется в файл
    assert not (tmp_path / "secret.key").exists()


def test_file_key_used_when_no_env(tmp_path, monkeypatch):
    monkeypatch.delenv(KEY_ENV, raising=False)
    v = Vault(tmp_path)
    assert (tmp_path / "secret.key").exists()
    blob = v.encrypt("x")
    assert v.decrypt(blob) == "x"


def test_wrong_key_decrypt_is_logged_not_crash(tmp_path, monkeypatch, caplog):
    monkeypatch.delenv(KEY_ENV, raising=False)
    v1 = Vault(tmp_path / "a")
    blob = v1.encrypt("secret")
    v2 = Vault(tmp_path / "b")            # другой ключ
    assert v2.decrypt(blob) is None
    assert any("cannot decrypt" in r.message for r in caplog.records)


def test_rotation_reencrypts(tmp_path, monkeypatch):
    k1 = Fernet.generate_key().decode()
    monkeypatch.setenv(KEY_ENV, k1)
    v1 = Vault(tmp_path)
    blob = v1.encrypt("rotate-me")
    plain = v1.decrypt(blob)
    # новый ключ → перешифровать
    k2 = Fernet.generate_key().decode()
    monkeypatch.setenv(KEY_ENV, k2)
    v2 = Vault(tmp_path)
    assert v2.decrypt(blob) is None       # старый шифротекст новым ключом не читается
    reblob = v2.encrypt(plain)
    assert v2.decrypt(reblob) == "rotate-me"
