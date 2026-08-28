"""Детерминированный фальшивый `opencode serve` для E2E-тестов Lane F.

Почему он существует: бинаря `opencode` в этой среде НЕТ (`which opencode`
пусто), значит настоящий host-E2E тут физически не выполним. Вместо того чтобы
делать вид, что интеграция проверена, поднимается локальный HTTP-сервер,
реализующий РЕАЛЬНЫЙ контракт эндпоинтов OpenCode.

Источник контракта — `packages/sdk/openapi.json` из исходников OpenCode
(вендорная копия), а не фантазия: пути, query-параметр `directory`,
Basic-auth (`OPENCODE_SERVER_USERNAME`/`OPENCODE_SERVER_PASSWORD`), форма
`Session`, `SnapshotFileDiff`, `SessionStatus`, `Todo`, `TextPartInput`.

Сервер умеет по-настоящему править файлы в каталоге сессии — благодаря этому
тест проверяет весь цикл: красный тест → правка → дифф → зелёный тест.
"""
from __future__ import annotations

import base64
import difflib
import itertools
import json
import threading
from dataclasses import dataclass, field
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlparse

_ids = itertools.count(1)


@dataclass
class FakeSession:
    id: str
    directory: str
    title: str = ""
    agent: str = ""
    parent_id: str = ""
    state: str = "idle"                 # idle | busy | retry
    diffs: list[dict] = field(default_factory=list)
    todo: list[dict] = field(default_factory=list)
    messages: list[dict] = field(default_factory=list)
    aborted: bool = False

    def as_json(self) -> dict:
        out = {"id": self.id, "directory": self.directory, "title": self.title,
               "projectID": "prj_fake", "version": "fake",
               "time": {"created": 0, "updated": 0}}
        if self.agent:
            out["agent"] = self.agent
        if self.parent_id:
            out["parentID"] = self.parent_id
        return out

    def edit_file(self, rel: str, new_text: str) -> dict:
        """Реальная правка файла + запись SnapshotFileDiff, как у настоящего сервера."""
        path = Path(self.directory) / rel
        old = path.read_text(encoding="utf-8") if path.exists() else ""
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(new_text, encoding="utf-8")
        patch = "".join(difflib.unified_diff(
            old.splitlines(keepends=True), new_text.splitlines(keepends=True),
            fromfile=f"a/{rel}", tofile=f"b/{rel}"))
        additions = sum(1 for line in patch.splitlines()
                        if line.startswith("+") and not line.startswith("+++"))
        deletions = sum(1 for line in patch.splitlines()
                        if line.startswith("-") and not line.startswith("---"))
        entry = {"file": rel, "patch": patch, "additions": additions,
                 "deletions": deletions,
                 "status": "modified" if old else "added"}
        self.diffs = [d for d in self.diffs if d.get("file") != rel] + [entry]
        return entry


class FakeOpenCode:
    """Управляемый сервер: тест задаёт сценарий, сервер его детерминированно играет."""

    def __init__(self, *, username: str = "opencode", password: str | None = None):
        self.username = username
        self.password = password
        self.sessions: dict[str, FakeSession] = {}
        self.requests: list[tuple[str, str, dict]] = []   # (method, path, query)
        # сценарии: (подстрока запроса, обработчик(session, text) -> текст ответа)
        self.behaviours: list[tuple[str, object]] = []
        self._server: ThreadingHTTPServer | None = None
        self._thread: threading.Thread | None = None

    # ------------------------------------------------------------ сценарий

    def on_prompt(self, contains: str, handler) -> None:
        """handler(session, text) -> str. Первое совпадение выигрывает."""
        self.behaviours.append((contains, handler))

    def edits_file(self, contains: str, rel: str, new_text: str,
                   reply: str = "готово") -> None:
        """Самый частый сценарий: агент правит файл и отчитывается."""
        def handler(session: FakeSession, _text: str) -> str:
            session.edit_file(rel, new_text)
            session.todo = [{"content": f"починить {rel}", "status": "completed",
                             "priority": "high"}]
            return reply
        self.on_prompt(contains, handler)

    def _run_prompt(self, session: FakeSession, text: str) -> str:
        for contains, handler in self.behaviours:
            if contains in text:
                return str(handler(session, text) or "")
        return "нечего делать"

    # ------------------------------------------------------------- запуск

    @property
    def url(self) -> str:
        assert self._server is not None, "сервер не запущен"
        host, port = self._server.server_address[:2]
        return f"http://127.0.0.1:{port}"

    def start(self) -> str:
        handler = _make_handler(self)
        self._server = ThreadingHTTPServer(("127.0.0.1", 0), handler)
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
            self._thread = None

    def __enter__(self) -> "FakeOpenCode":
        self.start()
        return self

    def __exit__(self, *_exc) -> None:
        self.stop()


