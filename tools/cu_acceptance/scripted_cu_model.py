"""Deterministic OpenAI-compatible "model" for the Computer Use acceptance run.

Only the model's judgement is replaced: the Command Center engine, the approval
queue, the computer.* tool handlers, UIA/pyautogui and Windows are all real.
This makes the acceptance run reproducible and keeps the shared local LLM free.

Scenario scripts live in SCRIPT_FILE (re-read on every request, so the harness
can extend them between tasks):
    {"vars": {"name": "value"},
     "scenarios": {"<tag>": [step, ...]}}
A task selects its script with "[cu:<tag>]" in its prompt. Steps run in order,
one tool call per model turn; the step index is the number of tool calls the
assistant already made in this task's history (so it survives a backend restart:
history lives in the run checkpoint). Step forms:
    {"tool": "computer_act"|"computer_observe", "arguments": {...}}
    {"text": "final answer"}
String placeholders inside arguments:
    "$GEN"            -> int generation from the latest observation in the history
    "$WIN" / "$PID"   -> hwnd / pid of the observed window ("окно: … [hwnd=…, pid=…]") in the
                         latest observation (2026-10-10: input actions bind to exact hwnd+pid)
    "$PICK:a|b|c"     -> first candidate that occurs in the latest tool result
    "$VAR:name"       -> vars[name]
    "$IDX:name|Type"  -> index of the element <name>/<Type> in the latest observation (-1 if absent)
"""
from __future__ import annotations

import json
import os
import re
import sys
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

SCRIPT_FILE = os.environ.get("CU_SCRIPT_FILE", "")
LOG_FILE = os.environ.get("CU_SCRIPT_LOG", "")
PORT = int(os.environ.get("CU_SCRIPT_PORT", "8841"))
GEN_RX = re.compile(r"generation: (\d+)")
WIN_RX = re.compile(r"\[hwnd=(\d+), pid=(\d+)")
TAG_RX = re.compile(r"\[cu:([a-z0-9_-]+)\]")


def _load() -> dict:
    with open(SCRIPT_FILE, encoding="utf-8") as f:
        return json.load(f)


def _log(entry: dict) -> None:
    if LOG_FILE:
        entry = {"ts": time.time(), **entry}
        with open(LOG_FILE, "a", encoding="utf-8") as f:
            f.write(json.dumps(entry, ensure_ascii=False) + "\n")


def _text(m: dict) -> str:
    c = m.get("content")
    if isinstance(c, list):
        return " ".join(str(p.get("text") or "") for p in c if isinstance(p, dict))
    return str(c or "")


def _tag(messages: list[dict]) -> str:
    for m in messages:
        if m.get("role") == "user":
            hit = TAG_RX.search(_text(m))
            if hit:
                return hit.group(1)
    return ""


def _latest_generation(messages: list[dict]) -> int | None:
    for m in reversed(messages):
        if m.get("role") == "tool":
            gens = GEN_RX.findall(_text(m))
            if gens:
                return int(gens[-1])
    return None


def _latest_tool_text(messages: list[dict]) -> str:
    for m in reversed(messages):
        if m.get("role") == "tool":
            return _text(m)
    return ""


def _resolve(value, messages, variables):
    if isinstance(value, dict):
        return {k: _resolve(v, messages, variables) for k, v in value.items()}
    if isinstance(value, list):
        return [_resolve(v, messages, variables) for v in value]
    if not isinstance(value, str):
        return value
    if value == "$GEN":
        gen = _latest_generation(messages)
        return gen if gen is not None else 0
    if value in ("$WIN", "$PID"):
        for m in reversed(messages):
            found = WIN_RX.findall(_text(m))
            if found:
                return int(found[-1][0 if value == "$WIN" else 1])
        return 0
    if value.startswith("$PICK:"):
        options = value[len("$PICK:"):].split("|")
        seen = _latest_tool_text(messages)
        return next((o for o in options if o and o in seen), options[0])
    if value.startswith("$IDX:"):
        # "$IDX:<name>|<ControlType>" -> index of that element in the latest observation, -1 if absent
        name, _, ctype = value[len("$IDX:"):].partition("|")
        for line in _latest_tool_text(messages).splitlines():
            m = re.match(r"\[(\d+)\] (.*?) \| (\w+) \|", line.strip())
            if m and m.group(2) == name and (not ctype or m.group(3) == ctype):
                return int(m.group(1))
        return -1
    if value.startswith("$VAR:"):
        return variables.get(value[len("$VAR:"):], "")
    return value


class Handler(BaseHTTPRequestHandler):
    def log_message(self, *a):
        pass

    def _send(self, obj, code=200):
        body = json.dumps(obj, ensure_ascii=False).encode("utf-8")
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self):
        if self.path.rstrip("/").endswith("models"):
            self._send({"object": "list", "data": [{"id": "scripted-cu", "object": "model"}]})
        else:
            self._send({"ok": True})

    def do_POST(self):
        n = int(self.headers.get("Content-Length", 0))
        req = json.loads(self.rfile.read(n) or b"{}")
        messages = req.get("messages") or []
        offered = [(t.get("function") or {}).get("name") for t in (req.get("tools") or [])]
        data = _load()
        tag = _tag(messages)
        steps = (data.get("scenarios") or {}).get(tag) or [{"text": f"no script for tag {tag!r}"}]
        done = sum(len(m.get("tool_calls") or []) for m in messages if m.get("role") == "assistant")
        step = steps[done] if done < len(steps) else {"text": f"[{tag}] script finished"}
        if "text" in step:
            message = {"role": "assistant", "content": step["text"]}
            finish = "stop"
            chosen = {"text": step["text"]}
        else:
            args = _resolve(step.get("arguments") or {}, messages, data.get("vars") or {})
            call_id = f"call_{tag}_{done}"
            message = {"role": "assistant", "content": None, "tool_calls": [{
                "id": call_id, "type": "function",
                "function": {"name": step["tool"], "arguments": json.dumps(args, ensure_ascii=False)}}]}
            finish = "tool_calls"
            chosen = {"tool": step["tool"], "arguments": args, "call_id": call_id}
        _log({"tag": tag, "step": done, "offered": offered, "history": len(messages),
              "last_tool": _latest_tool_text(messages)[-600:], **chosen})
        self._send({"id": f"cmpl-{tag}-{done}", "object": "chat.completion",
                    "model": req.get("model", "scripted-cu"),
                    "choices": [{"index": 0, "finish_reason": finish, "message": message}],
                    "usage": {"prompt_tokens": 50 + 5 * len(messages), "completion_tokens": 10}})


def main() -> None:
    if not SCRIPT_FILE:
        sys.exit("CU_SCRIPT_FILE is required")
    server = ThreadingHTTPServer(("127.0.0.1", PORT), Handler)
    print(f"SCRIPTED_CU_PORT={server.server_address[1]}", flush=True)
    server.serve_forever()


if __name__ == "__main__":
    main()
