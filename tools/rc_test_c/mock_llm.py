"""Минимальный OpenAI-совместимый LLM для LIVE acceptance-теста (нет внешнего ключа).

Скрипт: если в промпте есть маркер RCAPPROVE-*/RCREJECT-*, отвечает tool_call
memory_fact_add с этим маркером; после получения результата инструмента — финальный
текст. Только test-harness: реальное исполнение делает движок Command Center.
"""
import json
import re
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

MARKER_RE = re.compile(r"RC(?:APPROVE|REJECT)-[A-Za-z0-9]+")


class Handler(BaseHTTPRequestHandler):
    def log_message(self, *a):
        pass

    def _json(self, code, payload):
        body = json.dumps(payload).encode()
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self):
        if self.path.endswith("/models"):
            self._json(200, {"object": "list", "data": [{"id": "mock-llm", "object": "model"}]})
        else:
            self._json(404, {"error": "not found"})

    def do_POST(self):
        if not self.path.endswith("/chat/completions"):
            self._json(404, {"error": "not found"})
            return
        length = int(self.headers.get("Content-Length") or 0)
        req = json.loads(self.rfile.read(length) or b"{}")
        messages = req.get("messages") or []
        text = " ".join(str(m.get("content") or "") for m in messages)
        marker_match = MARKER_RE.search(text)
        marker = marker_match.group(0) if marker_match else "RC-UNKNOWN"
        last = messages[-1] if messages else {}
        tool_result_seen = any(m.get("role") == "tool" for m in messages) or "fact#" in text
        if tool_result_seen:
            message = {"role": "assistant", "content": f"готово ({marker}): факт сохранён",
                       "refusal": None}
            finish = "stop"
        else:
            args = json.dumps({
                "subject": marker,
                "predicate": "rc_acceptance",
                "statement": f"RC approval live test {marker}",
                "mode": "append",
            }, ensure_ascii=False)
            message = {
                "role": "assistant",
                "content": None,
                "tool_calls": [{
                    "id": "call_rc_1",
                    "type": "function",
                    "function": {"name": "memory_fact_add", "arguments": args},
                }],
            }
            finish = "tool_calls"
        self._json(200, {
            "id": "chatcmpl-rc", "object": "chat.completion", "model": req.get("model") or "mock-llm",
            "choices": [{"index": 0, "finish_reason": finish, "message": message}],
            "usage": {"prompt_tokens": 12, "completion_tokens": 7, "total_tokens": 19},
        })


if __name__ == "__main__":
    server = ThreadingHTTPServer(("127.0.0.1", 8899), Handler)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    print("mock-llm ready on :8899", flush=True)
    import signal
    import time
    try:
        while True:
            time.sleep(3600)
    except KeyboardInterrupt:
        pass
