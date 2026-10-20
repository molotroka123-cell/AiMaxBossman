"""Jeff outbound guard: the one choke point every Jeff Telegram message passes (incident 2026-10-19).

Incident: on 19.10 Jeff was seen writing an unsolicited «ты совсем за меня забыл»-style message, composed by a cloud
model, to everybody he had talked with before. The root cause was not found. This guard makes that class of
behaviour impossible regardless of the cause, because it sits BELOW every sender:

* It is installed as an httpx request/response event hook on the ``AsyncClient`` of every Jeff
  ``telegram_companion.adapters.Telegram`` instance (``install``). Every Bot API call of that instance - ``call``
  (sendMessage, sendSticker, ...), ``payment_call`` (sendInvoice) and the direct uploads (sendPhoto, sendVoice,
  sendVideo, sendDocument) - goes through that client, through every proxy mount, so no caller can skip it.
* A message is a REPLY when the same chat sent Jeff a message (or pressed a button / paid) within the last
  ``REPLY_WINDOW_S`` (30 min, measured from the participant's own Telegram ``date``). Inbound activity is
  recorded from the ``getUpdates`` responses of the same client and, as a second source, by the runtime's ingest.
* Anything else is UNSOLICITED and is allowed only when ALL hold: the owner switch ``jeff_unsolicited_enabled``
  (``jeff-settings.json``, default False) is on, the participant opted in with «включи напоминания» (the existing
  proactive preference ``proactive=True`` and not ``opted_out``), and fewer than ``UNSOLICITED_PER_CHAT`` (1)
  unsolicited message went to that chat in the last 24 h.
* Fan-out breaker: unsolicited attempts (allowed or not) to ``FANOUT_CHATS`` (3) distinct chats within
  ``FANOUT_WINDOW_S`` (10 min) block every unsolicited message and set ``broadcast_tripped``, which stays set
  (across restarts and processes) until the owner clears it (``clear_broadcast_trip``).
* Every attempt - allowed or blocked - is appended to ``<pit>/logs/outbound_guard.jsonl``: hashed chat id (HMAC
  with the identity salt, never the Telegram id), decision, reason, model/provider/origin when the caller set them,
  the first 80 characters of the text (secrets redacted). A blocked attempt raises ``OutboundBlocked`` (a
  ``CompanionError``) BEFORE any byte leaves the machine, and is reported to the owner Pult through an injectable
  notifier, coalesced (one notice per burst, at most one per ``NOTICE_COOLDOWN_S`` unless the breaker newly trips).

State lives in ``<pit>/outbound_guard.sqlite3`` (one transaction per decision, so Jeff's poller and the Bossman
backend - ``jeff.broadcast`` runs there - share one consistent view). A missing data dir means an in-memory state:
fail closed, everything unsolicited is blocked.
"""
from __future__ import annotations

import argparse
import asyncio
import contextvars
import hashlib
import hmac
import inspect
import json
import logging
import re
import sqlite3
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Awaitable, Callable
from urllib.parse import parse_qs

import httpx

from ..telegram_companion.config import CompanionError
from .config import pit_home

log = logging.getLogger("bcc.pit.outbound_guard")

SCHEMA = "bossman.pit.outbound-guard/1"
DB_NAME = "outbound_guard.sqlite3"
AUDIT_NAME = "outbound_guard.jsonl"
REPLY_WINDOW_S = 30 * 60
UNSOLICITED_WINDOW_S = 24 * 3600
UNSOLICITED_PER_CHAT = 1
FANOUT_WINDOW_S = 10 * 60
FANOUT_CHATS = 3
NOTICE_DEBOUNCE_S = 3.0
NOTICE_COOLDOWN_S = 10 * 60
KEEP_ATTEMPTS_S = 48 * 3600
TRIP_FLAG = "broadcast_tripped"

