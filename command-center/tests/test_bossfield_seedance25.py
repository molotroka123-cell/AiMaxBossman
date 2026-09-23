"""A newly catalogued premium video model must route to video, and stay gated."""
import httpx
from bcc.studio.providers.openrouter import OpenRouterProvider
from bcc.studio.provider import GenerationPlane


async def test_seedance25_uses_video_endpoint_and_first_frame():
    seen = []
    def serve(request):
        seen.append((request.url.path, request.read().decode()))
        return httpx.Response(202, json={"id": "vid-25"})
    provider = OpenRouterProvider("test-secret", transport=httpx.MockTransport(serve))
    shot = GenerationPlane("openrouter:bytedance/seedance-2.5", "one continuous scene",
                           settings={"duration": 15, "resolution": "720p", "aspect_ratio": "9:16"},
                           media=({"role": "start", "data_uri": "data:image/png;base64,AA=="},))
    result = await provider.submit(shot)
    assert result.request_id == "vid-25"
    assert seen[0][0] == "/api/v1/videos"
    assert '"first_frame"' in seen[0][1]
    import json
    body = json.loads(seen[0][1])
    assert body["duration"] == 15 and body["model"] == "bytedance/seedance-2.5"
    assert body["frame_images"][0]["frame_type"] == "first_frame"


def test_seedance25_catalog_is_not_verified_or_free():
    import json
    from pathlib import Path
    catalog = json.loads((Path(__file__).resolve().parents[2] / "tools/studio_models.json").read_text())
    model = next(m for m in catalog["models"] if m["id"] == "openrouter:bytedance/seedance-2.5")
    assert model["surface"] == "video" and model["settings"]["duration"]["default"] == 15
    assert model["enabled"] is False and model["answers"]["VERIFIED"] is False
    assert model["free"] is False and model["price"]["kind"] == "unknown"


def test_seedance25_accepts_short_shots_the_provider_supports():
    """SwapMe hybrid run 23.09: the catalog pinned duration to [15] while OpenRouter serves 4–30 s.
    A 5 s cloud shot inside a cloud+local edit was impossible through Bossman. 4–15 s are
    offered (the old 15 s default stays); anything the provider rejects stays rejected here."""
    import pytest
    from bcc.studio.catalog import load, validate_settings
    model = next(m for m in load()["models"] if m["id"] == "openrouter:bytedance/seedance-2.5")
    for seconds in (4, 5, 10, 15):
        assert validate_settings(model, {"duration": seconds})["duration"] == seconds
    assert validate_settings(model, {})["duration"] == 15
    for seconds in (3, 16, 0):
        with pytest.raises(ValueError):
            validate_settings(model, {"duration": seconds})


async def test_paid_openrouter_video_is_fetched_with_the_key_from_the_api_origin():
    """SwapMe run 23.09 (bug STUDIO-OPENROUTER-FETCH-NO-AUTH): OpenRouter serves the finished video at
    https://openrouter.ai/api/v1/videos/<id>/content and answers 401 without the key. Studio fetched it
    unauthenticated (and only from allow-listed CDN hosts), so a PAID generation ended `failed: malformed`.
    The key goes to that exact first-party path only; a third-party CDN still gets no token."""
    import pytest
    from bcc.studio.provider import ProviderOutput
    seen = []

    def serve(request):
        seen.append((request.url.host, request.url.path, request.headers.get("authorization")))
        if request.method == "POST":
            return httpx.Response(202, json={"id": "vid-9"})
        if request.url.path == "/api/v1/videos/vid-9":
            return httpx.Response(200, json={"status": "completed", "usage": {"cost": 1.2},
                                             "unsigned_urls": ["https://openrouter.ai/api/v1/videos/vid-9/content"]})
        if request.url.path == "/api/v1/videos/vid-9/content":
            if request.headers.get("authorization") != "Bearer test-secret":
                return httpx.Response(401, json={"error": {"message": "No cookie auth credentials found", "code": 401}})
            return httpx.Response(200, content=b"\x00\x00\x00\x18ftypmp42video")
        if request.url.host == "cdn.example.net":
            return httpx.Response(200, content=b"cdnbytes")
        return httpx.Response(404)

    provider = OpenRouterProvider("test-secret", transport=httpx.MockTransport(serve))
    shot = GenerationPlane("openrouter:bytedance/seedance-2.5", "one shot",
                           settings={"duration": 5, "resolution": "720p", "aspect_ratio": "9:16"})
    rid = (await provider.submit(shot)).request_id
    status = await provider.status(rid)
    assert status.state == "completed"
    import tempfile, pathlib
    dest = pathlib.Path(tempfile.mkdtemp()) / "out.mp4"
    fetched = await provider.fetch(status.outputs[0], dest)
    assert dest.read_bytes().startswith(b"\x00\x00\x00\x18ftyp") and fetched.bytes == dest.stat().st_size
    assert provider.costs[rid] == 1.2

    # A third-party CDN URL keeps the old rule: allow-listed host only, and never the key.
    cdn = OpenRouterProvider("test-secret", transport=httpx.MockTransport(serve), allowed_download_hosts=("cdn.example.net",))
    cdn._outputs["x:0"] = ("url", "https://cdn.example.net/v.mp4")
    await cdn.fetch(ProviderOutput("x:0"), dest)
    assert seen[-1] == ("cdn.example.net", "/v.mp4", None)
    other = OpenRouterProvider("test-secret", transport=httpx.MockTransport(serve))
    other._outputs["y:0"] = ("url", "https://openrouter.ai/somewhere/else")
    with pytest.raises(Exception):
        await other.fetch(ProviderOutput("y:0"), dest)
