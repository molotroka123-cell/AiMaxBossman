"""ComfyUI contract and real Images route persistence; no GPU claims."""
import hashlib
import json
import struct
import zlib

import httpx
import pytest

from bcc.oss.comfyui import (ComfyUIClient, ComfyUIImageProvider, local_url,
                             safe_relative, validate_workflow, verify_png)


def png(width=256, height=256):
    def chunk(kind, data):
        return struct.pack(">I", len(data)) + kind + data + struct.pack(">I", zlib.crc32(kind + data) & 0xffffffff)
    return (b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", struct.pack(">IIBBBBB", width, height, 8, 2, 0, 0, 0))
            + chunk(b"IDAT", zlib.compress((b"\x00" + b"\x00\x55\xaa" * width) * height))
            + chunk(b"IEND", b""))


class Server:
    def __init__(self, *, fail=False, empty=False, malicious=False):
        self.polls = 0
        self.submitted = []
        self.fail, self.empty, self.malicious = fail, empty, malicious

    def __call__(self, request):
        path = request.url.path
        if path == "/system_stats":
            return httpx.Response(200, json={"system": {}, "devices": []})
        if path == "/prompt":
            self.submitted.append(json.loads(request.content)["prompt"])
            return httpx.Response(200, json={"prompt_id": "job-123", "node_errors": {}})
        if path == "/history/job-123":
            self.polls += 1
            if self.polls == 1:
                return httpx.Response(200, json={})
            return httpx.Response(200, json={"job-123": {
                "status": {"completed": not self.fail, "status_str": "error" if self.fail else "success"},
                "outputs": {} if self.empty else {"7": {"images": [{"filename": "../secret.png" if self.malicious else "result.png", "subfolder": "BOSSMAN", "type": "output"}]}}
            }})
        if path == "/view":
            assert request.url.params["type"] == "output"
            return httpx.Response(200, content=png())
        raise AssertionError(f"unexpected request {path}")


def provider(server):
    return ComfyUIImageProvider("http://127.0.0.1:8188", "sd.safetensors",
        client=ComfyUIClient("http://127.0.0.1:8188", transport=httpx.MockTransport(server)), poll_seconds=0)


def spec():
    return {"prompt": "Prague", "negative_prompt": "blur", "width": 256, "height": 256, "steps": 4, "seed": 8}


@pytest.mark.parametrize("url", ["https://127.0.0.1:8188", "http://evil.test", "http://192.168.1.1", "http://127.0.0.1/a", "http://x:pw@127.0.0.1", "http://127.0.0.1?token=x"])
def test_reject_nonlocal_origins(url):
    with pytest.raises(ValueError):
        local_url(url)


def test_localhost_is_pinned():
    assert local_url("http://localhost:8188") == "http://127.0.0.1:8188"
    assert local_url("http://[::1]:8188") == "http://[::1]:8188"


@pytest.mark.parametrize("path", ["../x", "/x", "C:/x", "a\\x", "%2e%2e/x", "a/../x", "a\x00x"])
def test_output_path_containment(path):
    with pytest.raises(ValueError):
        safe_relative(path)


async def test_receipt_then_history_then_validated_artifact():
    server = Server()
    client = provider(server)
    health = await client.client.health()
    assert health["available"] and not health["generation_verified"]
    data, mime, meta = await client.render(spec(), 1)
    assert server.polls == 2
    assert mime == "image/png" and data == png()
    assert meta["sha256"] == hashlib.sha256(data).hexdigest()
    assert meta["mock"] is False and meta["seed"] == 9
    workflow = server.submitted[0]
    assert workflow["2"]["inputs"]["text"] == "Prague"
    assert workflow["3"]["inputs"]["text"] == "blur"
    assert workflow["5"]["inputs"]["steps"] == 4


@pytest.mark.parametrize("options", [{"fail": True}, {"empty": True}, {"malicious": True}])
async def test_history_failure_never_becomes_image(options):
    with pytest.raises((RuntimeError, ValueError)):
        await provider(Server(**options)).render(spec(), 0)


