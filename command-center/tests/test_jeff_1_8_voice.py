"""Jeff 1.8: pluggable TTS (Piper default, CosyVoice 3 candidate behind a flag), voice latency
metrics and the comparison harness. Fakes only: no voice binary, no weights, no network."""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from bcc.oss.piper import PiperError
from bcc.pit import speech, tts_engines, voice_bench
from bcc.pit.tts_engines import CosyVoiceCandidate, PiperEngine

OGG = b"OggS" + b"\x00" * 60


@pytest.fixture(autouse=True)
def _clean_env(monkeypatch):
    for name in (tts_engines.ENGINE_ENV, tts_engines.FLAG_ENV, tts_engines.CMD_ENV,
                 tts_engines.CONSENT_ENV, "BOSSMAN_PIT_TTS_EXECUTABLE",
                 "BOSSMAN_PIT_TTS_MODEL_PATH"):
        monkeypatch.delenv(name, raising=False)
    speech._tts_state.update(last_engine="", fallbacks=0)


def _files(tmp_path, monkeypatch, *, flag=True, consent=True, command=True, engine="cosyvoice"):
    cmd = tmp_path / "cosy-synth.exe"
    cmd.write_bytes(b"x")
    ffmpeg = tmp_path / "ffmpeg.exe"
    ffmpeg.write_bytes(b"x")
    monkeypatch.setattr(tts_engines.shutil, "which", lambda name: str(ffmpeg))
    if command:
        monkeypatch.setenv(tts_engines.CMD_ENV, str(cmd))
    if flag:
        monkeypatch.setenv(tts_engines.FLAG_ENV, "1")
    if consent:
        path = tmp_path / "consent.json"
        path.write_text(json.dumps({item: True for item in tts_engines.CONSENT_ITEMS}), encoding="utf-8")
        monkeypatch.setenv(tts_engines.CONSENT_ENV, str(path))
    if engine:
        monkeypatch.setenv(tts_engines.ENGINE_ENV, engine)


def test_piper_is_the_default_engine_and_the_legacy_status_contract_is_kept():
    assert tts_engines.selected_engine_name() == "piper"
    assert [engine.name for engine in tts_engines.engine_chain()] == ["piper"]
    status = speech.tts_status()
    assert set(status) >= {"available", "engine", "reason_code", "backend", "candidate"}
    assert status["engine"] == "local" and status["backend"] == "piper"


@pytest.mark.parametrize("kwargs,code", [
    ({"flag": False}, "CANDIDATE_FLAG_OFF"),
    ({"command": False}, "CANDIDATE_NOT_INSTALLED"),
    ({"consent": False}, "CANDIDATE_CONSENT_MISSING"),
])
def test_candidate_is_off_until_flag_command_and_consent_are_all_present(tmp_path, monkeypatch, kwargs, code):
    _files(tmp_path, monkeypatch, **kwargs)
    state = CosyVoiceCandidate().status()
    assert state["available"] is False and state["reason_code"] == code and state["candidate"] is True
    assert [engine.name for engine in tts_engines.engine_chain()] == ["piper"]


def test_incomplete_or_broken_consent_file_keeps_the_candidate_off(tmp_path, monkeypatch):
    _files(tmp_path, monkeypatch)
    consent = Path(tts_engines.os.environ[tts_engines.CONSENT_ENV])
    consent.write_text(json.dumps({"weights_license_checked": True}), encoding="utf-8")
    assert CosyVoiceCandidate().status()["reason_code"] == "CANDIDATE_CONSENT_INCOMPLETE"
    consent.write_text("{not json", encoding="utf-8")
    assert CosyVoiceCandidate().status()["reason_code"] == "CANDIDATE_CONSENT_INVALID"


def test_candidate_is_used_only_when_selected_and_available(tmp_path, monkeypatch):
    _files(tmp_path, monkeypatch, engine="")
    assert CosyVoiceCandidate().status()["available"] is True
    assert [e.name for e in tts_engines.engine_chain()] == ["piper"], "available is not selected"
    monkeypatch.setenv(tts_engines.ENGINE_ENV, "cosyvoice")
    assert [e.name for e in tts_engines.engine_chain()] == ["cosyvoice3", "piper"]


