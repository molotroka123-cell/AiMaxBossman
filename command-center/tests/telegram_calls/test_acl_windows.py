"""Windows DACL semantics of the calls folder, with the REAL ``icacls`` (POSIX: a short chmod contract at the end).

The defect these tests pin (found by ``test_calls_e2e_offline.py``, lost-key scenario): the calls directory was restricted with
``bcc.auth._restrict_to_owner`` — ``/inheritance:r`` plus a NON-inheritable owner ACE. When the folder was not created by
``mkdir(mode=0o700)`` (a restored backup, a copied data folder, an older runtime) the files inside hold only ACEs INHERITED from
it; Windows re-propagates the directory change and leaves each of them with an EMPTY DACL (SDDL ``D:AI``): nobody can open
``config.json`` — not even our own worker («Процесс звонков не запущен или упал»).
"""
from __future__ import annotations

import os
import subprocess
from pathlib import Path

import pytest

from bcc import auth
from bcc.secrets import Vault
from bcc.telegram_calls import hardening as h
from bcc.telegram_calls.account.credentials import CredentialStore
from bcc.telegram_calls.call.manager import CallsManager
from bcc.telegram_calls.settings import CallSettings, save_settings

API_HASH = "0123456789abcdef0123456789abcdef"
NAMES = ("config.json", "state.json", "history.jsonl", "STOP", "worker.log")
windows_only = pytest.mark.skipif(os.name != "nt", reason="Windows DACL semantics (icacls)")


def _plain_folder(root: Path, name: str = "telegram-calls") -> Path:
    """A folder the way a backup restore / copy produces it: plain mkdir, files that only INHERIT their ACEs."""
    home = root / name
    home.mkdir()
    for n in NAMES:
        (home / n).write_text("{}\n", encoding="utf-8")
    return home


def _readable(path: Path) -> bool:
    try:
        path.read_bytes()
        return True
    except PermissionError:
        return False


def _sddl(path: Path) -> str:
    out = subprocess.run(["powershell", "-NoProfile", "-Command", f"(Get-Acl -LiteralPath '{path}').Sddl"],
                         capture_output=True, text=True, timeout=60).stdout.strip()
    return out.split("D:", 1)[-1]


@windows_only
def test_restricting_a_restored_folder_keeps_every_file_readable_and_owner_only(tmp_path):
    # negative control: the OLD helper on the very same layout really locks the files out, so this test can see the defect
    broken = _plain_folder(tmp_path, "old-layout")
    auth._restrict_to_owner(broken)
    if any(not _readable(broken / n) for n in NAMES):          # (once bcc.auth itself grants an inheritable ACE this control is obsolete)
        assert [n for n in NAMES if not _readable(broken / n)] == list(NAMES), "an EMPTY DACL on every file"

    home = _plain_folder(tmp_path)
    assert h.restrict_to_owner(home) is True
    for n in NAMES:
        assert _readable(home / n), f"{n}: the legitimate owner lost access"
    assert h.check_owner_only(home).ok and all(h.check_owner_only(home / n).ok for n in NAMES)
    fresh = home / "new.json"                                   # what is created later inherits the owner's ACE, nothing else
    fresh.write_text("{}", encoding="utf-8")
    assert _readable(fresh) and h.check_owner_only(fresh).ok


@windows_only
def test_settings_and_credentials_writes_do_not_lock_out_the_files_already_in_the_folder(tmp_path):
    """The real code path of the lost-key e2e: a restored folder, then `setup` (credentials) and a settings save."""
    home = _plain_folder(tmp_path)
    save_settings(CallSettings(), home)
    CredentialStore(home, vault=Vault(tmp_path)).save_api(1234567, API_HASH)
    assert [n for n in (*NAMES, "credentials.enc") if not _readable(home / n)] == []
    assert all(r.ok for r in h.check_calls_home(home, tmp_path / "secret.key")), [r for r in h.check_calls_home(home) if not r.ok]
    assert CredentialStore(home, vault=Vault(tmp_path)).public()["has_api"] is True


@windows_only
def test_a_file_nobody_can_open_is_given_back_to_the_owner_before_the_worker_starts(tmp_path):
    home = _plain_folder(tmp_path)
    locked = home / "config.json"
    subprocess.run(["icacls", str(locked), "/inheritance:r"], capture_output=True, check=True, timeout=30)    # -> empty DACL
    assert not _readable(locked), "precondition: the state the older build left behind"
    mgr = CallsManager(tmp_path, home=home)
    assert mgr.repair_unreadable() == ["config.json"]
    assert _readable(locked) and h.check_owner_only(locked).ok
    assert mgr.repair_unreadable() == [], "healthy files are not touched"
    # and the same state is also cured by the doctor's heal step
    subprocess.run(["icacls", str(home / "state.json"), "/inheritance:r"], capture_output=True, check=True, timeout=30)
    assert not _readable(home / "state.json")
    assert "state.json" in mgr.heal_permissions() and _readable(home / "state.json")


@windows_only
def test_everyone_and_users_are_taken_off_and_a_foreign_account_is_reported(tmp_path):
    f = tmp_path / "credentials.enc"
    f.write_text("x", encoding="utf-8")
    assert h.restrict_to_owner(f)
    assert _readable(f) and h.check_owner_only(f).ok, "the legitimate owner reads it"
    for sid in ("*S-1-1-0", "*S-1-5-32-545", "*S-1-5-11"):                  # Everyone, BUILTIN\Users, Authenticated Users
        subprocess.run(["icacls", str(f), "/grant", f"{sid}:R"], capture_output=True, check=True, timeout=30)
    widened = h.check_owner_only(f)
    assert not widened.ok and "principal" in widened.detail       # «broad» in English, «unexpected» under a localized name
    assert h.restrict_to_owner(f) and h.check_owner_only(f).ok
    acl = _sddl(f)
    for code in ("WD", "BU", "AU", "IU"):                                     # SDDL aliases of the broad principals
        assert f";;;{code})" not in acl, (code, acl)
    assert _readable(f)
    # a foreign SID that is not in the deny list is still rejected by the check (the doctor shows BLOCKED + remedy)
    subprocess.run(["icacls", str(f), "/grant", "*S-1-5-32-546:R"], capture_output=True, check=True, timeout=30)   # Guests
    foreign = h.check_owner_only(f)
    assert not foreign.ok and "unexpected principal" in foreign.detail
    assert _readable(f), "reporting does not lock the owner out"


@pytest.mark.skipif(os.name == "nt", reason="POSIX contract")
def test_posix_restrict_is_chmod_700_for_a_directory_and_600_for_a_file(tmp_path):
    d = tmp_path / "telegram-calls"
    d.mkdir()
    f = d / "config.json"
    f.write_text("{}", encoding="utf-8")
    os.chmod(d, 0o755)
    os.chmod(f, 0o644)
    assert h.restrict_to_owner(d) and h.restrict_to_owner(f)
    assert (d.stat().st_mode & 0o777, f.stat().st_mode & 0o777) == (0o700, 0o600)
