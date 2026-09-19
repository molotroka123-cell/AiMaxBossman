"""Translate the Messages wire protocol without treating EOF as completion.

Protocol references (contract fixtures are not a live-model acceptance):
https://platform.claude.com/docs/en/build-with-claude/streaming
https://platform.claude.com/docs/en/agents-and-tools/tool-use/define-tools
https://platform.claude.com/docs/en/build-with-claude/structured-outputs
"""
from __future__ import annotations

import copy
import json
from typing import Any, Iterator

MAX_EVENT_BYTES = 1024 * 1024
MAX_EVENTS = 100_000
MAX_BLOCKS = 1024


class ProtocolError(ValueError):
    """A named protocol failure; never include upstream/user content in its text."""


def _object(value: Any, label: str) -> dict:
    if not isinstance(value, dict):
        raise ProtocolError(f"{label} must be an object")
    return value


def _text(value: Any, label: str) -> str:
    if not isinstance(value, str) or not value:
        raise ProtocolError(f"{label} must be a nonempty string")
    return value


def _constant(value: str) -> None:
    raise ProtocolError("nonfinite JSON value")


def _loads(value: str | bytes) -> dict:
    try:
        return _object(json.loads(value, parse_constant=_constant), "JSON")
    except (ValueError, UnicodeError, RecursionError) as exc:
        raise ProtocolError("malformed JSON object") from exc


def _dumps(value: Any) -> str:
    try:
        return json.dumps(value, ensure_ascii=False, allow_nan=False, separators=(",", ":"))
    except (TypeError, ValueError, RecursionError) as exc:
        raise ProtocolError("unserializable JSON value") from exc


def _blocks(content: Any) -> list[dict]:
    if content is None or content == "":
        return []
    if isinstance(content, str):
        return [{"type": "text", "text": content}]
    if not isinstance(content, list):
        raise ProtocolError("message content must be text or blocks")
    result = []
    for raw in content:
        block = copy.deepcopy(_object(raw, "content block"))
        if block.get("type") == "image_url":
            image = block.get("image_url")
            url = _text(image.get("url") if isinstance(image, dict) else image, "image URL")
            if url.startswith("data:"):
                metadata, separator, data = url[5:].partition(",")
                if not separator or not metadata.endswith(";base64") or not data:
                    raise ProtocolError("image data URL must contain base64")
                source = {"type": "base64", "media_type": metadata[:-7], "data": data}
            elif url.startswith(("https://", "http://")):
                source = {"type": "url", "url": url}
            else:
                raise ProtocolError("unsupported image URL")
            block = {"type": "image", "source": source}
        result.append(block)
    return result


