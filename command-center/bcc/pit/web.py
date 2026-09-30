"""Loopback-only Jeff web transport: the participant chat behind the Jeff window.

Reuse contract (docs/v1.7): every chat turn goes through
``ParticipantRuntime.handle()`` — the same public guard, forbidden-command
refusal, zero-start intro, per-person memory, free-only routing, reasoning
strip and presentation renderer as Telegram Jeff. This module adds only:

- local web accounts (scrypt) and sessions, stored under ``pit-v1.7/web``;
- a web identity namespace (HMAC ``web:<id>``) that can never collide with a
  Telegram participant's person_key, and a separate conversation store;
- a transport shim that replaces Telegram delivery. It has no Bot API method:
  the web server can never poll or send through either Telegram bot;
- local voice (ASR with confirmation, Piper TTS) and memory review/correction.

Authority: no owner Command Center token, no task/approval/computer/shell
endpoint, loopback bind only, Host/Origin checked (DNS rebinding), mutating
requests need a same-origin custom header. Model/provider names never appear
in any response; the owner reads them with ``bossman pit routes``.
"""
from __future__ import annotations

import argparse
import asyncio
import base64
import contextvars
import getpass
import hashlib
import hmac
import ipaddress
import json
import os
import re
import secrets
import sys
import threading
import time
from pathlib import Path

from fastapi import Request  # module level: route annotations resolve against globals

from bcc.telegram_companion.config import CompanionError, Person

from . import passport_api
from .config import PITSettings, load, pit_home
from .reply_stream import reply_sink
from .runtime import (
    CLOUD_PAUSED_RU,
    FORBIDDEN_REPLY_RU,
    INCOMPLETE_REPLY_RU,
    NO_MODEL_RU,
    NO_REMOTE_RU,
    PROVIDER_DOWN_RU,
    ParticipantRuntime,
    PITStore,
)

WEB_APP_ID = "bossman-jeff-web-v1"
SESSION_COOKIE = "jeff_session"
SESSION_TTL_SECONDS = 14 * 24 * 3600
CSRF_HEADER = "x-jeff-request"
MAX_TEXT_CHARS = 4000
MAX_UPLOAD_BYTES = 10 * 1024 * 1024
MAX_AUDIO_BYTES = 12 * 1024 * 1024
STOPPED_RU = "Остановлено. Можно продолжать."
GENERIC_ERROR_RU = "Произошла ошибка внутри Bossman. Она записана локально; повтор безопасен."
STREAM_ERROR_RU = "Не получилось ответить. Повторите чуть позже."
#: Replies that are an honest status, not a model answer: the window marks them ``kind=error`` after F5.
ERROR_REPLIES = frozenset({NO_MODEL_RU, PROVIDER_DOWN_RU, CLOUD_PAUSED_RU, INCOMPLETE_REPLY_RU, NO_REMOTE_RU,
                           GENERIC_ERROR_RU, STREAM_ERROR_RU})
UI_FILES = {"jeff.html": "text/html; charset=utf-8", "jeff.css": "text/css; charset=utf-8",
            "jeff.js": "text/javascript; charset=utf-8", "icon.svg": "image/svg+xml"}
IMAGE_TYPES = {"image/jpeg", "image/png", "image/webp"}
TEXT_TYPES = {"text/plain": ".txt", "text/markdown": ".md"}
_USERNAME = re.compile(r"[\w.\-]{2,32}", re.UNICODE)

_outbox: contextvars.ContextVar[list | None] = contextvars.ContextVar("jeff_web_outbox", default=None)
_uploads: contextvars.ContextVar[dict | None] = contextvars.ContextVar("jeff_web_uploads", default=None)


# -- accounts & sessions -----------------------------------------------------------------
def _hash_password(password: str, salt: bytes) -> str:
    return hashlib.scrypt(password.encode("utf-8"), salt=salt, n=2 ** 14, r=8, p=1,
                          dklen=32).hex()


def _write_private(path: Path, payload: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(payload, ensure_ascii=False, indent=1), encoding="utf-8")
    os.replace(tmp, path)
    try:
        from bcc.auth import _restrict_to_owner
        _restrict_to_owner(path)
    except Exception:  # noqa: BLE001 — best effort, like the rest of the data dir
        pass


class WebAccounts:
    """Local Jeff web accounts. Passwords are scrypt hashes; ids are 1..N."""

    def __init__(self, web_home: Path):
        self.path = Path(web_home) / "accounts.json"
        self._lock = threading.Lock()

    def _load(self) -> dict:
        if not self.path.is_file():
            return {"next_id": 1, "users": {}}
        data = json.loads(self.path.read_text(encoding="utf-8"))
        return data if isinstance(data, dict) else {"next_id": 1, "users": {}}

    def count(self) -> int:
        return len(self._load().get("users", {}))

    def create(self, name: str, password: str, *, only_if_empty: bool = False) -> int:
        name = str(name or "").strip()
        if not _USERNAME.fullmatch(name):
            raise ValueError("USERNAME_INVALID")
        if not 8 <= len(str(password or "")) <= 256:
            raise ValueError("PASSWORD_TOO_SHORT")
        with self._lock:
            data = self._load()
            users = data.setdefault("users", {})
            if only_if_empty and users:
                # First-run web signup: checked under the lock, so two
                # simultaneous signups cannot both create an account.
                raise ValueError("SIGNUP_CLOSED_USE_CLI")
            if name.lower() in users:
                raise ValueError("USERNAME_TAKEN")
            if len(users) >= 50:
                raise ValueError("TOO_MANY_ACCOUNTS")
            uid = int(data.get("next_id", 1))
            salt = secrets.token_bytes(16)
            users[name.lower()] = {"id": uid, "name": name, "salt": salt.hex(),
                                   "hash": _hash_password(password, salt),
                                   "created": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())}
            data["next_id"] = uid + 1
            _write_private(self.path, data)
            return uid

    def verify(self, name: str, password: str) -> int | None:
        row = self._load().get("users", {}).get(str(name or "").strip().lower())
        if not isinstance(row, dict):
            _hash_password(str(password or "x"), b"0" * 16)   # equalise timing
            return None
        expected = row.get("hash", "")
        actual = _hash_password(str(password or ""), bytes.fromhex(row.get("salt", "00")))
        return int(row["id"]) if hmac.compare_digest(expected, actual) else None

    def name_for(self, uid: int) -> str:
        for row in self._load().get("users", {}).values():
            if isinstance(row, dict) and row.get("id") == uid:
                return str(row.get("name", ""))
        return ""


