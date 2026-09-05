import json
import secrets
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from core.key_vault.vault import SecurityKeyVault, PBKDF2_ITERATIONS


def test_vault_encryption_tamper_isolation_and_no_overwrite(tmp_path):
    password = secrets.token_urlsafe(24)
    a = SecurityKeyVault(str(tmp_path / "a.json"))
    b = SecurityKeyVault(str(tmp_path / "b.json"))
    public = a.create_and_store_pool(1, password)
    b.create_and_store_pool(1, password)
    assert PBKDF2_ITERATIONS >= 100000
    assert [str(k.pubkey()) for k in a.load_keypairs(password)] == public
    before = Path(a.storage_path).read_bytes()
    with pytest.raises(FileExistsError):
        a.create_and_store_pool(1, password)
    assert Path(a.storage_path).read_bytes() == before
    with pytest.raises(PermissionError):
        a.load_keypairs("wrong password")
    data = json.loads(before)
    other = json.loads(Path(b.storage_path).read_bytes())
    assert data["salt"] != other["salt"]
    assert data["nonce"] != other["nonce"]
    data["ciphertext"] = other["ciphertext"]
    Path(a.storage_path).write_text(json.dumps(data))
    with pytest.raises(PermissionError):
        a.load_keypairs(password)


@pytest.mark.parametrize("kwargs", [{"count": True}, {"count": 0}, {"count": 1001},
                                   {"password": "short"}, {"mode": "unknown"}, {"mode": "hd_bip44"}])
def test_invalid_vault_requests_leave_no_file(tmp_path, kwargs):
    path = tmp_path / "vault.json"
    args = dict(count=1, password=secrets.token_urlsafe(24))
    args.update(kwargs)
    with pytest.raises(ValueError):
        SecurityKeyVault(str(path)).create_and_store_pool(**args)
    assert not path.exists()


def test_public_views_require_authentication_and_ignore_tampered_metadata(tmp_path):
    password = secrets.token_urlsafe(24)
    vault = SecurityKeyVault(str(tmp_path / "vault.json"))
    public = vault.create_and_store_pool(1, password)
    for view in (vault.get_public_addresses, vault.get_sanitized_public_view):
        with pytest.raises(PermissionError):
            view()
        with pytest.raises(PermissionError):
            view("wrong password")

    payload = json.loads(Path(vault.storage_path).read_text())
    payload["metadata"]["public_addresses"] = ["attacker-controlled-address"]
    payload["metadata"]["wallet_count"] = 999
    Path(vault.storage_path).write_text(json.dumps(payload))

    for view in (vault.get_public_addresses, vault.get_sanitized_public_view):
        with pytest.raises(PermissionError):
            view()
    assert vault.get_public_addresses(password) == public
    sanitized = vault.get_sanitized_public_view(password)
    assert [item["pubkey"] for item in sanitized] == public
    assert all(set(item) == {"wallet_index", "alias", "pubkey", "role"} for item in sanitized)
