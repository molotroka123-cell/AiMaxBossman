"""Deterministic fake OpenAI-compatible provider for BOSSMAN integration tests.

Run manually:
    uvicorn tests.v2.fake_provider_app:app --port 18080
"""
from __future__ import annotations

import json
from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse

app = FastAPI()

MODELS = [
    {
        "id": "fake/fast",
        "name": "Fake Fast",
        "context_length": 32768,
        "pricing": {"prompt": "0.0000001", "completion": "0.0000002"},
        "architecture": {"input_modalities": ["text"], "output_modalities": ["text"]},
        "supported_parameters": ["tools", "tool_choice", "response_format"],
    },
    {
        "id": "fake/vision",
        "name": "Fake Vision",
        "context_length": 65536,
        "pricing": {"prompt": "0.0000002", "completion": "0.0000004"},
        "architecture": {"input_modalities": ["text", "image"], "output_modalities": ["text"]},
        "supported_parameters": ["tools", "response_format"],
    },
]

@app.get("/v1/key")
async def key_info(req: Request):
    """Проверка ключа без инференса: 200 на любой Bearer, кроме канарейки 'bad'."""
    auth = req.headers.get("authorization") or ""
    if not auth.startswith("Bearer ") or auth == "Bearer bad":
        return JSONResponse({"error": {"message": "Invalid key"}}, status_code=401)
    return {"data": {"label": "test", "usage": 0, "limit": None}}

@app.get("/v1/models")
async def models():
    return {"data": MODELS}

@app.post("/v1/chat/completions")
async def chat(req: Request):
    body = await req.json()
    model = body.get("model") or "fake/fast"
    messages = body.get("messages") or []
    tools = body.get("tools") or []
    response_format = body.get("response_format")
    prompt = str(messages[-1].get("content") if messages else "")

    if tools and "bossman_probe" in prompt:
        message = {
            "role": "assistant",
            "content": None,
            "tool_calls": [{
                "id": "call_fake_1",
                "type": "function",
                "function": {"name": "bossman_probe", "arguments": '{"value":7}'},
            }],
        }
        finish = "tool_calls"
    elif response_format:
        message = {"role": "assistant", "content": '{"ok": true}'}
        finish = "stop"
    else:
        message = {"role": "assistant", "content": "OK"}
        finish = "stop"

    return JSONResponse({
        "id": "fake-completion",
        "object": "chat.completion",
        "model": model,
        "choices": [{"index": 0, "message": message, "finish_reason": finish}],
        "usage": {"prompt_tokens": 12, "completion_tokens": 3, "total_tokens": 15},
    })
