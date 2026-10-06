"""plugins-oss: /api/oss/status и /api/oss/speech/transcribe — граница без модели.

До 2026-10-06 у `bcc/features/oss_integrations.py` не было ни одного теста.
Здесь — только то, что проверяется без инференса: инвентарь не раскрывает
локальные пути и честно ставит inference_verified=False; загрузка речи
отклоняет неверный размер/язык/формат ДО движка. Валидный WAV сюда не
посылается намеренно: распознавание не запускается.
"""
from __future__ import annotations

import json

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from bcc.features import oss_integrations as O
from bcc.oss import whisper


@pytest.fixture
def client(monkeypatch):
    # до каталога модели (и тем более до движка) неверный ввод доходить не должен
    def _never(*_a, **_k):
        raise AssertionError("неверный ввод дошёл до загрузки модели")
    monkeypatch.setattr(whisper, "_model_directory", _never)
    app = FastAPI()
    app.include_router(O.router, prefix="/api")
    return TestClient(app, raise_server_exceptions=True)


def test_status_inventory_is_honest_and_pathless(monkeypatch, tmp_path):
    monkeypatch.setenv("BOSSMAN_WHISPER_MODEL_PATH", str(tmp_path / "no-such-model"))
    app = FastAPI()
    app.include_router(O.router, prefix="/api")
    r = TestClient(app).get("/api/oss/status")
    assert r.status_code == 200
    body = r.json()
    assert body["inference_verified"] is False
    ids = [i["id"] for i in body["items"]]
    assert len(ids) == len(set(ids))
    assert {"llamacpp", "docling", "qdrant", "whisper", "searxng", "comfyui", "uitars",
            "grapesjs"} <= set(ids)
    for item in body["items"]:
        assert item["state"] in {"integrated", "installed", "configured", "needs_setup",
                                 "invalid_configuration"}
        assert item["upstream"].startswith("https://github.com/")
    assert str(tmp_path) not in json.dumps(body, ensure_ascii=False)


@pytest.mark.parametrize("headers, status", [
    ({"content-length": "abc"}, 400),
    ({"content-length": "-1"}, 413),
    ({"content-length": str(whisper.MAX_AUDIO_BYTES + 1)}, 413),
])
def test_transcribe_rejects_bad_length(client, headers, status):
    r = client.post("/api/oss/speech/transcribe", content=b"x", headers=headers)
    assert r.status_code == status


def test_transcribe_streaming_limit_without_length(client, monkeypatch):
    monkeypatch.setattr(whisper, "MAX_AUDIO_BYTES", 8)

    def chunks():
        yield b"12345"
        yield b"67890"
    r = client.post("/api/oss/speech/transcribe", content=chunks())
    assert r.status_code == 413


@pytest.mark.parametrize("query", ["?language=../x", "?language=EN", "?language=english"])
def test_transcribe_rejects_bad_language(client, query):
    r = client.post("/api/oss/speech/transcribe" + query, content=b"RIFF")
    assert r.status_code == 422


@pytest.mark.parametrize("payload", [b"", b"RIFF", b"RIFFxxxxWAVEjunk", bytes(64)])
def test_transcribe_rejects_non_wav_before_engine(client, payload):
    r = client.post("/api/oss/speech/transcribe", content=payload)
    assert r.status_code == 422
