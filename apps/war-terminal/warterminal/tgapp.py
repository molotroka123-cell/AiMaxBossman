"""Owner-only Telegram Mini App (Web App) for Czech defence/security tenders, opened from the owner's control bot (pult).

Auth (server side, every session):
  1. initData is verified with the official Telegram algorithm:
       secret_key = HMAC_SHA256(key="WebAppData", msg=bot_token)
       hash       = hex(HMAC_SHA256(key=secret_key, msg=data_check_string))
     data_check_string = all received fields except `hash`, sorted by key, "k=v" joined by "\n". Constant-time compare.
     auth_date older than 1 hour (or in the future beyond a small skew) is rejected.
  2. user.id must equal the owner id read at runtime from the companion config (people[0].user_id, fallback chat_id).
     The bot token is read at runtime from companion.env (TG_COMPANION_BOT_TOKEN). Neither is ever logged or returned.
  3. On success a short-lived HttpOnly; Secure; SameSite=None session cookie (Path=/tg/) is issued plus a CSRF token
     that the page must echo in X-TG-CSRF. Any failure -> 403 with no data (same answer for every reason).

The session never touches /api/v1 directly. It only works on a small proxy surface under /tg/api/ that is rewritten in
process to the existing API with the internal role token:
  GET  /tg/api/tenders[?...]            -> GET  /api/v1/tenders            (viewer)
  GET  /tg/api/tenders/{key}            -> GET  /api/v1/tenders/{key}      (viewer)
  GET  /tg/api/tenders/{key}/checklist  -> GET  /api/v1/tenders/{key}/checklist (viewer)
  POST /tg/api/assessments              -> POST /api/v1/assessments with dry_run=true, use_ai=false forced (analyst, read-only)
Everything else under /tg/api/ is 403. Serving the whole thing under /tg lets `tailscale serve --set-path /tg` expose
only the Mini App, not the main terminal UI.
"""
from __future__ import annotations

import hashlib
import hmac
import json
import os
import secrets
import threading
import time
from pathlib import Path
from urllib.parse import parse_qsl

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles

TG_UI_DIR = Path(__file__).parent / "ui" / "tg"
COOKIE = "wt_tg"
SESSION_TTL = 30 * 60              # seconds; short-lived on purpose
AUTH_MAX_AGE = 60 * 60             # initData older than 1 hour is rejected
FUTURE_SKEW = 60                   # tolerated clock skew for auth_date in the future
MAX_INIT_DATA = 4096
MAX_BODY = 16 * 1024
MAX_SESSIONS = 64
FAIL_WINDOW, FAIL_LIMIT = 60, 30   # coarse brute-force brake on /tg/api/auth
DENY = {"detail": {"code": "TG_FORBIDDEN", "message": "доступ только владельцу через пульт"}}

_now = time.time                   # tests may monkeypatch


# ---------------------------------------------------------------------------------------------------- runtime secrets
def _companion_dir() -> Path:
    base = os.environ.get("LOCALAPPDATA") or str(Path.home() / "AppData" / "Local")
    return Path(base) / "Bossman" / "telegram-companion"


def config_paths() -> tuple[Path, Path]:
    """(config.json, companion.env). Env overrides exist for tests and for a non-default install."""
    d = _companion_dir()
    return (Path(os.environ.get("WAR_TERMINAL_TG_CONFIG") or d / "config.json"),
            Path(os.environ.get("WAR_TERMINAL_TG_ENV") or d / "companion.env"))


def load_bot_token(env_path: Path | None = None) -> str | None:
    env_path = env_path or config_paths()[1]
    try:
        text = env_path.read_text(encoding="utf-8-sig")
    except OSError:
        return None
    for line in text.splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        k, v = line.split("=", 1)
        if k.strip().removeprefix("export ").strip() == "TG_COMPANION_BOT_TOKEN":
            v = v.strip().strip('"').strip("'")
            return v or None
    return None


