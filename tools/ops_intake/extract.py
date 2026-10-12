"""Loading inbound items and extracting request fields.

Honesty rules enforced here (owner brief 12.10):
* a field value is only ever copied/normalised from the source text; nothing is invented;
* an ambiguous phone / amount / deadline / e-mail is FLAGGED and left empty (the raw text is kept in a ``*_raw``
  column) - it is never "best guessed";
* anything that cannot be parsed is counted and listed (``LoadResult.unparsed``), never dropped silently.

Telegram parsing reuses ``exchange_volume.parse`` (``parse_export`` / ``parse_export_json`` / ``parse_amount``), so
senders are the same anonymised labels S1, S2, ... as in the exchange-volume tool. Run with cwd=tools.
"""
from __future__ import annotations

import csv
import datetime as dt
import io
import json
import re
from dataclasses import dataclass, field
from decimal import Decimal, InvalidOperation
from pathlib import Path
from typing import Iterable, Optional

from exchange_volume import parse as tg

# ----------------------------------------------------------------------------- flag vocabulary
#: flags that always send a row to manual review (an ambiguity, not just an absence)
REVIEW_FLAGS = frozenset({
    "phone_ambiguous", "phone_multiple", "email_invalid", "amount_ambiguous", "amount_multiple",
    "amount_currency_missing", "service_ambiguous", "deadline_ambiguous", "possible_duplicate", "empty_request",
})
#: absences; they only trigger review when the field is in the operator's ``--require`` set
MISSING_FLAGS = ("name_missing", "phone_missing", "email_missing", "service_missing", "amount_missing",
                 "deadline_missing", "contact_missing")


# ----------------------------------------------------------------------------- data classes
@dataclass
class Inbound:
    """One raw inbound item (a chat message or a lead row) before extraction."""
    src_id: str                         # tg:<message id> | csv:row<N> | jsonl:line<N> | json:item<N>
    source: str                         # telegram_json | telegram_html | csv | jsonl | json_array
    text: str = ""
    ts_utc: Optional[dt.datetime] = None      # naive UTC
    ts_ref: Optional[dt.datetime] = None      # wall-clock used to resolve "tomorrow", "by Friday"
    sender: str = ""                    # anonymised S-label for chats
    reply_to: Optional[str] = None
    fields: dict = field(default_factory=dict)  # structured columns (name, phone, email, ...)
    structured: bool = False
    has_media: bool = False
    forwarded: bool = False
    is_staff: bool = False
    answered: Optional[bool] = None     # only for structured sources with an answered/status column


@dataclass
class Extracted:
    src_id: str
    source: str
    ts_utc: Optional[dt.datetime]
    sender: str
    text: str
    lead_id: str = ""
    name: str = ""
    phone: str = ""
    phone_raw: str = ""
    phone_alt: list = field(default_factory=list)
    email: str = ""
    email_raw: str = ""
    handle: str = ""
    service: str = ""
    service_candidates: list = field(default_factory=list)
    amount: Optional[Decimal] = None
    currency: str = ""
    amount_raw: str = ""
    amount_candidates: list = field(default_factory=list)
    deadline_raw: str = ""
    deadline_date: str = ""
    deadline_basis: str = ""
    language: str = "und"
    flags: list = field(default_factory=list)
    is_request: bool = True
    skip_reason: str = ""               # non_request | staff_message | media_only | empty_text
    answered: Optional[bool] = None
    answered_basis: str = ""
    forwarded: bool = False
    is_staff: bool = False
    signals: list = field(default_factory=list)


@dataclass
class LoadResult:
    items: list
    source_type: str
    total_items: int                    # chat messages / lead rows considered (service messages excluded)
    unparsed: list                      # [{"ref": ..., "reason": ...}]
    ignored: dict = field(default_factory=dict)   # service_messages, blank_rows ...
    reply_index: dict = field(default_factory=dict)  # replied-to src_id -> [{"src_id","sender","is_staff"}]
    staff_given: bool = False
    meta: dict = field(default_factory=dict)


# ----------------------------------------------------------------------------- text helpers
_QUOTES = str.maketrans({"’": "'", "‘": "'", "ʼ": "'", "\N{NO-BREAK SPACE}": " ", "\N{NARROW NO-BREAK SPACE}": " ", "\N{ZERO WIDTH SPACE}": ""})
_UP = "A-ZА-ЯЁČŠŽŘĚŮÁÉÍÓÚÝŇĎŤŁŚĆŹŻ"
_LO = "a-zа-яёčšžřěůáéíóúýňďťłśćźż"
_LETTER = r"[^\W\d_]"


def clean_text(s: str) -> str:
    return str(s or "").translate(_QUOTES).replace("\r\n", "\n").replace("\r", "\n").strip()


def _blank(buf: list, start: int, end: int) -> None:
    for i in range(start, end):
        buf[i] = " "


# ----------------------------------------------------------------------------- phones
#: country calling code -> allowed national-number lengths (only well-known codes; others: 8..15 digits total)
_CC_NATIONAL_LEN = {
    "1": {10}, "7": {10}, "33": {9}, "34": {9}, "39": {9, 10}, "40": {9}, "41": {9}, "44": {10}, "48": {9},
    "49": {10, 11, 12}, "90": {10}, "91": {10}, "370": {8}, "371": {8}, "372": {7, 8}, "374": {8}, "375": {9},
    "380": {9}, "420": {9}, "421": {9}, "971": {8, 9}, "994": {9}, "995": {9}, "996": {9}, "998": {9},
}
_PHONE_CAND_RE = re.compile(r"(?<![\w])(?P<p>[(+]{0,2}\d[\d\s()./\-–]{5,}\d)(?![\w])")
_PHONE_LABEL_RE = re.compile(r"(?:тел|phone|tel|mobile|моб|whatsapp|viber|вотсап|ватсап|звоните|call|номер|number)\W{0,6}$",
                             re.I)
_NOT_PHONE_CTX_RE = re.compile(r"(?:№|#|заказ|order|invoice|счет|счёт|\bid)\s*:?\s*$", re.I)


def _split_cc(digits: str) -> Optional[str]:
    for n in (3, 2, 1):
        if digits[:n] in _CC_NATIONAL_LEN:
            return digits[:n]
    return None


def normalize_phone(raw: str) -> tuple[str, str]:
    """-> (E.164 ``+<digits>`` or "", reason). Empty phone + non-empty reason means AMBIGUOUS (never guessed).

    A number is only accepted when its country is explicit (``+``, ``00``) or follows the Russian/Kazakh trunk
    convention ``8``/``7`` + 10 digits, or is a well-known country code written without ``+`` with the right length.
    A bare 9/10-digit number ("777 123 456") has no country and is ambiguous.
    """
    s = str(raw).strip()
    digits = re.sub(r"\D", "", s)
    if not digits:
        return "", "no_digits"
    has_plus = bool(re.match(r"^[\s(]*\+", s))
    intl = has_plus
    if digits.startswith("00") and not has_plus:
        digits, intl = digits[2:], True
    if intl:
        if digits.startswith("0"):
            return "", "bad_country_code"
        if has_plus and len(digits) == 11 and digits.startswith("8"):
            return "", "plus_with_trunk_prefix_8"
        cc = _split_cc(digits)
        if cc is None:
            return (f"+{digits}", "") if 8 <= len(digits) <= 15 else ("", f"length_{len(digits)}")
        if len(digits) - len(cc) in _CC_NATIONAL_LEN[cc]:
            return f"+{digits}", ""
        return "", f"length_for_cc_{cc}"
    if len(digits) == 11 and digits[0] in "78":
        return "+7" + digits[1:], ""
    if len(digits) in (9, 10):
        return "", "no_country_code"
    cc = _split_cc(digits)
    if cc and len(cc) >= 2 and len(digits) - len(cc) in _CC_NATIONAL_LEN[cc]:
        return f"+{digits}", ""
    return "", f"unrecognised_length_{len(digits)}"


