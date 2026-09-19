"""Speech adapter boundaries; fake engine tests are not model-quality evidence."""
import io
import sys
from types import SimpleNamespace
import wave

import pytest

from bcc.oss import whisper


def recording(seconds=1, rate=16000, width=2, channels=1):
    data = io.BytesIO()
    with wave.open(data, "wb") as wav:
        wav.setnchannels(channels)
        wav.setsampwidth(width)
        wav.setframerate(rate)
        wav.writeframes(bytes(int(seconds * rate) * width * channels))
    return data.getvalue()


@pytest.fixture
def model_dir(tmp_path, monkeypatch):
    for filename in ("model.bin", "tokenizer.json", "config.json"):
        (tmp_path / filename).write_bytes(b"test fixture, not model weights")
    monkeypatch.setenv("BOSSMAN_WHISPER_MODEL_PATH", str(tmp_path))
    return tmp_path


def engine(monkeypatch, *, fail=False, timing=(0, 0.5)):
    observed = {}

    class Model:
        def __init__(self, path, **kwargs):
            observed["init"] = kwargs

        def transcribe(self, source, **kwargs):
            observed["options"] = kwargs
            with wave.open(source, "rb") as wav:
                observed["frames"] = wav.getnframes()

            def segments():
                observed["consumed"] = True
                if fail:
                    raise RuntimeError("secret path /home/private/model.bin")
                yield SimpleNamespace(start=timing[0], end=timing[1], text=" Привет, Боссман. ")

            return segments(), SimpleNamespace(language="ru", language_probability=0.97)

    monkeypatch.setitem(sys.modules, "faster_whisper", SimpleNamespace(WhisperModel=Model))
    return observed


def test_lazy_generator_is_consumed_offline_cpu_and_text_is_returned(monkeypatch, model_dir):
    observed = engine(monkeypatch)
    result = whisper.transcribe_audio(recording(), language="ru")
    assert observed["consumed"] is True
    assert observed["init"] == {"device": "cpu", "compute_type": "int8", "cpu_threads": 4,
                                "num_workers": 1, "local_files_only": True}
    assert observed["options"]["language"] == "ru"
    assert result["text"] == "Привет, Боссман."
    assert result["segments"] == [{"start": 0, "end": 0.5, "text": "Привет, Боссман."}]
    assert result["cloud_used"] is False
    assert "model_path" not in result


@pytest.mark.parametrize("audio", [b"", b"https://private.internal/audio", b"M3U\nfile:///etc/passwd",
                                       recording(width=1), recording(rate=96000), recording(seconds=601, rate=8000)],
                         ids=["empty", "url", "playlist", "pcm8", "rate96k", "over10min"])
def test_invalid_or_unbounded_audio_never_loads_model(audio, monkeypatch, model_dir):
    observed = engine(monkeypatch)
    with pytest.raises(whisper.WhisperError):
        whisper.transcribe_audio(audio)
    assert "init" not in observed


def test_truncated_recording_fails_before_native_decoder(monkeypatch, model_dir):
    observed = engine(monkeypatch)
    with pytest.raises(whisper.WhisperError, match="truncated"):
        whisper.transcribe_audio(recording()[:-4])
    assert "init" not in observed


def test_byte_limit(monkeypatch):
    monkeypatch.setattr(whisper, "MAX_AUDIO_BYTES", 8)
    with pytest.raises(whisper.WhisperError, match="32 MiB"):
        whisper.transcribe_audio(recording())


def test_local_tokenizer_required_to_prevent_upstream_download(monkeypatch, model_dir):
    observed = engine(monkeypatch)
    (model_dir / "tokenizer.json").unlink()
    with pytest.raises(whisper.WhisperError, match="tokenizer.json"):
        whisper.transcribe_audio(recording())
    assert "init" not in observed


def test_status_does_not_import_or_claim_verified_inference(monkeypatch, model_dir):
    monkeypatch.setattr(whisper.importlib.util, "find_spec", lambda _: object())
    result = whisper.status()
    assert result["status"] == "configured"
    assert result["inference_verified"] is False
    assert result["download_on_request"] is False
    assert str(model_dir) not in str(result)


def test_hub_id_cannot_trigger_model_download(monkeypatch):
    monkeypatch.setenv("BOSSMAN_WHISPER_MODEL_PATH", "Systran/faster-whisper-small")
    with pytest.raises(whisper.WhisperError, match="absolute local"):
        whisper.transcribe_audio(recording())


def test_engine_failure_redacted_and_releases_work_slot(monkeypatch, model_dir):
    engine(monkeypatch, fail=True)
    with pytest.raises(whisper.WhisperError) as error:
        whisper.transcribe_audio(recording())
    assert "private" not in str(error.value)
    engine(monkeypatch)
    assert whisper.transcribe_audio(recording())["text"]


def test_parallel_request_is_rejected_without_loading(monkeypatch, model_dir):
    observed = engine(monkeypatch)
    with whisper._work_lock:
        with pytest.raises(whisper.WhisperError, match="busy"):
            whisper.transcribe_audio(recording())
    assert "init" not in observed


@pytest.mark.parametrize("timing", [(float("nan"), 1), (0, 400), (-1, 0)])
def test_invalid_model_timestamp_is_rejected(monkeypatch, model_dir, timing):
    engine(monkeypatch, timing=timing)
    with pytest.raises(whisper.WhisperError, match="timing"):
        whisper.transcribe_audio(recording())


@pytest.mark.parametrize("language", ["../../secret", "RU", None, ""])
def test_invalid_language(monkeypatch, model_dir, language):
    observed = engine(monkeypatch)
    with pytest.raises(whisper.WhisperError, match="language"):
        whisper.transcribe_audio(recording(), language=language)
    assert "init" not in observed


def test_optional_upstream_decoder_processes_real_wav():
    # No model weights or downloads; exercises the real optional PyAV path.
    pytest.importorskip("faster_whisper")
    from faster_whisper.audio import decode_audio

    clean, duration = whisper._validated_wav(recording(rate=48000, channels=2))
    samples = decode_audio(io.BytesIO(clean), sampling_rate=16000)
    assert duration == 1
    assert samples.shape == (16000,)
    assert not samples.any()
