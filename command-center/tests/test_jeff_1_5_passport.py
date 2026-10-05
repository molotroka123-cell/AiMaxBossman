"""Jeff 1.5 passport: versioned evidence, four layers, lossless migration, participant commands.

Fakes only: no model, no Telegram, no network, no real participant data.
"""
from __future__ import annotations

import asyncio
import json
from pathlib import Path

from bcc.pit import passport, passport_commands as pc
from bcc.pit.models import ConsentState, EvidenceKind, MemoryCandidate, Sensitivity
from bcc.pit.participant_context import build_participant_context
from bcc.pit.vault import PersonaVault

from .test_pit_runtime import make_runtime, message
from .test_pit_web import H, RecordingAdapter, chat, client_for, make_app, signup

SALT = b"s" * 32
LEGACY_ROW = {"id": "old1", "category": "interests", "key": "hobbies", "value": "горы",
              "confidence": 0.8, "evidence_kind": "explicit", "sensitivity": "normal",
              "source_message_id": "m7", "observed_at": "2026-05-01T10:00:00Z",
              "observation_count": 2, "tags": ["x"], "custom_future_field": {"a": 1}}


def vault_with_legacy(tmp_path: Path, key_seed: int = 1, rows=(LEGACY_ROW,)):
    vault = PersonaVault(tmp_path, SALT)
    key = vault.key_for_telegram(key_seed)
    vault.set_consent(key, ConsentState(memory_enabled=True))
    path = vault.person_dir(key) / "facts.jsonl"
    path.write_text("".join(json.dumps(r, ensure_ascii=False) + "\n" for r in rows), encoding="utf-8")
    return vault, key


def cand(value="кофе", cid="c1", sensitivity=Sensitivity.NORMAL):
    return MemoryCandidate(id=cid, category="interests", key="hobbies", value=value, confidence=0.8,
                           evidence_kind=EvidenceKind.EXPLICIT, sensitivity=sensitivity,
                           source_message_id="voice:5")


def test_schema_version_is_exposed():
    assert passport.JEFF_PASSPORT_SCHEMA == "jeff.passport/1"
    assert ConsentState().personalization_enabled is True      # old consent files default to on


# -- migration -------------------------------------------------------------------------------
def test_legacy_rows_stay_readable_without_migration(tmp_path):
    vault, key = vault_with_legacy(tmp_path)
    assert vault.list_facts(key)[0]["value"] == "горы"
    upgraded = passport.facts_layer(vault, key)[0]
    assert upgraded["passport"]["source"]["kind"] == "message"
    assert "passport" not in list(vault.iter_candidate_records(key))[0]  # read did not rewrite


def test_migration_is_lossless_idempotent_and_backed_up(tmp_path):
    other = dict(LEGACY_ROW, id="old2", value="море", source_message_id="voice:9")
    vault, key = vault_with_legacy(tmp_path, rows=(LEGACY_ROW, other))
    before = list(vault.iter_candidate_records(key))
    result = passport.migrate_person(vault, key)
    assert result == {"migrated": 2, "already": 0, "ok": True}
    after = list(vault.iter_candidate_records(key))
    assert len(after) == 2
    for old, new in zip(before, after):
        assert all(new[k] == v for k, v in old.items())       # every original field intact
        assert new["passport"]["schema"] == passport.JEFF_PASSPORT_SCHEMA
    assert after[1]["passport"]["source"]["kind"] == "voice"
    backup = vault.person_dir(key) / passport.BACKUP_NAME
    assert [json.loads(x) for x in backup.read_text(encoding="utf-8").splitlines()] == before
    again = passport.migrate_person(vault, key)
    assert again == {"migrated": 0, "already": 2, "ok": True}
    assert list(vault.iter_candidate_records(key)) == after


