"""Transparent loopback proxy in front of the owner's Ollama that pins sampling for the experiment.

    python sampling_proxy.py <listen_port> <upstream e.g. http://127.0.0.1:11434> <log.jsonl>

Why: Bossman's engine sends no temperature/seed (agent rows have no sampling fields), and the Ollama model's own
Modelfile default is temperature 0.7. For a preregistered A/B the sampling must be fixed, so every
POST /v1/chat/completions gets `temperature: 0` and `seed: 42` (only if the caller did not set them) and
`reasoning_effort: "none"` (exactly what Bossman itself adds when it talks to Ollama on :11434 directly).
Nothing else is changed: messages, tools, stream flag and the response bytes pass through unmodified.

Read-only towards Ollama (it only forwards requests Bossman makes). Loopback only. Logs per request: time, path,
model, injected keys, message count, sha256 of the request messages — never the message text.
"""
from __future__ import annotations

import hashlib
import http.client
import json
import sys
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import urlparse

PORT = int(sys.argv[1])
UPSTREAM = urlparse(sys.argv[2])
LOG = sys.argv[3]
PINNED = {"temperature": 0, "seed": 42}


def _log(row: dict) -> None:
    with open(LOG, "a", encoding="utf-8") as fh:
        fh.write(json.dumps(row, ensure_ascii=False) + "\n")


class Handler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.0"          # close-delimited bodies: streaming passes through unchanged

    def log_message(self, *_a):            # quiet
        pass

    def _forward(self, method: str) -> None:
        body = b""
        length = int(self.headers.get("Content-Length") or 0)
        if length:
            body = self.rfile.read(length)
        injected: list[str] = []
        if method == "POST" and self.path.rstrip("/").endswith("/chat/completions") and body:
            try:
                payload = json.loads(body)
                for k, v in PINNED.items():
                    if k not in payload:
                        payload[k] = v
                        injected.append(k)
                if "reasoning_effort" not in payload:
                    payload["reasoning_effort"] = "none"
                    injected.append("reasoning_effort")
                # Deviation 2 (01.11, integrator request): the server default keep_alive is -1 (forever), which
                # starved a concurrent live run on the same Ollama. Not a sampling field; output is unaffected.
                if "keep_alive" not in payload:
                    payload["keep_alive"] = "5m"
                    injected.append("keep_alive")
                msgs = payload.get("messages") or []
                _log({"t": time.time(), "path": self.path, "model": payload.get("model"),
                      "stream": bool(payload.get("stream")), "injected": injected,
                      "temperature": payload.get("temperature"), "seed": payload.get("seed"),
                      "n_messages": len(msgs),
                      "messages_sha256": hashlib.sha256(json.dumps(msgs, sort_keys=True, ensure_ascii=False)
                                                        .encode("utf-8")).hexdigest()})
                body = json.dumps(payload).encode("utf-8")
            except ValueError:
                pass
        conn = http.client.HTTPConnection(UPSTREAM.hostname, UPSTREAM.port, timeout=900)
        headers = {k: v for k, v in self.headers.items()
                   if k.lower() not in ("host", "content-length", "connection", "accept-encoding")}
        if body:
            headers["Content-Length"] = str(len(body))
        try:
            conn.request(method, self.path, body=body or None, headers=headers)
            resp = conn.getresponse()
        except OSError as exc:
            self.send_error(502, f"upstream: {type(exc).__name__}")
            return
        self.send_response(resp.status)
        for k, v in resp.getheaders():
            if k.lower() not in ("transfer-encoding", "connection", "content-length"):
                self.send_header(k, v)
        self.send_header("Connection", "close")
        self.end_headers()
        while True:
            chunk = resp.read1(65536) if hasattr(resp, "read1") else resp.read(65536)
            if not chunk:
                break
            self.wfile.write(chunk)
            self.wfile.flush()
        conn.close()

    def do_GET(self):  # noqa: N802
        self._forward("GET")

    def do_POST(self):  # noqa: N802
        self._forward("POST")


if __name__ == "__main__":
    ThreadingHTTPServer(("127.0.0.1", PORT), Handler).serve_forever()
