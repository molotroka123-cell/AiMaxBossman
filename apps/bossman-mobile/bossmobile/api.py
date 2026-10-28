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
import io
import json
import mimetypes
import re
import secrets
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Literal

import httpx
from fastapi import Depends, FastAPI, HTTPException, Request, WebSocket
from fastapi.responses import JSONResponse, RedirectResponse, Response, StreamingResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, ConfigDict, Field

from . import VERSION
from .auth import COOKIE, CSRF_HEADER, RateLimiter, Sessions, WorkerDeviceRegistry, check_token, load_or_create_pairing
from .bossman import BossmanClient, BossmanUnavailable, Redactor
from .config import Settings
from .policy import is_photo_model, local_agents, loopback_url, pick_default, uncensored_label
from .proxy import proxy_http, proxy_ws
from . import niche, reftags, slash, speech as speech_mod, video, video_timing
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
                "run.token_delta", "run.assistant_message", "run.tool_use", "run.tool_result", "approval.created", "approval.decided",
                "task.started", "task.progress", "task.completed", "task.failed", "task.stopped", "task.blocked",
                "task.paused", "task.queued", "tool.denied"}
STREAM_FIELDS = {"kind", "seq", "ts", "task_id", "run_id", "step", "attempt", "idx", "text", "chars", "streamed",
                 "delta", "token", "model", "call_id", "tool", "source", "args", "ok", "summary", "preview", "truncated", "duration_ms",
                 "id", "approval_kind", "approve", "status", "error", "reason", "cursor", "after"}
TERMINAL = {"completed", "failed", "stopped", "blocked", "cancelled"}

PRIVACY_BLACKLIST = (
    "1password", "bitwarden", "keepass", "lastpass", "enpass", "dashlane",
    "bank", "tinkoff", "sberbank", "alfa", "vtb", "binance", "metamask",
    "wallet", "keychain", "credential", "auth",
)


class LoginIn(BaseModel):
    token: str = Field(min_length=1, max_length=512)


class ThreadIn(BaseModel):
    title: str | None = Field(default=None, max_length=200)


class DeleteForeverIn(BaseModel):
    confirm: str = Field(min_length=1, max_length=64)


CHAT_ATTACH_MAX_BYTES = 8 * 1024 * 1024
CHAT_ATTACH_SUFFIXES = (
    ".txt", ".md", ".markdown", ".json", ".jsonl", ".yaml", ".yml", ".toml", ".ini", ".cfg",
    ".csv", ".tsv", ".log", ".py", ".js", ".mjs", ".ts", ".tsx", ".jsx", ".html", ".css",
    ".sql", ".xml", ".sh", ".ps1", ".bat", ".pdf", ".docx",
    ".png", ".jpg", ".jpeg", ".webp", ".gif",
)


class SendIn(BaseModel):
    text: str = Field(min_length=1, max_length=20000)
    agent_id: int | None = None
    client_request_id: str | None = Field(default=None, min_length=8, max_length=128, pattern=REQ_ID)
    attachments: list[str] = Field(default_factory=list, max_length=8)


AGENT_MODES = ("CHAT", "AGENT", "AUTO", "CODE", "COMPUTER", "SELF-IMPROVE")


class AgentMessageIn(BaseModel):
    """A chat message sent in an OpenCode mode chip (live audit 25.10: the chips only PATCHed the session while the text
    still went to /threads/send, so CODE / PC / /improve changed nothing). Strict: no worker, model, cloud switch or
    repo paths from the phone; the agent must pass the same local-only check as /threads/send."""
    model_config = ConfigDict(extra="forbid")
    text: str = Field(min_length=1, max_length=20000)
    mode: Literal["CHAT", "AGENT", "AUTO", "CODE", "COMPUTER", "SELF-IMPROVE"]
    agent_id: int
    client_request_id: str | None = Field(default=None, min_length=8, max_length=128, pattern=REQ_ID)
    attachments: list[str] = Field(default_factory=list, max_length=8)


# Repo root of the checkout this pult runs from (apps/bossman-mobile/bossmobile/api.py -> repo). The web terminal used
# roots[0] of Bossman's allowed roots as cwd - on the owner PC that is a Python Lib folder, so «git status» answered
# «not a git repository» and the pytest preset found no file (live audit 25.10).
REPO_ROOT = Path(__file__).resolve().parents[3]

# Web terminal: pipes, no TTY. An interactive program waits for input forever and Kill is the only way out.
_TERM_SPLIT = re.compile(r"&&|\|\||[;&|\n]")
_TERM_ALWAYS_INTERACTIVE = {"vim", "vi", "nvim", "nano", "emacs", "less", "more", "top", "htop", "btop", "ssh",
                            "telnet", "ftp", "sftp", "mysql", "psql", "mongo", "mongosh", "redis-cli", "tmux", "screen",
                            "watch", "edit", "notepad"}
_TERM_REPL_WITHOUT_ARGS = {"python", "python3", "py", "node", "ipython", "bash", "sh", "zsh", "fish", "cmd",
                           "powershell", "pwsh", "irb", "ghci", "lua", "sqlite3", "deno", "bun", "R", "julia"}


def _prog_name(tok: str) -> str:
    name = re.split(r"[\\/]", tok.strip("'\""))[-1]
    for ext in (".exe", ".cmd", ".bat", ".ps1"):
        if name.lower().endswith(ext):
            name = name[: -len(ext)]
    return name


def terminal_refusal(command: str) -> str | None:
    """Why the web terminal must not start this command, or None. Decision for `claude` recorded in
    docs/audits/2026-10-25-mobile-every-function/three_model_claude_terminal.json (option A): every claude invocation is
    refused - it is an interactive TUI here and cloud agents get no PC access through the terminal."""
    for seg in _TERM_SPLIT.split(command or ""):
        toks = seg.strip().split()
        while toks and re.match(r"^[A-Za-z_][A-Za-z0-9_]*=", toks[0]):
            toks = toks[1:]                                  # FOO=bar cmd
        if toks and toks[0].lower() in ("npx", "pnpx", "bunx") and len(toks) > 1:
            toks = toks[1:]
        if not toks:
            continue
        prog = _prog_name(toks[0])
        low = prog.lower()
        if low in ("claude", "claude-code") or "anthropic-ai/claude-code" in seg:
            return ("`claude` в веб-терминале не запускается: это интерактивная программа (нужен TTY), а облачным "
                    "агентам доступ к ПК через терминал закрыт. Спросите Claude во вкладке «Чат».")
        if low in _TERM_ALWAYS_INTERACTIVE:
            return (f"Интерактивные программы в веб-терминале не поддерживаются (нет TTY): `{prog}` ждал бы ввода "
                    "бесконечно. Запустите команду с аргументами, которая завершается сама.")
        if (low in _TERM_REPL_WITHOUT_ARGS or prog in _TERM_REPL_WITHOUT_ARGS) and len(toks) == 1:
            return (f"`{prog}` без аргументов открывает интерактивную оболочку, а в веб-терминале нет TTY. "
                    f"Используйте, например, `{prog} -V`, `{prog} --version` или `{prog} файл`.")
        if low == "ollama" and len(toks) == 3 and toks[1] == "run":
            return "`ollama run <модель>` без вопроса открывает интерактивный чат. Добавьте вопрос в кавычках."
    return None