def load_owner_id(config_path: Path | None = None) -> int | None:
    """people[0] of the companion config is the owner (role must be 'owner' if present). Fail closed on anything odd."""
    config_path = config_path or config_paths()[0]
    try:
        cfg = json.loads(config_path.read_text(encoding="utf-8-sig"))
        p0 = cfg["people"][0]
    except (OSError, ValueError, KeyError, IndexError, TypeError):
        return None
    if not isinstance(p0, dict) or p0.get("role", "owner") != "owner":
        return None
    raw = p0.get("user_id", p0.get("chat_id"))
    if isinstance(raw, bool):
        return None
    try:
        oid = int(raw)
    except (TypeError, ValueError):
        return None
    return oid if oid > 0 else None


# ---------------------------------------------------------------------------------------------------- initData check
def check_init_data(init_data: str, bot_token: str, *, now: float | None = None, max_age: int = AUTH_MAX_AGE) -> dict | None:
    """Official WebApp validation. Returns the parsed fields (user decoded) or None. Never raises on bad input."""
    if not isinstance(init_data, str) or not init_data or len(init_data) > MAX_INIT_DATA or not bot_token:
        return None
    try:
        pairs = parse_qsl(init_data, keep_blank_values=True, strict_parsing=True)
    except ValueError:
        return None
    fields: dict[str, str] = {}
    for k, v in pairs:
        if k in fields:                                   # duplicate keys make the check string ambiguous
            return None
        fields[k] = v
    received = fields.pop("hash", None)
    if not received or len(received) != 64:
        return None
    dcs = "\n".join(f"{k}={fields[k]}" for k in sorted(fields))
    secret_key = hmac.new(b"WebAppData", bot_token.encode("utf-8"), hashlib.sha256).digest()
    expected = hmac.new(secret_key, dcs.encode("utf-8"), hashlib.sha256).hexdigest()
    if not hmac.compare_digest(expected, received.lower()):
        return None
    try:
        auth_date = int(fields.get("auth_date", ""))
    except ValueError:
        return None
    t = _now() if now is None else now
    if auth_date > t + FUTURE_SKEW or t - auth_date > max_age:
        return None
    try:
        user = json.loads(fields.get("user", ""))
    except ValueError:
        return None
    if not isinstance(user, dict) or isinstance(user.get("id"), bool) or not isinstance(user.get("id"), int):
        return None
    return dict(fields, user=user, auth_date=auth_date)


def sign_init_data(fields: dict, bot_token: str) -> str:
    """Test/harness helper: build a signed initData query string exactly as Telegram does."""
    from urllib.parse import urlencode
    flat = {k: (json.dumps(v, separators=(",", ":"), ensure_ascii=False) if isinstance(v, (dict, list)) else str(v)) for k, v in fields.items()}
    dcs = "\n".join(f"{k}={flat[k]}" for k in sorted(flat))
    secret_key = hmac.new(b"WebAppData", bot_token.encode("utf-8"), hashlib.sha256).digest()
    flat["hash"] = hmac.new(secret_key, dcs.encode("utf-8"), hashlib.sha256).hexdigest()
    return urlencode(flat)


# ---------------------------------------------------------------------------------------------------- sessions
class Sessions:
    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._s: dict[str, dict] = {}
        self._fails: list[float] = []

    def issue(self) -> tuple[str, str]:
        sid, csrf = secrets.token_urlsafe(32), secrets.token_urlsafe(24)
        with self._lock:
            t = _now()
            self._s = {k: v for k, v in self._s.items() if v["exp"] > t}
            while len(self._s) >= MAX_SESSIONS:
                self._s.pop(min(self._s, key=lambda k: self._s[k]["exp"]))
            self._s[sid] = {"csrf": csrf, "exp": t + SESSION_TTL}
        return sid, csrf

    def valid(self, sid: str | None, csrf: str | None) -> bool:
        if not sid or not csrf:
            return False
        with self._lock:
            s = self._s.get(sid)
            if not s:
                return False
            if s["exp"] <= _now():
                self._s.pop(sid, None)
                return False
            return hmac.compare_digest(s["csrf"], csrf)

    def failed(self) -> bool:
        """Record a failed auth; True if the brake is engaged."""
        with self._lock:
            t = _now()
            self._fails = [f for f in self._fails if t - f < FAIL_WINDOW] + [t]
            return len(self._fails) > FAIL_LIMIT

    def braked(self) -> bool:
        with self._lock:
            t = _now()
            return len([f for f in self._fails if t - f < FAIL_WINDOW]) > FAIL_LIMIT


