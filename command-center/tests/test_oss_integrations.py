"""Owner-facing OSS routes: auth, truthful readiness and bounded uploads."""
import httpx
import pytest

from bcc.oss import whisper


async def test_inventory_requires_auth_and_never_claims_inference(env, monkeypatch):
    app, svc, client = env.app, env.svc, env.client
    monkeypatch.setenv("BOSSMAN_COMFYUI_URL", "http://127.0.0.1:8188")
    monkeypatch.setenv("BOSSMAN_COMFYUI_CHECKPOINT", "private-model.safetensors")
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as guest:
        assert (await guest.get("/api/oss/status")).status_code == 401
        assert (await guest.post("/api/oss/speech/transcribe", content=b"test")).status_code == 401
    response = await client.get("/api/oss/status")
    assert response.status_code == 200
    data = response.json()
    assert len(data["items"]) == 8
    assert data["inference_verified"] is False
    assert "private-model" not in response.text
    assert next(x for x in data["items"] if x["id"] == "comfyui")["state"] == "configured"
    monkeypatch.setenv("BOSSMAN_COMFYUI_URL", "https://invalid.example")
    bad = (await client.get("/api/oss/status")).json()
    assert next(x for x in bad["items"] if x["id"] == "comfyui")["state"] == "invalid_configuration"


async def test_transcription_returns_engine_text_and_errors(env, monkeypatch):
    client = env.client
    seen = []
    def transcribe(audio, *, language):
        seen.append((audio, language))
        return {"text": "Запись готова", "segments": [], "cloud_used": False}
    monkeypatch.setattr(whisper, "transcribe_audio", transcribe)
    response = await client.post("/api/oss/speech/transcribe?language=ru", content=b"wav-payload")
    assert response.status_code == 200
    assert response.json()["text"] == "Запись готова"
    assert seen == [(b"wav-payload", "ru")]
    def failed(*args, **kwargs):
        raise whisper.WhisperError("Local model unavailable")
    monkeypatch.setattr(whisper, "transcribe_audio", failed)
    response = await client.post("/api/oss/speech/transcribe", content=b"audio")
    assert response.status_code == 422
    assert "Local model unavailable" in response.text


async def test_upload_is_bounded_without_content_length(env, monkeypatch):
    client = env.client
    monkeypatch.setattr(whisper, "MAX_AUDIO_BYTES", 8)
    def never(*args, **kwargs):
        pytest.fail("Oversized upload reached model")
    monkeypatch.setattr(whisper, "transcribe_audio", never)
    async def chunks():
        yield b"12345"
        yield b"6789"
    assert (await client.post("/api/oss/speech/transcribe", content=chunks())).status_code == 413
    assert (await client.post("/api/oss/speech/transcribe", content=b"123456789")).status_code == 413


async def test_malformed_riff_returns_safe_client_error(env):
    import struct
    body = b"RIFF" + struct.pack("<I", 36) + b"WAVEJUNK" + struct.pack("<I", 0xffffffff)
    response = await env.client.post("/api/oss/speech/transcribe", content=body)
    assert response.status_code == 422
    assert "Traceback" not in response.text
