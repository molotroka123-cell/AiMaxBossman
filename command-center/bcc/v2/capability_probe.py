from __future__ import annotations

import json
from dataclasses import asdict, dataclass
from typing import Any, Protocol

from .tool_messages import parse_openai_chat_response

class RawChatClient(Protocol):
    async def chat_raw(self, model: str, messages: list[dict[str, Any]], **kwargs: Any) -> dict[str, Any]: ...

@dataclass(slots=True)
class ProbeResult:
    capability: str
    ok: bool
    detail: str = ""
    skipped: bool = False        # способность не заявлена → пробу не гоняли

    @property
    def verified(self) -> bool | None:
        """None = «не знаем» (пробу не гоняли). Роутер обязан отличать это от False."""
        return None if self.skipped else self.ok

# 1×1 PNG (красная точка) — самый дешёвый валидный вход для vision-пробы.
PIXEL_PNG = ("data:image/png;base64,"
             "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mP8z8"
             "BQDwAEhQGAhKmMIQAAAABJRU5ErkJggg==")

def skipped(capability: str, reason: str = "not advertised") -> ProbeResult:
    return ProbeResult(capability, False, reason, skipped=True)

async def probe_chat(client: RawChatClient, model: str) -> ProbeResult:
    try:
        data = await client.chat_raw(
            model, [{"role": "user", "content": "Reply exactly OK"}],
            max_tokens=8, temperature=0
        )
        turn = parse_openai_chat_response(data)
        return ProbeResult("chat", bool(turn.text), turn.text[:120])
    except Exception as exc:
        return ProbeResult("chat", False, f"{type(exc).__name__}: {exc}")

async def probe_tools(client: RawChatClient, model: str) -> ProbeResult:
    tool = {
        "type": "function",
        "function": {
            "name": "bossman_probe",
            "description": "Return the integer.",
            "parameters": {
                "type": "object",
                "properties": {"value": {"type": "integer"}},
                "required": ["value"],
            },
        },
    }
    try:
        data = await client.chat_raw(
            model,
            [{"role": "user", "content": "Call bossman_probe with value 7."}],
            tools=[tool], tool_choice="auto", max_tokens=64, temperature=0
        )
        turn = parse_openai_chat_response(data)
        ok = bool(turn.tool_calls and turn.tool_calls[0].name == "bossman_probe")
        # ToolCall — dataclass(slots=True): у него НЕТ __dict__ (был баг: AttributeError
        # ловился ниже и любая tools-проба становилась verified=False).
        detail = json.dumps([asdict(c) for c in turn.tool_calls[:1]], ensure_ascii=False)[:500]
        return ProbeResult("tools", ok, detail)
    except Exception as exc:
        return ProbeResult("tools", False, f"{type(exc).__name__}: {exc}")

async def probe_structured_output(client: RawChatClient, model: str) -> ProbeResult:
    try:
        data = await client.chat_raw(
            model,
            [{"role": "user", "content": 'Return JSON with {"ok": true}'}],
            response_format={"type": "json_object"}, max_tokens=32, temperature=0
        )
        turn = parse_openai_chat_response(data)
        parsed = json.loads(turn.text)
        return ProbeResult("structured_output", parsed.get("ok") is True, turn.text[:200])
    except Exception as exc:
        return ProbeResult("structured_output", False, f"{type(exc).__name__}: {exc}")

async def probe_vision(client: RawChatClient, model: str) -> ProbeResult:
    """Мультимодальный вход: картинка + вопрос. Гоняем ТОЛЬКО если vision заявлен —
    иначе провайдер вернёт 400 и мы запишем ложный verified=False."""
    messages = [{
        "role": "user",
        "content": [
            {"type": "text", "text": "Answer with one word: what is in the image?"},
            {"type": "image_url", "image_url": {"url": PIXEL_PNG}},
        ],
    }]
    try:
        data = await client.chat_raw(model, messages, max_tokens=16, temperature=0)
        turn = parse_openai_chat_response(data)
        return ProbeResult("vision", bool(turn.text.strip()), turn.text[:200])
    except Exception as exc:
        return ProbeResult("vision", False, f"{type(exc).__name__}: {exc}")

async def probe_streaming(client: Any, model: str) -> ProbeResult:
    """SSE-стрим: важен не текст, а то, что чанки реально приходят по частям."""
    stream = getattr(client, "stream_raw", None)
    if stream is None:
        return skipped("streaming", "client has no stream_raw()")
    try:
        chunks = await stream(model, [{"role": "user", "content": "Count: 1 2 3"}],
                              max_tokens=16)
        text = "".join(chunks)
        return ProbeResult("streaming", bool(chunks), f"{len(chunks)} chunks: {text[:120]}")
    except Exception as exc:
        return ProbeResult("streaming", False, f"{type(exc).__name__}: {exc}")

PROBES = {
    "tools": probe_tools,
    "structured_output": probe_structured_output,
    "vision": probe_vision,
    "streaming": probe_streaming,
}

async def probe_model(client: Any, model: str,
                      advertised: dict[str, bool] | None = None,
                      *, only: list[str] | None = None) -> list[ProbeResult]:
    """chat всегда + заявленные способности. Незаявленные помечаются skipped:
    verified=None («не знаем»), а не False («проверили, не умеет»)."""
    adv = {k: bool(v) for k, v in (advertised or {}).items()}
    results = [await probe_chat(client, model)]
    for cap, fn in PROBES.items():
        if only is not None and cap not in only:
            continue
        if not adv.get(cap):
            results.append(skipped(cap))
            continue
        results.append(await fn(client, model))
    return results
