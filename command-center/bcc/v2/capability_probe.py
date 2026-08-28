from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Any, Protocol

from .tool_messages import parse_openai_chat_response

class RawChatClient(Protocol):
    async def chat_raw(self, model: str, messages: list[dict[str, Any]], **kwargs: Any) -> dict[str, Any]: ...

@dataclass(slots=True)
class ProbeResult:
    capability: str
    ok: bool
    detail: str = ""

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
        detail = json.dumps([c.__dict__ for c in turn.tool_calls[:1]], ensure_ascii=False)[:500]
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
