"""Server-side client of the owner's Bossman Command Center.

The owner token is read from its file only to call POST /api/login; Bossman answers with its own HttpOnly
session cookie (`bcc_session`) and a CSRF token. Both stay in this process: they are sent only to Bossman and are
redacted from anything that goes back to the phone (see `Redactor`). On 401 the session is renewed once.
"""
from __future__ import annotations

import asyncio
from pathlib import Path
from typing import Any

import httpx

BCC_COOKIE = "bcc_session"
BCC_CSRF = "X-BCC-CSRF"
UNSAFE = {"POST", "PUT", "PATCH", "DELETE"}


class BossmanUnavailable(Exception):
    pass


class BossmanClient:
    def __init__(self, base_url: str, token_file: Path, transport: httpx.AsyncBaseTransport | None = None,
                 timeout: float = 60.0):
        self.base_url = base_url.rstrip("/")
        self.token_file = Path(token_file)
        self.http = httpx.AsyncClient(base_url=self.base_url, transport=transport, timeout=timeout, trust_env=False,
                                      follow_redirects=False)
        self.sid: str | None = None
        self.csrf: str | None = None
        self._token: str | None = None
        self._lock = asyncio.Lock()

    # ---------------------------------------------------------------- secrets kept here
    def _read_token(self) -> str:
        try:
            tok = self.token_file.read_text(encoding="utf-8").strip()
        except OSError as exc:
            raise BossmanUnavailable(f"файл токена Bossman не читается: {type(exc).__name__}") from None
        if not tok:
            raise BossmanUnavailable("файл токена Bossman пуст")
        self._token = tok
        return tok

    def secrets(self) -> list[str]:
        """Every value that must never reach the phone (for the redactor)."""
        out = [s for s in (self._token, self.sid, self.csrf) if s and len(s) >= 8]
        if self._token is None:
            try:
                out.append(self._read_token())
            except BossmanUnavailable:
                pass
        return out

    async def login(self) -> None:
        async with self._lock:
            token = self._read_token()
            try:
                r = await self.http.post("/api/login", json={"token": token, "label": "bossman-mobile"})
            except httpx.HTTPError as exc:
                raise BossmanUnavailable(f"Bossman не отвечает: {type(exc).__name__}") from None
            if r.status_code != 200:
                raise BossmanUnavailable(f"вход в Bossman не удался (HTTP {r.status_code})")
            sid = r.cookies.get(BCC_COOKIE)
            if not sid:
                for raw in r.headers.get_list("set-cookie"):
                    if raw.startswith(BCC_COOKIE + "="):
                        sid = raw.split("=", 1)[1].split(";", 1)[0]
            if not sid:
                raise BossmanUnavailable("Bossman не выдал сессию")
            self.sid, self.csrf = sid, (r.json() or {}).get("csrf")

    async def logout(self) -> None:
        if self.sid:
            try:
                await self.http.post("/api/logout", headers={"Cookie": f"{BCC_COOKIE}={self.sid}"})
            except httpx.HTTPError:
                pass
        self.sid = self.csrf = None

    def auth_headers(self, method: str) -> dict[str, str]:
        h = {"Cookie": f"{BCC_COOKIE}={self.sid}"} if self.sid else {}
        if method.upper() in UNSAFE and self.csrf:
            h[BCC_CSRF] = self.csrf
        return h

    # ---------------------------------------------------------------- calls
    async def send(self, method: str, path: str, *, params: Any = None, json: Any = None, content: bytes | None = None,
                   headers: dict | None = None, stream: bool = False, timeout: float | None = None) -> httpx.Response:
        """One request with the session; renews it once on 401. `stream=True` -> caller must aclose()."""
        if not self.sid:
            await self.login()
        for attempt in (1, 2):
            hdrs = dict(headers or {})
            hdrs.update(self.auth_headers(method))
            req = self.http.build_request(method.upper(), path, params=params, json=json, content=content, headers=hdrs,
                                          timeout=timeout if timeout is not None else httpx.USE_CLIENT_DEFAULT)
            try:
                r = await self.http.send(req, stream=stream)
            except httpx.HTTPError as exc:
                raise BossmanUnavailable(f"Bossman не отвечает: {type(exc).__name__}") from None
            if r.status_code == 401 and attempt == 1:
                await r.aclose()
                await self.login()
                continue
            return r
        raise BossmanUnavailable("unreachable")  # pragma: no cover

    async def json(self, method: str, path: str, **kw) -> tuple[int, Any]:
        r = await self.send(method, path, **kw)
        try:
            body = r.json()
        except ValueError:
            body = {"error": {"message": r.text[:300]}}
        return r.status_code, body

    async def public_get(self, path: str) -> tuple[int, Any]:
        try:
            r = await self.http.get(path, timeout=10)
            return r.status_code, r.json()
        except (httpx.HTTPError, ValueError) as exc:
            return 0, {"error": f"{type(exc).__name__}"}

    async def aclose(self) -> None:
        await self.http.aclose()


class Redactor:
    """Replaces secret byte strings in a stream, also when one is split across chunks."""

    MARK = b"[hidden]"

    def __init__(self, values: list[str]):
        self.values = sorted({v.encode("utf-8") for v in values if v}, key=len, reverse=True)
        self.keep = max((len(v) for v in self.values), default=1) - 1
        self.buf = b""

    def clean(self, data: bytes) -> bytes:
        for v in self.values:
            data = data.replace(v, self.MARK)
        return data

    _clean = clean

    def feed(self, chunk: bytes) -> bytes:
        if not self.values:
            return chunk
        data = self._clean(self.buf + chunk)
        if self.keep <= 0:
            self.buf = b""
            return data
        self.buf, out = data[-self.keep:], data[:-self.keep]
        return out

    def flush(self) -> bytes:
        out, self.buf = self._clean(self.buf), b""
        return out

    def text(self, s: str) -> str:
        return self._clean(s.encode("utf-8")).decode("utf-8", "replace")
