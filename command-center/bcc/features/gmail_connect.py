"""Owner's Gmail connection API (/api/gmail/*, token-auth like every feature router).

The logic lives in `bcc.gmail_connector`; here are only the owner-facing endpoints and the
one-shot loopback listener of the OAuth installed-app flow (RFC 8252 §7.3): it binds
127.0.0.1 on a random port, accepts exactly one redirect with the right `state`, and closes.
No endpoint ever returns a secret: client secret, tokens and the app password go in, only
status comes out. The owner normally drives this through `bossman gmail …`
(docs/owner/GMAIL_CONNECT_RU.md).
"""
from __future__ import annotations

import asyncio
from urllib.parse import parse_qs, urlsplit

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel, Field

from .. import gmail_connector as G
from . import Feature

router = APIRouter()

_LISTENER: dict[str, object] = {}          # {"server": asyncio.Server, "timer": Task, "result": dict}
_LISTEN_HOST = "127.0.0.1"

_DONE_HTML = ("<!doctype html><meta charset=utf-8><title>Bossman</title>"
              "<p style='font:16px sans-serif'>{msg}</p>")


def _svc(request: Request):
    svc = getattr(request.app.state, "svc", None)
    if svc is None:
        raise HTTPException(503, "services unavailable")
    return svc


def _bad(exc: Exception) -> HTTPException:
    return HTTPException(422, {"message": str(exc) if isinstance(exc, G.GmailError) else type(exc).__name__})


class ClientIn(BaseModel):
    client_id: str | None = Field(default=None, max_length=300)
    client_secret: str | None = Field(default=None, max_length=300)
    installed: dict | None = None            # the JSON Google lets you download, as is
    web: dict | None = None


class SettingsIn(BaseModel):
    send_enabled: bool | None = None
    drafts_enabled: bool | None = None
    owner_address: str | None = Field(default=None, max_length=254)
    agent_ids: list[int] | None = None


class ImapIn(BaseModel):
    address: str = Field(max_length=254)
    app_password: str = Field(max_length=64)


class CompleteIn(BaseModel):
    redirect_url: str = Field(max_length=4096)


class DisconnectIn(BaseModel):
    revoke: bool = True


@router.get("/gmail/status")
async def get_status(request: Request):
    out = await G.status(_svc(request))
    res = _LISTENER.get("result")
    out["last_oauth_result"] = res if isinstance(res, dict) else None
    out["oauth_waiting"] = _LISTENER.get("server") is not None
    return out


@router.put("/gmail/oauth-client")
async def put_client(body: ClientIn, request: Request):
    try:
        await G.save_client(_svc(request), body.model_dump(exclude_none=True))
    except G.GmailError as exc:
        raise _bad(exc) from None
    return {"ok": True, "client_configured": True}


@router.put("/gmail/settings")
async def put_settings(body: SettingsIn, request: Request):
    svc = _svc(request)
    try:
        prefs = await G.save_prefs(svc, **body.model_dump(exclude_none=True))
    except G.GmailError as exc:
        raise _bad(exc) from None
    await svc.bus.emit("gmail.settings_changed", send_enabled=prefs["send_enabled"],
                       drafts_enabled=prefs["drafts_enabled"], agent_ids=prefs["agent_ids"])
    return await G.status(svc)


@router.put("/gmail/imap")
async def put_imap(body: ImapIn, request: Request):
    svc = _svc(request)
    try:
        await G.save_imap(svc, body.address, body.app_password)
    except G.GmailError as exc:
        raise _bad(exc) from None
    await svc.bus.emit("gmail.connected", mode="imap")
    return await G.status(svc)


async def _close_listener() -> None:
    server = _LISTENER.pop("server", None)
    timer = _LISTENER.pop("timer", None)
    if server is not None:
        server.close()
    if timer is not None and timer is not asyncio.current_task():
        timer.cancel()


