"""Bossman Mobile gateway: FastAPI app on 127.0.0.1:8840.

/m/            the PWA (static app shell)
/m/api/*       a FIXED allowlist of calls to the owner's Bossman (unknown paths: 404) + local-only chat rule
/panel/*, /*   the full Bossman admin 1:1 (see proxy.py), owner session required

Every Bossman call goes through BossmanClient: Bossman's own permissions, task admission and approvals apply.
No approval is ever decided here without the owner's explicit tap (POST /m/api/approvals/{id}).
"""
from __future__ import annotations

import asyncio
import contextlib
import json
import mimetypes
import re
import time
from pathlib import Path
from typing import Any

import httpx
from fastapi import Depends, FastAPI, HTTPException, Request, WebSocket
from fastapi.responses import JSONResponse, RedirectResponse, Response, StreamingResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

from . import VERSION
from .auth import COOKIE, CSRF_HEADER, RateLimiter, Sessions, check_token, load_or_create_pairing
from .bossman import BossmanClient, BossmanUnavailable, Redactor
from .config import Settings
from .policy import is_photo_model, local_agents, pick_default, uncensored_label
from .proxy import proxy_http, proxy_ws
from .timing import from_history, step_range, timing_for

UI_DIR = Path(__file__).parent / "ui"
# The Windows registry can map .js to text/plain, which nosniff then refuses to execute.
mimetypes.add_type("text/javascript", ".js")
mimetypes.add_type("text/css", ".css")
mimetypes.add_type("application/manifest+json", ".webmanifest")
THREAD_ID = re.compile(r"^[0-9A-Za-z_-]{1,64}$")
JOB_ID = re.compile(r"^[0-9a-f]{32}$")
REQ_ID = r"^[A-Za-z0-9._:-]+$"

STREAM_KINDS = {"stream.open", "stream.replayed", "stream.lagged", "run.answer_delta", "run.answer_reset",
                "run.assistant_message", "run.tool_use", "run.tool_result", "approval.created", "approval.decided",
                "task.started", "task.progress", "task.completed", "task.failed", "task.stopped", "task.blocked",
                "task.paused", "task.queued", "tool.denied"}
STREAM_FIELDS = {"kind", "seq", "ts", "task_id", "run_id", "step", "attempt", "idx", "text", "chars", "streamed",
                 "model", "call_id", "tool", "source", "args", "ok", "summary", "preview", "truncated", "duration_ms",
                 "id", "approval_kind", "approve", "status", "error", "reason", "cursor", "after"}
TERMINAL = {"completed", "failed", "stopped", "blocked", "cancelled"}


class LoginIn(BaseModel):
    token: str = Field(min_length=1, max_length=512)


class ThreadIn(BaseModel):
    title: str | None = Field(default=None, max_length=200)


class SendIn(BaseModel):
    text: str = Field(min_length=1, max_length=20000)
    agent_id: int | None = None
    client_request_id: str | None = Field(default=None, min_length=8, max_length=128, pattern=REQ_ID)


class DecideIn(BaseModel):
    approve: bool


class PhotoIn(BaseModel):
    model: str = Field(min_length=1, max_length=80)
    prompt: str = Field(min_length=1, max_length=8000)
    negative: str = Field(default="", max_length=8000)
    resolution: str | None = Field(default=None, pattern=r"^\d{2,4}x\d{2,4}$")
    seed: int | None = Field(default=None, ge=0, lt=2**63)
    steps: int | None = Field(default=None, ge=1, le=200)
    image_b64: str | None = Field(default=None, max_length=48 * 1024 * 1024)


def _client_key(request: Request) -> str:
    host = request.client.host if request.client else "unknown"
    xff = request.headers.get("x-forwarded-for", "").split(",")[0].strip()
    return xff if host in ("127.0.0.1", "::1") and xff else host


def _https(request: Request) -> bool:
    return request.url.scheme == "https" or request.headers.get("x-forwarded-proto", "").lower() == "https"


