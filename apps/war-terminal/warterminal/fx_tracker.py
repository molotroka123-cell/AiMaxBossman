"""Daily FX tracker: official central-bank rates in an append-only SQLite log, cross-rates, honest statistics, a USDT/USD -> EUR/CZK calculator.

Sources (decision 18.10.2026): the Czech National Bank daily fixing (USD/CZK, EUR/CZK) and the ECB euro reference rates (EUR/USD, EUR/CZK).
xe.com is NOT used: its terms of use forbid scraping and systematic extraction/caching of currency data (see .agents/skills/fx-daily-rates).
Deterministic regex parsers are the source of truth. A free cloud model (Bossman gateway, price 0/0) only cross-checks them: a gap over
0.5 % flags both records "disagreement" and keeps them out of every report; a reply with usage.cost > 0 is dropped and the check aborted.
Mid-market / fixing values only: exchange fees and spreads are NOT included. Nothing here predicts prices or advises trades.
"""
from __future__ import annotations

import csv
import json
import os
import re
import sqlite3
import statistics
import sys
import time
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal, InvalidOperation
from pathlib import Path

CNB_URL = "https://www.cnb.cz/en/financial-markets/foreign-exchange-market/central-bank-exchange-rate-fixing/central-bank-exchange-rate-fixing/daily.txt"
ECB_URL = "https://www.ecb.europa.eu/stats/eurofxref/eurofxref-daily.xml"
DISAGREE_PCT = Decimal("0.5")
MAX_VISITS_PER_DAY = 2
BOUNDS = {"USDCZK": (Decimal(5), Decimal(100)), "EURCZK": (Decimal(5), Decimal(100)), "EURUSD": (Decimal("0.3"), Decimal(3))}
TERMINAL_JOB = {"completed", "done", "failed", "error", "cancelled", "canceled", "rejected", "expired"}
# Owner rule 18.10 (coordinator): a job that is already `generating` (sd-cli ~4 of 32 threads, GPU mostly idle, measured) does NOT block this
# light collection; a job that is loading weights (RAM/disk heavy) does, and so does low free RAM.
ACTIVE_JOB = {"loading", "starting", "preparing", "downloading"}
MIN_FREE_RAM_GB = 20.0

NOTES_RU = [
    "Только курсы центральных банков (ЧНБ, ЕЦБ), «середина рынка»: комиссии и спреды бирж и обменников НЕ учтены.",
    "USDT принят равным USD (допущение, не измерено): реальный курс Tether к доллару может отличаться.",
    "Это учёт и статистика прошлых значений. Прогнозов цен, советов по торговле и утверждений о будущем росте здесь нет.",
    "XE.com не используется: его условия запрещают автоматический сбор данных. Курсы берутся из официальных публичных файлов ЧНБ и ЕЦБ.",
]


def fx_dir(env: dict | None = None) -> Path:
    env = os.environ if env is None else env
    if env.get("BOSSMAN_FX_DIR"):
        return Path(env["BOSSMAN_FX_DIR"])
    base = env.get("LOCALAPPDATA")
    return Path(base) / "Bossman" / "CommandCenter" / "fx" if base else Path.home() / ".war-terminal" / "fx"


def _now() -> datetime:
    return datetime.now(timezone.utc).replace(microsecond=0)


def _dec(v) -> Decimal | None:
    try:
        d = Decimal(str(v).strip().replace(",", "."))
    except (InvalidOperation, ValueError):
        return None
    return d if d.is_finite() and d > 0 else None


def plausible(pair: str, d: Decimal | None) -> bool:
    lo_hi = BOUNDS.get(pair)
    return d is not None and (lo_hi is None or lo_hi[0] <= d <= lo_hi[1])


# ------------------------------------------------------------------ deterministic parsers (no network, tested on saved files)
def parse_cnb(text: str) -> dict:
    """CNB daily.txt: '16 Oct 2026 #200' / header / 'USA|dollar|1|USD|21.622'. Returns {rate_date, pairs:{USDCZK:(Decimal, excerpt)}}; {} if the layout is not recognised."""
    head = re.match(r"\s*(\d{1,2} [A-Z][a-z]{2} \d{4}) #\d+", text or "")
    if not head:
        return {}
    try:
        rate_date = datetime.strptime(head.group(1), "%d %b %Y").date().isoformat()
    except ValueError:
        return {}
    pairs = {}
    for code in ("USD", "EUR"):
        m = re.search(rf"^[^|\n]+\|[^|\n]+\|(\d+)\|{code}\|([0-9]+(?:[.,][0-9]+)?)\s*$", text, re.M)
        if not m:
            continue
        amount, rate = _dec(m.group(1)), _dec(m.group(2))
        if amount is None or rate is None:
            continue
        val = rate / amount
        if plausible(f"{code}CZK", val):
            pairs[f"{code}CZK"] = (val, f"{head.group(0).strip()} | {m.group(0).strip()}"[:200])
    return {"rate_date": rate_date, "pairs": pairs} if pairs else {}


