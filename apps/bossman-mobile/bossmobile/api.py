"""Bossman Mobile gateway: FastAPI app on 127.0.0.1:8840.

/m/            the PWA (static app shell)
/m/api/*       a FIXED allowlist of calls to the owner's Bossman (unknown paths: 404) + local-only chat rule
/panel/*, /*   the full Bossman admin 1:1 (see proxy.py), owner session required

Every Bossman call goes through BossmanClient: Bossman's own permissions, task admission and approvals apply.
No approval is ever decided here without the owner's explicit tap (POST /m/api/approvals/{id}).
"""
from __future__ import annotations

import asyncio
import base64
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
from .auth import COOKIE, CSRF_HEADER, RateLimiter, Sessions, WorkerDeviceRegistry, check_token, load_or_create_pairing
from .bossman import BossmanClient, BossmanUnavailable, Redactor
from .config import Settings
from .policy import is_photo_model, local_agents, pick_default, uncensored_label
from .proxy import proxy_http, proxy_ws
from . import reftags, video, video_timing
from .timing import calibrate_task_eta, from_history, queue_view, step_range, timing_for, warm_models

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


class ExchangeIn(BaseModel):
    text: str = Field(min_length=1, max_length=300)


class WorkerRegisterIn(BaseModel):
    device_id: str = Field(min_length=1, max_length=80, pattern=REQ_ID)
    role: str = Field(default="worker", min_length=1, max_length=60)
    capabilities: list[str] = Field(default_factory=list, max_length=20)


class WorkerHeartbeatIn(BaseModel):
    device_id: str = Field(min_length=1, max_length=80, pattern=REQ_ID)
    active_task_id: str | int | None = None


MAX_REFS = 6  # API ceiling; each model states its own (max_refs), the editors take 3