def request(payload: dict, default_max_tokens: int) -> dict:
    body = copy.deepcopy(_object(payload, "request"))
    messages = body.pop("messages", [])
    if not isinstance(messages, list):
        raise ProtocolError("messages must be a list")
    system = _blocks(body.pop("system", None))
    native = []
    for raw in messages:
        message = _object(raw, "message")
        role = message.get("role")
        if role in {"system", "developer"}:
            system.extend(_blocks(message.get("content")))
            continue
        if role == "tool":
            blocks = [{"type": "tool_result", "tool_use_id": _text(
                message.get("tool_call_id"), "tool_call_id"),
                "content": message.get("content") if isinstance(message.get("content"), str)
                else _blocks(message.get("content"))}]
            role = "user"
        elif role in {"assistant", "user"}:
            blocks = _blocks(message.get("content"))
            calls = message.get("tool_calls") or []
            if not isinstance(calls, list) or (calls and role != "assistant"):
                raise ProtocolError("tool calls require an assistant message")
            for call in calls:
                call = _object(call, "tool call")
                function = _object(call.get("function"), "tool function")
                if call.get("type") != "function":
                    raise ProtocolError("unsupported tool call type")
                arguments = function.get("arguments")
                if not isinstance(arguments, str):
                    raise ProtocolError("tool arguments must be a JSON string")
                blocks.append({"type": "tool_use", "id": _text(call.get("id"), "tool id"),
                               "name": _text(function.get("name"), "tool name"),
                               "input": _loads(arguments)})
        else:
            raise ProtocolError("unsupported message role")
        # Anthropic groups consecutive tool results in one user content list.
        if native and native[-1]["role"] == role:
            native[-1]["content"].extend(blocks)
        else:
            native.append({"role": role, "content": blocks})
    body["messages"] = native
    if system:
        # Keep cache-control on content blocks instead of stringifying the dict.
        body["system"] = ("\n\n".join(b["text"] for b in system)
                          if all(set(b) == {"type", "text"} and b["type"] == "text"
                                 for b in system) else system)
    tools = body.get("tools")
    if tools is not None:
        if not isinstance(tools, list):
            raise ProtocolError("tools must be a list")
        converted = []
        for raw in tools:
            tool = _object(raw, "tool")
            if tool.get("type") == "function":
                function = _object(tool.get("function"), "tool function")
                translated = {"name": _text(function.get("name"), "tool name"),
                              "input_schema": _object(function.get("parameters", {}), "tool schema")}
                for key in ("description", "strict", "cache_control"):
                    if key in function:
                        translated[key] = function[key]
                if "cache_control" in tool:
                    translated["cache_control"] = tool["cache_control"]
                converted.append(translated)
            else:
                # Explicit native tool definitions can still use the native path.
                _text(tool.get("name"), "native tool name")
                converted.append(tool)
        body["tools"] = converted
    choice = body.get("tool_choice")
    if isinstance(choice, str):
        if choice not in {"auto", "required", "none"}:
            raise ProtocolError("unsupported tool_choice")
        body["tool_choice"] = {"type": "any" if choice == "required" else choice}
    elif choice is not None:
        choice = _object(choice, "tool_choice")
        if choice.get("type") == "function":
            body["tool_choice"] = {"type": "tool", "name": _text(
                _object(choice.get("function"), "tool choice function").get("name"), "tool name")}
    if "parallel_tool_calls" in body:
        parallel = body.pop("parallel_tool_calls")
        if not isinstance(parallel, bool):
            raise ProtocolError("parallel_tool_calls must be boolean")
        if body.get("tools"):
            body.setdefault("tool_choice", {"type": "auto"})["disable_parallel_tool_use"] = not parallel
    form = body.pop("response_format", None)
    if form is not None:
        form = _object(form, "response_format")
        if form.get("type") == "json_schema":
            schema = _object(_object(form.get("json_schema"), "json_schema").get("schema"), "schema")
            output = _object(body.setdefault("output_config", {}), "output_config")
            if "format" in output:
                raise ProtocolError("conflicting structured output formats")
            output["format"] = {"type": "json_schema", "schema": schema}
        elif form.get("type") != "text":
            raise ProtocolError("Anthropic structured output requires an explicit JSON schema")
    if "max_completion_tokens" in body:
        if "max_tokens" in body and body["max_tokens"] != body["max_completion_tokens"]:
            raise ProtocolError("conflicting token limits")
        body["max_tokens"] = body.pop("max_completion_tokens")
    body.setdefault("max_tokens", default_max_tokens)
    if "stop" in body:
        stop = body.pop("stop")
        if stop is not None:
            body["stop_sequences"] = [stop] if isinstance(stop, str) else stop
    body.pop("stream_options", None)
    if body.pop("n", 1) != 1:
        raise ProtocolError("Anthropic supports one completion per request")
    return body


def finish_reason(reason: Any) -> str:
    mapping = {"end_turn": "stop", "stop_sequence": "stop", "tool_use": "tool_calls",
               "max_tokens": "length", "model_context_window_exceeded": "length",
               "refusal": "content_filter"}
    if not isinstance(reason, str) or reason not in mapping:
        raise ProtocolError("missing or unsupported Anthropic stop reason")
    return mapping[reason]


def usage(native: Any) -> dict | None:
    if native is None:
        return None
    native = _object(native, "usage")
    keys = ("input_tokens", "output_tokens", "cache_read_input_tokens", "cache_creation_input_tokens")
    for key in keys:
        if key in native and (type(native[key]) is not int or not 0 <= native[key] <= 2**53):
            raise ProtocolError("invalid usage counter")
    if "input_tokens" not in native or "output_tokens" not in native:
        return None  # missing measurement must settle against the reserved upper bound
    read, written = native.get(keys[2], 0), native.get(keys[3], 0)
    prompt, completion = native["input_tokens"] + read + written, native["output_tokens"]
    return {"prompt_tokens": prompt, "completion_tokens": completion,
            "total_tokens": prompt + completion,
            "cache_read_input_tokens": read, "cache_creation_input_tokens": written}


