"""Tier-1 Step (2): «Gemma по запросу»: промпт → запуск фото → выгрузка модели.

Verifies:
1. DirectGenService prefers local uncensored Gemma for on-demand prompt assist, rewrites the prompt,
   and immediately unloads the model from Ollama (keep_alive=0) so VRAM is freed before photo generation.
2. Launching a photo job in bossman-mobile (with or without on-demand Gemma prompt refinement)
   unloads any loaded Ollama models (keep_alive=0) before the photo job starts on the GPU.
"""
from __future__ import annotations

import json
import httpx
import pytest

from bossmobile.api import create_app
from bossmobile.config import Settings
from conftest import BOSS_TOKEN, MODELS, OPTIONS, PAIR, PROVIDERS


def test_mobile_gemma_on_demand_prompt_to_photo_unloads_model(tmp_path):
    """End-to-end flow: on-demand Gemma refines prompt -> launches photo job -> unloads Gemma (keep_alive=0)."""
    ollama_loaded = ["gemma4-uncensored:latest"]
    ollama_unload_calls: list[dict] = []
    bossman_calls: list[dict] = []

    def ollama_handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/api/ps":
            return httpx.Response(200, json={"models": [{"name": m} for m in ollama_loaded]})
        if request.url.path == "/api/tags":
            return httpx.Response(200, json={"models": [{"name": "gemma4-uncensored:latest", "size": 9_000_000_000}]})
        if request.url.path == "/api/generate":
            body = json.loads(request.content.decode("utf-8"))
            ollama_unload_calls.append(body)
            if body.get("keep_alive") == 0 and body.get("model") in ollama_loaded:
                ollama_loaded.remove(body["model"])
            return httpx.Response(200, json={"done": True})
        return httpx.Response(404)

    def bossman_handler(request: httpx.Request) -> httpx.Response:
        path = request.url.path
        body = json.loads(request.content.decode("utf-8")) if request.content else None
        bossman_calls.append({"method": request.method, "path": path, "body": body})
        if path == "/api/login":
            return httpx.Response(
                200,
                json={"ok": True, "csrf": "UPSTREAM-CSRF-123"},
                headers={"set-cookie": "bcc_session=UPSTREAM-SID-123; HttpOnly; Path=/; SameSite=Strict"},
            )
        if path == "/api/direct-gen/models":
            return httpx.Response(200, json={"models": [{
                "id": "z-image-turbo", "label": "Z-Image Turbo", "kind": "photo", "family": "z-image",
                "modes": ["T2I"], "available": True, "reason": "", "requires_image": False, "accepts_image": False,
                "max_refs": 0, "default_steps": 8, "max_steps": 20, "default_resolution": "512x512",
                "min_side": 256, "max_side": 1536, "side_multiple": 16,
            }]})
        if path == "/api/direct-gen/assist" and request.method == "POST":
            return httpx.Response(200, json={
                "assist_id": "a" * 32,
                "original": body["prompt"],
                "suggested": f"{body['prompt']}, кинематографичный свет, 85mm, высокая детализация",
                "model": "gemma4-uncensored:latest",
                "unloaded": True,
            })
        if path == "/api/direct-gen/jobs" and request.method == "POST":
            return httpx.Response(202, json={
                "job_id": "b" * 32,
                "model": body["model"],
                "kind": "photo",
                "status": "queued",
                "stage": "queued",
                "raw_prompt": body["prompt"],
                "effective_prompt": body["prompt"],
                "params": {"width": 512, "height": 512, "steps": 8, "seed": 42},
                "progress": {"kind": "none"},
            })
        if path == "/api/direct-gen/jobs" and request.method == "GET":
            return httpx.Response(200, json={"jobs": []})
        if path == "/api/direct-gen/timings":
            return httpx.Response(200, json={"records": []})
        return httpx.Response(200, json={})

    token_file = tmp_path / "token"
    token_file.write_text(BOSS_TOKEN)
    st = Settings(data_dir=tmp_path / "data", token_file=token_file)
    app = create_app(
        st,
        bossman_transport=httpx.MockTransport(bossman_handler),
        ollama_transport=httpx.MockTransport(ollama_handler),
        pairing_token=PAIR,
    )
    from starlette.testclient import TestClient
    with TestClient(app) as c:
        login = c.post("/m/api/login", json={"token": PAIR})
        assert login.status_code == 200
        csrf = login.json()["csrf"]
        headers = {"X-BM-CSRF": csrf}

        # Step 1: On-demand Gemma prompt refinement -> photo job -> immediate Ollama unload
        r = c.post("/m/api/photo/jobs", headers=headers, json={
            "model": "z-image-turbo",
            "prompt": "кот в скафандре на Луне",
            "assist": True,
        })
        assert r.status_code == 202, r.text
        data = r.json()
        # Prompt was refined by Gemma before launching photo job
        assert "кинематографичный свет" in data["raw_prompt"]
        assert data.get("assist_model") == "gemma4-uncensored:latest"
        # Gemma was immediately unloaded from Ollama (keep_alive=0) so 0 models remain in VRAM
        assert len(ollama_unload_calls) >= 1
        assert ollama_unload_calls[0]["model"] == "gemma4-uncensored:latest"
        assert ollama_unload_calls[0]["keep_alive"] == 0
        assert ollama_loaded == [], "Gemma must be unloaded from VRAM before photo generation runs"


@pytest.mark.asyncio
async def test_direct_gen_service_prefers_gemma_and_unloads_after_assist(tmp_path, monkeypatch):
    """DirectGenService.assist() picks local uncensored Gemma first and unloads it (keep_alive=0) immediately."""
    import sys
    from pathlib import Path
    cc_dir = str(Path(__file__).resolve().parents[3] / "command-center")
    if cc_dir not in sys.path:
        sys.path.insert(0, cc_dir)
    from types import SimpleNamespace
    from bcc.direct_gen.service import DirectGenService

    unloaded_models: list[str] = []

    class FakeAdapter:
        async def chat(self, model_name, messages):
            return SimpleNamespace(text="Кот в скафандре на поверхности Луны, мягкий контровой свет Земли, 85mm")

    class FakeRegistry:
        async def list_providers(self):
            return [{"id": 1, "name": "ollama-local", "base_url": "http://127.0.0.1:11434"}]

        async def list_models(self):
            return [
                {"id": 10, "name": "qwen2.5:14b", "alias": "Qwen 14B", "provider_id": 1, "status": "online", "kind": "chat"},
                {"id": 41, "name": "gemma4-uncensored:latest", "alias": "Gemma 4 uncensored", "provider_id": 1, "status": "online", "kind": "chat"},
            ]

        async def adapter_for(self, model_id):
            rows = {m["id"]: m for m in await self.list_models()}
            return FakeAdapter(), rows[model_id]

    class FakeBus:
        async def emit(self, *args, **kwargs):
            pass

    svc_root = SimpleNamespace(
        settings=SimpleNamespace(data_dir=tmp_path),
        registry=FakeRegistry(),
        bus=FakeBus(),
    )
    dg = DirectGenService(svc_root)
    dg.ollama_unload = lambda model_name=None: unloaded_models.append(model_name or "*") or ["gemma4-uncensored:latest"]

    res = await dg.assist("owner", "кот в скафандре на Луне")
    assert res["model"] == "gemma4-uncensored:latest", f"Expected Gemma uncensored first, got {res['model']}"
    assert "Кот в скафандре" in res["suggested"]
    assert res.get("unloaded") is True
    assert "gemma4-uncensored:latest" in unloaded_models