# ----------------------------------------------------------------------------- currencies / amounts
_CCY_ALIASES = {
    "czk": "CZK", "kč": "CZK", "kc": "CZK", "крон": "CZK", "крона": "CZK", "кроны": "CZK", "корун": "CZK",
    "коруна": "CZK", "коруны": "CZK", "korun": "CZK", "koruna": "CZK", "koruny": "CZK",
    "usd": "USD", "$": "USD", "долл": "USD", "доллар": "USD", "доллара": "USD", "долларов": "USD",
    "dollar": "USD", "dollars": "USD", "бакс": "USD", "баксов": "USD", "баксы": "USD",
    "eur": "EUR", "€": "EUR", "евро": "EUR", "euro": "EUR", "euros": "EUR",
    "rub": "RUB", "rur": "RUB", "руб": "RUB", "рубль": "RUB", "рубля": "RUB", "рублей": "RUB", "рубли": "RUB",
    "р": "RUB", "₽": "RUB", "ruble": "RUB", "rubles": "RUB",
    "uah": "UAH", "грн": "UAH", "гривен": "UAH", "гривна": "UAH", "гривны": "UAH", "₴": "UAH",
    "pln": "PLN", "zł": "PLN", "zl": "PLN", "злотых": "PLN", "злотый": "PLN",
    "gbp": "GBP", "£": "GBP", "фунтов": "GBP", "pounds": "GBP",
    "kzt": "KZT", "тенге": "KZT", "₸": "KZT",
    "byn": "BYN", "usdt": "USDT", "тезер": "USDT", "юсдт": "USDT", "usdc": "USDC", "btc": "BTC", "eth": "ETH",
}
_ABBREVIATED = frozenset(("руб", "р", "грн", "долл"))
_CCY_TOK_RE = re.compile(r"[ \t]{0,2}(?P<c>[€$£₽₴₸]|" + _LETTER + r"+)")
_NUM_PAT = (r"\d{1,3}(?:[ \N{NO-BREAK SPACE}\N{NARROW NO-BREAK SPACE}]\d{3})+(?:[.,]\d{1,2})?"
            r"|\d{1,3}(?:\.\d{3})+(?:,\d{1,2})?"
            r"|\d{1,3}(?:,\d{3})+(?:\.\d{1,2})?"
            r"|\d+(?:[.,]\d+)?")
_MULT_PAT = (r"(?:\s?(?P<mult>тыс(?:яч[а-я]*)?\.?|thousand|млн\.?|mln\.?|million|миллион[а-я]*|[kкKК])"
             r"(?!" + _LETTER + r"))?")
_AMT_RE = re.compile(r"(?<![\w.,])(?P<pre>[€$£₽₴₸]\s?)?(?P<num>" + _NUM_PAT + r")(?!\d)" + _MULT_PAT, re.I)
_BUDGET_CTX_RE = re.compile(
    r"(?:бюджет|budget|цен[аыуе]|price|стоимост|сумм[аыуе]|amount|cost|оплат[аыуи]|pay(?:ment)?)"
    r"\W{0,3}(?:" + _LETTER + r"{1,12}\W{0,3}){0,2}$", re.I)
_MULT_VALUE = {"тыс": 1000, "thousand": 1000, "k": 1000, "к": 1000, "млн": 10**6, "mln": 10**6, "million": 10**6,
               "миллион": 10**6}


def _ccy_after(text: str, pos: int) -> tuple[Optional[str], int]:
    m = _CCY_TOK_RE.match(text, pos)
    if not m:
        return None, pos
    ccy = _CCY_ALIASES.get(m.group("c").lower())
    if not ccy:
        return None, pos
    end = m.end()
    if m.group("c").lower() in _ABBREVIATED and end < len(text) and text[end] == ".":
        end += 1                        # "руб." / "р." - the dot belongs to the abbreviation
    return ccy, end


def _to_decimal(tok: str) -> Optional[Decimal]:
    s = tok.replace("\N{NO-BREAK SPACE}", "").replace("\N{NARROW NO-BREAK SPACE}", "").replace(" ", "")
    if re.fullmatch(r"\d{1,3}(?:\.\d{3}){2,}(?:,\d{1,2})?", s):
        s = s.replace(".", "").replace(",", ".")
        try:
            return Decimal(s)
        except InvalidOperation:
            return None
    if re.fullmatch(r"\d{1,3}(?:,\d{3}){2,}(?:\.\d{1,2})?", s):
        return Decimal(s.replace(",", ""))
    return tg.parse_amount(s)


def _mult_of(word: Optional[str]) -> int:
    if not word:
        return 1
    w = word.lower().rstrip(".")
    for key, val in _MULT_VALUE.items():
        if w.startswith(key):
            return val
    return 1


def _fmt_amount(value: Decimal, ccy: str) -> str:
    text = format(value.normalize(), "f") if value == value.to_integral() else str(value.normalize())
    return f"{text} {ccy}".strip()


@dataclass
class _Hit:
    start: int
    end: int
    raw: str
    value: Optional[Decimal]
    ccy: str
    ambiguous: bool = False
    has_mult: bool = False
    bare: bool = False
    in_range: bool = False


def extract_amounts(masked: str, force_ctx: bool = False) -> dict:
    """-> {amount, currency, raw, candidates, flags, spans}. Several distinct amounts are never summed or chosen."""
    hits: list[_Hit] = []
    for m in _AMT_RE.finditer(masked):
        num, mult_word, pre = m.group("num"), m.group("mult"), m.group("pre")
        ccy, cend = _ccy_after(masked, m.end())
        pre_ccy = _CCY_ALIASES.get(pre.strip()) if pre else None
        conflict = bool(ccy and pre_ccy and ccy != pre_ccy)
        final = ccy or pre_ccy or ""
        end = cend if ccy else m.end()
        mult = _mult_of(mult_word)
        value = None
        ambiguous = conflict
        if not mult_word and re.fullmatch(r"\d{1,3}[.,]\d{3}", num):
            ambiguous = True            # 5.000 / 2,500: thousands separator or decimals - never guessed
        else:
            dec = _to_decimal(num)
            if dec is not None and dec > 0:
                value = dec * mult
        if value is None and not ambiguous:
            continue
        hits.append(_Hit(m.start(), end, masked[m.start():end].strip(), value, final, ambiguous,
                         bool(mult_word), bare=not final))
    # ranges "3000-5000 Kč" / "от 3000 до 5000 руб": the left bound shares the right bound's currency
    for j in range(1, len(hits)):
        left, right = hits[j - 1], hits[j]
        between = masked[left.end:right.start].strip().lower()
        if not right.ccy and left.bare and right.bare and between in ("-", "–", "—", "to", "до") \
                and (force_ctx or _BUDGET_CTX_RE.search(masked[max(0, left.start - 40):left.start])):
            left.in_range = right.in_range = True      # "бюджет 5000-7000": a range, both bounds are kept
            continue
        if right.ccy and left.bare and between in ("-", "–", "—", "to", "до", "по"):
            left.ccy, left.bare, left.in_range, right.in_range = right.ccy, False, True, True
            if right.has_mult and not left.has_mult and left.value is not None and left.value < 1000:
                left.value = left.value * 1000
    significant: list[_Hit] = []
    for h in hits:
        if h.ccy or h.in_range:
            significant.append(h)
        elif force_ctx or _BUDGET_CTX_RE.search(masked[max(0, h.start - 40):h.start]):
            significant.append(h)
    out = {"amount": None, "currency": "", "raw": "", "candidates": [], "flags": [], "spans": []}
    if not significant:
        return out
    out["spans"] = [(h.start, h.end) for h in significant]
    seen: dict = {}
    for h in significant:
        key = ("?", h.raw) if h.value is None else (h.value, h.ccy)
        seen.setdefault(key, h)
    uniq = list(seen.values())
    out["raw"] = "; ".join(h.raw for h in uniq)
    out["candidates"] = [_fmt_amount(h.value, h.ccy) if h.value is not None else f"? {h.raw}" for h in uniq]
    if len(uniq) > 1:
        out["flags"].append("amount_multiple")
        if any(h.ambiguous for h in uniq):
            out["flags"].append("amount_ambiguous")
        return out
    h = uniq[0]
    if h.ambiguous or h.value is None:
        out["flags"].append("amount_ambiguous")
        return out
    out["amount"], out["currency"] = h.value, h.ccy
    if not h.ccy:
        out["flags"].append("amount_currency_missing")
    return out


