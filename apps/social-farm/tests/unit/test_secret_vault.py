"""Хранилище секретов: изоляция, отзыв, права на файлы.

Проверяется не «шифрование работает» — это свойство конструкции, — а те
свойства, ради которых хранилище вообще существует: чужую ссылку не
расшифровать, отозванный секрет не вернуть, ключ не лежит с правами по
умолчанию.
"""
from __future__ import annotations

import json
import os
import stat

import pytest

from social_farm.security.vault import (LocalEncryptedVault, SecretNotFound,
                                        SecretOwnershipError, SecretRevoked,
                                        SecretValue, VaultError, load_master_key)

TOKEN = "IGQVJXcanary000000000000000000000000000000000000TOKEN"


def test_stored_value_round_trips_for_its_owner(tmp_path):
    vault = LocalEncryptedVault(tmp_path, master_key=b"k" * 32)
    meta = vault.store(TOKEN, kind="oauth_access_token", owner_account_id="acct_a")
    assert vault.resolve_for_adapter(meta.ref, owner_account_id="acct_a").reveal() == TOKEN


def test_another_account_cannot_resolve_the_reference(tmp_path):
    """Инвариант A1: работа аккаунта A не достаёт секрет аккаунта B.

    Знание ссылки не должно давать доступа: ссылка живёт в аудите и в логах,
    и рано или поздно окажется там, где её увидит чужая работа.
    """
    vault = LocalEncryptedVault(tmp_path, master_key=b"k" * 32)
    meta = vault.store(TOKEN, kind="oauth_access_token", owner_account_id="acct_a")
    with pytest.raises(SecretOwnershipError):
        vault.resolve_for_adapter(meta.ref, owner_account_id="acct_b")


def test_revoke_erases_the_value_not_just_a_flag(tmp_path):
    """Помеченный, но сохранённый токен однажды достанут «на время»."""
    vault = LocalEncryptedVault(tmp_path, master_key=b"k" * 32)
    meta = vault.store(TOKEN, kind="oauth_access_token", owner_account_id="acct_a")
    vault.revoke(meta.ref)

    with pytest.raises(SecretRevoked):
        vault.resolve_for_adapter(meta.ref, owner_account_id="acct_a")
    assert vault.health(meta.ref).usable is False
    assert TOKEN not in (tmp_path / "secrets.json").read_text(encoding="utf-8")


def test_missing_reference_is_an_error_not_an_empty_token(tmp_path):
    vault = LocalEncryptedVault(tmp_path, master_key=b"k" * 32)
    with pytest.raises(SecretNotFound):
        vault.resolve_for_adapter("secret:nope", owner_account_id="acct_a")
    assert vault.health("secret:nope").present is False


def test_tampered_record_fails_closed(tmp_path):
    """Правка файла не должна давать «что получилось»."""
    vault = LocalEncryptedVault(tmp_path, master_key=b"k" * 32)
    meta = vault.store(TOKEN, kind="oauth_access_token", owner_account_id="acct_a")
    path = tmp_path / "secrets.json"
    raw = json.loads(path.read_text(encoding="utf-8"))
    raw["records"][meta.ref]["cipher" if "cipher" in raw["records"][meta.ref]
                             else "sealed"]["cipher"] = "AAAA"
    path.write_text(json.dumps(raw), encoding="utf-8")

    reopened = LocalEncryptedVault(tmp_path, master_key=b"k" * 32)
    with pytest.raises(VaultError):
        reopened.resolve_for_adapter(meta.ref, owner_account_id="acct_a")


def test_records_survive_reopening_the_vault(tmp_path):
    vault = LocalEncryptedVault(tmp_path, master_key=b"k" * 32)
    meta = vault.store(TOKEN, kind="oauth_access_token", owner_account_id="acct_a")
    again = LocalEncryptedVault(tmp_path, master_key=b"k" * 32)
    assert again.resolve_for_adapter(meta.ref, owner_account_id="acct_a").reveal() == TOKEN


def test_key_file_is_created_with_owner_only_permissions(tmp_path):
    key = load_master_key(tmp_path, env={})
    assert len(key) == 32
    mode = stat.S_IMODE(os.stat(tmp_path / "vault.key").st_mode)
    assert mode == 0o600, f"ключ создан с правами {oct(mode)}"
    # Повторный вызов берёт тот же ключ, а не создаёт второй.
    assert load_master_key(tmp_path, env={}) == key


def test_environment_key_wins_over_the_file(tmp_path):
    from base64 import b64encode

    supplied = b"e" * 32
    key = load_master_key(tmp_path, env={"SF_VAULT_KEY": b64encode(supplied).decode()})
    assert key == supplied
    assert not (tmp_path / "vault.key").exists()


def test_short_environment_key_is_refused(tmp_path):
    with pytest.raises(VaultError):
        load_master_key(tmp_path, env={"SF_VAULT_KEY": "short"})


def test_secret_value_hides_itself_in_every_default_rendering():
    """Секрет протекает через отладочный вывод, а не через злой умысел."""
    value = SecretValue(TOKEN)
    assert TOKEN not in repr(value)
    assert TOKEN not in str(value)
    assert TOKEN not in f"{value}"
    assert TOKEN not in f"{value!r}"
    assert TOKEN not in "%s" % (value,)
    assert value.reveal() == TOKEN
    with pytest.raises(VaultError):
        value.__getstate__()
