"""Фальшивый `opencode serve` v2 (R12, 2026-09-22) — и «только SPA».

Форма ответов снята с ЖИВОГО `opencode serve` v2.0.12 (`GET /openapi.json`,
ручные запросы на свободном loopback-порту):
  * всё API — под `/api/…`, ответы в конверте `{"data": …}`;
  * корневые v1-пути (`/session`, `/config`, `/project`, `/global/health`…)
    отдают HTML веб-приложения с HTTP 200 — ровно то, на чём прежний health
    рапортовал «online»;
  * `/api/health` — 404; версия — `GET /api/info` → {version, pid, urls, paths};
  * задание — `POST /api/session/{id}/prompt {text}` (inbox, асинхронно),
    конец прогона — `POST /api/experimental/session/{id}/wait` (204),
    занятые — `GET /api/session/active` → {data: {ses…: {type: running}}},
    остановка — `POST /api/session/{id}/interrupt` → {interrupted: bool},
    сообщения — `{type: user|assistant, content: [{type: text, text}]}`.

`spa_only=True` — сервер, который на ЛЮБОЙ путь отвечает HTML 200: так
выглядит «что-то живое, но не тот API».
"""
from __future__ import annotations

import base64
import itertools
import json
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, urlparse

SPA = (b"<!doctype html>\n<html lang=\"en\"><head><meta charset=\"utf-8\" />"
       b"<title>OpenCode</title></head><body><div id=\"root\"></div></body></html>")

_ids = itertools.count(1)


def _now_ms() -> int:
    return int(time.time() * 1000)


class FakeOpenCodeV2:
    def __init__(self, *, version: str = "2.0.12", password: str | None = None,
                 spa_only: bool = False):
        self.version = version
        self.password = password
        self.spa_only = spa_only
        self.sessions: dict[str, dict] = {}
        self.messages: dict[str, list[dict]] = {}
        self.diffs: dict[str, list[dict]] = {}
        self.active: set[str] = set()
        self.requests: list[tuple[str, str, dict, dict]] = []   # method, path, query, body
        self.reply = "готово"
        self.hold = False                      # prompt оставляет сессию занятой (для abort)
        self._server: ThreadingHTTPServer | None = None
        self._thread: threading.Thread | None = None

    @property
    def url(self) -> str:
        assert self._server is not None
        return f"http://127.0.0.1:{self._server.server_address[1]}"

    def start(self) -> str:
        self._server = ThreadingHTTPServer(("127.0.0.1", 0), _handler(self))
        self._server.daemon_threads = True
        self._thread = threading.Thread(target=self._server.serve_forever,
                                        kwargs={"poll_interval": 0.02}, daemon=True)
        self._thread.start()
        return self.url

    def stop(self) -> None:
        if self._server is not None:
            self._server.shutdown()
            self._server.server_close()
            self._server = None
        if self._thread is not None:
            self._thread.join(timeout=5)

    def __enter__(self):
        self.start()
        return self

    def __exit__(self, *_):
        self.stop()