class WebSessions:
    """Opaque cookie tokens; only their SHA-256 is stored, so a restart keeps logins."""

    def __init__(self, web_home: Path):
        self.path = Path(web_home) / "sessions.json"
        self._lock = threading.Lock()

    def _load(self) -> dict:
        if not self.path.is_file():
            return {}
        try:
            data = json.loads(self.path.read_text(encoding="utf-8"))
        except ValueError:
            return {}
        return data if isinstance(data, dict) else {}

    @staticmethod
    def _digest(token: str) -> str:
        return hashlib.sha256(token.encode("utf-8")).hexdigest()

    def create(self, uid: int) -> str:
        token = secrets.token_urlsafe(32)
        with self._lock:
            data = {k: v for k, v in self._load().items() if v.get("exp", 0) > time.time()}
            data[self._digest(token)] = {"uid": int(uid), "exp": time.time() + SESSION_TTL_SECONDS}
            _write_private(self.path, data)
        return token

    def resolve(self, token: str | None) -> int | None:
        if not token or len(token) > 200:
            return None
        row = self._load().get(self._digest(token))
        if not isinstance(row, dict) or row.get("exp", 0) <= time.time():
            return None
        return int(row["uid"])

    def revoke(self, token: str | None) -> None:
        if not token:
            return
        with self._lock:
            data = self._load()
            data.pop(self._digest(token), None)
            _write_private(self.path, data)


# -- transport shim ------------------------------------------------------------------------
class WebTransport:
    """Stands in for the Telegram adapter inside the runtime. No Bot API at all."""

    def __init__(self):
        self.authorize_delivery = lambda person: True
        self._ids = 0

    def _next(self) -> int:
        self._ids += 1
        return self._ids

    async def call(self, method: str, payload: dict):
        raise CompanionError("WEB_TRANSPORT_HAS_NO_TELEGRAM")

    async def preflight(self):
        raise CompanionError("WEB_TRANSPORT_HAS_NO_TELEGRAM")

    async def close(self):
        return None

    async def fetch_file(self, file_id: str, max_bytes: int = MAX_UPLOAD_BYTES) -> bytes:
        uploads = _uploads.get() or {}
        data = uploads.get(file_id)
        if data is None:
            raise CompanionError("TELEGRAM_FILE_UNAVAILABLE")
        if len(data) > max_bytes:
            raise CompanionError("IMAGE_TOO_LARGE")
        return data

    def _emit(self, item: dict) -> int:
        box = _outbox.get()
        if box is not None:
            box.append(item)
        return self._next()

    async def send(self, person, text, **kwargs) -> int:
        return self._emit({"kind": "text", "text": str(text)[:4000]})

    async def send_photo(self, person, data: bytes, caption: str = "") -> int:
        return self._emit({"kind": "image", "mime": "image/png",
                           "data_url": "data:image/png;base64," + base64.b64encode(data).decode()})

    async def send_document(self, person, name: str, data: bytes, caption: str = "") -> int:
        return self._emit({"kind": "document", "name": str(name)[:80],
                           "data_url": "data:application/json;base64," + base64.b64encode(data).decode()})

    async def send_voice(self, *args, **kwargs) -> int:
        raise CompanionError("WEB_TRANSPORT_HAS_NO_TELEGRAM")

    async def delete_message(self, person, message_id: int) -> bool:
        return True


def derive_web_person_key(web_user_id: int, salt: bytes) -> str:
    """Web identities live in their own HMAC namespace, disjoint from Telegram."""
    if len(salt) < 16:
        raise ValueError("PIT identity salt must be at least 16 bytes")
    return hmac.new(salt, b"web:" + str(int(web_user_id)).encode("ascii"),
                    hashlib.sha256).hexdigest()


class WebParticipantRuntime(ParticipantRuntime):
    """The unchanged participant pipeline, delivered to the Jeff window."""

    def __init__(self, settings: PITSettings, web_home: Path):
        super().__init__(settings)
        # The runtime opened the Telegram conversation store; the window keeps
        # its conversations apart and never touches the Bot API client again.
        with_suppressed(self.store.close)
        self._telegram_client = self.telegram
        self.telegram = WebTransport()
        self.store = PITStore(Path(web_home))
        salt = self.vault.identity_salt
        self.vault.key_for_telegram = lambda uid: derive_web_person_key(uid, salt)
        self.surface = "web"
        from .heartbeat import Heartbeat
        self.heartbeat = Heartbeat(Path(web_home), "web")

    async def close(self) -> None:
        with_suppressed_async = [self._telegram_client.close, self.models.close,
                                 self.photo_services.close]
        for closer in with_suppressed_async:
            try:
                await closer()
            except Exception:  # noqa: BLE001
                pass
        with_suppressed(self.store.close)


