"""Transport to the ONE configured Bossman backend (Command Center API).

The terminal owns no state of its own: it finds the running Command Center of
the owner's data root, authenticates with that data root's token (read from
the token file — never from argv, never printed) and talks to the same API the
web UI and Telegram use.

Discovery order: --url / BOSSMAN_URL, then the port in <data>/desktop.lock, then
BCC_PORT (default 8800). A port that answers but is not Command Center is
refused (identity check), never "reused".
"""
from __future__ import annotations

import json
import os
import queue
import re
import threading
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Iterator

import httpx

from ..build_identity import DESKTOP_APP_IDENTITY

APP_IDENTITY = DESKTOP_APP_IDENTITY
HEADER = "X-BCC-Token"
CSRF_HEADER = "X-BCC-CSRF"
_APPROVAL_DECISION = re.compile(r"^/api/approvals/\d+/?$")


class BossmanError(RuntimeError):
    """An API/transport failure, already in words the owner can act on."""

    def __init__(self, message: str, *, status: int | None = None, code: str | None = None,
                 hint: str | None = None, kind: str = "api"):
        super().__init__(message)
        self.message = message
        self.status = status
        self.code = code
        self.hint = hint
        self.kind = kind            # api | disconnected | auth | not_found | not_supported | conflict


def default_data_dir() -> Path:
    from ..config import Settings
    return Path(Settings().data_dir)


def default_port() -> int:
    try:
        return int(os.environ.get("BCC_PORT") or 8800)
    except ValueError:
        return 8800


def _lock_port(data_dir: Path) -> int | None:
    try:
        data = json.loads((data_dir / "desktop.lock").read_text(encoding="utf-8"))
        port = int(data.get("port") or 0)
        return port or None
    except (OSError, ValueError, TypeError, AttributeError):
        return None


def candidate_urls(url: str | None, data_dir: Path) -> list[str]:
    if url:
        return [url.rstrip("/")]
    env = os.environ.get("BOSSMAN_URL", "").strip()
    if env:
        return [env.rstrip("/")]
    out: list[str] = []
    host = os.environ.get("BCC_HOST", "127.0.0.1").strip() or "127.0.0.1"
    for port in (_lock_port(data_dir), default_port()):
        if port:
            candidate = f"http://{host}:{port}"
            if candidate not in out:
                out.append(candidate)
    return out


def identify(url: str, timeout: float = 2.0) -> dict | None:
    """Who listens at `url`: Command Center identity or None. Local address,
    so the proxy from the environment is bypassed (trust_env=False)."""
    try:
        with httpx.Client(trust_env=False, timeout=timeout) as c:
            r = c.get(url.rstrip("/") + "/api/identity")
        if r.status_code != 200:
            return None
        data = r.json()
    except (httpx.HTTPError, ValueError):
        return None
    return data if isinstance(data, dict) and data.get("app") == APP_IDENTITY else None


def read_token(data_dir: Path) -> str | None:
    try:
        value = (Path(data_dir) / "token").read_text(encoding="utf-8").strip()
    except OSError:
        return None
    return value or None


@dataclass
class Target:
    url: str
    data_dir: Path
    identity: dict = field(default_factory=dict)


def discover(url: str | None = None, data_dir: str | Path | None = None) -> Target:
    base = Path(data_dir).expanduser() if data_dir else default_data_dir()
    tried = candidate_urls(url, base)
    for candidate in tried:
        ident = identify(candidate)
        if ident is not None:
            return Target(url=candidate, data_dir=base, identity=ident)
    raise BossmanError("Bossman не отвечает: " + (", ".join(tried) or "адрес не задан"),
                       kind="disconnected",
                       hint="запустите Bossman (Start-Bossman.cmd или `bossman start`) "
                            "или укажите --url")


