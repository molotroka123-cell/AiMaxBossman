"""Owner-only assistant: Russian commands in, plain answers out. Pure entry point `Assistant.handle(text, actor)`.

The same function serves the pult, the Mini App and the CLI, so one request is one behaviour everywhere.
Parsing is rule-based on purpose: numbers never pass through a language model.
"""
from __future__ import annotations

import re
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from pathlib import Path
from typing import Callable

from .quote import CURRENCIES, QuoteError, build_quote
from .rates import RateBook, RateError, default_fx_db, load_official, pair_name, parse_decimal
from .store import Store, StoreError

ALIASES = {"usdt": "USDT", "юсдт": "USDT", "тезер": "USDT", "tether": "USDT", "usd": "USD", "доллар": "USD", "долларов": "USD",
           "доллары": "USD", "$": "USD", "eur": "EUR", "евро": "EUR", "€": "EUR", "czk": "CZK", "крон": "CZK", "кроны": "CZK",
           "крона": "CZK", "кч": "CZK"}
_CCY = r"(usdt|юсдт|тезер|tether|usd|доллар\w*|\$|eur|евро|€|czk|крон\w*|кч)"
_NUM = r"(\d[\d\s]*(?:[.,]\d+)?)"

_MARGIN_OPT = r"(?:\s+(?:при|ставка|комиссия|наценка|под)\s+(\d+(?:[.,]\d+)?)\s*%)?"
RE_RATES = re.compile(r"^(курс|курсы)$")
RE_MANUAL = re.compile(rf"^курс\s+{_CCY}\s*/\s*{_CCY}\s+{_NUM}\s+(?:источник|из)\s+(.+)$")
RE_GIVE = re.compile(rf"^(?:котировка|посчитай|сколько)?\s*{_NUM}\s*{_CCY}\s*(?:в|на|->|→|за)?\s*{_CCY}{_MARGIN_OPT}$")
RE_GET = re.compile(rf"^(?:(купить|клиент покупает)|получить|клиент хочет получить|хочет получить)\s+{_NUM}\s*{_CCY}\s+за\s+{_CCY}{_MARGIN_OPT}$")
RE_CONFIRM = re.compile(rf"^(?:обмен выполнен|подтверждаю|подтвердить)\s+(q-[0-9a-f]{{8}})(?:\s+(?:приняли|факт)\s+{_NUM}\s+(?:выдали)\s+{_NUM})?$")
RE_REVERSE = re.compile(r"^(?:отмена|отменить)\s+d-?(\d+)\s+(.+)$")
RE_DASH = re.compile(r"^(?:дашборд|итоги|итог)(?:\s+(сегодня|неделя|месяц))?$")

HELP = ("Команды помощника обменника:\n"
        "• курс — текущие курсы с источником и временем\n"
        "• курс usdt/usd 0.999 источник <откуда> — ручной курс (на 12 ч)\n"
        "• 500 usdt в czk [при 3%] — котировка: клиент отдаёт 500 USDT\n"
        "• получить 10000 czk за usdt [при 3%] — котировка: клиент хочет получить 10000 CZK\n"
        "• купить 1000 usdt за czk при 3% — покупка валюты по согласованной ставке (1 + ставка)\n"
        "• обмен выполнен Q-XXXXXXXX [приняли <сумма> выдали <сумма>] — записать сделку по котировке\n"
        "• отмена D-12 <причина> — отменить сделку (реверс, без удаления)\n"
        "• итоги [сегодня|неделя|месяц] — оборот, позиции, сделки")


WRITE_PATTERNS = (RE_MANUAL, RE_CONFIRM, RE_REVERSE)
ALL_PATTERNS = (RE_RATES, RE_MANUAL, RE_GIVE, RE_GET, RE_CONFIRM, RE_REVERSE, RE_DASH)


def normalize(text: str) -> str:
    return " ".join((text or "").strip().lower().replace("ё", "е").split())


def is_command(text: str) -> bool:
    """True when free text is one of the assistant's commands (used by the pult to route plain phrases)."""
    t = normalize(text)
    return any(p.match(t) for p in ALL_PATTERNS)


def is_write(text: str) -> bool:
    t = normalize(text)
    return any(p.match(t) for p in WRITE_PATTERNS)


def ccy(word: str) -> str:
    w = word.lower()
    if w in ALIASES:
        return ALIASES[w]
    for k, v in ALIASES.items():
        if len(k) >= 4 and w.startswith(k[:4]):
            return v
    raise QuoteError(f"не знаю валюту «{word}» (есть: {', '.join(CURRENCIES)})")


def business_month(now: datetime) -> tuple[datetime, datetime]:
    """Owner's business month: 21st of one month to 20th of the next, inclusive (Europe/Prague)."""
    try:
        from zoneinfo import ZoneInfo
        tz = ZoneInfo("Europe/Prague")
    except Exception:  # noqa: BLE001 - no tz database: fall back to UTC
        tz = timezone.utc
    local = now.astimezone(tz)
    y, m = local.year, local.month
    if local.day < 21:
        m -= 1
        if m == 0:
            y, m = y - 1, 12
    start = datetime(y, m, 21, tzinfo=tz)
    ny, nm = (y + 1, 1) if m == 12 else (y, m + 1)
    return start, datetime(ny, nm, 21, tzinfo=tz)


def default_stop_file(env: dict | None = None) -> Path:
    """The owner's global STOP: Bossman writes <data_dir>/computer/STOP on /stop and removes it on «Продолжить»."""
    import os
    env = os.environ if env is None else env
    data = env.get("BCC_DATA_DIR") or str(Path(env.get("LOCALAPPDATA", str(Path.home()))) / "Bossman" / "CommandCenter")
    return Path(data) / "computer" / "STOP"


