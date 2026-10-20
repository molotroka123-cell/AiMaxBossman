"""Panel: the owner's Bossman admin UI and API 1:1 (all paths, all methods, SSE, WebSocket) behind the phone session.

* /panel/<path>  -> Bossman /<path>  (the panel tab loads /panel/; its relative assets resolve under /panel/)
* /<path>        -> Bossman /<path>  (the Bossman UI calls absolute /api/... and WS /api/events)
* everything under /m/ is the mobile app itself and is never proxied.

Authentication is replaced, not forwarded: the phone's cookie is dropped and Bossman's own session (made
server-side from the owner token) is injected. The Bossman UI keeps its CSRF value in localStorage `bcc.csrf`;
the proxy puts a per-phone-session value there (`panel_csrf`) and swaps it for Bossman's real CSRF token on the
way in, so a mutating request needs both the SameSite=Strict cookie and that value. Upstream Set-Cookie headers
never reach the phone; the owner token, Bossman's session id and CSRF token are redacted from every body.
"""
from __future__ import annotations

import asyncio
import contextlib
import json
import secrets
from typing import Any
from urllib.parse import urlsplit

from fastapi import Request, WebSocket
from fastapi.responses import JSONResponse, Response, StreamingResponse

from .bossman import BCC_CSRF, BossmanClient, BossmanUnavailable, Redactor

HOP = {"connection", "keep-alive", "proxy-authenticate", "proxy-authorization", "te", "trailers", "transfer-encoding",
       "upgrade", "host", "content-length", "accept-encoding"}
DROP_REQ = HOP | {"cookie", "x-bcc-token", "authorization", BCC_CSRF.lower(), "x-forwarded-for", "x-forwarded-proto",
                  "x-forwarded-host", "forwarded", "origin", "referer", "x-participant", "x-bcc-participant"}
DROP_RESP = {"connection", "keep-alive", "transfer-encoding", "content-length", "content-encoding", "set-cookie",
             "upgrade", "trailers"}
SAFE = {"GET", "HEAD", "OPTIONS"}


def _inject(html: bytes, panel_csrf: str) -> bytes:
    """Bossman UI treats `bcc.csrf` in localStorage as "logged in"; give it this phone session's panel value."""
    script = ("<script>try{localStorage.setItem('bcc.csrf'," + json.dumps(panel_csrf) + ")}catch(e){}</script>").encode()
    low = html.lower()
    at = low.find(b"<head>")
    if at >= 0:
        at += len(b"<head>")
        return html[:at] + script + html[at:]
    return script + html


def _same_origin(origin: str, host: str) -> bool:
    try:
        p = urlsplit(origin)
    except ValueError:
        return False
    return p.scheme in ("http", "https") and bool(host) and p.netloc.lower() == host.strip().lower()


def _location(value: str, upstream: str, prefix: str) -> str:
    if value.startswith(upstream):
        value = value[len(upstream):] or "/"
    if prefix and value.startswith("/") and not value.startswith("//"):
        value = prefix + value
    return value


