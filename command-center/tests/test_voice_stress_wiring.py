"""The stress setting inside the cloned-voice path; no model, no network."""
from __future__ import annotations

from pathlib import Path
import wave

import pytest

from bcc.oss import chatterbox_clone as clone
from bcc.oss import ru_stress as rs
from bcc.oss.piper import PiperError

ACUTE = rs.ACUTE


def _assets(tmp_path: Path):
    python = tmp_path / "python.exe"
    python.write_bytes(b"stub")
    ffmpeg = tmp_path / "ffmpeg.exe"
    ffmpeg.write_bytes(b"stub")
    model = tmp_path / "model"
    model.mkdir(exist_ok=True)
    for name in clone.MODEL_FILES:
        (model / name).write_bytes(b"stub")
    reference = tmp_path / "ref.wav"
    with wave.open(str(reference), "wb") as audio:
        audio.setnchannels(1)
        audio.setsampwidth(2)
        audio.setframerate(24000)
        audio.writeframes(b"\x00\x00" * 24000 * 6)
    stress_python = tmp_path / "stress-python.exe"
    stress_python.write_bytes(b"stub")
    stress_models = tmp_path / "stress-models"
    stress_models.mkdir(exist_ok=True)
    return python, ffmpeg, model, reference, stress_python, stress_models


@pytest.fixture(autouse=True)
def _clean(monkeypatch):
    for name in (clone.STRESS_ENV, clone.STRESS_PYTHON_ENV, clone.STRESS_MODEL_DIR_ENV,
                 clone.STRESS_CACHE_DIR_ENV):
        monkeypatch.delenv(name, raising=False)
    rs.clear_cache()
    yield
    rs.clear_cache()


def _capture(monkeypatch):
    sent = []

    def run(argv, *, stdin, stopped, timeout):
        sent.append(stdin)
        if argv[0].endswith("python.exe") and "chatterbox_worker" in argv[1]:
            with wave.open(argv[4], "wb") as audio:
                audio.setnchannels(1)
                audio.setsampwidth(2)
                audio.setframerate(24000)
                audio.writeframes(b"\x00\x00" * 24000)
        else:
            Path(argv[-1]).write_bytes(b"OggS" + b"x" * 100)

    monkeypatch.setattr(clone, "_run", run)
    return sent


def _synth(tmp_path, text, **kwargs):
    python, ffmpeg, model, reference, *_ = _assets(tmp_path)
    return clone.synthesize_ogg(text, python_executable=python, model_dir=model,
                                reference_path=reference, ffmpeg_executable=ffmpeg, **kwargs)


def test_default_setting_is_a_valid_mode():
    assert clone.DEFAULT_STRESS_MODE in rs.STRESS_MODES
    assert clone.stress_mode_setting() == clone.DEFAULT_STRESS_MODE


def test_env_overrides_default_and_garbage_is_rejected(monkeypatch):
    monkeypatch.setenv(clone.STRESS_ENV, "ACUTE")
    assert clone.stress_mode_setting() == "acute"
    assert clone.stress_mode_setting("off") == "off"
    monkeypatch.setenv(clone.STRESS_ENV, "loud")
    with pytest.raises(PiperError):
        clone.stress_mode_setting()


def test_off_sends_the_original_text_untouched(tmp_path, monkeypatch):
    sent = _capture(monkeypatch)
    _synth(tmp_path, "Через 3 минуты, т.е. скоро.", stress_mode="off")
    assert sent[0] == "Через 3 минуты, т.е. скоро.".encode()


def test_acute_mode_sends_normalized_stressed_text(tmp_path, monkeypatch):
    sent = _capture(monkeypatch)
    *_, stress_python, stress_models = _assets(tmp_path)
    monkeypatch.setenv(clone.STRESS_PYTHON_ENV, str(stress_python))
    monkeypatch.setenv(clone.STRESS_MODEL_DIR_ENV, str(stress_models))
    seen = []

    def accentizer(argv, *, stdin, stopped, timeout):
        seen.append(stdin.decode())
        Path(argv[-1]).write_text("Чер+ез три мин+уты зам+ок", encoding="utf-8")

    monkeypatch.setattr(rs, "_default_runner", accentizer)
    _synth(tmp_path, "Через 3 минуты замок", stress_mode="acute")
    assert seen == ["Через три минуты замок"]  # numbers are words BEFORE stressing
    assert sent[0].decode() == ("Чере" + ACUTE + "з три мину" + ACUTE + "ты замо" + ACUTE + "к")


def test_missing_accentizer_falls_back_to_normalized_text(tmp_path, monkeypatch):
    sent = _capture(monkeypatch)
    _synth(tmp_path, "Через 3 минуты", stress_mode="acute")
    assert sent[0].decode() == "Через три минуты"


def test_accentizer_crash_never_blocks_the_voice(tmp_path, monkeypatch):
    sent = _capture(monkeypatch)
    *_, stress_python, stress_models = _assets(tmp_path)
    monkeypatch.setenv(clone.STRESS_PYTHON_ENV, str(stress_python))
    monkeypatch.setenv(clone.STRESS_MODEL_DIR_ENV, str(stress_models))

    def crash(argv, *, stdin, stopped, timeout):
        raise PiperError("VOICE_TIMEOUT")

    monkeypatch.setattr(rs, "_default_runner", crash)
    assert _synth(tmp_path, "Привет, друг", stress_mode="acute").startswith(b"OggS")
    assert sent[0] == "Привет, друг".encode()


def test_owner_stop_during_accentizer_wins(tmp_path, monkeypatch):
    _capture(monkeypatch)
    *_, stress_python, stress_models = _assets(tmp_path)
    monkeypatch.setenv(clone.STRESS_PYTHON_ENV, str(stress_python))
    monkeypatch.setenv(clone.STRESS_MODEL_DIR_ENV, str(stress_models))

    def stopped_run(argv, *, stdin, stopped, timeout):
        raise PiperError("VOICE_STOPPED")

    monkeypatch.setattr(rs, "_default_runner", stopped_run)
    with pytest.raises(PiperError, match="VOICE_STOPPED"):
        _synth(tmp_path, "Привет, друг", stress_mode="acute")


def test_input_limit_stays_280_but_spoken_limit_allows_expansion(tmp_path, monkeypatch):
    _capture(monkeypatch)
    with pytest.raises(PiperError):
        _synth(tmp_path, "а" * 281, stress_mode="off")
    assert clone.MAX_SPOKEN_CHARS > 280
