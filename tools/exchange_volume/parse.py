"""Parse a Telegram Desktop messages.html export into messages and deal candidates.

Clean-room implementation written from the behavioural description in the audited
repo's PARSER_SPEC.md (that repo ships no licence file, so no code is copied).
Nothing is guessed: a message that does not give exactly two recognisable
amount+currency pairs is never turned into a deal.
"""
from __future__ import annotations

import datetime as dt
import hashlib
import html as htmllib
import re
from dataclasses import dataclass, field
from decimal import Decimal, InvalidOperation
from typing import Optional

CURRENCIES = ("USDT", "USDC", "EUR", "USD", "CZK")
_ALIASES = {
    "usdt": "USDT", "usdc": "USDC", "юсдт": "USDT", "тезер": "USDT", "тез": "USDT", "usdttrc20": "USDT",
    "usd": "USD", "доллар": "USD", "долларов": "USD", "доллара": "USD", "бакс": "USD",
    "баксов": "USD", "$": "USD",
    "eur": "EUR", "евро": "EUR", "€": "EUR",
    "czk": "CZK", "крон": "CZK", "крона": "CZK", "кроны": "CZK", "кронь": "CZK", "kč": "CZK", "kc": "CZK",
}
_NETWORKS = {"trc20": "TRC20", "trc": "TRC20", "erc20": "ERC20", "erc": "ERC20", "bep20": "BEP20",
             "bsc": "BEP20", "ton": "TON", "sol": "SOL", "polygon": "POLYGON"}
_IN_WORDS = ("принял", "принял", "приняла", "получил", "получила", "взял", "зашло", "пришло")
_OUT_WORDS = ("выдал", "выдала", "отдал", "отдала", "дал", "отправил", "ушло")
_DEBT_WORDS = ("долг", "dolg", "odtat", "otdat")
_EXPENSE_WORDS = ("расход", "трата", "траты", "потратил", "оплата", "оплатил")

_AMT_RE = re.compile(
    r"(?<![\w.,])(\d{1,3}(?:\.\d{3})+,\d+|\d{1,3}(?:,\d{3})+\.\d+|\d{1,3}(?:[ ]\d{3})+(?:[.,]\d+)?|\d+(?:[.,]\d+)?)(?![\d])\s*(?P<k>(?:тыс\.?|[кkКK])(?![а-яa-z]))?",
    re.IGNORECASE)
_WORD_RE = re.compile(r"[€$]|[a-zа-яё0-9]+", re.IGNORECASE)
_DATE_PREFIX_RE = re.compile(r"^\s*(\d{1,2})[./](\d{1,2})(?:[./](\d{2,4}))?\b(?!\s*[a-zа-я]{0,3}\d)")
_MSG_SPLIT = re.compile(r'<div class="message (?=default|service)')
_TS_RE = re.compile(r'title="(\d\d)\.(\d\d)\.(\d{4}) (\d\d):(\d\d):(\d\d) UTC([+-])(\d\d):(\d\d)"')


@dataclass
class Message:
    msg_id: int
    ts_local: dt.datetime        # wall-clock as written in the export
    utc_offset_min: int          # offset of that wall-clock from UTC (export machine zone)
    ts_utc: dt.datetime          # naive UTC
    sender: str                  # anonymised label S1, S2, ...
    text: str
    reply_to: Optional[int] = None
    has_media: bool = False
    forwarded: bool = False


@dataclass
class Leg:
    amount: Decimal
    ccy: str
    network: Optional[str] = None


@dataclass
class Candidate:
    """Result of classifying one message."""
    kind: str                    # deal | complex | incomplete | ambiguous_amount | debt | expense | ignored
    msg_id: int
    ts_utc: dt.datetime
    in_leg: Optional[Leg] = None   # what the desk received ("принял")
    out_leg: Optional[Leg] = None  # what the desk paid out ("выдал")
    flags: list = field(default_factory=list)
    reason: str = ""


def parse_amount(token: str, k: bool = False) -> Optional[Decimal]:
    s = token.replace("\xa0", "").replace(" ", "")
    if "," in s and "." in s:   # the separator occurring last is the decimal one
        if s.rfind(",") > s.rfind("."):
            s = s.replace(".", "").replace(",", ".")
        else:
            s = s.replace(",", "")
    else:
        s = s.replace(",", ".")
    try:
        v = Decimal(s)
    except InvalidOperation:
        return None
    return v * 1000 if k else v


