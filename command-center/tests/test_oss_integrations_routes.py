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


def test_community_oss_integrations_blender_fincept_and_vibe_trading_shadow_account(monkeypatch, tmp_path):
    monkeypatch.setenv("BOSSMAN_WHISPER_MODEL_PATH", str(tmp_path / "no-such-model"))
    app = FastAPI()
    app.include_router(O.router, prefix="/api")
    client = TestClient(app, raise_server_exceptions=True)
    st = client.get("/api/oss/status").json()
    comm_ids = {c["id"] for c in st.get("community_integrations", [])}
    assert {"blender_mcp", "fincept_terminal", "vibe_trading"} == comm_ids

    # 1. MCP for Blender: scene generation & AST security guard on bpy script
    b_ok = client.post("/api/oss/blender/scene", json={"prompt": "Флакон духов на подиуме", "preset": "studio_product"})
    assert b_ok.status_code == 200
    b_body = b_ok.json()
    assert b_body["package"] == "mcp-for-blender" and b_body["script_verified_safe"] is True
    assert "bpy.ops.mesh" in b_body["bpy_script"]

    b_deny = client.post("/api/oss/blender/scene", json={"prompt": "test", "bpy_script": "import os\nos.system('calc')"})
    assert b_deny.status_code == 403 and "T3 DENY" in b_deny.text

    # 2. Fincept Terminal: read-only AGPL-3.0 market analytics vs T3 deny on Enterprise paid / live broker
    f_ok = client.post("/api/oss/fincept/analyze", json={"symbol": "SOL/USDT", "closes": [140.0, 140.2, 139.9, 140.1, 140.0]})
    assert f_ok.status_code == 200
    f_body = f_ok.json()
    assert f_body["license"] == "AGPL-3.0" and f_body["mode"] == "read_only_research"
    assert f_body["regime"] == "CHOP" and f_body["real_money"] == 0

    f_deny_plan = client.post("/api/oss/fincept/analyze", json={"symbol": "SOL/USDT", "edition": "exclusive_pro"})
    assert f_deny_plan.status_code == 403 and "T3 DENY" in f_deny_plan.text
    f_deny_live = client.post("/api/oss/fincept/analyze", json={"symbol": "SOL/USDT", "action": "live_broker_execution"})
    assert f_deny_live.status_code == 403 and "T3 DENY" in f_deny_live.text

    # 3. Vibe-Trading: Shadow Account PAPER_ONLY vs T3 deny on live mode or real_money > 0
    v_ok = client.post("/api/oss/vibe-trading/shadow", json={"symbol": "SOL/USDT", "closes": [140.0, 140.2, 139.9, 140.1, 140.0]})
    assert v_ok.status_code == 200
    v_body = v_ok.json()
    assert v_body["SIMULATED"] is True and v_body["PAPER_ONLY"] is True and v_body["real_money"] == 0
    assert v_body["action_taken"] == "HOLD_CHOP_FILTER" and v_body["equity_end_usd"] == 100.0

    v_deny_live = client.post("/api/oss/vibe-trading/shadow", json={"symbol": "SOL/USDT", "mode": "live"})
    assert v_deny_live.status_code == 403 and "T3 DENY" in v_deny_live.text
    v_deny_money = client.post("/api/oss/vibe-trading/shadow", json={"symbol": "SOL/USDT", "real_money": 50.0})
    assert v_deny_money.status_code == 403 and "T3 DENY" in v_deny_money.text

