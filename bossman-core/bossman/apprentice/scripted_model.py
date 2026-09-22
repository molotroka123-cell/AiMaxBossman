"""DETERMINISTIC TEST MODEL — an OpenAI-compatible server that replays a script.

It exists to prove the Coding path (UI/API → sandbox → sidecar → tools →
independent diff → verification) on real files and real processes WITHOUT
pretending a model solved anything. Every served model id contains
``DETERMINISTIC-TEST-MODEL``; the sidecar copies that flag into its response
and Bossman shows it on the task. A result produced here is evidence that the
plumbing works, never evidence of model quality.

Script file (JSON):
  {"name": "fix-add", "turns": [
      {"tool": "read_file", "args": {"path": "calc.py"}},
      {"tool": "edit_file", "args": {"path": "calc.py", "old": "a - b", "new": "a + b"}},
      {"tool": "run_tests", "args": {"paths": ["test_calc.py"], "runner": "unittest"}},
      {"tool": "finish", "args": {"summary": "fixed add"}}]}

Turn N answers the request that already holds N assistant messages, so the
replay is stateless and a restarted server continues correctly. The handshake
probe (system prompt starting "Handshake probe") always gets a list_dir call.

  python -m bossman.apprentice.scripted_model --script s.json --port 0 --port-file p.txt
"""
from __future__ import annotations

import argparse
import json
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

MARKER = "DETERMINISTIC-TEST-MODEL"


def model_id(script: dict) -> str:
    return f"{MARKER}-{script.get('name') or 'script'}"


def reply_for(script: dict, messages: list[dict]) -> dict:
    system = next((m.get("content") or "" for m in messages if m.get("role") == "system"), "")
    if str(system).startswith("Handshake probe"):
        turn = {"tool": "list_dir", "args": {"path": "."}}
        index = 0
    else:
        index = sum(1 for m in messages if m.get("role") == "assistant")
        turns = script.get("turns") or []
        turn = turns[index] if index < len(turns) else {"content": "(script exhausted)"}
    if "tool" in turn:
        return {"role": "assistant", "content": "",
                "tool_calls": [{"id": f"call_{index}", "type": "function",
                                "function": {"name": turn["tool"],
                                             "arguments": json.dumps(turn.get("args") or {})}}]}
    return {"role": "assistant", "content": str(turn.get("content") or "")}


def make_server(script: dict, host: str = "127.0.0.1", port: int = 0) -> ThreadingHTTPServer:
    served = model_id(script)

    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *_a):  # quiet
            return

        def _send(self, code: int, body: dict) -> None:
            data = json.dumps(body).encode("utf-8")
            self.send_response(code)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(data)))
            self.end_headers()
            self.wfile.write(data)

        def do_GET(self):  # noqa: N802
            if self.path.rstrip("/") in ("/v1/models", "/models"):
                self._send(200, {"object": "list", "data": [{"id": served, "object": "model",
                                                            "owned_by": "bossman-deterministic-test"}]})
            else:
                self._send(404, {"error": "not found"})

        def do_POST(self):  # noqa: N802
            if self.path.rstrip("/") not in ("/v1/chat/completions", "/chat/completions"):
                self._send(404, {"error": "not found"})
                return
            length = int(self.headers.get("Content-Length") or 0)
            try:
                req = json.loads(self.rfile.read(length) or b"{}")
            except ValueError:
                self._send(400, {"error": "bad json"})
                return
            msg = reply_for(script, req.get("messages") or [])
            self._send(200, {"id": "scripted", "object": "chat.completion", "model": served,
                             "choices": [{"index": 0, "message": msg,
                                          "finish_reason": "tool_calls" if msg.get("tool_calls") else "stop"}]})

    return ThreadingHTTPServer((host, port), Handler)


def serve_in_thread(script: dict) -> tuple[ThreadingHTTPServer, str]:
    server = make_server(script)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    return server, f"http://127.0.0.1:{server.server_address[1]}"


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="bossman-scripted-model")
    ap.add_argument("--script", required=True)
    ap.add_argument("--port", type=int, default=0)
    ap.add_argument("--port-file", default="")
    args = ap.parse_args(argv)
    script = json.loads(Path(args.script).read_text(encoding="utf-8"))
    server = make_server(script, port=args.port)
    if args.port_file:
        Path(args.port_file).write_text(str(server.server_address[1]), encoding="utf-8")
    print(f"{model_id(script)} on http://127.0.0.1:{server.server_address[1]}", flush=True)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