class PhotoIn(BaseModel):
    model: str = Field(min_length=1, max_length=80)
    prompt: str = Field(min_length=1, max_length=8000)
    negative: str = Field(default="", max_length=8000)
    resolution: str | None = Field(default=None, pattern=r"^\d{2,4}x\d{2,4}$")
    seed: int | None = Field(default=None, ge=0, lt=2**63)
    steps: int | None = Field(default=None, ge=1, le=200)
    image_b64: str | None = Field(default=None, max_length=48 * 1024 * 1024)
    # several reference photos, @1 first; ref_names = optional @names per position ("" = none)
    images_b64: list[str] | None = Field(default=None, max_length=MAX_REFS)
    ref_names: list[str] | None = Field(default=None, max_length=MAX_REFS)
    facelock: bool = False          # put the face of @1 back exactly as in the photo (post-process on the PC)


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
    workers = WorkerDeviceRegistry()
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
    app.state.workers = workers

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
                                    "connect-src 'self'; media-src 'self' blob:; frame-src 'self'; frame-ancestors 'none'; base-uri 'none'; "
                                    "form-action 'self'")
            if p.startswith("/m/api/") or p.endswith("sw.js"):
                resp.headers["Cache-Control"] = "no-store"
            elif p in ("/m/", "/m/index.html") or p.endswith((".js", ".css", ".webmanifest")):
                resp.headers["Cache-Control"] = "no-cache"   # revalidate (ETag): phones get a new shell at once
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

    async def timing_rows() -> list[dict]:
        """Bossman's measured timing log (owner-only endpoint; an older Bossman has none -> [])."""
        async def load():
            body = await guarded_call("GET", "/api/direct-gen/timings?limit=2000")
            rows = body.get("records") if isinstance(body, dict) else None
            return [r for r in rows or [] if isinstance(r, dict)]
        try:
            return await cached("timing_rows", 20, load)
        except Exception:  # noqa: BLE001 - the history and the table still apply
            return []

    async def warm_set():
        st = await cached("dg_status", 15, lambda: guarded_call("GET", "/api/direct-gen/status"))
        return warm_models(st)

    async def photo_models() -> list[dict]:
        async def load():
            body = await call("GET", "/api/direct-gen/models")
            try:
                hist = from_history((await call("GET", "/api/direct-gen/jobs")).get("jobs") or [], await timing_rows())
            except Exception:  # noqa: BLE001 - history is optional; the table of earlier measurements still applies
                hist = {}
            out = []
            for m in body.get("models") or []:
                if not is_photo_model(m):
                    continue
                out.append({k: m.get(k) for k in ("id", "label", "kind", "family", "modes", "available", "reason",
                                                  "requires_image", "accepts_image", "max_refs", "ref_label", "default_steps", "max_steps",
                                                  "default_resolution", "min_side", "max_side", "side_multiple",
                                                  "license", "notes")} | {"uncensored_label": uncensored_label(m.get("label")), **step_range(m),
                                                                   "timing": timing_for(str(m.get("id")), hist)})
            return out
        return await cached("photo_models", 20, load)

    async def history_all() -> dict[str, dict]:
        try:
            jobs = (await call("GET", "/api/direct-gen/jobs")).get("jobs") or []
            return from_history(jobs, await timing_rows()) | video_timing.from_history(jobs)
        except Exception:  # noqa: BLE001 - history is optional; the tables of earlier measurements still apply
            return {}

    async def video_catalog() -> dict:
        async def load():
            body, st, fs, hist = await asyncio.gather(
                call("GET", "/api/direct-gen/models"), guarded_call("GET", "/api/direct-gen/status"),
                guarded_call("GET", "/api/direct-gen/faceswap"), history_all())
            models = [video.model_view(m, st, hist) for m in body.get("models") or []
                      if isinstance(m, dict) and m.get("kind") == "video"]
            models.sort(key=lambda m: (not m["ready"], not m["available"]))
            q = st.get("queue") or {}
            return {"models": models,
                    "faceswap": video.faceswap_view(fs, hist) if fs else None,
                    "gpu": {"busy": bool(q.get("running")), "waiting": q.get("waiting") or 0,
                            "comfyui": (st.get("runtime") or {}).get("reachable"),
                            "memory_free_gb": round((st.get("memory") or {}).get("free_mb", 0) / 1024, 1)
                            if (st.get("memory") or {}).get("measured") else None}}
        return await cached("video_catalog", 15, load)

    async def guarded_call(method: str, path: str) -> dict:
        try:
            body = await call(method, path)
            return body if isinstance(body, dict) else {}
        except HTTPException:
            return {}

    async def vcall(method: str, path: str, **kw) -> Any:
        """Bossman call for the video section: Bossman's error codes come back in Russian (original kept)."""
        try:
            return await call(method, path, **kw)
        except HTTPException as exc:
            d = exc.detail if isinstance(exc.detail, dict) else {}
            code = d.get("code")
            if code in video.ERRORS_RU:
                msg = video.ERRORS_RU[code]
                if code == "model_unavailable" and d.get("message"):
                    msg += ": " + video.reason_ru(d["message"])
                raise HTTPException(exc.status_code, {**d, "message": msg, "bossman": d.get("message")}) from None
            raise

    def job_view(j: dict) -> dict:
        keep = ("job_id", "model", "kind", "status", "stage", "created_at", "started_at", "finished_at", "raw_prompt",
                "negative", "params", "progress", "result", "error", "elapsed_s", "queue_position", "timeline", "inputs",
                "facelock", "result_unlocked")
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
                     "workers": workers.snapshot(),
                     "mobile": {"version": VERSION, "sessions": sessions.count()}})

    # ------------------------------------------------------------------ workers (single-core worker device registry)
    @app.get("/m/api/workers")
    async def list_workers(_=Depends(session)):
        return workers.snapshot()

    @app.post("/m/api/workers/register")
    async def register_worker(body: WorkerRegisterIn, _=Depends(session)):
        return workers.register(body.device_id, role=body.role, capabilities=body.capabilities)

    @app.post("/m/api/workers/heartbeat")
    async def heartbeat_worker(body: WorkerHeartbeatIn, _=Depends(session)):
        try:
            return workers.heartbeat(body.device_id, active_task_id=body.active_task_id)
        except KeyError:
            raise fail(404, "устройство-воркер не зарегистрировано", "BM_WORKER_NOT_FOUND") from None

    @app.post("/m/api/workers/stop")
    async def stop_workers(_=Depends(session)):
        return workers.trigger_global_stop()

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
        workers.trigger_global_stop()
        return await call("POST", f"/api/chat/threads/{_tid(thread_id)}/stop")

    @app.get("/m/api/tasks/{task_id}")
    async def task(task_id: int, _=Depends(session)):
        body = await call("GET", f"/api/tasks/{task_id}")
        t = body.get("task") or {}
        runs = body.get("runs") if isinstance(body.get("runs"), list) else []
        model_alias = str(t.get("model_alias") or (runs[-1].get("model_alias") if runs and isinstance(runs[-1], dict) else "") or "main")
        eta = calibrate_task_eta(runs, model_alias=model_alias)
        return {"id": t.get("id"), "status": t.get("status"), "result": body.get("result"), "error": body.get("error"),
                "agent_id": t.get("agent_id"), "eta": eta}

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

    @app.get("/m/api/photo/queue")
    async def p_queue(_=Depends(session)):
        """Read-only: queue with real step progress (from Bossman) or an honest time estimate, ready for the UI."""
        body = await call("GET", "/api/direct-gen/jobs")
        jobs = body.get("jobs") or []
        try:
            hist = from_history(jobs, await timing_rows())
        except Exception:  # noqa: BLE001 - table of earlier measurements still applies
            hist = {}
        return {"jobs": [job_view(j) | {"ui": j["ui"]} for j in queue_view(jobs, hist, warm=await warm_set())]}

    def check_tags(prompt: str, count: int, names: list[str] | None, accepts: bool) -> tuple[str, ...]:
        """@1/@name must point at an attached photo; refused here, in Russian, before anything leaves the phone."""
        if not accepts:
            return ()
        try:
            clean = reftags.clean_names(names, count)
            reftags.rewrite(prompt, reftags.RefTags(count, clean), "{n}")
        except reftags.RefTagError as exc:
            raise fail(422, str(exc), "BM_UNKNOWN_REF") from None
        return clean

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
        images = list(body.images_b64 or []) or ([body.image_b64] if body.image_b64 else [])
        if body.images_b64 and body.image_b64:
            raise fail(422, "фото: либо одно image_b64, либо список images_b64", "BM_BAD_IMAGE")
        if any(not isinstance(i, str) or not i or len(i) > 48 * 1024 * 1024 for i in images):
            raise fail(422, "фото-референс пустой или больше 36 МБ", "BM_BAD_IMAGE")
        cap = int(spec.get("max_refs") or (1 if spec.get("accepts_image") else 0))
        if len(images) > cap:
            raise fail(422, f"«{spec.get('label')}» берёт не больше {cap} фото-референс(а), выбрано {len(images)}"
                       if cap else f"«{spec.get('label')}» не принимает фото", "BM_TOO_MANY_REFS")
        names = check_tags(body.prompt, len(images), body.ref_names, bool(spec.get("accepts_image")))
        payload: dict[str, Any] = {"model": body.model, "prompt": body.prompt, "negative": body.negative, "mode": "DIRECT"}
        for k in ("resolution", "seed", "steps"):
            v = getattr(body, k)
            if v is not None:
                payload[k] = v
        if len(images) == 1:
            payload["image_b64"] = images[0]           # one photo: the long-standing field
        elif images:
            payload["images_b64"] = images
        if any(names):
            payload["ref_names"] = list(names)
        if body.facelock:
            if not images or not spec.get("accepts_image"):
                raise fail(422, "FaceLock: нужна модель с фото-референсом и приложенное фото (лицо берётся с @1)", "BM_FACELOCK_NEEDS_REF")
            payload["facelock"] = True
        return job_view(await call("POST", "/api/direct-gen/jobs", json=payload, timeout=120))

    @app.get("/m/api/photo/jobs/{job_id}")
    async def p_job(job_id: str, _=Depends(session)):
        return job_view(await call("GET", f"/api/direct-gen/jobs/{_jid(job_id)}"))

    @app.post("/m/api/photo/jobs/{job_id}/cancel")
    async def p_cancel(job_id: str, _=Depends(session)):
        return await call("POST", f"/api/direct-gen/jobs/{_jid(job_id)}/cancel")

    async def to_pult(job_id: str) -> dict:
        """Owner asked for this ONE result in the Пульт: Bossman sends it (the bot token never reaches the phone app)."""
        r = await call("POST", f"/api/direct-gen/jobs/{_jid(job_id)}/send-to-pult", timeout=300)
        return {"ok": True, "sent": bool(isinstance(r, dict) and r.get("sent"))}

    # ------------------------------------------------------------------ exchange desk (owner 19.10)
    def exchange():
        """The SAME assistant and local book as the pult and the CLI: one Q-…/D-… record everywhere."""
        a = getattr(app.state, "exchange", None)
        if a is None:
            from bossman_shared.exchange_assistant import Assistant
            from bossman_shared.exchange_assistant.store import Store as ExchangeBook, default_db
            a = app.state.exchange = Assistant(ExchangeBook(default_db()))
        return a

    @app.post("/m/api/exchange")
    async def exchange_command(body: ExchangeIn, _=Depends(session)):
        try:
            a = exchange()
        except Exception as exc:  # noqa: BLE001 - the rest of the app keeps working
            raise fail(503, "помощник обменника недоступен", "BM_EXCHANGE_UNAVAILABLE", type(exc).__name__) from None
        reply = a.handle(body.text, "owner")
        qid = re.search(r"\bQ-[0-9A-F]{8}\b", reply) if reply.startswith("Котировка") else None
        return {"ok": not reply.startswith(("Не выполнено", "⛔")), "reply": reply,
                "quote_id": qid.group(0) if qid else None, "stopped": a.global_stop()}

    @app.get("/m/api/exchange/deals")
    async def exchange_deals(_=Depends(session)):
        try:
            a = exchange()
        except Exception as exc:  # noqa: BLE001
            raise fail(503, "помощник обменника недоступен", "BM_EXCHANGE_UNAVAILABLE", type(exc).__name__) from None
        keep = ("id", "quote_id", "status", "confirmed_at", "give_ccy", "give_amount", "get_ccy", "get_amount",
                "client_rate", "reversed_at", "reverse_reason")
        return {"deals": [{k: d.get(k) for k in keep} for d in reversed(a.store.deals()[-50:])]}

    @app.post("/m/api/photo/jobs/{job_id}/to-pult")
    async def p_to_pult(job_id: str, _=Depends(session)):
        return await to_pult(job_id)

    @app.post("/m/api/video/jobs/{job_id}/to-pult")
    async def v_to_pult(job_id: str, _=Depends(session)):
        return await to_pult(job_id)

    async def delete_forever(job_id: str) -> dict:
        """Owner's «Удалить навсегда»: Bossman removes the job's files and record from the PC (refused while it runs)."""
        await call("DELETE", f"/api/direct-gen/jobs/{_jid(job_id)}", timeout=60)
        return {"ok": True, "deleted": True}

    @app.post("/m/api/photo/jobs/{job_id}/delete")
    async def p_delete(job_id: str, _=Depends(session)):
        return await delete_forever(job_id)

    @app.post("/m/api/video/jobs/{job_id}/delete")
    async def v_delete(job_id: str, _=Depends(session)):
        return await delete_forever(job_id)

    EXT = {"image/png": "png", "image/jpeg": "jpg", "image/webp": "webp", "video/mp4": "mp4", "video/webm": "webm",
           "video/quicktime": "mov", "video/x-matroska": "mkv", "image/gif": "gif"}

    async def result_file(job_id: str, download: bool, request: Request, variant: str = "") -> StreamingResponse:
        """The finished file of a Bossman job, streamed. Range is passed through: iPhone Safari plays video only with
        206 partial answers (Bossman's FileResponse supports them)."""
        jid = _jid(job_id)
        rng = request.headers.get("range", "")
        hdrs = {"range": rng} if re.fullmatch(r"bytes=\d*-\d*", rng) else None
        try:
            r = await bm.send("GET", f"/api/direct-gen/jobs/{jid}/file",
                              params={"variant": "unlocked"} if variant == "unlocked" else None,
                              headers=hdrs, stream=True, timeout=120)
        except BossmanUnavailable as exc:
            raise fail(502, str(exc), "BOSSMAN_UNAVAILABLE") from None
        if r.status_code not in (200, 206):
            await r.aread()
            await r.aclose()
            raise fail(r.status_code if r.status_code != 416 else 416, "файл недоступен")
        mime = r.headers.get("content-type", "application/octet-stream")
        ext = EXT.get(mime.split(";")[0], "bin")
        disp = "attachment" if download else "inline"
        out = {"Content-Disposition": f'{disp}; filename="bossman-{jid[:8]}.{ext}"', "Cache-Control": "private, max-age=86400",
               "Accept-Ranges": "bytes"}
        for k in ("content-range", "content-length"):
            if r.headers.get(k):
                out[k] = r.headers[k]

        async def gen():
            try:
                async for chunk in r.aiter_bytes():
                    yield chunk
            finally:
                await r.aclose()

        return StreamingResponse(gen(), status_code=r.status_code, media_type=mime, headers=out)

    @app.get("/m/api/photo/jobs/{job_id}/file")
    async def p_file(job_id: str, request: Request, download: bool = False, variant: str = "", _=Depends(session)):
        if variant not in ("", "unlocked"):
            raise fail(422, "variant: пусто или unlocked", "BM_BAD_VARIANT")
        return await result_file(job_id, download, request, variant)

    # ------------------------------------------------------------------ video (Bossman Direct Generator + FaceFusion)
    @app.get("/m/api/video/models")
    async def v_models(_=Depends(session)):
        return await video_catalog()

    @app.get("/m/api/video/queue")
    async def v_queue(_=Depends(session)):
        """Read-only. Every active job of Bossman's ONE GPU queue (photo jobs too: they delay a video) + recent videos."""
        body = await call("GET", "/api/direct-gen/jobs")
        jobs = body.get("jobs") or []
        try:
            hist = from_history(jobs, await timing_rows()) | video_timing.from_history(jobs)
        except Exception:  # noqa: BLE001
            hist = {}
        view = queue_view(jobs, hist, kinds={"photo", "video", "faceswap"}, done_kinds=video_timing.VIDEO_KINDS,
                          warm=await warm_set())
        return {"jobs": [job_view(j) | {"ui": j["ui"]} for j in view]}

    @app.get("/m/api/video/jobs")
    async def v_jobs(_=Depends(session)):
        body = await call("GET", "/api/direct-gen/jobs")
        return {"jobs": [job_view(j) for j in body.get("jobs") or [] if j.get("kind") in video_timing.VIDEO_KINDS]}

    async def read_upload(item: Any, limit: int, what: str) -> bytes | None:
        if item is None or isinstance(item, str):
            return None
        data = bytearray()
        while chunk := await item.read(1 << 20):
            data += chunk
            if len(data) > limit:
                raise fail(413, f"{what}: файл больше {limit // 1048576} МБ", "BM_TOO_LARGE")
        return bytes(data) or None

    async def form_of(request: Request, files: int):
        if not request.headers.get("content-type", "").startswith(("multipart/form-data", "application/x-www-form-urlencoded")):
            raise fail(415, "нужна отправка формы с файлами", "BM_FORM")
        try:
            return await request.form(max_files=files, max_fields=24, max_part_size=64 * 1024)
        except Exception as exc:  # noqa: BLE001 - malformed form from the phone
            raise fail(400, f"не удалось прочитать форму ({type(exc).__name__})", "BM_FORM") from None

    def num(form, key: str, kind=int):
        raw = str(form.get(key) or "").strip()
        if not raw:
            return None
        try:
            return kind(raw)
        except ValueError:
            raise fail(422, f"{key}: нужно число", "BM_BAD_NUMBER") from None

    @app.post("/m/api/video/jobs", status_code=202)
    async def v_create(request: Request, _=Depends(session)):
        form = await form_of(request, 2)
        model_id = str(form.get("model") or "")
        prompt = str(form.get("prompt") or "").strip()
        negative = str(form.get("negative") or "")
        if not 1 <= len(prompt) <= 8000 or len(negative) > 8000:
            raise fail(422, "промпт: от 1 до 8000 символов", "BM_BAD_PROMPT")
        cache.pop("video_catalog", None)               # decide on fresh availability (ComfyUI may have started)
        spec = next((m for m in (await video_catalog())["models"] if m["id"] == model_id), None)
        if spec is None:
            raise fail(404, "такой видео-модели нет в Bossman", "BM_UNKNOWN_VIDEO_MODEL")
        duration, steps, seed = num(form, "duration", float), num(form, "steps"), num(form, "seed")
        if seed is not None and not 0 <= seed < 2**63:
            raise fail(422, "сид должен быть целым неотрицательным числом", "BM_BAD_SEED")
        resolution = str(form.get("resolution") or spec["default_resolution"])
        image = await read_upload(form.get("image"), video.MAX_INPUT, "картинка")
        audio = await read_upload(form.get("audio"), video.MAX_INPUT, "звук")
        if image is not None and video.image_kind(image) is None:
            raise fail(422, "картинка должна быть PNG или JPEG", "BM_BAD_IMAGE")
        check_tags(prompt, 1 if image is not None else 0, None, bool(spec.get("accepts_image")))
        why = video.check_job(spec, resolution=resolution, duration=duration if duration is not None else 1.0,
                              steps=steps, has_image=image is not None, has_audio=audio is not None)
        if why:
            raise fail(422, why, "BM_VIDEO_LIMIT")
        payload: dict[str, Any] = {"model": model_id, "prompt": prompt, "negative": negative, "mode": "DIRECT",
                                   "resolution": resolution, "duration": duration if duration is not None else 1.0}
        if steps is not None:
            payload["steps"] = steps
        if seed is not None:
            payload["seed"] = seed
        if image is not None:
            payload["image_b64"] = base64.b64encode(image).decode()
        if audio is not None:
            payload["audio_b64"] = base64.b64encode(audio).decode()
        return job_view(await vcall("POST", "/api/direct-gen/jobs", json=payload, timeout=300))

    @app.post("/m/api/video/swap", status_code=202)
    async def v_swap(request: Request, _=Depends(session)):
        form = await form_of(request, 1 + video.MAX_FACES)
        cache.pop("video_catalog", None)
        fs = (await video_catalog()).get("faceswap")
        if not fs or not fs["available"]:
            raise fail(409, (fs or {}).get("reason") or "замена лица сейчас недоступна", "BM_FACESWAP_UNAVAILABLE")
        if str(form.get("consent") or "").lower() not in ("1", "true", "yes", "on"):
            raise fail(422, "нужно подтвердить согласие человека на фото", "BM_CONSENT")
        preset, frame = str(form.get("preset") or "fast"), str(form.get("frame") or "original")
        if preset not in {p["id"] for p in fs["presets"]}:
            raise fail(422, "нет такого пресета замены лица", "BM_BAD_PRESET")
        if frame not in {f["id"] for f in fs["frames"]}:
            raise fail(422, "кадр: «как в исходнике» или 16:9", "BM_BAD_FRAME")
        clip = await read_upload(form.get("video"), video.MAX_SWAP_VIDEO, "видео")
        if clip is None:
            raise fail(422, "нужно исходное видео", "BM_NO_VIDEO")
        faces = [f for f in form.getlist("faces") if not isinstance(f, str)]
        if not 1 <= len(faces) <= video.MAX_FACES:
            raise fail(422, f"нужно от 1 до {video.MAX_FACES} фото лица", "BM_FACES")
        photos = [await read_upload(f, video.MAX_INPUT, "фото лица") for f in faces]
        if any(p is None or video.image_kind(p) is None for p in photos):
            raise fail(422, "фото лица должны быть PNG или JPEG", "BM_BAD_FACE")
        payload = {"video_b64": base64.b64encode(clip).decode(), "faces_b64": [base64.b64encode(p).decode() for p in photos],
                   "preset": preset, "frame": frame, "consent": True}
        del clip, photos
        return job_view(await vcall("POST", "/api/direct-gen/swap-jobs", json=payload, timeout=600))

    @app.get("/m/api/video/jobs/{job_id}")
    async def v_job(job_id: str, _=Depends(session)):
        return job_view(await call("GET", f"/api/direct-gen/jobs/{_jid(job_id)}"))

    @app.post("/m/api/video/jobs/{job_id}/cancel")
    async def v_cancel(job_id: str, _=Depends(session)):
        return await vcall("POST", f"/api/direct-gen/jobs/{_jid(job_id)}/cancel")

    @app.get("/m/api/video/jobs/{job_id}/file")
    async def v_file(job_id: str, request: Request, download: bool = False, _=Depends(session)):
        return await result_file(job_id, download, request)

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