class Assistant:
    def __init__(self, store: Store, *, fx_db: str | Path | None = None, owner_ids=frozenset({"owner"}),
                 clock: Callable[[], datetime] | None = None, stop_file: str | Path | None = None):
        self.store = store
        self.fx_db = Path(fx_db) if fx_db else default_fx_db()
        self.owner_ids = set(owner_ids)
        self.clock = clock or (lambda: datetime.now(timezone.utc).replace(microsecond=0))
        self.stop_file = Path(stop_file) if stop_file else default_stop_file()

    def global_stop(self) -> bool:
        try:
            return self.stop_file.is_file()
        except OSError:
            return True                 # cannot tell -> fail closed for writes

    def book(self) -> RateBook:
        b = RateBook(load_official(self.fx_db))
        for r in self.store.manual_rates():
            b.put(r)
        return b

    def handle(self, text: str, actor: str, *, stopped: bool = False) -> str:
        """`stopped`: the owner's STOP/pause is active -> reads still answer, every write is refused."""
        if actor not in self.owner_ids:
            return "Помощник обменника доступен только владельцу."
        t = normalize(text)
        if (stopped or self.global_stop()) and is_write(t):
            return "⛔ Действует СТОП/пауза: записи (курс, сделка, отмена) заблокированы до «Продолжить». Чтение работает."
        now = self.clock()
        try:
            return self._dispatch(t, actor, now)
        except (QuoteError, RateError, StoreError) as e:
            return f"Не выполнено: {e}"

    def _dispatch(self, t: str, actor: str, now: datetime) -> str:
        if t in ("", "помощь", "help", "/help", "команды"):
            return HELP
        if RE_RATES.match(t):
            return self._rates(now)
        if m := RE_MANUAL.match(t):
            r = self.store.set_manual_rate(ccy(m[1]) + ccy(m[2]), parse_decimal(m[3], "курс"), m[4], now, actor)
            return f"Записан ручной курс: {r.label()}"
        if m := RE_GET.match(t):
            buy_verb = bool(m[1])
            margin_kw = {"margin": parse_decimal(m[5], "наценка") / 100} if m[5] else {}
            q = build_quote(self.book(), give_ccy=ccy(m[4]), get_ccy=ccy(m[3]), now=now,
                            get_amount=parse_decimal(m[2], "сумма"),
                            client_buys_get=buy_verb, **margin_kw)
            self.store.save_quote(q)
            return q.text()
        if m := RE_GIVE.match(t):
            margin_kw = {"margin": parse_decimal(m[4], "наценка") / 100} if m[4] else {}
            q = build_quote(self.book(), give_ccy=ccy(m[2]), get_ccy=ccy(m[3]), now=now,
                            give_amount=parse_decimal(m[1], "сумма"), **margin_kw)
            self.store.save_quote(q)
            return q.text()
        if m := RE_CONFIRM.match(t):
            in_actual = parse_decimal(m[2], "приняли").quantize(Decimal("0.01")) if m[2] else None
            out_actual = parse_decimal(m[3], "выдали").quantize(Decimal("0.01")) if m[3] else None
            d, created = self.store.confirm(m[1], now, actor, leg_in_actual=in_actual, leg_out_actual=out_actual)
            head = "Сделка записана" if created else "Эта котировка уже подтверждена, повтор не создан"
            in_shown = d.get("leg_in_actual") or d["give_amount"]
            out_shown = d.get("leg_out_actual") or d["get_amount"]
            return (f"{head}: D-{d['id']} по {d['quote_id']}\n"
                    f"Приняли {in_shown} {d['give_ccy']}, выдали {out_shown} {d['get_ccy']}")
        if m := RE_REVERSE.match(t):
            d = self.store.reverse(int(m[1]), m[2], now, actor)
            return f"Сделка D-{d['id']} отменена (реверс записан, ничего не удалено). Причина: {d['reverse_reason']}"
        if m := RE_DASH.match(t):
            return self._dashboard(m[1] or "месяц", now)
        return "Не понял команду.\n\n" + HELP

    def _rates(self, now: datetime) -> str:
        b = self.book()
        lines = ["Курсы (середина рынка):"]
        for pair in ("USDCZK", "EURCZK", "EURUSD", "USDTUSD"):
            try:
                lines.append("• " + b.get(pair, now).label())
            except RateError as e:
                lines.append(f"• {pair_name(pair)}: {e}")
        lines.append("XE не используется: его условия запрещают автоматический сбор; ЧНБ/ЕЦБ — официальные дневные курсы.")
        return "\n".join(lines)

    def _dashboard(self, period: str, now: datetime) -> str:
        if period == "сегодня":
            start, end = now.replace(hour=0, minute=0, second=0), now + timedelta(seconds=1)
        elif period == "неделя":
            start, end = now - timedelta(days=7), now + timedelta(seconds=1)
        else:
            start, end = business_month(now)
        d = self.store.dashboard(start, end)
        pos = ", ".join(f"{k} {'+' if v >= 0 else ''}{v}" for k, v in d["position_change"].items()) or "—"
        return "\n".join([
            f"Итоги: {start.strftime('%d.%m.%Y')} — {(end - timedelta(seconds=1)).strftime('%d.%m.%Y')}",
            f"Сделок: {d['deals']} (отменено: {d['reversed']})",
            f"Оборот по USDT-ноге: {d['turnover_usdt']} USDT"
            + (f" (ещё {d['deals_without_usdt_leg']} сделок без USDT в оборот не вошли)" if d["deals_without_usdt_leg"] else ""),
            f"Изменение кассы: {pos}",
            f"Прибыль: {d['profit']}",
        ])