def test_candidate_synthesis_uses_the_command_contract_and_verifies_the_audio(tmp_path, monkeypatch):
    _files(tmp_path, monkeypatch)
    calls = []

    def runner(argv, *, stdin, stopped, timeout):
        calls.append(argv)
        if "--text-file" in argv:
            assert Path(argv[argv.index("--text-file") + 1]).read_text(encoding="utf-8") == "Привет"
        else:
            Path(argv[-1]).write_bytes(OGG)
    monkeypatch.setattr(tts_engines.piper_mod, "_validate_wav", lambda path: None)
    audio = CosyVoiceCandidate(runner=runner).synthesize("Привет", stopped=lambda: False)
    assert audio.startswith(b"OggS")
    assert calls[0][1] == "--text-file" and calls[0][3] == "--out"


def test_failed_candidate_falls_back_to_piper_and_counts_it(tmp_path, monkeypatch):
    _files(tmp_path, monkeypatch)

    def boom(self, text, *, stopped):
        raise PiperError("VOICE_ENGINE_FAILED")
    monkeypatch.setattr(CosyVoiceCandidate, "synthesize", boom)
    audio = speech.run_engines("Привет", piper_synth=lambda text, **kw: OGG)
    assert audio == OGG
    assert speech._tts_state["fallbacks"] == 1 and speech._tts_state["last_engine"] == "piper"


def test_stop_is_never_turned_into_a_fallback(tmp_path, monkeypatch):
    _files(tmp_path, monkeypatch)

    def stopped(self, text, *, stopped):
        raise PiperError("VOICE_STOPPED")
    monkeypatch.setattr(CosyVoiceCandidate, "synthesize", stopped)
    with pytest.raises(PiperError, match="VOICE_STOPPED"):
        speech.run_engines("Привет", piper_synth=lambda text, **kw: OGG)
    assert speech._tts_state["fallbacks"] == 0


def test_tts_latency_is_recorded_for_success_and_failure_but_not_for_stop(monkeypatch):
    before = speech.latency_snapshot("tts")
    speech.run_engines("Привет", piper_synth=lambda text, **kw: OGG)

    def broken(text, **kw):
        raise PiperError("VOICE_ENGINE_FAILED")
    with pytest.raises(PiperError):
        speech.run_engines("Привет", piper_synth=broken)

    def stopped(text, **kw):
        raise PiperError("VOICE_STOPPED")
    with pytest.raises(PiperError):
        speech.run_engines("Привет", piper_synth=stopped)
    after = speech.latency_snapshot("tts")
    assert after["count"] == before["count"] + 2 and after["failures"] == before["failures"] + 1
    assert after["last_engine"] == "piper" and after["p50_ms"] is not None


def test_stt_latency_is_recorded_and_stop_or_silence_is_not_counted(monkeypatch, tmp_path):
    monkeypatch.setattr(speech.whisper, "_validated_wav", lambda audio: (audio, 1.0))
    monkeypatch.setattr(speech.whisper, "_model_directory", lambda: tmp_path)

    class Row:
        text, start, end, avg_logprob, no_speech_prob = "привет", 0.0, 1.0, -0.1, 0.0

    class Model:
        def transcribe(self, *args, **kwargs):
            return [Row()], type("Info", (), {"language": "ru"})()
    monkeypatch.setattr(speech, "_recogniser", lambda path: Model())
    before = speech.latency_snapshot("stt")
    assert speech.transcribe_wav(b"RIFF")["text"] == "привет"
    with pytest.raises(speech.SpeechError):
        speech.transcribe_wav(b"RIFF", stopped=lambda: True)
    after = speech.latency_snapshot("stt")
    assert after["count"] == before["count"] + 1 and after["last_ms"] is not None


def test_tts_engines_module_never_downloads_or_installs_anything():
    source = Path(tts_engines.__file__).read_text(encoding="utf-8")
    for banned in ("urllib", "requests", "httpx", "snapshot_download", "pip install", "git clone",
                   "shell=True"):
        assert banned not in source, banned


