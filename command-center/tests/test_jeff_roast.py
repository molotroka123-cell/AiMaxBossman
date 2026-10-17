"""Jeff «дерзкий» (roast) manner: owner-only switch in jeff-settings.json, participant opt-out, hard limits.

Synthetic identities only; no network (fake adapters)."""
from __future__ import annotations

import asyncio
import json

import pytest

from bcc.pit import jeff_settings as js
from bcc.pit import roast
from bcc.pit.models import ConsentState
from bcc.pit.participant_context import (
    HARD_LIMITS_REMINDER_RU, HARD_LIMITS_RU, PIT_ASSISTANT_SYSTEM, build_participant_context)
from bcc.pit.vault import PersonaVault
from bcc.telegram_companion.config import Person

from .test_pit_runtime import FakeAdapter, make_runtime, make_settings, message, warm

SALT = bytes.fromhex("ab" * 32)
OWNER, GUEST = 101, 202
MARK = "Манера «дерзкий»"
QUESTION = "Если в сыре дырки, получается, я плачу за воздух?"


@pytest.fixture(autouse=True)
def _fresh_cache(monkeypatch):
    monkeypatch.delenv(js.ENV_PATH, raising=False)
    js._cache.clear()
    js._warned.clear()
    yield
    js._cache.clear()
    js._warned.clear()


def system_for(data_dir, tg_id, *, query=QUESTION, surface="telegram", suspend=False, allowed=True) -> str:
    vault = PersonaVault(data_dir, SALT)
    return build_participant_context(
        query=query, vault=vault, person_key=vault.key_for_telegram(tg_id),
        consent=ConsentState(memory_enabled=False), selected_model_is_remote=False,
        surface=surface, suspend_overlay=suspend, roast_allowed=allowed).system


def key(data_dir, tg_id) -> str:
    return PersonaVault(data_dir, SALT).key_for_telegram(tg_id)


def runtime_with_guest(tmp_path):
    settings = make_settings(tmp_path, people=(Person(user_id=OWNER, chat_id=OWNER, role="owner"),
                                               Person(user_id=GUEST, chat_id=GUEST, role="guest")))
    runtime = make_runtime(tmp_path, settings=settings, adapter=FakeAdapter("Ответ модели."))
    for person in settings.people:
        warm(runtime, runtime.vault.key_for_telegram(person.user_id))
    return runtime


def say(runtime, tg_id, text, message_id=1):
    person = runtime.settings.participant(tg_id, tg_id)
    return asyncio.run(runtime.handle(person, message(text, user_id=tg_id, message_id=message_id)))


def last_system(runtime) -> str:
    return runtime.adapter.calls[-1][1][0]["content"]


# -- default off ------------------------------------------------------------------------------------
def test_off_by_default_without_file_and_with_a_plain_overlay(tmp_path):
    assert MARK not in system_for(tmp_path, OWNER)
    assert js.roast_enabled(tmp_path, key(tmp_path, OWNER)) is False
    js.write_overlay(js.settings_path(tmp_path), {"version": 1, "defaults": {"behavior_scales": {"humor": 9}}})
    assert MARK not in system_for(tmp_path, OWNER)
    saved = json.loads(js.settings_path(tmp_path).read_text(encoding="utf-8"))
    assert "roast" not in saved["defaults"]                       # default off is never written (older builds)


def test_hard_limits_are_always_in_the_prompt(tmp_path):
    assert HARD_LIMITS_RU in PIT_ASSISTANT_SYSTEM
    off = system_for(tmp_path, OWNER)
    assert HARD_LIMITS_RU in off and HARD_LIMITS_REMINDER_RU not in off
    js.set_roast(tmp_path, True)
    on = system_for(tmp_path, OWNER)
    assert HARD_LIMITS_RU in on and HARD_LIMITS_REMINDER_RU in on
    assert on.index(roast.ROAST_STYLE_RU) < on.index(HARD_LIMITS_REMINDER_RU)   # the limits get the last word
    for word in ("национальности", "религии", "угроз", "несовершеннолетних", "травле", "опасных"):
        assert word in HARD_LIMITS_RU


