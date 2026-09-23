"""Offline mock of the Jev decision API: a real HTTP server on 127.0.0.1.

No paid calls, no external network. Each test scripts the responses; every
received request is recorded (headers + JSON body) so tests can check what left
the process.
"""
from __future__ import annotations

import json
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

FAKE_KEY = "jev-FAKE-test-key-not-real-7c1d"   # ci-secret-scan: allow (test canary)


def choice(ids, pick, confidence=0.9):
    ids = list(ids)
    return {"choice": pick, "confidence": confidence,
            "probabilities": {i: (1.0 if i == pick else 0.0) for i in ids}}


def answer_all(questions: dict, picks: dict | None = None, confidence: float = 0.9) -> dict:
    picks = picks or {}
    answers = {}
    for name, q in questions.items():
        ids = list(q["criteria"])
        answers[name] = choice(ids, picks.get(name, ids[0]), confidence)
    return {"model": "jev-mock", "answers": answers, "usage": {"input_tokens": 11, "output_tokens": 3}}


class MockJev:
    """``script`` items: ("json", payload | callable(req)->payload) | ("status", code[, body])
    | ("sleep", seconds, then) | ("raw", bytes). The last item repeats."""

    def __init__(self):
        self.requests: list[dict] = []
        self.script: list = [("json", lambda req: answer_all(req["questions"]))]
        mock = self

        class Handler(BaseHTTPRequestHandler):
            def log_message(self, *_a):
                pass

            def do_POST(self):  # noqa: N802
                length = int(self.headers.get("Content-Length") or 0)
                body = self.rfile.read(length)
                try:
                    req = json.loads(body)
                except ValueError:
                    req = {}
                mock.requests.append({"headers": dict(self.headers.items()), "body": req})
                step = mock.script[min(len(mock.requests) - 1, len(mock.script) - 1)]
                if step[0] == "sleep":
                    time.sleep(step[1])
                    step = step[2]
                kind = step[0]
                if kind == "status":
                    payload = step[2] if len(step) > 2 else b'{"error":"x"}'
                    self.send_response(step[1])
                    self.send_header("Content-Type", "application/json")
                    self.send_header("Content-Length", str(len(payload)))
                    self.end_headers()
                    self.wfile.write(payload)
                    return
                if kind == "raw":
                    payload = step[1]
                else:
                    data = step[1](req) if callable(step[1]) else step[1]
                    payload = json.dumps(data).encode()
                self.send_response(200)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(payload)))
                self.end_headers()
                try:
                    self.wfile.write(payload)
                except OSError:
                    pass

        self.server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        self.server.daemon_threads = True
        threading.Thread(target=self.server.serve_forever, daemon=True).start()
        self.url = f"http://127.0.0.1:{self.server.server_address[1]}/v1/systemone"

    def close(self):
        self.server.shutdown()
        self.server.server_close()