def _handler(fake: FakeOpenCodeV2):

    class H(BaseHTTPRequestHandler):
        protocol_version = "HTTP/1.1"

        def log_message(self, *_a):
            pass

        def _json(self, code: int, payload) -> None:
            body = json.dumps(payload).encode()
            self.send_response(code)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def _html(self) -> None:
            self.send_response(200)
            self.send_header("Content-Type", "text/html")
            self.send_header("Content-Length", str(len(SPA)))
            self.end_headers()
            self.wfile.write(SPA)

        def _empty(self, code: int = 204) -> None:
            self.send_response(code)
            self.send_header("Content-Length", "0")
            self.end_headers()

        def _body(self) -> dict:
            n = int(self.headers.get("Content-Length") or 0)
            if not n:
                return {}
            try:
                return json.loads(self.rfile.read(n) or b"{}")
            except Exception:
                return {}

        def _auth_ok(self) -> bool:
            if not fake.password:
                return True
            head = self.headers.get("Authorization") or ""
            try:
                user, _, pwd = base64.b64decode(head[6:]).decode().partition(":")
            except Exception:
                return False
            return head.startswith("Basic ") and pwd == fake.password

        def _route(self, method: str):
            parsed = urlparse(self.path)
            path, query = parsed.path, parse_qs(parsed.query)
            body = self._body() if method == "POST" else {}
            fake.requests.append((method, path, query, body))
            if not self._auth_ok():
                return self._json(401, {"_tag": "UnauthorizedError", "message": "unauthorized"})
            if fake.spa_only or not path.startswith("/api/"):
                return self._html()                  # v1-пути у v2 — это SPA
            parts = [p for p in path.split("/") if p][1:]   # без "api"
            q = {k: v[0] for k, v in query.items()}

            if method == "GET" and parts == ["info"]:
                return self._json(200, {"version": fake.version, "pid": 1,
                                        "urls": [fake.url], "paths": {"tmp": "/tmp"}})
            if parts == ["session"] and method == "GET":
                items = list(fake.sessions.values())
                if q.get("directory"):
                    items = [s for s in items if s.get("_dir") == q["directory"]]
                if q.get("parentID"):
                    items = [s for s in items if s.get("parentID") == q["parentID"]]
                if q.get("limit"):
                    items = items[: int(q["limit"])]
                return self._json(200, {"data": [_pub(s) for s in items],
                                        "cursor": {"previous": None, "next": None}})
            if parts == ["session"] and method == "POST":
                sid = f"ses_v2fake{next(_ids)}"
                loc = body.get("location") or {}
                s = {"id": sid, "projectID": "prj", "agent": body.get("agent") or "build",
                     "title": body.get("title") or "", "time": {"created": _now_ms()},
                     "_dir": loc.get("directory") or ""}
                fake.sessions[sid] = s
                fake.messages[sid] = []
                fake.diffs[sid] = []
                return self._json(200, {"data": _pub(s)})
            if parts == ["session", "active"] and method == "GET":
                return self._json(200, {"data": {sid: {"type": "running"}
                                                 for sid in fake.active}})
            if parts[:1] == ["experimental"] and parts[1:2] == ["session"] and parts[-1] == "wait":
                sid = parts[2]
                deadline = time.time() + 5
                while sid in fake.active and time.time() < deadline:
                    time.sleep(0.02)
                return self._empty(204)
            if len(parts) >= 2 and parts[0] == "session":
                sid = parts[1]
                s = fake.sessions.get(sid)
                if s is None:
                    return self._json(404, {"_tag": "SessionNotFoundError", "sessionID": sid,
                                            "message": "not found"})
                tail = parts[2] if len(parts) > 2 else ""
                if not tail and method == "GET":
                    return self._json(200, {"data": _pub(s)})
                if tail == "agent" and method == "POST":
                    s["agent"] = body.get("agent")
                    return self._empty(204)
                if tail == "prompt" and method == "POST":
                    if not isinstance(body.get("text"), str):
                        return self._json(400, {"_tag": "InvalidRequestError",
                                                "message": "text required"})
                    mid = f"msg_{next(_ids)}"
                    created = _now_ms()
                    fake.messages[sid].append({"id": mid, "type": "user", "text": body["text"],
                                               "time": {"created": created}})
                    if fake.hold:
                        fake.active.add(sid)
                    else:
                        fake.messages[sid].append({
                            "id": f"msg_{next(_ids)}", "type": "assistant", "agent": s["agent"],
                            "time": {"created": created + 1, "completed": created + 2},
                            "finish": "stop",
                            "content": [{"type": "reasoning", "text": "думаю"},
                                        {"type": "text", "text": fake.reply}]})
                        fake.diffs[sid] = [{"file": "calc.py", "patch": "@@ -1 +1 @@\n-a\n+b\n",
                                            "additions": 1, "deletions": 1,
                                            "status": "modified"}]
                    return self._json(200, {"data": {"id": mid, "sessionID": sid, "type": "user",
                                                     "time": {"created": created},
                                                     "payload": {"text": body["text"]}}})
                if tail == "message" and method == "GET":
                    items = list(fake.messages[sid])
                    if q.get("type"):
                        items = [m for m in items if m["type"] == q["type"]]
                    if q.get("order") == "desc":
                        items.reverse()
                    if q.get("limit"):
                        items = items[: int(q["limit"])]
                    return self._json(200, {"data": items,
                                            "cursor": {"previous": None, "next": None}})
                if tail == "interrupt" and method == "POST":
                    was = sid in fake.active
                    fake.active.discard(sid)
                    return self._json(200, {"interrupted": was})
                if tail == "fork" and method == "POST":
                    cid = f"ses_v2fake{next(_ids)}"
                    child = {**s, "id": cid, "parentID": sid,
                             "fork": {"sessionID": sid,
                                      "boundary": {"type": "before",
                                                   "messageID": body.get("before") or "msg_0"}}}
                    fake.sessions[cid] = child
                    fake.messages[cid] = []
                    fake.diffs[cid] = list(fake.diffs[sid])
                    return self._json(200, {"data": _pub(child)})
                if tail == "diff" and method == "GET":
                    return self._json(200, {"data": fake.diffs[sid]})
            return self._json(404, {"_tag": "NotFound", "message": f"no route {path}"})

        def do_GET(self):   # noqa: N802
            self._route("GET")

        def do_POST(self):  # noqa: N802
            self._route("POST")

    return H


def _pub(s: dict) -> dict:
    return {k: v for k, v in s.items() if not k.startswith("_")}