async def _handle_redirect(svc, reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
    """One HTTP request from the owner's browser: GET /?state=…&code=… (or ?error=…)."""
    status, msg = 400, "Неверный запрос."
    try:
        head = await asyncio.wait_for(reader.readuntil(b"\r\n\r\n"), timeout=10.0)
        line = head.split(b"\r\n", 1)[0].decode("latin-1")
        method, target, _ = (line.split(" ", 2) + ["", ""])[:3]
        q = parse_qs(urlsplit(target).query)
        if method != "GET" or not (q.get("state") or q.get("error")):
            status, msg = 404, "Не тот адрес."
        elif q.get("error"):
            _LISTENER["result"] = {"ok": False, "error": f"Google: {q['error'][0][:80]}"}
            status, msg = 200, "Подключение отменено. Можно закрыть вкладку."
            await _close_listener()
        else:
            try:
                res = await G.complete_oauth(svc, q["state"][0], (q.get("code") or [""])[0])
                _LISTENER["result"] = {"ok": True, "address": res["address"], "scopes": res["scopes"]}
                await svc.bus.emit("gmail.connected", mode="oauth", scopes=res["scopes"])
                status, msg = 200, "Gmail подключён к Bossman. Вкладку можно закрыть."
            except G.GmailError as exc:
                _LISTENER["result"] = {"ok": False, "error": str(exc)}
                status, msg = 200, "Не получилось: " + str(exc)
            await _close_listener()
    except (asyncio.TimeoutError, asyncio.IncompleteReadError, asyncio.LimitOverrunError, OSError):
        pass
    try:
        body = _DONE_HTML.format(msg=msg.replace("<", "&lt;")).encode("utf-8")
        writer.write(f"HTTP/1.1 {status} OK\r\nContent-Type: text/html; charset=utf-8\r\n"
                     f"Content-Length: {len(body)}\r\nConnection: close\r\n\r\n".encode() + body)
        await writer.drain()
    except OSError:
        pass
    finally:
        writer.close()


@router.post("/gmail/oauth/start")
async def oauth_start(request: Request):
    """Start the browser consent: returns the Google URL; Bossman waits on a loopback port."""
    svc = _svc(request)
    await _close_listener()
    _LISTENER.pop("result", None)
    server = await asyncio.start_server(lambda r, w: _handle_redirect(svc, r, w), _LISTEN_HOST, 0,
                                        limit=16 * 1024)
    port = server.sockets[0].getsockname()[1]
    try:
        out = await G.begin_oauth(svc, f"http://{_LISTEN_HOST}:{port}/")
    except G.GmailError as exc:
        server.close()
        raise _bad(exc) from None

    async def expire():
        await asyncio.sleep(G.FLOW_TTL_S)
        await _close_listener()

    _LISTENER["server"] = server
    _LISTENER["timer"] = asyncio.create_task(expire())
    return {"auth_url": out["auth_url"], "redirect_uri": out["redirect_uri"], "scopes": out["scopes"],
            "expires_in": out["expires_in"]}


@router.post("/gmail/oauth/complete")
async def oauth_complete(body: CompleteIn, request: Request):
    """Fallback: the owner pastes the address the browser ended on (if the listener missed it)."""
    svc = _svc(request)
    try:
        state, code = G.parse_redirect(body.redirect_url)
        res = await G.complete_oauth(svc, state, code)
    except G.GmailError as exc:
        raise _bad(exc) from None
    await _close_listener()
    await svc.bus.emit("gmail.connected", mode="oauth", scopes=res["scopes"])
    return await G.status(svc)


@router.post("/gmail/disconnect")
async def oauth_disconnect(body: DisconnectIn, request: Request):
    svc = _svc(request)
    await _close_listener()
    out = await G.disconnect(svc, revoke=body.revoke)
    await svc.bus.emit("gmail.disconnected", revoked=out["revoked_at_google"])
    return {**out, **(await G.status(svc))}


@router.post("/gmail/test")
async def connection_test(request: Request):
    """Owner-initiated read-only check: the newest letter's headers (no bodies)."""
    from ..tools import ToolContext
    svc = _svc(request)
    ctx = ToolContext(svc=svc, task={}, run_id=0, agent={})
    res = await G.h_search({"query": "", "max_results": 1}, ctx)
    return {"ok": not res.error, "summary": res.one_line,
            "error": res.content[:300] if res.error else None}


FEATURE = Feature(name="gmail_connect", router=router)