async def test_timeout_does_not_resubmit_or_claim_completion():
    calls = []
    def pending(request):
        calls.append(request.url.path)
        return httpx.Response(200, json={"prompt_id": "pending"} if request.url.path == "/prompt" else {})
    p = provider(pending)
    p.timeout_seconds = 0.001
    with pytest.raises(TimeoutError, match="may still run"):
        await p.render(spec(), 0)
    assert calls.count("/prompt") == 1
    assert "/view" not in calls


async def test_no_redirect_to_external_service():
    def redirect(request):
        return httpx.Response(302, headers={"location": "https://evil.test"})
    with pytest.raises(httpx.HTTPStatusError):
        await provider(redirect).client.health()


def test_deny_custom_paid_nodes_and_unsupported_inputs():
    with pytest.raises(ValueError, match="native"):
        validate_workflow({"1": {"class_type": "PaidCloudGenerate", "inputs": {}}})
    with pytest.raises(ValueError, match="references"):
        provider(Server()).workflow({**spec(), "reference_asset_ids": [1]}, 0)
    with pytest.raises(ValueError, match="multiples"):
        provider(Server()).workflow({**spec(), "width": 257}, 0)


def test_png_is_decoded_and_checked_not_just_sniffed():
    assert verify_png(png()) == (256, 256)
    for broken in (png()[:-1], b"<html>error</html>", png()[:40] + b"bad" + png()[43:], png() + b"extra"):
        with pytest.raises(ValueError):
            verify_png(broken)


async def test_real_images_api_queue_persists_artifact(env, monkeypatch):
    from bcc.features import images
    monkeypatch.setenv("BOSSMAN_COMFYUI_URL", "http://127.0.0.1:8188")
    monkeypatch.setenv("BOSSMAN_COMFYUI_CHECKPOINT", "sd.safetensors")
    p = provider(Server())
    monkeypatch.setattr(images, "ComfyUIImageProvider", lambda *_: p)
    models = (await env.client.get("/api/images/models")).json()
    assert next(m for m in models if m["alias"] == "comfyui")["executable"]
    created = await env.client.post("/api/images/jobs", json={**spec(), "model_alias": "comfyui"})
    assert created.status_code == 200
    job_id = created.json()["id"]
    assert created.json()["status"] == "queued"
    await images.process_one(env.svc)
    job = (await env.client.get(f"/api/images/jobs/{job_id}")).json()
    assert job["status"] == "completed"
    assets = (await env.client.get("/api/images/assets")).json()["items"]
    asset = next(a for a in assets if a["source_job_id"] == job_id)
    assert asset["mime_type"] == "image/png" and asset["width"] == 256
    content = await env.client.get(asset["file_url"])
    assert content.content == png()
    assert asset["meta"]["sha256"] == hashlib.sha256(content.content).hexdigest()


async def test_cancel_during_render_cannot_be_overwritten_as_completed(env, monkeypatch):
    from bcc.features import images
    monkeypatch.setenv("BOSSMAN_COMFYUI_URL", "http://127.0.0.1:8188")
    monkeypatch.setenv("BOSSMAN_COMFYUI_CHECKPOINT", "sd.safetensors")
    created = (await env.client.post("/api/images/jobs", json={**spec(), "model_alias": "comfyui"})).json()
    class CancellingProvider:
        async def render(self, *_):
            await env.client.post(f"/api/images/jobs/{created['id']}/cancel")
            return png(), "image/png", {}
    monkeypatch.setattr(images, "ComfyUIImageProvider", lambda *_: CancellingProvider())
    await images.process_one(env.svc)
    job = (await env.client.get(f"/api/images/jobs/{created['id']}")).json()
    assert job["status"] == "cancelled"
    assert (await env.client.get("/api/images/assets")).json()["total"] == 0


async def test_unconfigured_service_is_not_advertised_executable(env, monkeypatch):
    monkeypatch.delenv("BOSSMAN_COMFYUI_URL", raising=False)
    monkeypatch.delenv("BOSSMAN_COMFYUI_CHECKPOINT", raising=False)
    models = (await env.client.get("/api/images/models")).json()
    comfy = next(m for m in models if m["alias"] == "comfyui")
    assert not comfy["executable"] and comfy["status"] == "not_configured"


