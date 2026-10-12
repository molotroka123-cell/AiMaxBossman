"""Telegram Stars billing ledger for Jeff's paid document assistant.

One sqlite file next to Jeff's other stores (``<data_dir>/pit-v1.7/stars-ledger.sqlite3``); no second core, no
network. It answers four questions and nothing else:

* is this invoice payload a real, open order for exactly this user and amount (``pre_checkout``)?
* has this ``telegram_payment_charge_id`` already been credited (idempotent ``successful_payment``)?
* what may this person still use right now (trial, paid requests, document slots, validity)?
* what came in, what was refunded, what was actually withdrawn and what was spent (the owner's report)?

Design rules (see docs/owner/JEFF_STARS_PAID_DOCS_20261012_RU.md):

* Access is granted ONLY from a recorded ``successful_payment``; ``pre_checkout`` and invoices grant nothing.
* ``charge_id`` is the primary key of the payment: a duplicate event can never credit twice, also after a restart.
* A refund revokes the unused rest of that payment's grant and is idempotent as well.
* The ledger is financial record-keeping. It is deliberately NOT inside the participant vault: ``/delete_me`` removes
  documents and memory, never the proof that a payment was received or refunded.
* Stars are not money. Gross, refunded, test-environment, withdrawn and spent are kept apart; ``report`` never
  computes a "profit".
"""
from __future__ import annotations

import secrets
import sqlite3
import threading
import time
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path

DAY = 86400.0


@dataclass(frozen=True)
class Package:
    id: str
    title: str
    description: str
    stars: int
    days: int
    requests: int
    doc_slots: int


#: The one paid package. Price is an experimental hypothesis (cost per user is measured, see the owner doc).
DOCS30 = Package(
    id="docs30",
    title="Документы: 30 дней",
    description="150 запросов к вашим документам (вопросы с цитатами, сводки, черновики), до 25 документов, 30 дней.",
    stars=199, days=30, requests=150, doc_slots=25)

TRIAL_REQUESTS = 3          # free, once per person
TRIAL_DOCS = 2
DAILY_CAP_PAID = 60         # one person cannot burn the whole package (or the machine) in an hour
ORDER_TTL_SECONDS = 3600.0
MAX_VOIDS_PER_DAY = 20      # a request given back (no model answer) is free only this often per day
MAX_OPEN_TICKETS = 5        # per person
MAX_PENDING_ORDERS = 10     # per person, counted over UNEXPIRED orders only
STALE_USAGE_SECONDS = 900.0  # a request still unsettled after this long belongs to a crashed turn
MAX_USD = 1e9
MAX_STARS = 10 ** 12
MAX_REJECTED_ROWS = 500     # the rejected-events journal keeps the newest rows only
MAX_CHARGE_ID_CHARS = 256   # a real Telegram charge id is ~60 characters; longer is not stored as a tombstone

_SCHEMA = """
CREATE TABLE IF NOT EXISTS orders(
  payload TEXT PRIMARY KEY, person_key TEXT NOT NULL, user_id INTEGER NOT NULL, package TEXT NOT NULL,
  stars INTEGER NOT NULL, status TEXT NOT NULL, created_at REAL NOT NULL, expires_at REAL NOT NULL);
CREATE TABLE IF NOT EXISTS payments(
  charge_id TEXT PRIMARY KEY, payload TEXT NOT NULL, person_key TEXT NOT NULL, user_id INTEGER NOT NULL,
  package TEXT NOT NULL, stars INTEGER NOT NULL, status TEXT NOT NULL, paid_at REAL NOT NULL,
  refunded_at REAL, test_env INTEGER NOT NULL DEFAULT 0);
CREATE TABLE IF NOT EXISTS grants(
  charge_id TEXT PRIMARY KEY, person_key TEXT NOT NULL, requests INTEGER NOT NULL, doc_slots INTEGER NOT NULL,
  valid_from REAL NOT NULL, valid_until REAL NOT NULL, status TEXT NOT NULL, days INTEGER NOT NULL DEFAULT 30);
CREATE INDEX IF NOT EXISTS grants_person ON grants(person_key, status);
CREATE TABLE IF NOT EXISTS usage(
  id INTEGER PRIMARY KEY AUTOINCREMENT, person_key TEXT NOT NULL, kind TEXT NOT NULL, pool TEXT NOT NULL,
  charge_id TEXT, ts REAL NOT NULL, voided INTEGER NOT NULL DEFAULT 0, final INTEGER NOT NULL DEFAULT 1);
CREATE INDEX IF NOT EXISTS usage_person ON usage(person_key, pool, voided);
CREATE TABLE IF NOT EXISTS tickets(
  id INTEGER PRIMARY KEY AUTOINCREMENT, person_key TEXT NOT NULL, user_id INTEGER NOT NULL, text TEXT NOT NULL,
  status TEXT NOT NULL, created_at REAL NOT NULL, closed_at REAL);
CREATE TABLE IF NOT EXISTS finance(
  id INTEGER PRIMARY KEY AUTOINCREMENT, kind TEXT NOT NULL, stars INTEGER NOT NULL DEFAULT 0,
  usd REAL NOT NULL DEFAULT 0, note TEXT NOT NULL DEFAULT '', ref TEXT NOT NULL DEFAULT '', ts REAL NOT NULL);
CREATE TABLE IF NOT EXISTS refund_tombstones(
  charge_id TEXT PRIMARY KEY, seen_at REAL NOT NULL, matched_at REAL);
CREATE TABLE IF NOT EXISTS rejected_events(
  id INTEGER PRIMARY KEY AUTOINCREMENT, ts REAL NOT NULL, update_id INTEGER, kind TEXT NOT NULL,
  charge_id TEXT NOT NULL DEFAULT '', reason TEXT NOT NULL DEFAULT '');
"""