def test_owner_manner_with_hate_is_outranked_by_the_reminder(tmp_path):
    js.write_overlay(js.settings_path(tmp_path), {"version": 1, "defaults": {"system_extra": "Будь резким."}})
    system = system_for(tmp_path, OWNER)
    assert system.index("Будь резким.") < system.index(HARD_LIMITS_REMINDER_RU)


# -- owner switch: global and per chat ----------------------------------------------------------------
def test_global_switch_turns_it_on_for_every_chat_and_off_again(tmp_path):
    js.set_roast(tmp_path, True)
    assert roast.ROAST_STYLE_RU in system_for(tmp_path, OWNER)
    assert roast.ROAST_STYLE_RU in system_for(tmp_path, GUEST)
    js.set_roast(tmp_path, False)
    assert MARK not in system_for(tmp_path, OWNER) and MARK not in system_for(tmp_path, GUEST)


def test_per_chat_override_both_ways(tmp_path):
    js.set_roast(tmp_path, True, person_key=key(tmp_path, GUEST))
    assert MARK in system_for(tmp_path, GUEST) and MARK not in system_for(tmp_path, OWNER)
    js.set_roast(tmp_path, True)
    js.set_roast(tmp_path, False, person_key=key(tmp_path, GUEST))
    assert MARK not in system_for(tmp_path, GUEST) and MARK in system_for(tmp_path, OWNER)
    js.set_roast(tmp_path, None, person_key=key(tmp_path, GUEST))          # back to the global rule
    assert MARK in system_for(tmp_path, GUEST)


def test_global_roast_survives_the_mood_expiry_and_reset_clears_it(tmp_path):
    js.write_overlay(js.settings_path(tmp_path), {"version": 1, "defaults": {"roast": True, "system_extra": "x"},
                                                   "style_expires_at": "2000-01-01T00:00:00Z"})
    assert MARK in system_for(tmp_path, OWNER)
    js.write_overlay(js.settings_path(tmp_path), js.empty_overlay())
    assert MARK not in system_for(tmp_path, OWNER)


