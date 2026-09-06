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
    if os.name != "posix":
        pytest.skip("POSIX-семантика режима файла 0o600; на Windows права задаёт icacls (см. W8)")
    monkeypatch.setenv(ev.ENV_KEY_FILE, str(tmp_path / "k" / "evidence.key"))
    ev.reset_cache()
    key = ev.load_or_create_key()
    assert len(key) == 32 and stat.S_IMODE(os.stat(tmp_path / "k" / "evidence.key").st_mode) == 0o600
    ev.reset_cache()


# ------------------------------------------------- W8: права ключа на Windows

def _spy_icacls(monkeypatch, result=0, boom=None):
    """Перехватить вызов icacls и вернуть заданный исход."""
    import subprocess

    calls: list[list[str]] = []

    class _Proc:
        returncode = result
        stdout = ""
        stderr = "" if result == 0 else "Отказано в доступе"

    def fake_run(argv, **kw):
        calls.append(list(argv))
        if boom is not None:
            raise boom
        return _Proc()

    monkeypatch.setattr(subprocess, "run", fake_run)
    return calls


def _as_windows(mp):
    """os.name='nt' — только на время вызова: от него зависит класс pathlib."""
    import os

    mp.setattr(os, "name", "nt")


def test_restrict_to_owner_sets_owner_only_dacl_on_windows(tmp_path, monkeypatch):
    """os.chmod(0o600) на Windows переключает лишь «только чтение» и НЕ ставит ACL.

    Ключ подписи улик остаётся читаемым для любого процесса сессии владельца,
    то есть verified=True может подделать что угодно, запущенное под тем же
    логином. Нужен явный owner-only DACL. На Linux доказуемо ровно одно: какой
    вызов делается; что icacls действительно сузит DACL — только на Windows.
    """
    key = tmp_path / "evidence.key"
    key.write_bytes(b"\x00" * 32)
    with pytest.MonkeyPatch.context() as mp:
        calls = _spy_icacls(mp)
        _as_windows(mp)
        mp.setenv("USERNAME", "boss")
        mp.delenv("USERDOMAIN", raising=False)
        ev.restrict_to_owner(key)

    assert calls, "DACL на Windows не выставляется вовсе"
    argv = calls[0]
    assert argv[0] == "icacls" and argv[1] == str(key)
    assert "/inheritance:r" in argv          # наследуемые разрешения снимаются
    assert argv[argv.index("/grant:r") + 1] == "boss:F"


def test_restrict_to_owner_uses_domain_principal(tmp_path):
    key = tmp_path / "evidence.key"
    with pytest.MonkeyPatch.context() as mp:
        calls = _spy_icacls(mp)
        _as_windows(mp)
        mp.setenv("USERDOMAIN", "HOME-PC")
        mp.setenv("USERNAME", "boss")
        ev.restrict_to_owner(key)
    assert calls[0][-1] == "HOME-PC\\boss:F"


@pytest.mark.parametrize("kw", [{"result": 1}, {"boom": FileNotFoundError("icacls")}])
def test_restrict_to_owner_failure_warns_but_never_raises(tmp_path, kw):
    """Не смогли сузить DACL — старт продолжается, но об этом СЛЫШНО."""
    key = tmp_path / "evidence.key"
    with pytest.MonkeyPatch.context() as mp:
        _spy_icacls(mp, **kw)
        _as_windows(mp)
        mp.setenv("USERNAME", "boss")
        with pytest.warns(UserWarning, match="icacls"):
            ev.restrict_to_owner(key)   # именно возврат, а не исключение


def test_restrict_to_owner_is_noop_on_posix(tmp_path, monkeypatch):
    """POSIX-реализация не вызывает: сужение прав выполняется средствами ОС."""
    import os
    if os.name != "posix":
        pytest.skip("на Windows сужение прав честно выполняется через icacls (см. W8)")
    calls = _spy_icacls(monkeypatch)
    ev.restrict_to_owner(tmp_path / "evidence.key")
    assert calls == []


def test_key_creation_applies_owner_restriction(tmp_path, monkeypatch):
    """Ключ, созданный при первом обращении, проходит через сужение прав."""
    import os
    import stat

    seen: list[str] = []
    monkeypatch.setenv(ev.ENV_KEY_FILE, str(tmp_path / "k" / "evidence.key"))
    monkeypatch.setattr(ev, "restrict_to_owner", lambda p: seen.append(str(p)))
    ev.reset_cache()
    key = ev.load_or_create_key()
    ev.reset_cache()

    assert len(key) == 32
    assert seen == [str(tmp_path / "k" / "evidence.key")]
    # POSIX-права не тронуты (проверка POSIX-семантики режима файла)
    if os.name == "posix":
        assert stat.S_IMODE(os.stat(tmp_path / "k" / "evidence.key").st_mode) == 0o600
