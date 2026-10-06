"""authored_by_lane jeffa: real behavior of the Master Parser passport write path (consent gates, provenance, undo)."""
from __future__ import annotations

import pytest

from bcc.pit.master_parser.passport_sink import (
    PassportView, VaultPassportSink, blocked_telegram_ids)
from bcc.pit.models import ConsentState, EvidenceKind, MemoryCandidate, Sensitivity
from bcc.pit.vault import PersonaVault

SALT = bytes.fromhex("01" * 32)


def cand(cid, key, value, *, category="communication", kind=EvidenceKind.EXPLICIT,
         sens=Sensitivity.NORMAL):
    return MemoryCandidate(id=cid, category=category, key=key, value=value, confidence=0.9,
                           evidence_kind=kind, sensitivity=sens, source_message_id="m1")


@pytest.fixture()
def vault(tmp_path):
    return PersonaVault(tmp_path, SALT)


def person(vault, uid=1, **consent):
    key = vault.key_for_telegram(uid)
    vault.set_consent(key, ConsentState(**consent))
    return key


def test_admission_requires_memory_consent_and_respects_blocklist(vault):
    no = person(vault, 1)
    yes = person(vault, 2, memory_enabled=True)
    sink = VaultPassportSink(vault, blocked_keys=[yes])
    assert sink.admission(no) == (False, "NO_MEMORY_CONSENT")
    assert sink.admission(yes) == (False, "BLOCKED")          # block beats consent
    assert VaultPassportSink(vault).admission(yes) == (True, "OK")


def test_remote_allowed_follows_the_participant_flag(vault):
    a = person(vault, 1, memory_enabled=True)
    b = person(vault, 2, memory_enabled=True, remote_processing_enabled=True)
    sink = VaultPassportSink(vault)
    assert sink.remote_allowed(a) is False and sink.remote_allowed(b) is True


def test_write_refuses_secrets_and_unconsented_sensitive_but_accepts_normal(vault):
    key = person(vault, 1, memory_enabled=True)
    sink = VaultPassportSink(vault)
    assert sink.write(key, cand("f1", "primary_language", "ru"), ["u1"]) is True
    assert sink.write(key, cand("f2", "token", "x", sens=Sensitivity.SECRET_FORBIDDEN), []) is False
    assert sink.write(key, cand("f3", "illness", "flu", category="health", sens=Sensitivity.SENSITIVE), []) is False
    assert [f["id"] for f in vault.list_facts(key)] == ["f1"]


def test_dry_run_reports_success_but_writes_nothing(vault):
    key = person(vault, 1, memory_enabled=True)
    sink = VaultPassportSink(vault, dry_run=True)
    assert sink.write(key, cand("f1", "primary_language", "ru"), []) is True
    assert vault.list_facts(key) == []


def test_view_verdicts_known_conflict_and_revert_blocks_rewrite(vault):
    key = person(vault, 1, memory_enabled=True)
    sink = VaultPassportSink(vault)
    first = cand("f1", "primary_language", "Русский")
    assert sink.view(key).verdict(first) == ("ok", "")
    assert sink.write(key, first, ["u1"])
    view = sink.view(key)
    assert view.verdict(cand("zz", "primary_language", "  русский ")) == ("known", "")   # normalized duplicate
    status, existing = view.verdict(cand("f9", "primary_language", "English"))
    assert status == "conflict" and existing == "русский"
    # owner undo: fact gone, and the same id is never silently re-added by a later run
    assert sink.revert(key, "f1") is True and vault.list_facts(key) == []
    assert sink.revert(key, "f1") is False
    assert sink.view(key).verdict(first)[0] == "blocked"


def test_forget_query_blocks_matching_new_values(vault):
    key = person(vault, 1, memory_enabled=True)
    vault.append_correction(key, {"action": "forget", "query": "Гитар"})
    view = PassportView(vault, key)
    assert view.verdict(cand("n1", "hobby", "учусь играть на гитаре", category="interests"))[0] == "blocked"
    assert view.verdict(cand("n2", "hobby", "горные походы", category="interests"))[0] == "ok"


def test_blocked_telegram_ids_parses_comments_and_missing_file(tmp_path):
    p = tmp_path / "b.txt"
    p.write_text("# owner list\n123  # note\n\nabc\n456\n", encoding="utf-8")
    assert blocked_telegram_ids(p) == {123, 456}
    assert blocked_telegram_ids(tmp_path / "missing.txt") == set()
