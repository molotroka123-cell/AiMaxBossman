"""SECREM F-018 (command-center) — мёртвый защитный код подключён или честно помечен."""
from __future__ import annotations

import os
import stat
import subprocess

import pytest

from bcc.context_os import integration as ctx_int
from bcc.v2.code_index import CodeIndex
from bcc.v2.permissions import PermissionPolicy


def test_permissions_deny_list_matches_by_name_at_any_depth():
    pol = PermissionPolicy.safe_default()
    for p in (".env", "config/.env", "deep/dir/id_rsa", "keys/wallet.dat", "a/b/c/prod.env"):
        assert pol.denies_read(p), p
    assert not pol.denies_read("src/main.py")


def test_code_index_skips_secrets_and_symlink_escape(tmp_path):
    repo = tmp_path / "repo"
    (repo / "src").mkdir(parents=True)
    (repo / "src" / "main.py").write_text("def main():\n    return 1\n", encoding="utf-8")
    (repo / "src" / "keys.py").write_text("x = 1\n", encoding="utf-8")
    (repo / ".env").write_text("API_KEY=BOSSMAN_TEST_SECRET_9F31A7\n", encoding="utf-8")
    outside = tmp_path / "outside"
    outside.mkdir()
    (outside / "leak.py").write_text("LEAK = 'BOSSMAN_TEST_SECRET_9F31A7'\n", encoding="utf-8")
    (outside / "id_rsa").write_text("PRIVATE", encoding="utf-8")
    linkdir = repo / "src" / "linkdir"
    if os.name == "nt":
        # A junction is a real reparse-point escape and needs no symlink privilege.
        subprocess.run(
            ["cmd", "/c", "mklink", "/J", str(linkdir), str(outside)],
            check=True,
            capture_output=True,
        )
        assert linkdir.lstat().st_file_attributes & stat.FILE_ATTRIBUTE_REPARSE_POINT
    else:
        os.symlink(outside, linkdir, target_is_directory=True)
        os.symlink(outside / "leak.py", repo / "src" / "linked.py")
        # A harmless link name must not expose the denied target.
        os.symlink(outside / "id_rsa", repo / "src" / "helper.py")
    assert (linkdir / "leak.py").resolve() == (outside / "leak.py").resolve()

    idx = CodeIndex(roots=[repo], index_path=tmp_path / "idx.json")
    indexed = idx.iter_files()
    files = {p.name for p in indexed}
    assert "main.py" in files and "keys.py" in files
    assert ".env" not in files
    assert "leak.py" not in files  # directory reparse point leaves the root
    if os.name != "nt":
        assert "linked.py" not in files  # file symlink leaves the root
        assert "helper.py" not in files and "id_rsa" not in files
    assert all("outside" not in str(p) for p in indexed)


async def test_context_os_attach_is_honestly_not_wired():
    with pytest.raises(NotImplementedError, match="NOT WIRED"):
        await ctx_int.attach_to_engine(object(), object())
    assert "DEPRECATED_NON_PROTECTIVE" in (__import__("bcc.context_os").context_os.__doc__ or "")
