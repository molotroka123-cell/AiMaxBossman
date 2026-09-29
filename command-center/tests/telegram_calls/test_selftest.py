"""Offline self-test end to end with fake engines on the loopback transport (fast line). Plumbing only."""
from __future__ import annotations

import asyncio
import json

import pytest

from bcc.telegram_calls.audio.vad import EnergyVAD
from bcc.telegram_calls.call import selftest
from bcc.telegram_calls.call.selftest import PHRASES, wer
from bcc.telegram_calls.speech.testing import ScriptedSTT, ToneTTS


@pytest.fixture(autouse=True)
def no_real_models(monkeypatch):
    for k in ("BOSSMAN_WHISPER_MODEL_PATH", "BOSSMAN_PIPER_VOICE_PATH"):
        monkeypatch.delenv(k, raising=False)


def test_scripted_fallback_end_to_end(tmp_path):
    r = selftest.run(tmp_path)                       # no models in an empty data dir -> scripted engines, fast line
    assert r["code"] == "SELFTEST", r
    assert "ТЕСТ БЕЗ TELEGRAM" in r["verdict"] and r["label"] == "ТЕСТ БЕЗ TELEGRAM"
    assert r["real_call"] is False and r["transport"] == "loopback" and r["engines"]["real_speech_models"] is False
    assert r["engines"]["stt"].startswith("scripted-stt") and r["engines"]["tts"].startswith("tone-tts")
    assert r["turns_answered"] == 10, r["per_turn"]
    assert r["latency_ms"]["n"] >= 10 and r["latency_ms"]["p50"] is not None and r["latency_ms"]["p95"] >= r["latency_ms"]["p50"]
    assert r["wer"]["measured"] is False and r["wer"]["mean"] is None           # scripted STT does not listen
    assert r["status"] == "WARN" and r["ok"] is True                             # plumbing only, never a full PASS
    assert "проводка" in r["latency_meaning"]
    assert "НЕ выполнялся" in r["verdict"]
    assert all(c["status"] in {"PASS", "WARN"} for c in r["checks"]), r["checks"]
    json.dumps(r, ensure_ascii=False)               # JSON-serialisable for API/CLI


def test_never_claims_a_real_call(tmp_path):
    r = selftest.run(tmp_path, turns=2)
    text = json.dumps(r, ensure_ascii=False).lower()
    assert r["real_call"] is False
    assert "реальный звонок выполнен" not in text and "звонок telegram выполнен" not in text
    assert "token" not in text and "api_hash" not in text


def test_injected_listening_stt_measures_wer(tmp_path):
    heard = list(PHRASES[:3])
    heard[1] = heard[1].replace("Расскажи", "Скажи")            # one wrong word out of five
    stt = ScriptedSTT(heard)
    stt.name = "fake-listening-stt"                              # not "scripted-": treated as a real engine
    r = selftest.run(tmp_path, turns=3, engines=(stt, ToneTTS(), EnergyVAD), fast=True)
    assert r["engines"]["real_speech_models"] is True and r["turns_answered"] == 3
    assert r["wer"]["measured"] is True and r["wer"]["n"] == 3
    assert 0 < r["wer"]["mean"] < 0.2
    assert r["status"] == "WARN" and r["latency_ms"]["n"] == 3     # fast line and n<10: never a PASS
    assert any(c["id"] == "latency_sample" and c["status"] == "WARN" for c in r["checks"])


def test_missing_answers_are_blocked_not_hidden(tmp_path):
    stt = ScriptedSTT([""] * 2)                                   # never recognises anything
    r = selftest.run(tmp_path, turns=2, engines=(stt, ToneTTS(), EnergyVAD), fast=True, turn_timeout_s=1.0)
    assert r["status"] == "BLOCKED" and r["ok"] is False and r["turns_answered"] == 0
    assert "ТЕСТ БЕЗ TELEGRAM" in r["verdict"] and "провал" in r["verdict"]


def test_run_inside_running_loop(tmp_path):
    async def go():
        return selftest.run(tmp_path, turns=1)               # blocking call made on the loop thread: must still work

    assert asyncio.run(go())["code"] == "SELFTEST"


def test_wer_function():
    assert wer("привет, как слышно?", "Привет как слышно") == 0.0          # case and punctuation are ignored
    assert wer("один два три четыре", "один три четыре") == 0.25            # one deletion
    assert wer("один два", "") == 1.0                                       # empty hypothesis is a full error
    assert wer("", "") == 0.0
