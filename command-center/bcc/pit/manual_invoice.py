"""Manual (bank-transfer) invoices for Jeff's paid document assistant, while Telegram Stars are paused.

The owner issues an invoice for a package to one participant (amount in EUR / CZK / RUB, a unique reference), the
participant pays by bank transfer OUTSIDE Bossman, and the owner marks it paid with an explicit command. Only that
owner mark grants access, and the grant is booked in the SAME ``stars_ledger`` file and tables (``payments`` +
``grants``, charge id ``manual:<ref>``) so quota, validity chain, refunds and ``/plan`` work exactly as for Stars.

Design rules:

* Nothing here talks to Telegram or any network. Bossman never sends the invoice or any notice to the participant;
  the owner gets the reference and passes it on himself (``pit/outbound_guard.py`` stays the only path out).
* Every state change takes ``owner=True`` explicitly; a call without it is refused (``NotOwner``).
* Money is ``Decimal`` with two places and kept per currency, never mixed with Stars and never summed into a
  "profit". Test-environment invoices (``test_env``) are booked apart, like test Stars.
* Mark-paid is idempotent: the invoice row and the payment row are written in one transaction, and the ledger's
  ``charge_id`` primary key makes a second booking impossible also after a restart.
* An invoice expires (``INVOICE_TTL_DAYS``); an expired or cancelled invoice can never be marked paid: the owner
  issues a new one. A refund by the owner (money returned by bank outside Bossman) revokes the unused rest.
"""
from __future__ import annotations

import secrets
import time
from dataclasses import dataclass
from decimal import Decimal, InvalidOperation

from .stars_ledger import DAY, DOCS30, Package, StarsLedger

CURRENCIES = frozenset({"EUR", "CZK", "RUB"})
PACKAGES: dict[str, Package] = {DOCS30.id: DOCS30}
INVOICE_TTL_DAYS = 7
MAX_OPEN_INVOICES = 5          # per person, unexpired 'issued' ones
MAX_AMOUNT = Decimal("1000000")
CHARGE_PREFIX = "manual:"

_SCHEMA = """
CREATE TABLE IF NOT EXISTS manual_invoices(
  ref TEXT PRIMARY KEY, person_key TEXT NOT NULL, user_id INTEGER NOT NULL, package TEXT NOT NULL,
  amount TEXT NOT NULL, currency TEXT NOT NULL, status TEXT NOT NULL, created_at REAL NOT NULL,
  expires_at REAL NOT NULL, paid_at REAL, closed_at REAL, test_env INTEGER NOT NULL DEFAULT 0);
CREATE INDEX IF NOT EXISTS manual_invoices_person ON manual_invoices(person_key, status);
"""


class NotOwner(PermissionError):
    """A state change of a manual invoice was attempted without the owner's explicit command."""


class InvoiceRejected(ValueError):
    """The owner's input was refused; ``text`` is the Russian reason for the owner."""

    def __init__(self, text: str):
        super().__init__(text)
        self.text = text


@dataclass(frozen=True)
class InvoiceOutcome:
    status: str                 # granted | duplicate | unknown | expired | cancelled | refunded | not_paid | paid
    ref: str
    person_key: str = ""
    user_id: int = 0
    valid_until: float = 0.0
    requests: int = 0
    revoked_requests: int = 0


def parse_amount(text: str) -> Decimal:
    """``"199"``, ``"199.5"``, ``"199,50"`` -> Decimal with two places. Zero, negatives, nan/inf, more than two
    decimals and absurd sums are refused."""
    try:
        value = Decimal(str(text).strip().replace(",", "."))
    except (InvalidOperation, ValueError):
        raise InvoiceRejected("Сумма должна быть числом, например 199 или 199.50.") from None
    if not value.is_finite() or value <= 0 or value > MAX_AMOUNT:
        raise InvoiceRejected("Сумма должна быть больше нуля и не больше 1 000 000.")
    if value != value.quantize(Decimal("0.01")):
        raise InvoiceRejected("Не больше двух знаков после запятой.")
    return value.quantize(Decimal("0.01"))


def parse_currency(text: str) -> str:
    code = str(text).strip().upper()
    if code not in CURRENCIES:
        raise InvoiceRejected("Валюта: " + ", ".join(sorted(CURRENCIES)) + ".")
    return code


