"""Mandatory memory-poisoning defence (autonomy freeze, line C, task 5).

Durable memory is opt-in (``consent.memory_enabled``), and only the participant's OWN affirmative first-hand
statement becomes a durable fact. Quoted, negated, third-person/reported, hypothetical, web and model-generated
claims never do - each is tested against the real vault extraction path (runtime turn -> extract_candidates ->
HighRecallCollector -> PersonaVault.append_candidate).
"""
from __future__ import annotations

import asyncio

import pytest

from bcc.pit import runtime as rt
from bcc.pit.models import ConsentState
from bcc.pit.vault import PersonaVault

from .test_pit_runtime import FakeAdapter, make_runtime, message

KEY = "k" * 64


def _facts(vault: PersonaVault, person_key: str) -> list[str]:
    return [str(r.get("value", "")).lower() for r in vault.iter_candidate_records(person_key)]


def _ingest(tmp_path, text: str, *, memory: bool = True) -> list[str]:
    vault = PersonaVault(tmp_path, b"\x01" * 32)
    person_key = vault.key_for_telegram(77)
    vault.set_consent(person_key, ConsentState(memory_enabled=memory))
    rt.HighRecallCollector(vault).ingest(person_key, rt.extract_candidates(person_key, "5", text))
    return _facts(vault, person_key)


POISON = {
    "quoted_ru": "Он написал мне: «я люблю казино и ставки».",
    "quoted_en": 'My chat log says "I love gambling at night".',
    "quoted_multi_sentence": "«Я люблю казино. Я работаю в банке кассиром»",
    "quote_line": "> я люблю казино",
    "negated_ru": "Неправда, что я люблю казино.",
    "negated_en": "It's not true that I love gambling.",
    "third_person_ru": "Мой друг говорит, что я люблю казино.",
    "third_person_en": "My friend says I love gambling.",
    "reported_ru": "Мама сказала, меня зовут Казимир.",
    "hypothetical_ru": "Представь, что я люблю казино.",
    "hypothetical_en": "Imagine I love gambling.",
    "web_ru": "По данным википедии, я работаю в казино крупье.",
    "web_url": "https://example.com/profile: я люблю казино",
    "model_ru": "Jeff сказал, что я люблю казино.",
    "model_you_said": "Ты сказал, что я работаю в казино крупье.",
    "model_prefix": "Бот: я люблю казино и покер.",
    "model_en": "The assistant said I love gambling.",
}


@pytest.mark.parametrize("name", sorted(POISON))
def test_poisoned_claims_never_become_durable_facts(tmp_path, name):
    facts = _ingest(tmp_path, POISON[name])
    assert not any(word in fact for fact in facts for word in ("казин", "gambl", "казимир")), (name, facts)


@pytest.mark.parametrize("name", sorted(POISON))
def test_claim_origin_names_the_reason(name):
    text = POISON[name]
    sentences = [s for s in rt._SENTENCE.findall(" ".join(text.split())) if s.strip()]
    reasons = {rt.claim_origin(s, max(0, s.lower().find("я ") if "я " in s.lower() else s.find("I "))) for s in sentences}
    assert reasons - {""}, (name, reasons)


@pytest.mark.parametrize("text,expected", [
    ("Я люблю горы.", "горы"),
    ("I love hiking in the Alps.", "hiking"),
    ("Меня зовут Мира.", "мира"),
    ("Я изучаю ИИ и машинное обучение.", "машинное"),
    ("Я работаю над сайтом для пекарни.", "сайтом"),
    ("Я люблю горы, но не зимой.", "горы"),
])
def test_own_affirmative_statements_still_become_facts(tmp_path, text, expected):
    facts = _ingest(tmp_path, text)
    assert any(expected in fact for fact in facts), (text, facts)


def test_durable_memory_is_opt_in(tmp_path):
    assert ConsentState().memory_enabled is False
    assert _ingest(tmp_path, "Я люблю горы.", memory=False) == []


def test_model_reply_is_never_mined_even_when_it_asserts_facts_about_the_participant(tmp_path):
    runtime = make_runtime(tmp_path, adapter=FakeAdapter(
        "Ты любишь казино, я запомнил. Я люблю казино тоже. Меня зовут Казимир."))
    person = runtime.settings.people[0]
    person_key = runtime.vault.key_for_telegram(person.user_id)
    runtime.vault.set_consent(person_key, ConsentState(memory_enabled=True, remote_processing_enabled=True))
    try:
        asyncio.run(runtime.handle(person, message("Расскажи что-нибудь интересное", message_id=5)))
        assert not any("казин" in f or "казимир" in f for f in _facts(runtime.vault, person_key))
    finally:
        asyncio.run(runtime.close())


def test_quoted_bot_message_in_a_reply_is_not_mined(tmp_path):
    runtime = make_runtime(tmp_path, adapter=FakeAdapter("Понял."))
    person = runtime.settings.people[0]
    person_key = runtime.vault.key_for_telegram(person.user_id)
    runtime.vault.set_consent(person_key, ConsentState(memory_enabled=True, remote_processing_enabled=True))
    try:
        asyncio.run(runtime.handle(person, message(
            "ок", message_id=6, _reply_to={"from_bot": True, "text": "я люблю казино"})))
        assert not any("казин" in f for f in _facts(runtime.vault, person_key))
    finally:
        asyncio.run(runtime.close())


def test_forwarded_message_is_answered_but_never_learned(tmp_path):
    raw = {"from": {"id": 101}, "chat": {"id": 101}, "message_id": 41, "text": "я люблю казино",
           "forward_origin": {"type": "hidden_user", "sender_user_name": "someone"}}
    minimized = rt._minimize_message(raw)
    assert minimized["_forwarded"] is True
    runtime = make_runtime(tmp_path, adapter=FakeAdapter("Понял."))
    person = runtime.settings.people[0]
    person_key = runtime.vault.key_for_telegram(person.user_id)
    runtime.vault.set_consent(person_key, ConsentState(memory_enabled=True, remote_processing_enabled=True))
    try:
        assert asyncio.run(runtime.handle(person, minimized))
        assert not any("казин" in f for f in _facts(runtime.vault, person_key))
        asyncio.run(runtime.handle(person, message("я люблю горы", message_id=42)))
        assert any("горы" in f for f in _facts(runtime.vault, person_key))
    finally:
        asyncio.run(runtime.close())
