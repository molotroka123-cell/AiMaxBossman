"""Streaming timing measures a token event, not the HTTP response headers."""
from __future__ import annotations

import importlib.util
import json
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
import threading
import time

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location("model_bakeoff", ROOT / "tools/model_bakeoff.py")
bake = importlib.util.module_from_spec(spec)
spec.loader.exec_module(bake)


def test_ttft_from_first_real_streamed_token_and_provider_prefill():
    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *args):
            pass

        def do_POST(self):
            request = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
            assert request["stream"] is True
            self.send_response(200)
            self.send_header("Content-Type", "text/event-stream")
            self.end_headers()
            self.wfile.write(b'data: {"choices":[{"delta":{}}]}\n\n')
            self.wfile.flush()
            time.sleep(0.025)
            self.wfile.write(b'data: {"choices":[{"delta":{"content":"ready"}}],'
                             b'"timings":{"prompt_per_second":75.5}}\n\n')
            self.wfile.write(b'data: [DONE]\n\n')
            self.wfile.flush()

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        result = bake.latency_probe(f"http://127.0.0.1:{server.server_port}/v1", "fixture", timeout=5)
        assert result["status"] == "MEASURED"
        assert result["ttft_ms"] >= 20
        assert result["prefill_tps_reported"] == 75.5
        assert result["memory"]["peak_process_rss_bytes"] is None
    finally:
        server.shutdown()
        server.server_close()


def test_non_streaming_server_does_not_invent_ttft():
    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *args):
            pass

        def do_POST(self):
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.end_headers()
            self.wfile.write(b'{"choices":[{"message":{"content":"ready"}}]}')

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        result = bake.latency_probe(f"http://127.0.0.1:{server.server_port}/v1", "fixture", timeout=5)
        assert result["status"] == "UNMEASURED"
        assert result["ttft_ms"] is None
    finally:
        server.shutdown()
        server.server_close()