def test_migration_failure_rolls_back(tmp_path, monkeypatch):
    vault, key = vault_with_legacy(tmp_path)
    original = (vault.person_dir(key) / "facts.jsonl").read_text(encoding="utf-8")

    def broken(person_key, rows):
        (vault.person_dir(person_key) / "facts.jsonl").write_text("", encoding="utf-8")

    monkeypatch.setattr(vault, "_rewrite_facts", broken)
    result = passport.migrate_person(vault, key)
    assert result["ok"] is False
    assert (vault.person_dir(key) / "facts.jsonl").read_text(encoding="utf-8") == original


def test_migrate_all_keeps_participants_isolated(tmp_path):
    vault, key_a = vault_with_legacy(tmp_path, 1)
    key_b = vault.key_for_telegram(2)
    vault.set_consent(key_b, ConsentState(memory_enabled=True))
    (vault.person_dir(key_b) / "facts.jsonl").write_text(
        json.dumps(dict(LEGACY_ROW, id="b1", value="секрет-B")) + "\n", encoding="utf-8")
    assert passport.migrate_all(vault) == {"people": 2, "migrated": 2, "failed": 0}
    a_text = (vault.person_dir(key_a) / "facts.jsonl").read_text(encoding="utf-8")
    assert "секрет-B" not in a_text
    assert [r["id"] for r in vault.iter_candidate_records(key_b)] == ["b1"]


# -- evidence, versions, corrections ---------------------------------------------------------
def test_new_fact_has_evidence_envelope_and_scope(tmp_path):
    vault = PersonaVault(tmp_path, SALT)
    key = vault.key_for_telegram(1)
    vault.set_consent(key, ConsentState(memory_enabled=True, sensitive_memory_enabled=True))
    assert vault.append_candidate(key, cand())
    assert vault.append_candidate(key, cand("диабет", "c2", Sensitivity.SENSITIVE))
    plain, sensitive = list(vault.iter_candidate_records(key))
    env = plain["passport"]
    assert env["source"]["kind"] == "voice" and env["source"]["ref"] == "voice:5"
    assert env["consent"]["basis"] == "memory_enabled" and env["version"] == 1
    assert env["scope"] == "own_chat" and sensitive["passport"]["scope"] == "own_chat_local_only"
    assert passport.freshness_status(dict(plain, passport=dict(env, freshness={
        "last_seen": "2020-01-01T00:00:00Z"}))) == "stale"


def test_correction_bumps_version_and_keeps_history(tmp_path):
    vault, key = vault_with_legacy(tmp_path)
    assert vault.correct_fact(key, "old1", "озеро", actor="participant", surface="web")
    row = list(vault.iter_candidate_records(key))[0]
    env = row["passport"]
    assert row["value"] == "озеро" and env["version"] == 2
    assert env["history"][-1]["previous_value"] == "горы"
    assert env["history"][-1]["actor"] == "participant"
    assert row["custom_future_field"] == {"a": 1}
    assert vault.correct_fact(key, "old1", "пароль: qwerty123", actor="participant") is False
    assert list(vault.iter_candidate_records(key))[0]["passport"]["version"] == 2


def test_forget_removes_row_and_its_history(tmp_path):
    vault, key = vault_with_legacy(tmp_path)
    vault.correct_fact(key, "old1", "озеро", actor="participant")
    assert vault.delete_fact(key, "old1", actor="participant")
    assert "горы" not in (vault.person_dir(key) / "facts.jsonl").read_text(encoding="utf-8")


# -- layers ----------------------------------------------------------------------------------
NARRATIVE = {"status": "OK", "paragraphs": ["Контекст: пишет коротко.", "Манера: прямая."],
             "provenance": {"messages": 5}}


def test_style_layer_needs_consent_and_never_mixes_into_facts(tmp_path):
    vault, key = vault_with_legacy(tmp_path)
    person_dir = vault.person_dir(key)
    assert passport.write_style(person_dir, NARRATIVE, run_id="r1", model="local")
    assert passport.read_style(vault, key)["version"] == 1
    assert [r["id"] for r in vault.iter_candidate_records(key)] == ["old1"]
    assert "Контекст" not in (person_dir / "facts.jsonl").read_text(encoding="utf-8")
    assert passport.write_style(person_dir, dict(NARRATIVE, paragraphs=["Новое."]))
    style = passport.read_style(vault, key)
    assert style["version"] == 2 and style["history"][0]["paragraphs"][0].startswith("Контекст")
    vault.set_consent(key, ConsentState(memory_enabled=False))
    assert passport.write_style(person_dir, NARRATIVE) is False
    assert passport.write_style(person_dir, dict(NARRATIVE, status="LLM_FAILED")) is False