def response(data: dict) -> dict:
    data = _object(data, "response")
    blocks = data.get("content")
    if not isinstance(blocks, list):
        raise ProtocolError("Anthropic content must be a list")
    text, calls = [], []
    call_ids = set()
    for block in blocks:
        block = _object(block, "response content block")
        if block.get("type") == "text":
            value = block.get("text")
            if not isinstance(value, str):
                raise ProtocolError("invalid response text")
            text.append(value)
        elif block.get("type") == "tool_use":
            tool_id = _text(block.get("id"), "tool id")
            if tool_id in call_ids:
                raise ProtocolError("duplicate tool call identity")
            call_ids.add(tool_id)
            calls.append({"id": _text(block.get("id"), "tool id"), "type": "function",
                          "function": {"name": _text(block.get("name"), "tool name"),
                                       "arguments": _dumps(_object(block.get("input"), "tool input"))}})
        elif block.get("type") not in {"thinking", "redacted_thinking"}:
            raise ProtocolError("unsupported Anthropic response block")
    reason = finish_reason(data.get("stop_reason"))
    if not "".join(text).strip() and not calls and reason != "content_filter":
        raise ProtocolError("empty Anthropic response")
    if reason == "tool_calls" and not calls:
        raise ProtocolError("tool_use stop without a tool call")
    if calls and reason != "tool_calls":
        raise ProtocolError("tool calls lack a completed tool_use stop")
    message = {"role": "assistant", "content": "".join(text) or None}
    if calls:
        message["tool_calls"] = calls
    result = {"id": data.get("id"), "object": "chat.completion", "model": data.get("model"),
              "choices": [{"index": 0, "message": message, "finish_reason": reason}]}
    measured = usage(data.get("usage"))
    if measured is not None:
        result["usage"] = measured
    return result


