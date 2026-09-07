"""W8: файл токена доступа Command Center читался всей сессией пользователя.

`os.open(..., 0o600)` на Windows не создаёт ACL: режим переключает ровно один
атрибут — «только чтение». Токен из data dir (`bind 127.0.0.1` + обязательный
X-BCC-Token — это ВЕСЬ контроль доступа к Command Center) мог прочитать любой
процесс, запущенный под тем же логином. Нужен явный owner-only DACL.

Windows симулируется подменой `os.name` и `subprocess.run`. Что остаётся
недоказанным на Linux: что icacls реально сузит DACL и что после этого файл
недоступен другому процессу — это проверяется только на настоящей Windows.
`os.name` подменяется точечно, вокруг одного вызова: от него зависит выбор
класса в pathlib, и глобальная подмена ломает Path целиком.
"""
from __future__ import annotations

import os
import subprocess

import pytest

from bcc import auth


def _spy_icacls(mp, result: int = 0, boom: BaseException | None = None):
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

    mp.setattr(subprocess, "run", fake_run)
    return calls


def test_token_file_gets_owner_only_dacl_on_windows(tmp_path):
    token_file = tmp_path / "token"
    token_file.write_text("secret", encoding="utf-8")
    with pytest.MonkeyPatch.context() as mp:
        calls = _spy_icacls(mp)
        mp.setattr(os, "name", "nt")
        mp.setenv("USERNAME", "boss")
        mp.delenv("USERDOMAIN", raising=False)
        auth._restrict_to_owner(token_file)

    assert calls, "DACL токена на Windows не выставляется вовсе"
    argv = calls[0]
    assert argv[0] == "icacls" and argv[1] == str(token_file)
    assert "/inheritance:r" in argv
    assert argv[argv.index("/grant:r") + 1] == "boss:F"


@pytest.mark.parametrize("kw", [{"result": 1}, {"boom": FileNotFoundError("icacls")}])
def test_acl_failure_warns_but_does_not_break_startup(tmp_path, kw):
    with pytest.MonkeyPatch.context() as mp:
        _spy_icacls(mp, **kw)
        mp.setattr(os, "name", "nt")
        mp.setenv("USERNAME", "boss")
        with pytest.warns(UserWarning, match="icacls"):
            auth._restrict_to_owner(tmp_path / "token")


def test_posix_token_creation_spawns_nothing(tmp_path, monkeypatch):
    """POSIX не меняется: только 0600, никаких внешних процессов."""
    import stat

    calls = _spy_icacls(monkeypatch)
    a = auth.TokenAuth(tmp_path, announce=False)
    assert a.token and calls == []
    assert stat.S_IMODE(os.stat(tmp_path / "token").st_mode) == 0o600


def test_new_token_is_restricted_to_owner(tmp_path, monkeypatch):
    """Свежесозданный файл токена проходит через сужение прав."""
    seen: list[str] = []
    monkeypatch.setattr(auth, "_restrict_to_owner", lambda p: seen.append(str(p)))
    auth.TokenAuth(tmp_path, announce=False)
    assert seen == [str(tmp_path / "token")]


def test_existing_token_is_restricted_too(tmp_path, monkeypatch):
    """Ключ, созданный старой версией без ACL, лечится при следующем старте."""
    (tmp_path / "token").write_text("already-here", encoding="utf-8")
    seen: list[str] = []
    monkeypatch.setattr(auth, "_restrict_to_owner", lambda p: seen.append(str(p)))
    a = auth.TokenAuth(tmp_path, announce=False)
    assert a.token == "already-here"
    assert seen == [str(tmp_path / "token")]
