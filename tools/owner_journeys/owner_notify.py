"""Owner notifications for the 24/7 learning loop — ONLY to the owner's control bot («Пульт»).

The пульт is the Telegram companion bot (``bcc/telegram_companion``). This module
reuses its local configuration and never touches the participant (Jeff/PIT) bot:

* token  : ``TG_COMPANION_BOT_TOKEN`` (environment, else ``companion.env``) — the
           companion's own variable; no other token is ever read;
* chat   : the ``people`` entry with ``role == "owner"`` in the companion ``config.json``;
* method : ``sendMessage`` only (never ``getUpdates`` — the companion is the only poller).

A durable outbox (``<state>/notify/outbox.json``) makes delivery survive network
loss and restarts. Every message passes: secret-pattern refusal (and refusal of
any known secret value), key-based coalescing, same-text dedup window, a minimum
interval between routine messages, and a hard per-day ceiling.
"""
from __future__ import annotations

import hashlib
import json
import os
import re
import time
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Optional, Protocol

COMPANION_DIR = Path(os.environ.get("LOCALAPPDATA", str(Path.home()))) / "Bossman" / "telegram-companion"
TELEGRAM_API = "https://api.telegram.org"
SECRET_PATTERNS = [
    re.compile(r"sk-or-[A-Za-z0-9_-]{10,}"),
    re.compile(r"sk-(?:ant|proj)?-?[A-Za-z0-9_-]{20,}"),
    re.compile(r"\b\d{8,10}:[A-Za-z0-9_-]{30,}"),                 # Telegram bot token
    re.compile(r"gh[pousr]_[A-Za-z0-9]{20,}|github_pat_[A-Za-z0-9_]{20,}"),
    re.compile(r"AKIA[0-9A-Z]{16}"),
    re.compile(r"(?i)\b(?:api[_-]?key|token|password|secret)\s*[=:]\s*\S{8,}"),
    re.compile(r"-----BEGIN [A-Z ]*PRIVATE KEY-----"),
]
MAX_TEXT = 3800


class NotifyError(Exception):
    pass


class Transport(Protocol):
    def send(self, text: str) -> dict[str, Any]:
        """Return {"ok": bool, "message_id": int|None, "retry_after": float|None, "permanent": bool}."""


def contains_secret(text: str, known: tuple[str, ...] = ()) -> bool:
    if any(p.search(text) for p in SECRET_PATTERNS):
        return True
    return any(s and len(s) >= 8 and s in text for s in known)


# ------------------------------------------------------------------ real transport (пульт)

def _env_value(path: Path, key: str) -> str:
    if os.environ.get(key, "").strip():
        return os.environ[key].strip()
    if path.is_file():
        for line in path.read_text(encoding="utf-8").splitlines():
            if line.strip().startswith(key + "="):
                return line.split("=", 1)[1].strip().strip('"')
    return ""


def companion_owner(companion_dir: Optional[Path] = None) -> tuple[str, Optional[int]]:
    """(bot token, owner chat id) of the пульт. Never logged."""
    companion_dir = Path(companion_dir or COMPANION_DIR)
    token = _env_value(companion_dir / "companion.env", "TG_COMPANION_BOT_TOKEN")
    chat_id = None
    cfg = companion_dir / "config.json"
    if cfg.is_file():
        data = json.loads(cfg.read_text(encoding="utf-8"))
        owners = [p for p in data.get("people", []) if isinstance(p, dict) and p.get("role") == "owner"]
        if owners and type(owners[0].get("chat_id")) is int:
            chat_id = owners[0]["chat_id"]
    return token, chat_id