def charge_id_of(ref: str) -> str:
    return CHARGE_PREFIX + ref


class ManualInvoices:
    """Owner-issued bank-transfer invoices over an existing ``StarsLedger`` (same file, same connection)."""

    def __init__(self, ledger: StarsLedger, *, test_env: bool = False):
        self.ledger = ledger
        self.test_env = bool(test_env)
        with ledger.transaction() as db:
            for statement in filter(str.strip, _SCHEMA.split(";")):
                db.execute(statement)

    @staticmethod
    def _require_owner(owner: bool) -> None:
        if owner is not True:
            raise NotOwner("manual invoices change only on an explicit owner command")

    @staticmethod
    def _new_ref(now: float) -> str:
        return "JD-" + time.strftime("%Y%m%d", time.localtime(now)) + "-" + secrets.token_hex(3).upper()

    # -- owner actions ------------------------------------------------------------------------
    def issue(self, person_key: str, user_id: int, package_id: str, amount: str | Decimal, currency: str, *,
              owner: bool, now: float | None = None) -> dict:
        """Record one invoice. Grants nothing and sends nothing; returns the row (with ``ref``) for the owner."""
        self._require_owner(owner)
        now = time.time() if now is None else now
        package = PACKAGES.get(str(package_id).strip().lower())
        if package is None or package.id != self.ledger.package.id:
            raise InvoiceRejected("Пакет: " + ", ".join(sorted(PACKAGES)) + ".")
        if type(user_id) is not int or user_id <= 0 or not person_key:
            raise InvoiceRejected("Укажите Telegram id участника числом.")
        value = parse_amount(str(amount))
        code = parse_currency(currency)
        with self.ledger.transaction() as db:
            open_now = db.execute("SELECT COUNT(*) AS n FROM manual_invoices WHERE person_key=? AND status='issued'"
                                  " AND expires_at>=?", (person_key, now)).fetchone()["n"]
            if open_now >= MAX_OPEN_INVOICES:
                raise InvoiceRejected(f"У участника уже {open_now} открытых счетов: отмените лишние.")
            while True:
                ref = self._new_ref(now)
                if db.execute("SELECT 1 FROM manual_invoices WHERE ref=?", (ref,)).fetchone() is None:
                    break
            db.execute("INSERT INTO manual_invoices(ref,person_key,user_id,package,amount,currency,status,created_at,"
                       "expires_at,test_env) VALUES(?,?,?,?,?,?,?,?,?,?)",
                       (ref, person_key, user_id, package.id, str(value), code, "issued", now,
                        now + INVOICE_TTL_DAYS * DAY, int(self.test_env)))
        return self.get(ref)

    def mark_paid(self, ref: str, *, owner: bool, now: float | None = None) -> InvoiceOutcome:
        """The owner saw the money arrive: grant the package once. A second mark is ``duplicate``, never a second
        grant; an expired / cancelled / refunded invoice is refused with its status."""
        self._require_owner(owner)
        now = time.time() if now is None else now
        ref = str(ref).strip()
        with self.ledger.transaction() as db:
            row = db.execute("SELECT * FROM manual_invoices WHERE ref=?", (ref,)).fetchone()
            if row is None:
                return InvoiceOutcome("unknown", ref)
            who = dict(person_key=row["person_key"], user_id=row["user_id"])
            if row["status"] == "paid":
                return InvoiceOutcome("duplicate", ref, **who)
            if row["status"] in ("cancelled", "refunded", "expired"):
                return InvoiceOutcome(row["status"], ref, **who)
            if now > row["expires_at"]:
                db.execute("UPDATE manual_invoices SET status='expired', closed_at=? WHERE ref=?", (now, ref))
                return InvoiceOutcome("expired", ref, **who)
            booked = self.ledger.book_external_in(
                db, charge_id=charge_id_of(ref), payload=ref, person_key=row["person_key"], user_id=row["user_id"],
                now=now, test_env=bool(row["test_env"]))
            db.execute("UPDATE manual_invoices SET status='paid', paid_at=? WHERE ref=?", (now, ref))
            if booked.status != "granted":                       # a payment row without its invoice mark: no regrant
                return InvoiceOutcome("duplicate", ref, **who)
            return InvoiceOutcome("granted", ref, valid_until=booked.valid_until, requests=booked.requests, **who)

    def cancel(self, ref: str, *, owner: bool, now: float | None = None) -> InvoiceOutcome:
        """Close an unpaid invoice; it can never be marked paid afterwards. A paid one needs ``refund``."""
        self._require_owner(owner)
        now = time.time() if now is None else now
        ref = str(ref).strip()
        with self.ledger.transaction() as db:
            row = db.execute("SELECT * FROM manual_invoices WHERE ref=?", (ref,)).fetchone()
            if row is None:
                return InvoiceOutcome("unknown", ref)
            who = dict(person_key=row["person_key"], user_id=row["user_id"])
            if row["status"] == "issued":
                db.execute("UPDATE manual_invoices SET status='cancelled', closed_at=? WHERE ref=?", (now, ref))
                return InvoiceOutcome("cancelled", ref, **who)
            return InvoiceOutcome(row["status"], ref, **who)          # paid / cancelled / expired / refunded

    def refund(self, ref: str, *, owner: bool, now: float | None = None) -> InvoiceOutcome:
        """The owner returned the money (by bank, outside Bossman): revoke the unused rest of the grant. Idempotent."""
        self._require_owner(owner)
        now = time.time() if now is None else now
        ref = str(ref).strip()
        with self.ledger.transaction() as db:
            row = db.execute("SELECT * FROM manual_invoices WHERE ref=?", (ref,)).fetchone()
            if row is None:
                return InvoiceOutcome("unknown", ref)
            who = dict(person_key=row["person_key"], user_id=row["user_id"])
            if row["status"] == "refunded":
                return InvoiceOutcome("duplicate", ref, **who)
            if row["status"] != "paid":
                return InvoiceOutcome("not_paid", ref, **who)
            out = self.ledger.refund_in(db, charge_id_of(ref), now)
            db.execute("UPDATE manual_invoices SET status='refunded', closed_at=? WHERE ref=?", (now, ref))
            return InvoiceOutcome("refunded", ref, revoked_requests=out.revoked_requests, **who)

    # -- reads --------------------------------------------------------------------------------
    def get(self, ref: str) -> dict | None:
        with self.ledger._lock:
            row = self.ledger._db.execute("SELECT * FROM manual_invoices WHERE ref=?", (str(ref).strip(),)).fetchone()
        return dict(row) if row else None

    def listing(self, limit: int = 20, now: float | None = None) -> list[dict]:
        """Newest first; an 'issued' invoice past its end is shown as expired (the row is closed on the next mark)."""
        now = time.time() if now is None else now
        with self.ledger._lock:
            rows = [dict(r) for r in self.ledger._db.execute(
                "SELECT * FROM manual_invoices ORDER BY created_at DESC LIMIT ?", (int(limit),)).fetchall()]
        for row in rows:
            if row["status"] == "issued" and now > row["expires_at"]:
                row["status"] = "expired"
        return rows

    def report(self) -> dict:
        """Per currency: paid, refunded and net (real money only), and test-environment paid apart. Strings, so the
        Decimal is never rounded through a float."""
        with self.ledger._lock:
            rows = self.ledger._db.execute(
                "SELECT amount, currency, status, test_env FROM manual_invoices "
                "WHERE status IN ('paid','refunded')").fetchall()
            open_n = self.ledger._db.execute(
                "SELECT COUNT(*) FROM manual_invoices WHERE status='issued' AND expires_at>=?",
                (time.time(),)).fetchone()[0]
        out: dict[str, dict[str, Decimal]] = {}
        for row in rows:
            bucket = out.setdefault(row["currency"], {"paid": Decimal("0.00"), "refunded": Decimal("0.00"),
                                                      "test_paid": Decimal("0.00")})
            amount = Decimal(row["amount"])
            if row["test_env"]:
                bucket["test_paid"] += amount
                continue
            bucket["paid"] += amount
            if row["status"] == "refunded":
                bucket["refunded"] += amount
        return {"open_invoices": open_n,
                "by_currency": {code: {"paid": str(b["paid"]), "refunded": str(b["refunded"]),
                                       "net": str(b["paid"] - b["refunded"]), "test_paid": str(b["test_paid"])}
                                for code, b in sorted(out.items())}}
