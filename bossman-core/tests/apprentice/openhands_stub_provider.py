"""A local OpenAI-compatible endpoint that scripts the OpenHands agent loop.

This exists so the OpenHands path can be exercised END TO END with nothing
mocked on our side: the real `OpenHandsClient` spawns the real sidecar, which
imports the real `openhands-sdk`, builds a real `Agent`/`Conversation`, and
drives the real `file_editor` tool against a real git worktree. The only thing
replaced is the model's weights — and only because a paid provider key is not
available in every environment where these tests must run.

That substitution is deliberate and bounded. A mocked *client* would prove
nothing about the sidecar contract; a scripted *model* still exercises every
line of code Bossman owns, plus the SDK's own tool dispatch. What it cannot
prove is that a real model chooses sensible actions, which is why the live
acceptance against a real provider remains a separate, honestly-reported step.

The stub reads the tool schemas the SDK advertises rather than hard-coding
them, so an SDK upgrade that renames an argument makes the test fail loudly
instead of silently drifting into testing a fiction.
"""
from __future__ import annotations

import json
import threading
from http.server import BaseHTTPRequestHandler, HTTPServer
from typing import Any


class ScriptedProvider:
    """Serves POST /chat/completions with a scripted sequence of turns."""

    def __init__(self, plan: list[dict[str, Any]]):
        #: Each entry is either {"tool": name, "arguments": {...}} or
        #: {"text": "..."} for the final assistant answer.
        self.plan = list(plan)
        self.requests: list[dict[str, Any]] = []
        self._server: HTTPServer | None = None
        self._thread: threading.Thread | None = None

    # ------------------------------------------------------------------ http

    def _handler(self) -> type[BaseHTTPRequestHandler]:
        provider = self

        class Handler(BaseHTTPRequestHandler):
            protocol_version = "HTTP/1.1"

            def log_message(self, *args):        # keep the test output readable
                pass

            def _json(self, status: int, body: dict) -> None:
                blob = json.dumps(body).encode("utf-8")
                self.send_response(status)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(blob)))
                self.end_headers()
                self.wfile.write(blob)

            def do_GET(self):                    # /models and health probes
                self._json(200, {"data": [{"id": "stub-model"}]})

            def do_POST(self):
                length = int(self.headers.get("Content-Length") or 0)
                try:
                    payload = json.loads(self.rfile.read(length) or b"{}")
                except ValueError:
                    payload = {}
                provider.requests.append(payload)
                index = len(provider.requests) - 1
                step = (provider.plan[index] if index < len(provider.plan)
                        else {"text": "done"})
                self._json(200, provider._turn(step, payload))

        return Handler

    # ------------------------------------------------------------- scripting

    def _turn(self, step: dict[str, Any], payload: dict[str, Any]) -> dict[str, Any]:
        base = {"id": "chatcmpl-stub", "object": "chat.completion",
                "created": 0, "model": payload.get("model") or "stub-model",
                "usage": {"prompt_tokens": 10, "completion_tokens": 5, "total_tokens": 15}}
        if "tool" in step:
            advertised = {t.get("function", {}).get("name")
                          for t in (payload.get("tools") or [])}
            if step["tool"] not in advertised:
                # Loud rather than silent: if the SDK stopped offering this tool,
                # the scripted plan is testing a fiction.
                return {**base, "choices": [{"index": 0, "finish_reason": "stop",
                        "message": {"role": "assistant",
                                    "content": f"STUB_ERROR: tool {step['tool']!r} not advertised; "
                                               f"available: {sorted(n for n in advertised if n)}"}}]}
            return {**base, "choices": [{
                "index": 0, "finish_reason": "tool_calls",
                "message": {"role": "assistant", "content": None, "tool_calls": [{
                    "id": f"call_{len(self.requests)}", "type": "function",
                    "function": {"name": step["tool"],
                                 "arguments": json.dumps(step.get("arguments") or {})}}]}}]}
        return {**base, "choices": [{"index": 0, "finish_reason": "stop",
                "message": {"role": "assistant", "content": step.get("text") or "done"}}]}

    # ------------------------------------------------------------- lifecycle

    def __enter__(self) -> "ScriptedProvider":
        self._server = HTTPServer(("127.0.0.1", 0), self._handler())
        self._thread = threading.Thread(target=self._server.serve_forever, daemon=True)
        self._thread.start()
        return self

    def __exit__(self, *exc) -> None:
        if self._server is not None:
            self._server.shutdown()
            self._server.server_close()
        if self._thread is not None:
            self._thread.join(timeout=5)

    @property
    def base_url(self) -> str:
        assert self._server is not None
        return f"http://127.0.0.1:{self._server.server_address[1]}/v1"

    def advertised_tools(self) -> list[str]:
        for payload in self.requests:
            names = [t.get("function", {}).get("name") for t in (payload.get("tools") or [])]
            if names:
                return sorted(n for n in names if n)
        return []