class CompanionTelegram:
    """sendMessage through the companion («Пульт») bot to the owner's own chat."""

    name = "telegram-companion"

    def __init__(self, companion_dir: Optional[Path] = None, timeout: float = 20.0):
        self.companion_dir = Path(companion_dir or COMPANION_DIR)
        self.timeout = timeout

    def known_secrets(self) -> tuple[str, ...]:
        token, _ = companion_owner(self.companion_dir)
        core = _env_value(self.companion_dir / "companion.env", "TG_COMPANION_CORE_TOKEN")
        return tuple(s for s in (token, core) if s)

    def send(self, text: str) -> dict[str, Any]:
        token, chat_id = companion_owner(self.companion_dir)
        if not token or not chat_id:
            return {"ok": False, "permanent": False, "error": "COMPANION_CONFIG_INCOMPLETE"}
        data = urllib.parse.urlencode({"chat_id": chat_id, "text": text,
                                       "disable_web_page_preview": "true"}).encode()
        req = urllib.request.Request(f"{TELEGRAM_API}/bot{token}/sendMessage", data=data)
        try:
            with urllib.request.urlopen(req, timeout=self.timeout) as resp:  # noqa: S310 - fixed Telegram host
                body = json.loads(resp.read().decode())
        except urllib.error.HTTPError as exc:
            try:
                body = json.loads(exc.read().decode())
            except (ValueError, OSError):
                body = {}
            retry = ((body.get("parameters") or {}).get("retry_after") if isinstance(body, dict) else None)
            return {"ok": False, "retry_after": retry, "permanent": exc.code in (400, 401, 403, 404),
                    "error": f"HTTP_{exc.code}"}
        except (OSError, ValueError) as exc:  # network down / DNS / timeout -> stays queued
            return {"ok": False, "permanent": False, "error": type(exc).__name__}
        ok = bool(body.get("ok"))
        return {"ok": ok, "message_id": (body.get("result") or {}).get("message_id") if ok else None,
                "permanent": not ok, "error": None if ok else "TELEGRAM_NOT_OK"}


class NullTransport:
    """Reporting switched off (tests, readiness live-tests): nothing leaves the machine."""

    name = "off"

    def known_secrets(self) -> tuple[str, ...]:
        return ()

    def send(self, text: str) -> dict[str, Any]:
        return {"ok": False, "permanent": True, "error": "REPORTING_OFF"}


# ------------------------------------------------------------------ durable outbox

@dataclass
class Limits:
    min_interval_s: float = 600.0      # between routine messages
    urgent_min_interval_s: float = 30.0
    max_per_day: int = 12
    dedup_window_s: float = 6 * 3600.0  # the same text is not sent twice within this window
    max_attempts: int = 50
    max_pending: int = 30


class FileLock:
    def __init__(self, path: Path, stale_s: float = 60.0):
        self.path, self.stale_s = path, stale_s

    def __enter__(self):
        deadline = time.time() + 10
        while True:
            try:
                fd = os.open(self.path, os.O_CREAT | os.O_EXCL | os.O_WRONLY)
                os.write(fd, str(os.getpid()).encode())
                os.close(fd)
                return self
            except FileExistsError:
                try:
                    if time.time() - self.path.stat().st_mtime > self.stale_s:
                        self.path.unlink(missing_ok=True)
                        continue
                except OSError:
                    continue
                if time.time() > deadline:
                    raise NotifyError("outbox lock busy")
                time.sleep(0.05)

    def __exit__(self, *exc):
        self.path.unlink(missing_ok=True)


def _day(ts: float) -> str:
    return datetime.fromtimestamp(ts, timezone.utc).strftime("%Y-%m-%d")