#: Bot API methods that put a new message into a chat. Everything else (getUpdates, editMessageText of an earlier
#: reply, deleteMessage, sendChatAction, answerCallbackQuery, answerPreCheckoutQuery, getFile ...) passes unaudited.
MESSAGE_METHODS = frozenset({
    "sendMessage", "sendPhoto", "sendVoice", "sendVideo", "sendDocument", "sendSticker", "sendAudio",
    "sendAnimation", "sendVideoNote", "sendMediaGroup", "sendLocation", "sendVenue", "sendContact", "sendPoll",
    "sendDice", "sendInvoice", "sendPaidMedia", "copyMessage", "copyMessages", "forwardMessage", "forwardMessages",
})

#: What the caller knows about the message being sent (audit only; it never decides reply vs unsolicited).
OUTBOUND_CONTEXT: contextvars.ContextVar[dict] = contextvars.ContextVar("jeff_outbound_context", default={})

Notifier = Callable[[dict], Any]


class OutboundBlocked(CompanionError):
    """The guard refused this message; nothing was sent."""

    def __init__(self, reason: str):
        super().__init__("JEFF_OUTBOUND_BLOCKED")
        self.reason = reason


def set_context(**fields: Any) -> contextvars.Token:
    """Replace the outbound context of the current task (origin=..., model=..., provider=...)."""
    return OUTBOUND_CONTEXT.set({k: str(v)[:80] for k, v in fields.items() if v is not None})


def note_model(model: str, provider: str) -> None:
    """Merge the model/provider that produced the next outgoing text into the current task's context."""
    OUTBOUND_CONTEXT.set({**OUTBOUND_CONTEXT.get(), "model": str(model or "")[:80],
                          "provider": str(provider or "")[:80]})


@dataclass(frozen=True)
class Decision:
    allowed: bool
    kind: str          # "reply" | "unsolicited"
    reason: str


def _iso(ts: float) -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(ts))


def _excerpt(text: str, limit: int = 80) -> str:
    from .secret_filter import redact_secrets
    clean = re.sub(r"\s+", " ", redact_secrets(str(text or ""))[0]).strip()
    return clean[:limit]


def _default_unsolicited_switch(data_dir: Path | None) -> Callable[[], bool]:
    def read() -> bool:
        if data_dir is None:
            return False
        from . import jeff_settings
        return jeff_settings.unsolicited_enabled(data_dir)
    return read


def _default_opt_in(data_dir: Path | None, salt: bytes | None) -> Callable[[Any], bool]:
    """The participant's own «включи напоминания» (proactive prefs), resolved from the private chat id."""
    def read(chat_id: Any) -> bool:
        if data_dir is None or not salt:
            return False
        try:
            from .identity import derive_person_key
            from .j2.proactive import ProactiveStore
            key = derive_person_key(int(chat_id), salt)
            prefs = ProactiveStore(Path(data_dir) / "pit-v1.7" / "personalities").prefs(key)
        except (ValueError, TypeError, OSError):
            return False
        return prefs.get("proactive") is True and prefs.get("opted_out") is not True
    return read


