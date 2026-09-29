"""The offline self-test scenarios run against the production session code. Each must PASS, and the checks must be able to FAIL."""
from __future__ import annotations

import pytest

from bcc.telegram_calls.call import selftest as st
from bcc.telegram_calls.settings import CallSettings
from bcc.telegram_calls.speech import scripted


def test_wer_is_word_level_and_symmetric_enough():
    assert st.wer("привет босман как дела", "привет босман как дела") == 0.0
    assert st.wer("привет босман как дела", "привет босман") == 0.5
    assert st.wer("", "что-то") == 1.0 and st.wer("", "") == 0.0
    assert st.wer("Ёлка зелёная", "елка зеленая") == 0.5 or st.wer("Ёлка зелёная", "елка зеленая") == 0.0


@pytest.mark.parametrize("name", ["basic", "barge_in", "echo", "stop", "no_redial"])
async def test_scenario_passes_offline(name):
    out = await st.run_selftest(CallSettings(), name)
    res = out["results"][0]
    assert res["verdict"] == "PASS", res
    assert out["evidence_level"] == "loopback" and out["label"] == "ТЕСТ БЕЗ TELEGRAM"
    assert out["engines"].startswith("scripted")                       # the run says it did not use the real models


async def test_basic_reports_latency_and_models_used():
    res = (await st.run_selftest(CallSettings(), "basic"))["results"][0]
    lat = res["metrics"]["latency_ms"]
    assert lat["n"] >= 2 and 0 < lat["p50"] < 5000
    assert set(res["metrics"]["models"]) == {"stt", "llm", "tts"} and res["metrics"]["models"]["stt"].startswith("scripted")


async def test_a_broken_check_is_reported_as_fail_not_swallowed(monkeypatch):
    async def bad(_factory):                                            # negative control: the harness must be able to fail
        raise RuntimeError("boom")
    monkeypatch.setitem(st._RUNNERS, "basic", bad)
    out = await st.run_selftest(CallSettings(), "basic")
    assert out["verdict"] == "FAIL" and out["results"][0]["verdict"] == "FAIL"


async def test_real_mode_is_blocked_not_faked_when_engines_are_scripted():
    async def scripted_factory(settings, mode):
        return st._default_engines()
    out = await st.run_selftest(CallSettings(), "basic", real=True, engines_factory=scripted_factory)
    assert out["verdict"] == "BLOCKED" and out["results"] == []


async def test_unknown_scenario_is_refused():
    with pytest.raises(Exception):
        await st.run_selftest(CallSettings(), "nope")


def test_tone_speech_and_espeak_fallback_shapes():
    pcm = scripted.tone_speech(500, rate_hz=16000)
    assert len(pcm) == 16000 * 500 // 1000 * 2
