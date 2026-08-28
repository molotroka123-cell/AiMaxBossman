from __future__ import annotations

import json
from dataclasses import dataclass, field
from typing import Any

@dataclass(slots=True)
class ToolCall:
    id: str
    name: str
    arguments: dict[str, Any]
    raw_arguments: str = ""

@dataclass(slots=True)
class AssistantTurn:
    text: str = ""
    tool_calls: list[ToolCall] = field(default_factory=list)
    finish_reason: str = "stop"
    model: str = ""
    tokens_in: int = 0
    tokens_out: int = 0
    raw_message: dict[str, Any] = field(default_factory=dict)

def parse_openai_chat_response(data: dict[str, Any]) -> AssistantTurn:
    choices = data.get("choices") or []
    if not choices:
        raise ValueError("provider returned no choices")
    choice = choices[0]
    msg = choice.get("message") or {}
    calls: list[ToolCall] = []
    for item in msg.get("tool_calls") or []:
        fn = item.get("function") or {}
        raw = str(fn.get("arguments") or "{}")
        try:
            args = json.loads(raw)
            if not isinstance(args, dict):
                args = {"value": args}
        except json.JSONDecodeError:
            args = {"_raw": raw}
        calls.append(ToolCall(
            id=str(item.get("id") or ""),
            name=str(fn.get("name") or ""),
            arguments=args,
            raw_arguments=raw,
        ))
    usage = data.get("usage") or {}
    return AssistantTurn(
        text=str(msg.get("content") or ""),
        tool_calls=calls,
        finish_reason=str(choice.get("finish_reason") or "stop"),
        model=str(data.get("model") or ""),
        tokens_in=int(usage.get("prompt_tokens") or 0),
        tokens_out=int(usage.get("completion_tokens") or 0),
        raw_message=dict(msg),
    )
