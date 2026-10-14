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
import re
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

import asyncio
import hashlib

from . import analysis
from .initdata import display_name, verify, verify_bot

MAX_BODY = 8 * 1024
SESSION_MAX = 2 * 1024 * 1024  # one whole session (every hand + decision), trimmed by the game
SESSION_EVERY_S = 120.0        # one debrief request per player per 2 min
MAX_PENDING = 5                # debriefs waiting at once (the owner's GPU is shared)
SESSIONS_KEPT = 20             # per player, newest first
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


JEFF_EVERY_S = 2.0             # one Jeff decision per player per 2 s
JEFF_TIMEOUT_S = 25.0
CARD = re.compile(r"^[2-9TJQKA][shdc]$")
STAGES = ("preflop", "flop", "turn", "river")
ACTIONS = ("fold", "check", "call", "raise")


def clean_spot(raw) -> dict | None:
    """Only cards, a street, an action hint and bounded numbers — no free text reaches the model."""
    if not isinstance(raw, dict):
        return None
    hole, board = raw.get("holeCards"), raw.get("community") or []
    if not (isinstance(hole, list) and len(hole) == 2 and isinstance(board, list) and len(board) <= 5):
        return None
    if not all(isinstance(c, str) and CARD.match(c) for c in hole + board) or raw.get("stage") not in STAGES:
        return None
    spot = {"holeCards": hole, "community": board, "stage": raw["stage"],
            "position": raw.get("position") if raw.get("position") in POSITIONS else "?",
            "suggest": raw.get("suggest") if raw.get("suggest") in ACTIONS else "check"}
    for key in ("pot", "toCall", "myChips", "bigBlind", "playersInHand", "currentBet"):
        val = raw.get(key, 0)
        if isinstance(val, bool) or not isinstance(val, (int, float)) or not math.isfinite(val) or val < 0:
            return None
        spot[key] = min(int(val), 10**9)
    return spot


POSITIONS = ("UTG", "UTG+1", "MP", "HJ", "CO", "BTN", "SB", "BB")


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
        limit = SESSION_MAX if request.url.path == "/api/session" else MAX_BODY
        if int(request.headers.get("content-length") or 0) > limit:
            return JSONResponse({"error": "too_large"}, status_code=413)
        resp: Response = await call_next(request)
        resp.headers.update({"Content-Security-Policy": CSP, "X-Content-Type-Options": "nosniff",
                             "Referrer-Policy": "no-referrer", "Cross-Origin-Opener-Policy": "same-origin"})
        return resp


