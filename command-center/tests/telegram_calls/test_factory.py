"""speech.factory: build_engines wiring/errors and the doctor probe. Fake models, tmp dirs, no network."""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from bcc.telegram_calls.audio.vad import EnergyVAD
from bcc.telegram_calls.speech import factory
from bcc.telegram_calls.speech.factory import SpeechConfig, build_engines, doctor, pick_piper_voice, pick_whisper_dir
from bcc.telegram_calls.speech.testing import ScriptedBrain, ScriptedSTT, ToneTTS
from bcc.telegram_calls.speech.stt import FasterWhisperSTT
from bcc.telegram_calls.speech.tts import PiperTTS
from bcc.telegram_calls.types import CallError

from .fakes_b import FakeLLMServer


@pytest.fixture(autouse=True)
def clean_env(monkeypatch):
    for k in ("BOSSMAN_WHISPER_MODEL_PATH", "BOSSMAN_PIPER_VOICE_PATH", "BOSSMAN_TG_COMPANION_CONFIG"):
        monkeypatch.delenv(k, raising=False)


def make_models(data_dir: Path, *, whisper=True, piper=True, sub="whisper-small"):
    base = data_dir / "addons" / "telegram-calls" / "models"
    if whisper:
        d = base / "whisper" / sub
        d.mkdir(parents=True)
        for f in ("model.bin", "config.json", "tokenizer.json"):
            (d / f).write_bytes(b"x")
    if piper:
        p = base / "piper"
        p.mkdir(parents=True)
        (p / "ru_RU-irina-medium.onnx").write_bytes(b"o")
        (p / "ru_RU-irina-medium.onnx.json").write_text(json.dumps({"audio": {"sample_rate": 22050},
                                                                     "language": {"code": "ru_RU"}}), encoding="utf-8")
        (p / "en_US-amy.onnx").write_bytes(b"o")
        (p / "en_US-amy.onnx.json").write_text(json.dumps({"audio": {"sample_rate": 22050}}), encoding="utf-8")
    return base


def companion_config(tmp_path, url="http://127.0.0.1:9/v1", model="local-fake"):
    d = tmp_path / "companion"
    d.mkdir()
    p = d / "config.json"
    p.write_text(json.dumps({"people": [{"user_id": 5, "chat_id": 5, "role": "owner"}], "local_url": url,
                             "local_model": model, "core_url": "http://127.0.0.1:8800"}), encoding="utf-8")
    return p


def fake_pkgs(monkeypatch, stt=True, tts=True):
    monkeypatch.setattr(FasterWhisperSTT, "_package_installed", lambda self: stt)
    monkeypatch.setattr(PiperTTS, "_package_installed", lambda self: tts)


# ------------------------------------------------------------------ config
def test_coerce_accepts_path_dict_object_none(tmp_path):
    assert SpeechConfig.coerce(None).data_dir is None
    assert SpeechConfig.coerce(tmp_path).data_dir == tmp_path
    assert SpeechConfig.coerce(str(tmp_path)).data_dir == tmp_path
    c = SpeechConfig.coerce({"data_dir": str(tmp_path), "vad": "energy", "junk": 1, "brain_route": "main"})
    assert c.vad == "energy" and c.brain_route == "main" and c.data_dir == tmp_path
    same = SpeechConfig(vad="silero")
    assert SpeechConfig.coerce(same) is same

    class Obj:
        data_dir = str(tmp_path)
        preload = False
    o = SpeechConfig.coerce(Obj())
    assert o.data_dir == tmp_path and o.preload is False
    assert SpeechConfig.coerce(tmp_path).models == tmp_path / "addons" / "telegram-calls" / "models"


def test_discovery_prefers_explicit_then_env_then_addon_dir(tmp_path, monkeypatch):
    make_models(tmp_path)
    cfg = SpeechConfig(data_dir=tmp_path)
    w = pick_whisper_dir(cfg)
    assert w is not None and w.name == "whisper-small"
    assert pick_piper_voice(cfg).name == "ru_RU-irina-medium.onnx"          # ru* voice wins over en
    explicit = tmp_path / "mine"
    assert pick_whisper_dir(SpeechConfig(data_dir=tmp_path, whisper_model_dir=explicit)) == explicit
    monkeypatch.setenv("BOSSMAN_WHISPER_MODEL_PATH", str(explicit))
    monkeypatch.setenv("BOSSMAN_PIPER_VOICE_PATH", str(explicit))
    assert pick_whisper_dir(cfg) is None and pick_piper_voice(cfg) is None   # env wins: the engines read it themselves


