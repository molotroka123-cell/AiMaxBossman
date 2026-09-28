"""A tiny, fully local, configurable HTTP server for rc19/i-collector tests.

No real site is ever contacted: every collector test (robots.txt honouring,
rate limiting, provenance, real-browser rendering) talks only to one of
these, bound to 127.0.0.1 on an OS-assigned port.
"""
from __future__ import annotations

import http.server
import socketserver
import threading
import time
from dataclasses import dataclass, field


@dataclass
class Route:
    status: int = 200
    body: bytes = b""
    content_type: str = "text/html; charset=utf-8"
    delay_s: float = 0.0


@dataclass
class FakeServer:
    routes: dict[str, Route] = field(default_factory=dict)
    received: list[dict] = field(default_factory=list)
    _httpd: socketserver.TCPServer | None = None
    _thread: threading.Thread | None = None

    def __post_init__(self):
        routes, received = self.routes, self.received

        class Handler(http.server.BaseHTTPRequestHandler):
            protocol_version = "HTTP/1.1"

            def log_message(self, *a):  # noqa: D401 -- silence stdout noise
                pass

            def do_GET(self):
                received.append({"path": self.path, "headers": dict(self.headers.items())})
                route = routes.get(self.path)
                if route is None:
                    self.send_response(404)
                    self.send_header("Content-Length", "0")
                    self.end_headers()
                    return
                if route.delay_s:
                    time.sleep(route.delay_s)
                self.send_response(route.status)
                self.send_header("Content-Type", route.content_type)
                self.send_header("Content-Length", str(len(route.body)))
                self.end_headers()
                self.wfile.write(route.body)

        self._handler = Handler
        self._httpd = socketserver.TCPServer(("127.0.0.1", 0), self._handler)
        self.port = self._httpd.server_address[1]
        self._thread = threading.Thread(target=self._httpd.serve_forever, daemon=True)
        self._thread.start()

    @property
    def base_url(self) -> str:
        return f"http://127.0.0.1:{self.port}"

    def url(self, path: str) -> str:
        return self.base_url + path

    def set_route(self, path: str, *, status: int = 200, body: bytes | str = b"",
                 content_type: str = "text/html; charset=utf-8", delay_s: float = 0.0) -> None:
        if isinstance(body, str):
            body = body.encode("utf-8")
        self.routes[path] = Route(status=status, body=body, content_type=content_type,
                                  delay_s=delay_s)

    def headers_for(self, path: str) -> dict | None:
        for row in reversed(self.received):
            if row["path"] == path:
                return row["headers"]
        return None

    def close(self) -> None:
        if self._httpd is not None:
            self._httpd.shutdown()
            self._httpd.server_close()
