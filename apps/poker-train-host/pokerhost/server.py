"""Public host for the Poker Train Mini App: the built game + a Telegram-signed leaderboard. Nothing else.

It runs as its OWN process on 127.0.0.1 (reached from the internet only through the Cloudflare tunnel) and has no
route to Bossman, files outside the game build, keys or the owner's data. The game itself runs in the player's
browser; this server only serves static files and stores leaderboard rows.

Routes: GET static game; GET/POST /api/leaderboard; /api/claude -> 503 (paid model is off on the host, the game
falls back to its local AI); GET /api/health.
"""
from __future__ import annotations

import json
import math
import sqlite3
import threading
import time
from pathlib import Path

from starlette.applications import Starlette
from starlette.middleware import Middleware
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request
from starlette.responses import FileResponse, JSONResponse, Response
from starlette.routing import Mount, Route
from starlette.staticfiles import StaticFiles

from .initdata import display_name, verify

MAX_BODY = 8 * 1024
POST_EVERY_S = 10.0            # one leaderboard update per player per 10 s
IP_RPS_WINDOW_S, IP_RPS_MAX = 10.0, 60
CSP = ("default-src 'self'; script-src 'self' 'unsafe-inline' https://telegram.org; "
       "style-src 'self' 'unsafe-inline'; img-src 'self' data: https:; connect-src 'self'; "
       "font-src 'self' data:; object-src 'none'; base-uri 'none'; form-action 'none'; "
       "frame-ancestors https://web.telegram.org https://*.telegram.org")
NUMERIC = {"gtoScore": (0, 100), "hands": (0, 10_000_000), "sessions": (0, 1_000_000), "bestFinish": (1, 100_000),
           "roi": (-100, 100_000), "vpip": (0, 100), "pfr": (0, 100)}


class Store:
    """sqlite leaderboard: one row per Telegram user id."""

    def __init__(self, path: Path):
        path.parent.mkdir(parents=True, exist_ok=True)
        self.db = sqlite3.connect(str(path), check_same_thread=False)
        self.lock = threading.Lock()
        self.db.execute("CREATE TABLE IF NOT EXISTS players (tg_id INTEGER PRIMARY KEY, name TEXT, stats TEXT, "
                        "last_active REAL)")
        self.db.commit()

    def upsert(self, tg_id: int, name: str, stats: dict) -> None:
        with self.lock:
            self.db.execute("INSERT INTO players VALUES (?,?,?,?) ON CONFLICT(tg_id) DO UPDATE SET name=excluded.name,"
                            " stats=excluded.stats, last_active=excluded.last_active",
                            (tg_id, name, json.dumps(stats), time.time()))
            self.db.commit()

    def top(self, period: str, limit: int) -> tuple[list[dict], int]:
        with self.lock:
            rows = self.db.execute("SELECT tg_id, name, stats, last_active FROM players").fetchall()
        since = time.time() - 7 * 86400 if period == "weekly" else 0
        players = [{"playerId": f"tg{r[0] % 100000:05d}", "name": r[1], **json.loads(r[2]),
                    "lastActive": int(r[3] * 1000)} for r in rows if r[3] >= since]
        players.sort(key=lambda p: (-p.get("gtoScore", 0), -p.get("hands", 0)))
        return players[:limit], len(rows)


def clean_stats(raw: dict) -> dict:
    out = {}
    for key, (lo, hi) in NUMERIC.items():
        val = raw.get(key)
        if isinstance(val, bool) or not isinstance(val, (int, float)) or not math.isfinite(val):
            continue
        out[key] = max(lo, min(hi, round(val, 1)))
    return out


class Guard(BaseHTTPMiddleware):
    """Security headers, request size cap and a per-IP rate limit."""

    def __init__(self, app, **kw):
        super().__init__(app, **kw)
        self.hits: dict[str, list[float]] = {}

    async def dispatch(self, request: Request, call_next):
        ip = request.headers.get("cf-connecting-ip") or (request.client.host if request.client else "?")
        now = time.monotonic()
        recent = [t for t in self.hits.get(ip, []) if now - t < IP_RPS_WINDOW_S]
        if len(recent) >= IP_RPS_MAX:
            return JSONResponse({"error": "rate_limited"}, status_code=429)
        self.hits[ip] = recent + [now]
        if len(self.hits) > 50_000:
            self.hits.clear()
        if int(request.headers.get("content-length") or 0) > MAX_BODY:
            return JSONResponse({"error": "too_large"}, status_code=413)
        resp: Response = await call_next(request)
        resp.headers.update({"Content-Security-Policy": CSP, "X-Content-Type-Options": "nosniff",
                             "Referrer-Policy": "no-referrer", "Cross-Origin-Opener-Policy": "same-origin"})
        return resp


def create_app(dist: Path, data_dir: Path, bot_tokens: list[str]) -> Starlette:
    dist = Path(dist).resolve()
    store = Store(Path(data_dir) / "leaderboard.sqlite3")
    last_post: dict[int, float] = {}

    async def leaderboard(request: Request):
        if request.method == "GET":
            period = request.query_params.get("period", "all")
            try:
                limit = max(1, min(100, int(request.query_params.get("limit", "50"))))
            except ValueError:
                limit = 50
            players, total = store.top("weekly" if period == "weekly" else "all", limit)
            return JSONResponse({"players": players, "total": total, "updatedAt": int(time.time() * 1000)})
        body = await request.body()
        if len(body) > MAX_BODY:
            return JSONResponse({"error": "too_large"}, status_code=413)
        try:
            data = json.loads(body or b"{}")
        except json.JSONDecodeError:
            return JSONResponse({"error": "bad_json"}, status_code=400)
        user = verify(str(data.get("initData") or ""), bot_tokens) if isinstance(data, dict) else None
        if user is None:
            return JSONResponse({"error": "telegram_signature_required"}, status_code=401)
        now = time.monotonic()
        if now - last_post.get(user["id"], -1e9) < POST_EVERY_S:
            return JSONResponse({"error": "too_often"}, status_code=429)
        last_post[user["id"]] = now
        store.upsert(user["id"], display_name(user), clean_stats(data))
        return JSONResponse({"ok": True})

    async def claude_off(_request: Request):
        return JSONResponse({"error": "disabled_on_host", "message": "Платная модель на хосте выключена; "
                             "играет локальный ИИ."}, status_code=503)

    async def health(_request: Request):
        return JSONResponse({"ok": True, "players": store.top("all", 1)[1]})

    async def index(_request: Request):
        return FileResponse(dist / "index.html", headers={"Cache-Control": "no-cache"})

    routes = [Route("/api/leaderboard", leaderboard, methods=["GET", "POST"]),
              Route("/api/claude", claude_off, methods=["GET", "POST"]),
              Route("/api/health", health, methods=["GET"]),
              Route("/", index, methods=["GET"]),
              Mount("/", StaticFiles(directory=str(dist), html=True), name="game")]
    return Starlette(routes=routes, middleware=[Middleware(Guard)])