class ChatAttachmentIn(BaseModel):
    model_config = ConfigDict(extra="forbid")
    name: str = Field(min_length=1, max_length=200)
    data_b64: str = Field(min_length=1, max_length=(CHAT_ATTACH_MAX_BYTES * 4) // 3 + 64)
    thread_id: str | None = Field(default=None, max_length=64)


class DecideIn(BaseModel):
    approve: bool


class StopAllIn(BaseModel):
    """POST /m/api/stop: the owner's explicit «СТОП всё» (confirm must be true; nothing else is forwarded)."""
    model_config = ConfigDict(extra="forbid")
    confirm: Literal[True]


class CompactIn(BaseModel):
    """POST /m/api/agent/sessions/{id}/compact: an optional hint for the summary; nothing else is forwarded."""
    model_config = ConfigDict(extra="forbid")
    instruction: str | None = Field(default=None, max_length=2000)


class MemoryIn(BaseModel):
    """POST /m/api/agent/sessions/{id}/memory: the owner's optional note to the common memory entry."""
    model_config = ConfigDict(extra="forbid")
    note: str | None = Field(default=None, max_length=2000)


class UnloadIn(BaseModel):
    agent_id: int


class ExchangeIn(BaseModel):
    text: str = Field(min_length=1, max_length=300)


class WorkerRegisterIn(BaseModel):
    device_id: str = Field(min_length=1, max_length=80, pattern=REQ_ID)
    role: str = Field(default="worker", min_length=1, max_length=60)
    capabilities: list[str] = Field(default_factory=list, max_length=20)


class WorkerHeartbeatIn(BaseModel):
    device_id: str = Field(min_length=1, max_length=80, pattern=REQ_ID)
    active_task_id: str | int | None = None


class CallCredentialsIn(BaseModel):
    api_id: int = Field(gt=0, lt=2**31)
    api_hash: str = Field(min_length=32, max_length=32)


class CallLoginStartIn(BaseModel):
    phone: str = Field(min_length=5, max_length=32)


class CallLoginCodeIn(BaseModel):
    code: str = Field(min_length=3, max_length=16)


class CallLoginPasswordIn(BaseModel):
    password: str = Field(min_length=1, max_length=256)


class CallPeerIn(BaseModel):
    user_id: int | None = Field(default=None, gt=0, lt=2**52)
    username: str | None = Field(default=None, min_length=1, max_length=40)
    confirm: bool = False


class CallDialIn(BaseModel):
    confirm_unknown: bool = False


class CallSelftestIn(BaseModel):
    scenario: str = Field(default="all", min_length=1, max_length=32)


class CallSettingsIn(BaseModel):
    enabled: bool | None = None
    max_call_s: int | None = None
    ring_timeout_s: int | None = None
    idle_prompt_s: int | None = None
    idle_hangup_s: int | None = None
    barge_in: bool | None = None
    echo_mode: str | None = None
    vad: str | None = None
    greeting: str | None = Field(default=None, max_length=200)
    auto_save_to_bossman_memory: bool | None = None
    answering_machine: bool | None = None
    answer_ring_delay_s: int | None = None
    answer_max_call_s: int | None = None
    answer_allow_unknown: bool | None = None
    answer_greeting: str | None = Field(default=None, max_length=240)


MAX_REFS = 6  # API ceiling; each model states its own (max_refs), the editors take 3


class PhotoAssistIn(BaseModel):
    prompt: str = Field(min_length=1, max_length=8000)


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
    assist: bool = False            # on-demand Gemma prompt refinement -> photo job -> immediate LLM unload


JEFF_KB_MAX_BYTES = 8 * 1024 * 1024        # same ceiling as Bossman's /api/jeff-kb/manual
JEFF_KB_SUFFIXES = (".txt", ".md", ".pdf", ".docx")


class JeffManualIn(BaseModel):
    name: str = Field(min_length=1, max_length=200)
    data_b64: str = Field(min_length=1, max_length=(JEFF_KB_MAX_BYTES * 4) // 3 + 16)


class JeffModeIn(BaseModel):
    strict: bool | None = None
    scope_all: bool | None = None
    # owner 21.10: free cloud answers by the manual while the local model is unavailable (sections go to the provider)
    cloud_fallback: bool | None = None
    # owner 22.10 «пусть уберёт отсылки»: keep the «(§N)» marks in Jeff's answers (default OFF)
    show_citations: bool | None = None
    local_timeout_s: int | None = Field(default=None, ge=10, le=300)
    cloud_daily_max: int | None = Field(default=None, ge=0, le=1000)


class JeffCheckIn(BaseModel):
    question: str = Field(min_length=1, max_length=2000)
    use_manual: bool = True


class JeffModelIn(BaseModel):
    model: str = Field(min_length=1, max_length=120, pattern=r"^[A-Za-z0-9][A-Za-z0-9._:/\-]{0,119}$")


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
                "negative", "params", "progress", "progress_detail", "result", "error", "elapsed_s", "queue_position", "timeline", "inputs",
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
        return {"app": "bossman-mobile", "version": VERSION, "ok": True, "status": "ok"}

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
            if code != 200:
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

        live, oll, wt, dg, ap, ags, pca, hib = await asyncio.gather(
            bossman_live(), ollama_info(), war_info(), guarded(call("GET", "/api/direct-gen/status"), {}),
            guarded(call("GET", "/api/approvals", params={"status": "pending"}), {}), guarded(agents_view(), {}),
            guarded(call("GET", "/api/pc-agent/status"), {}),
            guarded(call("GET", "/api/agent/hibernation/status"), {}))
        strata_agents = [
            a for a in (ags if isinstance(ags, list) else [])
            if any(k in f"{a.get('name') or ''} {(a.get('model') or {}).get('alias') or ''} {(a.get('model') or {}).get('name') or ''}".lower()
                   for k in ("strata", "qwen3.8-flash-next"))
        ]
        strata_up = any((a.get("model") or {}).get("health") in ("online", "healthy", "ready") or a.get("allowed") for a in strata_agents)
        return safe({"bossman": live, "ollama": oll, "war_terminal": wt, "direct_gen": dg,
                     "strata": {"up": strata_up, "model": "qwen3.8-flash-next-iq3_s", "endpoint": "http://127.0.0.1:8090/v1",
                                "agents": [a.get("name") for a in strata_agents if a.get("allowed")]},
                     "pc_agent": pca if isinstance(pca, dict) else {},
                     "hibernation": hib if isinstance(hib, dict) else {},
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

    # ------------------------------------------------------------------ global STOP visibility (owner 20.10)
    def stop_view() -> dict:
        """<bcc data>/computer/STOP or <bcc data>/pc-agent/STOP: while either exists computer actions and exchange writes are refused. Read-only here."""
        pc_stop_file = st.stop_file.parent.parent / "pc-agent" / "STOP"
        mtimes: list[float] = []
        for candidate in (st.stop_file, pc_stop_file):
            try:
                mtimes.append(candidate.stat().st_mtime)
            except FileNotFoundError:
                continue
            except OSError:                   # cannot tell -> say it is on (same fail-closed rule as the exchange)
                return {"active": True, "since": None, "age_min": None, "unknown": True}
        if not mtimes:
            return {"active": False, "since": None, "age_min": None}
        mtime = min(mtimes)
        since = datetime.fromtimestamp(mtime, timezone.utc).isoformat(timespec="seconds")
        return {"active": True, "since": since, "age_min": max(0, int((time.time() - mtime) // 60))}

    @app.get("/m/api/stop")
    async def stop_state(_=Depends(session)):
        return stop_view()

    @app.post("/m/api/stop/resume")
    async def stop_resume(_=Depends(session)):
        """«Продолжить» = the owner's existing resume in Bossman (POST /api/computer/resume); never a file delete here."""
        body = await call("POST", "/api/computer/resume", json={})
        view = stop_view()
        if view["active"]:
            raise fail(409, "Bossman ответил, но STOP всё ещё включён", "BM_STOP_STILL_ACTIVE",
                       "проверьте Bossman на ПК (/resume в пульте)")
        workers.clear_global_stop()          # function probe 24.10: the worker-registry STOP stayed set until a pult restart
        return {"ok": True, **view, "generation": body.get("generation") if isinstance(body, dict) else None}

    # ------------------------------------------------------------------ chat (Bossman chat threads = Bossman tasks)
    @app.get("/m/api/agents")
    async def agents(_=Depends(session)):
        items = await agents_view()
        allowed = [a for a in items if a["allowed"]]
        return {"items": allowed, "default_agent_id": pick_default(items),
                "hidden_not_local": len(items) - len(allowed),
                "rule": "локальные модели этого ПК; облачные только те, которым владелец явно разрешил мобильный доступ "
                        "(без фото и файлов); остальные облачные скрыты и отклоняются сервером"}

    def _is_eval_thread(item: Any) -> bool:
        if not isinstance(item, dict):
            return False
        title = str(item.get("title") or "").strip().upper()
        return title.startswith("EVAL500 ") or title.startswith("EVAL500-")

    @app.get("/m/api/threads")
    async def threads(include_eval: bool = False, _=Depends(session)):
        res = await call("GET", "/api/chat/threads", params={"limit": 200 if not include_eval else 50})
        if not include_eval and isinstance(res, dict) and isinstance(res.get("items"), list):
            filtered = [t for t in res["items"] if not _is_eval_thread(t)][:50]
            return {**res, "items": filtered}
        return res

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

    async def mobile_agent_or_fail(agent_id: int | None, attachments: list[str]) -> dict:
        """The phone's chat rule, shared by /threads/send and the mode-chip route: only agents allowed for mobile."""
        if agent_id is None:
            raise fail(403, "Auto отключён в мобильном чате: он может выбрать облачную модель. Выберите локальную модель.",
                       "BM_LOCAL_ONLY")
        cache.pop("agents", None)                 # decide on fresh data, not a 10 s old list
        items = await agents_view()
        agent = next((a for a in items if a["id"] == agent_id), None)
        if agent is None or not agent["allowed"]:
            why = ", ".join(agent["why_not"]) if agent else "агент не найден"
            raise fail(403, f"Отклонено: мобильный чат работает только на локальных моделях ({why}).", "BM_LOCAL_ONLY")
        if not agent["local"] and attachments:
            raise fail(403, "Облачной модели нельзя отправлять фото и файлы с телефона: выберите локальную модель.",
                       "BM_CLOUD_NO_ATTACH")
        return agent

    @app.post("/m/api/threads/{thread_id}/send")
    async def send(thread_id: str, body: SendIn, _=Depends(session)):
        tid = _tid(thread_id)
        await mobile_agent_or_fail(body.agent_id, body.attachments)
        payload: dict[str, Any] = {"text": body.text, "agent_id": body.agent_id}
        if body.client_request_id:
            payload["client_request_id"] = body.client_request_id
        if body.attachments:
            payload["attachments"] = [str(a)[:64] for a in body.attachments[:8] if str(a).strip()]
        return await call("POST", f"/api/chat/threads/{tid}/send", json=payload, timeout=120)

    @app.post("/m/api/speech/transcribe")
    async def speech_transcribe(request: Request, language: str = "auto", _=Depends(session)):
        """Push-to-talk: the phone sends one recording (WebM/Opus or MP4/AAC as MediaRecorder made it); the pult converts it
        to WAV with the host's FFmpeg and Bossman's LOCAL Whisper returns the text. Nothing is stored, no cloud."""
        if language != "auto" and not re.fullmatch(r"[a-z]{2,3}", language):
            raise fail(422, "язык: auto или двух-трёхбуквенный код", "BM_SPEECH_LANG")
        length = request.headers.get("content-length")
        if length is not None and length.isdigit() and int(length) > speech_mod.MAX_BYTES:
            raise fail(413, f"запись больше {speech_mod.MAX_BYTES // 1048576} МБ", "BM_SPEECH_TOO_LARGE")
        raw = bytearray()
        async for chunk in request.stream():
            raw.extend(chunk)
            if len(raw) > speech_mod.MAX_BYTES:
                raise fail(413, f"запись больше {speech_mod.MAX_BYTES // 1048576} МБ", "BM_SPEECH_TOO_LARGE")
        try:
            wav = await speech_mod.to_wav16k(bytes(raw))
        except speech_mod.SpeechInputError as exc:
            raise fail(exc.status, exc.message, exc.code) from None
        res = await call("POST", "/api/oss/speech/transcribe", params={"language": language}, content=wav,
                         headers={"Content-Type": "audio/wav"}, timeout=180)
        return {"ok": True, "text": str(res.get("text") or "").strip(), "language": res.get("language"),
                "duration_s": res.get("duration_seconds")}

    @app.post("/m/api/chat/attachments", status_code=201)
    async def chat_attachment_upload(body: ChatAttachmentIn, _=Depends(session)):
        """Upload a file from the phone so the local chat model can study its content."""
        low = body.name.strip().lower()
        if not low.endswith(CHAT_ATTACH_SUFFIXES):
            raise fail(422, "поддерживаются текстовые файлы, код, .pdf, .docx и изображения (.txt, .md, .py, .json, .csv, .pdf, .docx…)",
                       "BM_CHAT_ATTACH_TYPE")
        try:
            raw_bytes = base64.b64decode(body.data_b64, validate=True)
        except ValueError:
            raise fail(422, "файл повреждён при передаче", "BM_CHAT_ATTACH_B64") from None
        if not raw_bytes:
            raise fail(422, "файл пустой", "BM_CHAT_ATTACH_EMPTY")
        if len(raw_bytes) > CHAT_ATTACH_MAX_BYTES:
            raise fail(413, f"файл больше {CHAT_ATTACH_MAX_BYTES // 1048576} МБ", "BM_TOO_LARGE")
        tid = _tid(body.thread_id) if body.thread_id else None
        payload: dict[str, Any] = {"name": body.name.strip(), "data_b64": body.data_b64}
        if tid:
            payload["thread_id"] = tid
        res = await call("POST", "/api/chat/attachments", json=payload, timeout=120)
        att = res.get("attachment") if isinstance(res, dict) and isinstance(res.get("attachment"), dict) else (res if isinstance(res, dict) else {})
        keep = ("id", "name", "kind", "mime", "size", "sha256", "text_chars", "created_at")
        return {"ok": True, "attachment": {k: att.get(k) for k in keep if k in att}}

    @app.post("/m/api/chat/unload")
    async def chat_unload(body: UnloadIn, _=Depends(session)):
        """Owner tapped «Запустить» under a photo prompt the chat model wrote: free that model's RAM/VRAM for the image
        model first. The same Ollama call the resource governor and the pult prompt refiner use (keep_alive=0), sent by
        the gateway's one Ollama client, only to a loopback Ollama, only for the agent's OWN local model and only when it
        is loaded right now (nothing else is touched; the next chat message loads it again)."""
        if not loopback_url(st.ollama_url):
            raise fail(503, "Ollama не на этом ПК — выгрузка модели отключена", "BM_OLLAMA_NOT_LOCAL")
        cache.pop("agents", None)
        agent = next((a for a in await agents_view() if a["id"] == body.agent_id), None)
        if agent is None or not agent["local"]:
            raise fail(403, "выгрузить можно только локальную модель чата этого ПК", "BM_LOCAL_ONLY")
        norm = lambda n: n[:-7] if n.endswith(":latest") else n  # noqa: E731 - «gemma3» == «gemma3:latest» in Ollama
        model = agent.get("model") or {}
        want = {norm(str(n)) for n in (model.get("name"), model.get("alias")) if n}
        try:
            ps = (await ollama.get("/api/ps")).json().get("models", [])
        except (httpx.HTTPError, ValueError, AttributeError):
            raise fail(502, "Ollama не отвечает — модель чата не выгружена", "BM_OLLAMA_UNAVAILABLE") from None
        loaded = [str(m.get("name")) for m in ps if isinstance(m, dict) and m.get("name")]
        unloaded = []
        for name in (n for n in loaded if norm(n) in want):
            try:
                r = await ollama.post("/api/generate", json={"model": name, "keep_alive": 0}, timeout=30)
            except httpx.HTTPError:
                raise fail(502, f"Ollama не ответил на выгрузку {name}", "BM_OLLAMA_UNAVAILABLE") from None
            if r.status_code != 200:
                raise fail(502, f"Ollama не выгрузил {name} (HTTP {r.status_code})", "BM_OLLAMA_UNLOAD_FAILED")
            unloaded.append(name)
        return {"ok": True, "unloaded": unloaded, "was_loaded": bool(unloaded)}

    @app.post("/m/api/threads/{thread_id}/stop")
    async def stop(thread_id: str, _=Depends(session)):
        workers.trigger_global_stop()
        return await call("POST", f"/api/chat/threads/{_tid(thread_id)}/stop")

    # -- long-press actions (owner 22.10): encrypted archive and delete forever. Bossman does the work; this is a proxy.
    @app.post("/m/api/threads/{thread_id}/archive-encrypted")
    async def archive_encrypted(thread_id: str, _=Depends(session)):
        return await call("POST", f"/api/chat/threads/{_tid(thread_id)}/archive-encrypted", timeout=60)

    @app.get("/m/api/archive")
    async def archive_list(_=Depends(session)):
        return await call("GET", "/api/chat/archive")

    @app.post("/m/api/archive/{thread_id}/restore")
    async def archive_restore(thread_id: str, _=Depends(session)):
        return await call("POST", f"/api/chat/archive/{_tid(thread_id)}/restore", timeout=60)

    @app.delete("/m/api/archive/{thread_id}")
    async def archive_delete(thread_id: str, _=Depends(session)):
        return await call("DELETE", f"/api/chat/archive/{_tid(thread_id)}")

    @app.post("/m/api/threads/{thread_id}/delete-forever")
    async def thread_delete_forever(thread_id: str, body: DeleteForeverIn, _=Depends(session)):
        """Typed confirmation: the last 6 characters of the chat id (or the whole id). Bossman is asked for the purge
        with confirm=<id> and the mobile surface header, so it re-checks on its side."""
        tid = _tid(thread_id)
        if body.confirm.strip() not in (tid[-6:], tid):
            raise fail(422, "введите последние 6 символов id чата", "BM_CONFIRM_REQUIRED")
        return await call("DELETE", f"/api/chat/threads/{tid}", params={"purge": 1, "confirm": tid},
                          headers={"X-Bossman-Surface": "mobile"}, timeout=60)

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

    # ------------------------------------------------------------------ global STOP from the phone (parity 24.10)
    def _plane_counts(d: Any) -> dict:
        return {str(k): len(v) for k, v in d.items() if isinstance(v, list)} if isinstance(d, dict) else {}

    @app.get("/m/api/stop/active")
    async def stop_active(_=Depends(session)):
        """What the owner's STOP would stop now: the same preview as the dashboard (/api/control-plane/active),
        counts per plane only."""
        body = await call("GET", "/api/control-plane/active")
        body = body if isinstance(body, dict) else {}
        return {"count": body.get("count"), "planes": _plane_counts(body.get("active")),
                "errors": len(body.get("errors") or []), "stop": stop_view()}

    @app.post("/m/api/stop")
    async def stop_all(body: StopAllIn, _=Depends(session)):
        """«СТОП всё» = the dashboard's owner STOP (POST /api/control-plane/stop-all): durable computer STOP first, then
        every execution plane. Needs an explicit confirm=true; «Продолжить» is POST /m/api/stop/resume."""
        res = await call("POST", "/api/control-plane/stop-all", json={}, timeout=120)
        res = res if isinstance(res, dict) else {}
        workers.trigger_global_stop()
        return {"ok": res.get("ok") is True, "stopped": _plane_counts(res.get("stopped")),
                "requested": _plane_counts(res.get("requested")), "remaining": _plane_counts(res.get("remaining")),
                "errors": len(res.get("errors") or []), "provider_outcome_unknown": len(res.get("provider_outcome_unknown") or []),
                "stop": stop_view()}

    # ------------------------------------------------------------------ Studio orders (read-only on the phone)
    STUDIO_CLIENT = re.compile(r"^[0-9a-f]{64}$")

    def order_view(o: dict) -> dict:
        rc = o.get("receipt") if isinstance(o.get("receipt"), dict) else {}
        tl = o.get("timeline") if isinstance(o.get("timeline"), list) else []
        return {"order_id": o.get("order_id"), "status": o.get("status"), "created_at": o.get("created_at"),
                "updated_at": o.get("updated_at"), "items": len(o.get("items") or []),
                "currency": rc.get("currency"), "price": rc.get("price"), "cost": rc.get("cost"),
                "margin": rc.get("margin"), "cost_complete": rc.get("cost_complete"),
                "licence_warnings": len(o.get("licence_warnings") or []),
                "last_change": tl[-1] if tl and isinstance(tl[-1], dict) else None}

    def studio_params(client: str | None) -> dict | None:
        if client is None:
            return None
        if not STUDIO_CLIENT.match(client):
            raise fail(422, "неверный ключ клиента", "BM_STUDIO_CLIENT")
        return {"client": client}

    @app.get("/m/api/studio/orders")
    async def studio_orders(client: str | None = None, _=Depends(session)):
        """Studio orders (Direct Gen jobs sold as a service): status, price, cost, margin. Briefs, notes and client
        data are not sent to the phone; every change of an order stays in the dashboard (Панель)."""
        body = await call("GET", "/api/studio/orders", params=studio_params(client))
        rows = [order_view(o) for o in (body.get("orders") or []) if isinstance(o, dict)] if isinstance(body, dict) else []
        by_status: dict[str, int] = {}
        for r in rows:
            by_status[str(r["status"])] = by_status.get(str(r["status"]), 0) + 1
        rows.sort(key=lambda r: str(r.get("updated_at") or ""), reverse=True)
        return {"orders": rows, "total": len(rows), "by_status": by_status}

    @app.get("/m/api/studio/orders/{order_id}")
    async def studio_order(order_id: str, client: str | None = None, _=Depends(session)):
        if not re.fullmatch(r"[0-9a-f]{32}", order_id):
            raise fail(404, "заказ не найден", "BM_STUDIO_ORDER")
        o = await call("GET", f"/api/studio/orders/{order_id}", params=studio_params(client))
        return order_view(o if isinstance(o, dict) else {})

    # ------------------------------------------------------------------ niche research run (read-only counters)
    @app.get("/m/api/niche/status")
    async def niche_status(_=Depends(session)):
        return await asyncio.to_thread(niche.status, st.niche_dir)

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

    async def unload_ollama(extra_model: str | None = None) -> list[str]:
        """Unload any loaded Ollama models (keep_alive=0) so VRAM is free before photo/video generation."""
        targets: list[str] = [extra_model] if extra_model else []
        try:
            r = await ollama.get("/api/ps")
            if r.status_code == 200:
                for m in (r.json().get("models") or []):
                    n = str(m.get("name") or m.get("model") or "").strip()
                    if n and n not in targets:
                        targets.append(n)
        except Exception:  # noqa: BLE001
            pass
        unloaded: list[str] = []
        for n in targets:
            try:
                r = await ollama.post("/api/generate", json={"model": n, "prompt": "", "keep_alive": 0})
                if r.status_code < 400:
                    unloaded.append(n)
            except Exception:  # noqa: BLE001
                pass
        return unloaded

    @app.post("/m/api/photo/assist")
    async def p_assist(body: PhotoAssistIn, _=Depends(session)):
        res = await call("POST", "/api/direct-gen/assist", json={"prompt": body.prompt}, timeout=180)
        unloaded = await unload_ollama(res.get("model") if isinstance(res, dict) else None)
        return {**(res if isinstance(res, dict) else {}), "ollama_unloaded": unloaded}

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
        prompt_text = body.prompt
        assist_model = None
        if body.assist:
            assisted = await call("POST", "/api/direct-gen/assist", json={"prompt": body.prompt}, timeout=180)
            if isinstance(assisted, dict) and assisted.get("suggested"):
                prompt_text = str(assisted["suggested"])
                assist_model = assisted.get("model")
        unloaded = await unload_ollama(assist_model)
        payload: dict[str, Any] = {"model": body.model, "prompt": prompt_text, "negative": body.negative, "mode": "DIRECT"}
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
        view = job_view(await call("POST", "/api/direct-gen/jobs", json=payload, timeout=120))
        if assist_model:
            view["assist_model"] = assist_model
        if unloaded:
            view["ollama_unloaded"] = unloaded
        return view

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
            from bossman_shared.exchange_assistant.agent import default_stop_file
            from bossman_shared.exchange_assistant.store import Store as ExchangeBook, default_db
            stop_path = (st.token_file.parent / "computer" / "STOP") if st.token_file else default_stop_file()
            a = app.state.exchange = Assistant(ExchangeBook(default_db(), stop_file=stop_path), stop_file=stop_path)
        return a

    @app.post("/m/api/exchange")
    async def exchange_command(body: ExchangeIn, _=Depends(session)):
        try:
            a = exchange()
        except Exception as exc:  # noqa: BLE001 - the rest of the app keeps working
            raise fail(503, "помощник обменника недоступен", "BM_EXCHANGE_UNAVAILABLE", type(exc).__name__) from None
        reply = a.handle(body.text, "owner")
        qid = re.search(r"\bQ-[0-9A-F]{8}\b", reply) if reply.startswith("Котировка") else None
        # «Не понял команду» is not a success either (function probe 24.10: it answered ok:true)
        return {"ok": not reply.startswith(("Не выполнено", "⛔", "Не понял")), "reply": reply,
                "quote_id": qid.group(0) if qid else None, "stopped": a.global_stop()}

    @app.get("/m/api/exchange/deals")
    async def exchange_deals(_=Depends(session)):
        try:
            a = exchange()
        except Exception as exc:  # noqa: BLE001
            raise fail(503, "помощник обменника недоступен", "BM_EXCHANGE_UNAVAILABLE", type(exc).__name__) from None
        keep = ("id", "quote_id", "status", "confirmed_at", "give_ccy", "give_amount", "get_ccy", "get_amount",
                "client_rate", "reversed_at", "reverse_reason")
        return {"deals": [({k: d.get(k) for k in keep} | {"pdf_url": f"/m/api/exchange/deals/{d['id']}/pdf"})
                          for d in reversed(a.store.deals()[-50:])]}

    @app.get("/m/api/exchange/deals/{deal_id}/pdf")
    async def exchange_deal_pdf(deal_id: int, _=Depends(session)):
        try:
            a = exchange()
        except Exception as exc:  # noqa: BLE001
            raise fail(503, "помощник обменника недоступен", "BM_EXCHANGE_UNAVAILABLE", type(exc).__name__) from None
        try:
            fname, pdf_bytes = a.deal_pdf(deal_id)
        except Exception as exc:  # noqa: BLE001
            raise fail(404, str(exc), "BM_DEAL_NOT_FOUND") from None
        return Response(
            content=pdf_bytes,
            media_type="application/pdf",
            headers={"Content-Disposition": f'inline; filename="{fname}"', "Cache-Control": "no-store"},
        )

    # ------------------------------------------------------------------ telegram calls (owner 20.10)
    @app.get("/m/api/calls/status")
    async def calls_status(_=Depends(session)):
        return await call("GET", "/api/telegram/calls/status")

    @app.get("/m/api/calls/settings")
    async def calls_get_settings(_=Depends(session)):
        return await call("GET", "/api/telegram/calls/settings")

    @app.put("/m/api/calls/settings")
    async def calls_put_settings(body: CallSettingsIn, _=Depends(session)):
        payload = {k: getattr(body, k) for k in body.model_fields_set if getattr(body, k) is not None}
        return await call("PUT", "/api/telegram/calls/settings", json=payload)

    @app.post("/m/api/calls/credentials")
    async def calls_credentials(body: CallCredentialsIn, _=Depends(session)):
        return await call("POST", "/api/telegram/calls/credentials",
                          json={"api_id": body.api_id, "api_hash": body.api_hash})

    @app.post("/m/api/calls/login/start")
    async def calls_login_start(body: CallLoginStartIn, _=Depends(session)):
        return await call("POST", "/api/telegram/calls/login/start", json={"phone": body.phone}, timeout=65)

    @app.post("/m/api/calls/login/code")
    async def calls_login_code(body: CallLoginCodeIn, _=Depends(session)):
        return await call("POST", "/api/telegram/calls/login/code", json={"code": body.code}, timeout=65)

    @app.post("/m/api/calls/login/password")
    async def calls_login_password(body: CallLoginPasswordIn, _=Depends(session)):
        return await call("POST", "/api/telegram/calls/login/password", json={"password": body.password}, timeout=65)

    @app.post("/m/api/calls/logout")
    async def calls_logout(_=Depends(session)):
        return await call("POST", "/api/telegram/calls/logout", timeout=35)

    @app.get("/m/api/calls/contacts")
    async def calls_contacts(q: str = "", _=Depends(session)):
        return await call("GET", "/api/telegram/calls/contacts", params={"q": q[:60]}, timeout=65)

    @app.put("/m/api/calls/peer")
    async def calls_put_peer(body: CallPeerIn, _=Depends(session)):
        payload: dict[str, Any] = {"confirm": body.confirm}
        if body.user_id is not None:
            payload["user_id"] = body.user_id
        if body.username is not None:
            payload["username"] = body.username
        return await call("PUT", "/api/telegram/calls/peer", json=payload, timeout=50)

    @app.delete("/m/api/calls/peer")
    async def calls_delete_peer(_=Depends(session)):
        return await call("DELETE", "/api/telegram/calls/peer")

    @app.post("/m/api/calls/call")
    async def calls_dial(body: CallDialIn | None = None, _=Depends(session)):
        return await call("POST", "/api/telegram/calls/call",
                          json={"confirm_unknown": bool(body and body.confirm_unknown)}, timeout=95)

    @app.post("/m/api/calls/hangup")
    async def calls_hangup(_=Depends(session)):
        return await call("POST", "/api/telegram/calls/hangup", timeout=15)

    @app.post("/m/api/calls/stop")
    async def calls_stop(_=Depends(session)):
        workers.trigger_global_stop()
        return await call("POST", "/api/telegram/calls/stop", timeout=15)

    @app.post("/m/api/calls/resume")
    async def calls_resume(_=Depends(session)):
        return await call("POST", "/api/telegram/calls/resume", timeout=10)

    @app.get("/m/api/calls/history")
    async def calls_history(limit: int = 20, _=Depends(session)):
        return await call("GET", "/api/telegram/calls/history", params={"limit": min(max(1, limit), 50)})

    @app.get("/m/api/calls/answering")
    async def calls_answering(_=Depends(session)):
        return await call("GET", "/api/telegram/calls/answering")

    @app.post("/m/api/calls/answering/start")
    async def calls_answering_start(_=Depends(session)):
        return await call("POST", "/api/telegram/calls/answering/start", timeout=95)

    @app.post("/m/api/calls/answering/stop")
    async def calls_answering_stop(_=Depends(session)):
        return await call("POST", "/api/telegram/calls/answering/stop", timeout=20)

    @app.get("/m/api/calls/answering/reports")
    async def calls_answering_reports(limit: int = 20, _=Depends(session)):
        return await call("GET", "/api/telegram/calls/answering/reports", params={"limit": min(max(1, limit), 50)})

    @app.post("/m/api/calls/selftest")
    async def calls_selftest(body: CallSelftestIn | None = None, _=Depends(session)):
        return await call("POST", "/api/telegram/calls/selftest",
                          json={"scenario": body.scenario if body else "all"}, timeout=430)

    @app.post("/m/api/calls/doctor")
    async def calls_doctor(_=Depends(session)):
        return await call("POST", "/api/telegram/calls/doctor", json={}, timeout=30)

    # ------------------------------------------------------------------ Jeff · обучение (owner 21.10)
    # The owner's manual («методичка») and Jeff's local model. The file goes straight to Bossman (encrypted there);
    # this gateway keeps nothing and logs nothing of it.
    @app.get("/m/api/jeff-kb")
    async def jeff_kb_state(_=Depends(session)):
        return await call("GET", "/api/jeff-kb")

    @app.get("/m/api/jeff-kb/sections")
    async def jeff_kb_sections(_=Depends(session)):
        return await call("GET", "/api/jeff-kb/sections")

    @app.put("/m/api/jeff-kb/manual")
    async def jeff_kb_upload(body: JeffManualIn, _=Depends(session)):
        if not body.name.lower().endswith(JEFF_KB_SUFFIXES):
            raise fail(422, "методичка — файл .txt, .md или .pdf", "BM_JEFF_KB_TYPE")
        try:
            size = len(base64.b64decode(body.data_b64, validate=True))
        except ValueError:
            raise fail(422, "файл повреждён при передаче", "BM_JEFF_KB_B64") from None
        if size > JEFF_KB_MAX_BYTES:
            raise fail(413, f"методичка больше {JEFF_KB_MAX_BYTES // 1048576} МБ", "BM_TOO_LARGE")
        return await call("PUT", "/api/jeff-kb/manual", json={"name": body.name, "data_b64": body.data_b64},
                          timeout=120)

    @app.delete("/m/api/jeff-kb/manual")
    async def jeff_kb_delete(_=Depends(session)):
        return await call("DELETE", "/api/jeff-kb/manual")

    @app.put("/m/api/jeff-kb/mode")
    async def jeff_kb_mode(body: JeffModeIn, _=Depends(session)):
        return await call("PUT", "/api/jeff-kb/mode", json=body.model_dump(exclude_none=True))

    @app.post("/m/api/jeff-kb/check")
    async def jeff_kb_check(body: JeffCheckIn, _=Depends(session)):
        return await call("POST", "/api/jeff-kb/check", json=body.model_dump(), timeout=240)

    @app.put("/m/api/jeff-kb/model")
    async def jeff_model_put(body: JeffModelIn, _=Depends(session)):
        return await call("PUT", "/api/jeff-model", json={"model": body.model})

    @app.delete("/m/api/jeff-kb/model")
    async def jeff_model_delete(_=Depends(session)):
        return await call("DELETE", "/api/jeff-model")

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
        if str(form.get("facelock") or "").lower() in ("1", "true", "yes", "on"):
            # Same rule as photo: the face is taken only from the picture attached to THIS job (@1 = start picture).
            if image is None or not spec.get("accepts_image"):
                raise fail(422, "FaceLock: нужна модель со стартовой картинкой и приложенное фото (лицо берётся с @1)",
                           "BM_FACELOCK_NEEDS_REF")
            payload["facelock"] = True
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

    # ------------------------------------------------------------------ mobile OpenCode Agent, Terminal & Computer Use
    @app.get("/m/api/agent/tools")
    async def m_agent_tools(_=Depends(session)):
        return await call("GET", "/api/agent/tools")

    @app.get("/m/api/agent/models")
    async def m_agent_models(_=Depends(session)):
        return await call("GET", "/api/agent/models")

    @app.post("/m/api/agent/router/select")
    async def m_agent_router_select(request: Request, _=Depends(session)):
        body = await request.json()
        return await call("POST", "/api/agent/router/select", json=body)

    @app.get("/m/api/agent/sessions")
    async def m_agent_sessions(q: str | None = None, archived: bool = False, limit: int = 100, include_eval: bool = False, _=Depends(session)):
        params: dict[str, Any] = {"archived": str(archived).lower(), "limit": max(limit, 200) if not include_eval else limit}
        if q:
            params["q"] = q
        res = await call("GET", "/api/agent/sessions", params=params)
        if not include_eval and isinstance(res, dict) and isinstance(res.get("items"), list):
            filtered = [s for s in res["items"] if not _is_eval_thread(s)][:limit]
            return {**res, "items": filtered, "total": len(filtered)}
        return res

    @app.get("/m/api/agent/hibernation/status")
    async def m_agent_hibernation_status(_=Depends(session)):
        return await call("GET", "/api/agent/hibernation/status")

    @app.post("/m/api/agent/sessions/{session_id}/hibernate")
    async def m_agent_session_hibernate(session_id: str, reason: str = "waiting_external", _=Depends(session)):
        return await call("POST", f"/api/agent/sessions/{_tid(session_id)}/hibernate", params={"reason": reason})

    @app.post("/m/api/agent/sessions/{session_id}/wake")
    async def m_agent_session_wake(session_id: str, _=Depends(session)):
        return await call("POST", f"/api/agent/sessions/{_tid(session_id)}/wake")

    @app.get("/m/api/pc-agent/status")
    async def m_pc_agent_status(_=Depends(session)):
        return await call("GET", "/api/pc-agent/status")

    @app.get("/m/api/pc-agent/tasks")
    async def m_pc_agent_tasks(limit: int = 20, _=Depends(session)):
        return await call("GET", "/api/pc-agent/tasks", params={"limit": limit})

    @app.post("/m/api/pc-agent/tasks")
    async def m_pc_agent_create_task(request: Request, _=Depends(session)):
        body = await request.json()
        return await call("POST", "/api/pc-agent/tasks", json=body, timeout=180)

    @app.get("/m/api/pc-agent/windows")
    async def m_pc_agent_windows(_=Depends(session)):
        st_w, body_w = await bm.json("GET", "/api/pc-agent/windows")
        if st_w == 200 and isinstance(body_w, dict):
            return safe(body_w)
        # Fallback when :8801 is running an un-restarted tournament process: enumerate visible Win32 windows locally
        items: list[dict[str, Any]] = []
        try:
            import ctypes
            user32 = ctypes.windll.user32
            WNDENUMPROC = ctypes.WINFUNCTYPE(ctypes.c_bool, ctypes.c_void_p, ctypes.c_void_p)
            def _cb(hwnd, _lparam):
                if user32.IsWindowVisible(hwnd):
                    length = user32.GetWindowTextLengthW(hwnd)
                    if length > 0:
                        buf = ctypes.create_unicode_buffer(length + 1)
                        user32.GetWindowTextW(hwnd, buf, length + 1)
                        title = buf.value.strip()
                        if title:
                            items.append({"hwnd": int(hwnd), "title": title})
                return True
            user32.EnumWindows(WNDENUMPROC(_cb), 0)
        except Exception:
            pass
        return safe({"ok": True, "count": len(items), "windows": items[:40]})

    @app.get("/m/api/pc-agent/sysinfo")
    async def m_pc_agent_sysinfo(_=Depends(session)):
        st_s, body_s = await bm.json("GET", "/api/pc-agent/sysinfo")
        if st_s == 200 and isinstance(body_s, dict):
            return safe(body_s)
        st_m, body_m = await bm.json("GET", "/api/system/metrics")
        if st_m == 200 and isinstance(body_m, dict):
            return safe({"ok": True, **body_m})
        import os, platform, shutil
        du = shutil.disk_usage(str(REPO_ROOT))
        return safe({
            "ok": True,
            "os": f"{platform.system()} {platform.release()}",
            "cpu_count": os.cpu_count() or 1,
            "disk_free_gb": round(du.free / (1024 ** 3), 1),
            "disk_total_gb": round(du.total / (1024 ** 3), 1),
        })

    require_owner = session
    require_csrf = session

    # The five routes below used to be facades (audit 28.10, «функции доказаны, а не обёрнуты»): a 1x1 blank PNG as «the screen»,
    # an invented element in the tap preview, a fabricated transcript and «выполняю» for any voice input, «rolled_back» on every
    # failure, and «network_used: false, $0, no files» for a route Bossman does not have. Now each one does the real thing or
    # says plainly that it cannot.

    def _foreground_title() -> str:
        try:
            import ctypes
            user32 = ctypes.windll.user32
            hwnd = user32.GetForegroundWindow()
            n = user32.GetWindowTextLengthW(hwnd)
            buf = ctypes.create_unicode_buffer(n + 1)
            user32.GetWindowTextW(hwnd, buf, n + 1)
            return buf.value.strip()
        except Exception:
            return ""

    def _blacklisted_window() -> str | None:
        """Title of the first visible window that matches PRIVACY_BLACKLIST, else None."""
        try:
            import ctypes
            user32 = ctypes.windll.user32
            found: list[str] = []
            WNDENUMPROC = ctypes.WINFUNCTYPE(ctypes.c_bool, ctypes.c_void_p, ctypes.c_void_p)

            def _cb(hwnd, _lparam):
                if user32.IsWindowVisible(hwnd):
                    length = user32.GetWindowTextLengthW(hwnd)
                    if length > 0:
                        buf = ctypes.create_unicode_buffer(length + 1)
                        user32.GetWindowTextW(hwnd, buf, length + 1)
                        title = buf.value.strip()
                        if any(bl in title.lower() for bl in PRIVACY_BLACKLIST):
                            found.append(title)
                            return False
                return True
            user32.EnumWindows(WNDENUMPROC(_cb), 0)
            return found[0] if found else None
        except Exception:
            return None

    def _grab_screen() -> tuple[bytes, int, int, int, int]:
        """(jpeg, frame_w, frame_h, screen_w, screen_h) of the primary screen; raises RuntimeError when it cannot be captured."""
        try:
            from PIL import ImageGrab
            img = ImageGrab.grab().convert("RGB")
        except Exception as exc:
            raise RuntimeError(f"снимок экрана не получен ({type(exc).__name__})") from None
        sw, sh = img.size
        if sw > 1280:
            img = img.resize((1280, max(1, round(sh * 1280 / sw))))
        buf = io.BytesIO()
        img.save(buf, "JPEG", quality=70)
        return buf.getvalue(), img.size[0], img.size[1], sw, sh

    @app.get("/m/api/pc/screen-stream")
    async def m_pc_screen_stream(_=Depends(require_owner)):
        """One REAL frame of the PC screen (JPEG, at most 1280 px wide). Any visible window on PRIVACY_BLACKLIST blocks the
        frame entirely. If the screen cannot be captured the answer says so: never a blank picture dressed up as a frame."""
        culprit = await asyncio.to_thread(_blacklisted_window)
        if culprit:
            return safe({"ok": True, "frame_base64": "", "privacy_filtered": True,
                         "privacy_reason": f"blacklisted_app: {culprit}", "touch_to_act_enabled": False,
                         "timestamp": time.time()})
        try:
            jpeg, fw, fh, sw, sh = await asyncio.to_thread(_grab_screen)
        except RuntimeError as exc:
            raise fail(503, str(exc), "BM_SCREEN_UNAVAILABLE",
                       "экран недоступен: сеанс заблокирован или нет интерактивного рабочего стола") from None
        return safe({"ok": True, "frame_base64": base64.b64encode(jpeg).decode("ascii"), "mime": "image/jpeg",
                     "privacy_filtered": False, "privacy_reason": None,
                     "active_window": await asyncio.to_thread(_foreground_title),
                     "frame_width": fw, "frame_height": fh, "width": sw, "height": sh, "scale_factor": round(sw / fw, 4),
                     "coordinates_space": {"min_x": 0, "max_x": sw, "min_y": 0, "max_y": sh},
                     # a tap can be previewed, but nothing executes it from the phone yet
                     "touch_to_act_enabled": False, "timestamp": time.time()})

    @app.post("/m/api/pc/touch-preview")
    async def m_pc_touch_preview(request: Request, _=Depends(require_csrf)):
        """Preview of a tap at {x, y}. It does NOT find the element under the finger (no UIA lookup here): `element` is only
        what the client sent, and `element_resolved` says so. Nothing is executed."""
        try:
            body = await request.json()
        except Exception:
            raise fail(422, "тело запроса должно быть JSON", "BM_BAD_REQUEST")
        if not isinstance(body, dict):
            raise fail(422, "тело запроса должно быть JSON-объектом", "BM_BAD_REQUEST")
        if "x" not in body or "y" not in body:
            raise fail(422, "требуются координаты x и y", "BM_MISSING_COORDINATES")
        try:
            x = float(body["x"])
            y = float(body["y"])
        except (ValueError, TypeError):
            raise fail(422, "координаты x и y должны быть числами", "BM_INVALID_COORDINATES")
        element = str(body.get("element") or "").strip() or None
        return safe({"ok": True, "tier": "T2",
                     "action_preview": f"Нажатие в точке ({x:.0f}, {y:.0f})" + (f" по элементу «{element}»" if element else ""),
                     "requires_approval": True, "executed": False,
                     "target": {"x": x, "y": y, "element": element, "element_resolved": False},
                     "timestamp": time.time()})

    @app.post("/m/api/voice/turn")
    async def m_voice_turn(request: Request, _=Depends(require_csrf)):
        """Voice input: `audio_b64` is transcribed by the LOCAL Whisper (the chain of /m/api/speech/transcribe); `text` passes
        through. It never starts a task and never invents a reply or an execution: `executed` is False and the owner sends
        the text to the chat."""
        try:
            body = await request.json()
        except Exception:
            raise fail(422, "тело запроса должно быть JSON", "BM_BAD_REQUEST")
        if not isinstance(body, dict):
            raise fail(422, "тело запроса должно быть JSON-объектом", "BM_BAD_REQUEST")
        transcript = str(body.get("text") or body.get("transcript") or "").strip()
        audio_b64 = str(body.get("audio_b64") or "")
        if not transcript and not audio_b64:
            raise fail(422, "требуется text или audio_b64", "BM_MISSING_VOICE_INPUT")
        if not transcript:
            try:
                raw = base64.b64decode(audio_b64, validate=True)
            except ValueError:
                raise fail(422, "аудио повреждено при передаче", "BM_VOICE_B64") from None
            try:
                wav = await speech_mod.to_wav16k(raw)
            except speech_mod.SpeechInputError as exc:
                raise fail(exc.status, exc.message, exc.code) from None
            res = await call("POST", "/api/oss/speech/transcribe", params={"language": "auto"}, content=wav,
                             headers={"Content-Type": "audio/wav"}, timeout=180)
            transcript = str(res.get("text") or "").strip()
        if not transcript:
            raise fail(422, "речь не распознана", "BM_VOICE_EMPTY")
        return safe({"ok": True, "transcript": transcript, "reply": "", "task_id": None, "executed": False,
                     "mode": "push_to_talk", "note": "поручение не запущено: отправьте текст в чат",
                     "created_at": time.time()})

    @app.post("/m/api/tasks/{task_id}/rollback")
    async def m_task_rollback(task_id: str, _=Depends(require_csrf)):
        """Roll back the files a PC-agent task changed, through Bossman's real endpoint. Its answer (including a refusal or
        an error) is passed through untouched: this route never says «rolled back» by itself."""
        if not re.fullmatch(r"[0-9]{1,12}", task_id):
            raise fail(422, "id задачи агента — число", "BM_BAD_TASK_ID")
        return await call("POST", f"/api/pc-agent/tasks/{task_id}/rollback", timeout=60)

    @app.get("/m/api/tasks/{task_id}/blast-radius")
    async def m_task_blast_radius(task_id: str, _=Depends(require_owner)):
        """What a task changed, as far as Bossman knows. Bossman has no such route today: until it has one this answers 501
        instead of «no files, no network, $0», which nobody measured."""
        if not re.fullmatch(r"[0-9]{1,12}", task_id):
            raise fail(422, "id задачи агента — число", "BM_BAD_TASK_ID")
        st, body = await bm.json("GET", f"/api/tasks/{task_id}/blast-radius")
        if st == 200 and isinstance(body, dict):
            return safe(body)
        raise fail(501, "Bossman не отдаёт blast-radius задачи: что она изменила, неизвестно", "BM_BLAST_UNAVAILABLE",
                   "смотрите журнал pc-agent или откатите задачу кнопкой отката")

    @app.post("/m/api/agent/sessions", status_code=201)
    async def m_agent_session_create(request: Request, _=Depends(session)):
        body = await request.json()
        return await call("POST", "/api/agent/sessions", json=body)

    @app.get("/m/api/agent/sessions/{session_id}")
    async def m_agent_session_get(session_id: str, _=Depends(session)):
        return await call("GET", f"/api/agent/sessions/{_tid(session_id)}")

    @app.patch("/m/api/agent/sessions/{session_id}")
    async def m_agent_session_patch(session_id: str, request: Request, _=Depends(session)):
        body = await request.json()
        return await call("PATCH", f"/api/agent/sessions/{_tid(session_id)}", json=body)

    @app.post("/m/api/agent/sessions/{session_id}/messages")
    async def m_agent_session_messages(session_id: str, body: AgentMessageIn, _=Depends(session)):
        tid = _tid(session_id)
        await mobile_agent_or_fail(body.agent_id, body.attachments)
        payload: dict[str, Any] = {"text": body.text, "mode": body.mode, "agent_id": body.agent_id,
                                   "allow_cloud": False}
        if body.client_request_id:
            payload["client_request_id"] = body.client_request_id
        if body.attachments:
            payload["attachments"] = [str(a)[:64] for a in body.attachments[:8] if str(a).strip()]
        return await call("POST", f"/api/agent/sessions/{tid}/messages", json=payload, timeout=180)

    @app.get("/m/api/agent/sessions/{session_id}/cost")
    async def m_agent_session_cost(session_id: str, _=Depends(session)):
        """Numbers for the «$» badge only (no chat text): the badge was a static «$0.00» (live audit 25.10)."""
        d = await call("GET", f"/api/agent/sessions/{_tid(session_id)}")
        d = d if isinstance(d, dict) else {}

        def num(k: str) -> float | None:
            v = d.get(k)
            return round(float(v), 6) if isinstance(v, (int, float)) and not isinstance(v, bool) else None
        return {"spent_usd": num("spent_usd"), "budget_usd": num("budget_usd"),
                "budget_remaining_usd": num("budget_remaining_usd"),
                "mode": d.get("mode") if d.get("mode") in AGENT_MODES else None}

    @app.get("/m/api/agent/sessions/{session_id}/events")
    async def m_agent_session_events(session_id: str, after: int = 0, limit: int = 200, _=Depends(session)):
        return await call("GET", f"/api/agent/sessions/{_tid(session_id)}/events", params={"after": after, "limit": limit})

    @app.post("/m/api/agent/sessions/{session_id}/stop")
    async def m_agent_session_stop(session_id: str, _=Depends(session)):
        return await call("POST", f"/api/agent/sessions/{_tid(session_id)}/stop")

    # «/» menu + context gauge (owner 21.10). Each answer is cut down to the documented keys (slash.py): the phone gets
    # numbers, ids and enums, never chat text. Memory is written only by the owner's explicit POST .../memory.
    @app.get("/m/api/agent/palette")
    async def m_agent_palette(session_id: str | None = None, _=Depends(session)):
        params = {"session_id": _tid(session_id)} if session_id else None
        return slash.palette_view(await call("GET", "/api/agent/palette", params=params))

    @app.get("/m/api/agent/sessions/{session_id}/context")
    async def m_agent_session_context(session_id: str, _=Depends(session)):
        return slash.context_view(await call("GET", f"/api/agent/sessions/{_tid(session_id)}/context"))

    @app.get("/m/api/agent/sessions/{session_id}/updates")
    async def m_agent_session_updates(session_id: str, _=Depends(session)):
        return slash.updates_view(await call("GET", f"/api/agent/sessions/{_tid(session_id)}/updates"))

    @app.post("/m/api/agent/sessions/{session_id}/compact", status_code=202)
    async def m_agent_session_compact(session_id: str, body: CompactIn | None = None, _=Depends(session)):
        payload = body.model_dump(exclude_none=True) if body else {}
        return slash.compact_view(await call("POST", f"/api/agent/sessions/{_tid(session_id)}/compact", json=payload, timeout=60))

    @app.post("/m/api/agent/sessions/{session_id}/memory")
    async def m_agent_session_memory(session_id: str, body: MemoryIn | None = None, _=Depends(session)):
        payload = body.model_dump(exclude_none=True) if body else {}
        return slash.memory_view(await call("POST", f"/api/agent/sessions/{_tid(session_id)}/memory", json=payload, timeout=60))

    @app.post("/m/api/agent/tasks/{task_id}/approve")
    async def m_agent_task_approve(task_id: str, request: Request, _=Depends(session)):
        body = await request.json()
        return await call("POST", f"/api/agent/tasks/{task_id}/approve", json=body)

    @app.post("/m/api/agent/improve")
    async def m_agent_improve(request: Request, _=Depends(session)):
        body = await request.json()
        return await call("POST", "/api/agent/improve", json=body, timeout=180)

    @app.get("/m/api/agent/improve")
    async def m_agent_improve_list(_=Depends(session)):
        return await call("GET", "/api/agent/improve")

    @app.get("/m/api/terminal/capabilities")
    async def m_term_caps(_=Depends(session)):
        return await call("GET", "/api/terminal/capabilities")

    async def _default_term_root() -> tuple[list[str], str | None]:
        body = await call("GET", "/api/terminal/roots")
        roots = [r for r in (body.get("roots") if isinstance(body, dict) else None) or [] if isinstance(r, str)]

        def norm(p: str) -> str:
            return str(Path(p)).rstrip("\\/").lower()
        here = norm(str(REPO_ROOT))
        default = next((r for r in roots if norm(r) == here), roots[0] if roots else None)
        return roots, default

    @app.get("/m/api/terminal/roots")
    async def m_term_roots(_=Depends(session)):
        roots, default = await _default_term_root()
        return {"roots": roots, "default": default}

    @app.post("/m/api/terminal/run")
    async def m_term_run(request: Request, _=Depends(session)):
        body = await request.json()
        if not isinstance(body, dict):
            raise fail(422, "тело запроса должно быть JSON-объектом", "BM_BAD_REQUEST")
        why = terminal_refusal(str(body.get("command") or ""))
        if why:
            raise fail(422, why, "TERMINAL_INTERACTIVE", "веб-терминал без TTY: только команды, которые завершаются сами")
        try:
            status, payload = await bm.json("POST", "/api/terminal/run", json=body)
        except BossmanUnavailable as exc:
            raise fail(502, str(exc), "BOSSMAN_UNAVAILABLE", "проверьте, что Bossman запущен на ПК") from None
        if status == 202:
            # Bossman answers «needs approval» as HTTP 202 with the id in `detail`; passed through as a 200 body the
            # phone saw no session id and printed nothing for `python -V` / `echo test` (live audit 25.10).
            # Bossman's error handler wraps HTTPException details as {"error": {...}} (older shape: {"detail": {...}})
            det = next((payload.get(k) for k in ("error", "detail")
                        if isinstance(payload, dict) and isinstance(payload.get(k), dict)), {})
            aid = det.get("approval_id")
            aid = int(aid) if isinstance(aid, int) or (isinstance(aid, str) and aid.isdigit()) else None
            return JSONResponse(status_code=202, content={
                "approval_required": True, "approval_id": aid, "decision": "ask",
                "message": "нужно подтверждение владельца: команда не из списка только-чтения"})
        if status >= 400:
            err = payload.get("error") if isinstance(payload, dict) else None
            if isinstance(err, dict):
                raise HTTPException(status, safe(err))
            detail = payload.get("detail") if isinstance(payload, dict) else None
            raise HTTPException(status, safe(detail if isinstance(detail, dict) else {"message": f"Bossman: HTTP {status}"}))
        return safe(payload)

    @app.get("/m/api/terminal/sessions")
    async def m_term_sessions(_=Depends(session)):
        return await call("GET", "/api/terminal/sessions")

    @app.get("/m/api/terminal/sessions/{session_id}")
    async def m_term_session(session_id: str, _=Depends(session)):
        return await call("GET", f"/api/terminal/sessions/{session_id}")

    @app.get("/m/api/terminal/sessions/{session_id}/log")
    async def m_term_session_log(session_id: str, _=Depends(session)):
        return await call("GET", f"/api/terminal/sessions/{session_id}/log")

    @app.post("/m/api/terminal/sessions/{session_id}/stdin")
    async def m_term_stdin(session_id: str, request: Request, _=Depends(session)):
        body = await request.json()
        return await call("POST", f"/api/terminal/sessions/{session_id}/stdin", json=body)

    @app.post("/m/api/terminal/sessions/{session_id}/sigint")
    async def m_term_sigint(session_id: str, _=Depends(session)):
        return await call("POST", f"/api/terminal/sessions/{session_id}/sigint")

    @app.post("/m/api/terminal/sessions/{session_id}/kill")
    async def m_term_kill(session_id: str, _=Depends(session)):
        return await call("POST", f"/api/terminal/sessions/{session_id}/kill")

    @app.get("/m/api/terminal/files")
    async def m_term_files(path: str | None = None, _=Depends(session)):
        return await call("GET", "/api/terminal/files", params={"path": path} if path else None)

    @app.get("/m/api/terminal/diff")
    async def m_term_diff(cwd: str | None = None, _=Depends(session)):
        target_cwd = cwd
        if not target_cwd:
            _, target_cwd = await _default_term_root()
        return await call("GET", "/api/terminal/diff", params={"cwd": target_cwd} if target_cwd else None)

    @app.get("/m/api/coding-tasks/status")
    async def m_coding_status(_=Depends(session)):
        """Counts of coding tasks by status. Bossman has no /api/coding-tasks/status (that path hit /{task_id} and
        answered 404 - parity audit 24.10); its readiness probe can load a local model, so the phone counts the list."""
        body = await call("GET", "/api/coding-tasks")
        items = [i for i in (body.get("items") or []) if isinstance(i, dict)] if isinstance(body, dict) else []
        by_status: dict[str, int] = {}
        for i in items:
            by_status[str(i.get("status"))] = by_status.get(str(i.get("status")), 0) + 1
        active = sum(n for s, n in by_status.items() if s in ("queued", "running"))   # = control-plane «active»
        return {"total": len(items), "active": active, "by_status": by_status}

    @app.get("/m/api/coding-tasks")
    async def m_coding_list(limit: int = 25, _=Depends(session)):
        return await call("GET", "/api/coding-tasks", params={"limit": limit})

    @app.post("/m/api/coding-tasks")
    async def m_coding_create(request: Request, _=Depends(session)):
        body = await request.json()
        return await call("POST", "/api/coding-tasks", json=body, timeout=180)

    @app.get("/m/api/coding-tasks/{task_id}")
    async def m_coding_get(task_id: str, _=Depends(session)):
        return await call("GET", f"/api/coding-tasks/{task_id}")

    @app.post("/m/api/coding-tasks/{task_id}/apply")
    async def m_coding_apply(task_id: str, _=Depends(session)):
        return await call("POST", f"/api/coding-tasks/{task_id}/apply", timeout=120)

    @app.post("/m/api/coding-tasks/{task_id}/cancel")
    async def m_coding_cancel(task_id: str, _=Depends(session)):
        return await call("POST", f"/api/coding-tasks/{task_id}/cancel")

    @app.websocket("/m/api/agent/sessions/{session_id}/stream")
    async def m_agent_ws(ws: WebSocket, session_id: str):
        await _ws(ws, f"api/agent/sessions/{session_id}/stream")

    @app.websocket("/m/api/terminal/sessions/{session_id}/stream")
    async def m_terminal_ws(ws: WebSocket, session_id: str):
        await _ws(ws, f"api/terminal/sessions/{session_id}/stream")

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
