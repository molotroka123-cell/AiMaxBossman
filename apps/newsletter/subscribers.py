"""Subscriber store: the single source of truth for consent (SQLite, no external account needed).

States: pending -> confirmed -> unsubscribed | bounced | complained. Only `confirmed` and not suppressed can be mailed.
Rules enforced here:
  - one row per normalised address (case and surrounding spaces; Gmail dots and +tags are NOT merged: on other
    providers they are different mailboxes, and guessing wrong would silently drop a real subscriber);
  - double opt-in: subscribe() only creates `pending` and returns a signed token; confirm() needs that token;
  - consent evidence is stored: source, timestamps, policy version, and every state change is an event row;
  - unsubscribe, hard bounce and complaint put the address on the suppression list. After an unsubscribe the person
    may subscribe and confirm again; after a hard bounce or a complaint the address is never re-added;
  - tokens are HMAC-SHA256 over subscriber id + purpose with a secret from NEWSLETTER_SECRET (never stored in git).
"""
from __future__ import annotations

import hashlib
import hmac
import json
import os
import re
import sqlite3
import time
import uuid

LANGS = ("en", "ru", "cz")
INTERESTS = ("bitcoin", "ethereum", "altcoins", "defi", "regulation", "technology")
STATES = ("pending", "confirmed", "unsubscribed", "bounced", "complained")
EMAIL_RE = re.compile(r"^[^@\s]{1,64}@[^@\s]{1,255}\.[^@\s.]{2,}$")
SCHEMA = """
CREATE TABLE IF NOT EXISTS subscribers(
  id TEXT PRIMARY KEY, email TEXT NOT NULL UNIQUE, language TEXT NOT NULL, interests TEXT NOT NULL,
  state TEXT NOT NULL, consent_source TEXT NOT NULL, consent_ts INTEGER NOT NULL, confirm_ts INTEGER,
  policy_version TEXT NOT NULL, updated_ts INTEGER NOT NULL);
CREATE TABLE IF NOT EXISTS events(
  id INTEGER PRIMARY KEY AUTOINCREMENT, subscriber_id TEXT NOT NULL, type TEXT NOT NULL, campaign TEXT,
  ts INTEGER NOT NULL, meta TEXT);
CREATE TABLE IF NOT EXISTS suppression(email TEXT PRIMARY KEY, reason TEXT NOT NULL, ts INTEGER NOT NULL);
"""


class ConsentError(ValueError):
    pass


def normalise(email: str) -> str:
    e = str(email or "").strip().lower()
    if not EMAIL_RE.match(e):
        raise ConsentError("invalid email address")
    return e


