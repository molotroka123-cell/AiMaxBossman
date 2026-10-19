"""Local SQLite book of the assistant: manual rates, issued quotes, confirmed deals, append-only events.

A deal exists only after an explicit confirmation of a still-valid quote. One quote -> at most one deal
(duplicate confirmations return the same deal). Reversal never deletes: it marks the deal and logs an event.
Turnover counts the USDT leg only; profit is N/A without a cost basis.
"""
from __future__ import annotations

import functools
import json
import os
import sqlite3
import threading
from datetime import datetime, timezone
from decimal import Decimal
from pathlib import Path

from .quote import Quote
from .rates import MANUAL_DEFAULT_TTL, Rate, check_bounds

SCHEMA = """
CREATE TABLE IF NOT EXISTS manual_rates(id INTEGER PRIMARY KEY, pair TEXT NOT NULL, value TEXT NOT NULL, source TEXT NOT NULL,
  set_at TEXT NOT NULL, expires_at TEXT NOT NULL, set_by TEXT NOT NULL);
CREATE INDEX IF NOT EXISTS idx_manual_rates_pair_id ON manual_rates(pair, id);
CREATE TABLE IF NOT EXISTS quotes(id TEXT PRIMARY KEY, body TEXT NOT NULL, created_at TEXT NOT NULL, valid_until TEXT NOT NULL,
  crm_status TEXT NOT NULL DEFAULT 'quoted');
CREATE TABLE IF NOT EXISTS deals(id INTEGER PRIMARY KEY, quote_id TEXT NOT NULL UNIQUE REFERENCES quotes(id),
  status TEXT NOT NULL CHECK(status IN ('done','reversed')),
  crm_status TEXT NOT NULL DEFAULT 'settled' CHECK(crm_status IN ('accepted','settled','cancelled')),
  confirmed_at TEXT NOT NULL, confirmed_by TEXT NOT NULL,
  give_ccy TEXT NOT NULL, give_amount TEXT NOT NULL, get_ccy TEXT NOT NULL, get_amount TEXT NOT NULL,
  leg_in_actual TEXT, leg_out_actual TEXT, fee_rate TEXT,
  client_rate TEXT NOT NULL, mid TEXT NOT NULL, reversed_at TEXT, reverse_reason TEXT);
CREATE INDEX IF NOT EXISTS idx_deals_confirmed_at ON deals(confirmed_at);
CREATE TABLE IF NOT EXISTS events(id INTEGER PRIMARY KEY, ts TEXT NOT NULL, kind TEXT NOT NULL, ref TEXT NOT NULL, detail TEXT NOT NULL);
CREATE TRIGGER IF NOT EXISTS events_no_update BEFORE UPDATE ON events BEGIN SELECT RAISE(ABORT, 'events are append-only'); END;
CREATE TRIGGER IF NOT EXISTS events_no_delete BEFORE DELETE ON events BEGIN SELECT RAISE(ABORT, 'events are append-only'); END;
CREATE TRIGGER IF NOT EXISTS deals_no_delete BEFORE DELETE ON deals BEGIN SELECT RAISE(ABORT, 'deals are never deleted; reverse instead'); END;
"""
TABLES = {"manual_rates", "quotes", "deals", "events"}


class StoreError(Exception):
    pass


def default_db(env: dict | None = None) -> Path:
    env = os.environ if env is None else env
    if env.get("BOSSMAN_EXCHANGE_DIR"):
        return Path(env["BOSSMAN_EXCHANGE_DIR"]) / "assistant.db"
    return Path(env.get("LOCALAPPDATA", str(Path.home()))) / "Bossman" / "CommandCenter" / "exchange-assistant" / "assistant.db"


def _iso(d: datetime) -> str:
    return d.astimezone(timezone.utc).isoformat()


def _dt(s: str) -> datetime:
    return datetime.fromisoformat(s)