def with_suppressed(fn) -> None:
    if fn is None:
        return
    try:
        fn()
    except Exception:  # noqa: BLE001
        pass


# -- HTTP app ------------------------------------------------------------------------------
def _ui_dir() -> Path:
    try:
        from bcc.config import settings as cc_settings
        candidate = Path(cc_settings.ui_dir)
        if (candidate / "jeff.html").is_file():
            return candidate
    except Exception:  # noqa: BLE001
        pass
    return Path(__file__).resolve().parents[2] / "ui"


def _identity() -> dict:
    from bcc import __version__
    from bcc.build_identity import source_identity
    ident = source_identity()
    return {"app": WEB_APP_ID, "version": __version__,
            "build_sha": ident.get("build_sha"), "build_sha_short": ident.get("build_sha_short"),
            "source_identity": ident.get("source_identity")}


def _public_heartbeat(beat: dict | None) -> dict | None:
    """The heartbeat as the UNAUTHENTICATED health endpoint may show it: no model id, no route list, no policy.

    The owner reads the full record from the heartbeat file (``/api/jeff-settings/status``) and ``bossman pit
    routes``; a web guest only learns counts and whether Jeff is up."""
    if not isinstance(beat, dict):
        return beat
    public = dict(beat)
    public["llm"] = {key: value for key, value in (beat.get("llm") or {}).items() if key != "model"}
    route = beat.get("route")
    if isinstance(route, dict):
        public["route"] = {
            "schema": route.get("schema"),
            "remote_routes": len(route.get("free_remote") or []),
            "local_routes": len(route.get("local") or []),
            "refusal": route.get("refusal", ""),
            "price_checked_age_s": route.get("price_checked_age_s"),
            "paid_routes_allowed": False,
        }
    return public


def _guest_synthesize(text: str, *, stopped, audit_dir: Path) -> bytes:
    """Speak ``text`` for a window participant: the stock local voice only.

    Same steps as ``speech.synthesize`` (filtered text, pre-TTS audit, engine chain) but with
    ``allow_candidate=False``: a guest never gets the CosyVoice candidate or a cloned voice, whatever
    text the client sends."""
    from bcc.oss.piper import PiperError

    from . import speech, speech_audit
    voice_text = speech.tts_text(text)
    if not voice_text:
        raise speech.SpeechError("VOICE_TEXT_INVALID")
    try:
        speech_audit.capture(voice_text, surface="web", audit_dir=audit_dir)
    except OSError as exc:
        raise speech.SpeechError("VOICE_AUDIT_FAILED") from exc
    if speech_audit.is_threat(voice_text):
        # Audited above; a threat is never read aloud (the window's speak button sends reply text back here).
        raise speech.SpeechError("VOICE_TEXT_REFUSED")
    try:
        return speech.run_engines(voice_text, stopped=stopped, allow_candidate=False)
    except PiperError as exc:
        raise speech.SpeechError(str(exc)) from exc


class _RuntimeHandle:
    """The runtime is built on the server's event-loop thread (SQLite is thread-bound)."""

    def __init__(self):
        self.value = None

    def __getattr__(self, name):
        value = self.__dict__.get("value")
        if value is None:
            raise RuntimeError("Jeff runtime not started")
        return getattr(value, name)