class Stream:
    """Bounded SSE parser. Final usage and finish appear only at message_stop."""

    def __init__(self):
        self.buffer = b""
        self.data: list[bytes] = []
        self.event_size = 0
        self.events = 0
        self.started = self.done = self.has_content = False
        self.message: dict = {}
        self.blocks: dict[int, dict] = {}
        self.tool_count = 0
        self.tool_ids: set[str] = set()
        self.reason: str | None = None
        self.final_usage = False

    def _chunk(self, delta: dict, *, reason: str | None = None, measured: dict | None = None) -> bytes:
        body = {"id": self.message.get("id"), "object": "chat.completion.chunk",
                "model": self.message.get("model"),
                "choices": [{"index": 0, "delta": delta, "finish_reason": reason}]}
        if measured is not None:
            body["usage"] = measured
        return ("data: " + _dumps(body) + "\n\n").encode()

    def feed(self, chunk: bytes) -> Iterator[bytes]:
        self.buffer += chunk
        start = 0
        while (end := self.buffer.find(b"\n", start)) != -1:
            line = self.buffer[start:end]
            start = end + 1
            line = line.rstrip(b"\r")
            self.event_size += len(line) + 1
            if self.event_size > MAX_EVENT_BYTES:
                raise ProtocolError("Anthropic stream event exceeds size limit")
            if line.startswith(b"data:"):
                self.data.append(line[5:].lstrip())
            elif not line:
                data, self.data = self.data, []
                self.event_size = 0
                if data:
                    self.events += 1
                    if self.events > MAX_EVENTS:
                        raise ProtocolError("Anthropic stream exceeds event limit")
                    yield from self._event(_loads(b"\n".join(data)))
                    if self.done:
                        self.buffer = b""
                        return
        self.buffer = self.buffer[start:]
        if len(self.buffer) + self.event_size > MAX_EVENT_BYTES:
            raise ProtocolError("Anthropic stream event exceeds size limit")

    def _event(self, event: dict) -> Iterator[bytes]:
        kind = event.get("type")
        if kind == "ping":
            return
        if kind == "error":
            raise ProtocolError("Anthropic provider stream error")
        if kind == "message_start":
            if self.started:
                raise ProtocolError("duplicate message_start")
            self.message = _object(event.get("message"), "message_start")
            usage(self.message.get("usage"))  # validate, never emit provisional usage
            self.started = True
            yield self._chunk({"role": "assistant"})
            return
        if not self.started:
            raise ProtocolError("Anthropic stream missing message_start")
        if kind == "content_block_start":
            index = event.get("index")
            if type(index) is not int or index < 0 or index in self.blocks or len(self.blocks) >= MAX_BLOCKS:
                raise ProtocolError("invalid or duplicate content block index")
            block = _object(event.get("content_block"), "content block")
            entry = {"kind": block.get("type"), "closed": False, "arguments": ""}
            self.blocks[index] = entry
            if entry["kind"] == "tool_use":
                tool_id = _text(block.get("id"), "tool id")
                if tool_id in self.tool_ids:
                    raise ProtocolError("duplicate tool call identity")
                self.tool_ids.add(tool_id)
                entry["tool_index"] = self.tool_count
                self.tool_count += 1
                value = _object(block.get("input"), "tool input")
                entry["arguments"] = _dumps(value) if value else ""
                yield self._chunk({"tool_calls": [{"index": entry["tool_index"],
                    "id": tool_id, "type": "function", "function": {
                    "name": _text(block.get("name"), "tool name"), "arguments": entry["arguments"]}}]})
                self.has_content = True
            elif entry["kind"] == "text":
                text = block.get("text", "")
                if not isinstance(text, str):
                    raise ProtocolError("invalid text content block")
                if text:
                    self.has_content |= bool(text.strip())
                    yield self._chunk({"content": text})
            elif entry["kind"] not in {"thinking", "redacted_thinking"}:
                raise ProtocolError("unsupported Anthropic stream block")
            return
        if kind in {"content_block_delta", "content_block_stop"}:
            index = event.get("index")
            if type(index) is not int or index not in self.blocks or self.blocks[index]["closed"]:
                raise ProtocolError("delta or stop without an open content block")
            entry = self.blocks[index]
            if kind == "content_block_stop":
                if entry["kind"] == "tool_use":
                    if not entry["arguments"]:
                        yield self._chunk({"tool_calls": [{"index": entry["tool_index"],
                                           "function": {"arguments": "{}"}}]})
                    _loads(entry["arguments"] or "{}")
                entry["arguments"] = ""
                entry["closed"] = True
                return
            delta = _object(event.get("delta"), "content delta")
            if entry["kind"] == "text" and delta.get("type") == "text_delta":
                text = delta.get("text")
                if not isinstance(text, str):
                    raise ProtocolError("invalid text delta")
                self.has_content |= bool(text.strip())
                yield self._chunk({"content": text})
            elif entry["kind"] == "tool_use" and delta.get("type") == "input_json_delta":
                value = delta.get("partial_json")
                if not isinstance(value, str):
                    raise ProtocolError("invalid tool JSON delta")
                entry["arguments"] += value
                if len(entry["arguments"].encode()) > MAX_EVENT_BYTES:
                    raise ProtocolError("tool arguments exceed size limit")
                yield self._chunk({"tool_calls": [{"index": entry["tool_index"],
                                   "function": {"arguments": value}}]})
            elif entry["kind"] not in {"thinking", "redacted_thinking"}:
                raise ProtocolError("content delta does not match its block")
            return
        if kind == "message_delta":
            delta = _object(event.get("delta"), "message delta")
            if delta.get("stop_reason") is not None:
                reason = finish_reason(delta["stop_reason"])
                if self.reason and self.reason != reason:
                    raise ProtocolError("conflicting Anthropic stop reasons")
                self.reason = reason
            measured = _object(event.get("usage", {}), "message usage")
            usage(measured)
            accumulated = _object(self.message.setdefault("usage", {}), "message usage")
            for key in ("input_tokens", "output_tokens", "cache_read_input_tokens", "cache_creation_input_tokens"):
                if key in measured and key in accumulated and measured[key] < accumulated[key]:
                    raise ProtocolError("cumulative usage decreased")
            accumulated.update(measured)
            self.final_usage |= "output_tokens" in measured
            return
        if kind == "message_stop":
            if not self.reason or any(not b["closed"] for b in self.blocks.values()):
                raise ProtocolError("Anthropic message stopped before completion")
            if not self.has_content and self.reason != "content_filter":
                raise ProtocolError("empty Anthropic stream")
            if self.reason == "tool_calls" and not self.tool_count:
                raise ProtocolError("tool_use stop without a tool call")
            if self.tool_count and self.reason != "tool_calls":
                raise ProtocolError("tool calls lack a completed tool_use stop")
            measured = usage(self.message.get("usage")) if self.final_usage else None
            self.done = True
            yield self._chunk({}, reason=self.reason, measured=measured)
            yield b"data: [DONE]\n\n"
            return
        # Versioned providers may add informational events; bounds still apply.

    def finish(self) -> None:
        if not self.done:
            raise ProtocolError("incomplete Anthropic stream: missing message_stop")