def parse_ecb(text: str) -> dict:
    """ECB eurofxref-daily.xml: <Cube time='2026-10-16'> <Cube currency='USD' rate='1.1253'/> ... Returns EURUSD and EURCZK (units of USD / CZK per 1 EUR)."""
    t = re.search(r"<Cube\s+time=['\"](\d{4}-\d{2}-\d{2})['\"]", text or "")
    if not t:
        return {}
    pairs = {}
    for code, pair in (("USD", "EURUSD"), ("CZK", "EURCZK")):
        m = re.search(rf"<Cube\s+currency=['\"]{code}['\"]\s+rate=['\"]([0-9.]+)['\"]", text)
        val = _dec(m.group(1)) if m else None
        if plausible(pair, val):
            pairs[pair] = (val, f"ECB reference rates {t.group(1)} | {m.group(0)}"[:200])
    return {"rate_date": t.group(1), "pairs": pairs} if pairs else {}


PARSERS = {"cnb": (CNB_URL, parse_cnb), "ecb": (ECB_URL, parse_ecb)}


# ------------------------------------------------------------------ append-only store
SCHEMA = """
CREATE TABLE IF NOT EXISTS fx_obs(
  id INTEGER PRIMARY KEY, observed_at TEXT NOT NULL, minute TEXT NOT NULL, pair TEXT NOT NULL, rate TEXT NOT NULL, source TEXT NOT NULL,
  source_url TEXT NOT NULL, rate_date TEXT, raw_text_excerpt TEXT NOT NULL, parser TEXT NOT NULL, flags TEXT NOT NULL DEFAULT '', model TEXT,
  UNIQUE(source, pair, minute, parser));
CREATE TABLE IF NOT EXISTS fx_runs(id INTEGER PRIMARY KEY, started_at TEXT NOT NULL, day_local TEXT NOT NULL, outcome TEXT NOT NULL, detail TEXT NOT NULL);
CREATE TRIGGER IF NOT EXISTS fx_obs_no_update BEFORE UPDATE ON fx_obs BEGIN SELECT RAISE(ABORT, 'fx_obs is append-only'); END;
CREATE TRIGGER IF NOT EXISTS fx_obs_no_delete BEFORE DELETE ON fx_obs BEGIN SELECT RAISE(ABORT, 'fx_obs is append-only'); END;
CREATE TRIGGER IF NOT EXISTS fx_runs_no_update BEFORE UPDATE ON fx_runs BEGIN SELECT RAISE(ABORT, 'fx_runs is append-only'); END;
CREATE TRIGGER IF NOT EXISTS fx_runs_no_delete BEFORE DELETE ON fx_runs BEGIN SELECT RAISE(ABORT, 'fx_runs is append-only'); END;
"""


class FxStore:
    def __init__(self, path: str | Path):
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.db = sqlite3.connect(str(self.path))
        self.db.row_factory = sqlite3.Row
        self.db.executescript(SCHEMA)

    def close(self) -> None:
        self.db.close()

    def append(self, *, observed_at: str, pair: str, rate, source: str, source_url: str, rate_date: str | None, excerpt: str,
               parser: str, flags: list[str] | None = None, model: str | None = None) -> bool:
        """True if a new row was written; False when the same source+pair+minute+parser already exists (dedupe). Never updates."""
        cur = self.db.execute(
            "INSERT OR IGNORE INTO fx_obs(observed_at, minute, pair, rate, source, source_url, rate_date, raw_text_excerpt, parser, flags, model) VALUES (?,?,?,?,?,?,?,?,?,?,?)",
            (observed_at, observed_at[:16], pair, str(rate), source, source_url, rate_date, excerpt[:200], parser, ",".join(sorted(set(flags or []))), model))
        self.db.commit()
        return cur.rowcount == 1

    def rows(self) -> list[dict]:
        return [dict(r) for r in self.db.execute("SELECT * FROM fx_obs ORDER BY observed_at, id")]

    def add_run(self, started_at: str, day_local: str, outcome: str, detail: str) -> None:
        self.db.execute("INSERT INTO fx_runs(started_at, day_local, outcome, detail) VALUES (?,?,?,?)", (started_at, day_local, outcome, detail[:500]))
        self.db.commit()

    def done_visits(self, day_local: str) -> int:
        return self.db.execute("SELECT COUNT(*) FROM fx_runs WHERE day_local=? AND outcome='done'", (day_local,)).fetchone()[0]

    def runs(self, limit: int = 20) -> list[dict]:
        return [dict(r) for r in self.db.execute("SELECT * FROM fx_runs ORDER BY id DESC LIMIT ?", (limit,))]

    def backup(self, keep: int = 120, today: date | None = None) -> str | None:
        """One consistent copy per local day in <fx dir>/backup/fx-YYYYMMDD.db (SQLite online backup); old copies beyond `keep` are pruned."""
        d = self.path.parent / "backup"
        d.mkdir(exist_ok=True)
        target = d / f"fx-{(today or date.today()).strftime('%Y%m%d')}.db"
        if target.exists():
            return None
        dst = sqlite3.connect(str(target))
        try:
            self.db.backup(dst)
        finally:
            dst.close()
        for old in sorted(d.glob("fx-*.db"))[:-keep]:
            old.unlink()
        return str(target)