# -- comparison harness -------------------------------------------------------------------------
class FakeEngine:
    def __init__(self, name, *, available=True, candidate=False, fail_ids=(), delay=0.0):
        self.name, self.candidate, self.available = name, candidate, available
        self.fail_ids, self.delay, self.spoken = fail_ids, delay, []

    def status(self):
        return {"engine": self.name, "available": self.available,
                "reason_code": None if self.available else "CANDIDATE_NOT_INSTALLED"}

    def synthesize(self, text, *, stopped):
        self.spoken.append(text)
        if any(text == p["text"] and p["id"] in self.fail_ids for p in voice_bench.PHRASES):
            raise PiperError("VOICE_ENGINE_FAILED")
        return text.encode("utf-8")


def test_wer_values():
    assert voice_bench.wer("Привет, мир!", "привет мир") == 0.0
    assert voice_bench.wer("один два три четыре", "один три четыре") == pytest.approx(0.25)
    assert voice_bench.wer("а б", "в г д") == pytest.approx(1.5)
    assert voice_bench.wer("", "") == 0.0 and voice_bench.wer("", "х") == 1.0
    assert voice_bench.wer("Всё ёлка", "все елка") == 0.0


def test_phrase_set_covers_stress_numbers_questions_and_pauses():
    assert {p["category"] for p in voice_bench.PHRASES} == {"stress", "numbers", "questions", "pauses"}
    assert len({p["id"] for p in voice_bench.PHRASES}) == len(voice_bench.PHRASES)


def test_bench_measures_only_installed_engines_and_never_declares_adoption():
    ticks = iter(range(0, 10_000))
    piper = FakeEngine("piper")
    cosy = FakeEngine("cosyvoice3", available=False, candidate=True)
    report = voice_bench.run_bench(
        [piper, cosy], transcribe=lambda audio: audio.decode("utf-8"),
        clock=lambda: next(ticks) / 1000.0, duration_of=lambda audio: 2.0)
    by_name = {e["engine"]: e for e in report["engines"]}
    assert report["schema"] == "bossman.pit.voice-bench/1" and report["adopted"] is False
    assert by_name["piper"]["installed"] is True and by_name["piper"]["wer_mean"] == 0.0
    assert by_name["piper"]["latency"]["count"] == len(voice_bench.PHRASES)
    assert by_name["piper"]["latency"]["p50_ms"] == 1 and by_name["piper"]["rtf_mean"] == pytest.approx(0.0005)
    assert set(by_name["piper"]["wer_by_category"]) == {"stress", "numbers", "questions", "pauses"}
    assert by_name["cosyvoice3"]["installed"] is False and "latency" not in by_name["cosyvoice3"]
    assert by_name["cosyvoice3"]["candidate"] is True and cosy.spoken == []
    assert len(piper.spoken) == len(voice_bench.PHRASES)


def test_bench_survives_a_failing_phrase_and_a_failing_stt():
    engine = FakeEngine("piper", fail_ids={"num-1"})

    def transcribe(audio):
        if audio.decode("utf-8").startswith("Ты уже"):
            raise RuntimeError("stt down")
        return audio.decode("utf-8")
    report = voice_bench.run_bench([engine], transcribe=transcribe)
    entry = report["engines"][0]
    assert entry["failures"]["num-1"] and entry["failures"]["q-1:stt"]
    assert entry["latency"]["failures"] == 1 and entry["wer_mean"] == 0.0


def test_bench_without_stt_reports_latency_only():
    report = voice_bench.run_bench([FakeEngine("piper")], transcribe=None)
    entry = report["engines"][0]
    assert entry["wer_mean"] is None and entry["latency"]["count"] == len(voice_bench.PHRASES)


def test_bench_cli_with_nothing_installed_reports_that_and_exits_zero(tmp_path, monkeypatch, capsys):
    monkeypatch.setattr(voice_bench, "_default_transcribe", lambda: None)
    out = tmp_path / "report.json"
    assert voice_bench.main(["--out", str(out)]) == 0
    data = json.loads(out.read_text(encoding="utf-8"))
    assert data["engines"] and all(e["installed"] is False for e in data["engines"])
    assert json.loads(capsys.readouterr().out)["adopted"] is False