# ----------------------------------------------------------------------------- deadlines
_MONTHS: dict = {}


def _months(num: int, *forms: str) -> None:
    for f in forms:
        _MONTHS[f] = num


_months(1, "январь", "января", "янв", "january", "jan", "ledna", "leden")
_months(2, "февраль", "февраля", "фев", "february", "feb", "února", "unor", "únor")
_months(3, "март", "марта", "мар", "march", "mar", "března", "brezen", "březen")
_months(4, "апрель", "апреля", "апр", "april", "apr", "dubna", "duben")
_months(5, "май", "мая", "may", "května", "kvetna", "květen")
_months(6, "июнь", "июня", "июн", "june", "jun", "června", "cerven", "červen")
_months(7, "июль", "июля", "июл", "july", "jul", "července", "cervence", "červenec")
_months(8, "август", "августа", "авг", "august", "aug", "srpna", "srpen")
_months(9, "сентябрь", "сентября", "сен", "сент", "september", "sep", "sept", "září", "zari")
_months(10, "октябрь", "октября", "окт", "october", "oct", "října", "rijna", "říjen")
_months(11, "ноябрь", "ноября", "ноя", "нояб", "november", "nov", "listopadu", "listopad")
_months(12, "декабрь", "декабря", "дек", "december", "dec", "prosince", "prosinec")

_WEEKDAYS = {
    0: ("понедельник", "понедельника", "monday", "mon"), 1: ("вторник", "вторника", "tuesday", "tue"),
    2: ("среда", "среду", "среды", "wednesday", "wed"), 3: ("четверг", "четверга", "thursday", "thu"),
    4: ("пятница", "пятницу", "пятницы", "friday", "fri"), 5: ("суббота", "субботу", "субботы", "saturday", "sat"),
    6: ("воскресенье", "воскресенья", "sunday", "sun"),
}
_WEEKDAY_LOOKUP = {f: n for n, forms in _WEEKDAYS.items() for f in forms}
_WEEKDAY_ALT = "|".join(sorted(_WEEKDAY_LOOKUP, key=len, reverse=True))

_CTX = (r"(?:до|к|по|на|не\s+позже|не\s+позднее|срок[а-я]*|дедлайн|нужн[оаы]?|надо|готов[оаы]?|сдать|"
        r"by|until|before|due|deadline|need(?:ed)?|ready|deliver(?:y|ed)?|on|for)")
_NUMDATE_RE = re.compile(
    r"(?<![\w.])(?P<ctx>" + _CTX + r")\s*[:\-–]?\s*(?:the\s+)?(?P<d>\d{1,2})(?P<sep>[./])(?P<m>\d{1,2})"
    r"(?:(?P=sep)(?P<y>\d{2,4}))?(?!\d)", re.I)
_ISODATE_RE = re.compile(r"(?<![\w.])(?P<ctx>" + _CTX + r")\s*[:\-–]?\s*(?P<y>\d{4})-(?P<m>\d{2})-(?P<d>\d{2})(?!\d)",
                         re.I)
_DAYMONTH_RE = re.compile(
    r"(?<![\w.])(?P<ctx>" + _CTX + r")\s*[:\-–]?\s*(?:the\s+)?(?P<d>\d{1,2})(?:-?(?:го|st|nd|rd|th))?\.?\s*"
    r"(?:of\s+)?(?P<mon>" + _LETTER + r"{3,12})\.?(?:\s*,?\s*(?P<y>\d{4}))?", re.I)
_MONTHDAY_RE = re.compile(
    r"(?<![\w.])(?P<ctx>" + _CTX + r")\s*[:\-–]?\s*(?P<mon>" + _LETTER + r"{3,12})\.?\s+(?P<d>\d{1,2})"
    r"(?:st|nd|rd|th)?(?:\s*,?\s*(?P<y>\d{4}))?(?!\d)", re.I)
_WEEKDAY_RE = re.compile(r"(?<![\w])(?P<ctx>до|к|в|во|на|by|on|until|before|this|этот|эту)\s+(?:эту\s+|этот\s+)?"
                         r"(?P<wd>" + _WEEKDAY_ALT + r")(?!" + _LETTER + r")", re.I)
_REL_DAY_RE = re.compile(r"(?<![\w])(?P<w>послезавтра|завтра|tomorrow|day\s+after\s+tomorrow)(?!" + _LETTER + r")", re.I)
_TODAY_RE = re.compile(r"(?<![\w])(?P<ctx>до|к|by|until|before|нужн[оаы]?|need(?:ed)?)\s+(?P<w>сегодня|today|tonight)"
                       r"(?!" + _LETTER + r")", re.I)
_WITHIN_RE = re.compile(
    r"(?<![\w])(?:через|in|within|в\s+течение|за)\s+(?P<n>\d{1,3}|a|one|один|одну|пару)\s*"
    r"(?P<u>дн(?:я|ей|ь)?|день|суток|сутки|недел(?:ю|и|ь|ях)?|нед|месяц(?:а|ев)?|час(?:а|ов)?|day|days|week|weeks|"
    r"month|months|hour|hours)(?!" + _LETTER + r")", re.I)
_WITHIN_NOUN_RE = re.compile(r"(?<![\w])в\s+течение\s+(?P<u>недели|месяца|дня|суток)(?!" + _LETTER + r")", re.I)
_VAGUE_RE = re.compile(
    r"(?<![\w])(?:на\s+следующей\s+неделе|на\s+этой\s+неделе|next\s+week|this\s+week|до\s+конца\s+(?:недели|месяца|года)|"
    r"в\s+этом\s+месяце|end\s+of\s+(?:the\s+)?(?:week|month|year)|this\s+month)(?!" + _LETTER + r")", re.I)
_URGENT_RE = re.compile(r"(?<![\w])(?:срочно|срочный|срочная|urgent(?:ly)?|asap|как\s+можно\s+скорее|немедленно|"
                        r"immediately)(?!" + _LETTER + r")", re.I)
_UNIT_AFTER_RE = re.compile(r"\s{0,2}(?:час|мин|сек|hour|hr|min|sec|mb|gb|мб|гб|%|раз|стр|шт|см|мм|тыс|млн|mln|k\b|к\b|"
                            r"метр|m\b|pages|pcs)", re.I)


def _year(y: Optional[str]) -> Optional[int]:
    if y is None:
        return None
    n = int(y)
    return 2000 + n if n < 100 else n


