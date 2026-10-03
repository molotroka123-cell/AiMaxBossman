"""Jeff must describe his own memory truthfully (live reply 2026-10-01: 'я не сохраняю историю между сессиями').

The service keeps a separate, consent-gated memory per participant. The system prompt used to say only 'you know nothing
personal at the start', so the model improvised a denial. Each consent state gets its own, accurate sentence, and the
states never leak into each other.
"""
from __future__ import annotations

from bcc.pit.models import ConsentState
from bcc.pit.participant_context import (
    MEMORY_LIMITED_RU, MEMORY_OFF_RU, MEMORY_ON_RU, build_participant_context)
from bcc.pit.vault import PersonaVault

SALT = b"s" * 32


def system_for(tmp_path, consent, *, remote):
    vault = PersonaVault(tmp_path, SALT)
    key = vault.key_for_telegram(4242)
    ctx = build_participant_context(query="ты помнишь прошлые разговоры?", vault=vault, person_key=key,
                                    consent=consent, selected_model_is_remote=remote)
    return ctx.as_messages()[0]["content"]


def only(text, expected):
    return [name for name, phrase in (("on", MEMORY_ON_RU), ("off", MEMORY_OFF_RU), ("limited", MEMORY_LIMITED_RU))
            if phrase in text] == [expected]


def test_memory_enabled_jeff_is_told_he_has_a_per_person_memory(tmp_path):
    text = system_for(tmp_path, ConsentState(memory_enabled=True), remote=False)
    assert only(text, "on")
    assert "не отвечай, что ничего не сохраняешь" in text and "/forget" in text


def test_memory_disabled_jeff_is_told_to_say_so_honestly(tmp_path):
    text = system_for(tmp_path, ConsentState(memory_enabled=False), remote=False)
    assert only(text, "off")
    assert "не помнишь" in text


def test_cloud_route_without_remote_personalization_gets_the_limited_statement(tmp_path):
    text = system_for(tmp_path, ConsentState(memory_enabled=True, remote_processing_enabled=True,
                                             remote_personalization_enabled=False), remote=True)
    assert only(text, "limited")
    assert "Не отрицай, что память у сервиса есть" in text


def test_cloud_route_with_remote_personalization_keeps_the_full_statement(tmp_path):
    text = system_for(tmp_path, ConsentState(memory_enabled=True, remote_processing_enabled=True,
                                             remote_personalization_enabled=True), remote=True)
    assert only(text, "on")


def test_the_denial_wording_is_gone_from_every_state(tmp_path):
    for consent, remote in ((ConsentState(memory_enabled=True), False), (ConsentState(memory_enabled=False), False),
                            (ConsentState(memory_enabled=True), True)):
        assert "я не сохраняю историю" not in system_for(tmp_path, consent, remote=remote).lower()