def create_app(settings: PITSettings, *, port: int, runtime: ParticipantRuntime | None = None,
               runtime_factory=None, ui_dir: Path | None = None):
    from fastapi import FastAPI
    from fastapi.responses import JSONResponse, RedirectResponse, Response

    from . import speech

    home = pit_home(Path(settings.data_dir))
    web_home = home / "web"
    web_home.mkdir(parents=True, exist_ok=True)
    accounts, sessions = WebAccounts(web_home), WebSessions(web_home)
    rt = _RuntimeHandle()
    rt.value = runtime
    factory = runtime_factory or (lambda: WebParticipantRuntime(settings, web_home))
    ui = Path(ui_dir) if ui_dir else _ui_dir()
    locks: dict[int, asyncio.Lock] = {}
    inflight: dict[int, asyncio.Task] = {}
    background: set[asyncio.Task] = set()
    stop_events: dict[int, threading.Event] = {}
    login_failures: dict[str, list[float]] = {}
    allowed_hosts = {f"127.0.0.1:{port}", f"localhost:{port}"}

    app = FastAPI(docs_url=None, redoc_url=None, openapi_url=None)
    app.state.runtime = rt

    startup_tasks: list[asyncio.Task] = []

    @app.on_event("startup")
    async def _startup():
        if rt.value is None:
            rt.value = factory()
        # The window is a Jeff surface of its own: it beats every 15 s into pit-v1.7/web/heartbeat.json (the owner
        # panel and ``bossman pit status`` read it) and builds the route catalog now, not on the first message.
        try:
            rt.value.heartbeat.state = "running"
            for coro in (rt.value._heartbeat_loop(), rt.value.refresh_catalog_safe()):
                startup_tasks.append(asyncio.create_task(coro))
        except Exception:  # noqa: BLE001 - observability/warm-up never stops the window from starting
            pass

    def error(status: int, code: str) -> JSONResponse:
        return JSONResponse({"error": code}, status_code=status)

    @app.middleware("http")
    async def guard(request: Request, call_next):
        host = request.headers.get("host", "")
        if host not in allowed_hosts:
            return error(421, "HOST_NOT_ALLOWED")
        origin = request.headers.get("origin")
        if origin and origin not in {f"http://{h}" for h in allowed_hosts}:
            return error(403, "ORIGIN_NOT_ALLOWED")
        if request.method not in {"GET", "HEAD"} and request.headers.get(CSRF_HEADER) != "1":
            return error(403, "CSRF_HEADER_REQUIRED")
        response = await call_next(request)
        response.headers["Cache-Control"] = "no-store"
        response.headers["X-Content-Type-Options"] = "nosniff"
        response.headers["Referrer-Policy"] = "no-referrer"
        response.headers["Content-Security-Policy"] = (
            "default-src 'self'; img-src 'self' data: blob:; media-src 'self' blob:; "
            "style-src 'self' 'unsafe-inline'; script-src 'self'; connect-src 'self'; "
            "frame-ancestors 'none'")
        return response

    def user_of(request: Request) -> int | None:
        return sessions.resolve(request.cookies.get(SESSION_COOKIE))

    def person_for(uid: int) -> Person:
        return Person(user_id=uid, chat_id=uid, role="guest")

    def lock_for(uid: int) -> asyncio.Lock:
        return locks.setdefault(uid, asyncio.Lock())

    def stop_event(uid: int) -> threading.Event:
        return stop_events.setdefault(uid, threading.Event())

    def session_response(uid: int, payload: dict) -> JSONResponse:
        response = JSONResponse(payload)
        response.set_cookie(SESSION_COOKIE, sessions.create(uid), max_age=SESSION_TTL_SECONDS,
                            httponly=True, samesite="strict", path="/")
        return response

    async def body_json(request: Request) -> dict:
        raw = await request.body()
        if len(raw) > 64_000:
            return {}
        try:
            data = json.loads(raw or b"{}")
        except ValueError:
            return {}
        return data if isinstance(data, dict) else {}

    def next_message_id(uid: int) -> int:
        key = f"web_message_seq:{uid}"
        value = int(rt.store.get(key, 0) or 0) + 1
        rt.store.put(key, value)
        return value

    def disclosure(uid: int, had_history: bool, reply: str) -> list[str]:
        """Public 'how Jeff answered' notes. Never reasoning text or model names.

        The history note says what the model REALLY received: the runtime records how many earlier pairs went
        into the answering route, and a cloud route may have got none of them (privacy default)."""
        who = person_for(uid).key
        used_context = rt.turn_context.pop(who, None)
        if used_context is None:
            notes = ["Ответ выдан без обращения к модели (команда или быстрый ответ)"]
        elif used_context.get("pairs", 0) > 0:
            notes = ["Учёл предыдущие сообщения разговора"]
        elif had_history:
            notes = ["Этот ответ дан без прошлых сообщений (настройка приватности)"]
        else:
            notes = ["Начал разговор с чистого листа"]
        used = rt.store.get(f"last_context:{who}") or []
        if used:
            notes.append(f"Опирался на факты из твоей памяти: {len(used)}")
        if "Источники:" in reply:
            notes.append("Проверил свежие источники в интернете")
        return notes

    def display_text(message: dict) -> str:
        """What the participant sees as their own bubble for this message."""
        text = str(message.get("text") or "").strip()
        if message.get("_photo"):
            return "📎 фото" + (" — " + text if text else "")
        if message.get("_document"):
            return "📎 файл" + (" — " + text if text else "")
        return text

    def reply_kind(user_text: str, reply: str, *, stopped: bool = False, attachments: bool = False) -> str:
        if stopped:
            return "stopped"
        if reply in ERROR_REPLIES:
            return "error"
        if user_text.startswith("/"):
            return "command"
        return "upload" if attachments else "chat"

    def memory_on(uid: int) -> bool:
        return bool(rt.vault.consent(rt.vault.key_for_telegram(uid)).memory_enabled)

    def transcript_add(who: str, role: str, text: str, kind: str) -> None:
        """Best effort: a transcript failure never breaks a reply (the context history is separate)."""
        try:
            rt.store.transcript_add(who, role, text, kind)
        except Exception:  # noqa: BLE001
            pass

    def record_static_exchange(uid: int, user_text: str, reply: str, kind: str = "command") -> None:
        """A turn answered without run_turn (a voice transcript refused as a command): still part of the chat."""
        if memory_on(uid):
            who = person_for(uid).key
            transcript_add(who, "user", user_text, "chat")
            transcript_add(who, "assistant", reply, kind)

    async def run_turn(uid: int, message: dict, uploads: dict | None = None, sink=None, *,
                       record: str = "both") -> dict:
        """One chat turn. ``record``: "both" = the user message and the displayed reply go to the window
        transcript (when memory is on), "reply" = only the reply (the internal /start greeting), "none" = nothing
        (the privacy switches run commands the participant never typed)."""
        person = person_for(uid)
        who = person.key
        box: list = []
        stopped = False
        async with lock_for(uid):
            event = stop_event(uid)
            event.clear()
            memory_before = memory_on(uid)
            epoch_before = rt.store.forget_epoch.get(who, 0)
            had_history = bool(memory_before and rt.store.history(who))
            user_text = display_text(message)
            rt.turn_context.pop(who, None)
            if record == "both" and memory_before and user_text:
                transcript_add(who, "user", user_text, "upload" if message.get("_photo") or message.get("_document")
                               else "chat")
            token_box, token_up = _outbox.set(box), _uploads.set(uploads or {})
            token_sink = reply_sink.set(sink)
            try:
                task = asyncio.create_task(rt.handle(person, message))
                inflight[uid] = task
                try:
                    reply = await task
                except asyncio.CancelledError:
                    if not event.is_set():
                        raise
                    reply, stopped = STOPPED_RU, True
                finally:
                    inflight.pop(uid, None)
            except CompanionError as exc:
                from .runtime import _failure_text
                reply = _failure_text(str(exc))
            except Exception as exc:  # noqa: BLE001 — same generic reply as the Telegram worker
                import traceback
                from .vault import _append_jsonl
                frames = traceback.extract_tb(exc.__traceback__)
                frame = frames[-1] if frames else None
                try:
                    _append_jsonl(rt.home / "logs" / "runtime_error.jsonl", {
                        "at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
                        "kind": type(exc).__name__, "surface": "web",
                        "file": Path(frame.filename).name if frame else "unknown",
                        "function": frame.name if frame else "unknown",
                        "line": frame.lineno if frame else 0,
                        "schema": "bossman.pit.runtime-error/1"})
                except OSError:
                    pass
                reply = GENERIC_ERROR_RU
            finally:
                _outbox.reset(token_box)
                _uploads.reset(token_up)
                reply_sink.reset(token_sink)
        reply = str(reply or "")
        attachments = [item for item in box if item.get("kind") != "text"]
        texts = [item["text"] for item in box if item.get("kind") == "text"]
        if not reply and texts:
            reply = texts[-1]
        if reply and not stopped:
            # Mandatory outgoing filter for EVERY reply the window returns: commands, j2 early replies,
            # uploads and photo answers as well as model answers (Telegram filters at its single send point).
            reply = rt.guard_outgoing(reply)
        if reply and record != "none":
            # Recorded by the state AFTER the turn as well: /resume_memory (off -> on) and the first contact are
            # part of the chat; a /delete_me confirmation that erased the data must not re-create any of it.
            wiped = rt.store.forget_epoch.get(who, 0) != epoch_before
            if not wiped and (memory_before or memory_on(uid)):
                if record == "both" and not memory_before and user_text:
                    transcript_add(who, "user", user_text, "chat")
                kind = ("greeting" if record == "reply"
                        else reply_kind(user_text, reply, stopped=stopped, attachments=bool(attachments)))
                transcript_add(who, "assistant", reply, kind)
        if stopped:
            return {"reply": STOPPED_RU, "stopped": True, "attachments": [], "disclosure": []}
        return {"reply": reply, "stopped": False, "attachments": attachments,
                "disclosure": disclosure(uid, had_history, reply) if reply else []}

    def base_message(uid: int, text: str = "") -> dict:
        return {"_user_id": uid, "_chat_id": uid, "_message_id": next_message_id(uid),
                "text": text, "_photo": "", "_document": None, "_voice": None,
                "_sticker": "", "_reply_to": None}

    # -- static & identity ------------------------------------------------------------
    @app.get("/")
    async def root():
        return RedirectResponse("/jeff.html")

    @app.get("/{name}")
    async def static(name: str):
        if name not in UI_FILES:
            return error(404, "NOT_FOUND")
        path = ui / name
        if not path.is_file():
            return error(404, "NOT_FOUND")
        return Response(path.read_bytes(), media_type=UI_FILES[name])

    @app.get("/api/jeff/identity")
    async def identity():
        return _identity()

    @app.get("/api/jeff/health")
    async def health():
        try:
            beat = rt.heartbeat_snapshot("running")
        except Exception:  # noqa: BLE001 — health must answer even without a live runtime
            beat = None
        return {"ok": True, "voice": {"asr": speech.asr_status(), "tts": speech.tts_status()},
                "heartbeat": _public_heartbeat(beat)}

    # -- auth -------------------------------------------------------------------------------
    @app.get("/api/jeff/me")
    async def me(request: Request):
        uid = user_of(request)
        if uid is None:
            return JSONResponse({"authenticated": False, "signup_open": accounts.count() == 0},
                                status_code=401)
        return {"authenticated": True, "name": accounts.name_for(uid)}

    @app.post("/api/jeff/signup")
    async def signup(request: Request):
        if accounts.count() != 0:
            return error(403, "SIGNUP_CLOSED_USE_CLI")
        data = await body_json(request)
        try:
            uid = await asyncio.to_thread(
                accounts.create, str(data.get("username", "")), str(data.get("password", "")),
                only_if_empty=True)
        except ValueError as exc:
            return error(403 if str(exc) == "SIGNUP_CLOSED_USE_CLI" else 400, str(exc))
        greeting = (await run_turn(uid, base_message(uid, "/start"), record="reply"))["reply"]
        return session_response(uid, {"ok": True, "greeting": greeting})

    @app.post("/api/jeff/login")
    async def login(request: Request):
        data = await body_json(request)
        name = str(data.get("username", "")).strip().lower()[:40]
        now = time.time()
        recent = [t for t in login_failures.get(name, []) if now - t < 60]
        if len(recent) >= 5:
            return error(429, "TOO_MANY_ATTEMPTS")
        uid = accounts.verify(name, str(data.get("password", "")))
        if uid is None:
            login_failures[name] = recent + [now]
            return error(401, "LOGIN_FAILED")
        login_failures.pop(name, None)
        greeting = None
        if not (rt.vault.person_dir(rt.vault.key_for_telegram(uid)) / "consent.json").is_file():
            greeting = (await run_turn(uid, base_message(uid, "/start"), record="reply"))["reply"]
        return session_response(uid, {"ok": True, "greeting": greeting})

    @app.post("/api/jeff/logout")
    async def logout(request: Request):
        sessions.revoke(request.cookies.get(SESSION_COOKIE))
        response = JSONResponse({"ok": True})
        response.delete_cookie(SESSION_COOKIE, path="/")
        return response

    # -- chat ---------------------------------------------------------------------------------
    @app.get("/api/jeff/history")
    async def history(request: Request):
        uid = user_of(request)
        if uid is None:
            return error(401, "AUTH_REQUIRED")
        who = person_for(uid).key
        try:
            limit = int(request.query_params.get("limit", PITStore.TRANSCRIPT_CAP))
        except ValueError:
            limit = PITStore.TRANSCRIPT_CAP
        # The visible chat is the display transcript (errors, stopped turns, commands included). The model-context
        # window ``store.history`` is only the fallback for a window upgraded from a build without a transcript.
        rows = rt.store.transcript(who, limit)
        if rows:
            messages = [{"role": row["role"], "text": row["text"], "kind": row["kind"]} for row in rows]
        else:
            messages = [{"role": row["role"], "text": row["content"], "kind": "chat"}
                        for row in rt.store.history(who)]
        return {"messages": messages, "memory_enabled": memory_on(uid)}

    @app.post("/api/jeff/chat")
    async def chat(request: Request):
        uid = user_of(request)
        if uid is None:
            return error(401, "AUTH_REQUIRED")
        data = await body_json(request)
        text = str(data.get("text", "")).strip()
        if not text or len(text) > MAX_TEXT_CHARS:
            return error(400, "TEXT_REQUIRED")
        if data.get("via") == "voice" and text.startswith("/"):
            # A transcript is chat input, never an executable command.
            record_static_exchange(uid, text, FORBIDDEN_REPLY_RU)
            return {"reply": FORBIDDEN_REPLY_RU, "stopped": False, "attachments": [],
                    "disclosure": []}
        return await run_turn(uid, base_message(uid, text))

    def sse(event: str, payload: dict) -> bytes:
        body = json.dumps(payload, ensure_ascii=False)
        return f"event: {event}\ndata: {body}\n\n".encode("utf-8")

    @app.post("/api/jeff/chat/stream")
    async def chat_stream(request: Request):
        """Same turn as /api/jeff/chat, delivered as server-sent events: ``delta`` (visible text
        so far arrives in pieces), ``reset`` (drop what was shown: that attempt was discarded),
        then exactly one ``final`` with the authoritative reply. STOP works as before."""
        from fastapi.responses import StreamingResponse
        uid = user_of(request)
        if uid is None:
            return error(401, "AUTH_REQUIRED")
        data = await body_json(request)
        text = str(data.get("text", "")).strip()
        if not text or len(text) > MAX_TEXT_CHARS:
            return error(400, "TEXT_REQUIRED")
        queue: asyncio.Queue = asyncio.Queue()
        if data.get("via") == "voice" and text.startswith("/"):
            record_static_exchange(uid, text, FORBIDDEN_REPLY_RU)
            queue.put_nowait(("final", {"reply": FORBIDDEN_REPLY_RU, "stopped": False,
                                        "attachments": [], "disclosure": []}))
        else:
            async def sink(piece):
                queue.put_nowait(("reset", {}) if piece is None else ("delta", {"t": piece}))

            async def turn():
                try:
                    queue.put_nowait(("final", await run_turn(uid, base_message(uid, text),
                                                              sink=sink)))
                except Exception:  # noqa: BLE001 — the stream always ends with a final event
                    queue.put_nowait(("final", {"reply": STREAM_ERROR_RU,
                                                "stopped": False, "attachments": [],
                                                "disclosure": []}))
            background.add(task := asyncio.create_task(turn()))
            task.add_done_callback(background.discard)

        async def body():
            while True:
                kind, payload = await queue.get()
                yield sse(kind, payload)
                if kind == "final":
                    return
        return StreamingResponse(body(), media_type="text/event-stream",
                                 headers={"X-Accel-Buffering": "no"})

    @app.post("/api/jeff/stop")
    async def stop(request: Request):
        uid = user_of(request)
        if uid is None:
            return error(401, "AUTH_REQUIRED")
        stop_event(uid).set()
        task = inflight.get(uid)
        if task is not None and not task.done():
            task.cancel()
            return {"ok": True, "cancelled": True}
        return {"ok": True, "cancelled": False}

    @app.post("/api/jeff/upload")
    async def upload(request: Request):
        uid = user_of(request)
        if uid is None:
            return error(401, "AUTH_REQUIRED")
        mime = request.headers.get("content-type", "").split(";")[0].strip().lower()
        raw = await request.body()
        if not raw or len(raw) > MAX_UPLOAD_BYTES:
            return error(413, "UPLOAD_TOO_LARGE")
        caption = str(request.headers.get("x-jeff-caption", ""))[:1000]
        try:
            caption = bytes.fromhex(caption).decode("utf-8") if caption else ""
        except ValueError:
            caption = ""
        file_id = "web-upload-" + secrets.token_hex(8)
        message = base_message(uid, caption)
        if mime in IMAGE_TYPES:
            message["_photo"] = file_id
        elif mime in TEXT_TYPES:
            message["_document"] = {"file_id": file_id,
                                    "file_name": "upload" + TEXT_TYPES[mime]}
        else:
            return error(415, "UPLOAD_TYPE_UNSUPPORTED")
        return await run_turn(uid, message, uploads={file_id: raw})

    # -- memory (own namespace only) ------------------------------------------------------------
    def own_key(uid: int) -> str:
        return rt.vault.key_for_telegram(uid)

    @app.get("/api/jeff/privacy")
    async def privacy(request: Request):
        uid = user_of(request)
        if uid is None:
            return error(401, "AUTH_REQUIRED")
        consent = rt.vault.consent(own_key(uid))
        return {"memory_enabled": consent.memory_enabled,
                "cloud_context_enabled": consent.remote_personalization_enabled}

    @app.post("/api/jeff/privacy")
    async def privacy_set(request: Request):
        """The participant's own consent switches, via the same commands as Telegram."""
        uid = user_of(request)
        if uid is None:
            return error(401, "AUTH_REQUIRED")
        data = await body_json(request)
        commands = []
        if isinstance(data.get("cloud_context_enabled"), bool):
            commands.append("/privacy personalization " + ("on" if data["cloud_context_enabled"] else "off"))
        if isinstance(data.get("memory_enabled"), bool):
            commands.append("/resume_memory" if data["memory_enabled"] else "/pause_memory")
        if not commands:
            return error(400, "NOTHING_TO_CHANGE")
        replies = [(await run_turn(uid, base_message(uid, command), record="none"))["reply"]
                   for command in commands]
        consent = rt.vault.consent(own_key(uid))
        return {"memory_enabled": consent.memory_enabled,
                "cloud_context_enabled": consent.remote_personalization_enabled,
                "notes": replies}

    @app.get("/api/jeff/memory")
    async def memory(request: Request):
        uid = user_of(request)
        if uid is None:
            return error(401, "AUTH_REQUIRED")
        key = own_key(uid)
        if not rt.vault.person_dir(key).is_dir():
            return {"facts": [], "audit": [], "memory_enabled": False}
        rt.vault.audit(key, "view", actor="participant", surface="web")
        return {"facts": rt.vault.list_facts(key), "audit": rt.vault.memory_audit(key, last=50),
                "memory_enabled": rt.vault.consent(key).memory_enabled}

    @app.post("/api/jeff/memory/correct")
    async def memory_correct(request: Request):
        uid = user_of(request)
        if uid is None:
            return error(401, "AUTH_REQUIRED")
        data = await body_json(request)
        ok = rt.vault.correct_fact(own_key(uid), str(data.get("id", "")), str(data.get("value", "")),
                                   actor="participant", surface="web")
        return {"ok": ok} if ok else error(400, "FACT_NOT_CORRECTED")

    @app.post("/api/jeff/memory/delete")
    async def memory_delete(request: Request):
        uid = user_of(request)
        if uid is None:
            return error(401, "AUTH_REQUIRED")
        data = await body_json(request)
        ok = rt.vault.delete_fact(own_key(uid), str(data.get("id", "")), actor="participant",
                                  surface="web")
        return {"ok": ok} if ok else error(404, "FACT_NOT_FOUND")

    passport_api.register(app, rt=rt, own_key=own_key, user_of=user_of, body_json=body_json,
                          error=error)

    # -- voice ----------------------------------------------------------------------------------
    @app.post("/api/jeff/voice/transcribe")
    async def transcribe(request: Request):
        uid = user_of(request)
        if uid is None:
            return error(401, "AUTH_REQUIRED")
        raw = await request.body()
        if not raw or len(raw) > MAX_AUDIO_BYTES:
            return error(413, "VOICE_TOO_LARGE")
        event = stop_event(uid)
        event.clear()
        started = time.perf_counter()
        try:
            result = await asyncio.to_thread(speech.transcribe_wav, raw, stopped=event.is_set)
        except speech.SpeechError as exc:
            code = str(exc)
            status = (409 if code in {"VOICE_STOPPED", "VOICE_BUSY"}
                      else 503 if "UNAVAILABLE" in code else 422)
            return error(status, code)
        result["latency_ms"] = int((time.perf_counter() - started) * 1000)
        return result

    @app.post("/api/jeff/voice/speak")
    async def speak(request: Request):
        uid = user_of(request)
        if uid is None:
            return error(401, "AUTH_REQUIRED")
        data = await body_json(request)
        text = str(data.get("text", ""))[:MAX_TEXT_CHARS]
        event = stop_event(uid)
        event.clear()
        started = time.perf_counter()
        try:
            audio = await asyncio.to_thread(_guest_synthesize, text, stopped=event.is_set,
                                            audit_dir=home / "logs")
        except speech.SpeechError as exc:
            code = str(exc)
            return error(409 if code == "VOICE_STOPPED" else 422 if code == "VOICE_TEXT_REFUSED" else 503, code)
        return Response(audio, media_type="audio/ogg",
                        headers={"X-Jeff-TTS-Latency-Ms": str(int((time.perf_counter() - started) * 1000))})

    @app.on_event("shutdown")
    async def _shutdown():
        for task in startup_tasks:
            task.cancel()
        await asyncio.gather(*startup_tasks, return_exceptions=True)
        startup_tasks.clear()
        if rt.value is not None:
            try:
                rt.value.heartbeat.write(state="stopped", queue=0)
            except Exception:  # noqa: BLE001
                pass
            await rt.close()
            rt.value = None

    return app