def _resolve_dm(d: int, m: int, y: Optional[int], ref: Optional[dt.date]) -> tuple[str, str]:
    """-> (iso date or "", basis)."""
    if not (1 <= m <= 12 and 1 <= d <= 31):
        return "", "invalid"
    if y is not None:
        try:
            return dt.date(y, m, d).isoformat(), "explicit_date"
        except ValueError:
            return "", "invalid"
    if ref is None:
        return "", "no_message_date"
    try:
        cand = dt.date(ref.year, m, d)
        if cand < ref:
            cand = dt.date(ref.year + 1, m, d)
    except ValueError:
        return "", "invalid"
    return cand.isoformat(), "year_inferred_from_message_date"


def extract_deadline(text: str, ref: Optional[dt.datetime]) -> dict:
    """-> {raw, date, basis, flags, spans}. Relative deadlines are only resolved against the message's own date."""
    ref_d = ref.date() if ref else None
    hits: list[dict] = []   # {raw, date, basis, ambiguous, span, kind}

    def add(m, date, basis, ambiguous=False, kind="date"):
        hits.append({"raw": m.group(0).strip(), "date": date, "basis": basis, "ambiguous": ambiguous,
                     "span": m.span(), "kind": kind})

    def unit_follows(end: int) -> bool:
        if _UNIT_AFTER_RE.match(text, end):
            return True
        return _ccy_after(text, end)[0] is not None

    for m in _NUMDATE_RE.finditer(text):
        if unit_follows(m.end()):
            continue
        d, mo, sep = int(m.group("d")), int(m.group("m")), m.group("sep")
        if sep == "/" and d <= 12 and mo <= 12 and d != mo:
            add(m, "", "ambiguous_dd_mm_or_mm_dd", ambiguous=True)
            continue
        date, basis = _resolve_dm(d, mo, _year(m.group("y")), ref_d)
        add(m, date, basis, ambiguous=(basis == "invalid"))
    for m in _ISODATE_RE.finditer(text):
        date, basis = _resolve_dm(int(m.group("d")), int(m.group("m")), int(m.group("y")), ref_d)
        add(m, date, basis, ambiguous=(basis == "invalid"))
    for m in _DAYMONTH_RE.finditer(text):
        mon = _MONTHS.get(m.group("mon").lower().rstrip("."))
        if not mon:
            continue
        date, basis = _resolve_dm(int(m.group("d")), mon, _year(m.group("y")), ref_d)
        add(m, date, basis, ambiguous=(basis == "invalid"))
    for m in _MONTHDAY_RE.finditer(text):
        mon = _MONTHS.get(m.group("mon").lower().rstrip("."))
        if not mon:
            continue
        date, basis = _resolve_dm(int(m.group("d")), mon, _year(m.group("y")), ref_d)
        add(m, date, basis, ambiguous=(basis == "invalid"))
    for m in _WEEKDAY_RE.finditer(text):
        wd = _WEEKDAY_LOOKUP.get(m.group("wd").lower())
        if wd is None:
            continue
        if ref_d is None:
            add(m, "", "no_message_date")
        else:
            add(m, (ref_d + dt.timedelta(days=(wd - ref_d.weekday()) % 7)).isoformat(), "relative_to_message_date")
    for m in _REL_DAY_RE.finditer(text):
        w = m.group("w").lower()
        off = 2 if w.startswith(("после", "day after")) else 1
        add(m, (ref_d + dt.timedelta(days=off)).isoformat() if ref_d else "",
            "relative_to_message_date" if ref_d else "no_message_date")
    for m in _TODAY_RE.finditer(text):
        add(m, ref_d.isoformat() if ref_d else "", "relative_to_message_date" if ref_d else "no_message_date")
    for m in _WITHIN_RE.finditer(text):
        n_raw, unit = m.group("n").lower(), m.group("u").lower()
        n = int(n_raw) if n_raw.isdigit() else (2 if n_raw == "пару" else 1)
        days = (n * 7 if unit.startswith(("нед", "week")) else n * 30 if unit.startswith(("меся", "month"))
                else 0 if unit.startswith(("час", "hour")) else n)
        add(m, (ref_d + dt.timedelta(days=days)).isoformat() if ref_d else "",
            "relative_to_message_date" if ref_d else "no_message_date")
    for m in _WITHIN_NOUN_RE.finditer(text):
        days = {"недели": 7, "месяца": 30}.get(m.group("u").lower(), 1)
        add(m, (ref_d + dt.timedelta(days=days)).isoformat() if ref_d else "",
            "relative_to_message_date" if ref_d else "no_message_date")
    # drop sub-hits fully inside another hit span (e.g. "через 3 дня" matched twice)
    hits = [h for h in hits if not any(o is not h and o["span"][0] <= h["span"][0] and h["span"][1] <= o["span"][1]
                                       and o["span"] != h["span"] for o in hits)]
    vague = [{"raw": m.group(0).strip(), "span": m.span()} for m in _VAGUE_RE.finditer(text)]
    urgent = [{"raw": m.group(0).strip(), "span": m.span()} for m in _URGENT_RE.finditer(text)]
    out = {"raw": "", "date": "", "basis": "", "flags": [], "spans": []}
    if urgent:
        out["flags"].append("urgent")
    dated = [h for h in hits if h["date"]]
    ambiguous = [h for h in hits if h["ambiguous"] or (not h["date"] and h["basis"] != "no_message_date")]
    distinct = {h["date"] for h in dated}
    if len(distinct) > 1 or ambiguous:
        out.update(raw=" | ".join(h["raw"] for h in hits), basis="ambiguous",
                   spans=[h["span"] for h in hits + vague + urgent])
        out["flags"].append("deadline_ambiguous")
        return out
    if dated:
        h = dated[0]
        out.update(raw=h["raw"], date=h["date"], basis=h["basis"],
                   spans=[x["span"] for x in hits + vague + urgent])
        return out
    unresolved = [h for h in hits if h["basis"] == "no_message_date"]
    if unresolved:
        out.update(raw=unresolved[0]["raw"], basis="no_message_date",
                   spans=[x["span"] for x in hits + vague + urgent])
        return out
    if vague:
        out.update(raw=vague[0]["raw"], basis="vague", spans=[x["span"] for x in vague + urgent])
        return out
    if urgent:
        out.update(raw=urgent[0]["raw"], basis="urgent", spans=[x["span"] for x in urgent])
    return out


# ----------------------------------------------------------------------------- services
#: canonical service -> {"strong": [regex fragments], "weak": [...]}; weak words only decide when no strong one hits
DEFAULT_SERVICES = {
    "video_editing": {
        "strong": [r"монтаж", r"смонтир", r"видеомонтаж", r"рилс", r"рилз", r"reels?\b", r"shorts\b",
                   r"video\s+edit", r"edit(?:ing)?\s+(?:a\s+|the\s+|my\s+)?video", r"video\s+cut"],
        "weak": [r"ролик", r"видеоролик", r"видео\b", r"video\b", r"клип", r"clip\b"]},
    "face_swap": {
        "strong": [r"замен\w*\s+лиц", r"face\s*-?swap", r"faceswap", r"swap\s+(?:the\s+)?face", r"дипфейк", r"deepfake"],
        "weak": []},
    "voice_dubbing": {
        "strong": [r"озвучк", r"озвуч", r"дубляж", r"дублир", r"voice\s*-?over", r"dubbing", r"клон\w*\s+голос",
                   r"voice\s+clon"],
        "weak": []},
    "design": {
        "strong": [r"дизайн", r"логотип", r"баннер", r"брендбук", r"макет", r"\blogo", r"banner", r"design\b",
                   r"branding"],
        "weak": []},
    "website": {
        "strong": [r"сайт", r"лендинг", r"landing", r"website", r"web\s*site", r"интернет-магазин", r"web\s*page"],
        "weak": []},
    "bot_dev": {
        "strong": [r"(?:телеграм|telegram|tg|тг)[\s-]*(?:бот|bot)", r"чат[\s-]*бот", r"chat[\s-]*bot", r"\bбот(?:а|у|ом|ов)?\b"],
        "weak": []},
    "translation": {
        "strong": [r"перевод", r"перевест", r"переводчик", r"translat"], "weak": []},
    "bookkeeping": {
        "strong": [r"бухгалтер", r"бухучет", r"бухучёт", r"налог", r"accounting", r"bookkeeping", r"tax\s+return"],
        "weak": []},
    "photo_retouch": {
        "strong": [r"ретуш", r"retouch", r"обработк\w*\s+фото", r"photo\s+edit"], "weak": []},
    "copywriting": {
        "strong": [r"копирайт", r"copywrit", r"написать\s+стат", r"write\s+(?:an?\s+)?article"], "weak": []},
    "consulting": {
        "strong": [r"консультац", r"consult"], "weak": []},
}

