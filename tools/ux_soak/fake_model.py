"""Tiny OpenAI-compatible model stub for the soak (no real model is called).

Model names select behaviour:
  * ``soak-fast``  — answers after ~0.3 s;
  * ``soak-slow``  — answers after SLOW_SECONDS (keeps a task ``running`` across a restart);
  * ``soak-500``   — HTTP 500 (provider error path).
Any other name answers fast. The reply echoes the last user message's first 60 chars so
history can be checked end-to-end.
"""
from __future__ import annotations

import json
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

SLOW_SECONDS = 25.0


class _Handler(BaseHTTPRequestHandler):
    server_version = "soak-fake/1"

    def log_message(self, *_a):  # quiet
        pass

    def _json(self, code: int, body: dict) -> None:
        raw = json.dumps(body).encode()
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(raw)))
        self.end_headers()
        self.wfile.write(raw)

    def do_GET(self):  # noqa: N802
        if self.path.rstrip("/").endswith("/models"):
            self._json(200, {"object": "list", "data": [
                {"id": n, "object": "model"} for n in ("soak-fast", "soak-slow", "soak-500")]})
        else:
            self._json(404, {"error": {"message": "not found"}})

    def do_POST(self):  # noqa: N802
        n = int(self.headers.get("Content-Length") or 0)
        try:
            body = json.loads(self.rfile.read(n) or b"{}")
        except ValueError:
            body = {}
        model = str(body.get("model") or "")
        self.server.calls += 1  # type: ignore[attr-defined]
        self.server.last_messages = body.get("messages") or []  # type: ignore[attr-defined]
        if model == "soak-500":
            self._json(500, {"error": {"message": "soak stub: induced failure"}})
            return
        time.sleep(SLOW_SECONDS if model == "soak-slow" else 0.3)
        last = ""
        for m in reversed(body.get("messages") or []):
            if m.get("role") == "user":
                c = m.get("content")
                last = c if isinstance(c, str) else json.dumps(c, ensure_ascii=False)
                break
        text = f"soak-ok: {last.strip()[:60]}"
        try:
            self._json(200, {
                "id": "soak", "object": "chat.completion", "model": model,
                "choices": [{"index": 0, "finish_reason": "stop",
                             "message": {"role": "assistant", "content": text}}],
                "usage": {"prompt_tokens": 10, "completion_tokens": 5},
            })
        except (BrokenPipeError, ConnectionResetError):
            pass  # backend was killed mid-call


def start(port: int) -> ThreadingHTTPServer:
    srv = ThreadingHTTPServer(("127.0.0.1", port), _Handler)
    srv.calls = 0  # type: ignore[attr-defined]
    srv.last_messages = []  # type: ignore[attr-defined]  (what the caller sent: context checks)
    srv.daemon_threads = True
    threading.Thread(target=srv.serve_forever, name="soak-fake-model", daemon=True).start()
    return srv