async def proxy_http(request: Request, path: str, prefix: str, bm: BossmanClient, sess: dict, end_session) -> Response:
    method = request.method.upper()
    up_path = "/" + path.lstrip("/")
    host = request.headers.get("host", "")
    if method not in SAFE:
        origin = request.headers.get("origin")
        if origin and not _same_origin(origin, host):
            return JSONResponse({"error": {"message": "чужой источник запроса", "code": "BM_ORIGIN"}}, 403)
        sent = request.headers.get(BCC_CSRF) or ""
        if not sent or not secrets.compare_digest(sent.encode("utf-8", "ignore"), sess["panel_csrf"].encode()):
            return JSONResponse({"error": {"message": "не пройдена CSRF-проверка", "code": "csrf",
                                           "hint": "откройте панель заново из приложения"}}, 403)
    body = await request.body()

    # ---- the few auth endpoints of the Bossman UI are answered here, never by Bossman's token logic
    if up_path == "/api/login-hint":
        return JSONResponse({"token_file": None})
    if up_path == "/api/login" and method == "POST":
        # The phone is already the owner (mobile session). Re-establish Bossman's session server-side.
        try:
            await bm.login()
        except BossmanUnavailable as exc:
            return JSONResponse({"error": {"message": str(exc)}}, 502)
        return JSONResponse({"ok": True, "csrf": sess["panel_csrf"], "csrf_header": BCC_CSRF, "expires_at": None})
    if up_path == "/api/logout" and method == "POST":
        await end_session()
        resp = JSONResponse({"ok": True, "revoked": True})
        resp.delete_cookie("bm_session", path="/")
        return resp

    headers = {k: v for k, v in request.headers.items() if k.lower() not in DROP_REQ}
    headers["accept-encoding"] = "identity"
    try:
        r = await bm.send(method, up_path, params=list(request.query_params.multi_items()), content=body or None,
                          headers=headers, stream=True, timeout=None if "text/event-stream" in
                          request.headers.get("accept", "") else 600)
    except BossmanUnavailable as exc:
        return JSONResponse({"error": {"message": str(exc), "code": "BOSSMAN_UNAVAILABLE"}}, 502)
    red = Redactor(bm.secrets())
    out_headers = {}
    for k, v in r.headers.multi_items():
        lk = k.lower()
        if lk in DROP_RESP or any(s in v for s in bm.secrets()):
            continue
        if lk == "location":
            v = _location(v, bm.base_url, prefix)
        out_headers[k] = v
    ctype = r.headers.get("content-type", "")
    if "text/html" in ctype:
        try:
            raw = await r.aread()
        finally:
            await r.aclose()
        data = red.clean(raw)
        data = _inject(data, sess["panel_csrf"])
        out_headers["cache-control"] = "no-store"
        return Response(data, status_code=r.status_code, headers=out_headers)

    async def gen():
        try:
            async for chunk in r.aiter_bytes():
                out = red.feed(chunk)
                if out:
                    yield out
            tail = red.flush()
            if tail:
                yield tail
        finally:
            await r.aclose()

    return StreamingResponse(gen(), status_code=r.status_code, headers=out_headers)


async def default_ws_connect(url: str, headers: dict[str, str]):
    from websockets.asyncio.client import connect
    return await connect(url, additional_headers=headers, open_timeout=15, max_size=16 * 1024 * 1024)


async def proxy_ws(ws: WebSocket, path: str, bm: BossmanClient, ws_connect=None) -> None:
    connect = ws_connect or default_ws_connect
    if not bm.sid:
        try:
            await bm.login()
        except BossmanUnavailable:
            await ws.close(code=1011)
            return
    base = bm.base_url.replace("http://", "ws://", 1).replace("https://", "wss://", 1)
    qs = ws.url.query
    url = f"{base}/{path.lstrip('/')}" + (f"?{qs}" if qs else "")
    try:
        upstream = await connect(url, {"Cookie": f"bcc_session={bm.sid}"})
    except Exception:  # noqa: BLE001 - the phone sees a closed socket, the UI reconnects itself
        await ws.close(code=1011)
        return
    await ws.accept()
    red = Redactor(bm.secrets())

    async def down():
        async for msg in upstream:
            if isinstance(msg, bytes):
                await ws.send_bytes(red.clean(msg))
            else:
                await ws.send_text(red.text(msg))

    async def up():
        while True:
            m: dict[str, Any] = await ws.receive()
            if m.get("type") == "websocket.disconnect":
                return
            if m.get("text") is not None:
                await upstream.send(m["text"])
            elif m.get("bytes") is not None:
                await upstream.send(m["bytes"])

    tasks = [asyncio.create_task(down()), asyncio.create_task(up())]
    try:
        await asyncio.wait(tasks, return_when=asyncio.FIRST_COMPLETED)
    finally:
        for t in tasks:
            t.cancel()
        with contextlib.suppress(Exception):
            await upstream.close()
        with contextlib.suppress(Exception):
            await ws.close()
