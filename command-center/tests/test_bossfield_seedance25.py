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