def _make_handler(fake: FakeOpenCode):

    class Handler(BaseHTTPRequestHandler):
        protocol_version = "HTTP/1.1"

        def log_message(self, *_a):        # тихо: тестовый вывод не засоряем
            pass

        # ---------------------------------------------------------- служебное

        def _authorized(self) -> bool:
            if not fake.password:
                return True
            header = self.headers.get("Authorization") or ""
            if not header.startswith("Basic "):
                return False
            try:
                raw = base64.b64decode(header[6:]).decode()
            except Exception:
                return False
            user, _, pwd = raw.partition(":")
            return user == fake.username and pwd == fake.password

        def _send(self, code: int, payload) -> None:
            body = json.dumps(payload).encode()
            self.send_response(code)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def _body(self) -> dict:
            length = int(self.headers.get("Content-Length") or 0)
            if not length:
                return {}
            try:
                return json.loads(self.rfile.read(length) or b"{}")
            except Exception:
                return {}

        def _session(self, sid: str) -> FakeSession | None:
            return fake.sessions.get(sid)

        # ------------------------------------------------------------- GET

        def do_GET(self):                                    # noqa: N802
            parsed = urlparse(self.path)
            path, query = parsed.path, parse_qs(parsed.query)
            fake.requests.append(("GET", path, query))
            if not self._authorized():
                return self._send(401, {"error": "unauthorized"})
            directory = (query.get("directory") or [""])[0]
            parts = [p for p in path.split("/") if p]

            if path == "/api/health":
                return self._send(200, {"healthy": True})
            if path == "/project":
                return self._send(200, [{"id": "prj_fake", "worktree": directory or ".",
                                         "time": {"created": 0}, "sandboxes": []}])
            if path == "/config":
                return self._send(200, {})
            if path == "/session":
                return self._send(200, [s.as_json() for s in fake.sessions.values()
                                        if not directory or s.directory == directory])
            if path == "/session/status":
                return self._send(200, {s.id: {"type": s.state}
                                        for s in fake.sessions.values()
                                        if not directory or s.directory == directory})
            if len(parts) >= 2 and parts[0] == "session":
                session = self._session(parts[1])
                if session is None:
                    return self._send(404, {"error": "session not found"})
                tail = parts[2] if len(parts) > 2 else ""
                if not tail:
                    return self._send(200, session.as_json())
                if tail == "diff":
                    return self._send(200, session.diffs)
                if tail == "todo":
                    return self._send(200, session.todo)
                if tail == "children":
                    return self._send(200, [s.as_json() for s in fake.sessions.values()
                                            if s.parent_id == session.id])
                if tail == "message":
                    return self._send(200, session.messages)
            return self._send(404, {"error": f"no route {path}"})

        # ------------------------------------------------------------ POST

        def do_POST(self):                                   # noqa: N802
            parsed = urlparse(self.path)
            path, query = parsed.path, parse_qs(parsed.query)
            fake.requests.append(("POST", path, query))
            if not self._authorized():
                return self._send(401, {"error": "unauthorized"})
            body = self._body()
            directory = (query.get("directory") or [""])[0]
            parts = [p for p in path.split("/") if p]

            if path == "/session":
                sid = f"ses_{next(_ids)}"
                session = FakeSession(id=sid, directory=directory,
                                      title=str(body.get("title") or ""),
                                      agent=str(body.get("agent") or ""),
                                      parent_id=str(body.get("parentID") or ""))
                fake.sessions[sid] = session
                return self._send(200, session.as_json())

            if len(parts) >= 3 and parts[0] == "session":
                session = self._session(parts[1])
                if session is None:
                    return self._send(404, {"error": "session not found"})
                tail = parts[2]

                if tail == "message":
                    text = _text_of(body)
                    session.state = "busy"
                    reply = fake._run_prompt(session, text)
                    session.state = "idle"
                    mid = f"msg_{next(_ids)}"
                    out = {"info": {"id": mid, "role": "assistant",
                                    "sessionID": session.id},
                           "parts": [{"id": f"prt_{next(_ids)}", "type": "text",
                                      "text": reply}]}
                    session.messages.append(out)
                    return self._send(200, out)

                if tail == "prompt_async":
                    # Длинный прогон: задание принято, сессия занята и сама не
                    # завершится — это и проверяет тест на abort.
                    session.state = "busy"
                    session.messages.append({"info": {"id": f"msg_{next(_ids)}",
                                                      "role": "user"},
                                             "parts": body.get("parts") or []})
                    return self._send(200, {})

                if tail == "abort":
                    was_busy = session.state == "busy"
                    session.state = "idle"
                    session.aborted = True
                    return self._send(200, was_busy or True)

                if tail == "fork":
                    sid = f"ses_{next(_ids)}"
                    child = FakeSession(id=sid, directory=session.directory,
                                        title=f"fork of {session.title}",
                                        parent_id=session.id,
                                        diffs=list(session.diffs))
                    fake.sessions[sid] = child
                    return self._send(200, child.as_json())

            return self._send(404, {"error": f"no route {path}"})

    return Handler


def _text_of(body: dict) -> str:
    parts = body.get("parts")
    if not isinstance(parts, list):
        return ""
    return "\n".join(str(p.get("text") or "") for p in parts
                     if isinstance(p, dict) and p.get("type") == "text")
