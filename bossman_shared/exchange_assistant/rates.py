"""Rates with provenance. A rate without a source and a timestamp does not exist here.

Official rates come read-only from the War Terminal FX log (fx_obs: CNB USDCZK/EURCZK, ECB EURUSD).
USDT/USD has no official source: it must be set by the owner as a manual rate with a source note
and an expiry. Nothing is ever substituted silently: a missing or stale rate is an error.
"""
from __future__ import annotations

import os
import sqlite3
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from decimal import Decimal, InvalidOperation
from pathlib import Path

# pair "AAABBB" = units of BBB for 1 AAA
OFFICIAL_PAIRS = {("cnb", "USDCZK"), ("cnb", "EURCZK"), ("ecb", "EURUSD")}
OFFICIAL_MAX_AGE = timedelta(hours=96)          # daily fixings + a weekend
MANUAL_DEFAULT_TTL = timedelta(hours=12)
BOUNDS = {"USDCZK": (Decimal(5), Decimal(100)), "EURCZK": (Decimal(5), Decimal(100)),
          "EURUSD": (Decimal("0.3"), Decimal(3)), "USDTUSD": (Decimal("0.9"), Decimal("1.1"))}


def pair_name(pair: str) -> str:
    """'USDTUSD' -> 'USDT/USD', 'EURCZK' -> 'EUR/CZK'."""
    base = 4 if pair.startswith("USDT") else 3
    return f"{pair[:base]}/{pair[base:]}"


class RateError(Exception):
    """A rate is missing, stale, implausible or not allowed."""


@dataclass(frozen=True, slots=True)
class Rate:
    pair: str
    value: Decimal
    source: str              # "ЧНБ", "ЕЦБ", "licensed_xe_api", or the owner's note for a manual rate
    as_of: datetime          # UTC
    kind: str                # "official" | "manual" | "licensed_api"
    expires_at: datetime | None = None
    raw_receipt: str = ""    # immutable provenance digest or feed receipt ID

    def label(self) -> str:
        t = self.as_of.astimezone(timezone.utc).strftime("%d.%m.%Y %H:%M UTC")
        tail = "" if self.kind == "official" else (
            f", лиценз. API, до {self.expires_at.strftime('%d.%m %H:%M UTC')}"
            if self.kind == "licensed_api" and self.expires_at else
            f", ручной, до {self.expires_at.strftime('%d.%m %H:%M UTC')}" if self.expires_at else ""
        )
        return f"{pair_name(self.pair)} = {self.value} ({self.source}, {t}{tail})"

    def provenance(self) -> dict[str, str | None]:
        return {
            "pair": self.pair,
            "pair_display": pair_name(self.pair),
            "value": str(self.value),
            "source": self.source,
            "kind": self.kind,
            "timestamp_utc": self.as_of.astimezone(timezone.utc).isoformat(),
            "expires_at_utc": self.expires_at.astimezone(timezone.utc).isoformat() if self.expires_at else None,
            "raw_receipt": self.raw_receipt or f"{self.source}:{self.pair}:{self.as_of.astimezone(timezone.utc).isoformat()}",
        }


def licensed_xe_feed_status(env: dict | None = None) -> dict[str, str | bool]:
    """Xe rate provenance policy: never scrape or screenshot xe.com.
    Without licensed API credentials (XE_API_ID + XE_API_KEY), return N/A / MANUAL_ENTRY."""
    import os
    env = os.environ if env is None else env
    has_creds = bool(str(env.get("XE_API_ID") or "").strip() and str(env.get("XE_API_KEY") or "").strip())
    if not has_creds:
        return {
            "licensed_feed_configured": False,
            "status": "N/A",
            "fallback_mode": "MANUAL_ENTRY",
            "policy": "XE scraping/screenshots prohibited; use CNB/ECB official rates or explicit MANUAL_ENTRY with source and TTL.",
        }
    return {
        "licensed_feed_configured": True,
        "status": "LICENSED_API_READY",
        "fallback_mode": "LICENSED_API",
        "policy": "Licensed Xe API credentials present; every rate requires UTC timestamp, TTL, and raw_receipt.",
    }


def parse_decimal(raw: str, name: str = "число") -> Decimal:
    try:
        v = Decimal(str(raw).strip().replace(",", ".").replace(" ", ""))
    except (InvalidOperation, AttributeError):
        raise RateError(f"{name}: не число ({raw!r})") from None
    if not v.is_finite() or v <= 0:
        raise RateError(f"{name}: должно быть положительным конечным числом")
    return v