_INTENT_RE = re.compile(
    r"(?<![^\W\d_])(?:нужен|нужна|нужно|нужны|надо|хочу|хотел|хотела|хотим|интересует|интересуют|требуется|ищу|ищем|"
    r"заказать|заказ|сделайте|сделать|помогите|подскажите|сколько\s+стоит|цен|прайс|стоимост|возьм[её]тесь|"
    r"need|want|looking\s+for|interested|order|quote|price|how\s+much|can\s+you|could\s+you|hire|require)",
    re.I)


class Taxonomy:
    def __init__(self, services: Optional[dict] = None):
        self.services: dict = {}
        for name, spec in (services or DEFAULT_SERVICES).items():
            if isinstance(spec, list):
                spec = {"strong": spec, "weak": []}
            self.services[name] = {
                tier: [re.compile(r"(?<![^\W\d_])(?:" + frag + r")", re.I) for frag in spec.get(tier, [])]
                for tier in ("strong", "weak")}

    @classmethod
    def from_file(cls, path) -> "Taxonomy":
        return cls(json.loads(Path(path).read_text(encoding="utf-8")))

    def match(self, text: str) -> tuple[list, list]:
        """-> (strong services, weak services), canonical names in taxonomy order."""
        strong = [n for n, t in self.services.items() if any(p.search(text) for p in t["strong"])]
        weak = [n for n, t in self.services.items() if any(p.search(text) for p in t["weak"])]
        return strong, weak

    def resolve(self, text: str) -> tuple[str, list]:
        """-> (service, candidates). One match -> service; several -> candidates (ambiguous); none -> ("", [])."""
        strong, weak = self.match(text)
        pool = strong or weak
        if len(pool) == 1:
            return pool[0], []
        if len(pool) > 1:
            return "", pool
        return "", []


_TAXONOMY_DEFAULT: Optional[Taxonomy] = None


def default_taxonomy() -> Taxonomy:
    global _TAXONOMY_DEFAULT
    if _TAXONOMY_DEFAULT is None:
        _TAXONOMY_DEFAULT = Taxonomy()
    return _TAXONOMY_DEFAULT


# ----------------------------------------------------------------------------- names, language
_NAME_TOK = rf"[{_UP}][{_LO}]+(?:[-'][{_UP}]?[{_LO}]+)?"
_NAME_ANY = rf"[{_UP}{_LO}][{_LO}]+(?:[-'][{_UP}{_LO}][{_LO}]+)?"
_NAME_STRONG_RE = re.compile(
    r"(?<![^\W\d_])(?i:меня\s+зовут|моё\s+имя|мое\s+имя|my\s+name\s+is|my\s+name's|имя|name|контактное\s+лицо|"
    r"contact\s+person)\s*[:\-–—]?\s*(?P<n>" + _NAME_ANY + r"(?:\s+" + _NAME_TOK + r"){0,2})")
_NAME_WEAK_RE = re.compile(
    r"(?<![^\W\d_])(?i:я|это|i\s*am|i'm|im|it's|this\s+is|here\s+is)\s+(?P<n>" + _NAME_TOK + r"(?:\s+" + _NAME_TOK +
    r"){0,2})")
_NAME_SIGN_RE = re.compile(
    r"(?<![^\W\d_])(?i:с\s+уважением|regards|best\s+regards|sincerely|cheers)[,.!\s]+(?P<n>" + _NAME_TOK + r"(?:\s+" +
    _NAME_TOK + r"){0,1})\s*[.!]?\s*$")
_NAME_STOP = frozenset((
    "interested looking urgent please hello hi thanks thank need want from here calling writing just sorry good new "
    "client customer order quote price the and for with can could would not very also again still "
    "заказчик клиент срочно пожалуйста здравствуйте привет добрый день вечер утро из интересует ищу хочу нужен нужна "
    "нужно нужны готов готова не и а но ваш ваша ваше ваши снова опять ещё еще это".split()))
_ENGLISH_STOP = frozenset("the and is are i need want for with please hello hi my you can how much to a of in on "
                          "looking quote contact email call me we our your it this that".split())
_CZECH_CHARS = set("ěščřžůďťňĚŠČŘŽŮĎŤŇ")
_UKR_CHARS = set("іїєґІЇЄҐ")


def extract_name(text: str) -> str:
    """Name from an explicit marker ("меня зовут", "my name is", "I'm", "с уважением,"). Never from a bare word."""
    def clean(raw: str) -> str:
        keep: list = []
        for tok in raw.split():
            if tok.lower().strip(".,!?") in _NAME_STOP:
                break
            keep.append(tok)
        if not keep or any(len(re.sub(r"\W", "", t)) < 2 for t in keep):
            return ""
        keep[0] = keep[0][:1].upper() + keep[0][1:]       # case of an explicit "name: ivan" only
        return " ".join(keep)

    for rx in (_NAME_STRONG_RE, _NAME_WEAK_RE, _NAME_SIGN_RE):
        for m in rx.finditer(text):
            n = clean(m.group("n"))
            if n:
                return n
    return ""


def detect_language(masked: str) -> str:
    cyr = len(re.findall(r"[А-Яа-яЁё]", masked))
    lat = len(re.findall(r"[A-Za-zČŠŽŘĚŮÁÉÍÓÚÝŇĎŤčšžřěůáéíóúýňďť]", masked))
    total = cyr + lat
    if total < 3:
        return "und"
    share = cyr / total
    if share >= 0.75:
        return "uk" if any(c in _UKR_CHARS for c in masked) else "ru"
    if share > 0.25:
        return "mixed"
    if any(c in _CZECH_CHARS for c in masked):
        return "cs"
    words = re.findall(r"[a-z']+", masked.lower())
    return "en" if sum(1 for w in words if w in _ENGLISH_STOP) >= 1 else "und"


# ----------------------------------------------------------------------------- contacts
_EMAIL_RE = re.compile(r"(?<![\w.+-])[A-Za-z0-9._%+-]+@[A-Za-z0-9-]+(?:\.[A-Za-z0-9-]+)*\.[A-Za-z]{2,}(?![\w-])")
_URL_RE = re.compile(r"(?:https?://|www\.|t\.me/)\S+", re.I)
_HANDLE_RE = re.compile(r"(?<![\w@.])@([A-Za-z][A-Za-z0-9_]{4,31})\b")
_AT_TOKEN_RE = re.compile(r"[^\s,;()<>]*@[^\s,;()<>]*")
_FULL_DATE_RE = re.compile(r"(?<!\d)(?:\d{1,2}[./-]\d{1,2}[./-]\d{2,4}|\d{4}-\d{2}-\d{2})(?!\d)")
_TIME_RE = re.compile(r"(?<!\d)\d{1,2}:\d{2}(?::\d{2})?(?!\d)")