@dataclass(frozen=True)
class PaymentOutcome:
    status: str                 # granted | duplicate | unmatched | refunded (the refund event came first)
    charge_id: str
    stars: int = 0
    requests: int = 0
    valid_until: float = 0.0
    person_key: str = ""


@dataclass(frozen=True)
class RefundOutcome:
    status: str                 # refunded | duplicate | unknown
    charge_id: str
    person_key: str = ""
    user_id: int = 0
    revoked_requests: int = 0
    stars: int = 0


@dataclass(frozen=True)
class Consumed:
    ok: bool
    pool: str = ""              # trial | paid | owner
    usage_id: int = 0
    reason: str = ""            # no_quota | expired | daily_cap


class TooManyOrders(ValueError):
    """The person already has MAX_PENDING_ORDERS open (unexpired) invoices."""


class StarsLedger:
    def __init__(self, path: Path, *, package: Package = DOCS30):
        self.path = Path(path)
        self.package = package
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._lock = threading.RLock()
        self._db = sqlite3.connect(str(self.path), isolation_level=None, check_same_thread=False, timeout=30)
        self._db.row_factory = sqlite3.Row
        # The very first open of a file by several processes at once: switching to WAL needs the file to itself and
        # SQLite answers "database is locked" at once instead of waiting (the busy timeout is not consulted).
        self._retry_locked(lambda: self._db.execute("PRAGMA journal_mode=WAL"))
        self._db.execute("PRAGMA synchronous=FULL")
        self._retry_locked(lambda: self._db.executescript(_SCHEMA))
        self._migrate_usage_final()

    @staticmethod
    def _retry_locked(operation, attempts: int = 60):
        for attempt in range(attempts):
            try:
                return operation()
            except sqlite3.OperationalError as exc:
                text = str(exc).lower()
                if ("locked" not in text and "busy" not in text) or attempt == attempts - 1:
                    raise
                time.sleep(min(0.02 * (attempt + 1), 0.25))

    def _migrate_usage_final(self) -> None:
        """A ledger written by an earlier build: its rows are settled history, never "in progress".

        Two processes (the Jeff window and the poller) may open an old ledger for the first time together: the check
        and the ALTER run under one write lock and the column is looked at again inside it, so the loser of the race
        finds the work done instead of failing with ``duplicate column name``."""
        def has_final() -> bool:
            return "final" in {row["name"] for row in self._db.execute("PRAGMA table_info(usage)").fetchall()}

        if has_final():
            return
        try:
            with self._tx() as db:
                if not has_final():
                    db.execute("ALTER TABLE usage ADD COLUMN final INTEGER NOT NULL DEFAULT 1")
        except sqlite3.OperationalError as exc:
            if "duplicate column name" not in str(exc).lower() or not has_final():
                raise

    def close(self) -> None:
        with self._lock:
            self._db.close()

    @contextmanager
    def _tx(self):
        with self._lock:
            self._db.execute("BEGIN IMMEDIATE")
            try:
                yield self._db
                self._db.execute("COMMIT")
            except BaseException:
                try:                                          # a failed COMMIT must not leave the connection open
                    self._db.execute("ROLLBACK")
                except sqlite3.Error:
                    pass
                raise

    # -- orders / payments --------------------------------------------------------------------
    def create_order(self, person_key: str, user_id: int, now: float | None = None) -> str:
        """Open one invoice. An order that already passed pre-checkout is never evicted to make room: only EXPIRED
        orders are cleaned up, and a person with MAX_PENDING_ORDERS unexpired ones gets ``TooManyOrders``."""
        now = time.time() if now is None else now
        payload = f"jd1:{self.package.id}:{secrets.token_hex(12)}"          # 1..128 bytes, opaque to the user
        with self._tx() as db:
            db.execute("DELETE FROM orders WHERE status='pending' AND expires_at<?", (now - 7 * DAY,))
            open_now = db.execute("SELECT COUNT(*) AS n FROM orders WHERE person_key=? AND status='pending' "
                                  "AND expires_at>=?", (person_key, now)).fetchone()["n"]
            if open_now >= MAX_PENDING_ORDERS:
                raise TooManyOrders(person_key[:8])
            db.execute("INSERT INTO orders VALUES(?,?,?,?,?,?,?,?)",
                       (payload, person_key, int(user_id), self.package.id, self.package.stars, "pending",
                        now, now + ORDER_TTL_SECONDS))
        return payload

    def check_pre_checkout(self, payload: str, user_id: int, currency: str, total_amount: int,
                           now: float | None = None) -> tuple[bool, str]:
        """Answer for ``answerPreCheckoutQuery``. Pure read; grants nothing."""
        now = time.time() if now is None else now
        with self._lock:
            row = self._db.execute("SELECT * FROM orders WHERE payload=?", (str(payload),)).fetchone()
        if row is None:
            return False, "Счёт не найден. Откройте /buy и создайте новый."
        if row["user_id"] != int(user_id):
            return False, "Этот счёт выписан другому пользователю."
        if currency != "XTR" or int(total_amount) != row["stars"]:
            return False, "Сумма или валюта счёта не совпадает. Откройте /buy заново."
        if row["status"] != "pending":
            return False, "Этот счёт уже оплачен или закрыт. Откройте /plan."
        if now > row["expires_at"]:
            return False, "Счёт устарел. Откройте /buy и создайте новый."
        return True, ""

    def record_payment(self, *, person_key: str, user_id: int, payload: str, charge_id: str, currency: str,
                       total_amount: int, now: float | None = None, test_env: bool = False) -> PaymentOutcome:
        """Credit one ``successful_payment``. Idempotent on ``charge_id``; unmatched money is kept and flagged."""
        now = time.time() if now is None else now
        charge_id = str(charge_id)
        if not charge_id:
            raise ValueError("telegram_payment_charge_id is required")
        with self._tx() as db:
            prior = db.execute("SELECT person_key,stars FROM payments WHERE charge_id=?", (charge_id,)).fetchone()
            if prior is not None:
                return PaymentOutcome("duplicate", charge_id, prior["stars"], person_key=prior["person_key"])
            order = db.execute("SELECT * FROM orders WHERE payload=?", (str(payload),)).fetchone()
            matched = (order is not None and order["user_id"] == int(user_id) and currency == "XTR"
                       and order["stars"] == int(total_amount) and order["status"] in ("pending", "expired")
                       and order["person_key"] == person_key)
            tomb = db.execute("SELECT seen_at FROM refund_tombstones WHERE charge_id=?", (charge_id,)).fetchone()
            if tomb is not None:
                # The refund event of this charge arrived BEFORE its payment event: the Stars already went back, so
                # nothing is granted. It is booked as paid-and-refunded (gross and refunded both, net zero) and the
                # tombstone is marked so the owner's report shows that it happened.
                db.execute("INSERT INTO payments VALUES(?,?,?,?,?,?,?,?,?,?)",
                           (charge_id, str(payload)[:128], person_key, int(user_id),
                            order["package"] if matched else "?", int(total_amount), "refunded", now,
                            tomb["seen_at"], int(test_env)))
                db.execute("UPDATE refund_tombstones SET matched_at=? WHERE charge_id=?", (now, charge_id))
                if matched:
                    db.execute("UPDATE orders SET status='paid' WHERE payload=?", (order["payload"],))
                return PaymentOutcome("refunded", charge_id, int(total_amount), person_key=person_key)
            if not matched:
                db.execute("INSERT INTO payments VALUES(?,?,?,?,?,?,?,?,NULL,?)",
                           (charge_id, str(payload)[:128], person_key, int(user_id), "?", int(total_amount),
                            "unmatched", now, int(test_env)))
                return PaymentOutcome("unmatched", charge_id, int(total_amount), person_key=person_key)
            package = self.package
            db.execute("INSERT INTO payments VALUES(?,?,?,?,?,?,?,?,NULL,?)",
                       (charge_id, order["payload"], person_key, int(user_id), order["package"],
                        int(total_amount), "paid", now, int(test_env)))
            db.execute("INSERT INTO grants(charge_id,person_key,requests,doc_slots,valid_from,valid_until,status,days)"
                       " VALUES(?,?,?,?,?,?,?,?)",
                       (charge_id, person_key, package.requests, package.doc_slots, now, now, "active", package.days))
            db.execute("UPDATE orders SET status='paid' WHERE payload=?", (order["payload"],))
            valid_until = next(end for g, end in self._chain(db, person_key) if g["charge_id"] == charge_id)
            db.execute("UPDATE grants SET valid_until=? WHERE charge_id=?", (valid_until, charge_id))
            return PaymentOutcome("granted", charge_id, int(total_amount), package.requests, valid_until,
                                  person_key)

    def record_refund(self, charge_id: str, now: float | None = None) -> RefundOutcome:
        """Apply a refund (our own ``refundStarPayment`` or Telegram's ``refunded_payment``). Idempotent."""
        now = time.time() if now is None else now
        charge_id = str(charge_id)
        with self._tx() as db:
            pay = db.execute("SELECT * FROM payments WHERE charge_id=?", (charge_id,)).fetchone()
            if pay is None:
                # Telegram can deliver the refund before the payment event: remember it, so that payment is never
                # granted when it shows up (``record_payment`` looks the charge id up here).
                if charge_id and len(charge_id) <= MAX_CHARGE_ID_CHARS:
                    db.execute("INSERT OR IGNORE INTO refund_tombstones(charge_id, seen_at) VALUES(?,?)",
                               (charge_id, now))
                return RefundOutcome("unknown", charge_id)
            if pay["status"] == "refunded":
                return RefundOutcome("duplicate", charge_id, pay["person_key"], pay["user_id"], 0, pay["stars"])
            used = db.execute("SELECT COUNT(*) AS n FROM usage WHERE charge_id=? AND voided=0",
                              (charge_id,)).fetchone()["n"]
            grant = db.execute("SELECT * FROM grants WHERE charge_id=?", (charge_id,)).fetchone()
            revoked = max(0, grant["requests"] - used) if grant and grant["status"] == "active" else 0
            db.execute("UPDATE payments SET status='refunded', refunded_at=? WHERE charge_id=?", (now, charge_id))
            db.execute("UPDATE grants SET status='revoked' WHERE charge_id=?", (charge_id,))
            return RefundOutcome("refunded", charge_id, pay["person_key"], pay["user_id"], revoked, pay["stars"])

    def payment(self, charge_id: str) -> dict | None:
        with self._lock:
            row = self._db.execute("SELECT * FROM payments WHERE charge_id=?", (str(charge_id),)).fetchone()
        return dict(row) if row else None

    def person_payments(self, person_key: str) -> list[dict]:
        with self._lock:
            rows = self._db.execute("SELECT * FROM payments WHERE person_key=? ORDER BY paid_at", (person_key,))
            return [dict(r) for r in rows.fetchall()]

    # -- entitlement / usage ------------------------------------------------------------------
    @staticmethod
    def _chain(db, person_key: str) -> list[tuple]:
        """Non-revoked grants with their effective end. Each grant starts when it was paid or when the previous one
        ends, whichever is later, so a refund of an earlier package shortens the later one back to its own length.

        A REVOKED grant still occupies the time it really ran: from its start to ``min(its end, refunded_at)``. So
        refunding a package that has already elapsed (or is half way) never makes a later package start earlier and
        end in the past; a package refunded before it even started occupies nothing."""
        rows = db.execute(
            "SELECT g.*, p.refunded_at AS refunded_at FROM grants g LEFT JOIN payments p ON p.charge_id=g.charge_id "
            "WHERE g.person_key=? AND g.status IN ('active','revoked') ORDER BY g.valid_from, g.charge_id",
            (person_key,)).fetchall()
        out, cursor = [], 0.0
        for g in rows:
            start = max(g["valid_from"], cursor)
            end = start + g["days"] * DAY
            if g["status"] == "active":
                out.append((g, end))
                cursor = end
            else:
                refunded_at = g["refunded_at"]
                cursor = start if refunded_at is None else min(end, max(start, float(refunded_at)))
        return out

    def _active_grants(self, db, person_key: str, now: float):
        """Usable grants (effective end still ahead), earliest end first."""
        return sorted(((g, end) for g, end in self._chain(db, person_key) if end > now), key=lambda t: t[1])

    def entitlement(self, person_key: str, now: float | None = None) -> dict:
        now = time.time() if now is None else now
        with self._lock:
            db = self._db
            paid_left, valid_until, slots = 0, 0.0, 0
            for g, end in self._active_grants(db, person_key, now):
                used = db.execute("SELECT COUNT(*) AS n FROM usage WHERE charge_id=? AND voided=0",
                                  (g["charge_id"],)).fetchone()["n"]
                paid_left += max(0, g["requests"] - used)
                valid_until = max(valid_until, end)
                slots = max(slots, g["doc_slots"])
            trial_used = db.execute("SELECT COUNT(*) AS n FROM usage WHERE person_key=? AND pool='trial'"
                                    " AND voided=0", (person_key,)).fetchone()["n"]
            expired = any(end <= now for _, end in self._chain(db, person_key))
        return {"paid_requests_left": paid_left, "valid_until": valid_until if slots else 0.0,
                "doc_limit": max(slots, TRIAL_DOCS), "trial_left": max(0, TRIAL_REQUESTS - trial_used),
                "has_paid": bool(slots), "expired": expired and not slots}

    def consume(self, person_key: str, kind: str, now: float | None = None, *, owner: bool = False) -> Consumed:
        """Reserve one request atomically. The caller ``void``s it if no answer was produced."""
        now = time.time() if now is None else now
        sql = "INSERT INTO usage(person_key,kind,pool,charge_id,ts,final) VALUES(?,?,?,?,?,?)"
        with self._tx() as db:
            if owner:
                cur = db.execute(sql, (person_key, kind, "owner", None, now, 1))        # nothing to settle
                return Consumed(True, "owner", cur.lastrowid)
            for g, _end in self._active_grants(db, person_key, now):
                used = db.execute("SELECT COUNT(*) AS n FROM usage WHERE charge_id=? AND voided=0",
                                  (g["charge_id"],)).fetchone()["n"]
                if used >= g["requests"]:
                    continue
                today = db.execute("SELECT COUNT(*) AS n FROM usage WHERE person_key=? AND pool='paid' AND voided=0"
                                   " AND ts>=?", (person_key, now - (now % DAY))).fetchone()["n"]
                if today >= DAILY_CAP_PAID:
                    return Consumed(False, reason="daily_cap")
                cur = db.execute(sql, (person_key, kind, "paid", g["charge_id"], now, 0))
                return Consumed(True, "paid", cur.lastrowid)
            trial_used = db.execute("SELECT COUNT(*) AS n FROM usage WHERE person_key=? AND pool='trial'"
                                    " AND voided=0", (person_key,)).fetchone()["n"]
            if trial_used < TRIAL_REQUESTS:
                cur = db.execute(sql, (person_key, kind, "trial", None, now, 0))
                return Consumed(True, "trial", cur.lastrowid)
            expired = any(end <= now for _, end in self._chain(db, person_key))
            return Consumed(False, reason="expired" if expired else "no_quota")

    def finalize(self, usage_id: int) -> None:
        """The request is settled as charged (an answer was delivered): the crash sweep must leave it alone."""
        with self._tx() as db:
            db.execute("UPDATE usage SET final=1 WHERE id=?", (int(usage_id),))

    def void(self, usage_id: int, now: float | None = None, *, bounded: bool = True) -> bool:
        """Give one request back (no model answered); the request is settled either way.

        ``bounded`` is for the case where a model DID produce text that was then replaced (self-disclosure, garbage):
        a trigger that always makes the model say that must not become an unlimited free path, so only
        MAX_VOIDS_PER_DAY of those per person per day are given back. With ``bounded=False`` (no model text at all:
        outage, flood/safety early reply) the request is always given back and is not counted against the bound.
        Returns whether it was given back."""
        now = time.time() if now is None else now
        with self._tx() as db:
            row = db.execute("SELECT person_key, voided FROM usage WHERE id=?", (int(usage_id),)).fetchone()
            if row is None or row["voided"]:
                return False
            if bounded:
                given = db.execute("SELECT COUNT(*) AS n FROM usage WHERE person_key=? AND voided=1 AND ts>=?",
                                   (row["person_key"], now - DAY)).fetchone()["n"]
                if given >= MAX_VOIDS_PER_DAY:
                    db.execute("UPDATE usage SET final=1 WHERE id=?", (int(usage_id),))
                    return False
            db.execute("UPDATE usage SET voided=?, final=1 WHERE id=?", (1 if bounded else 2, int(usage_id)))
            return True

    def sweep_unfinished(self, now: float | None = None, *, max_age: float = STALE_USAGE_SECONDS) -> int:
        """Give back requests whose turn never finished (a crash between ``consume`` and the answer).

        Only rows older than ``max_age`` are touched: a younger one may belong to a turn another process (the Jeff
        window) is still running. Owner rows are never charged and are left alone. Returns how many were voided."""
        now = time.time() if now is None else now
        with self._tx() as db:
            cur = db.execute("UPDATE usage SET voided=2, final=1 WHERE final=0 AND voided=0 AND pool!='owner' "
                             "AND ts<?", (now - max_age,))
            return cur.rowcount

    def usage_count(self, person_key: str) -> int:
        with self._lock:
            return self._db.execute("SELECT COUNT(*) AS n FROM usage WHERE person_key=? AND voided=0",
                                    (person_key,)).fetchone()["n"]

    # -- support / owner accounting -----------------------------------------------------------
    def add_ticket(self, person_key: str, user_id: int, text: str, now: float | None = None) -> int:
        """Returns the ticket number, or 0 when this person already has MAX_OPEN_TICKETS open ones."""
        now = time.time() if now is None else now
        with self._tx() as db:
            open_now = db.execute("SELECT COUNT(*) AS n FROM tickets WHERE person_key=? AND status='open'",
                                  (person_key,)).fetchone()["n"]
            if open_now >= MAX_OPEN_TICKETS:
                return 0
            cur = db.execute("INSERT INTO tickets(person_key,user_id,text,status,created_at) VALUES(?,?,?,?,?)",
                             (person_key, int(user_id), str(text)[:1000], "open", now))
            return cur.lastrowid

    def open_tickets(self) -> list[dict]:
        with self._lock:
            return [dict(r) for r in self._db.execute(
                "SELECT * FROM tickets WHERE status='open' ORDER BY id").fetchall()]

    def close_ticket(self, ticket_id: int, now: float | None = None) -> bool:
        now = time.time() if now is None else now
        with self._tx() as db:
            cur = db.execute("UPDATE tickets SET status='closed', closed_at=? WHERE id=? AND status='open'",
                             (now, int(ticket_id)))
            return cur.rowcount == 1

    def erase_ticket_texts(self, person_key: str, marker: str = "[текст удалён по запросу участника]") -> int:
        """``/delete_me``: the participant's own words in support tickets go; the row stays as a content-free
        tombstone (number, status, dates, who) because it is part of the payment record. Returns rows blanked."""
        with self._tx() as db:
            cur = db.execute("UPDATE tickets SET text=? WHERE person_key=? AND text!=?", (marker, person_key, marker))
            return cur.rowcount

    # -- events that could not be booked -------------------------------------------------------
    def add_rejected(self, kind: str, *, charge_id: str = "", reason: str = "", update_id: int | None = None,
                     now: float | None = None) -> int:
        """Journal one payment-related update that was refused (malformed, impossible parties...) and acknowledged.

        Never message text: the kind, the charge id when there is one, and a short code. Newest MAX_REJECTED_ROWS
        are kept."""
        now = time.time() if now is None else now
        with self._tx() as db:
            cur = db.execute("INSERT INTO rejected_events(ts,update_id,kind,charge_id,reason) VALUES(?,?,?,?,?)",
                             (now, update_id if type(update_id) is int else None, str(kind)[:40],
                              str(charge_id)[:80], str(reason)[:80]))
            db.execute("DELETE FROM rejected_events WHERE id NOT IN "
                       "(SELECT id FROM rejected_events ORDER BY id DESC LIMIT ?)", (MAX_REJECTED_ROWS,))
            return cur.lastrowid

    def rejected_events(self, limit: int = 20) -> list[dict]:
        with self._lock:
            return [dict(r) for r in self._db.execute(
                "SELECT * FROM rejected_events ORDER BY id DESC LIMIT ?", (int(limit),)).fetchall()]

    def add_finance(self, kind: str, *, stars: int = 0, usd: float = 0.0, note: str = "", ref: str = "",
                    now: float | None = None) -> int:
        if kind not in ("withdrawal", "expense"):
            raise ValueError("finance kind must be withdrawal or expense")
        if not (0 <= stars <= MAX_STARS) or not (0 <= usd <= MAX_USD):       # also refuses nan / inf
            raise ValueError("amounts must be finite, non-negative and within bounds")
        now = time.time() if now is None else now
        with self._tx() as db:
            cur = db.execute("INSERT INTO finance(kind,stars,usd,note,ref,ts) VALUES(?,?,?,?,?,?)",
                             (kind, int(stars), float(usd), str(note)[:200], str(ref)[:80], now))
            return cur.lastrowid

    def report(self, now: float | None = None) -> dict:
        """Owner report. Stars received are NOT money and NOT profit; every column is a separate fact."""
        now = time.time() if now is None else now
        with self._lock:
            db = self._db

            def one(sql, *args):
                return db.execute(sql, args).fetchone()[0] or 0

            gross = one("SELECT SUM(stars) FROM payments WHERE test_env=0 AND status IN ('paid','refunded')")
            refunded = one("SELECT SUM(stars) FROM payments WHERE test_env=0 AND status='refunded'")
            unmatched = one("SELECT SUM(stars) FROM payments WHERE test_env=0 AND status='unmatched'")
            test_stars = one("SELECT SUM(stars) FROM payments WHERE test_env=1")
            withdrawn_stars = one("SELECT SUM(stars) FROM finance WHERE kind='withdrawal'")
            withdrawn_usd = one("SELECT SUM(usd) FROM finance WHERE kind='withdrawal'")
            expenses_usd = one("SELECT SUM(usd) FROM finance WHERE kind='expense'")
            payers = one("SELECT COUNT(DISTINCT person_key) FROM payments WHERE test_env=0 AND status='paid'")
            # Effective ends are recomputed from the chain (a refund moves later packages); the stored valid_until
            # of a grant is only what was true when it was booked.
            holders = [r["person_key"] for r in db.execute(
                "SELECT DISTINCT person_key FROM grants WHERE status='active'").fetchall()]
            active = sum(1 for holder in holders if any(end > now for _g, end in self._chain(db, holder)))
            tickets = one("SELECT COUNT(*) FROM tickets WHERE status='open'")
            early_refunds = one("SELECT COUNT(*) FROM refund_tombstones WHERE matched_at IS NOT NULL")
            orphan_refunds = one("SELECT COUNT(*) FROM refund_tombstones WHERE matched_at IS NULL")
            rejected = one("SELECT COUNT(*) FROM rejected_events")
        net = gross - refunded
        return {
            "stars_gross_received": gross, "stars_refunded": refunded, "stars_net_received": net,
            "refunds_before_payment": early_refunds, "refunds_without_payment": orphan_refunds,
            "rejected_events": rejected,
            "stars_unmatched_needs_owner": unmatched, "test_environment_stars": test_stars,
            "stars_withdrawn": withdrawn_stars, "stars_estimated_on_balance": max(0, net - withdrawn_stars),
            "usd_withdrawn_recorded": round(withdrawn_usd, 2), "usd_expenses_recorded": round(expenses_usd, 2),
            "usd_cash_result_recorded": round(withdrawn_usd - expenses_usd, 2),
            "paying_users": payers, "active_entitlements": active, "open_support_tickets": tickets,
            "note": ("Stars на балансе Telegram не деньги и не прибыль: прибыль считается только по фактически "
                     "выведенным деньгам минус записанные расходы; тестовые Stars не учитываются."),
        }