def parse_export(html_text: str) -> list[Message]:
    msgs: list[Message] = []
    senders: dict[str, str] = {}
    last_sender = ""
    for chunk in _MSG_SPLIT.split(html_text)[1:]:
        if chunk.startswith("service"):
            continue
        mid = re.search(r'id="message(-?\d+)"', chunk)
        ts = _TS_RE.search(chunk)
        if not mid or not ts:
            continue
        d, mo, y, hh, mi, ss, sign, oh, om = ts.groups()
        local = dt.datetime(int(y), int(mo), int(d), int(hh), int(mi), int(ss))
        off = (int(oh) * 60 + int(om)) * (1 if sign == "+" else -1)
        fn = re.search(r'<div class="from_name">\s*(.*?)\s*</div>', chunk, re.S)
        if fn:
            name = htmllib.unescape(re.sub(r"<[^>]+>", "", fn.group(1))).strip()
            last_sender = senders.setdefault(
                hashlib.sha256(name.encode("utf-8")).hexdigest()[:12], f"S{len(senders) + 1}")
        tx = re.search(r'<div class="text">(.*?)</div>', chunk, re.S)
        text = ""
        if tx:
            raw = re.sub(r"<br\s*/?>", "\n", tx.group(1))
            text = htmllib.unescape(re.sub(r"<[^>]+>", "", raw)).strip()
        rep = re.search(r"GoToMessage\((\d+)\)", chunk)
        msgs.append(Message(
            msg_id=int(mid.group(1)), ts_local=local, utc_offset_min=off,
            ts_utc=local - dt.timedelta(minutes=off), sender=last_sender, text=text,
            reply_to=int(rep.group(1)) if rep else None,
            has_media="media_wrap" in chunk, forwarded="forwarded" in chunk))
    return msgs


def _norm_ccy(word: str) -> Optional[str]:
    w = word.lower()
    if w.upper() in CURRENCIES:
        return w.upper()
    return _ALIASES.get(w)


def classify(msg: Message) -> Candidate:
    text = msg.text.replace("\xa0", " ")
    low = text.lower()
    base = dict(msg_id=msg.msg_id, ts_utc=msg.ts_utc)
    if not low.strip():
        return Candidate(kind="ignored", reason="empty", **base)
    words = _WORD_RE.findall(low)
    has_dir = any(w.startswith(p) for w in words for p in _IN_WORDS + _OUT_WORDS)
    if any(w.startswith(p) for w in words for p in _DEBT_WORDS):
        return Candidate(kind="debt", reason="debt keyword", **base)
    pairs = []  # (start, amount, ccy)
    ambiguous_tokens: set = set()
    for am in _AMT_RE.finditer(text):
        amt = parse_amount(am.group(1), bool(am.group("k")))
        if amt is None or amt <= 0:
            continue
        after = re.match(r"\s*([€$]|[a-zа-яё]{2,10})", text[am.end():], re.I)
        before = re.search(r"([€$])\s*$", text[:am.start()])
        ccy = None
        if after:
            ccy = _norm_ccy(after.group(1))
        if ccy is None and before:
            ccy = _norm_ccy(before.group(1))
        if ccy:
            if re.fullmatch(r"\d{1,3}[.,]\d{3}", am.group(1)) and not am.group("k"):
                ambiguous_tokens.add(am.start())   # 17.615 = 17 615 or 17,615?  never guessed
            pairs.append((am.start(), amt, ccy))
    if any(w.startswith(p) for w in words for p in _EXPENSE_WORDS) and not has_dir:
        return Candidate(kind="expense", reason="expense keyword", **base)
    if len(pairs) < 2:
        n_amt = len([1 for am in _AMT_RE.finditer(text)])
        if has_dir and n_amt >= 1:
            return Candidate(kind="incomplete",
                             reason=f"direction words but {len(pairs)} amount+currency pairs", **base)
        return Candidate(kind="ignored", reason=f"{len(pairs)} amount+currency pairs", **base)
    if any(p[0] in ambiguous_tokens for p in pairs[:2]):
        return Candidate(kind="ambiguous_amount",
                         reason="separator followed by exactly 3 digits: thousands or decimals", **base)
    flags = []
    if _DATE_PREFIX_RE.match(text):
        flags.append("text_date_prefix_not_applied")
    net = next((_NETWORKS[w] for w in words if w in _NETWORKS), None)

    def role(start: int) -> Optional[str]:
        prev = re.findall(r"[a-zа-яё]+", low[:start])
        tail = prev[-1] if prev else ""
        if any(tail.startswith(p) for p in _IN_WORDS):
            return "in"
        if any(tail.startswith(p) for p in _OUT_WORDS):
            return "out"
        return None

    complex_msg = len(pairs) > 2
    (s0, a0, c0), (s1, a1, c1) = pairs[:2]
    r0, r1 = role(s0), role(s1)
    if r0 == "in" or r1 == "out":
        i, o = (a0, c0), (a1, c1)
    elif r0 == "out" or r1 == "in":
        i, o = (a1, c1), (a0, c0)
    else:
        i, o = (a0, c0), (a1, c1)
        flags.append("direction_assumed_first_is_in")
    if i[1] == o[1] and complex_msg:
        return Candidate(kind="complex", flags=flags,
                         reason=f"{len(pairs)} amount+currency pairs, first two same currency", **base)
    if i[1] == o[1]:
        return Candidate(kind="ignored", reason="same currency on both legs", **base)
    leg = lambda p: Leg(p[0], p[1], net if p[1] == "USDT" else None)  # noqa: E731
    if complex_msg:
        # legs are PROVISIONAL (first two pairs); callers must keep them out of exact totals
        return Candidate(kind="complex", in_leg=leg(i), out_leg=leg(o),
                         flags=flags + ["provisional_first_two_pairs"],
                         reason=f"{len(pairs)} amount+currency pairs (needs manual split)", **base)
    return Candidate(kind="deal", in_leg=leg(i), out_leg=leg(o), flags=flags, **base)