class OutboundGuard:
    def __init__(self, data_dir: Path | str | None, *, salt: bytes | None = None,
                 notifier: Notifier | None = None, clock: Callable[[], float] = time.time,
                 unsolicited_switch: Callable[[], bool] | None = None,
                 opted_in: Callable[[Any], bool] | None = None):
        self.data_dir = Path(data_dir) if data_dir is not None else None
        self.home = pit_home(self.data_dir) if self.data_dir is not None else None
        self.salt = bytes(salt) if salt else None
        self.clock = clock
        self.notifier = notifier if notifier is not None else (
            PultOutboxNotifier(self.home) if self.home is not None else None)
        self.unsolicited_switch = unsolicited_switch or _default_unsolicited_switch(self.data_dir)
        self.opted_in = opted_in or _default_opt_in(self.data_dir, self.salt)
        self._memory: sqlite3.Connection | None = None
        self._pending: dict | None = None
        self._flush_handle: asyncio.TimerHandle | None = None
        self._flush_loop: asyncio.AbstractEventLoop | None = None
        with self._db() as db:
            db.executescript(
                "CREATE TABLE IF NOT EXISTS inbound(chat TEXT PRIMARY KEY, last_ts REAL NOT NULL);"
                "CREATE TABLE IF NOT EXISTS unsolicited(id INTEGER PRIMARY KEY, chat TEXT NOT NULL, ts REAL NOT NULL,"
                " allowed INTEGER NOT NULL DEFAULT 0);"
                "CREATE TABLE IF NOT EXISTS flags(key TEXT PRIMARY KEY, value TEXT NOT NULL);")

    # -- storage -------------------------------------------------------------------------------------------
    def _db(self) -> sqlite3.Connection:
        if self.home is None:
            if self._memory is None:
                self._memory = sqlite3.connect(":memory:", isolation_level=None, check_same_thread=False)
            return _Borrowed(self._memory)
        self.home.mkdir(parents=True, exist_ok=True)
        return _Owned(sqlite3.connect(self.home / DB_NAME, timeout=10, isolation_level=None))

    def chat_hash(self, chat_id: Any) -> str:
        raw = b"jeff-outbound:" + str(chat_id).encode("utf-8")
        if self.salt:
            return hmac.new(self.salt, raw, hashlib.sha256).hexdigest()[:16]
        return hashlib.sha256(raw).hexdigest()[:16]

    def _flag(self, db, key: str) -> dict | None:
        row = db.execute("SELECT value FROM flags WHERE key=?", (key,)).fetchone()
        if row is None:
            return None
        try:
            value = json.loads(row[0])
        except ValueError:
            return {"raw": row[0]}
        return value if isinstance(value, dict) else {"value": value}

    # -- inbound -------------------------------------------------------------------------------------------
    def note_inbound(self, chat_id: Any, ts: float | None = None) -> None:
        """The participant wrote (or pressed a button / paid) at ``ts`` (their Telegram ``date``)."""
        if chat_id is None or isinstance(chat_id, bool):
            return
        now = self.clock()
        when = now if ts is None else min(float(ts), now)
        with self._db() as db:
            db.execute("INSERT INTO inbound(chat, last_ts) VALUES(?, ?) ON CONFLICT(chat) DO UPDATE SET "
                       "last_ts=max(last_ts, excluded.last_ts)", (self.chat_hash(chat_id), when))

    def note_updates(self, updates: Any) -> None:
        if not isinstance(updates, list):
            return
        for update in updates:
            if not isinstance(update, dict):
                continue
            message = update.get("message")
            if isinstance(message, dict):
                chat = message.get("chat") if isinstance(message.get("chat"), dict) else {}
                date = message.get("date")
                self.note_inbound(chat.get("id"), date if isinstance(date, (int, float)) else None)
            callback = update.get("callback_query")
            if isinstance(callback, dict):
                sender = callback.get("from") if isinstance(callback.get("from"), dict) else {}
                inner = callback.get("message") if isinstance(callback.get("message"), dict) else {}
                chat = inner.get("chat") if isinstance(inner.get("chat"), dict) else {}
                self.note_inbound(chat.get("id", sender.get("id")))
            checkout = update.get("pre_checkout_query")
            if isinstance(checkout, dict) and isinstance(checkout.get("from"), dict):
                self.note_inbound(checkout["from"].get("id"))

    # -- the decision ----------------------------------------------------------------------------------------
    def decide(self, chat_id: Any, *, now: float | None = None) -> Decision:
        now = self.clock() if now is None else now
        chat = self.chat_hash(chat_id)
        with self._db() as db:
            db.execute("BEGIN IMMEDIATE")
            try:
                decision = self._decide_locked(db, chat, chat_id, now)
                db.execute("COMMIT")
            except BaseException:
                db.execute("ROLLBACK")
                raise
        return decision

    def _decide_locked(self, db, chat: str, chat_id: Any, now: float) -> Decision:
        if chat_id is None or chat_id == "":
            return Decision(False, "unsolicited", "chat_unknown")
        row = db.execute("SELECT last_ts FROM inbound WHERE chat=?", (chat,)).fetchone()
        if row is not None and now - float(row[0]) <= REPLY_WINDOW_S:
            return Decision(True, "reply", "reply_window")
        db.execute("DELETE FROM unsolicited WHERE ts < ?", (now - KEEP_ATTEMPTS_S,))
        cur = db.execute("INSERT INTO unsolicited(chat, ts, allowed) VALUES(?, ?, 0)", (chat, now))
        attempt_id = cur.lastrowid
        if self._flag(db, TRIP_FLAG) is not None:
            return Decision(False, "unsolicited", TRIP_FLAG)
        distinct = db.execute("SELECT count(DISTINCT chat) FROM unsolicited WHERE ts >= ?",
                              (now - FANOUT_WINDOW_S,)).fetchone()[0]
        if distinct >= FANOUT_CHATS:
            db.execute("INSERT OR REPLACE INTO flags(key, value) VALUES(?, ?)",
                       (TRIP_FLAG, json.dumps({"at": _iso(now), "ts": now, "chats": int(distinct)})))
            return Decision(False, "unsolicited", "fanout_breaker")
        try:
            switch_on = bool(self.unsolicited_switch())
        except Exception:                                  # noqa: BLE001 - an unreadable switch is OFF
            switch_on = False
        if not switch_on:
            return Decision(False, "unsolicited", "global_switch_off")
        try:
            opted = bool(self.opted_in(chat_id))
        except Exception:                                  # noqa: BLE001 - unknown consent is no consent
            opted = False
        if not opted:
            return Decision(False, "unsolicited", "not_opted_in")
        sent = db.execute("SELECT count(*) FROM unsolicited WHERE chat=? AND allowed=1 AND ts >= ?",
                          (chat, now - UNSOLICITED_WINDOW_S)).fetchone()[0]
        if sent >= UNSOLICITED_PER_CHAT:
            return Decision(False, "unsolicited", "rate_24h")
        db.execute("UPDATE unsolicited SET allowed=1 WHERE id=?", (attempt_id,))
        return Decision(True, "unsolicited", "opted_in_reminder")

    # -- one attempt, end to end ----------------------------------------------------------------------------
    def check(self, method: str, chat_id: Any, text: str = "") -> Decision:
        """Decide, audit, notify; raise ``OutboundBlocked`` when refused."""
        now = self.clock()
        try:
            decision = self.decide(chat_id, now=now)
        except OutboundBlocked:
            raise
        except Exception as exc:                           # noqa: BLE001 - a broken guard never lets a message out
            decision = Decision(False, "unsolicited", "guard_error:" + type(exc).__name__)
        context = OUTBOUND_CONTEXT.get()
        row = {"at": _iso(now), "ts": round(now, 3), "chat": self.chat_hash(chat_id), "method": method,
               "kind": decision.kind, "decision": "allowed" if decision.allowed else "blocked",
               "reason": decision.reason, "origin": context.get("origin", ""), "model": context.get("model", ""),
               "provider": context.get("provider", ""), "text80": _excerpt(text), "schema": SCHEMA}
        self._audit(row)
        if not decision.allowed:
            self._queue_notice(row)
            raise OutboundBlocked(decision.reason)
        return decision

    def _audit(self, row: dict) -> None:
        if self.home is None:
            return
        try:
            from .vault import _append_jsonl
            _append_jsonl(self.home / "logs" / AUDIT_NAME, row)
        except OSError:
            log.warning("outbound guard audit write failed")

    # -- owner notices (coalesced) ------------------------------------------------------------------------
    def _queue_notice(self, row: dict) -> None:
        pending = self._pending
        if pending is None:
            pending = self._pending = {"first_at": row["at"], "blocked": 0, "chats": set(), "reasons": {},
                                       "sample": row["text80"], "model": row["model"],
                                       "provider": row["provider"], "origin": row["origin"], "tripped": False}
        pending["blocked"] += 1
        pending["chats"].add(row["chat"])
        pending["reasons"][row["reason"]] = pending["reasons"].get(row["reason"], 0) + 1
        if row["reason"] == "fanout_breaker":
            pending["tripped"] = True
        self._schedule_flush()

    def _cooldown_left(self) -> float:
        with self._db() as db:
            last = self._flag(db, "last_notice")
        if not last:
            return 0.0
        return max(0.0, float(last.get("ts", 0)) + NOTICE_COOLDOWN_S - self.clock())

    def _schedule_flush(self) -> None:
        try:
            loop = asyncio.get_running_loop()
        except RuntimeError:
            return                                      # no loop: the caller flushes (tests, CLI)
        delay = NOTICE_DEBOUNCE_S if self._pending and self._pending["tripped"] else max(
            NOTICE_DEBOUNCE_S, self._cooldown_left())
        if self._flush_handle is not None and self._flush_loop is loop and not self._flush_handle.cancelled():
            if not self._pending or not self._pending["tripped"]:
                return
            self._flush_handle.cancel()
        self._flush_loop = loop
        self._flush_handle = loop.call_later(delay, lambda: asyncio.ensure_future(self.flush_notices()))

    async def flush_notices(self, *, force: bool = False) -> dict | None:
        """Send the coalesced notice. Respects the cooldown unless the breaker newly tripped (or ``force``)."""
        if self._flush_handle is not None:
            self._flush_handle.cancel()
            self._flush_handle = None
        pending = self._pending
        if pending is None:
            return None
        if not (force or pending["tripped"]) and self._cooldown_left() > 0:
            self._schedule_flush()
            return None
        self._pending = None
        now = self.clock()
        notice = {"id": f"jg-{int(now)}-{hashlib.sha256(repr((now, pending['first_at'])).encode()).hexdigest()[:8]}",
                  "at": _iso(now), "kind": "jeff_outbound_guard", "blocked": pending["blocked"],
                  "chats": len(pending["chats"]), "reasons": dict(pending["reasons"]),
                  "tripped": pending["tripped"] or self.tripped() is not None, "sample": pending["sample"],
                  "model": pending["model"], "provider": pending["provider"], "origin": pending["origin"]}
        notice["text"] = render_notice(notice)
        with self._db() as db:
            db.execute("INSERT OR REPLACE INTO flags(key, value) VALUES('last_notice', ?)",
                       (json.dumps({"ts": now, "id": notice["id"]}),))
        if self.notifier is not None:
            try:
                result = self.notifier(notice)
                if inspect.isawaitable(result):
                    await result
            except Exception as exc:                    # noqa: BLE001 - the audit already holds every attempt
                log.warning("outbound guard notice failed: %s", type(exc).__name__)
        return notice

    # -- owner controls -----------------------------------------------------------------------------------
    def tripped(self) -> dict | None:
        with self._db() as db:
            return self._flag(db, TRIP_FLAG)

    def clear_trip(self) -> bool:
        """OWNER action: clear ``broadcast_tripped`` and forget the blocked attempts that tripped it."""
        with self._db() as db:
            had = db.execute("DELETE FROM flags WHERE key=?", (TRIP_FLAG,)).rowcount
            db.execute("DELETE FROM unsolicited WHERE allowed=0")
        return bool(had)

    def status(self) -> dict:
        now = self.clock()
        with self._db() as db:
            recent = db.execute("SELECT count(*), count(DISTINCT chat) FROM unsolicited WHERE ts >= ?",
                                (now - UNSOLICITED_WINDOW_S,)).fetchone()
            trip = self._flag(db, TRIP_FLAG)
        try:
            switch_on = bool(self.unsolicited_switch())
        except Exception:                                  # noqa: BLE001
            switch_on = False
        return {"schema": SCHEMA, "jeff_unsolicited_enabled": switch_on, "broadcast_tripped": trip,
                "unsolicited_attempts_24h": int(recent[0]), "unsolicited_chats_24h": int(recent[1])}

    # -- httpx hooks --------------------------------------------------------------------------------------
    async def on_request(self, request: httpx.Request) -> None:
        method = _bot_method(request.url.path)
        if method is None or method not in MESSAGE_METHODS:
            return
        fields = await _request_fields(request)
        self.check(method, fields.get("chat_id"), fields.get("text") or fields.get("caption") or "")

    async def on_response(self, response: httpx.Response) -> None:
        if _bot_method(response.request.url.path) != "getUpdates" or response.status_code != 200:
            return
        try:
            await response.aread()
            body = json.loads(response.content)
        except Exception:                                  # noqa: BLE001 - the caller reports a bad body itself
            return
        if isinstance(body, dict) and body.get("ok") is True:
            try:
                self.note_updates(body.get("result"))
            except Exception:                              # noqa: BLE001 - never stops the poller
                log.warning("outbound guard could not record inbound updates")