def _cookie_value(headers: list[tuple[bytes, bytes]], name: str) -> str | None:
    for k, v in headers:
        if k == b"cookie":
            for part in v.decode("latin-1").split(";"):
                n, _, val = part.strip().partition("=")
                if n == name:
                    return val
    return None


def _header(headers: list[tuple[bytes, bytes]], name: bytes) -> str | None:
    for k, v in headers:
        if k == name:
            return v.decode("latin-1")
    return None


# ---------------------------------------------------------------------------------------------------- proxy middleware
class TgProxy:
    """Pure ASGI middleware: maps an authenticated Mini App session to a tiny whitelist of the existing API."""
    STRIP = {b"authorization", b"x-war-token", b"cookie", b"x-tg-csrf", b"content-length", b"content-type"}

    def __init__(self, app, sessions: Sessions, tokens_ref) -> None:
        self.app, self.sessions, self.tokens_ref = app, sessions, tokens_ref

    async def _reply(self, scope, receive, send, status: int, body: dict):
        await JSONResponse(body, status_code=status, headers={"cache-control": "no-store"})(scope, receive, send)

    async def __call__(self, scope, receive, send):
        path = scope.get("path", "") if scope["type"] == "http" else ""
        if not path.startswith("/tg/api/") or path == "/tg/api/auth":
            return await self.app(scope, receive, send)
        headers = list(scope.get("headers") or [])
        if not self.sessions.valid(_cookie_value(headers, COOKIE), _header(headers, b"x-tg-csrf")):
            return await self._reply(scope, receive, send, 401, {"detail": {"code": "TG_SESSION_REQUIRED", "message": "сессия истекла: откройте заново из пульта"}})
        rest, method = path[len("/tg/api/"):], scope["method"]
        body = None
        if method == "GET" and (rest == "tenders" or rest.startswith("tenders/")):
            role = "viewer"
        elif method == "POST" and rest == "assessments":
            role = "analyst"                                  # analyst-level endpoint, but forced read-only below
            raw, more = b"", True
            while more:
                msg = await receive()
                raw += msg.get("body", b"")
                more = msg.get("more_body", False)
                if len(raw) > MAX_BODY:
                    return await self._reply(scope, receive, send, 413, {"detail": {"code": "TOO_LARGE"}})
            try:
                req = json.loads(raw or b"{}")
            except ValueError:
                req = None
            if not isinstance(req, dict) or not isinstance(req.get("tender_key"), str) or req.get("dry_run") is not True \
                    or req.get("use_ai") not in (None, False) or set(req) - {"tender_key", "dry_run", "use_ai", "back_to_back"}:
                return await self._reply(scope, receive, send, 403, {"detail": {"code": "TG_DRY_RUN_ONLY", "message": "из мини-приложения доступна только оценка dry_run=true"}})
            body = json.dumps({"tender_key": req["tender_key"], "dry_run": True, "use_ai": False,
                               "back_to_back": bool(req.get("back_to_back") is True)}).encode("utf-8")
        else:
            return await self._reply(scope, receive, send, 403, {"detail": {"code": "TG_NOT_ALLOWED", "message": "действие недоступно в мини-приложении"}})
        token = self.tokens_ref().get(role)
        if not token:
            return await self._reply(scope, receive, send, 503, {"detail": {"code": "TG_NOT_CONFIGURED"}})
        new_headers = [(k, v) for k, v in headers if k not in self.STRIP] + [(b"authorization", f"Bearer {token}".encode("latin-1"))]
        raw_path = scope.get("raw_path") or path.encode("latin-1")
        new_scope = dict(scope, path="/api/v1/" + rest, raw_path=b"/api/v1/" + raw_path[len(b"/tg/api/"):])
        if body is not None:
            new_headers += [(b"content-type", b"application/json"), (b"content-length", str(len(body)).encode())]
            sent = False

            async def receive_body():
                nonlocal sent
                if not sent:
                    sent = True
                    return {"type": "http.request", "body": body, "more_body": False}
                return await receive()
            inner_receive = receive_body
        else:
            inner_receive = receive
        new_scope["headers"] = new_headers

        async def send_nostore(msg):
            if msg["type"] == "http.response.start":
                msg = dict(msg, headers=[h for h in msg.get("headers", []) if h[0].lower() != b"cache-control"] + [(b"cache-control", b"no-store")])
            await send(msg)
        await self.app(new_scope, inner_receive, send_nostore)


