"""Deterministic browser secret actuator for pre-bound login forms.

The model is used only before intake to identify safe field bindings. Secret
bytes are never sent to a model. This executor performs exact semantic fills
and verifies a fresh post-login condition.
"""
from __future__ import annotations

from dataclasses import dataclass
import ipaddress
import json
from typing import Any
from urllib.parse import urlsplit
import urllib.request


@dataclass(frozen=True)
class ApplyResult:
    success: bool
    verified: bool
    code: str = ""
    detail: str = ""


@dataclass(frozen=True)
class BoundField:
    secret_name: str
    role: str
    accessible_name: str

    def __post_init__(self):
        if self.role not in {"textbox"}:
            raise ValueError("only exact textbox bindings are supported")
        if not self.secret_name or not self.accessible_name:
            raise ValueError("secret field binding is incomplete")


@dataclass(frozen=True)
class BrowserSecretBinding:
    page_url_prefix: str
    fields: tuple[BoundField, ...]
    submit_role: str
    submit_name: str
    success_url_prefix: str = ""
    success_text: str = ""

    def __post_init__(self):
        if not self.page_url_prefix.startswith(("http://", "https://")):
            raise ValueError("page_url_prefix must be http(s)")
        if self.submit_role not in {"button"} or not self.submit_name:
            raise ValueError("submit binding is invalid")
        if not self.success_url_prefix and not self.success_text:
            raise ValueError("at least one independent success condition is required")


class PlaywrightSecretExecutor:
    """Low-level exact semantic actuator used by the local-model controller."""

    local_only = True
    network_isolated = True
    model_sees_secret = False
    local_model_controlled = False

    def __init__(self, page: Any, binding: BrowserSecretBinding):
        self.page = page
        self.binding = binding

    def _exact(self, role: str, name: str):
        loc = self.page.get_by_role(role, name=name, exact=True)
        count = loc.count()
        if count != 1:
            raise RuntimeError(f"bound target {role}:{name!r} has {count} matches")
        return loc.first

    async def apply(self, request, values: dict[str, bytearray]) -> ApplyResult:
        current = str(self.page.url or "")
        if not current.startswith(self.binding.page_url_prefix):
            return ApplyResult(False, False, "LOGIN_PAGE_IDENTITY_CHANGED")

        expected = {f.secret_name for f in self.binding.fields}
        if set(values) != expected:
            return ApplyResult(False, False, "LOGIN_SECRET_BINDING_MISMATCH")

        # Decode only at the final actuator boundary. Python strings cannot be
        # physically zeroized, so references are kept as short-lived as possible.
        try:
            for field in self.binding.fields:
                text = values[field.secret_name].decode("utf-8")
                self._exact(field.role, field.accessible_name).fill(text, timeout=10_000)
                del text
            self._exact(self.binding.submit_role, self.binding.submit_name).click(timeout=10_000)
            self.page.wait_for_load_state("domcontentloaded", timeout=20_000)
        except Exception:
            return ApplyResult(False, False, "LOGIN_ACTUATOR_ERROR")

        if self.binding.success_url_prefix and str(self.page.url or "").startswith(self.binding.success_url_prefix):
            return ApplyResult(True, True, "LOGIN_VERIFIED_BY_URL")
        if self.binding.success_text:
            try:
                loc = self.page.get_by_text(self.binding.success_text, exact=True)
                if loc.count() == 1 and loc.first.is_visible():
                    return ApplyResult(True, True, "LOGIN_VERIFIED_BY_POSTSTATE")
            except Exception:
                pass
        return ApplyResult(False, False, "LOGIN_POSTSTATE_NOT_VERIFIED")



def _loopback_api_base(value: str) -> str:
    parsed = urlsplit(str(value or "").strip())
    if parsed.scheme not in {"http", "https"} or parsed.username or parsed.password:
        raise ValueError("local model endpoint must be a plain http(s) URL")
    host = parsed.hostname or ""
    try:
        ip = ipaddress.ip_address(host)
    except ValueError as exc:
        raise ValueError("local model endpoint must use an explicit loopback IP") from exc
    if not ip.is_loopback:
        raise ValueError("local model endpoint is not loopback")
    return str(value).rstrip("/")


