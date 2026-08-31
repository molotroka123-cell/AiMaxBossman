"""V2.6 раздел 20 — voice_capability: честный probe STT/TTS по tools/registry.yaml.

Ключевой инвариант — честность: запись в реестре есть, а бинаря нет →
available=False с пометкой «binary not found»; нет самого реестра → обе
способности False без исключения. Для `sh -c 'echo … | X …'` проверяется
именно X (потребитель пайпа), а не echo.
"""
from __future__ import annotations

from bossman import voice_capability
from bossman.config import settings


def _registry(tmp_path, monkeypatch, text: str):
    path = tmp_path / "registry.yaml"
    path.write_text(text, encoding="utf-8")
    monkeypatch.setattr(settings, "tools_registry", path, raising=False)
    return path


def test_missing_binary_is_honestly_unavailable(tmp_path, monkeypatch):
    _registry(tmp_path, monkeypatch, """
tools:
  whisper_local:
    kind: cmd
    where: home
    can: [subtitles, transcribe]
    cmd: "definitely-missing-binary-xyz -m /opt/models/w.gguf -f {input} -of {out}"
""")
    cap = voice_capability.probe()
    assert cap.stt_available is False
    assert cap.stt_provider is None
    d = cap.details["whisper_local"]
    assert d["available"] is False
    assert d["binary"] == "definitely-missing-binary-xyz"
    assert "binary not found" in d["note"]


def test_sh_c_pipe_checks_piped_target_not_echo(tmp_path, monkeypatch):
    """Как piper_local в боевом реестре: бинарь — потребитель пайпа.
    python3 в тестовом окружении есть → available=True."""
    _registry(tmp_path, monkeypatch, """
tools:
  fake_tts:
    kind: cmd
    where: home
    can: [tts, voiceover]
    quality: 7
    cmd: "sh -c 'echo {text} | python3 /opt/tts.py -f {out}'"
""")
    cap = voice_capability.probe()
    assert cap.tts_available is True
    assert cap.tts_provider == "fake_tts"
    assert cap.details["fake_tts"]["binary"] == "python3"
    assert cap.details["fake_tts"]["available"] is True


def test_no_registry_file_means_no_voice_no_exception(tmp_path, monkeypatch):
    monkeypatch.setattr(settings, "tools_registry",
                        tmp_path / "нет-такого.yaml", raising=False)
    cap = voice_capability.probe()
    assert cap.stt_available is False and cap.tts_available is False
    assert cap.stt_provider is None and cap.tts_provider is None
    assert "registry" in cap.details


def test_mixed_registry_stt_missing_tts_present(tmp_path, monkeypatch):
    _registry(tmp_path, monkeypatch, """
tools:
  whisper_local:
    can: [subtitles, transcribe]
    cmd: "whisper-cli-точно-нет -f {input}"
  piper_like:
    can: [tts, voiceover]
    cmd: "sh -c 'echo {text} | python3 - -f {out}'"
  seedance:
    can: [t2v, i2v]
""")
    cap = voice_capability.probe()
    assert cap.stt_available is False
    assert cap.tts_available is True and cap.tts_provider == "piper_like"
    assert "seedance" not in cap.details  # не голосовая запись — не трогаем


def test_direct_cmd_binary_available(tmp_path, monkeypatch):
    _registry(tmp_path, monkeypatch, """
tools:
  py_stt:
    can: [transcribe]
    quality: 3
    cmd: "python3 /opt/stt.py -f {input}"
""")
    cap = voice_capability.probe()
    assert cap.stt_available is True and cap.stt_provider == "py_stt"


def test_best_quality_provider_wins(tmp_path, monkeypatch):
    _registry(tmp_path, monkeypatch, """
tools:
  tts_low:
    can: [tts]
    quality: 2
    cmd: "python3 low.py"
  tts_high:
    can: [voiceover]
    quality: 9
    cmd: "python3 high.py"
""")
    cap = voice_capability.probe()
    assert cap.tts_available is True
    assert cap.tts_provider == "tts_high"


def test_entry_without_cmd_is_unavailable_not_crash(tmp_path, monkeypatch):
    _registry(tmp_path, monkeypatch, """
tools:
  ghost_tts:
    can: [tts]
""")
    cap = voice_capability.probe()
    assert cap.tts_available is False
    assert cap.details["ghost_tts"]["available"] is False