def open_store(directory: str | Path | None = None) -> FxStore:
    return FxStore(Path(directory or fx_dir()) / "fx.db")


# ------------------------------------------------------------------ idle gate: the owner's phone generations have absolute priority
def free_ram_gb() -> float | None:
    """Available physical RAM in GB (Windows GlobalMemoryStatusEx); None when unknown."""
    try:
        import ctypes

        class MS(ctypes.Structure):
            _fields_ = [("l", ctypes.c_ulong), ("load", ctypes.c_ulong), ("tp", ctypes.c_ulonglong), ("ap", ctypes.c_ulonglong), ("tpf", ctypes.c_ulonglong),
                        ("apf", ctypes.c_ulonglong), ("tv", ctypes.c_ulonglong), ("av", ctypes.c_ulonglong), ("ae", ctypes.c_ulonglong)]
        m = MS()
        m.l = ctypes.sizeof(MS)
        ctypes.windll.kernel32.GlobalMemoryStatusEx(ctypes.byref(m))
        return m.ap / 2**30
    except Exception:                                 # noqa: BLE001
        return None


def jobs_busy(jobs: list[dict], now: datetime | None = None, free_gb: float | None = None) -> list[str]:
    """What makes the PC busy: a job that is loading (not generating/queued), or available RAM below MIN_FREE_RAM_GB."""
    busy = []
    if free_gb is not None and free_gb < MIN_FREE_RAM_GB:
        busy.append(f"low_ram_{free_gb:.0f}GB")
    for j in jobs or []:
        st = str(j.get("status") or "").lower()
        if st in ACTIVE_JOB:
            busy.append(st)
        elif st and st not in TERMINAL_JOB and st not in ("generating", "running", "queued"):
            busy.append(st)                           # unknown live state: be careful
    return busy


def fetch_jobs(base: str = "http://127.0.0.1:8801", token_file: str | None = None, timeout: float = 10.0) -> list[dict]:
    """GET /api/direct-gen/jobs. ConnectionError = Bossman is down, so nothing is generating."""
    import urllib.error
    import urllib.request
    tf = token_file or str(Path(os.environ.get("LOCALAPPDATA", "")) / "Bossman" / "CommandCenter" / "token")
    try:
        tok = Path(tf).read_text(encoding="utf-8").strip()
    except OSError:
        tok = ""
    req = urllib.request.Request(base.rstrip("/") + "/api/direct-gen/jobs", headers={"X-BCC-Token": tok})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            return (json.loads(r.read().decode("utf-8")) or {}).get("jobs") or []
    except urllib.error.HTTPError as exc:
        raise RuntimeError(f"HTTP {exc.code}") from None
    except (urllib.error.URLError, OSError) as exc:
        raise ConnectionError(type(exc).__name__) from None


def wait_idle(get_jobs, *, ram=None, sleep=time.sleep, retry_s: int = 1200, max_retries: int = 3, log=print) -> dict:
    """Idle -> go. Busy -> wait retry_s and look again, at most max_retries times, then skip this slot. Bossman down -> go."""
    for attempt in range(max_retries + 1):
        try:
            busy = jobs_busy(get_jobs(), free_gb=(ram or free_ram_gb)())
        except ConnectionError:
            return {"status": "IDLE", "attempts": attempt + 1, "note": "Bossman :8801 не отвечает - генераций нет"}
        except Exception as exc:                      # noqa: BLE001 - unreadable queue = treat as busy, never as idle
            busy = [f"queue_unreadable:{type(exc).__name__}"]
        if not busy:
            return {"status": "IDLE", "attempts": attempt + 1}
        log(f"busy {busy}; attempt {attempt + 1}/{max_retries + 1}")
        if attempt < max_retries:
            sleep(retry_s)
    return {"status": "SKIPPED_BUSY", "attempts": max_retries + 1, "busy": busy}