def extract_phones(masked: str) -> dict:
    """-> {valid: [E.164...], ambiguous: [(raw, reason)], spans}."""
    valid: list = []
    ambiguous: list = []
    spans: list = []
    for m in _PHONE_CAND_RE.finditer(masked):
        raw = m.group("p").strip()
        digits = re.sub(r"\D", "", raw)
        n = len(digits)
        has_plus = "+" in raw[:3]
        if n > 15:
            continue
        if _ccy_after(masked, m.end())[0] or re.search(r"[€$£₽₴₸]\s*$", masked[max(0, m.start() - 3):m.start()]):
            continue                    # "150 000 000 Kč" is an amount, not a phone
        if re.match(r"\s{0,2}(?:тыс|млн|k\b|к\b)", masked[m.end():m.end() + 6], re.I):
            continue
        before = masked[max(0, m.start() - 20):m.start()]
        if _NOT_PHONE_CTX_RE.search(before):
            continue
        labelled = bool(_PHONE_LABEL_RE.search(before))
        if n < 7 or (n == 7 and not labelled) or (n == 8 and not (has_plus or labelled)):
            continue
        spans.append(m.span("p"))
        phone, reason = normalize_phone(raw)
        if phone:
            if phone not in valid:
                valid.append(phone)
        else:
            ambiguous.append((raw, reason))
    return {"valid": valid, "ambiguous": ambiguous, "spans": spans}


# ----------------------------------------------------------------------------- extraction
_TRUE = {"1", "true", "yes", "y", "да", "ответили", "отвечено", "answered", "replied", "done", "closed", "processed",
         "обработано", "закрыта", "закрыто"}
_FALSE = {"0", "false", "no", "n", "нет", "new", "новая", "новый", "open", "pending", "не обработано", "unanswered"}


def _truth(v: str) -> Optional[bool]:
    s = str(v or "").strip().lower()
    if s in _TRUE:
        return True
    if s in _FALSE:
        return False
    return None


def extract(inb: Inbound, taxonomy: Optional[Taxonomy] = None) -> Extracted:
    tax = taxonomy or default_taxonomy()
    text = clean_text(inb.text)
    col = {k: clean_text(v) for k, v in inb.fields.items() if v is not None}
    ex = Extracted(src_id=inb.src_id, source=inb.source, ts_utc=inb.ts_utc, sender=inb.sender, text=text,
                   lead_id=col.get("lead_id", ""), forwarded=inb.forwarded, is_staff=inb.is_staff,
                   answered=inb.answered)
    flags: list = []
    ref = inb.ts_ref or inb.ts_utc
    buf = list(text)

    def cur() -> str:
        return "".join(buf)

    # 1. URLs, e-mails, handles, malformed "@" tokens
    for m in list(_URL_RE.finditer(cur())):
        _blank(buf, *m.span())
    emails: list = []
    for m in list(_EMAIL_RE.finditer(cur())):
        e = m.group(0)
        if e.lower() not in [x.lower() for x in emails]:
            emails.append(e)
        _blank(buf, *m.span())
    handles: list = []
    for m in list(_HANDLE_RE.finditer(cur())):
        h = "@" + m.group(1)
        if h.lower() not in [x.lower() for x in handles]:
            handles.append(h)
        _blank(buf, *m.span())
    bad_at: list = []
    for m in list(_AT_TOKEN_RE.finditer(cur())):
        bad_at.append(m.group(0).strip(".:"))
        _blank(buf, *m.span())
    # 2. deadline (on text with contacts masked), then bare dates/times
    dl = extract_deadline(cur(), ref)
    for s, e in dl["spans"]:
        _blank(buf, s, e)
    for rx in (_FULL_DATE_RE, _TIME_RE):
        for m in list(rx.finditer(cur())):
            _blank(buf, *m.span())
    # 3. phones, amounts
    ph = extract_phones(cur())
    for s, e in ph["spans"]:
        _blank(buf, s, e)
    am = extract_amounts(cur())
    for s, e in am["spans"]:
        _blank(buf, s, e)
    body = cur()
    # 4. structured columns win over free text (a column is data the sender/CRM supplied)
    if col.get("phone"):
        p, reason = normalize_phone(col["phone"])
        if p:
            if p in ph["valid"]:
                ph["valid"].remove(p)
            ph["valid"].insert(0, p)
        else:
            ph["ambiguous"].insert(0, (col["phone"], reason))
    if col.get("email"):
        em = col["email"].strip()
        if _EMAIL_RE.fullmatch(em):
            emails.insert(0, em)
        else:
            bad_at.insert(0, em)
    if col.get("handle"):
        h = col["handle"].strip()
        h = h if h.startswith("@") else "@" + h
        if re.fullmatch(r"@[A-Za-z][A-Za-z0-9_]{4,31}", h):
            handles.insert(0, h)
    if col.get("amount"):
        cam = extract_amounts(f"{col['amount']} {col.get('currency', '')}".strip(), force_ctx=True)
        am = cam if cam["candidates"] else {"amount": None, "currency": "", "raw": col["amount"], "candidates": [],
                                            "flags": ["amount_ambiguous"], "spans": []}
    if col.get("deadline"):
        urgent = [f for f in dl["flags"] if f == "urgent"]
        for probe in (col["deadline"], "до " + col["deadline"]):
            dl2 = extract_deadline(probe, ref)
            if dl2["raw"]:
                dl = dl2
                break
        else:
            dl = {"raw": col["deadline"], "date": "", "basis": "ambiguous", "flags": ["deadline_ambiguous"],
                  "spans": []}
        dl["flags"] = list(dict.fromkeys(dl["flags"] + urgent))
    # phone
    if ph["valid"]:
        ex.phone = ph["valid"][0]
        ex.phone_alt = ph["valid"][1:]
        if ex.phone_alt:
            flags.append("phone_multiple")
    if ph["ambiguous"]:
        ex.phone_raw = "; ".join(r for r, _ in ph["ambiguous"])
        flags.append("phone_ambiguous")
    if not ph["valid"] and not ph["ambiguous"]:
        flags.append("phone_missing")
    # e-mail
    if emails:
        ex.email = emails[0].lower()
        if len(emails) > 1:
            ex.email_raw = "; ".join(emails[1:])
    if bad_at and not emails:
        ex.email_raw = "; ".join(bad_at)
        flags.append("email_invalid")
    elif bad_at:
        ex.email_raw = "; ".join([ex.email_raw] + bad_at if ex.email_raw else bad_at)
        flags.append("email_invalid")
    if not emails and not bad_at:
        flags.append("email_missing")
    ex.handle = handles[0] if handles else ""
    # amount
    ex.amount, ex.currency, ex.amount_raw = am["amount"], am["currency"], am["raw"]
    ex.amount_candidates = am["candidates"]
    flags += am["flags"]
    if not am["candidates"] and not am["raw"]:
        flags.append("amount_missing")
    # deadline
    ex.deadline_raw, ex.deadline_date, ex.deadline_basis = dl["raw"], dl["date"], dl["basis"]
    flags += dl["flags"]
    if not dl["raw"] or dl["basis"] == "urgent":
        flags.append("deadline_missing")
    # service
    if col.get("service"):
        svc, cand = tax.resolve(col["service"])
        if svc:
            ex.service = svc
        elif cand:
            ex.service_candidates = cand
        else:
            ex.service = col["service"]
            flags.append("service_unmapped")
    else:
        ex.service, ex.service_candidates = tax.resolve(body.lower())
    if ex.service_candidates:
        flags.append("service_ambiguous")
    if not ex.service and not ex.service_candidates:
        flags.append("service_missing")
    # name, language
    ex.name = col.get("name") or extract_name(body)
    if not ex.name:
        flags.append("name_missing")
    ex.language = col["language"].lower() if col.get("language") else detect_language(body)
    # contact channel for structured sources
    if inb.structured and not (ex.phone or ex.email or ex.handle):
        flags.append("contact_missing")     # a chat message is contactable through the chat itself
    # request signals
    signals: list = []
    if ex.service or ex.service_candidates:
        signals.append("service")
    if _INTENT_RE.search(body):
        signals.append("intent")
    if ph["valid"] or ph["ambiguous"]:
        signals.append("phone")
    if emails or bad_at:
        signals.append("email")
    if am["candidates"] or am["raw"]:
        signals.append("amount")
    ex.signals = signals
    if inb.structured:
        ex.is_request = True
        if not signals:
            flags.append("empty_request")
    else:
        ex.is_request = bool(signals)
        if not ex.is_request:
            ex.skip_reason = "media_only" if (not text and inb.has_media) else ("empty_text" if not text
                                                                               else "non_request")
    if inb.forwarded:
        flags.append("forwarded")
    if col.get("_ts_unparsed"):
        flags.append("timestamp_unparsed")
    if inb.is_staff:
        ex.is_request, ex.skip_reason = False, "staff_message"
    ex.flags = sorted(set(flags), key=flags.index)
    return ex


