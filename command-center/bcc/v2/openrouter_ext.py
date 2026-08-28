from __future__ import annotations

import json
from dataclasses import dataclass, field
from typing import Any

import httpx

DEFAULT_BASE = "https://openrouter.ai/api/v1"

def _float(v: Any, default: float = 0.0) -> float:
    try:
        return float(v)
    except (TypeError, ValueError):
        return default

def per_million(v: Any) -> float:
    """OpenRouter pricing fields are commonly per-token strings; normalize to USD / 1M."""
    return _float(v) * 1_000_000

@dataclass(slots=True)
class OpenRouterModelCard:
    id: str
    name: str
    context_length: int = 0
    price_in: float = 0.0
    price_out: float = 0.0
    input_modalities: list[str] = field(default_factory=list)
    output_modalities: list[str] = field(default_factory=list)
    supported_parameters: list[str] = field(default_factory=list)
    architecture: dict[str, Any] = field(default_factory=dict)
    created: int | None = None
    raw: dict[str, Any] = field(default_factory=dict)

    @property
    def advertised_caps(self) -> dict[str, bool]:
        params = set(self.supported_parameters)
        inp = set(self.input_modalities)
        return {
            "vision": bool({"image", "video"} & inp),
            "tools": bool({"tools", "tool_choice"} & params),
            "structured_output": bool({"response_format", "structured_outputs"} & params),
            "streaming": True,
        }

def parse_model_card(raw: dict[str, Any]) -> OpenRouterModelCard:
    arch = raw.get("architecture") or {}
    pricing = raw.get("pricing") or {}
    input_modalities = (
        arch.get("input_modalities")
        or raw.get("input_modalities")
        or []
    )
    output_modalities = (
        arch.get("output_modalities")
        or raw.get("output_modalities")
        or []
    )
    return OpenRouterModelCard(
        id=str(raw.get("id") or ""),
        name=str(raw.get("name") or raw.get("id") or ""),
        context_length=int(raw.get("context_length") or 0),
        price_in=per_million(pricing.get("prompt")),
        price_out=per_million(pricing.get("completion")),
        input_modalities=[str(x) for x in input_modalities],
        output_modalities=[str(x) for x in output_modalities],
        supported_parameters=[str(x) for x in (raw.get("supported_parameters") or [])],
        architecture=dict(arch),
        created=int(raw["created"]) if raw.get("created") is not None else None,
        raw=dict(raw),
    )

class OpenRouterClient:
    def __init__(self, api_key: str, base_url: str = DEFAULT_BASE,
                 transport: httpx.AsyncBaseTransport | None = None):
        self.api_key = api_key
        self.base_url = base_url.rstrip("/")
        self.transport = transport

    def _headers(self) -> dict[str, str]:
        return {
            "Authorization": f"Bearer {self.api_key}",
            "Content-Type": "application/json",
        }

    def _client(self, timeout: float = 60) -> httpx.AsyncClient:
        return httpx.AsyncClient(timeout=timeout, transport=self.transport)

    async def list_models(self) -> list[OpenRouterModelCard]:
        async with self._client(30) as client:
            r = await client.get(f"{self.base_url}/models", headers=self._headers())
        r.raise_for_status()
        data = r.json().get("data") or []
        return [parse_model_card(x) for x in data if x.get("id")]

    async def chat_raw(self, model: str, messages: list[dict[str, Any]], *,
                       tools: list[dict[str, Any]] | None = None,
                       tool_choice: Any = None,
                       response_format: dict[str, Any] | None = None,
                       max_tokens: int = 128,
                       temperature: float | None = None,
                       stream: bool = False,
                       provider: dict[str, Any] | None = None) -> dict[str, Any]:
        payload: dict[str, Any] = {
            "model": model,
            "messages": messages,
            "max_tokens": max_tokens,
            "stream": stream,
        }
        if tools:
            payload["tools"] = tools
        if tool_choice is not None:
            payload["tool_choice"] = tool_choice
        if response_format is not None:
            payload["response_format"] = response_format
        if temperature is not None:
            payload["temperature"] = temperature
        if provider is not None:
            payload["provider"] = provider
        async with self._client(120) as client:
            r = await client.post(
                f"{self.base_url}/chat/completions",
                headers=self._headers(),
                json=payload,
            )
        r.raise_for_status()
        return r.json()

    async def stream_raw(self, model: str, messages: list[dict[str, Any]], *,
                         max_tokens: int = 32, temperature: float | None = 0,
                         max_chunks: int = 32) -> list[str]:
        """SSE-стрим → список текстовых дельт. Пустой список = стрим не работает."""
        payload: dict[str, Any] = {
            "model": model, "messages": messages,
            "max_tokens": max_tokens, "stream": True,
        }
        if temperature is not None:
            payload["temperature"] = temperature
        deltas: list[str] = []
        async with self._client(120) as client:
            async with client.stream("POST", f"{self.base_url}/chat/completions",
                                     headers=self._headers(), json=payload) as r:
                r.raise_for_status()
                async for line in r.aiter_lines():
                    line = line.strip()
                    if not line.startswith("data:"):
                        continue
                    body = line[5:].strip()
                    if body in ("", "[DONE]"):
                        if body == "[DONE]":
                            break
                        continue
                    try:
                        chunk = json.loads(body)
                    except json.JSONDecodeError:
                        continue
                    choice = (chunk.get("choices") or [{}])[0]
                    piece = (choice.get("delta") or {}).get("content")
                    if piece:
                        deltas.append(str(piece))
                    if len(deltas) >= max_chunks:
                        break
        return deltas

    async def probe_chat(self, model: str) -> tuple[bool, str]:
        try:
            data = await self.chat_raw(
                model, [{"role": "user", "content": "Reply exactly OK"}],
                max_tokens=8, temperature=0
            )
            text = str((((data.get("choices") or [{}])[0].get("message") or {}).get("content") or ""))
            return True, text[:120]
        except Exception as exc:
            return False, f"{type(exc).__name__}: {exc}"

    async def probe_tools(self, model: str) -> tuple[bool, str]:
        tool = {
            "type": "function",
            "function": {
                "name": "bossman_probe",
                "description": "Return the supplied integer.",
                "parameters": {
                    "type": "object",
                    "properties": {"value": {"type": "integer"}},
                    "required": ["value"],
                },
            },
        }
        try:
            data = await self.chat_raw(
                model,
                [{"role": "user", "content": "Call bossman_probe with value 7. Do not answer normally."}],
                tools=[tool],
                tool_choice="auto",
                max_tokens=64,
                temperature=0,
            )
            msg = ((data.get("choices") or [{}])[0].get("message") or {})
            calls = msg.get("tool_calls") or []
            return bool(calls), json.dumps(calls[:1])[:500]
        except Exception as exc:
            return False, f"{type(exc).__name__}: {exc}"