def test_discovery_skips_incomplete_models(tmp_path):
    base = make_models(tmp_path, whisper=False, piper=False)
    bad = base / "whisper" / "half"
    bad.mkdir(parents=True)
    (bad / "model.bin").write_bytes(b"x")
    (base / "piper").mkdir(parents=True)
    (base / "piper" / "lonely.onnx").write_bytes(b"o")                     # no .onnx.json
    cfg = SpeechConfig(data_dir=tmp_path)
    assert pick_whisper_dir(cfg) is None and pick_piper_voice(cfg) is None


# ------------------------------------------------------------------ build_engines
def test_build_engines_returns_real_engines_and_preloads(tmp_path, monkeypatch):
    make_models(tmp_path)
    fake_pkgs(monkeypatch)
    loaded = []
    monkeypatch.setattr(FasterWhisperSTT, "_load_sync", lambda self: loaded.append("stt"))
    monkeypatch.setattr(PiperTTS, "_load_sync", lambda self: loaded.append("tts"))
    with FakeLLMServer() as srv:
        cc = companion_config(tmp_path, srv.url, srv.model)
        stt, tts, brain, vad_factory = build_engines(SpeechConfig(data_dir=tmp_path, companion_config=cc, vad="energy"))
    assert isinstance(stt, FasterWhisperSTT) and isinstance(tts, PiperTTS) and stt.model == "whisper-small"
    assert tts.voice == "ru_RU-irina-medium" and brain.model == srv.model and loaded == ["stt", "tts"]
    v = vad_factory()
    assert isinstance(v, EnergyVAD) and v is not vad_factory()             # a fresh detector per call


def test_build_engines_accepts_the_worker_call_shape(tmp_path, monkeypatch):
    make_models(tmp_path)
    fake_pkgs(monkeypatch)
    monkeypatch.setattr(FasterWhisperSTT, "_load_sync", lambda self: None)
    monkeypatch.setattr(PiperTTS, "_load_sync", lambda self: None)
    cc = companion_config(tmp_path)
    monkeypatch.setenv("BOSSMAN_TG_COMPANION_CONFIG", str(cc))
    stt, tts, brain, _ = build_engines(tmp_path)                            # worker passes the data dir
    assert stt.status()["ok"] and tts.status()["ok"] and brain.route == "main"


@pytest.mark.parametrize("missing,code", [("stt", "STT_UNAVAILABLE"), ("tts", "TTS_UNAVAILABLE")])
def test_build_engines_fails_with_the_right_code_no_silent_substitute(tmp_path, monkeypatch, missing, code):
    make_models(tmp_path, whisper=missing != "stt", piper=missing != "tts")
    fake_pkgs(monkeypatch)
    monkeypatch.setattr(FasterWhisperSTT, "_load_sync", lambda self: None)
    monkeypatch.setattr(PiperTTS, "_load_sync", lambda self: None)
    with pytest.raises(CallError) as e:
        build_engines(SpeechConfig(data_dir=tmp_path, companion_config=companion_config(tmp_path)))
    assert e.value.code == code


def test_build_engines_missing_package_is_unavailable(tmp_path, monkeypatch):
    make_models(tmp_path)
    fake_pkgs(monkeypatch, stt=False)
    with pytest.raises(CallError) as e:
        build_engines(SpeechConfig(data_dir=tmp_path, companion_config=companion_config(tmp_path)))
    assert e.value.code == "STT_UNAVAILABLE"


def test_build_engines_without_companion_model_is_brain_not_configured(tmp_path, monkeypatch):
    make_models(tmp_path)
    fake_pkgs(monkeypatch)
    cc = companion_config(tmp_path, model="")
    with pytest.raises(CallError) as e:
        build_engines(SpeechConfig(data_dir=tmp_path, companion_config=cc))
    assert e.value.code == "BRAIN_NOT_CONFIGURED"


def test_preload_failure_surfaces_before_the_call(tmp_path, monkeypatch):
    make_models(tmp_path)
    fake_pkgs(monkeypatch)

    def boom(self):
        raise CallError("STT_UNAVAILABLE", detail="OSError")

    monkeypatch.setattr(FasterWhisperSTT, "_load_sync", boom)
    with pytest.raises(CallError) as e:
        build_engines(SpeechConfig(data_dir=tmp_path, companion_config=companion_config(tmp_path)))
    assert e.value.code == "STT_UNAVAILABLE" and e.value.detail == "OSError"
    monkeypatch.setattr(FasterWhisperSTT, "_load_sync", lambda self: None)     # legit control
    monkeypatch.setattr(PiperTTS, "_load_sync", lambda self: None)
    assert len(build_engines(SpeechConfig(data_dir=tmp_path, companion_config=tmp_path / "companion" / "config.json"))) == 4


def test_injected_engines_bypass_real_ones_for_selftest(tmp_path):
    stt, tts, brain = ScriptedSTT(["привет"]), ToneTTS(), ScriptedBrain(["Здравствуйте."])
    out = build_engines(SpeechConfig(preload=False, vad="energy"), stt=stt, tts=tts, brain=brain)
    assert out[0] is stt and out[1] is tts and out[2] is brain


