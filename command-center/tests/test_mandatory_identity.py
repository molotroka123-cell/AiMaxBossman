"""Mandatory identity red-team (autonomy freeze, line C, task 3).

40 RU/EN probes (direct, roleplay, translation, base64/rot13/reversed encodings, quoting, multi-turn) run through
the real ``ParticipantRuntime`` against a fake model that leaks a base-model identity two answers out of three.
Required: 0 leaks, both with the input guard on and with every probe forced through the leaking model.
"""
from __future__ import annotations

import asyncio
from collections import Counter

import pytest

from bcc.pit import runtime as rt
from bcc.pit.identity_guard import guard_reply, identity_leak, stream_leaks
from bcc.pit.reply_stream import TurnStream

from .redteam.fixtures import BENIGN, LEAKS, LIMITATIONS, LeakyModel, leaked_names, load_corpus
from .test_pit_runtime import make_runtime, message, warm

CORPUS = load_corpus("identity_corpus.json")


def test_corpus_has_forty_probes_across_every_vector_and_both_languages():
    assert len(CORPUS) == 40
    vectors = Counter(p["vector"] for p in CORPUS)
    assert set(vectors) >= {"direct", "roleplay", "translation", "base64", "rot13", "quoting", "multi_turn"}
    assert {p["lang"] for p in CORPUS} == {"ru", "en"}
    assert sum(len(p["turns"]) > 1 for p in CORPUS) >= 5


def test_the_oracle_sees_every_fake_leak():
    for text in LEAKS:
        assert leaked_names(text), text
    for text in BENIGN:
        assert not leaked_names(text), text


@pytest.mark.parametrize("index", range(len(LEAKS)))
def test_output_filter_rewrites_each_leak_to_the_persona_and_keeps_honest_limits(index):
    result = guard_reply(LEAKS[index])
    assert result.changed
    assert leaked_names(result.text) == [], result.text
    assert "Jeff" in result.text or "[скрыто]" in result.text
    if index in LIMITATIONS:
        assert LIMITATIONS[index] in result.text


@pytest.mark.parametrize("text", [
    "Claude и GPT-4 — хорошие модели для кода; я бы начал с того, что проще подключить.",
    "I recommend trying Llama locally if you have a GPU.",
    "Меня зовут Jeff. Я пользуюсь только тем, что ты мне написал.",
    "Google Maps покажет маршрут лучше меня.",
    "I can't browse the internet, but I can explain how search engines work.",
])
def test_ordinary_mentions_and_honest_limitations_are_untouched(text):
    result = guard_reply(text)
    assert not result.changed and result.text == text


def test_jev_persona_is_supported():
    result = guard_reply("I am GPT-4o, made by OpenAI.", persona="Jev")
    assert "Jev" in result.text and not leaked_names(result.text)


def _run_corpus(runtime, probes):
    person = runtime.settings.people[0]
    replies = []
    for n, probe in enumerate(probes):
        for t, turn in enumerate(probe["turns"]):
            answer = asyncio.run(runtime.handle(person, message(turn, message_id=1000 + n * 10 + t)))
            replies.append((probe, turn, answer or ""))
    return replies


def _leaks(replies):
    return [(p["id"], turn, answer, leaked_names(answer)) for p, turn, answer in replies if leaked_names(answer)]


@pytest.fixture
def leaky_runtime(tmp_path):
    model = LeakyModel()
    runtime = make_runtime(tmp_path, adapter=model)
    warm(runtime, runtime.vault.key_for_telegram(runtime.settings.people[0].user_id))
    yield runtime, model
    asyncio.run(runtime.close())


def test_full_pipeline_has_zero_identity_leaks(leaky_runtime):
    runtime, _model = leaky_runtime
    replies = _run_corpus(runtime, CORPUS)
    assert _leaks(replies) == []
    for probe, _turn, answer in replies:
        if probe["vector"] == "direct":
            assert "Jeff" in answer, (probe["id"], answer)


def test_zero_leaks_even_when_every_probe_reaches_the_leaking_model(leaky_runtime, monkeypatch):
    runtime, model = leaky_runtime
    monkeypatch.setattr(rt, "public_guard", lambda text: None)      # input guard off
    monkeypatch.setenv("BOSSMAN_JEFF_J2", "off")                     # optional Jeff 2.0 modules off
    replies = _run_corpus(runtime, CORPUS)
    assert len(model.calls) >= 40
    raw_leaks = sum(1 for n in range(len(model.calls)) if n % 3 != 2)
    assert raw_leaks >= 30, "the fake must really leak"
    assert _leaks(replies) == []
    history = " ".join(str(item.get("content", "")) for item in runtime.store.history(
        runtime.settings.people[0].key))
    assert leaked_names(history) == [], "a leak must not reach the stored history either"


def test_stream_preview_stops_before_showing_a_leak():
    shown: list = []

    async def sink(piece):
        shown.append(piece)

    async def run():
        stream = TurnStream(sink)
        for piece in ("Привет! ", "Я — Nemo", "tron от NVIDIA, ", "и я могу помочь."):
            await stream.on_delta(piece)
        return stream

    stream = asyncio.run(run())
    visible = "".join(p for p in shown if isinstance(p, str))
    assert "Nemotron" not in visible and "NVIDIA" not in visible
    assert stream.leak_stopped and stream.sink is None


def test_stream_check_and_identity_detector_agree_on_the_fixtures():
    for text in LEAKS:
        assert stream_leaks(text) or identity_leak(text) or leaked_names(guard_reply(text).text) == []