def check_bounds(pair: str, value: Decimal) -> None:
    lo, hi = BOUNDS.get(pair, (Decimal(0), Decimal("1e9")))
    if not lo <= value <= hi:
        raise RateError(f"{pair}: значение {value} вне разумных границ {lo}–{hi}")


class RateBook:
    def __init__(self, rates: list[Rate] | None = None):
        self._r: dict[str, Rate] = {}
        for r in rates or []:
            self.put(r)

    def put(self, r: Rate) -> None:
        check_bounds(r.pair, r.value)
        cur = self._r.get(r.pair)
        # a manual rate overrides an official one; among the same kind the newest wins
        if cur is None or r.kind == "manual" or (cur.kind == r.kind and r.as_of >= cur.as_of):
            self._r[r.pair] = r

    def get(self, pair: str, now: datetime) -> Rate:
        r = self._r.get(pair)
        if r is None:
            if pair == "USDTUSD":
                raise RateError("нет курса USDT/USD. Официального источника нет, USDT≠USD без подтверждения. "
                                "Задайте вручную: «курс usdt/usd 0.999 источник <откуда>»")
            raise RateError(f"нет курса {pair_name(pair)}: в журнале ЧНБ/ЕЦБ нет данных")
        if r.kind == "manual":
            if r.expires_at is None or now >= r.expires_at:
                raise RateError(f"ручной курс {pair_name(pair)} просрочен ({r.label()}); задайте заново")
        elif now - r.as_of > OFFICIAL_MAX_AGE:
            raise RateError(f"курс {pair_name(pair)} устарел: {r.label()}, "
                            f"старше {int(OFFICIAL_MAX_AGE.total_seconds() // 3600)} ч")
        if r.as_of - now > timedelta(minutes=5):
            raise RateError(f"курс {pair_name(pair)} из будущего: {r.label()}")
        return r

    def all(self) -> list[Rate]:
        return [self._r[k] for k in sorted(self._r)]


def _parse_ts(s: str) -> datetime:
    d = datetime.fromisoformat(s.replace("Z", "+00:00"))
    return d if d.tzinfo else d.replace(tzinfo=timezone.utc)


_OFFICIAL_RATES_CACHE: dict[tuple[str, int, int, int, int], list[Rate]] = {}


def load_official(fx_db: str | Path) -> list[Rate]:
    """Latest regex-parsed, non-disputed CNB/ECB values from the War Terminal FX log (read-only)."""
    key_path = os.fspath(fx_db)
    try:
        st = os.stat(key_path)
    except OSError:
        return []
    try:
        wal_st = os.stat(key_path + "-wal")
        wal_mtime_ns, wal_size = wal_st.st_mtime_ns, wal_st.st_size
    except OSError:
        wal_mtime_ns, wal_size = 0, 0
    cache_key = (key_path, st.st_mtime_ns, st.st_size, wal_mtime_ns, wal_size)
    cached = _OFFICIAL_RATES_CACHE.get(cache_key)
    if cached is not None:
        return list(cached)
    p = Path(key_path)
    con = sqlite3.connect(f"file:{p.as_posix()}?mode=ro", uri=True)
    try:
        rows = con.execute(
            "SELECT observed_at, pair, rate, source, flags FROM fx_obs "
            "WHERE parser='regex' AND source IN ('cnb', 'ecb') "
            "AND pair IN ('USDCZK', 'EURCZK', 'EURUSD') "
            "ORDER BY observed_at, id"
        ).fetchall()
    finally:
        con.close()
    latest: dict[tuple[str, str], Rate] = {}
    for observed_at, pair, rate, source, flags in rows:
        if (source, pair) not in OFFICIAL_PAIRS or "disagreement" in (flags or "").split(","):
            continue
        latest[(source, pair)] = Rate(pair, Decimal(rate), "ЧНБ" if source == "cnb" else "ЕЦБ",
                                      _parse_ts(observed_at), "official")
    result = list(latest.values())
    if len(_OFFICIAL_RATES_CACHE) >= 64:
        _OFFICIAL_RATES_CACHE.clear()
    _OFFICIAL_RATES_CACHE[cache_key] = result
    return list(result)


def default_fx_db(env: dict | None = None) -> Path:
    import os
    env = os.environ if env is None else env
    if env.get("BOSSMAN_FX_DIR"):
        return Path(env["BOSSMAN_FX_DIR"]) / "fx.db"
    return Path(env.get("LOCALAPPDATA", str(Path.home()))) / "Bossman" / "CommandCenter" / "fx" / "fx.db"

