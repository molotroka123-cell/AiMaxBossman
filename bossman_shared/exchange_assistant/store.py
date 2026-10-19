"""Local SQLite book of the assistant: manual rates, issued quotes, confirmed deals, append-only events.

A deal exists only after an explicit confirmation of a still-valid quote. One quote -> at most one deal
(duplicate confirmations return the same deal). Reversal never deletes: it marks the deal and logs an event.
Turnover counts the USDT leg only; profit is N/A without a cost basis.
"""
from __future__ import annotations

import json
import os
import sqlite3
from datetime import datetime, timezone
from decimal import Decimal
from pathlib import Path

from .quote import Quote
from .rates import MANUAL_DEFAULT_TTL, Rate, check_bounds

SCHEMA = """
CREATE TABLE IF NOT EXISTS manual_rates(id INTEGER PRIMARY KEY, pair TEXT NOT NULL, value TEXT NOT NULL, source TEXT NOT NULL,
  set_at TEXT NOT NULL, expires_at TEXT NOT NULL, set_by TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS quotes(id TEXT PRIMARY KEY, body TEXT NOT NULL, created_at TEXT NOT NULL, valid_until TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS deals(id INTEGER PRIMARY KEY, quote_id TEXT NOT NULL UNIQUE REFERENCES quotes(id),
  status TEXT NOT NULL CHECK(status IN ('done','reversed')), confirmed_at TEXT NOT NULL, confirmed_by TEXT NOT NULL,
  give_ccy TEXT NOT NULL, give_amount TEXT NOT NULL, get_ccy TEXT NOT NULL, get_amount TEXT NOT NULL,
  client_rate TEXT NOT NULL, mid TEXT NOT NULL, reversed_at TEXT, reverse_reason TEXT);
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


class Store:
    def __init__(self, path: str | Path):
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.db = sqlite3.connect(str(self.path))
        self.db.row_factory = sqlite3.Row
        self.db.execute("PRAGMA journal_mode=WAL")
        self.db.executescript(SCHEMA)

    def close(self) -> None:
        self.db.close()

    def _event(self, now: datetime, kind: str, ref: str, detail: dict) -> None:
        self.db.execute("INSERT INTO events(ts, kind, ref, detail) VALUES (?,?,?,?)",
                        (_iso(now), kind, ref, json.dumps(detail, ensure_ascii=False)))

    # ---------------------------------------------------------------- manual rates
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
        return r

    def manual_rates(self) -> list[Rate]:
        out: dict[str, Rate] = {}
        for row in self.db.execute("SELECT * FROM manual_rates ORDER BY id"):
            out[row["pair"]] = Rate(row["pair"], Decimal(row["value"]), row["source"], _dt(row["set_at"]), "manual",
                                    _dt(row["expires_at"]))
        return list(out.values())

    # ---------------------------------------------------------------- quotes and deals
    def save_quote(self, q: Quote) -> None:
        with self.db:
            self.db.execute("INSERT OR IGNORE INTO quotes(id, body, created_at, valid_until) VALUES (?,?,?,?)",
                            (q.id, _quote_body(q), _iso(q.created_at), _iso(q.valid_until)))
            self._event(q.created_at, "quote", q.id, {"give": f"{q.give_amount} {q.give_ccy}", "get": f"{q.get_amount} {q.get_ccy}"})

    def quote(self, qid: str) -> dict | None:
        row = self.db.execute("SELECT body FROM quotes WHERE id=?", (qid.upper(),)).fetchone()
        return json.loads(row["body"]) if row else None

    def confirm(self, qid: str, now: datetime, by: str) -> tuple[dict, bool]:
        """Record 'exchange done' for a quote. Returns (deal, created). A second confirmation returns the same deal."""
        q = self.quote(qid)
        if q is None:
            raise StoreError(f"котировка {qid} не найдена")
        existing = self.db.execute("SELECT * FROM deals WHERE quote_id=?", (q["id"],)).fetchone()
        if existing:
            return dict(existing), False
        if now > _dt(q["valid_until"]):
            raise StoreError(f"котировка {q['id']} просрочена ({q['valid_until']}); сделайте новую котировку")
        with self.db:
            cur = self.db.execute(
                "INSERT INTO deals(quote_id, status, confirmed_at, confirmed_by, give_ccy, give_amount, get_ccy, get_amount, client_rate, mid) "
                "VALUES (?,?,?,?,?,?,?,?,?,?)",
                (q["id"], "done", _iso(now), by, q["give_ccy"], q["give_amount"], q["get_ccy"], q["get_amount"],
                 q["client_rate"], q["mid"]))
            self._event(now, "deal_done", f"D-{cur.lastrowid}", {"quote": q["id"], "by": by})
        return dict(self.db.execute("SELECT * FROM deals WHERE id=?", (cur.lastrowid,)).fetchone()), True

    def reverse(self, deal_id: int, reason: str, now: datetime, by: str) -> dict:
        row = self.db.execute("SELECT * FROM deals WHERE id=?", (deal_id,)).fetchone()
        if row is None:
            raise StoreError(f"сделка D-{deal_id} не найдена")
        if row["status"] == "reversed":
            raise StoreError(f"сделка D-{deal_id} уже отменена")
        if not reason.strip():
            raise StoreError("укажите причину отмены")
        with self.db:
            self.db.execute("UPDATE deals SET status='reversed', reversed_at=?, reverse_reason=? WHERE id=?",
                            (_iso(now), reason.strip()[:200], deal_id))
            self._event(now, "deal_reversed", f"D-{deal_id}", {"reason": reason.strip()[:200], "by": by})
        return dict(self.db.execute("SELECT * FROM deals WHERE id=?", (deal_id,)).fetchone())

    def deals(self) -> list[dict]:
        return [dict(r) for r in self.db.execute("SELECT * FROM deals ORDER BY id")]

    def legs(self) -> list[dict]:
        """Ledger view: each deal = two legs from the desk's side; a reversal adds two opposite legs."""
        out = []
        for d in self.deals():
            ref = f"D-{d['id']}"
            out += [dict(ref=ref, ts=d["confirmed_at"], ccy=d["give_ccy"], amount=Decimal(d["give_amount"]), kind="inflow"),
                    dict(ref=ref, ts=d["confirmed_at"], ccy=d["get_ccy"], amount=-Decimal(d["get_amount"]), kind="outflow")]
            if d["status"] == "reversed":
                out += [dict(ref=ref, ts=d["reversed_at"], ccy=d["give_ccy"], amount=-Decimal(d["give_amount"]), kind="reversal"),
                        dict(ref=ref, ts=d["reversed_at"], ccy=d["get_ccy"], amount=Decimal(d["get_amount"]), kind="reversal")]
        return out

    def dashboard(self, start: datetime, end: datetime) -> dict:
        if end <= start:
            raise StoreError("период: конец должен быть позже начала")
        active = [d for d in self.deals() if d["status"] == "done" and start <= _dt(d["confirmed_at"]) < end]
        reversed_n = sum(1 for d in self.deals() if d["status"] == "reversed" and start <= _dt(d["confirmed_at"]) < end)
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

    def restore(self, source: str | Path) -> None:
        """Replace the live book with a verified copy; the current book is saved next to it first."""
        Store.verify(source)
        self.backup(self.path.with_suffix(".before-restore.db"))
        src = sqlite3.connect(f"file:{Path(source).as_posix()}?mode=ro", uri=True)
        try:
            src.backup(self.db)
        finally:
            src.close()