# -- CLI ------------------------------------------------------------------------------------
def _loopback(host: str) -> bool:
    if host == "localhost":
        return True
    try:
        return ipaddress.ip_address(host).is_loopback
    except ValueError:
        return False


def serve(settings: PITSettings, *, host: str = "127.0.0.1", port: int = 8850) -> int:
    import uvicorn
    if not _loopback(host):
        print("bossman pit web: только loopback (127.0.0.1)", file=sys.stderr)
        return 2
    app = create_app(settings, port=port)
    print(f"Jeff web: http://127.0.0.1:{port}/jeff.html (только этот компьютер)", flush=True)
    uvicorn.run(app, host=host, port=port, log_level="warning")
    return 0


def cli_main(config: Path, argv: list[str]) -> int:
    parser = argparse.ArgumentParser(prog="bossman pit web")
    parser.add_argument("command", choices=("web", "web-user"))
    parser.add_argument("action", nargs="?", default="")
    parser.add_argument("name", nargs="?", default="")
    parser.add_argument("--data-dir", default="")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8850)
    parser.add_argument("--password-stdin", action="store_true")
    ns = parser.parse_args(argv)
    settings = load(config)
    if ns.command == "web":
        return serve(settings, host=ns.host, port=ns.port)
    if ns.action != "add" or not ns.name:
        print("Использование: bossman pit web-user add <имя> [--password-stdin]", file=sys.stderr)
        return 2
    password = (sys.stdin.readline().rstrip("\r\n") if ns.password_stdin
                else getpass.getpass("Пароль (скрыт, от 8 символов): "))
    accounts = WebAccounts(pit_home(Path(settings.data_dir)) / "web")
    try:
        uid = accounts.create(ns.name, password)
    except ValueError as exc:
        print(f"bossman pit web-user: {exc}", file=sys.stderr)
        return 2
    print(f"Учётная запись Jeff создана (web id {uid}).")
    return 0