class Client:
    """Thin synchronous API client. The token lives only in a request header."""

    def __init__(self, target: Target, *, timeout: float = 30.0):
        self.target = target
        token = read_token(target.data_dir)
        if not token:
            raise BossmanError(f"нет файла токена в {target.data_dir}", kind="auth",
                               hint="это не тот каталог данных, что у запущенного Bossman? "
                                    "укажите --data-dir или BCC_DATA_DIR")
        self._token = token
        self._session_login = False
        self.http = httpx.Client(base_url=target.url, trust_env=False, timeout=timeout,
                                 follow_redirects=False, headers={HEADER: token})

    # -- auth ---------------------------------------------------------------

    def _login(self) -> None:
        """Legacy header auth switched off (BCC_LEGACY_TOKEN=0): exchange the
        token for a server session like the web UI does."""
        self.http.headers.pop(HEADER, None)
        r = self.http.post("/api/login", json={"token": self._token, "label": "terminal"})
        if r.status_code != 200:
            raise BossmanError("вход не выполнен: токен не подошёл этому Bossman", status=r.status_code,
                               kind="auth", hint="запущен другой экземпляр? проверьте --data-dir")
        self.http.headers[CSRF_HEADER] = r.json().get("csrf", "")
        self._session_login = True

    def close(self) -> None:
        try:
            if self._session_login:
                self.http.post("/api/logout")
        except httpx.HTTPError:
            pass
        self.http.close()

    def __enter__(self) -> "Client":
        return self

    def __exit__(self, *_exc) -> None:
        self.close()

    # -- requests -----------------------------------------------------------

    def request(self, method: str, path: str, **kw: Any) -> Any:
        for attempt in range(2):
            try:
                r = self.http.request(method, path, **kw)
            except httpx.TimeoutException as exc:
                raise BossmanError(f"Bossman не ответил вовремя ({method} {path})", kind="disconnected",
                                   hint="backend занят или завис; `bossman status`") from exc
            except httpx.HTTPError as exc:
                raise BossmanError(f"связь с Bossman потеряна ({type(exc).__name__})",
                                   kind="disconnected", hint="`bossman status`") from exc
            if r.status_code == 401 and attempt == 0 and not self._session_login:
                self._login()
                continue
            return self._decode(r, method, path)
        raise BossmanError("нужна аутентификация", status=401, kind="auth")

    @staticmethod
    def _decode(r: httpx.Response, method: str, path: str) -> Any:
        if r.status_code < 400:
            if not r.content:
                return None
            try:
                return r.json()
            except ValueError:
                return r.text
        message, hint, code = f"{method} {path}: HTTP {r.status_code}", None, None
        try:
            body = r.json()
            err = body.get("error") if isinstance(body, dict) else None
            detail = body.get("detail") if isinstance(body, dict) else None
            if isinstance(err, dict):
                message = str(err.get("message") or message)
                hint, code = err.get("hint"), err.get("code")
            elif isinstance(detail, dict):
                message = str(detail.get("message") or message)
                hint, code = detail.get("hint"), detail.get("code")
            elif isinstance(detail, str):
                message = detail
        except ValueError:
            pass
        kind = {401: "auth", 404: "not_found", 409: "conflict"}.get(r.status_code, "api")
        if r.status_code in (404, 405) and message in ("Not Found", "Method Not Allowed",
                                                      f"{method} {path}: HTTP {r.status_code}"):
            # Маршрута нет вовсе (а не «объект не найден»): эта сборка не умеет.
            kind = "not_supported"
        raise BossmanError(message, status=r.status_code, code=code, hint=hint, kind=kind)

    def get(self, path: str, **kw: Any) -> Any:
        return self.request("GET", path, **kw)

    def post(self, path: str, body: Any = None, **kw: Any) -> Any:
        # BOSSMAN_TERMINAL_NO_APPROVE (окно учителя/автоматики): проверка была только
        # в `bossman approve`, а /approve в чате и ask-режим follow слали решение
        # напрямую. Одна точка для всех путей терминала — через этот клиент.
        if (isinstance(body, dict) and body.get("approve") and _APPROVAL_DECISION.match(path)
                and os.environ.get("BOSSMAN_TERMINAL_NO_APPROVE", "").strip() in ("1", "true", "yes")):
            raise BossmanError("одобрение из этого окна запрещено (BOSSMAN_TERMINAL_NO_APPROVE=1): решение "
                               "принимает владелец в вебе, Telegram или своём терминале", kind="blocked")
        return self.request("POST", path, json=body if body is not None else {}, **kw)

    def patch(self, path: str, body: Any = None, **kw: Any) -> Any:
        return self.request("PATCH", path, json=body if body is not None else {}, **kw)

    def delete(self, path: str, **kw: Any) -> Any:
        return self.request("DELETE", path, **kw)

    # -- live events --------------------------------------------------------

    def stream_task(self, task_id: int, *, after: int | None, stop: threading.Event,
                    read_timeout: float = 45.0) -> Iterator[dict]:
        """One SSE connection to /api/events/stream. Yields decoded events;
        returns when the server ends the stream; raises BossmanError on a
        transport failure (the caller reconnects with its cursor)."""
        params: dict[str, Any] = {"task_id": task_id}
        if after is not None:
            params["after"] = after
        timeout = httpx.Timeout(connect=5.0, read=read_timeout, write=10.0, pool=5.0)
        headers = dict(self.http.headers)
        try:
            with httpx.Client(base_url=self.target.url, trust_env=False, timeout=timeout,
                              headers=headers, cookies=self.http.cookies) as sc:
                with sc.stream("GET", "/api/events/stream", params=params) as r:
                    if r.status_code != 200:
                        r.read()
                        self._decode(r, "GET", "/api/events/stream")
                    data_lines: list[str] = []
                    for line in r.iter_lines():
                        if stop.is_set():
                            return
                        if line.startswith(":"):
                            yield {"kind": "stream.keepalive"}
                            continue
                        if line == "":
                            if data_lines:
                                raw = "\n".join(data_lines)
                                data_lines = []
                                try:
                                    msg = json.loads(raw)
                                except ValueError:
                                    continue
                                if isinstance(msg, dict):
                                    yield msg
                            continue
                        if line.startswith("data:"):
                            data_lines.append(line[5:].lstrip())
        except httpx.HTTPError as exc:
            raise BossmanError(f"поток событий прерван ({type(exc).__name__})",
                               kind="disconnected") from exc


