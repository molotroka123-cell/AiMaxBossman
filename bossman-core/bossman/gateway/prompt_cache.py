"""Provider-aware prompt-cache request shaping and usage normalization.

Only OpenRouter payloads are changed.  The module never stores prompt text:
callers receive hashes, token estimates, and numeric provider evidence only.
"""
from __future__ import annotations

import copy
import json
import re
from dataclasses import dataclass
from decimal import Decimal, InvalidOperation
from hashlib import sha256
from typing import Any, Mapping


ALLOWED_TTLS = {"5m": 300, "1h": 3600}
_TRUSTED_SESSION = re.compile(r"^bossman-pc-[0-9a-f]{32}$")


@dataclass(frozen=True, slots=True)
class PromptCachePolicy:
    enabled: bool = True
    ttl: str = "5m"
    session_affinity: bool = True

    def cache_control(self) -> dict[str, str]:
        control = {"type": "ephemeral"}
        if self.ttl == "1h":
            control["ttl"] = "1h"
        return control


def stable_session_id(*parts: object) -> str:
    """Return an opaque, stable OpenRouter affinity key for one Bossman session."""
    normalized = [str(part).strip() for part in parts if part is not None and str(part).strip()]
    if not normalized:
        return ""
    digest = sha256("\x1f".join(normalized).encode("utf-8")).hexdigest()[:32]
    return f"bossman-pc-{digest}"


def is_trusted_session_id(value: object) -> bool:
    return isinstance(value, str) and len(value) <= 256 and bool(_TRUSTED_SESSION.fullmatch(value))


def normalize_ttl(value: object, default: str = "5m") -> tuple[str, bool]:
    fallback = default if default in ALLOWED_TTLS else "5m"
    if value is None or value == "":
        return fallback, False
    candidate = str(value).strip().lower()
    if candidate in ALLOWED_TTLS:
        return candidate, False
    return fallback, True


