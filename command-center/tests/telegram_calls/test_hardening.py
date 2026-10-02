"""Secret hygiene: redaction (positive AND negative), ACL parser, restart/interrupted-write behaviour, backups deny-list."""
from __future__ import annotations

import json
import logging
import os
import stat

import pytest

from bcc.secrets import Vault
from bcc.telegram_calls import hardening as h
from bcc.telegram_calls.account.credentials import CredentialStore
from bcc.telegram_calls.account.login import TelegramAccount

API_HASH = "fedcba9876543210" * 2
SESSION = "1" + "AbC_dEf-gHi" * 20
PHONE = "+7 900 123-45-67"


def test_redact_removes_registered_values_and_secret_shaped_strings():
    text = f"connect api_hash={API_HASH} session {SESSION} phone {PHONE} code: 48213 bot 123456789:{'Ab' * 18}"
    out = h.redact(text, secrets=[API_HASH, SESSION])
    for leak in (API_HASH, SESSION, "900 123", "48213", "Ab" * 18):
        assert leak not in out
    assert out.count(h.MARK) >= 4


def test_redact_does_not_eat_ordinary_log_text_negative_control():
    text = "call c-42 outcome=completed turns=7 latency_p50=812.5 model=fast:qwen barge_ins=1"
    assert h.redact(text) == text
    assert h.redact("codec opus 48000 Hz frames=100") == "codec opus 48000 Hz frames=100"


def test_short_secrets_are_not_blindly_replaced_everywhere():
    assert h.redact("abc abc abc", secrets=["abc"]) == "abc abc abc"        # would gut every message otherwise


def test_formatter_redacts_tracebacks_too(tmp_path):
    logger = logging.getLogger("calls.hardening.test")
    logger.propagate = False
    handler = h.configure_worker_logging(tmp_path / "calls", lambda: [SESSION])
    logger.addHandler(handler)
    try:
        try:
            raise RuntimeError(f"boom with {SESSION} and {PHONE}")
        except RuntimeError:
            logger.exception("login failed for %s", PHONE)
        handler.flush()
    finally:
        logger.removeHandler(handler)
        logging.getLogger().removeHandler(handler)
    text = (tmp_path / "calls" / "worker.log").read_text(encoding="utf-8")
    assert "login failed" in text and "Traceback" in text
    assert SESSION not in text and "900 123" not in text
    if os.name != "nt":
        assert stat.S_IMODE((tmp_path / "calls" / "worker.log").stat().st_mode) == 0o600


def test_third_party_loggers_are_pinned_to_warning(tmp_path):
    h.configure_worker_logging(tmp_path / "c")
    for name in ("telethon", "pytgcalls", "ntgcalls"):
        assert logging.getLogger(name).level == logging.WARNING
    logging.getLogger().handlers[:] = [x for x in logging.getLogger().handlers if not getattr(x, "_calls_worker", False)]


# ---------------------------------------------------------------- ACL

ICACLS_OK = """C:\\Users\\asd\\AppData\\Local\\Bossman\\telegram-companion\\calls\\credentials.enc WIN\\asd:(F)
                                                                                        NT AUTHORITY\\SYSTEM:(F)
                                                                                        BUILTIN\\Administrators:(F)

Successfully processed 1 files; Failed processing 0 files
"""
ICACLS_BAD = ICACLS_OK.replace("NT AUTHORITY\\SYSTEM:(F)", "BUILTIN\\Users:(R)")
ICACLS_EVERYONE = ICACLS_OK + "                                        Everyone:(R)\n"
PATH = "C:\\Users\\asd\\AppData\\Local\\Bossman\\telegram-companion\\calls\\credentials.enc"


def test_icacls_owner_only_passes_and_broad_principals_fail():
    assert h.parse_icacls(ICACLS_OK, PATH, "WIN\\asd")[0] is True
    ok, detail = h.parse_icacls(ICACLS_BAD, PATH, "WIN\\asd")
    assert ok is False and "users" in detail
    assert h.parse_icacls(ICACLS_EVERYONE, PATH, "WIN\\asd")[0] is False
    assert h.parse_icacls("garbage", PATH, "WIN\\asd")[0] is False


def test_icacls_flags_a_foreign_account_even_if_not_in_the_deny_list():
    extra = ICACLS_OK + "                                        WIN\\guest:(R)\n"
    ok, detail = h.parse_icacls(extra, PATH, "WIN\\asd")
    assert ok is False and "guest" in detail


def test_posix_check_detects_a_world_readable_secret(tmp_path):
    if os.name == "nt":                       # the Windows path is covered by the icacls parser tests above
        return
    f = tmp_path / "credentials.enc"
    f.write_text("x")
    os.chmod(f, 0o644)
    assert h.check_owner_only(f).ok is False
    os.chmod(f, 0o600)
    assert h.check_owner_only(f).ok is True
    assert h.check_owner_only(tmp_path / "missing").ok is True


# ---------------------------------------------------------------- restart / interrupted write

API_ID = 777001