# ------------------------------------------------------------------ free-model cross-check
class CostAbort(Exception):
    pass


def _model_prompt(source: str, pairs: list[str]) -> list[dict]:
    what = {"USDCZK": "how many Czech koruna (CZK) per 1 US dollar (USD)", "EURCZK": "how many Czech koruna (CZK) per 1 euro (EUR)",
            "EURUSD": "how many US dollars (USD) per 1 euro (EUR)"}
    ask = "; ".join(f'"{p}": {what[p]}' for p in pairs)
    system = ("You read a public exchange-rate file and extract numbers from it. Use ONLY numbers present in the text, never outside knowledge. "
              "Mind the Amount column (rate is for that many units). Reply with ONE JSON object and nothing else: {" + ask + "}. "
              "Use null for a value that is not in the text.")
    return [{"role": "system", "content": system}]


def parse_model_reply(text: str, pairs: list[str]) -> dict[str, Decimal]:
    m = re.search(r"\{.*\}", text or "", re.S)
    if not m:
        return {}
    try:
        data = json.loads(m.group(0))
    except json.JSONDecodeError:
        return {}
    out = {}
    for p in pairs:
        v = data.get(p) if isinstance(data, dict) else None
        d = None if isinstance(v, bool) or v is None else _dec(v)
        if plausible(p, d):
            out[p] = d
    return out


def model_check(gateway, source: str, page_text: str, pairs: list[str], *, halt_file: Path | None = None, today: str | None = None) -> dict:
    """One free-route read of the page text. {status: OK|NOT_RUN|COST_ABORT|FAILED, values, model, reason}. Never raises, never spends."""
    from .translate import RouteError, free_routes
    today = today or _now().date().isoformat()
    if gateway is None:
        return {"status": "NOT_RUN", "reason": "шлюз Bossman не настроен", "values": {}}
    if halt_file and halt_file.exists() and halt_file.read_text(encoding="utf-8").strip().startswith(today):
        return {"status": "COST_ABORT", "reason": "сегодня уже была остановка: cost>0", "values": {}}
    try:
        routes = free_routes(gateway.models(), limit=4)
    except Exception as exc:                          # noqa: BLE001
        return {"status": "NOT_RUN", "reason": f"список моделей Bossman недоступен: {type(exc).__name__}", "values": {}}
    if not routes:
        return {"status": "NOT_RUN", "reason": "нет бесплатных маршрутов (price 0/0 + free tier)", "values": {}}
    msgs = _model_prompt(source, pairs) + [{"role": "user", "content": (page_text or "")[:6000]}]
    errors = []
    for route in routes:
        try:
            res = gateway.free_chat(route["id"], msgs, 1024)           # thinking models need >= 512
        except RouteError as exc:
            if exc.status in (404, 405):                              # gateway route absent: the backend was not restarted with it
                return {"status": "NOT_RUN", "reason": "шлюз /api/war-terminal/free-chat отсутствует (Bossman нужно перезапустить)", "values": {}}
            errors.append(f"{route.get('alias')}: {str(exc)[:60]}")
            continue
        except Exception as exc:                      # noqa: BLE001
            errors.append(f"{route.get('alias')}: {type(exc).__name__}")
            continue
        cost = (res.get("usage") or {}).get("cost")
        if isinstance(cost, (int, float)) and not isinstance(cost, bool) and cost > 0:
            if halt_file:
                halt_file.write_text(f"{today} {route.get('alias')} cost={cost}", encoding="utf-8")
            return {"status": "COST_ABORT", "reason": f"{route.get('alias')}: usage.cost={cost}, ответ отброшен", "values": {}}
        vals = parse_model_reply(res.get("text") or "", pairs)
        if vals:
            return {"status": "OK", "values": vals, "model": str(res.get("model") or route.get("alias")), "reason": ""}
        errors.append(f"{route.get('alias')}: ответ без чисел")
    return {"status": "FAILED", "reason": "; ".join(errors)[:200] or "маршруты не ответили", "values": {}}


def pct_gap(a: Decimal, b: Decimal) -> Decimal:
    return abs(a - b) / b * 100