def cmd_web_setup(config: Path, argv: list[str]) -> int:
    """Create a Jeff-window-only PIT configuration (no Telegram token at all).

    Local model: the configured Ollama model. Optional zero-cost cloud fallback:
    the provider key is read from the hidden prompt or BOSSMAN_PIT_PROVIDER_KEY
    (never argv) and stored only in the encrypted credential store.
    """
    from .config import DEFAULT_FREE_CHAT_MODELS, save_setup

    parser = argparse.ArgumentParser(prog="bossman pit web-setup")
    parser.add_argument("command")
    parser.add_argument("--data-dir", default="")
    parser.add_argument("--local-model", default="")
    parser.add_argument("--local-url", default="http://127.0.0.1:11434/v1")
    parser.add_argument("--cloud-models", default="")
    parser.add_argument("--no-cloud", action="store_true")
    ns = parser.parse_args(argv)
    key = ""
    if not ns.no_cloud:
        key = os.environ.get("BOSSMAN_PIT_PROVIDER_KEY", "").strip() or getpass.getpass(
            "Ключ OpenRouter для бесплатных :free моделей (скрыт, Enter = без облака): ").strip()
    models = [m.strip() for m in ns.cloud_models.split(",") if m.strip()] or [
        m for m in DEFAULT_FREE_CHAT_MODELS if m.endswith(":free")]
    if any(not m.endswith(":free") for m in models):
        print("bossman pit web-setup: облачные модели Jeff — только :free", file=sys.stderr)
        return 2
    local_models = [ns.local_model] if ns.local_model else []
    if not key and not local_models:
        print("bossman pit web-setup: нужна локальная модель (--local-model) или ключ для :free",
              file=sys.stderr)
        return 2
    # No cloud (--no-cloud, or Enter at the key prompt) means a LOCAL-ONLY window: no cloud model is configured
    # at all. Before, the ':free' defaults stayed in the config without a key; the public catalog lists them as
    # live 0/0 routes, so ~70% of the turns went to routes that answered 401 and the window looked dead.
    local_only = bool(local_models) and (ns.no_cloud or not key)
    if local_only:
        models = []
    try:
        save_setup(config, people=[Person(user_id=1, chat_id=1, role="owner")],
                   chat_models=models, provider_base_url="https://openrouter.ai/api/v1",
                   core_url="http://127.0.0.1:8800", local_url=ns.local_url if local_models else "",
                   local_models=local_models, local_chat_only=local_only, web_only=True, provider_key=key)
    except (CompanionError, ValueError) as exc:
        print(f"bossman pit web-setup: {exc}", file=sys.stderr)
        return 2
    mode = "только локальная модель, облако не настроено" if local_only else "локальная модель + бесплатное облако"
    print(f"Готово: окно Jeff настроено в {config.parent} ({mode}). Учётная запись: "
          "bossman pit web-user add <имя>, запуск: bossman pit web.")
    return 0