def _canonical_prefix(payload: Mapping[str, Any]) -> tuple[str | None, int]:
    messages = payload.get("messages")
    leading: list[Any] = []
    if isinstance(messages, list):
        for message in messages:
            if not isinstance(message, Mapping) or message.get("role") not in {"system", "developer"}:
                break
            leading.append(message)
    prefix = {"tools": payload.get("tools") or [], "messages": leading}
    if not prefix["tools"] and not prefix["messages"]:
        return None, 0
    encoded = json.dumps(prefix, ensure_ascii=False, sort_keys=True,
                         separators=(",", ":"), default=str).encode("utf-8")
    return sha256(encoded).hexdigest(), max(1, len(encoded.decode("utf-8")) // 3)


def _explicit_anthropic_breakpoint(payload: dict[str, Any], control: Mapping[str, str]) -> bool:
    messages = payload.get("messages")
    if not isinstance(messages, list):
        return False
    candidate: dict[str, Any] | None = None
    for message in messages:
        if not isinstance(message, dict) or message.get("role") not in {"system", "developer"}:
            break
        candidate = message
    if candidate is None:
        return False
    content = candidate.get("content")
    if isinstance(content, str):
        candidate["content"] = [{"type": "text", "text": content,
                                  "cache_control": dict(control)}]
        return True
    if isinstance(content, list):
        for block in reversed(content):
            if isinstance(block, dict) and block.get("type") == "text":
                block["cache_control"] = dict(control)
                return True
    return False


def _anthropic_model(model: str) -> bool:
    value = (model or "").lower().lstrip("~")
    return value.startswith("anthropic/") or value.startswith("claude-")


def minimum_cacheable_tokens(model: str) -> int | None:
    value = (model or "").lower()
    if "claude-opus-5" in value:
        return 512
    return None


def prepare_provider_payload(
    payload: Mapping[str, Any], *, provider_kind: str, provider_model: str,
    session_id: str = "", requested_ttl: object = None, default_ttl: str = "5m",
    enabled: bool = True, session_affinity: bool = True, endpoint: str = "/v1/chat/completions",
) -> tuple[dict[str, Any], dict[str, Any]]:
    """Copy and shape a payload; unsupported providers are returned unchanged."""
    shaped = copy.deepcopy(dict(payload))
    ttl, invalid_ttl = normalize_ttl(requested_ttl, default_ttl)
    prefix_hash, prefix_tokens = _canonical_prefix(payload)
    meta: dict[str, Any] = {
        "provider": (provider_kind or "openai").lower(),
        "model": provider_model,
        "enabled": bool(enabled),
        "ttl": ttl,
        "mode": "none",
        "session_affinity": False,
        "session_id_hash": None,
        "prefix_hash": prefix_hash,
        "prefix_tokens": prefix_tokens,
        "cache_control_applied": False,
        "degraded_reason": "invalid metadata" if invalid_ttl else None,
    }
    if meta["provider"] != "openrouter":
        meta["state"] = "UNSUPPORTED"
        meta["miss_reason"] = "unsupported provider"
        return shaped, meta
    if not enabled:
        meta["state"] = "UNSUPPORTED"
        meta["miss_reason"] = "caching disabled"
        return shaped, meta

    if session_affinity and session_id:
        if is_trusted_session_id(session_id):
            shaped["session_id"] = session_id
            meta["session_affinity"] = True
            meta["session_id_hash"] = sha256(session_id.encode("utf-8")).hexdigest()[:16]
        else:
            meta["degraded_reason"] = "invalid metadata"

    if _anthropic_model(provider_model):
        control = PromptCachePolicy(ttl=ttl).cache_control()
        if endpoint.rstrip("/").endswith("/responses"):
            shaped["cache_control"] = control
            meta["mode"] = "anthropic-automatic"
            meta["cache_control_applied"] = True
        elif _explicit_anthropic_breakpoint(shaped, control):
            meta["mode"] = "anthropic-explicit"
            meta["cache_control_applied"] = True
        else:
            # Official OpenRouter fallback for requests without a cacheable
            # message block.  It remains a request optimization only.
            shaped["cache_control"] = control
            meta["mode"] = "anthropic-automatic"
            meta["cache_control_applied"] = True
    else:
        meta["mode"] = "provider-implicit"
    meta["state"] = "DEGRADED" if meta["degraded_reason"] else "COLD"
    meta["miss_reason"] = meta["degraded_reason"]
    return shaped, meta


def _int(value: object) -> int:
    try:
        return max(0, int(value or 0))
    except (TypeError, ValueError, OverflowError):
        return 0


def _decimal(value: object) -> Decimal | None:
    if value is None or value == "":
        return None
    try:
        result = Decimal(str(value))
    except (InvalidOperation, TypeError, ValueError):
        return None
    return result if result.is_finite() and result >= 0 else None


def extract_cache_usage(body: Mapping[str, Any] | None) -> dict[str, Any]:
    usage = body.get("usage") if isinstance(body, Mapping) else None
    usage = usage if isinstance(usage, Mapping) else {}
    prompt_details = usage.get("prompt_tokens_details") or usage.get("input_tokens_details") or {}
    prompt_details = prompt_details if isinstance(prompt_details, Mapping) else {}
    cached = max(
        _int(prompt_details.get("cached_tokens")),
        _int(prompt_details.get("cache_read_tokens")),
        _int(usage.get("cached_tokens")),
        _int(usage.get("cache_read_tokens")),
        _int(usage.get("cache_read_input_tokens")),
    )
    written = max(
        _int(prompt_details.get("cache_write_tokens")),
        _int(usage.get("cache_write_tokens")),
        _int(usage.get("cache_creation_input_tokens")),
    )
    has_normalized_prompt = usage.get("prompt_tokens") is not None
    prompt = _int(usage.get("prompt_tokens") if has_normalized_prompt else usage.get("input_tokens"))
    if not has_normalized_prompt and (cached or written):
        prompt += cached + written
    completion = _int(usage.get("completion_tokens") or usage.get("output_tokens"))
    cost_details = usage.get("cost_details")
    cost_details = cost_details if isinstance(cost_details, Mapping) else {}
    provider_cost = _decimal(usage.get("cost"))
    if provider_cost is None:
        provider_cost = _decimal(cost_details.get("upstream_inference_cost"))
    discount = _decimal(usage.get("cache_discount"))
    return {
        "prompt_tokens": prompt,
        "completion_tokens": completion,
        "cached_tokens": cached,
        "cache_read_tokens": cached,
        "cache_write_tokens": written,
        "fresh_input_tokens": max(0, prompt - cached - written),
        "provider_cost": provider_cost,
        "cache_discount": discount,
    }


def cache_metadata_rejected(message: str, status_code: int | None) -> bool:
    # The retry is only attempted when the Gateway itself added optional cache
    # metadata and no response bytes were emitted.  Any validation-class 4xx
    # may be a provider that reports the bad parameter generically; retrying
    # the original payload once is the fail-open contract.
    return status_code in {400, 422}


class SSEUsageCollector:
    """Observe numeric SSE usage while forwarding every byte unchanged."""

    def __init__(self, max_line_bytes: int = 1024 * 1024):
        self._buffer = b""
        self._max_line_bytes = max_line_bytes
        self.body: dict[str, Any] | None = None

    def feed(self, chunk: bytes) -> None:
        self._buffer += chunk
        if len(self._buffer) > self._max_line_bytes:
            self._buffer = b""
            return
        while b"\n" in self._buffer:
            line, self._buffer = self._buffer.split(b"\n", 1)
            self._line(line)

    def finish(self) -> None:
        if self._buffer:
            self._line(self._buffer)
        self._buffer = b""

    def _line(self, line: bytes) -> None:
        line = line.strip()
        if not line.startswith(b"data:"):
            return
        raw = line[5:].strip()
        if not raw or raw == b"[DONE]":
            return
        try:
            value = json.loads(raw)
        except (UnicodeDecodeError, json.JSONDecodeError):
            return
        if isinstance(value, dict) and isinstance(value.get("usage"), dict):
            self.body = {"usage": value["usage"]}