def _sha(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()[:16]


class Notifier:
    def __init__(self, state_dir: Path, transport: Transport, limits: Optional[Limits] = None,
                 clock: Callable[[], float] = time.time):
        self.dir = Path(state_dir) / "notify"
        self.dir.mkdir(parents=True, exist_ok=True)
        self.path = self.dir / "outbox.json"
        self.log = self.dir / "sent.jsonl"
        self.transport = transport
        self.limits = limits or Limits()
        self.clock = clock
        self._known: tuple[str, ...] = ()
        try:
            self._known = tuple(getattr(transport, "known_secrets", lambda: ())())
        except (OSError, ValueError):
            self._known = ()

    # -- storage
    def _read(self) -> dict[str, Any]:
        try:
            return json.loads(self.path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return {"pending": [], "recent": [], "per_day": {}, "last_sent_ts": 0.0, "refused": 0, "seq": 0}

    def _write(self, data: dict[str, Any]) -> None:
        tmp = self.path.with_suffix(".tmp")
        tmp.write_text(json.dumps(data, ensure_ascii=False, indent=1), encoding="utf-8")
        os.replace(tmp, self.path)

    def _journal(self, rec: dict[str, Any]) -> None:
        with self.log.open("a", encoding="utf-8") as fh:
            fh.write(json.dumps(rec, ensure_ascii=False) + "\n")

    def status(self) -> dict[str, Any]:
        d = self._read()
        return {"pending": len(d["pending"]), "sent_today": d["per_day"].get(_day(self.clock()), 0),
                "last_sent_ts": d["last_sent_ts"], "refused": d["refused"], "transport": self.transport.name}

    # -- API
    def enqueue(self, key: str, text: str, *, urgent: bool = False) -> str:
        """Queue one message. Returns QUEUED | REFUSED_SECRET | DEDUP | EMPTY."""
        text = (text or "").strip()[:MAX_TEXT]
        if not text:
            return "EMPTY"
        now = self.clock()
        with FileLock(self.dir / "outbox.lock"):
            d = self._read()
            if contains_secret(text, self._known):
                d["refused"] += 1
                self._write(d)
                self._journal({"ts": now, "key": key, "status": "REFUSED_SECRET"})
                return "REFUSED_SECRET"
            sha = _sha(text)
            if any(r["sha"] == sha and now - r["ts"] < self.limits.dedup_window_s for r in d["recent"]):
                return "DEDUP"
            d["pending"] = [p for p in d["pending"] if p["key"] != key]      # coalesce: newest wins
            d["seq"] += 1
            d["pending"].append({"id": d["seq"], "key": key, "text": text, "sha": sha, "urgent": bool(urgent),
                                 "created": now, "attempts": 0, "next_try": 0.0})
            d["pending"] = sorted(d["pending"], key=lambda p: (not p["urgent"], p["id"]))[-self.limits.max_pending:]
            self._write(d)
        return "QUEUED"

    def flush(self) -> list[dict[str, Any]]:
        """Try to deliver due messages; network failures keep them queued with backoff."""
        now = self.clock()
        out: list[dict[str, Any]] = []
        with FileLock(self.dir / "outbox.lock"):
            d = self._read()
            keep = []
            for p in sorted(d["pending"], key=lambda p: (not p["urgent"], p["id"])):
                today = d["per_day"].get(_day(now), 0)
                gap = self.limits.urgent_min_interval_s if p["urgent"] else self.limits.min_interval_s
                if (p["next_try"] > now or today >= self.limits.max_per_day
                        or now - d["last_sent_ts"] < gap):
                    keep.append(p)
                    continue
                res = self.transport.send(p["text"])
                p["attempts"] += 1
                if res.get("ok"):
                    d["last_sent_ts"] = now
                    d["per_day"][_day(now)] = today + 1
                    d["recent"] = (d["recent"] + [{"sha": p["sha"], "ts": now}])[-200:]
                    rec = {"ts": now, "key": p["key"], "status": "SENT", "message_id": res.get("message_id"),
                           "transport": self.transport.name, "chars": len(p["text"])}
                    self._journal(rec)
                    out.append(rec)
                    continue
                if res.get("permanent") and (p["attempts"] >= 3 or res.get("error") == "REPORTING_OFF"):
                    rec = {"ts": now, "key": p["key"], "status": "DROPPED", "error": res.get("error"),
                           "transport": self.transport.name}
                    self._journal(rec)
                    out.append(rec)
                    continue
                if p["attempts"] >= self.limits.max_attempts:
                    self._journal({"ts": now, "key": p["key"], "status": "GAVE_UP", "error": res.get("error")})
                    continue
                backoff = float(res.get("retry_after") or min(3600.0, 30.0 * 2 ** min(p["attempts"], 7)))
                p["next_try"] = now + backoff
                out.append({"ts": now, "key": p["key"], "status": "RETRY_LATER", "error": res.get("error"),
                            "in_s": backoff})
                keep.append(p)
            d["pending"] = keep
            d["per_day"] = {k: v for k, v in d["per_day"].items() if k >= _day(now - 8 * 86400)}
            self._write(d)
        return out


def make_transport(kind: str, companion_dir: Optional[Path] = None):
    if kind == "telegram":
        return CompanionTelegram(companion_dir)
    if kind == "off":
        return NullTransport()
    raise ValueError(f"unknown report transport: {kind}")