# ---------------------------------------------------------------------------------------------------- mount
def mount(app: FastAPI, store=None) -> None:
    """Called once from create_app. Adds /tg/api/auth, the /tg/api proxy and the static UI at /tg/."""
    sessions = Sessions()
    app.state.tg_sessions = sessions

    def audit(event: str, payload: dict | None = None) -> None:
        if store is not None:
            store.audit("tg_owner", event, payload or {})      # never the owner id, never the token

    @app.post("/tg/api/auth", include_in_schema=False)
    async def tg_auth(request: Request):
        if sessions.braked():
            return JSONResponse({"detail": {"code": "TG_RATE_LIMITED"}}, status_code=429, headers={"cache-control": "no-store"})
        token, owner_id = load_bot_token(), load_owner_id()
        if not token or not owner_id:
            audit("tg.auth.not_configured")
            return JSONResponse({"detail": {"code": "TG_NOT_CONFIGURED", "message": "пульт не настроен на этом ПК"}}, status_code=503,
                                headers={"cache-control": "no-store"})
        try:
            raw = await request.body()
            body = json.loads(raw[:MAX_INIT_DATA * 2] or b"{}") if len(raw) <= MAX_INIT_DATA * 2 else {}
        except ValueError:
            body = {}
        init_data = body.get("init_data") if isinstance(body, dict) else None
        parsed = check_init_data(init_data, token) if isinstance(init_data, str) else None
        ok = parsed is not None and hmac.compare_digest(str(parsed["user"]["id"]), str(owner_id))
        if not ok:
            sessions.failed()
            audit("tg.auth.denied", {"reason": "missing" if not init_data else ("signature_or_age" if parsed is None else "not_owner")})
            return JSONResponse(DENY, status_code=403, headers={"cache-control": "no-store"})
        sid, csrf = sessions.issue()
        audit("tg.auth.ok", {"ttl": SESSION_TTL})
        resp = JSONResponse({"ok": True, "csrf": csrf, "expires_in": SESSION_TTL, "access": "viewer+dry_run_assessment"},
                            headers={"cache-control": "no-store"})
        resp.set_cookie(COOKIE, sid, max_age=SESSION_TTL, path="/tg/", httponly=True, secure=True, samesite="none")
        return resp

    @app.get("/tg", include_in_schema=False)
    def tg_root():
        return RedirectResponse("/tg/")

    if TG_UI_DIR.exists():
        app.mount("/tg", StaticFiles(directory=str(TG_UI_DIR), html=True), name="tg")
    app.add_middleware(TgProxy, sessions=sessions, tokens_ref=lambda: app.state.tokens)