# ----------------------------------------------------------------------------- loaders
def _canon_key(k: str) -> str:
    return re.sub(r"[\s_\-]+", "_", str(k).strip().lower().lstrip("\N{ZERO WIDTH NO-BREAK SPACE}"))


_COLUMN_SYNONYMS = {
    "lead_id": ("id", "lead_id", "ticket", "ticket_id", "номер", "№", "request_id", "заявка_id"),
    "ts": ("date", "datetime", "timestamp", "created", "created_at", "time", "received", "received_at", "дата",
           "время", "дата_создания", "date_time"),
    "name": ("name", "full_name", "fullname", "client", "customer", "contact", "contact_name", "имя", "фио", "клиент",
             "контакт"),
    "phone": ("phone", "tel", "telephone", "mobile", "phone_number", "телефон", "тел", "моб", "мобильный", "whatsapp"),
    "email": ("email", "e_mail", "mail", "почта", "эл_почта", "e-mail"),
    "handle": ("telegram", "tg", "username", "handle", "телеграм", "тг", "telegram_username"),
    "service": ("service", "product", "request_type", "category", "услуга", "товар", "тема", "тип", "type"),
    "amount": ("amount", "budget", "price", "sum", "бюджет", "сумма", "цена"),
    "currency": ("currency", "ccy", "валюта"),
    "deadline": ("deadline", "due", "due_date", "срок", "дедлайн"),
    "text": ("message", "text", "comment", "comments", "description", "request", "note", "notes", "сообщение",
             "текст", "комментарий", "описание", "заявка", "запрос"),
    "answered": ("answered", "replied", "processed", "обработано", "отвечено"),
    "status": ("status", "статус"),
    "language": ("language", "lang", "язык"),
    "sender": ("sender", "from", "author", "отправитель"),
}
_COLUMN_LOOKUP = {_canon_key(s): canon for canon, syns in _COLUMN_SYNONYMS.items() for s in syns}


def parse_ts(s: str) -> Optional[dt.datetime]:
    """-> naive UTC datetime, or None when the text is not an unambiguous timestamp (never guessed)."""
    s = str(s or "").strip()
    if not s:
        return None
    if re.fullmatch(r"\d{10}", s):
        return dt.datetime.fromtimestamp(int(s), dt.timezone.utc).replace(tzinfo=None)
    if re.fullmatch(r"\d{13}", s):
        return dt.datetime.fromtimestamp(int(s) / 1000, dt.timezone.utc).replace(tzinfo=None)
    try:
        d = dt.datetime.fromisoformat(s.replace("Z", "+00:00"))
        if d.tzinfo is not None:
            d = d.astimezone(dt.timezone.utc).replace(tzinfo=None)
        return d
    except ValueError:
        pass
    for fmt in ("%d.%m.%Y %H:%M:%S", "%d.%m.%Y %H:%M", "%d.%m.%Y", "%Y/%m/%d %H:%M:%S", "%Y/%m/%d %H:%M",
                "%Y/%m/%d"):
        try:
            return dt.datetime.strptime(s, fmt)
        except ValueError:
            continue
    return None


def _map_row(row: dict) -> dict:
    out: dict = {}
    for k, v in row.items():
        canon = _COLUMN_LOOKUP.get(_canon_key(k))
        if canon and v not in (None, "") and canon not in out:
            out[canon] = v if isinstance(v, str) else str(v)
    return out


def _structured_inbound(src_id: str, source: str, mapped: dict) -> Inbound:
    ts = parse_ts(mapped.get("ts", ""))
    answered = _truth(mapped.get("answered", ""))
    if answered is None:
        answered = _truth(mapped.get("status", ""))
    fields = {k: v for k, v in mapped.items() if k not in ("ts", "text", "answered", "status", "sender")}
    return Inbound(src_id=src_id, source=source, text=mapped.get("text", ""), ts_utc=ts, ts_ref=ts,
                   sender=mapped.get("sender", ""), fields=fields, structured=True, answered=answered)


def _raw_ts_failed(mapped: dict) -> bool:
    return bool(mapped.get("ts")) and parse_ts(mapped["ts"]) is None


def load_csv(text: str) -> LoadResult:
    text = text.lstrip("\N{ZERO WIDTH NO-BREAK SPACE}")
    first_line = text.split("\n", 1)[0]
    counts = {d: first_line.count(d) for d in (",", ";", "\t")}
    delim = max(counts, key=counts.get) if max(counts.values()) else ","      # header names never contain one
    reader = csv.reader(io.StringIO(text, newline=""), delimiter=delim)
    try:
        header = next(reader)
    except StopIteration:
        raise ValueError("CSV is empty") from None
    keys = [_canon_key(h) for h in header]
    if not any(_COLUMN_LOOKUP.get(k) for k in keys):
        raise ValueError("CSV header has no recognised column (name/phone/email/service/amount/message...)")
    items: list = []
    unparsed: list = []
    blank = 0
    total = 0
    for n, row in enumerate(reader, start=1):
        if not row or not any(c.strip() for c in row):
            blank += 1
            continue
        total += 1
        if len(row) > len(header):
            unparsed.append({"ref": f"csv:row{n}", "reason": "more cells than header columns (unquoted delimiter?)"})
            continue
        row = row + [""] * (len(header) - len(row))
        mapped = _map_row(dict(zip(header, row)))
        if not mapped:
            unparsed.append({"ref": f"csv:row{n}", "reason": "no recognised column holds a value"})
            continue
        items.append(_structured_inbound(f"csv:row{n}", "csv", mapped))
        if _raw_ts_failed(mapped):
            items[-1].fields["_ts_unparsed"] = "1"
    return LoadResult(items=items, source_type="csv", total_items=total, unparsed=unparsed,
                      ignored={"blank_rows": blank}, meta={"delimiter": delim, "columns": header})