class EventPump:
    """Background reader: SSE -> queue, with reconnect + replay by cursor.

    A lost connection is resumed with `after=<last seq>`, so stored events are
    delivered exactly once; bounded retries with backoff, then a
    `stream.disconnected` item tells the follower to fall back to polling."""

    def __init__(self, client: Client, task_id: int, *, after: int = 0,
                 max_failures: int = 6):
        self.client = client
        self.task_id = task_id
        self.cursor = after
        self.items: "queue.Queue[dict]" = queue.Queue()
        self.stop = threading.Event()
        self.max_failures = max_failures
        self.opened = threading.Event()
        self._thread = threading.Thread(target=self._run, name="bossman-events", daemon=True)

    def start(self) -> "EventPump":
        self._thread.start()
        return self

    def close(self) -> None:
        self.stop.set()

    def _run(self) -> None:
        failures = 0
        first = True
        while not self.stop.is_set():
            opened = False
            try:
                for msg in self.client.stream_task(self.task_id, after=self.cursor, stop=self.stop):
                    if msg.get("kind") == "stream.open":
                        opened = True
                        self.opened.set()
                        if not first:
                            self.items.put({"kind": "stream.reconnected", "cursor": self.cursor})
                        first = False
                        failures = 0
                        continue
                    if msg.get("kind") == "stream.lagged":
                        break                              # переподключиться с курсором
                    seq = msg.get("seq")
                    if isinstance(seq, int):
                        if seq <= self.cursor:
                            continue
                        self.cursor = seq
                    self.items.put(msg)
            except BossmanError as exc:
                if exc.kind != "disconnected":
                    self.items.put({"kind": "stream.error", "message": exc.message})
                    return
            if self.stop.is_set():
                return
            if not opened:
                failures += 1                              # соединение не состоялось
            if failures >= self.max_failures:
                self.items.put({"kind": "stream.disconnected", "cursor": self.cursor})
                return
            time.sleep(min(0.25 * (2 ** failures), 5.0))