# ------------------------------------------------------------------ collection
def collect(store: FxStore, *, get, gateway=None, now: datetime | None = None, sleep=time.sleep, gap_s: float = 5.0,
            max_visits: int = MAX_VISITS_PER_DAY, force: bool = False, halt_file: Path | None = None, log=print) -> dict:
    """One visit: fetch the two public files (>= gap_s apart), parse, cross-check by a free model, append. Returns a summary."""
    now = (now or _now()).astimezone(timezone.utc)
    observed = now.isoformat(timespec="seconds").replace("+00:00", "Z")
    day_local = now.astimezone().date().isoformat()
    if not force and store.done_visits(day_local) >= max_visits:
        return {"status": "VISIT_LIMIT", "message": f"уже {max_visits} визита сегодня", "written": 0}
    written = dup = 0
    notes: list[str] = []
    model_state = "NOT_RUN"
    first = True
    for source, (url, parser) in PARSERS.items():
        if not first:
            sleep(gap_s)
        first = False
        try:
            raw = get(url)
            text = raw.decode("utf-8", "replace") if isinstance(raw, bytes) else str(raw)
        except Exception as exc:                       # noqa: BLE001 - one bad source must not stop the other
            notes.append(f"{source}: загрузка не удалась ({type(exc).__name__})")
            continue
        parsed = parser(text)
        want = ["USDCZK", "EURCZK"] if source == "cnb" else ["EURUSD", "EURCZK"]
        mc = model_check(gateway, source, text, want, halt_file=halt_file)
        model_state = mc["status"] if mc["status"] != "NOT_RUN" or model_state == "NOT_RUN" else model_state
        if mc["status"] != "OK" and mc.get("reason"):
            notes.append(f"{source} модель: {mc['status']} - {mc['reason']}")
        if not parsed:
            notes.append(f"{source}: страница не распознана - записи нет")
            for pair, v in mc["values"].items():       # layout changed: the model value is kept, flagged, and never used in reports
                written += store.append(observed_at=observed, pair=pair, rate=v, source=source, source_url=url, rate_date=None,
                                        excerpt="model read of unrecognised page", parser="model", flags=["model_only"], model=mc.get("model"))
            continue
        for pair, (val, excerpt) in parsed["pairs"].items():
            flags: list[str] = []
            mv = mc["values"].get(pair)
            if mc["status"] == "OK" and mv is not None:
                if pct_gap(mv, val) > DISAGREE_PCT:
                    flags.append("disagreement")
                    written += store.append(observed_at=observed, pair=pair, rate=mv, source=source, source_url=url, rate_date=parsed["rate_date"],
                                            excerpt=f"model {mc.get('model')} read {mv}; parser {val}", parser="model", flags=["disagreement"], model=mc.get("model"))
                else:
                    flags.append("model_ok")
            else:
                flags.append("model_unchecked")
            if store.append(observed_at=observed, pair=pair, rate=val, source=source, source_url=url, rate_date=parsed["rate_date"],
                            excerpt=excerpt, parser="regex", flags=flags, model=mc.get("model") if "model_ok" in flags else None):
                written += 1
            else:
                dup += 1
    outcome = "done" if written or dup else "failed"
    store.add_run(observed, day_local, outcome, "; ".join(notes) or "ok")
    backup = store.backup(today=now.astimezone().date()) if outcome == "done" else None
    return {"status": "OK" if outcome == "done" else "NO_DATA", "written": written, "duplicates": dup, "model": model_state, "notes": notes, "backup": backup,
            "observed_at": observed}


# ------------------------------------------------------------------ analysis (pure functions over rows)
def usable(rows: list[dict]) -> list[dict]:
    return [r for r in rows if r["parser"] == "regex" and "disagreement" not in r["flags"].split(",")]


def _day(r: dict) -> str:
    return r["observed_at"][:10]


def snapshot(rows: list[dict], upto_day: str | None = None) -> dict:
    """Latest usable value per (source, pair) observed on or before upto_day. {(source,pair): row}."""
    snap: dict[tuple[str, str], dict] = {}
    for r in usable(rows):
        if upto_day and _day(r) > upto_day:
            continue
        snap[(r["source"], r["pair"])] = r
    return snap


def core_rates(snap: dict) -> dict[str, Decimal] | None:
    """USDCZK (CNB), EURCZK (CNB), EURUSD (ECB): the three numbers every cross-rate is built from."""
    try:
        return {"USDCZK": Decimal(snap[("cnb", "USDCZK")]["rate"]), "EURCZK": Decimal(snap[("cnb", "EURCZK")]["rate"]), "EURUSD": Decimal(snap[("ecb", "EURUSD")]["rate"])}
    except KeyError:
        return None


