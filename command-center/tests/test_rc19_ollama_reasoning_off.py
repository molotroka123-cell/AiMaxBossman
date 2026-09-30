"""RC19 owner defect (2026-09-28): «Bossman CMD» showed the private thinking of
local Ollama Qwen models. Ollama's OpenAI-compatible /v1 returns it in
`message.reasoning` and spends the answer budget on it; `think: false` is
ignored on /v1, `reasoning_effort: "none"` switches it off (owner host, Ollama
0.34.4). Every other local server already runs with reasoning off
(llama.cpp `--reasoning off`, Jeff's native `think: false`).

No network: httpx.MockTransport.
"""
import json

import httpx

from bcc.providers import OpenAICompatAdapter, is_ollama_v1_url


def _chat_transport(seen: list[dict]):
    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(json.loads(request.content))
        return httpx.Response(200, json={"choices": [{"message": {
            "role": "assistant", "content": "4"}, "finish_reason": "stop"}]})
    return httpx.MockTransport(handler)


async def test_ollama_v1_chat_switches_thinking_off():
    seen: list[dict] = []
    ollama = OpenAICompatAdapter(base_url="http://127.0.0.1:11434/v1", transport=_chat_transport(seen))
    result = await ollama.chat("bossman-fast-qwen36-35b-a3b-q5:latest",
                               [{"role": "user", "content": "2+2?"}], max_tokens=32)
    assert result.text == "4"
    assert seen[-1]["reasoning_effort"] == "none"


async def test_explicit_reasoning_mode_and_other_servers_are_untouched():
    seen: list[dict] = []
    ollama = OpenAICompatAdapter(base_url="http://localhost:11434/v1", transport=_chat_transport(seen))
    await ollama.chat("m", [{"role": "user", "content": "x"}], reasoning_effort="high")
    assert seen[-1]["reasoning_effort"] == "high"
    for base in ("http://127.0.0.1:8083/v1", "https://openrouter.ai/api/v1",
                 "http://192.168.1.5:11434/v1"):
        other = OpenAICompatAdapter(base_url=base, api_key="k", transport=_chat_transport(seen))
        await other.chat("m", [{"role": "user", "content": "x"}])
        assert "reasoning_effort" not in seen[-1]


def test_ollama_v1_url_detection():
    assert is_ollama_v1_url("http://127.0.0.1:11434/v1")
    assert is_ollama_v1_url("http://localhost:11434/v1/")
    assert not is_ollama_v1_url("http://127.0.0.1:11434")          # native API, own adapter
    assert not is_ollama_v1_url("http://127.0.0.1:8081/v1")
    assert not is_ollama_v1_url("http://[::1:bad/v1")
