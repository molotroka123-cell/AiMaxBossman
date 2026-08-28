import json
import httpx

from bcc.v2.openrouter_ext import OpenRouterClient, parse_model_card

def test_parse_openrouter_card():
    c = parse_model_card({
        "id": "vendor/model",
        "name": "Model",
        "context_length": 100000,
        "pricing": {"prompt": "0.000001", "completion": "0.000003"},
        "architecture": {"input_modalities": ["text", "image"], "output_modalities": ["text"]},
        "supported_parameters": ["tools", "response_format"],
    })
    assert c.price_in == 1.0
    assert c.price_out == 3.0
    assert c.advertised_caps["vision"]
    assert c.advertised_caps["tools"]

async def test_list_models_with_mock_transport():
    def handler(request: httpx.Request):
        assert request.url.path.endswith("/models")
        return httpx.Response(200, json={"data": [{
            "id": "x/y", "name": "Y", "context_length": 8192, "pricing": {}
        }]})
    c = OpenRouterClient("secret", transport=httpx.MockTransport(handler))
    models = await c.list_models()
    assert [m.id for m in models] == ["x/y"]