def create_app(dist: Path, data_dir: Path, bot_tokens: list[str], *, bots: dict[str, str] | None = None,
               deliver=None, coach=None, jeff=None) -> Starlette:
    """bots = {'jeff': token, 'pult': token} (which bot answers); deliver(bot, chat_id, text),
    coach(summary) -> str | None and jeff(spot) -> {action, amount} | None are async callables injected by
    __main__ (fakes in tests)."""
    last_jeff: dict[int, float] = {}
    jeff_busy = asyncio.Lock()
    dist = Path(dist).resolve()
    store = Store(Path(data_dir) / "leaderboard.sqlite3")
    last_post: dict[int, float] = {}
    bots = bots or {f"bot{i}": t for i, t in enumerate(bot_tokens)}
    last_session: dict[int, float] = {}
    pending: set = set()
    debrief_lock = asyncio.Lock()

    async def debrief(user: dict, bot: str, session: dict) -> None:
        async with debrief_lock:                      # one at a time: shared GPU / Ollama
            summary = analysis.summarize(session)
            text = analysis.render(summary, display_name(user))
            if coach is not None and summary["hands"]:
                try:
                    note = await coach(summary)
                except Exception:                     # noqa: BLE001 — the numbers still go out without the comment
                    note = None
                if note:
                    text += "\n\nКомментарий тренера (локальная модель на ПК владельца):\n" + note.strip()[:1500]
            if deliver is not None:
                await deliver(bot, user["id"], text)

    async def session_post(request: Request):
        body = await request.body()
        if len(body) > SESSION_MAX:
            return JSONResponse({"error": "too_large"}, status_code=413)
        try:
            data = json.loads(body or b"{}")
        except json.JSONDecodeError:
            return JSONResponse({"error": "bad_json"}, status_code=400)
        if not isinstance(data, dict):
            return JSONResponse({"error": "bad_json"}, status_code=400)
        user, bot = verify_bot(str(data.get("initData") or ""), bots)
        if user is None:
            return JSONResponse({"error": "telegram_signature_required"}, status_code=401)
        session = data.get("session")
        if not isinstance(session, dict) or not isinstance(session.get("handSummaries", []), list):
            return JSONResponse({"error": "bad_session"}, status_code=400)
        now = time.monotonic()
        if now - last_session.get(user["id"], -1e9) < SESSION_EVERY_S:
            return JSONResponse({"error": "too_often"}, status_code=429)
        if len(pending) >= MAX_PENDING:
            return JSONResponse({"error": "busy"}, status_code=429)
        last_session[user["id"]] = now
        folder = Path(data_dir) / "sessions" / hashlib.sha256(str(user["id"]).encode()).hexdigest()[:16]
        folder.mkdir(parents=True, exist_ok=True)
        (folder / f"{int(time.time() * 1000)}.json").write_text(json.dumps(session, ensure_ascii=False), "utf-8")
        for old in sorted(folder.glob("*.json"))[:-SESSIONS_KEPT]:
            old.unlink(missing_ok=True)
        task = asyncio.create_task(debrief(user, bot, session))
        pending.add(task)
        task.add_done_callback(pending.discard)
        return JSONResponse({"ok": True, "queued": True, "hands": len(session.get("handSummaries") or [])})

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

    async def jeff_move(request: Request):
        """HARDCORE: Jeff (local model on the owner's PC) decides key spots. Only validated numbers/cards reach the
        prompt, the player must be signed by Telegram, and the GPU serves one decision at a time."""
        if jeff is None:
            return JSONResponse({"error": "jeff_off"}, status_code=503)
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
        spot = clean_spot(data.get("spot"))
        if spot is None:
            return JSONResponse({"error": "bad_spot"}, status_code=400)
        now = time.monotonic()
        if now - last_jeff.get(user["id"], -1e9) < JEFF_EVERY_S or jeff_busy.locked():
            return JSONResponse({"error": "busy"}, status_code=429)
        last_jeff[user["id"]] = now
        async with jeff_busy:
            try:
                move = await asyncio.wait_for(jeff(spot), JEFF_TIMEOUT_S)
            except Exception:  # noqa: BLE001 — the game falls back to its local bot
                move = None
        if not move:
            return JSONResponse({"error": "no_answer"}, status_code=504)
        return JSONResponse({"action": move["action"], "amount": move.get("amount"), "by": "Jeff"})

    async def claude_off(_request: Request):
        return JSONResponse({"error": "disabled_on_host", "message": "Платная модель на хосте выключена; "
                             "играет локальный ИИ."}, status_code=503)

    async def health(_request: Request):
        return JSONResponse({"ok": True, "players": store.top("all", 1)[1]})

    async def index(_request: Request):
        return FileResponse(dist / "index.html", headers={"Cache-Control": "no-cache"})

    routes = [Route("/api/leaderboard", leaderboard, methods=["GET", "POST"]),
              Route("/api/session", session_post, methods=["POST"]),
              Route("/api/jeff", jeff_move, methods=["POST"]),
              Route("/api/claude", claude_off, methods=["GET", "POST"]),
              Route("/api/health", health, methods=["GET"]),
              Route("/", index, methods=["GET"]),
              Mount("/", StaticFiles(directory=str(dist), html=True), name="game")]
    return Starlette(routes=routes, middleware=[Middleware(Guard)])