def derive(core: dict[str, Decimal]) -> dict[str, Decimal]:
    c = core
    return {"USDCZK": c["USDCZK"], "CZKUSD": 1 / c["USDCZK"], "EURCZK": c["EURCZK"], "CZKEUR": 1 / c["EURCZK"], "EURUSD": c["EURUSD"], "USDEUR": 1 / c["EURUSD"]}


def triangle(core: dict[str, Decimal]) -> dict:
    """USD->EUR->CZK (ECB EURUSD x CNB EURCZK) against USD->CZK direct (CNB). The fixings are published at different times, so a small gap is normal."""
    via = core["EURCZK"] / core["EURUSD"]
    return {"direct_usd_czk": core["USDCZK"], "via_eur_usd_czk": via, "spread_pct": (via - core["USDCZK"]) / core["USDCZK"] * 100}


def first_complete_day(rows: list[dict]) -> str | None:
    for d in sorted({_day(r) for r in usable(rows)}):
        if core_rates(snapshot(rows, d)):
            return d
    return None


def amounts(core: dict[str, Decimal], amount: Decimal) -> dict[str, Decimal]:
    """Owner's route: crypto -> USD -> USDT (assumed 1:1) -> EUR | CZK. EUR via ECB, CZK via CNB; amount is in USDT = USD."""
    return {"EUR": amount / core["EURUSD"], "CZK": amount * core["USDCZK"], "CZK_via_EUR": amount / core["EURUSD"] * core["EURCZK"]}


def calc(rows: list[dict], amount, baseline: str | None = None, today: str | None = None) -> dict:
    amt = _dec(amount)
    if amt is None:
        return {"status": "BAD_AMOUNT"}
    base_day = baseline or first_complete_day(rows)
    if not base_day:
        return {"status": "NO_BASELINE"}
    sb, st = snapshot(rows, base_day), snapshot(rows, today)
    cb, ct = core_rates(sb), core_rates(st)
    if not cb or not ct:
        return {"status": "NO_DATA", "baseline_day": base_day}
    ab, at = amounts(cb, amt), amounts(ct, amt)
    out = {"status": "OK", "amount_usdt": str(amt), "baseline_day": base_day, "today_day": max(_day(r) for r in usable(rows)),
           "usdt_usd": {"rate": "1", "measured": False, "flag": "not measured"}, "baseline": {k: str(v.quantize(Decimal("0.01"))) for k, v in ab.items()},
           "today": {k: str(v.quantize(Decimal("0.01"))) for k, v in at.items()}, "diff": {}}
    for k in ab:
        d = at[k] - ab[k]
        out["diff"][k] = {"abs": str(d.quantize(Decimal("0.01"))), "pct": str((d / ab[k] * 100).quantize(Decimal("0.001")))}
    out["core_baseline"], out["core_today"] = ({k: str(v) for k, v in cb.items()}, {k: str(v) for k, v in ct.items()})
    return out


def series(rows: list[dict]) -> dict[str, list[dict]]:
    """Per source:pair, one point per distinct rate_date (the first observation of it)."""
    out: dict[str, list[dict]] = {}
    seen = set()
    for r in usable(rows):
        key = f"{r['source']}:{r['pair']}"
        k2 = (key, r["rate_date"] or _day(r))
        if k2 in seen:
            continue
        seen.add(k2)
        out.setdefault(key, []).append({"rate_date": r["rate_date"] or _day(r), "observed_at": r["observed_at"], "rate": r["rate"]})
    return out


def stats(points: list[dict]) -> dict:
    vals = [float(p["rate"]) for p in points]
    n = len(vals)
    s = {"n": n, "first": vals[0] if n else None, "last": vals[-1] if n else None, "min": min(vals) if n else None, "max": max(vals) if n else None,
         "mean": round(statistics.mean(vals), 8) if n else None, "stdev": round(statistics.stdev(vals), 8) if n > 1 else None}
    chg = [(b - a) / a * 100 for a, b in zip(vals, vals[1:])]
    s["change_pct_total"] = round((vals[-1] - vals[0]) / vals[0] * 100, 4) if n > 1 else None
    s["last_daily_change_pct"] = round(chg[-1], 4) if chg else None
    s["volatility_daily_pct"] = round(statistics.stdev(chg), 4) if len(chg) > 1 else None      # stdev of daily % changes; needs >= 3 points
    return s