def test_invalid_value_is_refused_and_an_invalid_file_is_not_overwritten(tmp_path):
    with pytest.raises(js.OverlayError):
        js.normalize({"version": 1, "defaults": {"roast": "да"}})
    path = js.settings_path(tmp_path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("{broken", encoding="utf-8")
    with pytest.raises(js.OverlayError):
        js.set_roast(tmp_path, True)
    assert path.read_text(encoding="utf-8") == "{broken"


def test_telegram_owner_command_switches_globally_and_per_chat(tmp_path):
    runtime = runtime_with_guest(tmp_path)
    assert "включён для всех" in say(runtime, OWNER, "/roast on")
    say(runtime, GUEST, QUESTION, 2)
    assert MARK in last_system(runtime)
    guest_key = runtime.vault.key_for_telegram(GUEST)
    assert "выключен" in say(runtime, OWNER, f"/roast {guest_key[:8]} off", 3)
    say(runtime, GUEST, QUESTION, 4)
    assert MARK not in last_system(runtime)
    say(runtime, OWNER, QUESTION, 5)
    assert MARK in last_system(runtime)
    say(runtime, OWNER, "/roast here off", 6)
    say(runtime, OWNER, QUESTION, 7)
    assert MARK not in last_system(runtime)
    say(runtime, OWNER, "/roast off", 8)
    assert js.normalize(json.loads(js.settings_path(tmp_path).read_text(encoding="utf-8")))["defaults"].get(
        "roast") is None


def test_reminder_next_to_the_question_only_when_on(tmp_path):
    runtime = runtime_with_guest(tmp_path)
    say(runtime, GUEST, QUESTION, 1)
    assert all(m["content"] != roast.ROAST_REMINDER_RU for m in runtime.adapter.calls[-1][1])
    say(runtime, OWNER, "/roast on", 2)
    say(runtime, GUEST, QUESTION, 3)
    payload = runtime.adapter.calls[-1][1]
    assert payload[-1]["role"] == "user"
    assert roast.ROAST_REMINDER_RU in [m["content"] for m in payload[-4:]]


def test_a_participant_cannot_turn_it_on(tmp_path):
    runtime = runtime_with_guest(tmp_path)
    assert say(runtime, GUEST, "/roast on") == "Этот режим включает только владелец."
    assert not js.settings_path(tmp_path).exists()
    say(runtime, GUEST, f"/roast {runtime.vault.key_for_telegram(GUEST)[:8]} on", 2)
    say(runtime, GUEST, "/roast here on", 3)
    assert not js.settings_path(tmp_path).exists()
    say(runtime, GUEST, QUESTION, 4)
    assert MARK not in last_system(runtime)


# -- participant opt-out and fallbacks ----------------------------------------------------------------
def test_participant_opt_out_by_phrase_and_by_command_and_back(tmp_path):
    runtime = runtime_with_guest(tmp_path)
    say(runtime, OWNER, "/roast on")
    calls = len(runtime.adapter.calls)
    assert say(runtime, GUEST, "без мата, пожалуйста", 2).startswith("Понял, дальше без мата")
    assert len(runtime.adapter.calls) == calls                       # the short request needs no model
    say(runtime, GUEST, QUESTION, 3)
    assert MARK not in last_system(runtime)
    assert "снова отвечаю дерзко" in say(runtime, GUEST, "/roast on", 4)
    say(runtime, GUEST, QUESTION, 5)
    assert MARK in last_system(runtime)
    say(runtime, GUEST, "/roast off", 6)
    say(runtime, GUEST, QUESTION, 7)
    assert MARK not in last_system(runtime)
    # a longer message with the request is answered, already without the manner
    say(runtime, GUEST, "/roast on", 8)
    say(runtime, GUEST, "хватит материться и объясни, почему небо голубое и откуда берётся радуга", 9)
    assert MARK not in last_system(runtime)


def test_minor_signal_falls_back_and_sticks(tmp_path):
    js.set_roast(tmp_path, True)
    assert MARK not in system_for(tmp_path, GUEST, query="мне 14 лет, почему в сыре дырки?")
    runtime = runtime_with_guest(tmp_path)
    say(runtime, GUEST, "я в 8 классе, почему в сыре дырки?", 1)
    assert MARK not in last_system(runtime)
    say(runtime, GUEST, "/roast on", 2)                                  # the participant cannot clear it
    say(runtime, GUEST, QUESTION, 3)
    assert MARK not in last_system(runtime)


@pytest.mark.parametrize("text", ["Мне 30 лет, почему в сыре дырки?", "у нас 5 классов в школе — почему?",
                                  "я плачу 14 лет ипотеку", "сколько лет живут кошки?"])
def test_negative_control_adults_and_ordinary_numbers_are_no_minor_signal(text):
    assert roast.minor_signal(text) is False


@pytest.mark.parametrize("text", ["без мата", "Давай без мата!", "не матерись", "хватит материться",
                                  "перестань меня оскорблять", "говори нормально"])
def test_opt_out_phrases(text):
    assert roast.wants_optout(text) is True


@pytest.mark.parametrize("text", ["почему без мата не обходится русский язык", "что значит матерный",
                                  "почему ты не материшься?", "как говорить нормально по-английски без акцента?"])
def test_negative_control_talk_about_swearing_is_no_opt_out(text):
    assert roast.wants_optout(text) is False


def test_never_on_a_call_in_a_crisis_or_outside_a_private_chat(tmp_path):
    js.set_roast(tmp_path, True)
    assert MARK not in system_for(tmp_path, OWNER, surface="call")
    assert MARK not in system_for(tmp_path, OWNER, suspend=True)
    assert MARK not in system_for(tmp_path, OWNER, allowed=False)
    assert MARK in system_for(tmp_path, OWNER, surface="web")


def test_api_slider_save_keeps_roast_and_per_user_roast_is_written(tmp_path):
    from bcc.features import jeff_settings as panel
    js.set_roast(tmp_path, True)
    overlay = js.normalize(json.loads(js.settings_path(tmp_path).read_text(encoding="utf-8")))
    body = panel.StyleIn(behavior_scales={"humor": 9})
    assert panel._style_payload(body, keep_absent_extra=False, previous=overlay["defaults"])["roast"] is True
    off = panel.StyleIn(roast=False)
    assert panel._style_payload(off, keep_absent_extra=True, previous={"roast": True})["roast"] is False