def test_credentials_survive_a_process_restart_with_the_same_key_file(tmp_path, monkeypatch):
    monkeypatch.delenv("BOSSMAN_VAULT_KEY", raising=False)
    s1 = CredentialStore(tmp_path / "calls", vault=Vault(tmp_path))
    s1.save_api(API_ID, API_HASH)
    s1.save_session(SESSION, 9, PHONE)
    del s1
    s2 = CredentialStore(tmp_path / "calls", vault=Vault(tmp_path))        # a new process: key is read from secret.key
    c = s2.load()
    assert c.session == SESSION and c.api_hash == API_HASH and c.me_id == 9


def test_an_interrupted_write_never_corrupts_or_leaks(tmp_path, monkeypatch):
    monkeypatch.delenv("BOSSMAN_VAULT_KEY", raising=False)
    home = tmp_path / "calls"
    s = CredentialStore(home, vault=Vault(tmp_path))
    s.save_api(API_ID, API_HASH)
    s.save_session(SESSION, 9, PHONE)
    leftover = home / "credentials.enc.tmp"                                # crash after writing tmp, before os.replace
    leftover.write_text(s.vault.encrypt(json.dumps({"api_id": 1, "api_hash": "0" * 32, "session": "half"})))
    again = CredentialStore(home, vault=Vault(tmp_path)).load()
    assert again.session == SESSION and again.api_id == API_ID              # the committed file wins, tmp is ignored
    assert SESSION.encode() not in leftover.read_bytes() and b"half" not in leftover.read_bytes()


def test_failed_replace_keeps_the_previous_credentials(tmp_path, monkeypatch):
    monkeypatch.delenv("BOSSMAN_VAULT_KEY", raising=False)
    s = CredentialStore(tmp_path / "calls", vault=Vault(tmp_path))
    s.save_api(API_ID, API_HASH)
    s.save_session(SESSION, 9, PHONE)
    real = os.replace

    def boom(*a, **k):
        raise PermissionError("antivirus holds the file")
    monkeypatch.setattr(os, "replace", boom)
    with pytest.raises(PermissionError):
        s.save_session("NEW" + "z" * 50, 10, "+79005550000")
    monkeypatch.setattr(os, "replace", real)
    assert s.load().session == SESSION                                      # old login intact, not half-written


def test_rotated_vault_key_makes_credentials_unreadable_and_says_so_without_crashing(tmp_path, monkeypatch):
    monkeypatch.delenv("BOSSMAN_VAULT_KEY", raising=False)
    s = CredentialStore(tmp_path / "calls", vault=Vault(tmp_path))
    s.save_api(API_ID, API_HASH)
    from cryptography.fernet import Fernet
    monkeypatch.setenv("BOSSMAN_VAULT_KEY", Fernet.generate_key().decode())
    rotated = CredentialStore(tmp_path / "calls", vault=Vault(tmp_path))
    assert rotated.public()["unreadable"] is True
    assert TelegramAccount(rotated).state().value == "error"


def test_session_string_never_reaches_the_public_state_of_the_account(tmp_path, monkeypatch):
    monkeypatch.delenv("BOSSMAN_VAULT_KEY", raising=False)
    store = CredentialStore(tmp_path / "calls", vault=Vault(tmp_path))
    store.save_api(API_ID, API_HASH)
    store.save_session(SESSION, 9, PHONE)
    view = json.dumps(TelegramAccount(store).public())
    assert SESSION not in view and API_HASH not in view and "900 123" not in view and "9001234567" not in view


# ---------------------------------------------------------------- backups / bundles

def test_diagnostic_bundle_denies_the_calls_secret_files_by_name():
    from bcc.features import diag_bundle
    assert "credentials.enc" in diag_bundle.DENY_NAMES
    assert any(x.endswith(".session") for x in diag_bundle.DENY_SUFFIXES)


def test_localized_well_known_accounts_are_accepted_but_a_foreign_one_still_fails(monkeypatch):
    """Russian Windows prints the SYSTEM/Administrators accounts in Russian: they must not make the doctor BLOCKED."""
    monkeypatch.setattr(h, "_localized_allowed", lambda: {"nt authority\\система", "builtin\\администраторы"})
    ru = (ICACLS_OK.replace("NT AUTHORITY\\SYSTEM", "NT AUTHORITY\\СИСТЕМА")
          .replace("BUILTIN\\Administrators", "BUILTIN\\Администраторы"))
    assert h.parse_icacls(ru, PATH, "WIN\\asd") == (True, "owner-only ACL")
    foreign = ru + "                                        WIN\\guest:(R)\n"
    ok, detail = h.parse_icacls(foreign, PATH, "WIN\\asd")
    assert ok is False and "guest" in detail


def test_icacls_output_is_decoded_with_the_console_code_page():
    import codecs
    name = h._console_encoding("nt")
    assert name and codecs.lookup(name)          # a real codec (OEM page on Windows), never an empty name
    assert h._console_encoding("posix") == "utf-8"
