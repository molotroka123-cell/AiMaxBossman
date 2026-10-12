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
MAX_PENDING_ORDERS = 10     # per person
MAX_USD = 1e9
MAX_STARS = 10 ** 12

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
  charge_id TEXT, ts REAL NOT NULL, voided INTEGER NOT NULL DEFAULT 0);
CREATE INDEX IF NOT EXISTS usage_person ON usage(person_key, pool, voided);
CREATE TABLE IF NOT EXISTS tickets(
  id INTEGER PRIMARY KEY AUTOINCREMENT, person_key TEXT NOT NULL, user_id INTEGER NOT NULL, text TEXT NOT NULL,
  status TEXT NOT NULL, created_at REAL NOT NULL, closed_at REAL);
CREATE TABLE IF NOT EXISTS finance(
  id INTEGER PRIMARY KEY AUTOINCREMENT, kind TEXT NOT NULL, stars INTEGER NOT NULL DEFAULT 0,
  usd REAL NOT NULL DEFAULT 0, note TEXT NOT NULL DEFAULT '', ref TEXT NOT NULL DEFAULT '', ts REAL NOT NULL);
"""


@dataclass(frozen=True)
class PaymentOutcome:
    status: str                 # granted | duplicate | unmatched
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


class StarsLedger:
    def __init__(self, path: Path, *, package: Package = DOCS30):
        self.path = Path(path)
        self.package = package
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._lock = threading.RLock()
        self._db = sqlite3.connect(str(self.path), isolation_level=None, check_same_thread=False, timeout=30)
        self._db.row_factory = sqlite3.Row
        self._db.execute("PRAGMA journal_mode=WAL")
        self._db.execute("PRAGMA synchronous=FULL")
        self._db.executescript(_SCHEMA)

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
        now = time.time() if now is None else now
        payload = f"jd1:{self.package.id}:{secrets.token_hex(12)}"          # 1..128 bytes, opaque to the user
        with self._tx() as db:
            db.execute("DELETE FROM orders WHERE status='pending' AND expires_at<?", (now - 7 * DAY,))
            db.execute("DELETE FROM orders WHERE payload IN (SELECT payload FROM orders WHERE person_key=? AND "
                       "status='pending' ORDER BY created_at DESC LIMIT -1 OFFSET ?)",
                       (person_key, MAX_PENDING_ORDERS - 1))
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
        ends, whichever is later, so a refund of an earlier package shortens the later one back to its own length."""
        rows = db.execute("SELECT * FROM grants WHERE person_key=? AND status='active' ORDER BY valid_from, charge_id",
                          (person_key,)).fetchall()
        out, cursor = [], 0.0
        for g in rows:
            end = max(g["valid_from"], cursor) + g["days"] * DAY
            out.append((g, end))
            cursor = end
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
        with self._tx() as db:
            if owner:
                cur = db.execute("INSERT INTO usage(person_key,kind,pool,charge_id,ts) VALUES(?,?,?,?,?)",
                                 (person_key, kind, "owner", None, now))
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
                cur = db.execute("INSERT INTO usage(person_key,kind,pool,charge_id,ts) VALUES(?,?,?,?,?)",
                                 (person_key, kind, "paid", g["charge_id"], now))
                return Consumed(True, "paid", cur.lastrowid)
            trial_used = db.execute("SELECT COUNT(*) AS n FROM usage WHERE person_key=? AND pool='trial'"
                                    " AND voided=0", (person_key,)).fetchone()["n"]
            if trial_used < TRIAL_REQUESTS:
                cur = db.execute("INSERT INTO usage(person_key,kind,pool,charge_id,ts) VALUES(?,?,?,?,?)",
                                 (person_key, kind, "trial", None, now))
                return Consumed(True, "trial", cur.lastrowid)
            expired = any(end <= now for _, end in self._chain(db, person_key))
            return Consumed(False, reason="expired" if expired else "no_quota")

    def void(self, usage_id: int, now: float | None = None) -> bool:
        """Give one request back (no model answered). Bounded per person per day: a trigger that always makes the
        model not answer must not become an unlimited free path. Returns whether it was given back."""
        now = time.time() if now is None else now
        with self._tx() as db:
            row = db.execute("SELECT person_key, voided FROM usage WHERE id=?", (int(usage_id),)).fetchone()
            if row is None or row["voided"]:
                return False
            given = db.execute("SELECT COUNT(*) AS n FROM usage WHERE person_key=? AND voided=1 AND ts>=?",
                               (row["person_key"], now - DAY)).fetchone()["n"]
            if given >= MAX_VOIDS_PER_DAY:
                return False
            db.execute("UPDATE usage SET voided=1 WHERE id=?", (int(usage_id),))
            return True

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
            active = one("SELECT COUNT(DISTINCT person_key) FROM grants WHERE status='active' AND valid_until>?",
                         now)
            tickets = one("SELECT COUNT(*) FROM tickets WHERE status='open'")
        net = gross - refunded
        return {
            "stars_gross_received": gross, "stars_refunded": refunded, "stars_net_received": net,
            "stars_unmatched_needs_owner": unmatched, "test_environment_stars": test_stars,
            "stars_withdrawn": withdrawn_stars, "stars_estimated_on_balance": max(0, net - withdrawn_stars),
            "usd_withdrawn_recorded": round(withdrawn_usd, 2), "usd_expenses_recorded": round(expenses_usd, 2),
            "usd_cash_result_recorded": round(withdrawn_usd - expenses_usd, 2),
            "paying_users": payers, "active_entitlements": active, "open_support_tickets": tickets,
            "note": ("Stars на балансе Telegram не деньги и не прибыль: прибыль считается только по фактически "
                     "выведенным деньгам минус записанные расходы; тестовые Stars не учитываются."),
        }