class LocalModelSecretExecutor:
    """One-shot local LLM controller over opaque secret handles.

    The model decides the order of fill/submit tool calls but never receives
    the sensitive values. The only network socket opened by this controller is
    an explicit loopback model endpoint; HTTP proxy environment is ignored.
    """

    local_only = True
    network_isolated = True
    model_sees_secret = False
    local_model_controlled = True

    def __init__(self, page: Any, binding: BrowserSecretBinding, *,
                 api_base: str, model: str, api_key: str = "", max_steps: int = 8,
                 transport=None):
        self.page = page
        self.binding = binding
        self.api_base = _loopback_api_base(api_base)
        self.model = str(model or "").strip()
        if not self.model:
            raise ValueError("local model id is required")
        self.api_key = str(api_key or "")
        self.max_steps = max(2, min(int(max_steps), 12))
        self._transport = transport
        self._actuator = PlaywrightSecretExecutor(page, binding)

    def _call_model(self, messages: list[dict], tools: list[dict]) -> dict:
        payload = {
            "model": self.model,
            "messages": messages,
            "tools": tools,
            "tool_choice": "required",
            "temperature": 0,
            "max_tokens": 800,
        }
        if self._transport is not None:
            return self._transport(payload)
        headers = {"Content-Type": "application/json"}
        if self.api_key:
            headers["Authorization"] = "Bearer " + self.api_key
        req = urllib.request.Request(
            self.api_base + "/chat/completions",
            data=json.dumps(payload).encode("utf-8"),
            headers=headers,
            method="POST",
        )
        opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
        with opener.open(req, timeout=30) as resp:  # noqa: S310 — loopback enforced above
            return json.loads(resp.read().decode("utf-8"))

    @staticmethod
    def _tool_calls(body: dict) -> list[dict]:
        choices = body.get("choices") if isinstance(body, dict) else None
        if not isinstance(choices, list) or not choices:
            raise RuntimeError("local model returned no choices")
        message = choices[0].get("message") if isinstance(choices[0], dict) else None
        calls = message.get("tool_calls") if isinstance(message, dict) else None
        if not isinstance(calls, list) or not calls:
            raise RuntimeError("local model returned no tool calls")
        return calls

    async def apply(self, request, values: dict[str, bytearray]) -> ApplyResult:
        current = str(self.page.url or "")
        if not current.startswith(self.binding.page_url_prefix):
            return ApplyResult(False, False, "LOGIN_PAGE_IDENTITY_CHANGED")

        allowed = {f.secret_name: f for f in self.binding.fields}
        if set(values) != set(allowed):
            return ApplyResult(False, False, "LOGIN_SECRET_BINDING_MISMATCH")

        tools = [
            {
                "type": "function",
                "function": {
                    "name": "fill_secret",
                    "description": "Fill one already-bound login field from an opaque local secret handle.",
                    "parameters": {
                        "type": "object",
                        "properties": {"field": {"type": "string", "enum": sorted(allowed)}},
                        "required": ["field"],
                        "additionalProperties": False,
                    },
                },
            },
            {
                "type": "function",
                "function": {
                    "name": "submit_login",
                    "description": "Submit the already-bound login form after every required field was filled.",
                    "parameters": {"type": "object", "properties": {}, "additionalProperties": False},
                },
            },
        ]
        field_lines = [
            {"field": f.secret_name, "label": f.accessible_name, "secret_handle": f"secret:{f.secret_name}"}
            for f in self.binding.fields
        ]
        messages = [{
            "role": "system",
            "content": (
                "You are the offline local Bossman credential-entry controller. "
                "Sensitive values are hidden behind opaque handles and must never be requested, echoed or guessed. "
                "Call fill_secret exactly once for each listed field, then call submit_login. "
                "Do not call any other tool and do not return sensitive text."
            ),
        }, {
            "role": "user",
            "content": json.dumps({"target": request.target, "fields": field_lines}, ensure_ascii=False),
        }]

        filled: set[str] = set()
        submitted = False
        try:
            for _ in range(self.max_steps):
                body = self._call_model(messages, tools)
                calls = self._tool_calls(body)
                assistant = {"role": "assistant", "content": "", "tool_calls": calls}
                messages.append(assistant)
                for idx, call in enumerate(calls):
                    fn = call.get("function") if isinstance(call, dict) else None
                    name = str((fn or {}).get("name") or "")
                    raw_args = (fn or {}).get("arguments") or "{}"
                    try:
                        args = json.loads(raw_args) if isinstance(raw_args, str) else dict(raw_args)
                    except Exception:
                        return ApplyResult(False, False, "LOCAL_MODEL_TOOL_ARGS_INVALID")
                    call_id = str(call.get("id") or f"call_{idx}")
                    if name == "fill_secret":
                        field_name = str(args.get("field") or "")
                        if field_name not in allowed or field_name in filled:
                            return ApplyResult(False, False, "LOCAL_MODEL_FIELD_ORDER_INVALID")
                        field = allowed[field_name]
                        # Plaintext is decoded only at this final local actuator boundary.
                        text = values[field_name].decode("utf-8")
                        try:
                            self._actuator._exact(field.role, field.accessible_name).fill(text, timeout=10_000)
                        finally:
                            del text
                        filled.add(field_name)
                        result_text = "field filled from local opaque handle"
                    elif name == "submit_login":
                        if filled != set(allowed):
                            return ApplyResult(False, False, "LOCAL_MODEL_SUBMIT_BEFORE_ALL_FIELDS")
                        self._actuator._exact(self.binding.submit_role, self.binding.submit_name).click(timeout=10_000)
                        self.page.wait_for_load_state("domcontentloaded", timeout=20_000)
                        submitted = True
                        result_text = "form submitted"
                    else:
                        return ApplyResult(False, False, "LOCAL_MODEL_TOOL_NOT_ALLOWED")
                    messages.append({"role": "tool", "tool_call_id": call_id, "content": result_text})
                if submitted:
                    break
        except Exception:
            return ApplyResult(False, False, "LOCAL_MODEL_ENTRY_ERROR")

        if not submitted:
            return ApplyResult(False, False, "LOCAL_MODEL_DID_NOT_SUBMIT")
        if self.binding.success_url_prefix and str(self.page.url or "").startswith(self.binding.success_url_prefix):
            return ApplyResult(True, True, "LOGIN_VERIFIED_BY_URL")
        if self.binding.success_text:
            try:
                loc = self.page.get_by_text(self.binding.success_text, exact=True)
                if loc.count() == 1 and loc.first.is_visible():
                    return ApplyResult(True, True, "LOGIN_VERIFIED_BY_POSTSTATE")
            except Exception:
                pass
        return ApplyResult(False, False, "LOGIN_POSTSTATE_NOT_VERIFIED")