def _quote_body(q: Quote) -> str:
    return json.dumps({"id": q.id, "give_ccy": q.give_ccy, "give_amount": str(q.give_amount), "get_ccy": q.get_ccy,
                       "get_amount": str(q.get_amount), "mid": str(q.mid), "client_rate": str(q.client_rate),
                       "margin": str(q.margin), "created_at": _iso(q.created_at), "valid_until": _iso(q.valid_until),
                       "rates": list(q.rates), "formula": q.formula, "fixed_side": q.fixed_side}, ensure_ascii=False)


def _locked(fn):
    """One connection shared by the web server's worker threads: every public call runs under the store's lock."""
    @functools.wraps(fn)
    def wrapper(self, *a, **kw):
        with self.lock:
            return fn(self, *a, **kw)
    return wrapper


class Store:
    def __init__(self, path: str | Path):
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.lock = threading.RLock()
        self._manual_rates_cache: list[Rate] | None = None
        self.db = sqlite3.connect(str(self.path), check_same_thread=False, timeout=10)
        self.db.row_factory = sqlite3.Row
        self.db.execute("PRAGMA journal_mode=WAL")
        self.db.execute("PRAGMA busy_timeout=10000")
        self.db.executescript(SCHEMA)
        self._migrate()

    def _migrate(self) -> None:
        qcols = {r[1] for r in self.db.execute("PRAGMA table_info(quotes)")}
        if "crm_status" not in qcols:
            self.db.execute("ALTER TABLE quotes ADD COLUMN crm_status TEXT NOT NULL DEFAULT 'quoted'")
        dcols = {r[1] for r in self.db.execute("PRAGMA table_info(deals)")}
        for col, default in (("crm_status", "'settled'"), ("leg_in_actual", "NULL"),
                             ("leg_out_actual", "NULL"), ("fee_rate", "NULL")):
            if col not in dcols:
                self.db.execute(f"ALTER TABLE deals ADD COLUMN {col} TEXT DEFAULT {default}")
        self.db.commit()

    @_locked
    def close(self) -> None:
        self.db.close()

    def _event(self, now: datetime, kind: str, ref: str, detail: dict) -> None:
        self.db.execute("INSERT INTO events(ts, kind, ref, detail) VALUES (?,?,?,?)",
                        (_iso(now), kind, ref, json.dumps(detail, ensure_ascii=False)))

    # ---------------------------------------------------------------- manual rates
    @_locked
    def set_manual_rate(self, pair: str, value: Decimal, source: str, now: datetime, set_by: str,
                        ttl=MANUAL_DEFAULT_TTL) -> Rate:
        pair = pair.upper()
        check_bounds(pair, value)
        if not source.strip():
            raise StoreError("для ручного курса нужен источник (откуда цифра)")
        r = Rate(pair, value, source.strip()[:80], now, "manual", now + ttl)
        with self.db:
            self.db.execute("INSERT INTO manual_rates(pair, value, source, set_at, expires_at, set_by) VALUES (?,?,?,?,?,?)",
                            (pair, str(value), r.source, _iso(now), _iso(r.expires_at), set_by))
            self._event(now, "manual_rate", pair, {"value": str(value), "source": r.source, "by": set_by})
        self._manual_rates_cache = None
        return r

    @_locked
    def manual_rates(self) -> list[Rate]:
        if self._manual_rates_cache is not None:
            return list(self._manual_rates_cache)
        rows = self.db.execute(
            "SELECT pair, value, source, set_at, expires_at, MAX(id) AS max_id "
            "FROM manual_rates GROUP BY pair ORDER BY max_id"
        ).fetchall()
        res = [
            Rate(row["pair"], Decimal(row["value"]), row["source"], _dt(row["set_at"]), "manual", _dt(row["expires_at"]))
            for row in rows
        ]
        self._manual_rates_cache = res
        return list(res)

    # ---------------------------------------------------------------- quotes and deals
    @_locked
    def save_quote(self, q: Quote, *, crm_status: str = "quoted") -> None:
        if crm_status not in ("draft", "quoted"):
            raise StoreError(f"недопустимый статус котировки: {crm_status}")
        with self.db:
            self.db.execute("INSERT OR IGNORE INTO quotes(id, body, created_at, valid_until, crm_status) VALUES (?,?,?,?,?)",
                            (q.id, _quote_body(q), _iso(q.created_at), _iso(q.valid_until), crm_status))
            self._event(q.created_at, "quote", q.id, {"give": f"{q.give_amount} {q.give_ccy}",
                                                      "get": f"{q.get_amount} {q.get_ccy}", "crm_status": crm_status})

    @_locked
    def quote(self, qid: str) -> dict | None:
        row = self.db.execute("SELECT body, crm_status FROM quotes WHERE id=?", (qid.upper(),)).fetchone()
        if not row:
            return None
        data = json.loads(row["body"])
        data["crm_status"] = row["crm_status"]
        return data

    @_locked
    def confirm(self, qid: str, now: datetime, by: str, *,
                crm_status: str = "settled",
                leg_in_actual: Decimal | None = None,
                leg_out_actual: Decimal | None = None) -> tuple[dict, bool]:
        """Record 'exchange done' for a quote. Returns (deal, created). A second confirmation returns the same deal."""
        if crm_status not in ("accepted", "settled"):
            raise StoreError(f"недопустимый CRM-статус подтверждения: {crm_status}")
        q = self.quote(qid)
        if q is None:
            raise StoreError(f"котировка {qid} не найдена")
        existing = self.db.execute("SELECT * FROM deals WHERE quote_id=?", (q["id"],)).fetchone()
        if existing:
            return dict(existing), False
        if now > _dt(q["valid_until"]):
            raise StoreError(f"котировка {q['id']} просрочена ({q['valid_until']}); сделайте новую котировку")
        in_act = str(leg_in_actual if leg_in_actual is not None else q["give_amount"])
        out_act = str(leg_out_actual if leg_out_actual is not None else q["get_amount"])
        fee_rate = str(q.get("margin", "0.03"))
        try:
            with self.db:
                cur = self.db.execute(
                    "INSERT INTO deals(quote_id, status, crm_status, confirmed_at, confirmed_by, give_ccy, give_amount, "
                    "get_ccy, get_amount, leg_in_actual, leg_out_actual, fee_rate, client_rate, mid) "
                    "VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                    (q["id"], "done", crm_status, _iso(now), by, q["give_ccy"], q["give_amount"], q["get_ccy"], q["get_amount"],
                     in_act, out_act, fee_rate, q["client_rate"], q["mid"]))
                self._event(now, "deal_done", f"D-{cur.lastrowid}",
                            {"quote": q["id"], "by": by, "crm_status": crm_status,
                             "leg_in_actual": in_act, "leg_out_actual": out_act, "fee_rate": fee_rate})
        except sqlite3.IntegrityError:
            # the pult and the phone app are separate processes on one file: the other one confirmed first
            row = self.db.execute("SELECT * FROM deals WHERE quote_id=?", (q["id"],)).fetchone()
            if row is None:
                raise
            return dict(row), False
        return dict(self.db.execute("SELECT * FROM deals WHERE id=?", (cur.lastrowid,)).fetchone()), True

    @_locked
    def reverse(self, deal_id: int, reason: str, now: datetime, by: str) -> dict:
        row = self.db.execute("SELECT * FROM deals WHERE id=?", (deal_id,)).fetchone()
        if row is None:
            raise StoreError(f"сделка D-{deal_id} не найдена")
        if row["status"] == "reversed":
            raise StoreError(f"сделка D-{deal_id} уже отменена")
        if not reason.strip():
            raise StoreError("укажите причину отмены")
        with self.db:
            self.db.execute("UPDATE deals SET status='reversed', crm_status='cancelled', reversed_at=?, reverse_reason=? WHERE id=?",
                            (_iso(now), reason.strip()[:200], deal_id))
            self._event(now, "deal_reversed", f"D-{deal_id}", {"reason": reason.strip()[:200], "by": by, "crm_status": "cancelled"})
        return dict(self.db.execute("SELECT * FROM deals WHERE id=?", (deal_id,)).fetchone())

    @_locked
    def deals(self) -> list[dict]:
        return [dict(r) for r in self.db.execute("SELECT * FROM deals ORDER BY id")]

    @_locked
    def legs(self) -> list[dict]:
        """Ledger view: each deal = two legs from the desk's side; a reversal adds two opposite legs."""
        out = []
        for d in self.deals():
            ref = f"D-{d['id']}"
            in_amt = Decimal(d.get("leg_in_actual") or d["give_amount"])
            out_amt = Decimal(d.get("leg_out_actual") or d["get_amount"])
            out += [dict(ref=ref, ts=d["confirmed_at"], ccy=d["give_ccy"], amount=in_amt, kind="inflow"),
                    dict(ref=ref, ts=d["confirmed_at"], ccy=d["get_ccy"], amount=-out_amt, kind="outflow")]
            if d["status"] == "reversed":
                out += [dict(ref=ref, ts=d["reversed_at"], ccy=d["give_ccy"], amount=-in_amt, kind="reversal"),
                        dict(ref=ref, ts=d["reversed_at"], ccy=d["get_ccy"], amount=out_amt, kind="reversal")]
        return out

    @_locked
    def dashboard(self, start: datetime, end: datetime) -> dict:
        if end <= start:
            raise StoreError("период: конец должен быть позже начала")
        all_deals = self.deals()
        active = [d for d in all_deals if d["status"] == "done" and start <= _dt(d["confirmed_at"]) < end]
        reversed_n = sum(1 for d in all_deals if d["status"] == "reversed" and start <= _dt(d["confirmed_at"]) < end)
        turnover, no_usdt = Decimal(0), 0
        position: dict[str, Decimal] = {}
        for d in active:
            if d["give_ccy"] == "USDT":
                turnover += Decimal(d["give_amount"])
            elif d["get_ccy"] == "USDT":
                turnover += Decimal(d["get_amount"])
            else:
                no_usdt += 1
            position[d["give_ccy"]] = position.get(d["give_ccy"], Decimal(0)) + Decimal(d["give_amount"])
            position[d["get_ccy"]] = position.get(d["get_ccy"], Decimal(0)) - Decimal(d["get_amount"])
        return {"start": _iso(start), "end": _iso(end), "deals": len(active), "reversed": reversed_n,
                "turnover_usdt": turnover, "deals_without_usdt_leg": no_usdt,
                "position_change": dict(sorted(position.items())),
                "profit": "N/A — нет данных о себестоимости (по какому курсу куплена валюта)"}

    # ---------------------------------------------------------------- backup / restore
    @_locked
    def backup(self, target: str | Path) -> Path:
        target = Path(target)
        target.parent.mkdir(parents=True, exist_ok=True)
        dst = sqlite3.connect(str(target))
        try:
            self.db.backup(dst)
        finally:
            dst.close()
        return target

    @staticmethod
    def verify(path: str | Path) -> None:
        if not Path(path).is_file():
            raise StoreError(f"файл копии не найден: {path}")
        con = sqlite3.connect(f"file:{Path(path).as_posix()}?mode=ro", uri=True)
        try:
            ok = con.execute("PRAGMA integrity_check").fetchone()[0]
            names = {r[0] for r in con.execute("SELECT name FROM sqlite_master WHERE type='table'")}
        except sqlite3.DatabaseError as e:
            raise StoreError(f"это не база помощника: {e}") from None
        finally:
            con.close()
        if ok != "ok":
            raise StoreError(f"копия повреждена: {ok}")
        if not TABLES <= names:
            raise StoreError(f"в копии нет таблиц: {', '.join(sorted(TABLES - names))}")

    @_locked
    def restore(self, source: str | Path) -> None:
        """Replace the live book with a verified copy; the current book is saved next to it first."""
        Store.verify(source)
        self.backup(self.path.with_suffix(".before-restore.db"))
        src = sqlite3.connect(f"file:{Path(source).as_posix()}?mode=ro", uri=True)
        try:
            src.backup(self.db)
        finally:
            src.close()
        self._manual_rates_cache = None