class _Owned:
    """A connection used for one block and closed after it."""

    def __init__(self, db: sqlite3.Connection):
        self.db = db

    def __enter__(self):
        return self.db

    def __exit__(self, *exc):
        self.db.close()
        return False


class _Borrowed(_Owned):
    def __exit__(self, *exc):
        return False


def _bot_method(path: str) -> str | None:
    if not path.startswith("/bot"):
        return None                                      # /file/bot... downloads and anything else
    return path.rstrip("/").rsplit("/", 1)[-1] or None


async def _request_fields(request: httpx.Request) -> dict:
    fields: dict[str, Any] = {k: v for k, v in request.url.params.items()}
    try:
        content = await request.aread()
    except Exception:                                      # noqa: BLE001
        content = b""
    ctype = request.headers.get("content-type", "").lower()
    try:
        if "application/json" in ctype and content:
            body = json.loads(content)
            if isinstance(body, dict):
                fields.update(body)
        elif "application/x-www-form-urlencoded" in ctype:
            fields.update({k: v[0] for k, v in parse_qs(content.decode("utf-8", "replace")).items() if v})
        elif "multipart/form-data" in ctype:
            match = re.search(r'boundary="?([^";]+)"?', request.headers.get("content-type", ""))
            if match:
                fields.update(_multipart_text_fields(content, match.group(1).encode("latin-1")))
    except (ValueError, UnicodeError):
        pass
    return fields


