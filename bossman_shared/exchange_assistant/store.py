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
  slippage_in TEXT, slippage_out TEXT, idempotency_key TEXT,
  client_rate TEXT NOT NULL, mid TEXT NOT NULL, reversed_at TEXT, reverse_reason TEXT);
CREATE INDEX IF NOT EXISTS idx_deals_confirmed_at ON deals(confirmed_at);
CREATE TABLE IF NOT EXISTS events(id INTEGER PRIMARY KEY, ts TEXT NOT NULL, kind TEXT NOT NULL, ref TEXT NOT NULL, detail TEXT NOT NULL);
CREATE TRIGGER IF NOT EXISTS events_no_update BEFORE UPDATE ON events BEGIN SELECT RAISE(ABORT, 'events are append-only'); END;
CREATE TRIGGER IF NOT EXISTS events_no_delete BEFORE DELETE ON events BEGIN SELECT RAISE(ABORT, 'events are append-only'); END;
CREATE TRIGGER IF NOT EXISTS deals_no_delete BEFORE DELETE ON deals BEGIN SELECT RAISE(ABORT, 'deals are never deleted; reverse instead'); END;
"""
TABLES = {"manual_rates", "quotes", "deals", "events"}
TRIGGERS = {"events_no_update", "events_no_delete", "deals_no_delete"}
MAX_LEG_DEVIATION_PCT = Decimal("10")


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


def _to_dec(raw: object, name: str = "сумма") -> Decimal:
    from decimal import InvalidOperation
    try:
        val = Decimal(str(raw).strip().replace(",", ".").replace(" ", ""))
    except (InvalidOperation, ValueError, TypeError):
        raise StoreError(f"{name}: некорректное число ({raw!r})") from None
    if not val.is_finite():
        raise StoreError(f"{name}: число должно быть конечным")
    return val


def _quote_body(q: Quote) -> str:
    return json.dumps({"id": q.id, "give_ccy": q.give_ccy, "give_amount": str(q.give_amount), "get_ccy": q.get_ccy,
                       "get_amount": str(q.get_amount), "mid": str(q.mid), "client_rate": str(q.client_rate),
                       "margin": str(q.margin), "created_at": _iso(q.created_at), "valid_until": _iso(q.valid_until),
                       "rates": list(q.rates), "formula": q.formula, "fixed_side": q.fixed_side,
                       "direction": getattr(q, "direction", "SELL"),
                       "manager_margin_decision": getattr(q, "manager_margin_decision", "default")}, ensure_ascii=False)


def _locked(fn):
    """One connection shared by the web server's worker threads: every public call runs under the store's lock."""
    @functools.wraps(fn)
    def wrapper(self, *a, **kw):
        with self.lock:
            return fn(self, *a, **kw)
    return wrapper


class Store:
    def __init__(self, path: str | Path, *, stop_file: str | Path | None = None):
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.stop_file: Path | None = Path(stop_file) if stop_file else None
        self.lock = threading.RLock()
        self.db = sqlite3.connect(str(self.path), check_same_thread=False, timeout=10)
        self.db.row_factory = sqlite3.Row
        self.db.execute("PRAGMA journal_mode=WAL")
        self.db.execute("PRAGMA busy_timeout=10000")
        self.db.executescript(SCHEMA)
        self._migrate()

    def _check_stop(self) -> None:
        if self.stop_file is not None:
            try:
                stopped = self.stop_file.is_file()
            except OSError:
                stopped = True
            if stopped:
                raise StoreError("⛔ Действует СТОП/пауза: записи (курс, котировка, сделка, отмена) заблокированы до «Продолжить».")

    def _migrate(self) -> None:
        qcols = {r[1] for r in self.db.execute("PRAGMA table_info(quotes)")}
        if "crm_status" not in qcols:
            try:
                self.db.execute("ALTER TABLE quotes ADD COLUMN crm_status TEXT NOT NULL DEFAULT 'quoted'")
            except sqlite3.OperationalError as exc:
                if "duplicate column" not in str(exc).lower():
                    raise
        dcols = {r[1] for r in self.db.execute("PRAGMA table_info(deals)")}
        for col, default in (("crm_status", "'settled'"), ("leg_in_actual", "NULL"),
                             ("leg_out_actual", "NULL"), ("fee_rate", "NULL"),
                             ("slippage_in", "NULL"), ("slippage_out", "NULL"),
                             ("idempotency_key", "NULL")):
            if col not in dcols:
                try:
                    self.db.execute(f"ALTER TABLE deals ADD COLUMN {col} TEXT DEFAULT {default}")
                except sqlite3.OperationalError as exc:
                    if "duplicate column" not in str(exc).lower():
                        raise
        self.db.execute("UPDATE deals SET crm_status='cancelled' WHERE status='reversed' AND (crm_status IS NULL OR crm_status != 'cancelled')")
        self.db.execute("UPDATE deals SET crm_status='settled' WHERE status='done' AND crm_status IS NULL")
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
        self._check_stop()
        pair = pair.upper()
        value = _to_dec(value, "курс")
        check_bounds(pair, value)
        if not source.strip():
            raise StoreError("для ручного курса нужен источник (откуда цифра)")
        r = Rate(pair, value, source.strip()[:80], now, "manual", now + ttl)
        with self.db:
            self.db.execute("INSERT INTO manual_rates(pair, value, source, set_at, expires_at, set_by) VALUES (?,?,?,?,?,?)",
                            (pair, str(value), r.source, _iso(now), _iso(r.expires_at), set_by))
            self._event(now, "manual_rate", pair, {"value": str(value), "source": r.source, "by": set_by})
        return r

    @_locked
    def manual_rates(self) -> list[Rate]:
        rows = self.db.execute(
            "SELECT id, pair, value, source, set_at, expires_at "
            "FROM manual_rates ORDER BY id ASC"
        ).fetchall()
        chosen: dict[str, tuple[datetime, int, Rate]] = {}
        for row in rows:
            as_of = _dt(row["set_at"])
            rate = Rate(row["pair"], _to_dec(row["value"], "курс"), row["source"], as_of, "manual", _dt(row["expires_at"]))
            prev = chosen.get(row["pair"])
            if prev is None or (as_of, int(row["id"])) >= (prev[0], prev[1]):
                chosen[row["pair"]] = (as_of, int(row["id"]), rate)
        return [item[2] for item in chosen.values()]

    # ---------------------------------------------------------------- quotes and deals
    @_locked
    def save_quote(self, q: Quote, *, crm_status: str = "quoted") -> None:
        self._check_stop()
        if crm_status not in ("draft", "quoted"):
            raise StoreError(f"недопустимый статус котировки: {crm_status}")
        with self.db:
            self.db.execute("INSERT OR IGNORE INTO quotes(id, body, created_at, valid_until, crm_status) VALUES (?,?,?,?,?)",
                            (q.id, _quote_body(q), _iso(q.created_at), _iso(q.valid_until), crm_status))
            self._event(q.created_at, "quote", q.id, {"give": f"{q.give_amount} {q.give_ccy}",
                                                      "get": f"{q.get_amount} {q.get_ccy}", "crm_status": crm_status,
                                                      "direction": getattr(q, "direction", "SELL"),
                                                      "manager_margin_decision": getattr(q, "manager_margin_decision", "default")})

    @_locked
    def quote(self, qid: str) -> dict | None:
        row = self.db.execute("SELECT body, crm_status FROM quotes WHERE id=?", (qid.upper(),)).fetchone()
        if not row:
            return None
        data = json.loads(row["body"])
        data["crm_status"] = row["crm_status"]
        return data

    @_locked
    def quote_object(self, qid: str) -> Quote | None:
        data = self.quote(qid)
        if data is None:
            return None
        return Quote(
            id=data["id"],
            give_ccy=data["give_ccy"],
            give_amount=_to_dec(data["give_amount"], "give_amount"),
            get_ccy=data["get_ccy"],
            get_amount=_to_dec(data["get_amount"], "get_amount"),
            mid=_to_dec(data["mid"], "mid"),
            client_rate=_to_dec(data["client_rate"], "client_rate"),
            margin=_to_dec(data.get("margin", "0.03"), "margin"),
            created_at=_dt(data["created_at"]),
            valid_until=_dt(data["valid_until"]),
            rates=tuple(data.get("rates") or ()),
            formula=str(data.get("formula") or ""),
            fixed_side=str(data.get("fixed_side") or "give"),
            direction=str(data.get("direction") or "SELL"),
            manager_margin_decision=str(data.get("manager_margin_decision") or "default"),
        )

    @_locked
    def receipt_pdf_path(self, deal_id: int, quote_id: str | None = None) -> Path:
        if quote_id is None:
            row = self.db.execute("SELECT quote_id FROM deals WHERE id=?", (deal_id,)).fetchone()
            if row is None:
                raise StoreError(f"сделка D-{deal_id} не найдена")
            quote_id = str(row["quote_id"])
        rec_dir = self.path.parent / "receipts"
        rec_dir.mkdir(parents=True, exist_ok=True)
        return rec_dir / f"SwapMe-D-{deal_id}-{quote_id}.pdf"

    def _write_receipt_pdf(self, deal_row: dict) -> Path:
        self._check_stop()
        from .quote import render_pdf_document
        q_obj = self.quote_object(deal_row["quote_id"])
        if q_obj is None:
            raise StoreError(f"котировка {deal_row['quote_id']} не найдена")
        pdf_bytes = render_pdf_document(q_obj, deal=deal_row)
        target = self.receipt_pdf_path(int(deal_row["id"]), str(deal_row["quote_id"]))
        tmp = target.with_suffix(".pdf.tmp")
        tmp.write_bytes(pdf_bytes)
        os.replace(tmp, target)
        return target

    @staticmethod
    def _validate_actual_legs(q: dict, leg_in_actual: object, leg_out_actual: object, *,
                              max_deviation_pct: Decimal = MAX_LEG_DEVIATION_PCT,
                              confirm_deviation: bool = False) -> tuple[Decimal, Decimal, str, str]:
        in_dec = _to_dec(leg_in_actual, "приняли").quantize(Decimal("0.01"))
        out_dec = _to_dec(leg_out_actual, "выдали").quantize(Decimal("0.01"))
        if in_dec <= 0 or out_dec <= 0:
            raise StoreError("фактические суммы обеих сторон должны быть больше 0")
        q_give = _to_dec(q["give_amount"], "give_amount")
        q_get = _to_dec(q["get_amount"], "get_amount")
        dev_in_pct = (abs(in_dec - q_give) * Decimal(100) / q_give) if q_give > 0 else Decimal(0)
        dev_out_pct = (abs(out_dec - q_get) * Decimal(100) / q_get) if q_get > 0 else Decimal(0)
        if not confirm_deviation and (dev_in_pct > max_deviation_pct or dev_out_pct > max_deviation_pct):
            raise StoreError(
                f"расхождение фактических сумм с котировкой {q['id']} превышает ±{max_deviation_pct}% "
                f"(приняли {in_dec} при расчёте {q_give} [{dev_in_pct.quantize(Decimal('0.01'))}%], "
                f"выдали {out_dec} при расчёте {q_get} [{dev_out_pct.quantize(Decimal('0.01'))}%]); "
                f"проверьте суммы или подтвердите отклонение (подтверждаю расхождение)"
            )
        slip_in = str(in_dec - q_give)
        slip_out = str(out_dec - q_get)
        return in_dec, out_dec, slip_in, slip_out

    @_locked
    def confirm(self, qid: str, now: datetime, by: str, *,
                crm_status: str = "settled",
                leg_in_actual: Decimal | None = None,
                leg_out_actual: Decimal | None = None,
                idempotency_key: str | None = None,
                max_deviation_pct: Decimal = MAX_LEG_DEVIATION_PCT,
                confirm_deviation: bool = False) -> tuple[dict, bool]:
        """Record deal confirmation for a quote.
        - Only quotes in status `quoted` can be confirmed (`cancelled` and `draft` are refused).
        - Settled deals (`crm_status='settled'`) require explicit `leg_in_actual` and `leg_out_actual`
          from the manager within ±max_deviation_pct (unless `confirm_deviation=True`).
        - Accepted deals (`crm_status='accepted'`) without actual amounts record `'unknown'` until settled.
        """
        self._check_stop()
        if crm_status not in ("accepted", "settled"):
            raise StoreError(f"недопустимый CRM-статус подтверждения: {crm_status}")
        if crm_status == "settled" and (leg_in_actual is None or leg_out_actual is None):
            raise StoreError(
                "укажите фактические суммы обеих сторон (приняли и выдали) перед записью проведённой сделки "
                "(пока суммы неизвестны, статус: unknown / accepted)"
            )
        q = self.quote(qid)
        if q is None:
            raise StoreError(f"котировка {qid} не найдена")
        q_status = str(q.get("crm_status") or "quoted")
        if q_status == "cancelled":
            raise StoreError(f"котировка {q['id']} отменена")
        if q_status == "draft":
            raise StoreError(f"котировка {q['id']} в статусе черновика (draft) — сначала выпустите котировку (quoted)")
        idem = (idempotency_key or q["id"]).strip()
        existing = self.db.execute("SELECT * FROM deals WHERE quote_id=? OR (idempotency_key IS NOT NULL AND idempotency_key=?)",
                                   (q["id"], idem)).fetchone()
        if existing:
            existing_dict = dict(existing)
            if existing_dict.get("crm_status") == "accepted" and crm_status == "settled" and leg_in_actual is not None and leg_out_actual is not None:
                in_dec, out_dec, slip_in, slip_out = self._validate_actual_legs(
                    q, leg_in_actual, leg_out_actual,
                    max_deviation_pct=max_deviation_pct, confirm_deviation=confirm_deviation,
                )
                in_act, out_act = str(in_dec), str(out_dec)
                with self.db:
                    self.db.execute(
                        "UPDATE deals SET crm_status='settled', confirmed_at=?, confirmed_by=?, "
                        "leg_in_actual=?, leg_out_actual=?, slippage_in=?, slippage_out=? WHERE id=?",
                        (_iso(now), by, in_act, out_act, slip_in, slip_out, existing_dict["id"]),
                    )
                    self.db.execute("UPDATE quotes SET crm_status='settled' WHERE id=?", (q["id"],))
                    self._event(now, "deal_settled", f"D-{existing_dict['id']}",
                                {"quote": q["id"], "by": by, "crm_status": "settled",
                                 "leg_in_actual": in_act, "leg_out_actual": out_act,
                                 "slippage_in": slip_in, "slippage_out": slip_out})
                deal_dict = dict(self.db.execute("SELECT * FROM deals WHERE id=?", (existing_dict["id"],)).fetchone())
                self._write_receipt_pdf(deal_dict)
                return deal_dict, True
            self._write_receipt_pdf(existing_dict)
            return existing_dict, False
        if q_status != "quoted":
            raise StoreError(f"котировка {q['id']} имеет статус {q_status!r} (подтверждение разрешено только для 'quoted')")
        if now > _dt(q["valid_until"]):
            raise StoreError(f"котировка {q['id']} просрочена ({q['valid_until']}); сделайте новую котировку")
        if leg_in_actual is not None and leg_out_actual is not None:
            in_dec, out_dec, slip_in, slip_out = self._validate_actual_legs(
                q, leg_in_actual, leg_out_actual,
                max_deviation_pct=max_deviation_pct, confirm_deviation=confirm_deviation,
            )
            in_act, out_act = str(in_dec), str(out_dec)
        else:
            in_act = str(_to_dec(leg_in_actual, "приняли").quantize(Decimal("0.01"))) if leg_in_actual is not None else "unknown"
            out_act = str(_to_dec(leg_out_actual, "выдали").quantize(Decimal("0.01"))) if leg_out_actual is not None else "unknown"
            slip_in = "unknown"
            slip_out = "unknown"
        fee_rate = str(q.get("margin", "0.03"))
        try:
            with self.db:
                cur = self.db.execute(
                    "INSERT INTO deals(quote_id, status, crm_status, confirmed_at, confirmed_by, give_ccy, give_amount, "
                    "get_ccy, get_amount, leg_in_actual, leg_out_actual, fee_rate, slippage_in, slippage_out, idempotency_key, client_rate, mid) "
                    "VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                    (q["id"], "done", crm_status, _iso(now), by, q["give_ccy"], q["give_amount"], q["get_ccy"], q["get_amount"],
                     in_act, out_act, fee_rate, slip_in, slip_out, idem, q["client_rate"], q["mid"]))
                self.db.execute("UPDATE quotes SET crm_status=? WHERE id=?", (crm_status, q["id"]))
                self._event(now, "deal_done", f"D-{cur.lastrowid}",
                            {"quote": q["id"], "by": by, "crm_status": crm_status,
                             "leg_in_actual": in_act, "leg_out_actual": out_act,
                             "slippage_in": slip_in, "slippage_out": slip_out,
                             "idempotency_key": idem, "fee_rate": fee_rate})
        except sqlite3.IntegrityError:
            row = self.db.execute("SELECT * FROM deals WHERE quote_id=?", (q["id"],)).fetchone()
            if row is None:
                raise
            deal_dict = dict(row)
            self._write_receipt_pdf(deal_dict)
            return deal_dict, False
        deal_dict = dict(self.db.execute("SELECT * FROM deals WHERE id=?", (cur.lastrowid,)).fetchone())
        self._write_receipt_pdf(deal_dict)
        return deal_dict, True

    @_locked
    def reverse(self, deal_id: int, reason: str, now: datetime, by: str) -> dict:
        self._check_stop()
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
            self.db.execute("UPDATE quotes SET crm_status='cancelled' WHERE id=?", (row["quote_id"],))
            self._event(now, "deal_reversed", f"D-{deal_id}", {"reason": reason.strip()[:200], "by": by, "crm_status": "cancelled"})
        deal_dict = dict(self.db.execute("SELECT * FROM deals WHERE id=?", (deal_id,)).fetchone())
        self._write_receipt_pdf(deal_dict)
        return deal_dict

    @_locked
    def cancel_quote(self, qid: str, reason: str, now: datetime, by: str) -> dict:
        """Cancel an unconfirmed quote (draft/quoted -> cancelled)."""
        self._check_stop()
        q = self.quote(qid)
        if q is None:
            raise StoreError(f"котировка {qid} не найдена")
        existing = self.db.execute("SELECT id FROM deals WHERE quote_id=?", (q["id"],)).fetchone()
        if existing:
            raise StoreError(f"по котировке {q['id']} уже есть сделка D-{existing['id']}; отменяйте сделку")
        if q.get("crm_status") == "cancelled":
            return q
        with self.db:
            self.db.execute("UPDATE quotes SET crm_status='cancelled' WHERE id=?", (q["id"],))
            self._event(now, "quote_cancelled", q["id"], {"reason": reason.strip()[:200], "by": by, "crm_status": "cancelled"})
        return self.quote(q["id"])  # type: ignore[return-value]

    @_locked
    def deals(self) -> list[dict]:
        return [dict(r) for r in self.db.execute("SELECT * FROM deals ORDER BY id")]

    @staticmethod
    def _actual_or_quoted(raw_actual: str | None, quoted: str) -> Decimal:
        if raw_actual and str(raw_actual) != "unknown":
            return _to_dec(raw_actual)
        return _to_dec(quoted)

    @_locked
    def legs(self) -> list[dict]:
        """Ledger view: each settled deal = two legs from the desk's side; a reversal adds two opposite legs."""
        out = []
        for d in self.deals():
            if d.get("crm_status") == "accepted" and (d.get("leg_in_actual") == "unknown" or d.get("leg_out_actual") == "unknown"):
                continue
            ref = f"D-{d['id']}"
            in_amt = self._actual_or_quoted(d.get("leg_in_actual"), d["give_amount"])
            out_amt = self._actual_or_quoted(d.get("leg_out_actual"), d["get_amount"])
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
        active = [
            d for d in all_deals
            if d["status"] == "done"
            and d.get("crm_status", "settled") == "settled"
            and d.get("leg_in_actual") != "unknown"
            and d.get("leg_out_actual") != "unknown"
            and start <= _dt(d["confirmed_at"]) < end
        ]
        reversed_n = sum(
            1 for d in all_deals
            if (d["status"] == "reversed" or d.get("crm_status") == "cancelled")
            and start <= _dt(d["confirmed_at"]) < end
        )
        turnover, no_usdt = Decimal(0), 0
        position: dict[str, Decimal] = {}
        for d in active:
            in_amt = self._actual_or_quoted(d.get("leg_in_actual"), d["give_amount"])
            out_amt = self._actual_or_quoted(d.get("leg_out_actual"), d["get_amount"])
            if d["give_ccy"] == "USDT":
                turnover += in_amt
            elif d["get_ccy"] == "USDT":
                turnover += out_amt
            else:
                no_usdt += 1
            position[d["give_ccy"]] = position.get(d["give_ccy"], Decimal(0)) + in_amt
            position[d["get_ccy"]] = position.get(d["get_ccy"], Decimal(0)) - out_amt
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
            triggers = {r[0] for r in con.execute("SELECT name FROM sqlite_master WHERE type='trigger'")}
        except sqlite3.DatabaseError as e:
            raise StoreError(f"это не база помощника: {e}") from None
        finally:
            con.close()
        if ok != "ok":
            raise StoreError(f"копия повреждена: {ok}")
        if not TABLES <= names:
            raise StoreError(f"в копии нет таблиц: {', '.join(sorted(TABLES - names))}")
        if not TRIGGERS <= triggers:
            raise StoreError(f"в копии нет защитных триггеров: {', '.join(sorted(TRIGGERS - triggers))}")

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
        self.db.executescript(SCHEMA)
        self._migrate()