def test_style_reaches_context_only_with_memory_and_personalization(tmp_path):
    vault, key = vault_with_legacy(tmp_path)
    passport.write_style(vault.person_dir(key), NARRATIVE)
    consent = vault.consent(key)
    ctx = build_participant_context(query="привет", vault=vault, person_key=key, consent=consent,
                                    selected_model_is_remote=False)
    assert any("Стиль общения (предположение" in item for item in ctx.persona_items)
    consent.personalization_enabled = False
    ctx = build_participant_context(query="горы", vault=vault, person_key=key, consent=consent,
                                    selected_model_is_remote=False)
    assert ctx.persona_items == ()


def test_procedures_need_verification_evidence(tmp_path):
    vault, key = vault_with_legacy(tmp_path)
    assert passport.add_procedure(vault, key, name="p", steps=["a"], verified_by="", evidence_ref="") is False
    assert passport.add_procedure(vault, key, name="p", steps=["a"], verified_by="verifier",
                                  evidence_ref="test:abc")
    assert passport.procedures(vault, key)[0]["verification"]["evidence"] == "test:abc"
    assert passport.layer_summary(vault, key) == {
        "schema": passport.JEFF_PASSPORT_SCHEMA, "events": 0, "facts": 1, "style": 0, "procedures": 1}


# -- participant commands (Telegram runtime) ---------------------------------------------------
def _run(runtime, text, n, user=101):
    person = next(p for p in runtime.settings.people if p.user_id == user)
    return asyncio.run(runtime.handle(person, message(text, message_id=n, user_id=user)))


def test_commands_view_personalization_revoke_survive_restart(tmp_path):
    runtime = make_runtime(tmp_path)
    key = runtime.vault.key_for_telegram(101)
    runtime.vault.set_consent(key, ConsentState(memory_enabled=True, remote_processing_enabled=True,
                                                remote_personalization_enabled=True))
    runtime.vault.append_candidate(key, cand())
    view = _run(runtime, "/passport", 1)
    assert "кофе" in view and "голос" in view and "уверенность 0.80" in view
    assert "выключена" in _run(runtime, "/personalization off", 2)

    restarted = make_runtime(tmp_path)                       # new process state, same files
    assert restarted.vault.consent(key).personalization_enabled is False
    ctx = build_participant_context(query="кофе", vault=restarted.vault, person_key=key,
                                    consent=restarted.vault.consent(key),
                                    selected_model_is_remote=False)
    assert ctx.persona_items == ()
    assert "включена" in _run(restarted, "/personalization on", 3)

    assert "отозвано" in _run(restarted, "/revoke_consent", 4)
    again = make_runtime(tmp_path)
    flags = pc.consent_flags(again.vault.consent(key))
    assert not any(v for k, v in flags.items() if k != "personalization_enabled")
    assert "выключена" in _run(again, "/passport", 5)        # consent-gated view
    assert "память" in _run(again, "/personalization on", 6)   # cannot re-grant through this switch
    assert again.vault.consent(key).personalization_enabled is True
    assert list(again.vault.iter_candidate_records(key))      # data kept until the person deletes


def test_personalization_on_requires_memory_consent(tmp_path):
    vault, key = vault_with_legacy(tmp_path)
    vault.set_consent(key, ConsentState(memory_enabled=False, personalization_enabled=False))
    ok, note = pc.set_personalization(vault, key, True)
    assert ok is False and "память" in note
    assert vault.consent(key).personalization_enabled is False


