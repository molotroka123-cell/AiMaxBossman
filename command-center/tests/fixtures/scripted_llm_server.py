"""Детерминированная «модель» как настоящий OpenAI-совместимый HTTP-сервер.

Зачем не питоновский фейк-адаптер: так проверяется ВЕСЬ путь целиком —
HTTP-запрос с tools → разбор ответа в bcc/providers.py → tool_calls в движке →
исполнение инструмента → tool-сообщение обратно по HTTP. Подменена только
«сообразительность»: следующий шаг выбирается детерминированным правилом,
а не рассуждением. Всё остальное — боевой код.

Сценарий задаётся списком шагов в env SCRIPT_FILE (JSON):
  {"tool": "имя_для_модели", "arguments": {...}, "when": "подстрока"}  — вызвать инструмент
  {"text": "ответ"}                                                     — финальный ответ
Шаг с "when" срабатывает, только если подстрока есть в последнем сообщении
(так модель «реагирует» на результат инструмента, например на фидбек ревьюера).
"""
from __future__ import annotations

import json
import os
import re
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

SCRIPT_FILE = os.environ.get("SCRIPT_FILE", "")
LOG_FILE = os.environ.get("SCRIPT_LOG", "")
PORT = int(os.environ.get("SCRIPT_PORT", "0"))


def _script() -> list[dict]:
    with open(SCRIPT_FILE, encoding="utf-8") as f:
        return json.load(f)


def _log(entry: dict) -> None:
    if not LOG_FILE:
        return
    with open(LOG_FILE, "a", encoding="utf-8") as f:
        f.write(json.dumps(entry, ensure_ascii=False) + "\n")


def _last_content(messages: list[dict]) -> str:
    for m in reversed(messages):
        if m.get("content"):
            return str(m["content"])
    return ""


def _done_tools(messages: list[dict]) -> list[str]:
    """Какие инструменты уже отработали в этой истории (по tool-сообщениям)."""
    out = []
    for m in messages:
        if m.get("role") == "tool":
            out.append(str(m.get("name") or ""))
    return out


def _pick(script: list[dict], messages: list[dict]) -> dict:
    """Следующий невыполненный шаг сценария.

    Шаги с "when" берутся, только если подстрока встретилась в истории —
    это позволяет модели «отреагировать» на фидбек ревьюера или на ошибку.
    """
    history = json.dumps(messages, ensure_ascii=False)
    used: dict[str, int] = {}
    for m in messages:
        if m.get("role") == "assistant":
            for call in m.get("tool_calls") or []:
                name = (call.get("function") or {}).get("name", "")
                used[name] = used.get(name, 0) + 1

    seen: dict[str, int] = {}
    for step in script:
        if step.get("when") and step["when"] not in history:
            continue
        if "text" in step:
            return step
        name = step["tool"]
        seen[name] = seen.get(name, 0) + 1
        if used.get(name, 0) < seen[name]:
            return step
    return {"text": "все шаги сценария выполнены"}


class Handler(BaseHTTPRequestHandler):
    def log_message(self, *a):  # тишина в выводе тестов
        pass

    def _send(self, obj, code=200):
        body = json.dumps(obj, ensure_ascii=False).encode()
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self):
        if self.path.rstrip("/").endswith("models"):
            self._send({"object": "list", "data": [{"id": "scripted-coder", "object": "model"}]})
        else:
            self._send({"ok": True})

    def do_POST(self):
        n = int(self.headers.get("Content-Length", 0))
        req = json.loads(self.rfile.read(n) or b"{}")
        messages = req.get("messages") or []
        tools = [t.get("function", {}).get("name") for t in (req.get("tools") or [])]
        step = _pick(_script(), messages)
        _log({"tools_offered": tools, "history_len": len(messages),
              "chose": step.get("tool") or "text", "done": _done_tools(messages)})

        if "text" in step:
            message = {"role": "assistant", "content": step["text"]}
            finish = "stop"
        else:
            # Настоящий OpenAI-формат tool_calls: именно его разбирает адаптер
            call_id = "call_" + re.sub(r"\W+", "", step["tool"]) + str(len(messages))
            message = {"role": "assistant", "content": step.get("say") or None,
                       "tool_calls": [{
                           "id": call_id, "type": "function",
                           "function": {"name": step["tool"],
                                        "arguments": json.dumps(step.get("arguments") or {},
                                                                ensure_ascii=False)}}]}
            finish = "tool_calls"

        self._send({
            "id": "cmpl-scripted", "object": "chat.completion",
            "model": req.get("model", "scripted-coder"),
            "choices": [{"index": 0, "finish_reason": finish, "message": message}],
            "usage": {"prompt_tokens": 40 + len(messages) * 5, "completion_tokens": 12},
        })


def main() -> None:
    server = ThreadingHTTPServer(("127.0.0.1", PORT), Handler)
    print(f"SCRIPTED_LLM_PORT={server.server_address[1]}", flush=True)
    server.serve_forever()


if __name__ == "__main__":
    main()