def _multipart_text_fields(content: bytes, boundary: bytes) -> dict[str, str]:
    out: dict[str, str] = {}
    for part in content.split(b"--" + boundary):
        head, sep, body = part.partition(b"\r\n\r\n")
        if not sep:
            continue
        header = head.decode("utf-8", "replace")
        name = re.search(r'name="([^"]*)"', header)
        if not name or "filename=" in header:
            continue
        out[name.group(1)] = body[:-2].decode("utf-8", "replace") if body.endswith(b"\r\n") else \
            body.decode("utf-8", "replace")
    return out


_REASON_RU = {"global_switch_off": "выключатель jeff_unsolicited_enabled выключен",
              "not_opted_in": "участник не включал напоминания", "rate_24h": "лимит 1 в сутки на чат",
              "fanout_breaker": "веерная рассылка (3+ чата за 10 минут)",
              TRIP_FLAG: "предохранитель рассылки уже сработал", "chat_unknown": "чат не определён"}


def render_notice(notice: dict) -> str:
    reasons = ", ".join(f"{_REASON_RU.get(k.split(':')[0], k)}: {v}" for k, v in notice["reasons"].items())
    lines = [f"🛑 Jeff пытался написать первым: заблокировано {notice['blocked']} сообщ. в {notice['chats']} чат(ов).",
             "Причины: " + reasons + "."]
    source = " / ".join(x for x in (notice.get("origin"), notice.get("model"), notice.get("provider")) if x)
    if source:
        lines.append("Источник: " + source + ".")
    if notice.get("sample"):
        lines.append("Начало текста: «" + notice["sample"] + "»")
    if notice.get("tripped"):
        lines.append("Предохранитель рассылки ВКЛЮЧЁН: всё, что Jeff пишет первым, заблокировано, пока владелец "
                     "его не снимет (python -m bcc.pit.outbound_guard clear-trip). Ответы на сообщения работают.")
    return "\n".join(lines)


