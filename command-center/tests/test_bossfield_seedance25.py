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