def load_jsonl(text: str) -> LoadResult:
    items: list = []
    unparsed: list = []
    blank = 0
    total = 0
    for n, line in enumerate(text.lstrip("\N{ZERO WIDTH NO-BREAK SPACE}").splitlines(), start=1):
        if not line.strip():
            blank += 1
            continue
        total += 1
        try:
            obj = json.loads(line)
        except json.JSONDecodeError as exc:
            unparsed.append({"ref": f"jsonl:line{n}", "reason": f"invalid JSON ({exc.msg})"})
            continue
        if not isinstance(obj, dict):
            unparsed.append({"ref": f"jsonl:line{n}", "reason": "JSON line is not an object"})
            continue
        mapped = _map_row(obj)
        if not mapped:
            unparsed.append({"ref": f"jsonl:line{n}", "reason": "no recognised field holds a value"})
            continue
        items.append(_structured_inbound(f"jsonl:line{n}", "jsonl", mapped))
        if _raw_ts_failed(mapped):
            items[-1].fields["_ts_unparsed"] = "1"
    return LoadResult(items=items, source_type="jsonl", total_items=total, unparsed=unparsed,
                      ignored={"blank_rows": blank})


def _load_json_leads(objs: list) -> LoadResult:
    items: list = []
    unparsed: list = []
    for n, obj in enumerate(objs, start=1):
        if not isinstance(obj, dict):
            unparsed.append({"ref": f"json:item{n}", "reason": "array element is not an object"})
            continue
        mapped = _map_row(obj)
        if not mapped:
            unparsed.append({"ref": f"json:item{n}", "reason": "no recognised field holds a value"})
            continue
        items.append(_structured_inbound(f"json:item{n}", "json_array", mapped))
        if _raw_ts_failed(mapped):
            items[-1].fields["_ts_unparsed"] = "1"
    return LoadResult(items=items, source_type="json_array", total_items=len(objs), unparsed=unparsed)


def _reply_index(items: Iterable[Inbound]) -> dict:
    idx: dict = {}
    for it in items:
        if it.reply_to:
            idx.setdefault(it.reply_to, []).append({"src_id": it.src_id, "sender": it.sender,
                                                    "is_staff": it.is_staff})
    return idx


def load_telegram_json(text: str, staff: Iterable[str] = ()) -> LoadResult:
    """Telegram Desktop ``result.json``. Messages without a usable id/date are counted as unparsed (the upstream
    parser would refuse the whole export); everything else goes through ``exchange_volume.parse.parse_export_json``.
    ``staff``: raw ``from_id`` or display names whose messages are replies, not requests."""
    data = json.loads(text)
    raw_items = data.get("messages") if isinstance(data, dict) else data
    if not isinstance(raw_items, list):
        raise ValueError("result.json: no 'messages' list (export a single chat, not the whole account)")
    staff_keys = {str(s).strip().casefold() for s in staff if str(s).strip()}
    valid: list = []
    unparsed: list = []
    service_msgs = 0
    total = 0
    for idx, item in enumerate(raw_items):
        if not isinstance(item, dict):
            total += 1
            unparsed.append({"ref": f"item[{idx}]", "reason": "not an object"})
            continue
        if item.get("type") != "message":
            service_msgs += 1
            continue
        total += 1
        try:
            dt.datetime.fromisoformat(str(item["date"]))
            int(item["date_unixtime"])
            int(item["id"])
        except (KeyError, ValueError, TypeError):
            unparsed.append({"ref": f"tg:{item.get('id', f'item[{idx}]')}",
                             "reason": "no usable id / date / date_unixtime"})
            continue
        valid.append(item)
    msgs = tg.parse_export_json(json.dumps({"messages": valid}, ensure_ascii=False)) if valid else []
    items: list = []
    for raw, m in zip(valid, msgs):
        who = {str(raw.get("from_id") or "").casefold(), str(raw.get("from") or "").casefold()} - {""}
        # a message with no sender at all must not share one anonymous label (that would look like "same sender")
        sender = m.sender if who else ""
        items.append(Inbound(
            src_id=f"tg:{m.msg_id}", source="telegram_json", text=m.text, ts_utc=m.ts_utc, ts_ref=m.ts_local,
            sender=sender, reply_to=f"tg:{m.reply_to}" if m.reply_to else None, has_media=m.has_media,
            forwarded=m.forwarded, is_staff=bool(who & staff_keys)))
    return LoadResult(items=items, source_type="telegram_json", total_items=total, unparsed=unparsed,
                      ignored={"service_messages": service_msgs}, reply_index=_reply_index(items),
                      staff_given=bool(staff_keys),
                      meta={"staff_messages": sum(1 for i in items if i.is_staff),
                            "senders": len({i.sender for i in items})})


def load_telegram_html(text: str, staff: Iterable[str] = ()) -> LoadResult:
    if staff:
        raise ValueError("--staff needs result.json: messages.html has only anonymised display names")
    msgs = tg.parse_export(text)
    blocks = len(re.findall(r'<div class="message (?=default)', text))
    unparsed = []
    if blocks > len(msgs):
        unparsed.append({"ref": "html", "reason": f"{blocks - len(msgs)} message block(s) without id/timestamp",
                         "count": blocks - len(msgs)})
    items = [Inbound(src_id=f"tg:{m.msg_id}", source="telegram_html", text=m.text, ts_utc=m.ts_utc, ts_ref=m.ts_local,
                     sender=m.sender, reply_to=f"tg:{m.reply_to}" if m.reply_to else None, has_media=m.has_media,
                     forwarded=m.forwarded) for m in msgs]
    return LoadResult(items=items, source_type="telegram_html", total_items=max(blocks, len(msgs)),
                      unparsed=unparsed, ignored={"service_messages": len(re.findall(r'<div class="message service', text))},
                      reply_index=_reply_index(items), meta={"html_blocks_unparsed": max(0, blocks - len(msgs))})


def detect_and_load(path, staff: Iterable[str] = ()) -> LoadResult:
    p = Path(path)
    suffix = p.suffix.lower()
    try:
        text = p.read_text(encoding="utf-8-sig")
    except UnicodeDecodeError as exc:
        raise ValueError(f"{p.name} is not valid UTF-8 ({exc.reason} at byte {exc.start}); re-save it as UTF-8") from None
    if suffix in (".html", ".htm"):
        return load_telegram_html(text, staff)
    if suffix in (".jsonl", ".ndjson"):
        return load_jsonl(text)
    if suffix in (".csv", ".tsv", ".txt"):
        return load_csv(text)
    if suffix == ".json":
        data = json.loads(text)
        if isinstance(data, dict):
            if isinstance(data.get("messages"), list):
                return load_telegram_json(text, staff)
            raise ValueError("result.json: no 'messages' list (export a single chat, not the whole account)")
        if isinstance(data, list):
            if any(isinstance(x, dict) and x.get("type") == "message" and "date_unixtime" in x for x in data):
                return load_telegram_json(text, staff)
            return _load_json_leads(data)
        raise ValueError("JSON input must be an object with 'messages' or an array of lead objects")
    raise ValueError(f"unsupported input type {suffix!r}: use .json .html .csv .jsonl")


# ----------------------------------------------------------------------------- answered status
def mark_answered(items: list, load: LoadResult) -> None:
    """Telegram: a request counts as answered when another sender (or a --staff sender) replied to it with
    Telegram's reply link. Without any reply link in the input the status stays unknown (None), never False."""
    if load.source_type not in ("telegram_json", "telegram_html"):
        for ex in items:
            ex.answered_basis = "column" if ex.answered is not None else "no_status_column"
        return
    relevant = [r for lst in load.reply_index.values() for r in lst if (r["is_staff"] if load.staff_given else True)]
    for ex in items:
        if not relevant:
            ex.answered, ex.answered_basis = None, "no_reply_links_in_input"
            continue
        replies = load.reply_index.get(ex.src_id, [])
        hit = [r for r in replies if (r["is_staff"] if load.staff_given else r["sender"] != ex.sender)]
        if hit:
            ex.answered, ex.answered_basis = True, "reply_link:" + hit[0]["src_id"]
        else:
            ex.answered, ex.answered_basis = False, "no_reply_link_found"