class PultOutboxNotifier:
    """Production notifier: the owner Pult drains ``<pit>/insights/pult-outbox.jsonl`` (owner only), the same
    outbox as Jeff's weekly digest (``bcc.pit.j2.insights.pending_pult_items``). No second Telegram sender."""

    def __init__(self, home: Path):
        self.path = Path(home) / "insights" / "pult-outbox.jsonl"

    def __call__(self, notice: dict) -> None:
        from .vault import _append_jsonl
        _append_jsonl(self.path, {"id": notice["id"], "at": notice["at"], "kind": "jeff_outbound_guard",
                                  "text": notice["text"][:3500]})


# -- installation ---------------------------------------------------------------------------------------------
def guard_for_settings(settings: Any, *, notifier: Notifier | None = None) -> OutboundGuard:
    data_dir = getattr(settings, "data_dir", None)
    salt_hex = getattr(settings, "identity_salt", "") or ""
    try:
        salt = bytes.fromhex(salt_hex) if isinstance(salt_hex, str) else bytes(salt_hex)
    except ValueError:
        salt = None
    return OutboundGuard(data_dir, salt=salt or None, notifier=notifier)


def install(telegram: Any, guard: OutboundGuard) -> OutboundGuard | None:
    """Hook ``guard`` into a Jeff Telegram adapter's httpx client. Idempotent; a transport without an httpx client
    (the Jeff window, the call surface, test fakes) cannot reach Telegram and is left alone."""
    client = getattr(telegram, "client", None)
    if not isinstance(client, httpx.AsyncClient):
        return None
    existing = getattr(client, "_jeff_outbound_guard", None)
    if existing is not None:
        return existing
    hooks = client.event_hooks
    client.event_hooks = {"request": [*hooks.get("request", []), guard.on_request],
                          "response": [*hooks.get("response", []), guard.on_response]}
    client._jeff_outbound_guard = guard
    telegram.outbound_guard = guard
    return guard