def test_scripted_engines_are_not_returned_by_the_real_build(tmp_path, monkeypatch):
    make_models(tmp_path)
    fake_pkgs(monkeypatch)
    monkeypatch.setattr(FasterWhisperSTT, "_load_sync", lambda self: None)
    monkeypatch.setattr(PiperTTS, "_load_sync", lambda self: None)
    stt, tts, brain, _ = build_engines(SpeechConfig(data_dir=tmp_path, companion_config=companion_config(tmp_path)))
    assert not isinstance(stt, ScriptedSTT) and not isinstance(tts, ToneTTS) and not isinstance(brain, ScriptedBrain)
    assert stt.status().get("synthetic") is None


# ------------------------------------------------------------------ doctor
def test_doctor_all_available(tmp_path, monkeypatch):
    make_models(tmp_path)
    fake_pkgs(monkeypatch)
    monkeypatch.setattr("bcc.telegram_calls.call.pytgcalls_transport.dependencies_status", lambda: {"ok": True})
    monkeypatch.setattr(factory, "make_vad", lambda kind="auto": EnergyVAD() if kind == "energy" else _Silero())
    with FakeLLMServer() as srv:
        cc = companion_config(tmp_path, srv.url, srv.model)
        rep = doctor(SpeechConfig(data_dir=tmp_path, companion_config=cc), probe_brain=True)
    assert rep["verdict"] == "PASS" and rep["ok"] is True
    assert {i["name"]: i["level"] for i in rep["items"]} == {"stt": "PASS", "tts": "PASS", "brain": "PASS",
                                                             "vad": "PASS", "transport": "PASS"}
    assert rep["engines"]["brain"]["reachable"] is True and rep["engines"]["stt"]["loaded"] is False


class _Silero:
    name, degraded = "silero-vad-v6", False


def test_doctor_reports_blocked_with_remedy_per_engine(tmp_path, monkeypatch):
    fake_pkgs(monkeypatch, stt=False, tts=False)
    monkeypatch.setattr("bcc.telegram_calls.call.pytgcalls_transport.dependencies_status", lambda: {"ok": False})
    rep = doctor(SpeechConfig(data_dir=tmp_path, companion_config=tmp_path / "missing.json", vad="energy"))
    by = {i["name"]: i for i in rep["items"]}
    assert rep["verdict"] == "BLOCKED" and rep["ok"] is False
    for name, code in (("stt", "STT_UNAVAILABLE"), ("tts", "TTS_UNAVAILABLE"), ("brain", "BRAIN_NOT_CONFIGURED"),
                       ("transport", "DEPENDENCIES_MISSING")):
        assert by[name]["level"] == "BLOCKED" and by[name]["code"] == code and by[name]["remedy"]
    assert by["vad"]["level"] == "WARN"                                       # energy fallback = degraded, not blocked
    assert rep["engines"]["brain"]["cloud_used"] is False


def test_doctor_warns_on_degraded_vad_only(tmp_path, monkeypatch):
    make_models(tmp_path)
    fake_pkgs(monkeypatch)
    monkeypatch.setattr("bcc.telegram_calls.call.pytgcalls_transport.dependencies_status", lambda: {"ok": True})
    rep = doctor(SpeechConfig(data_dir=tmp_path, companion_config=companion_config(tmp_path), vad="energy"))
    assert rep["verdict"] == "WARN" and rep["ok"] is True
    assert [i["name"] for i in rep["items"] if i["level"] == "WARN"] == ["vad"]


def test_doctor_unreachable_brain_is_blocked_when_probed(tmp_path, monkeypatch):
    make_models(tmp_path)
    fake_pkgs(monkeypatch)
    monkeypatch.setattr("bcc.telegram_calls.call.pytgcalls_transport.dependencies_status", lambda: {"ok": True})
    rep = doctor(SpeechConfig(data_dir=tmp_path, companion_config=companion_config(tmp_path, "http://127.0.0.1:1/v1"),
                              vad="energy"), probe_brain=True)
    brain = next(i for i in rep["items"] if i["name"] == "brain")
    assert brain["level"] == "BLOCKED" and rep["engines"]["brain"]["reachable"] is False


def test_doctor_never_loads_models(tmp_path, monkeypatch):
    make_models(tmp_path)
    fake_pkgs(monkeypatch)

    def forbidden(self):
        raise AssertionError("doctor must not load models")

    monkeypatch.setattr(FasterWhisperSTT, "_load_sync", forbidden)
    monkeypatch.setattr(PiperTTS, "_load_sync", forbidden)
    rep = doctor(SpeechConfig(data_dir=tmp_path, companion_config=companion_config(tmp_path), vad="energy"))
    assert rep["engines"]["stt"]["ok"] is True and rep["engines"]["tts"]["ok"] is True