class Store:
    def __init__(self, path: str, secret: str | None = None):
        secret = secret or os.environ.get("NEWSLETTER_SECRET")
        if not secret or len(secret) < 16:
            raise ConsentError("NEWSLETTER_SECRET (16+ chars) is required to sign confirmation and unsubscribe tokens")
        self._key = secret.encode()
        self.db = sqlite3.connect(path)
        self.db.row_factory = sqlite3.Row
        self.db.executescript(SCHEMA)

    # ---- tokens
    def token(self, sid: str, purpose: str) -> str:
        sig = hmac.new(self._key, f"{purpose}:{sid}".encode(), hashlib.sha256).hexdigest()[:32]
        return f"{sid}.{sig}"

    def _check(self, token: str, purpose: str) -> str:
        sid, _, sig = str(token or "").partition(".")
        good = hmac.new(self._key, f"{purpose}:{sid}".encode(), hashlib.sha256).hexdigest()[:32]
        if not sid or not hmac.compare_digest(sig, good):
            raise ConsentError("invalid token")
        return sid

    def _event(self, sid: str, typ: str, campaign: str | None = None, meta: dict | None = None):
        self.db.execute("INSERT INTO events(subscriber_id,type,campaign,ts,meta) VALUES(?,?,?,?,?)",
                        (sid, typ, campaign, int(time.time()), json.dumps(meta or {})))

    def _set(self, sid: str, state: str, confirm_ts: int | None = None):
        if confirm_ts is None:
            self.db.execute("UPDATE subscribers SET state=?, updated_ts=? WHERE id=?", (state, int(time.time()), sid))
        else:
            self.db.execute("UPDATE subscribers SET state=?, updated_ts=?, confirm_ts=? WHERE id=?",
                            (state, int(time.time()), confirm_ts, sid))
        self._event(sid, "state:" + state)

    # ---- consent flow
    def subscribe(self, email: str, language: str, interests: list[str], source: str, policy_version: str) -> dict:
        e = normalise(email)
        if language not in LANGS:
            raise ConsentError(f"language must be one of {LANGS}")
        bad = [i for i in interests if i not in INTERESTS]
        if bad:
            raise ConsentError(f"unknown interests {bad}")
        if not source or not policy_version:
            raise ConsentError("consent source and policy version are required")
        sup = self.db.execute("SELECT reason FROM suppression WHERE email=?", (e,)).fetchone()
        if sup and sup["reason"] in ("bounced", "complained"):
            raise ConsentError(f"address is suppressed ({sup['reason']})")
        row = self.db.execute("SELECT * FROM subscribers WHERE email=?", (e,)).fetchone()
        now = int(time.time())
        ints = json.dumps(sorted(set(interests)))
        if row and row["state"] == "confirmed":
            return {"id": row["id"], "state": "confirmed", "duplicate": True, "confirm_token": None}
        if row:  # still pending, or returning after an unsubscribe: this is new consent and must be confirmed again
            sid = row["id"]
            self.db.execute(
                "UPDATE subscribers SET language=?, interests=?, consent_source=?, consent_ts=?, policy_version=?, confirm_ts=NULL WHERE id=?",
                (language, ints, source, now, policy_version, sid))
            if row["state"] != "pending":
                self._set(sid, "pending")
        else:
            sid = uuid.uuid4().hex
            self.db.execute("INSERT INTO subscribers VALUES(?,?,?,?,?,?,?,?,?,?)",
                            (sid, e, language, ints, "pending", source, now, None, policy_version, now))
            self._event(sid, "state:pending", meta={"source": source})
        self.db.commit()
        return {"id": sid, "state": "pending", "duplicate": bool(row), "confirm_token": self.token(sid, "confirm")}

    def confirm(self, token: str) -> str:
        sid = self._check(token, "confirm")
        row = self.db.execute("SELECT * FROM subscribers WHERE id=?", (sid,)).fetchone()
        if not row or row["state"] not in ("pending", "confirmed"):
            raise ConsentError("nothing to confirm")
        if row["state"] == "pending":
            self.db.execute("DELETE FROM suppression WHERE email=? AND reason=?", (row["email"], "unsubscribed"))
            self._set(sid, "confirmed", confirm_ts=int(time.time()))
            self.db.commit()
        return sid

    def unsubscribe(self, token: str) -> str:
        return self._suppress(self._check(token, "unsubscribe"), "unsubscribed")

    def record_bounce(self, email: str, hard: bool, campaign: str | None = None) -> str | None:
        row = self.db.execute("SELECT id FROM subscribers WHERE email=?", (normalise(email),)).fetchone()
        if not row:
            return None
        self._event(row["id"], "bounce:hard" if hard else "bounce:soft", campaign)
        self.db.commit()
        return self._suppress(row["id"], "bounced") if hard else row["id"]

    def record_complaint(self, email: str, campaign: str | None = None) -> str | None:
        row = self.db.execute("SELECT id FROM subscribers WHERE email=?", (normalise(email),)).fetchone()
        return self._suppress(row["id"], "complained") if row else None

    def _suppress(self, sid: str, reason: str) -> str:
        row = self.db.execute("SELECT email FROM subscribers WHERE id=?", (sid,)).fetchone()
        if not row:
            raise ConsentError("unknown subscriber")
        self.db.execute("INSERT OR REPLACE INTO suppression VALUES(?,?,?)", (row["email"], reason, int(time.time())))
        self._set(sid, reason)
        self.db.commit()
        return sid

    def record_delivery(self, sid: str, typ: str, campaign: str):
        if typ not in ("sent", "delivered", "click"):
            raise ConsentError("event type must be sent, delivered or click")
        self._event(sid, typ, campaign)
        self.db.commit()

    # ---- reading
    def segment(self, language: str | None = None, interest: str | None = None) -> list[dict]:
        """Only confirmed, non-suppressed subscribers; each row carries its own unsubscribe token."""
        rows = self.db.execute(
            "SELECT s.* FROM subscribers s LEFT JOIN suppression x ON x.email=s.email "
            "WHERE s.state=? AND x.email IS NULL", ("confirmed",)).fetchall()
        out = []
        for r in rows:
            ints = json.loads(r["interests"])
            if (language is None or r["language"] == language) and (interest is None or interest in ints):
                out.append({"id": r["id"], "email": r["email"], "language": r["language"], "interests": ints,
                            "unsubscribe_token": self.token(r["id"], "unsubscribe")})
        return out

    def counts(self) -> dict:
        c = {s: 0 for s in STATES}
        for r in self.db.execute("SELECT state, COUNT(*) n FROM subscribers GROUP BY state"):
            c[r["state"]] = r["n"]
        c["suppressed"] = self.db.execute("SELECT COUNT(*) FROM suppression").fetchone()[0]
        return c

    def backup(self, path: str) -> int:
        dst = sqlite3.connect(path)
        with dst:
            self.db.backup(dst)
        n = dst.execute("SELECT COUNT(*) FROM subscribers").fetchone()[0]
        dst.close()
        return n