async def test_real_loopback_http_service_contract():
    """Exercise the real HTTP client/streaming path against a tiny local service."""
    import asyncio
    from contextlib import suppress

    server_contract = Server()
    handlers = set()

    async def serve(reader, writer):
        task = asyncio.current_task()
        handlers.add(task)
        try:
            header = await reader.readuntil(b"\r\n\r\n")
            lines = header.decode().split("\r\n")
            method, target, _ = lines[0].split(" ")
            headers = dict(line.split(": ", 1) for line in lines[1:] if ": " in line)
            size = int(next((v for k, v in headers.items() if k.lower() == "content-length"), "0"))
            body = await reader.readexactly(size) if size else b""
            response = server_contract(httpx.Request(method, "http://127.0.0.1" + target, content=body))
            payload = response.content
            writer.write(f"HTTP/1.1 200 OK\r\nContent-Length: {len(payload)}\r\nConnection: close\r\n\r\n".encode() + payload)
            await writer.drain()
        finally:
            writer.close()
            with suppress(ConnectionError):
                await writer.wait_closed()
            handlers.discard(task)

    server = await asyncio.start_server(serve, "127.0.0.1", 0)
    try:
        port = server.sockets[0].getsockname()[1]
        p = ComfyUIImageProvider(f"http://127.0.0.1:{port}", "sd.safetensors", poll_seconds=0)
        data, _, meta = await p.render(spec(), 0)
        assert data == png() and meta["prompt_id"] == "job-123"
        assert server_contract.polls == 2
    finally:
        server.close()
        await server.wait_closed()
        if handlers:
            await asyncio.gather(*handlers)


def test_explicit_zero_seed_is_preserved():
    assert provider(Server()).workflow({**spec(), "seed": 0}, 0)["5"]["inputs"]["seed"] == 0


@pytest.mark.parametrize("changes", [{"width": 257}, {"height": 900}, {"kind": "variation"},
                                     {"source_asset_id": 7}, {"reference_asset_ids": [7]}])
async def test_unsupported_image_specs_rejected_before_enqueue(env, monkeypatch, changes):
    monkeypatch.setenv("BOSSMAN_COMFYUI_URL", "http://127.0.0.1:8188")
    monkeypatch.setenv("BOSSMAN_COMFYUI_CHECKPOINT", "sd.safetensors")
    response = await env.client.post("/api/images/jobs", json={**spec(), "model_alias": "comfyui", **changes})
    assert response.status_code == 422
    assert (await env.client.get("/api/images/jobs")).json()["items"] == []


async def test_all_visible_ratio_presets_are_accepted_by_comfyui(env, monkeypatch):
    import re
    from pathlib import Path
    monkeypatch.setenv("BOSSMAN_COMFYUI_URL", "http://127.0.0.1:8188")
    monkeypatch.setenv("BOSSMAN_COMFYUI_CHECKPOINT", "sd.safetensors")
    source = (Path(__file__).parents[1] / "ui/pages/images.js").read_text(encoding="utf-8")
    presets = re.findall(r"'(\d+:\d+)':\s*\[(\d+),\s*(\d+)\]", source)
    assert len(presets) == 5
    for ratio, raw_width, raw_height in presets:
        width, height = int(raw_width), int(raw_height)
        numerator, denominator = map(int, ratio.split(":"))
        assert width * denominator == height * numerator
        body = {**spec(), "model_alias": "comfyui", "width": width, "height": height, "aspect_ratio": ratio}
        response = await env.client.post("/api/images/jobs", json=body)
        assert response.status_code == 200, (ratio, response.text)
        assert response.json()["status"] == "queued"
        provider(Server()).workflow(body, 0)
    assert len((await env.client.get("/api/images/jobs")).json()["items"]) == 5


async def test_missing_comfy_configuration_rejected_before_enqueue(env, monkeypatch):
    monkeypatch.delenv("BOSSMAN_COMFYUI_URL", raising=False)
    monkeypatch.delenv("BOSSMAN_COMFYUI_CHECKPOINT", raising=False)
    response = await env.client.post("/api/images/jobs", json={**spec(), "model_alias": "comfyui"})
    assert response.status_code == 503
    assert (await env.client.get("/api/images/jobs")).json()["items"] == []