def build_report(rows: list[dict], *, amount="1000", baseline: str | None = None, today: str | None = None) -> dict:
    ser = series(rows)
    snap = snapshot(rows, today)
    core = core_rates(snap)
    rep = {"n_rows": len(rows), "n_usable": len(usable(rows)), "n_flagged": len(rows) - len(usable(rows)), "notes": NOTES_RU,
           "baseline_day": baseline or first_complete_day(rows), "series": ser, "stats": {k: stats(v) for k, v in ser.items()},
           "latest": {f"{s}:{p}": {"rate": r["rate"], "rate_date": r["rate_date"], "observed_at": r["observed_at"]} for (s, p), r in snap.items()}}
    if core:
        rep["cross"] = {k: str(v) for k, v in derive(core).items()}
        tri = triangle(core)
        rep["triangle"] = {k: str(v) for k, v in tri.items()}
        ecb_czk = snap.get(("ecb", "EURCZK"))
        if ecb_czk:
            rep["eurczk_cnb_vs_ecb_pct"] = str((core["EURCZK"] - Decimal(ecb_czk["rate"])) / Decimal(ecb_czk["rate"]) * 100)
        rep["routes"] = {"usdt_usd": {"rate": "1", "measured": False, "flag": "not measured"},
                         "per_1_usdt": {k: str(v) for k, v in amounts(core, Decimal(1)).items()}}
    rep["calc"] = calc(rows, amount, baseline, today)
    return rep


# ------------------------------------------------------------------ year-end files + report text
def _f(x, nd=4):
    return "—" if x is None else (f"{x:.{nd}f}" if isinstance(x, float) else str(x))


def report_text(rep: dict) -> str:
    L = ["Курсы / FX: отчёт", f"Записей: {rep['n_rows']} (в отчёте {rep['n_usable']}, отброшено/помечено {rep['n_flagged']})", ""]
    for k, v in sorted(rep["latest"].items()):
        L.append(f"  {k}: {v['rate']}  (курс на {v['rate_date']}, снято {v['observed_at']})")
    if "cross" in rep:
        c = rep["cross"]
        L += ["", f"Кросс-курсы: 1 USD = {Decimal(c['USDEUR']):.6f} EUR = {Decimal(c['USDCZK']):.4f} CZK; 1 EUR = {Decimal(c['EURCZK']):.4f} CZK; 1 CZK = {Decimal(c['CZKEUR']):.6f} EUR = {Decimal(c['CZKUSD']):.6f} USD",
              f"Треугольник USD->EUR->CZK = {Decimal(rep['triangle']['via_eur_usd_czk']):.4f} против USD->CZK напрямую {Decimal(rep['triangle']['direct_usd_czk']):.4f}; разрыв {Decimal(rep['triangle']['spread_pct']):.3f} %"]
    k = rep["calc"]
    if k.get("status") == "OK":
        L += ["", f"Калькулятор: {k['amount_usdt']} USDT (= USD, допущение «не измерено»), база {k['baseline_day']} -> сегодня {k['today_day']}",
              f"  EUR: {k['baseline']['EUR']} -> {k['today']['EUR']}  разница {k['diff']['EUR']['abs']} ({k['diff']['EUR']['pct']} %)",
              f"  CZK: {k['baseline']['CZK']} -> {k['today']['CZK']}  разница {k['diff']['CZK']['abs']} ({k['diff']['CZK']['pct']} %)"]
    else:
        L += ["", f"Калькулятор: нет данных ({k.get('status')})"]
    L += ["", "Статистика (по датам курса):"]
    for key, s in sorted(rep["stats"].items()):
        L.append(f"  {key}: n={s['n']} min={_f(s['min'])} max={_f(s['max'])} mean={_f(s['mean'])} stdev={_f(s['stdev'])} изменение за период={_f(s['change_pct_total'], 3)} %")
    L += [""] + [f"* {n}" for n in rep["notes"]]
    return "\n".join(L)


def write_year_end(store: FxStore, directory: Path, *, amount="1000", baseline: str | None = None, today: str | None = None) -> dict:
    rows = store.rows()
    rep = build_report(rows, amount=amount, baseline=baseline, today=today)
    directory.mkdir(parents=True, exist_ok=True)
    stamp = (today or _now().date().isoformat())
    md, cs = directory / f"fx-year-end-{stamp}.md", directory / f"fx-year-end-{stamp}.csv"
    md.write_text("```\n" + report_text(rep) + "\n```\n", encoding="utf-8")
    with cs.open("w", encoding="utf-8", newline="") as fh:
        w = csv.writer(fh)
        w.writerow(["observed_at", "rate_date", "pair", "rate", "source", "parser", "flags", "used_in_reports", "source_url"])
        for r in rows:
            w.writerow([r["observed_at"], r["rate_date"], r["pair"], r["rate"], r["source"], r["parser"], r["flags"],
                        int(r["parser"] == "regex" and "disagreement" not in r["flags"].split(",")), r["source_url"]])
    return {"markdown": str(md), "csv": str(cs), "report": rep}