def create_app(settings: Settings | None = None, *, bossman_transport: httpx.AsyncBaseTransport | None = None,
               ollama_transport: httpx.AsyncBaseTransport | None = None, war_transport: httpx.AsyncBaseTransport | None = None,
               ws_connect=None, pairing_token: str | None = None) -> FastAPI:
    st = settings or Settings()
    st.data_dir.mkdir(parents=True, exist_ok=True)
    pairing = pairing_token or load_or_create_pairing(st.data_dir)
    sessions = Sessions(st.data_dir, st.session_ttl_s)
    limiter = RateLimiter(st.login_failures_per_min, st.login_failures_global_per_min)
    bm = BossmanClient(st.bossman_url, st.token_file, transport=bossman_transport)
    ollama = httpx.AsyncClient(base_url=st.ollama_url, transport=ollama_transport, timeout=8, trust_env=False)
    war = httpx.AsyncClient(base_url=st.war_url, transport=war_transport, timeout=4, trust_env=False)
    cache: dict[str, tuple[float, Any]] = {}

    @contextlib.asynccontextmanager
    async def lifespan(_app):
        yield
        await bm.aclose()
        await ollama.aclose()
        await war.aclose()

    app = FastAPI(title="Bossman Mobile", version=VERSION, docs_url=None, redoc_url=None, openapi_url=None,
                  lifespan=lifespan)
    app.state.settings, app.state.sessions, app.state.bossman, app.state.limiter = st, sessions, bm, limiter

    @app.middleware("http")
    async def _headers(request: Request, call_next):
        resp = await call_next(request)
        p = request.url.path
        if p.startswith("/m/") or p == "/m":
            resp.headers.setdefault("X-Content-Type-Options", "nosniff")
            resp.headers.setdefault("Referrer-Policy", "no-referrer")
            resp.headers.setdefault("X-Frame-Options", "DENY")
            resp.headers.setdefault("Content-Security-Policy",
                                    "default-src 'self'; img-src 'self' data: blob:; style-src 'self'; script-src 'self'; "
                                    "connect-src 'self'; frame-src 'self'; frame-ancestors 'none'; base-uri 'none'; "
                                    "form-action 'self'")
            if p.startswith("/m/api/") or p.endswith("sw.js"):
                resp.headers["Cache-Control"] = "no-store"
        return resp

    # ------------------------------------------------------------------ helpers
    def safe(obj: Any) -> Any:
        """JSON going to the phone, with every Bossman secret value replaced."""
        text = json.dumps(obj, ensure_ascii=False, default=str)
        cleaned = Redactor(bm.secrets()).text(text)
        return json.loads(cleaned) if cleaned != text else obj

    def fail(status: int, message: str, code: str = "", hint: str = "") -> HTTPException:
        body = {"message": message}
        if code:
            body["code"] = code
        if hint:
            body["hint"] = hint
        return HTTPException(status, body)

    async def call(method: str, path: str, **kw) -> Any:
        try:
            status, body = await bm.json(method, path, **kw)
        except BossmanUnavailable as exc:
            raise fail(502, str(exc), "BOSSMAN_UNAVAILABLE", "проверьте, что Bossman запущен на ПК") from None
        if status >= 400:
            err = body.get("error") if isinstance(body, dict) else None
            if isinstance(err, dict):
                raise HTTPException(status, safe(err))
            detail = body.get("detail") if isinstance(body, dict) else None
            raise HTTPException(status, safe(detail if isinstance(detail, dict) else {"message": f"Bossman: HTTP {status}"}))
        return safe(body)

    def session(request: Request) -> dict:
        sid = request.cookies.get(COOKIE)
        row = sessions.get(sid)
        if row is None:
            raise fail(401, "нужен вход", "BM_LOGIN_REQUIRED", "введите код сопряжения")
        if request.method not in ("GET", "HEAD", "OPTIONS"):
            sent = request.headers.get(CSRF_HEADER, "")
            if not check_token(sent, row["csrf"]):
                raise fail(403, "не пройдена CSRF-проверка", "csrf", "перезапустите приложение")
        return {**row, "_sid": sid}

    async def cached(key: str, ttl: float, fn):
        hit = cache.get(key)
        if hit and time.monotonic() - hit[0] < ttl:
            return hit[1]
        val = await fn()
        cache[key] = (time.monotonic(), val)
        return val

    async def agents_view() -> list[dict]:
        async def load():
            opts, agents, models, providers = await asyncio.gather(
                call("GET", "/api/chat/options"), call("GET", "/api/agents"), call("GET", "/api/models"),
                call("GET", "/api/providers"))
            return local_agents(opts, agents, models, providers)
        return await cached("agents", 10, load)

    async def photo_models() -> list[dict]:
        async def load():
            body = await call("GET", "/api/direct-gen/models")
            try:
                hist = from_history((await call("GET", "/api/direct-gen/jobs")).get("jobs") or [])
            except Exception:  # noqa: BLE001 - history is optional; the table of earlier measurements still applies
                hist = {}
            out = []
            for m in body.get("models") or []:
                if not is_photo_model(m):
                    continue
                out.append({k: m.get(k) for k in ("id", "label", "kind", "family", "modes", "available", "reason",
                                                  "requires_image", "accepts_image", "default_steps", "max_steps",
                                                  "default_resolution", "min_side", "max_side", "side_multiple",
                                                  "license", "notes")} | {"uncensored_label": uncensored_label(m.get("label")), **step_range(m),
                                                                   "timing": timing_for(str(m.get("id")), hist)})
            return out
        return await cached("photo_models", 20, load)

    def job_view(j: dict) -> dict:
        keep = ("job_id", "model", "kind", "status", "stage", "created_at", "started_at", "finished_at", "raw_prompt",
                "negative", "params", "progress", "result", "error", "elapsed_s", "queue_position", "timeline", "inputs")
        return {k: j.get(k) for k in keep}

    def stream_event(msg: dict) -> dict | None:
        if isinstance(msg.get("data"), dict) and "kind" in msg:
            msg = {**msg["data"], "kind": msg["kind"], "seq": msg.get("seq", msg.get("id")), "ts": msg.get("ts")}
        if msg.get("kind") not in STREAM_KINDS:
            return None
        return {k: v for k, v in msg.items() if k in STREAM_FIELDS}

    # ------------------------------------------------------------------ public
    @app.get("/m/api/health")
    async def health():
        return {"app": "bossman-mobile", "version": VERSION, "ok": True}

    @app.post("/m/api/login")
    async def login(body: LoginIn, request: Request):
        key = _client_key(request)
        wait = limiter.retry_after(key)
        if wait > 0:
            raise HTTPException(429, {"message": "слишком много попыток входа", "code": "BM_RATE_LIMIT",
                                      "retry_after_s": round(wait)}, headers={"Retry-After": str(int(wait) + 1)})
        if not check_token(body.token, pairing):
            limiter.failure(key)
            raise fail(401, "неверный код сопряжения", "BM_BAD_TOKEN")
        sid, row = sessions.create(label=request.headers.get("user-agent", "")[:80])
        resp = JSONResponse({"ok": True, "csrf": row["csrf"], "csrf_header": CSRF_HEADER})
        resp.set_cookie(COOKIE, sid, max_age=st.session_ttl_s, httponly=True, samesite="strict", secure=_https(request),
                        path="/")
        return resp

    # ------------------------------------------------------------------ session
    @app.get("/m/api/session")
    async def get_session(sess=Depends(session)):
        return {"ok": True, "csrf": sess["csrf"], "csrf_header": CSRF_HEADER, "expires": sess["expires"]}

    @app.post("/m/api/logout")
    async def logout(request: Request, sess=Depends(session)):
        sessions.revoke(sess["_sid"])
        resp = JSONResponse({"ok": True})
        resp.delete_cookie(COOKIE, path="/")
        return resp

    @app.get("/m/api/status")
    async def status(_=Depends(session)):
        async def bossman_live():
            code, body = await bm.public_get("/health/live")
            return {"up": code == 200, **({k: body.get(k) for k in ("build_sha_short", "build_sha", "status", "source_identity")}
                                          if isinstance(body, dict) else {})}

        async def ollama_info():
            try:
                tags = (await ollama.get("/api/tags")).json().get("models", [])
                ps = (await ollama.get("/api/ps")).json().get("models", [])
            except (httpx.HTTPError, ValueError) as exc:
                return {"up": False, "reason": type(exc).__name__, "models": [], "loaded": []}
            return {"up": True, "models": [{"name": m.get("name"), "size": m.get("size"),
                                            "params": (m.get("details") or {}).get("parameter_size")} for m in tags],
                    "loaded": [m.get("name") for m in ps]}

        async def war_info():
            try:
                r = await war.get("/api/v1/health")
                b = r.json()
                return {"up": r.status_code == 200, "version": b.get("version"), "mode": b.get("mode")}
            except (httpx.HTTPError, ValueError) as exc:
                return {"up": False, "reason": type(exc).__name__}

        async def guarded(coro, fallback):
            try:
                return await coro
            except HTTPException as exc:
                return {**fallback, "error": exc.detail}

        live, oll, wt, dg, ap, ags = await asyncio.gather(
            bossman_live(), ollama_info(), war_info(), guarded(call("GET", "/api/direct-gen/status"), {}),
            guarded(call("GET", "/api/approvals", params={"status": "pending"}), {}), guarded(agents_view(), {}))
        return safe({"bossman": live, "ollama": oll, "war_terminal": wt, "direct_gen": dg,
                     "approvals_pending": len(ap) if isinstance(ap, list) else None,
                     "local_agents": len([a for a in ags if a.get("local")]) if isinstance(ags, list) else None,
                     "mobile": {"version": VERSION, "sessions": sessions.count()}})

    # ------------------------------------------------------------------ chat (Bossman chat threads = Bossman tasks)
    @app.get("/m/api/agents")
    async def agents(_=Depends(session)):
        items = await agents_view()
        allowed = [a for a in items if a["local"]]
        return {"items": allowed, "default_agent_id": pick_default(items),
                "hidden_not_local": len(items) - len(allowed),
                "rule": "только локальные модели этого ПК; облачные агенты скрыты и отклоняются сервером"}

    @app.get("/m/api/threads")
    async def threads(_=Depends(session)):
        return await call("GET", "/api/chat/threads", params={"limit": 50})

    @app.post("/m/api/threads", status_code=201)
    async def new_thread(body: ThreadIn, _=Depends(session)):
        return await call("POST", "/api/chat/threads", json={"title": body.title})

    def _tid(thread_id: str) -> str:
        if not THREAD_ID.match(thread_id):
            raise fail(404, "чат не найден")
        return thread_id

    @app.get("/m/api/threads/{thread_id}")
    async def thread(thread_id: str, _=Depends(session)):
        return await call("GET", f"/api/chat/threads/{_tid(thread_id)}")

    @app.post("/m/api/threads/{thread_id}/send")
    async def send(thread_id: str, body: SendIn, _=Depends(session)):
        tid = _tid(thread_id)
        if body.agent_id is None:
            raise fail(403, "Auto отключён в мобильном чате: он может выбрать облачную модель. Выберите локальную модель.",
                       "BM_LOCAL_ONLY")
        cache.pop("agents", None)                 # decide on fresh data, not a 10 s old list
        items = await agents_view()
        agent = next((a for a in items if a["id"] == body.agent_id), None)
        if agent is None or not agent["local"]:
            why = ", ".join(agent["why_not"]) if agent else "агент не найден"
            raise fail(403, f"Отклонено: мобильный чат работает только на локальных моделях ({why}).", "BM_LOCAL_ONLY")
        payload = {"text": body.text, "agent_id": body.agent_id}
        if body.client_request_id:
            payload["client_request_id"] = body.client_request_id
        return await call("POST", f"/api/chat/threads/{tid}/send", json=payload, timeout=120)

    @app.post("/m/api/threads/{thread_id}/stop")
    async def stop(thread_id: str, _=Depends(session)):
        return await call("POST", f"/api/chat/threads/{_tid(thread_id)}/stop")

    @app.get("/m/api/tasks/{task_id}")
    async def task(task_id: int, _=Depends(session)):
        body = await call("GET", f"/api/tasks/{task_id}")
        t = body.get("task") or {}
        return {"id": t.get("id"), "status": t.get("status"), "result": body.get("result"), "error": body.get("error"),
                "agent_id": t.get("agent_id")}

    @app.get("/m/api/tasks/{task_id}/history")
    async def task_history(task_id: int, _=Depends(session)):
        body = await call("GET", f"/api/tasks/{task_id}/events", params={"after": 0, "limit": 500})
        events = [e for e in (stream_event(m) for m in body.get("events") or []) if e]
        return {"task_id": task_id, "status": body.get("status"), "events": events}

    @app.get("/m/api/tasks/{task_id}/events")
    async def task_events(task_id: int, request: Request, after: int = 0, _=Depends(session)):
        try:
            r = await bm.send("GET", "/api/events/stream", params={"task_id": task_id, "after": max(0, after)},
                              headers={"accept": "text/event-stream"}, stream=True, timeout=None)
        except BossmanUnavailable as exc:
            raise fail(502, str(exc), "BOSSMAN_UNAVAILABLE") from None
        if r.status_code != 200:
            await r.aread()
            await r.aclose()
            raise fail(r.status_code, f"Bossman: поток событий недоступен (HTTP {r.status_code})")
        red = Redactor(bm.secrets())

        async def gen():
            data_lines: list[str] = []
            try:
                yield "retry: 3000\n\n"
                async for line in r.aiter_lines():
                    if line.startswith(":"):
                        yield ": keepalive\n\n"
                        continue
                    if line.startswith("data:"):
                        data_lines.append(line[5:].lstrip())
                        continue
                    if line.strip() or not data_lines:
                        continue
                    raw, data_lines = "\n".join(data_lines), []
                    try:
                        msg = json.loads(red.text(raw))
                    except ValueError:
                        continue
                    ev = stream_event(msg) if isinstance(msg, dict) else None
                    if ev is None:
                        continue
                    head = f"id: {ev['seq']}\n" if isinstance(ev.get("seq"), int) else ""
                    yield head + "data: " + json.dumps(ev, ensure_ascii=False, default=str) + "\n\n"
            finally:
                await r.aclose()

        return StreamingResponse(gen(), media_type="text/event-stream",
                                 headers={"Cache-Control": "no-store", "X-Accel-Buffering": "no"})

    # ------------------------------------------------------------------ approvals (owner's tap only)
    @app.get("/m/api/approvals")
    async def approvals(_=Depends(session)):
        rows = await call("GET", "/api/approvals", params={"status": "pending"})
        keep = ("id", "kind", "preview", "task_id", "run_id", "status", "created_at")
        return {"items": [{k: r.get(k) for k in keep} for r in rows if isinstance(r, dict)]}

    @app.post("/m/api/approvals/{approval_id}")
    async def decide(approval_id: int, body: DecideIn, _=Depends(session)):
        row = await call("POST", f"/api/approvals/{approval_id}", json={"approve": body.approve, "by": "owner"})
        return {k: row.get(k) for k in ("id", "status", "kind", "task_id", "decided_at", "decided_by")} if isinstance(row, dict) else row

    # ------------------------------------------------------------------ photo (Bossman Direct Generator)
    @app.get("/m/api/photo/models")
    async def p_models(_=Depends(session)):
        return {"models": await photo_models()}

    @app.get("/m/api/photo/status")
    async def p_status(_=Depends(session)):
        return await call("GET", "/api/direct-gen/status")

    @app.get("/m/api/photo/jobs")
    async def p_jobs(_=Depends(session)):
        body = await call("GET", "/api/direct-gen/jobs")
        return {"jobs": [job_view(j) for j in body.get("jobs") or [] if j.get("kind") == "photo"]}

    def _jid(job_id: str) -> str:
        if not JOB_ID.match(job_id):
            raise fail(404, "задача генерации не найдена")
        return job_id

    @app.post("/m/api/photo/jobs", status_code=202)
    async def p_create(body: PhotoIn, _=Depends(session)):
        models = {m["id"]: m for m in await photo_models()}
        spec = models.get(body.model)
        if spec is None:
            raise fail(404, "такой фото-модели нет в Bossman", "BM_UNKNOWN_PHOTO_MODEL")
        top = int(spec.get("max_steps") or 0)
        if body.steps is not None and top and not 1 <= body.steps <= top:
            raise fail(422, f"для этой модели шагов может быть от 1 до {top}", "BM_BAD_STEPS")
        payload: dict[str, Any] = {"model": body.model, "prompt": body.prompt, "negative": body.negative, "mode": "DIRECT"}
        for k in ("resolution", "seed", "steps", "image_b64"):
            v = getattr(body, k)
            if v is not None:
                payload[k] = v
        return job_view(await call("POST", "/api/direct-gen/jobs", json=payload, timeout=120))

    @app.get("/m/api/photo/jobs/{job_id}")
    async def p_job(job_id: str, _=Depends(session)):
        return job_view(await call("GET", f"/api/direct-gen/jobs/{_jid(job_id)}"))

    @app.post("/m/api/photo/jobs/{job_id}/cancel")
    async def p_cancel(job_id: str, _=Depends(session)):
        return await call("POST", f"/api/direct-gen/jobs/{_jid(job_id)}/cancel")

    @app.get("/m/api/photo/jobs/{job_id}/file")
    async def p_file(job_id: str, download: bool = False, _=Depends(session)):
        jid = _jid(job_id)
        try:
            r = await bm.send("GET", f"/api/direct-gen/jobs/{jid}/file", stream=True, timeout=120)
        except BossmanUnavailable as exc:
            raise fail(502, str(exc), "BOSSMAN_UNAVAILABLE") from None
        if r.status_code != 200:
            await r.aread()
            await r.aclose()
            raise fail(r.status_code, "файл недоступен")
        mime = r.headers.get("content-type", "application/octet-stream")
        ext = {"image/png": "png", "image/jpeg": "jpg", "image/webp": "webp", "video/mp4": "mp4"}.get(mime.split(";")[0], "bin")
        disp = "attachment" if download else "inline"

        async def gen():
            try:
                async for chunk in r.aiter_bytes():
                    yield chunk
            finally:
                await r.aclose()

        return StreamingResponse(gen(), media_type=mime, headers={
            "Content-Disposition": f'{disp}; filename="bossman-{jid[:8]}.{ext}"', "Cache-Control": "private, max-age=86400"})

    @app.api_route("/m/api/{rest:path}", methods=["GET", "POST", "PUT", "PATCH", "DELETE"])
    async def m_api_unknown(rest: str):
        raise fail(404, "нет такого вызова", "BM_NOT_ALLOWED")

    # ------------------------------------------------------------------ app shell
    @app.get("/m", include_in_schema=False)
    async def m_root():
        return RedirectResponse("/m/", status_code=307)

    if UI_DIR.exists():
        app.mount("/m", StaticFiles(directory=str(UI_DIR), html=True), name="m")

    @app.get("/", include_in_schema=False)
    async def root():
        return RedirectResponse("/m/", status_code=307)

    # ------------------------------------------------------------------ panel: Bossman 1:1
    async def end_session_for(request: Request):
        sessions.revoke(request.cookies.get(COOKIE))

    def panel_session(request: Request) -> dict:
        row = sessions.get(request.cookies.get(COOKIE))
        if row is None:
            raise fail(401, "нужен вход в Bossman Mobile", "BM_LOGIN_REQUIRED")
        return row

    @app.get("/panel", include_in_schema=False)
    async def panel_root():
        return RedirectResponse("/panel/", status_code=307)

    METHODS = ["GET", "POST", "PUT", "PATCH", "DELETE", "HEAD", "OPTIONS"]

    @app.api_route("/panel/{path:path}", methods=METHODS, include_in_schema=False)
    async def panel(path: str, request: Request):
        sess = panel_session(request)
        return await proxy_http(request, path, "/panel", bm, sess, lambda: end_session_for(request))

    @app.websocket("/panel/{path:path}")
    async def panel_ws(ws: WebSocket, path: str):
        await _ws(ws, path)

    async def _ws(ws: WebSocket, path: str):
        if sessions.get(ws.cookies.get(COOKIE)) is None:
            await ws.close(code=4401)
            return
        origin = ws.headers.get("origin")
        host = ws.headers.get("host", "")
        if origin is not None and not origin.lower().endswith("://" + host.lower()):
            await ws.close(code=4403)
            return
        await proxy_ws(ws, path, bm, ws_connect)

    @app.api_route("/{path:path}", methods=METHODS, include_in_schema=False)
    async def passthrough(path: str, request: Request):
        sess = panel_session(request)
        return await proxy_http(request, path, "", bm, sess, lambda: end_session_for(request))

    @app.websocket("/{path:path}")
    async def passthrough_ws(ws: WebSocket, path: str):
        await _ws(ws, path)

    return app