def guard_of(telegram: Any) -> OutboundGuard | None:
    return getattr(getattr(telegram, "client", None), "_jeff_outbound_guard", None)


def ensure(telegram: Any, settings: Any, *, existing: OutboundGuard | None = None,
           notifier: Notifier | None = None) -> OutboundGuard | None:
    """The guard on ``telegram`` (installing ``existing`` or a new one for ``settings``); None when the transport
    has no httpx client and therefore cannot reach Telegram."""
    found = guard_of(telegram)
    if found is not None:
        return found
    if not isinstance(getattr(telegram, "client", None), httpx.AsyncClient):
        return None
    return install(telegram, existing or guard_for_settings(settings, notifier=notifier))


def clear_broadcast_trip(data_dir: Path | str) -> bool:
    return OutboundGuard(data_dir, notifier=lambda notice: None).clear_trip()


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="python -m bcc.pit.outbound_guard",
                                     description="Jeff outbound guard: status and owner controls")
    parser.add_argument("action", choices=("status", "clear-trip", "enable-unsolicited", "disable-unsolicited"))
    parser.add_argument("--data-dir", required=True)
    args = parser.parse_args(argv)
    data_dir = Path(args.data_dir)
    if args.action in ("enable-unsolicited", "disable-unsolicited"):
        from . import jeff_settings
        jeff_settings.set_unsolicited_enabled(data_dir, args.action == "enable-unsolicited")
    if args.action == "clear-trip":
        print("cleared" if clear_broadcast_trip(data_dir) else "not tripped")
    print(json.dumps(OutboundGuard(data_dir, notifier=lambda notice: None).status(), ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