def test_consent_changes_are_audited_and_logged_without_content(tmp_path):
    vault, key = vault_with_legacy(tmp_path)
    pc.set_personalization(vault, key, False)
    pc.revoke_consent(vault, key)
    actions = [(r["action"], tuple(r["categories"])) for r in vault.memory_audit(key)]
    assert ("consent", ("personalization_off",)) in actions and ("consent", ("revoke_all",)) in actions
    assert "горы" not in json.dumps(vault.memory_audit(key), ensure_ascii=False)
    history = passport.consent_history(vault, key)
    assert history[-1]["changes"]["memory_enabled"] is False


def test_commands_touch_only_the_asking_participant(tmp_path):
    vault, key_a = vault_with_legacy(tmp_path, 1)
    key_b = vault.key_for_telegram(2)
    vault.set_consent(key_b, ConsentState(memory_enabled=True))
    pc.revoke_consent(vault, key_a)
    assert vault.consent(key_b).memory_enabled is True
    assert vault.memory_audit(key_b, last=5) == []


def test_revoke_without_namespace_is_a_no_op(tmp_path):
    vault = PersonaVault(tmp_path, SALT)
    ok, _ = pc.revoke_consent(vault, vault.key_for_telegram(9))
    assert ok is False and not vault.person_dir(vault.key_for_telegram(9)).exists()


def test_revoke_invalidates_in_flight_memory_writes(tmp_path):
    runtime = make_runtime(tmp_path)
    key = runtime.vault.key_for_telegram(101)
    runtime.vault.set_consent(key, ConsentState(memory_enabled=True))
    epoch = runtime._memory_epoch.get(key, 0)
    _run(runtime, "/revoke_consent", 1)
    assert runtime._memory_epoch.get(key, 0) == epoch + 1
    assert runtime.vault.append_candidate(key, cand()) is False


# -- Jeff window API ---------------------------------------------------------------------------
def test_window_api_view_and_consent_isolated_and_audited(tmp_path):
    app, _ = make_app(tmp_path, RecordingAdapter("Ок."))
    with client_for(app) as c:
        assert c.get("/api/jeff/passport").status_code == 401
        signup(c)
        chat(c, "я люблю зелёный чай")
        data = c.get("/api/jeff/passport").json()
        assert data["schema"] == passport.JEFF_PASSPORT_SCHEMA
        fact = data["facts"][0]
        assert fact["source"]["kind"] == "message" and fact["version"] == 1
        assert c.post("/api/jeff/passport/consent", json={"action": "bogus"}, headers=H).status_code == 400
        off = c.post("/api/jeff/passport/consent", json={"action": "personalization_off"}, headers=H).json()
        assert off["ok"] and off["consent"]["personalization_enabled"] is False
        gone = c.post("/api/jeff/passport/consent", json={"action": "revoke"}, headers=H).json()
        assert gone["ok"] and gone["consent"]["memory_enabled"] is False
        after = c.get("/api/jeff/passport").json()
        assert after["facts"] == []                          # consent-gated
        actions = [r["action"] for r in c.get("/api/jeff/memory").json()["audit"]]
    assert "consent" in actions and "passport_view" in actions


def test_master_parser_narrative_feeds_style_layer_only_with_consent(tmp_path):
    from bcc.pit.master_parser import narrative as narr
    vault, key = vault_with_legacy(tmp_path)
    pit = vault.root.parent
    narr.save_narrative(pit, key, dict(NARRATIVE), run_id="run-1", model="local-fake")
    style = passport.read_style(vault, key)
    assert style["source"]["run_id"] == "run-1" and style["kind"] == "inference"
    assert (pit / "passport-checkpoints" / "narratives" / f"{key}.json").is_file()
    vault2 = PersonaVault(tmp_path / "other", SALT)
    key2 = vault2.key_for_telegram(2)
    vault2.set_consent(key2, ConsentState(memory_enabled=False))
    narr.save_narrative(vault2.root.parent, key2, dict(NARRATIVE), run_id="run-2", model="m")
    assert passport.read_style(vault2, key2) is None
