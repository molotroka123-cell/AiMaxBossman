"""`_restrict_to_owner` on a directory must not lock the owner out of the files inside it (Windows DACL inheritance)."""
from __future__ import annotations

import os

import pytest

from bcc.auth import _restrict_to_owner

windows_only = pytest.mark.skipif(os.name != "nt", reason="Windows ACLs")


def _icacls_argv(monkeypatch, path):
    """What `_restrict_to_owner` would hand to icacls on Windows, captured on any host (no real ACL is touched)."""
    import types
    from bcc import auth
    seen = []
    shim = types.SimpleNamespace(name="nt", environ={"USERNAME": "owner", "USERDOMAIN": "PC"}, path=os.path)
    monkeypatch.setattr(auth, "os", shim)
    monkeypatch.setattr(auth.subprocess, "run", lambda argv, **kw: seen.append(argv) or types.SimpleNamespace(
        returncode=0, stdout="", stderr=""))
    auth._restrict_to_owner(path)
    return seen[0]


def test_a_directory_gets_an_inheritable_grant_and_a_file_a_plain_one(tmp_path, monkeypatch):
    """Legit case: a directory is granted (OI)(CI)F so children keep a DACL; a file keeps the plain :F grant.
    Bad case guarded: `/inheritance:r` + plain `:F` on a directory leaves children with an EMPTY DACL (unreadable by the owner)."""
    folder = tmp_path / "telegram-calls"
    folder.mkdir()
    file = folder / "config.json"
    file.write_text("{}", encoding="utf-8")
    dir_argv = _icacls_argv(monkeypatch, folder)
    assert dir_argv[2:4] == ["/inheritance:r", "/grant:r"] and dir_argv[4].endswith(":(OI)(CI)F"), dir_argv
    file_argv = _icacls_argv(monkeypatch, file)
    assert file_argv[4].endswith(":F") and "(OI)" not in file_argv[4], file_argv


@windows_only
def test_children_stay_readable_after_the_directory_is_restricted(tmp_path):
    home = tmp_path / "telegram-calls"
    home.mkdir()
    fresh = home / "config.json"                 # never restricted by itself: it only inherits from the directory
    fresh.write_text("{}", encoding="utf-8")
    _restrict_to_owner(home)
    assert fresh.read_text(encoding="utf-8") == "{}"
    later = home / "worker.log"                  # created after the restriction
    later.write_text("x", encoding="utf-8")
    assert later.read_text(encoding="utf-8") == "x"
    _restrict_to_owner(fresh)                    # and a file can still be narrowed on its own afterwards
    assert fresh.read_text(encoding="utf-8") == "{}"