# ------------------------------------------------------------------ owner-only pult (Telegram companion)
def pult_send(text: str, files: list[str] | None = None, *, env_file: str | None = None, config_file: str | None = None, http=None) -> dict:
    """Send to the single owner chat of the companion bot. Asserts exactly one person; never prints or returns tokens / ids."""
    import httpx
    base = Path(os.environ.get("LOCALAPPDATA", "")) / "Bossman" / "telegram-companion"
    env_p, cfg_p = Path(env_file or base / "companion.env"), Path(config_file or base / "config.json")
    token = ""
    for line in env_p.read_text(encoding="utf-8").splitlines():
        if line.startswith("TG_COMPANION_BOT_TOKEN="):
            token = line.split("=", 1)[1].strip().strip('"')
    people = json.loads(cfg_p.read_text(encoding="utf-8")).get("people") or []
    assert token and len(people) == 1, "pult: нужен ровно один владелец и токен"
    chat = people[0]["chat_id"]
    c = http or httpx
    api = f"https://api.telegram.org/bot{token}/"
    sent = 0
    for i in range(0, len(text), 3900):
        r = c.post(api + "sendMessage", data={"chat_id": chat, "text": text[i:i + 3900]}, timeout=30)
        sent += int(r.status_code == 200)
    for f in files or []:
        with open(f, "rb") as fh:
            r = c.post(api + "sendDocument", data={"chat_id": chat}, files={"document": (Path(f).name, fh)}, timeout=60)
        sent += int(r.status_code == 200)
    return {"sent": sent}


# ------------------------------------------------------------------ CLI: python -m warterminal fx collect|report|calc
def main(argv: list[str]) -> int:
    import argparse
    ap = argparse.ArgumentParser(prog="warterminal fx", description="Daily FX tracker (CNB + ECB), append-only log, cross-rates")
    sub = ap.add_subparsers(dest="cmd", required=True)
    c = sub.add_parser("collect", help="one visit: fetch CNB + ECB, cross-check with a free model, append")
    c.add_argument("--wait-idle", action="store_true", help="skip/retry (20 min x3) while a phone generation is loading/generating")
    c.add_argument("--no-model", action="store_true")
    c.add_argument("--force", action="store_true", help="ignore the 2 visits/day limit")
    r = sub.add_parser("report", help="series, cross-rates, triangle, statistics, calculator")
    r.add_argument("--amount", default="1000")
    r.add_argument("--baseline", default=None, help="YYYY-MM-DD (default: first complete day)")
    r.add_argument("--year-end", action="store_true", help="also write markdown + CSV into the fx dir")
    r.add_argument("--send-pult", action="store_true", help="send the text (and year-end files) to the owner pult")
    r.add_argument("--json", action="store_true")
    a = ap.parse_args(argv)
    fdir = fx_dir()
    if a.cmd == "collect":
        if a.wait_idle:
            gate = wait_idle(fetch_jobs)
            if gate["status"] != "IDLE":
                st = open_store(fdir)
                st.add_run(_now().isoformat(timespec="seconds"), datetime.now().date().isoformat(), "skipped_busy", json.dumps(gate))
                print(json.dumps({"status": "SKIPPED_BUSY", **gate}, ensure_ascii=False))
                return 3
        from .translate import gateway_from_env
        from .connectors import http as http_mod
        gw = None if a.no_model else gateway_from_env(dict(os.environ))
        st = open_store(fdir)
        res = collect(st, get=lambda u: http_mod.get_bytes(u, timeout=60), gateway=gw, force=a.force, halt_file=fdir / "model_halt.txt")
        print(json.dumps(res, ensure_ascii=False))
        return 0 if res["status"] in ("OK", "VISIT_LIMIT") else 2
    st = open_store(fdir)
    if a.year_end:
        out = write_year_end(st, fdir, amount=a.amount, baseline=a.baseline)
        rep, files = out["report"], [out["markdown"], out["csv"]]
        print(f"written: {out['markdown']}\n         {out['csv']}")
    else:
        rep, files = build_report(st.rows(), amount=a.amount, baseline=a.baseline), []
    print(json.dumps(rep, ensure_ascii=False, default=str, indent=1) if a.json else report_text(rep))
    if a.send_pult:
        print(pult_send(report_text(rep), files))
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
