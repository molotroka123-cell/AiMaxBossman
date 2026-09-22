#!/usr/bin/env python3
"""HW-10 MVČR — подготовка пакета ПЕРВИЧНОГО ПМЖ (trvalý pobyt) до WAIT_APPROVAL.

Что это: конвейер, который по официальным источникам (домены MV ČR / gov.cz)
находит правило и форму, скачивает и проверяет файлы, считает периоды
проживания ТОЛЬКО из подтверждённых владельцем фактов, заполняет рабочую копию
формы, переоткрывает её для сверки, собирает пакет для владельца и
останавливается на WAIT_APPROVAL в очереди подтверждений продукта
(``bcc.approvals.Approvals``).

Чего здесь НЕТ и быть не может (структурно, см. FORBIDDEN_ACTIONS и тест):
подача, подпись, оплата, запись на приём, вход через BankID / NIA, Datová
schránka, e-mail, загрузка файлов куда-либо. Сетевой слой умеет только GET на
официальные домены. Одобрение владельца фиксирует «пакет окончательный, подаю
сам» — и не запускает никакого внешнего действия. Заполнение онлайн-формы на
сайте (если она есть) — это не подача, и здесь не делается.

Переиспользуется продукт, а не пишется второй: ``bcc.file_intelligence``
(границы папки владельца, sha256, снимок «папка не изменилась»),
``bcc.html_text`` (декодирование и разбор HTML), ``bcc.v2.browser_control``
(безопасное имя файла), ``bcc.approvals`` (очередь подтверждений с защитой от
повторного использования). PDF — ``pypdf`` из закреплённого архива
(windows_bundle_lock.txt: pypdf==6.19.0); превью страниц — ``pypdfium2``, если
он есть.

Коды выхода:
    0   WAIT_APPROVAL, APPROVED_FOR_MANUAL_DELIVERY, DENIED (решение записано)
    10  PARTIAL_MISSING_DATA — частичный пакет и список вопросов
    20  BLOCKED — причина в выводе
    21  REFUSED — решение не принято (повтор, пакет изменён, не то состояние)
    2   ошибка аргументов (argparse)
"""
from __future__ import annotations

import argparse
import asyncio
import contextlib
import datetime as dt
import functools
import hashlib
import http.server
import json
import os
import re
import shutil
import socketserver
import sys
import threading
import unicodedata
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path
from typing import Any, Callable

SCHEMA = "bossman.mvcr_prepare.v1"
FACTS_SCHEMA = "bossman.mvcr.facts.v1"
APPROVAL_KIND = "mvcr.package_review"

EXIT_CODES = {
    "WAIT_APPROVAL": 0,
    "APPROVED_FOR_MANUAL_DELIVERY": 0,
    "DENIED": 0,
    "PARTIAL_MISSING_DATA": 10,
    "BLOCKED": 20,
    "REFUSED": 21,
}

STEPS = ("official_rules", "residence_timeline", "current_form", "download_and_verify",
         "fill", "reopen_verify", "package")
#: От каких шагов зависит шаг: перезапуск зависимости перезапускает и его.
STEP_DEPS = {
    "official_rules": (), "residence_timeline": (), "current_form": ("official_rules",),
    "download_and_verify": ("current_form",),
    "fill": ("download_and_verify", "residence_timeline"), "reopen_verify": ("fill",),
    "package": ("official_rules", "residence_timeline", "download_and_verify", "reopen_verify"),
}

# --------------------------------------------------------------------------- запреты

#: Действия, которых у этого конвейера нет. Их нельзя «включить флагом»: единственная
#: точка входа для внешних действий отказывает всегда.
FORBIDDEN_ACTIONS = frozenset({
    "submit", "sign", "pay", "book_appointment", "login_bankid", "login_nia",
    "datova_schranka_send", "send_email", "upload", "online_form_submit",
})


class ForbiddenAction(RuntimeError):
    pass


def request_external_action(action: str, **_ignored: Any) -> None:
    """Единственный «вход» для внешних действий — и он всегда закрыт."""
    raise ForbiddenAction(
        f"действие {action!r} этим конвейером не выполняется никогда: подача, подпись, "
        "оплата, запись на приём, BankID/NIA и Datová schránka — только владелец лично")


# --------------------------------------------------------------------------- источники

#: Реестр официальных доменов. Совпадение — домен целиком или его поддомен.
#: Из облака 2026-09-22 доступность НЕ проверена (egress-прокси: 403); на машине
#: владельца реальный прогон обязан записать фактические адреса в provenance.
OFFICIAL_DOMAINS = {
    "mvcr.cz": "Ministerstvo vnitra ČR",
    "mv.gov.cz": "Ministerstvo vnitra ČR (doména gov.cz)",
    "ipc.gov.cz": "Informace pro cizince (MV ČR)",
    "portal.gov.cz": "Portál veřejné správy",
    "frs.gov.cz": "Rezervační systém MV ČR — только чтение, запись не выполняется",
}

#: Стартовые страницы реального режима. Ссылки с них проходятся по ключевым словам.
DEFAULT_ENTRY_POINTS = ("https://www.mvcr.cz/", "https://ipc.gov.cz/", "https://mv.gov.cz/")

#: Адреса, по которым GET сам может быть действием (вход, запись, оплата, отправка).
_ACTION_URL = re.compile(
    r"(login|prihlas|bankid|identitaobcana|/nia\b|isds|mojedatovaschranka|rezervac|"
    r"reservation|/objednat|platba|payment|checkout|submit|odeslat)", re.I)

CRAWL_KEYWORDS = ("trval", "pobyt", "cizin", "formular", "zadost", "poplat", "pokyn",
                  "tiskopis", "residence", "foreigner")
MAX_PAGES = 60
MAX_DEPTH = 3
MAX_BYTES = 30 * 1024 * 1024
FETCH_TIMEOUT_S = 30


def fold(text: str) -> str:
    """Нижний регистр без диакритики: 'Žádost' → 'zadost'."""
    decomposed = unicodedata.normalize("NFKD", str(text or ""))
    return "".join(c for c in decomposed if not unicodedata.combining(c)).lower()


def official_url(url: str) -> tuple[bool, str]:
    """(официальный?, причина). Похожие домены отвергаются с названной причиной."""
    try:
        parts = urllib.parse.urlsplit(str(url))
    except ValueError:
        return False, "BAD_URL"
    if parts.scheme != "https":
        return False, "NOT_HTTPS"
    if parts.username or parts.password or "@" in parts.netloc:
        return False, "USERINFO_IN_URL"
    host = (parts.hostname or "").rstrip(".").lower()
    if not host:
        return False, "BAD_HOST"
    if not host.isascii() or "xn--" in host:
        return False, "IDN_LOOKALIKE"
    if parts.port not in (None, 443):
        return False, "NON_STANDARD_PORT"
    for domain in OFFICIAL_DOMAINS:
        if host == domain or host.endswith("." + domain):
            if _ACTION_URL.search(parts.path + "?" + parts.query):
                return False, "ACTION_URL_REFUSED"
            return True, "OFFICIAL:" + domain
    return False, "NOT_OFFICIAL_DOMAIN"


# --------------------------------------------------------------------------- продукт

@functools.lru_cache(maxsize=1)
def product() -> Any:
    """Модули продукта. В архиве они в runtime; в клоне — рядом с tools/."""
    here = Path(__file__).resolve().parent
    for candidate in (here.parent / "command-center", here.parent):
        if (candidate / "bcc").is_dir() or (candidate / "bossman_shared").is_dir():
            if str(candidate) not in sys.path:
                sys.path.append(str(candidate))
    from bcc import html_text
    from bcc.file_intelligence.models import Denied
    from bcc.file_intelligence.scope import ScopePolicy, canonical
    from bcc.file_intelligence.verify import scan_scope, sha256_file, unexpected_mutations
    from bcc.v2.browser_control import safe_download_name

    class _P:
        pass
    p = _P()
    p.html_text, p.Denied, p.ScopePolicy, p.canonical = html_text, Denied, ScopePolicy, canonical
    p.scan_scope, p.sha256_file, p.unexpected_mutations = scan_scope, sha256_file, unexpected_mutations
    p.safe_download_name = safe_download_name
    return p


def approvals_backend() -> tuple[Any, Any, Any]:
    product()
    from bcc.approvals import Approvals
    from bcc.db import Database
    from bcc.events import EventBus
    return Database, EventBus, Approvals


def _pypdf() -> Any:
    try:
        import pypdf
    except ImportError:
        return None
    return pypdf


# --------------------------------------------------------------------------- сеть (только GET)

class FetchError(RuntimeError):
    pass


class Fetched:
    def __init__(self, url: str, final_url: str, status: int, content_type: str, body: bytes):
        self.url, self.final_url, self.status = url, final_url, status
        self.content_type, self.body = content_type, body
        self.retrieved_at = _now()


class _OfficialRedirects(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):  # noqa: D401
        ok, reason = official_url(newurl)
        if not ok:
            raise FetchError(f"REDIRECT_OFF_OFFICIAL:{reason}:{newurl}")
        return super().redirect_request(req, fp, code, msg, headers, newurl)


class Fetcher:
    """Только GET, только официальные домены. Никаких тел запроса, cookies, форм.

    В режиме fixtures логический адрес остаётся https://<официальный хост>/…,
    а транспорт идёт на локальный http.server — проверки домена те же самые.
    """

    def __init__(self, transport_base: str | None = None):
        self.transport_base = transport_base
        self.requests: list[dict] = []
        self._opener = urllib.request.build_opener(_OfficialRedirects())

    def get(self, url: str) -> Fetched:
        ok, reason = official_url(url)
        if not ok:
            raise FetchError(f"REFUSED:{reason}")
        target = url
        if self.transport_base:
            parts = urllib.parse.urlsplit(url)
            target = f"{self.transport_base}/{parts.hostname}{parts.path or '/'}"
            if target.endswith("/"):
                target += "index.html"
            if parts.query:
                target += "?" + parts.query
        request = urllib.request.Request(target, method="GET", headers={
            "User-Agent": "Bossman-HW10-prepare/1 (read-only)",
            "Accept": "text/html,application/pdf;q=0.9,*/*;q=0.1"})
        self.requests.append({"method": "GET", "url": url, "at": _now()})
        try:
            with self._opener.open(request, timeout=FETCH_TIMEOUT_S) as resp:
                body = resp.read(MAX_BYTES + 1)
                final = resp.geturl()
                status = int(getattr(resp, "status", 200))
                ctype = resp.headers.get("Content-Type", "")
        except urllib.error.HTTPError as exc:
            raise FetchError(f"HTTP_{exc.code}") from None
        except (urllib.error.URLError, OSError) as exc:
            raise FetchError(f"NETWORK:{type(exc).__name__}") from None
        if len(body) > MAX_BYTES:
            raise FetchError("TOO_LARGE")
        final_logical = url if self.transport_base else final
        ok, reason = official_url(final_logical)
        if not ok:
            raise FetchError(f"FINAL_URL_NOT_OFFICIAL:{reason}")
        return Fetched(url, final_logical, status, ctype, body)


class _QuietHandler(http.server.SimpleHTTPRequestHandler):
    def log_message(self, *_a: Any) -> None:
        pass

    def do_POST(self) -> None:          # the fake site records forbidden methods loudly
        self.server.forbidden_methods.append(("POST", self.path))  # type: ignore[attr-defined]
        self.send_error(405)

    do_PUT = do_DELETE = do_PATCH = do_POST


@contextlib.contextmanager
def fixture_site(root: Path):
    """Локальный http.server с синтетическим «официальным» сайтом (только для CI)."""
    handler = functools.partial(_QuietHandler, directory=str(root))
    server = socketserver.ThreadingTCPServer(("127.0.0.1", 0), handler)
    server.daemon_threads = True
    server.forbidden_methods = []  # type: ignore[attr-defined]
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield server, f"http://127.0.0.1:{server.server_address[1]}"
    finally:
        server.shutdown()
        server.server_close()


# --------------------------------------------------------------------------- утилиты

def _now() -> str:
    return dt.datetime.now(dt.timezone.utc).replace(microsecond=0).isoformat()


def _sha_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _write_json(path: Path, data: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(path.name + ".tmp")
    tmp.write_text(json.dumps(data, ensure_ascii=False, indent=2, default=str), encoding="utf-8")
    os.replace(tmp, path)


def _read_json(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8"))


def _sentences(text: str) -> list[str]:
    return [s.strip() for s in re.split(r"(?<=[.!?])\s+|\n+", text or "") if s.strip()]


_FEE = re.compile(r"(\d{1,3}(?:[   .]\d{3})+|\d+)\s*(?:,-\s*)?(?:kc|czk)\b")
_RULE_YEARS = re.compile(r"po (\d+) letech|(\d+) let\w* nepretrzit")
_VERSION = re.compile(r"(platn\w*|verze|vydan\w*)[^0-9]{0,40}(\d{1,2})\.\s?(\d{1,2})\.\s?(\d{4})")
_ARCHIVED = ("archiv", "neplatn", "zrusen", "stara verze", "starsi verze", "puvodni verze")
_PLACEHOLDER = re.compile(r"^(x+|\?+|-+|_+|n/?a|tbd|todo|xxx.*|neznam\w*|nevim|unknown|placeholder|"
                          r"doplnit|\.\.\.)$", re.I)


def fee_quotes(text: str, source: str) -> list[dict]:
    found = []
    for sentence in _sentences(text):
        folded = fold(sentence)
        if "poplat" not in folded:
            continue
        for match in _FEE.finditer(folded):
            amount = int(re.sub(r"\D", "", match.group(1)))
            found.append({"amount_czk": amount, "quote": sentence[:300], "source": source})
    return found


def rule_quotes(text: str, source: str) -> list[dict]:
    found = []
    for sentence in _sentences(text):
        folded = fold(sentence)
        if "trval" not in folded:
            continue
        match = _RULE_YEARS.search(folded)
        if match:
            found.append({"years": int(match.group(1) or match.group(2)),
                          "quote": sentence[:300], "source": source})
    return found


def channel_quotes(text: str, source: str) -> list[dict]:
    found = []
    for sentence in _sentences(text):
        folded = fold(sentence)
        if (any(k in folded for k in ("osobne", "elektronick", "datov", "postou"))
                and any(k in folded for k in ("zadost", "podav", "podani", "podat"))):
            found.append({"quote": sentence[:300], "source": source,
                          "electronic_not_possible": bool(re.search(
                              r"elektronick\w* podani\w*[^.]*\bneni mozn", folded))})
    return found


def version_date(text: str) -> str | None:
    match = _VERSION.search(fold(text))
    if not match:
        return None
    try:
        return dt.date(int(match.group(4)), int(match.group(3)), int(match.group(2))).isoformat()
    except ValueError:
        return None


# --------------------------------------------------------------------------- PDF

def sniff_pdf(body: bytes) -> tuple[bool, str]:
    """Тип по байтам, а не по имени/заголовку. HTML под видом .pdf — отказ."""
    head = body[:1024].lstrip().lower()
    if head.startswith((b"<!doctype", b"<html", b"<?xml", b"<head", b"<body", b"{")):
        return False, "HTML_OR_TEXT_AS_PDF"
    if not body.startswith(b"%PDF-"):
        return False, "NO_PDF_MAGIC"
    if b"%%EOF" not in body[-2048:]:
        return False, "TRUNCATED_PDF"
    if len(body) < 200:
        return False, "TOO_SMALL"
    return True, "application/pdf"


def read_pdf(path_or_bytes: Any) -> dict:
    """Открыть PDF заново и вернуть страницы, текст, поля. Исключение → нечитаемый."""
    pypdf = _pypdf()
    if pypdf is None:
        raise RuntimeError("PYPDF_MISSING")
    import io
    source = io.BytesIO(path_or_bytes) if isinstance(path_or_bytes, bytes) else str(path_or_bytes)
    reader = pypdf.PdfReader(source)
    if reader.is_encrypted:
        raise RuntimeError("ENCRYPTED_PDF")
    text = "\n".join((page.extract_text() or "") for page in reader.pages)
    meta = reader.metadata or {}
    meta_text = " ".join(str(v) for v in meta.values())
    fields = {}
    for name, field in (reader.get_fields() or {}).items():
        fields[name] = {"type": str(field.get("/FT", "")), "tooltip": str(field.get("/TU", "") or ""),
                        "value": None if field.get("/V") in (None, "") else str(field.get("/V"))}
    return {"pages": len(reader.pages), "text": text, "meta_text": meta_text, "fields": fields}


# --------------------------------------------------------------------------- факты

#: Сопоставление полей формы и ключей фактов (по имени поля и подсказке /TU).
FIELD_RULES = (
    ("OWNER_ONLY", r"podpis|signature"),
    ("surname", r"prijmeni|surname|family name"),
    ("given_names", r"\bjmeno\b|given name|first name"),
    ("birth_date", r"datum narozeni|date of birth|birth date"),
    ("nationality", r"statni obcanstvi|statni prislusnost|nationality|citizenship"),
    ("passport_number", r"cislo (cestovniho dokladu|pasu)|passport"),
    ("address_cz", r"adresa|address"),
    ("continuous_since", r"nepretrzit\w* pobyt|pobyt na uzemi od|continuous residence"),
)
DATE_KEYS = {"birth_date", "continuous_since"}


def classify_field(name: str, tooltip: str, field_map: dict) -> tuple[str, list[str]]:
    if name in field_map:
        key = str(field_map[name])
        return ("OWNER_ONLY", []) if key == "OWNER_ONLY" else ("MAPPED", [key])
    text = fold(f"{name} {tooltip}").replace("_", " ")
    if re.search(FIELD_RULES[0][1], text):
        return "OWNER_ONLY", []
    keys = [key for key, pattern in FIELD_RULES[1:] if re.search(pattern, text)]
    if not keys:
        return "UNMAPPED", []
    if len(keys) > 1:
        return "AMBIGUOUS", keys
    return "MAPPED", keys


def check_fact(fact: Any, owner: Path | None) -> tuple[bool, str, dict]:
    """(можно использовать?, причина, доказательство). Никаких догадок."""
    if not isinstance(fact, dict) or fact.get("value") in (None, ""):
        return False, "MISSING", {}
    value = str(fact["value"]).strip()
    if not value or _PLACEHOLDER.match(value):
        return False, "PLACEHOLDER_VALUE", {}
    if fact.get("conflicts_with"):
        return False, "CONFLICT", {}
    if fact.get("confirmed") is not True:
        return False, "UNCONFIRMED", {}
    source = str(fact.get("source") or "").strip()
    if not source:
        return False, "NO_SOURCE", {}
    if source.startswith("owner:"):
        return True, "OWNER_STATEMENT", {"source": source}
    if source.startswith("doc:"):
        if owner is None:
            return False, "DOC_WITHOUT_OWNER_FOLDER", {}
        rel = source[4:]
        p = product()
        try:
            policy = p.ScopePolicy([owner])
            path = policy.check(owner / rel, mutating=False)
        except p.Denied as exc:
            return False, "DOC_OUTSIDE_OWNER_FOLDER:" + exc.refusal.value, {}
        if not path.is_file():
            return False, "DOC_NOT_FOUND", {}
        return True, "OWNER_DOCUMENT", {"source": source, "document": rel,
                                        "sha256": p.sha256_file(path)}
    return False, "UNKNOWN_SOURCE_KIND", {}


def _date(text: str) -> dt.date:
    return dt.date.fromisoformat(str(text))


def calendar_diff(start: dt.date, end_inclusive: dt.date) -> tuple[int, int, int]:
    """Годы/месяцы/дни от start до end_inclusive+1 день (календарно)."""
    end = end_inclusive + dt.timedelta(days=1)
    years = end.year - start.year
    months = end.month - start.month
    days = end.day - start.day
    if days < 0:
        months -= 1
        prev_month_end = end.replace(day=1) - dt.timedelta(days=1)
        days += prev_month_end.day
    if months < 0:
        years -= 1
        months += 12
    return years, months, days


def residence_timeline(facts: dict, owner: Path | None, as_of: dt.date) -> dict:
    periods_in = facts.get("residence_periods")
    questions: list[dict] = []
    evidence: list[dict] = []
    usable: list[dict] = []
    if not isinstance(periods_in, list) or not periods_in:
        questions.append({"id": "T-NONE", "topic": "residence_periods",
                          "text": "Нет ни одного подтверждённого периода проживания. Укажите периоды "
                                  "(с датами и документом-источником)."})
        periods_in = []
    for index, period in enumerate(periods_in, start=1):
        pid = str((period or {}).get("id") or f"P{index}")
        if not isinstance(period, dict):
            questions.append({"id": f"T-{pid}", "topic": "residence_period",
                              "text": f"Период {pid}: неверный формат."})
            continue
        ok, reason, proof = check_fact({"value": f"{period.get('from')}..{period.get('to')}",
                                        **period}, owner)
        try:
            start = _date(period.get("from"))
            raw_end = period.get("to")
            end = as_of if raw_end == "ongoing" else _date(raw_end)
        except (TypeError, ValueError):
            ok, reason = False, "BAD_DATE"
            start = end = None
        if ok and end > as_of:
            ok, reason = False, "END_AFTER_AS_OF"
        if ok and start > end:
            ok, reason = False, "START_AFTER_END"
        evidence.append({"fact": f"период {pid}", "from": period.get("from"), "to": period.get("to"),
                         "basis": period.get("basis", ""), "source": period.get("source", ""),
                         "confirmed": period.get("confirmed") is True,
                         "status": "USED" if ok else reason, **proof})
        if not ok:
            questions.append({"id": f"T-{pid}", "topic": "residence_period",
                              "text": f"Период {pid}: {reason_ru(reason)} — подтвердите даты и источник."})
            continue
        if "studi" in fold(period.get("basis", "")):
            questions.append({"id": f"T-{pid}-STUDY", "topic": "study_period",
                              "text": f"Период {pid} — учёба. Как он засчитывается, решает официальное "
                                      "правило; Bossman его не толкует. Подтвердите по источнику."})
        usable.append({"id": pid, "from": start, "to": end, "ongoing": raw_end == "ongoing",
                       "days": (end - start).days + 1})
    usable.sort(key=lambda p: p["from"])
    calc: list[str] = []
    conflicts, gaps = [], []
    for prev, cur in zip(usable, usable[1:]):
        if cur["from"] <= prev["to"]:
            conflicts.append((prev["id"], cur["id"]))
        elif (cur["from"] - prev["to"]).days > 1:
            gaps.append({"after": prev["id"], "before": cur["id"],
                         "gap_days": (cur["from"] - prev["to"]).days - 1})
    for a, b in conflicts:
        questions.append({"id": f"T-OVERLAP-{a}-{b}", "topic": "residence_conflict",
                          "text": f"Периоды {a} и {b} пересекаются. Какой из них верен?"})
    for p in usable:
        tail = f" (по {as_of.isoformat()}, период продолжается)" if p["ongoing"] else ""
        calc.append(f"{p['id']}: {p['from'].isoformat()} → {p['to'].isoformat()}{tail} = {p['days']} дн.")
    for g in gaps:
        calc.append(f"Разрыв между {g['after']} и {g['before']}: {g['gap_days']} дн. — непрерывность прервана.")
    chain = None
    if usable and not conflicts:
        chain_start = usable[-1]["from"]
        chain_end = usable[-1]["to"]
        for prev, cur in reversed(list(zip(usable, usable[1:]))):
            if (cur["from"] - prev["to"]).days == 1:
                chain_start = prev["from"]
            else:
                break
        y, m, d = calendar_diff(chain_start, chain_end)
        total = (chain_end - chain_start).days + 1
        chain = {"from": chain_start.isoformat(), "to": chain_end.isoformat(), "days": total,
                 "years": y, "months": m, "days_rest": d}
        calc.append(f"Непрерывная цепочка: {chain_start.isoformat()} → {chain_end.isoformat()} = "
                    f"{total} дн. = {y} г. {m} мес. {d} дн. (календарно, включая обе даты).")
    elif conflicts:
        calc.append("Расчёт не выполнен: периоды противоречат друг другу.")
    return {"as_of": as_of.isoformat(), "periods": [
        {**p, "from": p["from"].isoformat(), "to": p["to"].isoformat()} for p in usable],
        "gaps": gaps, "chain": chain, "calculation": calc, "evidence": evidence,
        "questions": questions, "complete": not questions and chain is not None}


REASONS_RU = {
    "MISSING": "нет значения", "PLACEHOLDER_VALUE": "значение похоже на заглушку, а не на данные",
    "CONFLICT": "есть противоречащее значение", "UNCONFIRMED": "не подтверждено владельцем",
    "NO_SOURCE": "не указан источник", "DOC_NOT_FOUND": "документ-источник не найден в папке владельца",
    "DOC_WITHOUT_OWNER_FOLDER": "ссылка на документ, но папка владельца не задана",
    "UNKNOWN_SOURCE_KIND": "неизвестный вид источника (нужно doc:… или owner:…)",
    "BAD_DATE": "дата не в формате ГГГГ-ММ-ДД", "END_AFTER_AS_OF": "конец периода позже даты расчёта",
    "START_AFTER_END": "начало позже конца",
}


def reason_ru(code: str) -> str:
    if code.startswith("DOC_OUTSIDE_OWNER_FOLDER"):
        return "документ вне разрешённой папки владельца"
    return REASONS_RU.get(code, code)


# --------------------------------------------------------------------------- конвейер

class Blocked(Exception):
    def __init__(self, reason: str, detail: str = ""):
        super().__init__(reason)
        self.reason, self.detail = reason, detail


class Pipeline:
    def __init__(self, args: argparse.Namespace, fetcher: Fetcher, fixtures: Path | None):
        self.args = args
        self.fetcher = fetcher
        self.fixtures = fixtures
        self.out = Path(args.out).resolve()
        self.owner = Path(args.owner_folder).resolve() if args.owner_folder else None
        self.state_path = self.out / "state.json"
        self.state: dict = _read_json(self.state_path) if self.state_path.is_file() else {}
        self.state.setdefault("schema", SCHEMA)
        self.state.setdefault("steps", {})
        self.state.setdefault("history", [])
        self.log_path = self.out / "run.log"
        self.facts: dict = {}
        self.facts_bytes = b""
        self.rerun: set[str] = set()
        #: Пока входы не проверены, на диск не пишется НИЧЕГО: --out может оказаться
        #: внутри папки владельца, а она только читается.
        self.writable = False

    # -- журнал без персональных данных: только шаги, статусы, счётчики и официальные URL
    def log(self, event: str, **data: Any) -> None:
        if not self.writable:
            return
        safe = {k: v for k, v in data.items() if isinstance(v, (int, bool)) or k in ("step", "status", "reason", "url")}
        self.out.mkdir(parents=True, exist_ok=True)
        with self.log_path.open("a", encoding="utf-8") as fh:
            fh.write(json.dumps({"at": _now(), "event": event, **safe}, ensure_ascii=False) + "\n")

    def save(self) -> None:
        if self.writable:
            _write_json(self.state_path, self.state)

    # -- отпечатки входов, от которых зависят шаги
    def fingerprints(self) -> dict:
        entry = self.entry_points()
        src = _sha_bytes(json.dumps({"mode": "fixtures" if self.fixtures else "live",
                                     "entry": entry}, sort_keys=True).encode())
        facts = _sha_bytes(self.facts_bytes + str(self.as_of()).encode()
                           + str(self.owner or "").encode())
        fmap = _sha_bytes(json.dumps(self.field_map(), sort_keys=True).encode())
        return {"official_rules": src, "current_form": src, "download_and_verify": src,
                "residence_timeline": facts,
                "fill": _sha_bytes((src + facts + fmap).encode()),
                "reopen_verify": _sha_bytes((src + facts + fmap).encode()),
                "package": _sha_bytes((src + facts + fmap).encode())}

    def entry_points(self) -> list[str]:
        if self.fixtures:
            return list(_read_json(self.fixtures / "sources.json").get("entry_points", []))
        return list(self.args.entry_url or []) + list(DEFAULT_ENTRY_POINTS)

    def as_of(self) -> dt.date:
        raw = self.args.as_of or self.facts.get("as_of")
        return _date(raw) if raw else dt.date.today()

    def field_map(self) -> dict:
        if self.args.field_map:
            return dict(_read_json(Path(self.args.field_map)))
        return dict(self.facts.get("field_map") or {})

    # -- проверки входов
    def check_inputs(self) -> None:
        p = product()
        if self.owner is None:
            raise Blocked("OWNER_FOLDER_REQUIRED", "--owner-folder обязателен")
        try:
            p.ScopePolicy([self.owner]).check(self.owner, mutating=False)
        except p.Denied as exc:
            raise Blocked("OWNER_FOLDER_NOT_ALLOWED", exc.refusal.value) from None
        if not self.owner.is_dir():
            raise Blocked("OWNER_FOLDER_MISSING")
        for a, b in ((self.out, self.owner), (self.owner, self.out)):
            if a == b or b in a.parents:
                raise Blocked("OUT_OVERLAPS_OWNER_FOLDER",
                              "папка владельца только читается; --out должен быть вне неё")
        self.writable = True
        try:
            self.facts_bytes = Path(self.args.facts).read_bytes()
            self.facts = json.loads(self.facts_bytes.decode("utf-8"))
        except (OSError, ValueError):
            raise Blocked("FACTS_UNREADABLE") from None
        if not isinstance(self.facts, dict) or self.facts.get("schema") != FACTS_SCHEMA:
            raise Blocked("FACTS_SCHEMA", f"ожидается schema={FACTS_SCHEMA}")
        try:
            self.as_of()
        except ValueError:
            raise Blocked("BAD_AS_OF") from None

    # -- шаги
    def run(self) -> dict:
        self.check_inputs()
        prints = self.fingerprints()
        steps = self.state["steps"]
        for name in STEPS:
            record = steps.get(name) or {}
            fresh = (record.get("status") == "done" and record.get("fingerprint") == prints[name]
                     and not any(dep in self.rerun for dep in STEP_DEPS[name])
                     and self.artifacts_intact(name, record.get("result") or {}))
            if fresh:
                self.log("step_resumed", step=name)
                continue
            self.rerun.add(name)
            self.log("step_started", step=name)
            try:
                result = getattr(self, "step_" + name)()
            except Blocked as exc:
                steps[name] = {"status": "blocked", "reason": exc.reason, "at": _now()}
                self.save()
                self.log("step_blocked", step=name, reason=exc.reason)
                raise
            steps[name] = {"status": "done", "fingerprint": prints[name], "at": _now(),
                           "result": result}
            self.save()
            self.log("step_done", step=name)
        return steps["package"]["result"]

    def artifacts_intact(self, name: str, result: dict) -> bool:
        p = product()
        files = []
        if name == "download_and_verify":
            files = [(d["path"], d["sha256"]) for d in result.get("documents", [])]
        elif name in ("fill", "reopen_verify"):
            if result.get("work_file"):
                files = [(result["work_file"], result["work_sha256"])]
        elif name == "package":
            files = [(f["path"], f["sha256"]) for f in result.get("manifest", {}).get("files", [])]
        for rel, sha in files:
            path = self.out / rel
            if not path.is_file() or p.sha256_file(path) != sha:
                self.log("artifact_changed", step=name)
                return False
        return True

    def result_of(self, name: str) -> dict:
        return self.state["steps"][name]["result"]

    def step_official_rules(self) -> dict:
        p = product()
        pages, rejected, forms, instructions, external = [], [], [], [], []
        fees, rules, channels = [], [], []
        queue = [(u, 0) for u in self.entry_points()]
        seen: set[str] = set()
        while queue and len(pages) < MAX_PAGES:
            url, depth = queue.pop(0)
            if url in seen:
                continue
            seen.add(url)
            ok, reason = official_url(url)
            if not ok:
                rejected.append({"url": url, "reason": reason})
                self.log("source_rejected", url=url, reason=reason)
                continue
            try:
                resp = self.fetcher.get(url)
            except FetchError as exc:
                rejected.append({"url": url, "reason": str(exc)})
                continue
            if not resp.body[:1024].lstrip().lower().startswith((b"<!doctype", b"<html")):
                continue
            text, _enc, _bad = p.html_text.decode_body(resp.body, resp.content_type)
            ex = p.html_text.extract(text, base_url=resp.final_url)
            page_text = f"{ex.title}\n{ex.text}"
            pages.append({"url": url, "final_url": resp.final_url, "retrieved_at": resp.retrieved_at,
                          "http_status": resp.status, "sha256": _sha_bytes(resp.body),
                          "title": ex.title, "official": official_url(resp.final_url)[1]})
            fees += fee_quotes(page_text, resp.final_url)
            rules += rule_quotes(page_text, resp.final_url)
            channels += channel_quotes(page_text, resp.final_url)
            for link in ex.links:
                folded = fold(f"{link.text} {link.url}")
                link_ok, link_reason = official_url(link.url)
                is_pdf = urllib.parse.urlsplit(link.url).path.lower().endswith(".pdf")
                if not link_ok:
                    if any(k in folded for k in CRAWL_KEYWORDS):
                        external.append({"url": link.url, "text": link.text, "page": resp.final_url,
                                         "reason": link_reason})
                    continue
                entry = {"url": link.url, "text": link.text, "page": resp.final_url,
                         "archived_marker": any(k in folded for k in _ARCHIVED)}
                if is_pdf and "zadost" in folded and "trval" in folded:
                    forms.append(entry)
                elif is_pdf and any(k in folded for k in ("pokyn", "poplat", "platb", "instrukc", "poucen")):
                    instructions.append(entry)
                elif (not is_pdf and depth < MAX_DEPTH and link.url not in seen
                      and any(k in folded for k in CRAWL_KEYWORDS)):
                    queue.append((link.url, depth + 1))
        if not pages:
            raise Blocked("NO_OFFICIAL_SOURCE",
                          "ни одна официальная страница не получена; похожие домены отвергнуты")
        dedup = lambda items: list({i["url"]: i for i in items}.values())  # noqa: E731
        return {"pages": pages, "rejected": rejected, "external_ignored": dedup(external),
                "form_links": dedup(forms), "instruction_links": dedup(instructions),
                "fees": fees, "rules": rules, "channels": channels,
                "domains_registry": OFFICIAL_DOMAINS}

    def step_residence_timeline(self) -> dict:
        return residence_timeline(self.facts, self.owner, self.as_of())

    def step_current_form(self) -> dict:
        links = self.result_of("official_rules")["form_links"]
        stale = [dict(l, stale_reason="ARCHIVED_MARKER_ON_OFFICIAL_PAGE") for l in links if l["archived_marker"]]
        candidates = [l for l in links if not l["archived_marker"]]
        if not candidates:
            raise Blocked("ONLY_STALE_FORM" if stale else "FORM_NOT_FOUND",
                          "на официальных страницах нет действующей формы")
        return {"candidates": candidates, "stale": stale,
                "instructions": self.result_of("official_rules")["instruction_links"]}

    def download(self, link: dict, kind: str) -> dict:
        p = product()
        record = {"kind": kind, "source_url": link["url"], "page": link.get("page"),
                  "link_text": link.get("text")}
        try:
            resp = self.fetcher.get(link["url"])
        except FetchError as exc:
            return {**record, "valid": False, "reason": str(exc)}
        ok, sniffed = sniff_pdf(resp.body)
        record.update(final_url=resp.final_url, retrieved_at=resp.retrieved_at,
                      http_status=resp.status, content_type_header=resp.content_type,
                      size=len(resp.body), sha256=_sha_bytes(resp.body), sniffed_type=sniffed)
        if not ok:
            return {**record, "valid": False, "reason": sniffed}
        try:
            parsed = read_pdf(resp.body)
        except Exception as exc:  # noqa: BLE001 — любая ошибка разбора = нечитаемый файл
            return {**record, "valid": False, "reason": "UNREADABLE_PDF:" + str(exc)[:80]}
        corpus = fold(parsed["text"] + " " + parsed["meta_text"])
        if kind == "form" and not ("zadost" in corpus and "trval" in corpus):
            return {**record, "valid": False, "reason": "NOT_INTENDED_DOCUMENT"}
        name = p.safe_download_name(urllib.parse.urlsplit(resp.final_url).path.rsplit("/", 1)[-1])
        rel = Path("downloads") / "original" / f"{record['sha256'][:12]}_{name}"
        target = self.out / rel
        target.parent.mkdir(parents=True, exist_ok=True)
        if not target.exists():
            target.write_bytes(resp.body)
            os.chmod(target, 0o444)
        record.update(valid=True, path=rel.as_posix(), pages=parsed["pages"],
                      form_fields=len(parsed["fields"]),
                      version_date=version_date(parsed["text"] + " " + parsed["meta_text"]),
                      fees=fee_quotes(parsed["text"], resp.final_url) if kind == "instructions" else [])
        _write_json(self.out / "downloads" / "provenance" / f"{record['sha256'][:12]}.json", record)
        return record

    def step_download_and_verify(self) -> dict:
        cur = self.result_of("current_form")
        forms = [self.download(l, "form") for l in cur["candidates"]]
        instr = [self.download(l, "instructions") for l in cur["instructions"]]
        valid = [f for f in forms if f["valid"]]
        stale = list(cur["stale"])
        if not valid:
            raise Blocked("NO_VALID_FORM_DOWNLOAD",
                          "; ".join(f"{f['source_url']}: {f['reason']}" for f in forms))
        if len(valid) > 1:
            if any(f["version_date"] is None for f in valid):
                raise Blocked("FORM_AMBIGUOUS", "несколько форм, версия не у всех указана")
            valid.sort(key=lambda f: f["version_date"], reverse=True)
            if valid[0]["version_date"] == valid[1]["version_date"] and valid[0]["sha256"] != valid[1]["sha256"]:
                raise Blocked("FORM_AMBIGUOUS", "две разные формы с одной версией")
            for older in valid[1:]:
                stale.append({"url": older["source_url"], "text": older["link_text"],
                              "stale_reason": f"OLDER_VERSION:{older['version_date']}<{valid[0]['version_date']}"})
        chosen = valid[0]
        documents = [d for d in forms + instr if d["valid"]]
        return {"chosen_form": chosen, "stale": stale, "documents": documents,
                "rejected": [d for d in forms + instr if not d["valid"]]}

    def step_fill(self) -> dict:
        p = product()
        dl = self.result_of("download_and_verify")
        chosen = dl["chosen_form"]
        original = self.out / chosen["path"]
        if p.sha256_file(original) != chosen["sha256"]:
            raise Blocked("ORIGINAL_CHANGED", "оригинал формы изменился после скачивания")
        parsed = read_pdf(original)
        timeline = self.result_of("residence_timeline")
        fmap = self.field_map()
        person = self.facts.get("person") or {}
        fields, values = [], {}
        for name, info in parsed["fields"].items():
            status, keys = classify_field(name, info["tooltip"], fmap)
            row = {"field": name, "tooltip": info["tooltip"], "keys": keys}
            if status != "MAPPED":
                fields.append({**row, "status": status})
                continue
            if info["type"] != "/Tx":
                fields.append({**row, "status": "NOT_TEXT_FIELD"})
                continue
            key = keys[0]
            if key == "continuous_since":
                if timeline["complete"] and timeline["chain"]:
                    value, source = timeline["chain"]["from"], "calc:residence_timeline"
                    ok, reason = True, "CALCULATED"
                else:
                    ok, reason, value, source = False, "TIMELINE_INCOMPLETE", None, None
            else:
                ok, reason, proof = check_fact(person.get(key), self.owner)
                value = str(person[key]["value"]).strip() if ok else None
                source = proof.get("source") if ok else None
            if not ok:
                fields.append({**row, "status": "MISSING_DATA", "reason": reason})
                continue
            if key in DATE_KEYS:
                d = _date(value)
                value = f"{d.day:02d}.{d.month:02d}.{d.year:04d}"
            values[name] = value
            fields.append({**row, "status": "FILLED", "value": value, "source": source})
        pypdf = _pypdf()
        work_dir = self.out / "work"
        work_dir.mkdir(parents=True, exist_ok=True)
        version = 1 + max([int(m.group(1)) for f in work_dir.glob("filled_v*.pdf")
                           if (m := re.match(r"filled_v(\d+)\.pdf$", f.name))] or [0])
        work = work_dir / f"filled_v{version}.pdf"
        writer = pypdf.PdfWriter(clone_from=str(original))
        if values:
            for page in writer.pages:
                writer.update_page_form_field_values(page, values, auto_regenerate=False)
            writer.set_need_appearances_writer(True)
        with work.open("wb") as fh:
            writer.write(fh)
        if p.sha256_file(original) != chosen["sha256"]:
            raise Blocked("ORIGINAL_CHANGED", "оригинал изменился при записи копии")
        return {"work_file": work.relative_to(self.out).as_posix(), "work_sha256": p.sha256_file(work),
                "version": version, "fields": fields, "intended": values,
                "original_values": {n: i["value"] for n, i in parsed["fields"].items()}}

    def step_reopen_verify(self) -> dict:
        p = product()
        fill = self.result_of("fill")
        chosen = self.result_of("download_and_verify")["chosen_form"]
        work = self.out / fill["work_file"]
        reopened = read_pdf(work)
        mismatches = []
        for name, intended in fill["intended"].items():
            got = (reopened["fields"].get(name) or {}).get("value")
            if got != intended:
                mismatches.append({"field": name, "reason": "VALUE_MISMATCH"})
        for name, before in fill["original_values"].items():
            if name not in fill["intended"] and (reopened["fields"].get(name) or {}).get("value") != before:
                mismatches.append({"field": name, "reason": "UNFILLED_FIELD_CHANGED"})
        if set(reopened["fields"]) != set(fill["original_values"]):
            mismatches.append({"field": "*", "reason": "FIELD_SET_CHANGED"})
        original_ok = p.sha256_file(self.out / chosen["path"]) == chosen["sha256"]
        if mismatches or not original_ok:
            raise Blocked("REOPEN_VERIFY_FAILED",
                          json.dumps({"mismatches": mismatches, "original_intact": original_ok}))
        previews = self.render_previews(work)
        prefilled = [n for n, v in fill["original_values"].items() if v and n not in fill["intended"]]
        return {"work_file": fill["work_file"], "work_sha256": fill["work_sha256"],
                "verified_fields": sorted(fill["intended"]), "pages": reopened["pages"],
                "original_intact": True, "previews": previews, "prefilled_in_original": prefilled,
                "parser": "pypdf (новое открытие файла после записи)"}

    def render_previews(self, work: Path) -> list[str]:
        try:
            import pypdfium2 as pdfium
        except ImportError:
            return []
        out = self.out / "work" / "previews"
        out.mkdir(parents=True, exist_ok=True)
        done = []
        try:
            pdf = pdfium.PdfDocument(str(work))
            with contextlib.suppress(Exception):
                pdf.init_forms()
            for i in range(len(pdf)):
                image = pdf[i].render(scale=1.2, may_draw_forms=True).to_pil()
                target = out / f"{work.stem}_p{i + 1}.png"
                image.save(target)
                done.append(target.relative_to(self.out).as_posix())
        except Exception:  # noqa: BLE001 — превью вспомогательное, отсутствие фиксируется
            return done
        return done

    def step_package(self) -> dict:
        p = product()
        rules = self.result_of("official_rules")
        timeline = self.result_of("residence_timeline")
        dl = self.result_of("download_and_verify")
        fill = self.result_of("fill")
        verify = self.result_of("reopen_verify")
        questions: list[dict] = list(timeline["questions"])
        # правило о сроке
        years = sorted({r["years"] for r in rules["rules"]})
        if not years:
            questions.append({"id": "R-RULE", "topic": "official_rule",
                              "text": "Официальное правило о сроке проживания не найдено на официальных "
                                      "страницах. Подтвердите по официальному источнику."})
        elif len(years) > 1:
            questions.append({"id": "R-RULE-CONFLICT", "topic": "official_rule",
                              "text": "Официальные страницы называют разные сроки: "
                                      + ", ".join(f"{y} лет" for y in years) + ". Какой применим?"})
        # пошлина
        fee_list = list(rules["fees"]) + [f for d in dl["documents"] for f in d.get("fees", [])]
        amounts = sorted({f["amount_czk"] for f in fee_list})
        if not amounts:
            questions.append({"id": "F-NONE", "topic": "fee",
                              "text": "Официальная сумма пошлины не найдена. Уточните по официальному источнику."})
        elif len(amounts) > 1:
            questions.append({"id": "F-CONFLICT", "topic": "fee",
                              "text": "Официальные источники называют разные суммы пошлины: "
                                      + ", ".join(f"{a} Kč" for a in amounts)
                                      + ". Bossman не выбирает сам — какая верна?"})
        if not rules["channels"]:
            questions.append({"id": "C-NONE", "topic": "channel",
                              "text": "Официальный способ подачи не найден. Куда и как подаётся заявление?"})
        if not any(d["kind"] == "instructions" for d in dl["documents"]):
            questions.append({"id": "I-NONE", "topic": "payment_instructions",
                              "text": "Официальные инструкции об оплате не скачаны."})
        for f in fill["fields"]:
            if f["status"] in ("MISSING_DATA", "UNMAPPED", "AMBIGUOUS", "NOT_TEXT_FIELD"):
                questions.append({"id": f"Q-{f['field']}", "topic": "form_field",
                                  "text": f"Поле «{f['field']}» ({f['tooltip'] or 'без подсказки'}): "
                                          + field_question_ru(f)})
        if not fill["fields"]:
            questions.append({"id": "Q-NO-FIELDS", "topic": "form_field",
                              "text": "В официальной форме нет заполняемых полей: заполнить можно только "
                                      "вручную. Bossman приложил оригинал."})
        status = "PARTIAL_MISSING_DATA" if questions else "WAIT_APPROVAL"
        pkg = self.out / "package"
        if pkg.exists():
            shutil.rmtree(pkg)
        pkg.mkdir(parents=True)
        files: list[dict] = []

        def put(src: Path, name: str, leaves: bool) -> None:
            dst = pkg / name
            dst.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(src, dst)
            files.append({"path": dst.relative_to(self.out).as_posix(), "sha256": p.sha256_file(dst),
                          "would_leave_computer": leaves})

        put(self.out / fill["work_file"], "zadost_VYPLNENO_NEPODEPSANO.pdf", True)
        put(self.out / dl["chosen_form"]["path"], "zadost_ORIGINAL.pdf", False)
        for doc in dl["documents"]:
            if doc["kind"] == "instructions":
                put(self.out / doc["path"], "pokyny/" + Path(doc["path"]).name, False)
        for rel in verify["previews"]:
            put(self.out / rel, "previews/" + Path(rel).name, False)
        owner_docs = [{"document": e["document"], "sha256": e["sha256"]}
                      for e in timeline["evidence"] if e.get("document")]
        for key, fact in (self.facts.get("person") or {}).items():
            ok, _r, proof = check_fact(fact, self.owner)
            if ok and proof.get("document"):
                owner_docs.append({"document": proof["document"], "sha256": proof["sha256"]})
        owner_docs = list({d["document"]: d for d in owner_docs}.values())
        _write_json(pkg / "questions.json", questions)
        _write_json(pkg / "residence_timeline.json", timeline)
        _write_json(pkg / "field_review.json", fill["fields"])
        _write_json(pkg / "sources.json", {"pages": rules["pages"], "rejected": rules["rejected"],
                                           "external_ignored": rules["external_ignored"],
                                           "documents": dl["documents"], "stale": dl["stale"],
                                           "rejected_downloads": dl["rejected"]})
        for name in ("questions.json", "residence_timeline.json", "field_review.json", "sources.json"):
            files.append({"path": f"package/{name}", "sha256": p.sha256_file(pkg / name),
                          "would_leave_computer": False})
        review = render_review(status=status, rules=rules, timeline=timeline, dl=dl, fill=fill,
                               verify=verify, questions=questions, amounts=amounts, fees=fee_list,
                               years=years, owner_docs=owner_docs, files=files)
        (pkg / "review.md").write_text(review, encoding="utf-8")
        files.append({"path": "package/review.md", "sha256": p.sha256_file(pkg / "review.md"),
                      "would_leave_computer": False})
        manifest = {"schema": SCHEMA, "status": status, "files": files,
                    "owner_documents_to_bring": owner_docs,
                    "recipient": [c["quote"] for c in rules["channels"]],
                    "submitted": False, "signed": False, "paid": False}
        _write_json(pkg / "manifest.json", manifest)
        manifest_sha = p.sha256_file(pkg / "manifest.json")
        return {"status": status, "manifest": manifest, "manifest_sha256": manifest_sha,
                "questions": questions, "package_dir": "package"}


def field_question_ru(f: dict) -> str:
    if f["status"] == "MISSING_DATA":
        return f"нет подтверждённого значения ({reason_ru(f.get('reason', ''))}). Укажите значение и источник."
    if f["status"] == "AMBIGUOUS":
        return "неясно, какой факт сюда относится (" + ", ".join(f["keys"]) + "). Уточните."
    if f["status"] == "NOT_TEXT_FIELD":
        return "это не текстовое поле (галочка/выбор) — заполняет владелец."
    return "Bossman не знает, что сюда писать. Укажите значение или оставьте пустым."


def render_review(*, status, rules, timeline, dl, fill, verify, questions, amounts, fees, years,
                  owner_docs, files) -> str:
    L: list[str] = []
    add = L.append
    add("# HW-10 МВД ЧР — пакет на ПЕРВИЧНОЕ ПМЖ (trvalý pobyt): проверка владельцем")
    add("")
    add(f"**Статус: {status}.** Это ПОДГОТОВЛЕННЫЙ ЧЕРНОВИК. Ничего не подано, не подписано, "
        "не оплачено, запись на приём не сделана. Заполнение онлайн-формы — не подача.")
    add("")
    add("## 1. Что нашёл на официальном сайте")
    for page in rules["pages"]:
        add(f"- {page['title'] or page['final_url']} — {page['final_url']} "
            f"(получено {page['retrieved_at']}, sha256 {page['sha256'][:12]}…)")
    for r in rules["rules"]:
        add(f"- Правило: «{r['quote']}» — {r['source']}")
    if rules["rejected"]:
        add("- Отвергнуто как неофициальное/недопустимое:")
        for r in rules["rejected"]:
            add(f"  - {r['url']} — {r['reason']}")
    for e in rules["external_ignored"]:
        add(f"  - ссылка на {e['url']} с официальной страницы проигнорирована ({e['reason']})")
    add("")
    add("## 2. Какие периоды проживания использовал и откуда")
    add("| Факт | С | По | Основание | Источник | Статус |")
    add("|---|---|---|---|---|---|")
    for e in timeline["evidence"]:
        add(f"| {e['fact']} | {e['from']} | {e['to']} | {e['basis']} | {e['source']} | {e['status']} |")
    add("")
    add(f"Расчёт на {timeline['as_of']}:")
    for line in timeline["calculation"]:
        add(f"- {line}")
    if years and timeline["chain"]:
        add(f"- Официальный срок по найденному правилу: {', '.join(str(y) for y in years)} лет. "
            f"Рассчитанная цепочка: {timeline['chain']['years']} г. {timeline['chain']['months']} мес. "
            "Это арифметика по подтверждённым датам, НЕ вывод о праве на ПМЖ.")
    add("")
    add("## 3. Что скачал")
    for d in dl["documents"]:
        add(f"- {d['kind']}: `{d['path']}` ← {d['final_url']} (sha256 {d['sha256'][:12]}…, "
            f"{d['size']} байт, тип по байтам {d['sniffed_type']}, страниц {d['pages']}, "
            f"версия {d.get('version_date') or 'не указана'})")
    for s in dl["stale"]:
        add(f"- УСТАРЕВШАЯ форма обнаружена и НЕ выбрана: {s['url']} ({s['stale_reason']})")
    for r in dl["rejected"]:
        add(f"- ОТВЕРГНУТ файл: {r['source_url']} — {r['reason']}")
    add("")
    add("## 4. Что заполнил")
    add(f"Рабочая копия `{fill['work_file']}` (версия {fill['version']}); оригинал не тронут. "
        f"После записи файл открыт заново и сверен: {len(verify['verified_fields'])} полей совпали.")
    add("")
    add("| Поле | Статус | Значение | Источник |")
    add("|---|---|---|---|")
    for f in fill["fields"]:
        add(f"| {f['field']} | {f['status']} | {f.get('value', '— (пусто)')} | {f.get('source', '')} |")
    add("")
    add("## 5. Чего не хватает")
    if questions:
        for q in questions:
            add(f"- [{q['id']}] {q['text']}")
    else:
        add("- Открытых вопросов нет. Поля подписи и даты подписи заполняет только владелец.")
    add("")
    add("## 6. Какая оплата/пошлина указана официально")
    for f in fees:
        add(f"- {f['amount_czk']} Kč — «{f['quote']}» — {f['source']}")
    if len(amounts) > 1:
        add("- **КОНФЛИКТ сумм — Bossman не выбрал ни одну.**")
    add("- Оплата Bossman'ом не выполняется и не готовится к исполнению.")
    add("")
    add("## 7. Что готово к отправке")
    add("Bossman ничего не отправляет. Если вы одобрите, пакет считается окончательным для ЛИЧНОЙ подачи:")
    for f in files:
        if f["would_leave_computer"]:
            add(f"- `{f['path']}` (sha256 {f['sha256'][:12]}…) — распечатать, подписать от руки")
    for d in owner_docs:
        add(f"- оригинал вашего документа `{d['document']}` (из вашей папки, не копировался)")
    add("")
    add("## 8. Куда именно будет отправлено")
    if rules["channels"]:
        for c in rules["channels"]:
            add(f"- «{c['quote']}» — {c['source']}")
    else:
        add("- Официальный способ подачи не найден — см. вопросы.")
    add("- Электронную отправку Bossman не выполняет; запись на приём, BankID, Datová schránka — только вы.")
    add("")
    add("## 9. Что требует моего подтверждения")
    add("- Проверить каждое заполненное поле в таблице раздела 4.")
    add("- Ответить на вопросы раздела 5.")
    add("- Подпись и дата подписи — только вы, от руки.")
    add("- Одобрение этого пакета = «пакет окончательный, подаю сам». Оно ничего не отправляет.")
    add("")
    add("## 10. Что было реально проверено, а что осталось предположением")
    add("- Проверено: домены источников по реестру; тип файлов по байтам; sha256; читаемость PDF; "
        "повторное открытие заполненной копии и совпадение значений; неизменность оригинала; "
        "папка владельца не изменена.")
    add("- Предположение/не проверено: юридическая применимость правила к вашему случаю; "
        "что найденная форма — единственная нужная; внешний вид полей при печати "
        + ("(есть превью)" if verify["previews"] else "(превью не построено)")
        + "; актуальность сайта на момент подачи.")
    add("")
    return "\n".join(L)


# --------------------------------------------------------------------------- подтверждение

def approval_preview(manifest_sha: str) -> str:
    return (f"HW-10 MVČR: пакет ПМЖ для проверки; manifest sha256={manifest_sha}; "
            "после одобрения Bossman НИЧЕГО не отправляет — владелец подаёт лично")


def approvals_db_url(path: Path) -> str:
    return "sqlite+aiosqlite:///" + Path(path).resolve().as_posix()


async def _with_approvals(db_path: Path, fn: Callable[[Any], Any]) -> Any:
    Database, EventBus, Approvals = approvals_backend()
    db_path.parent.mkdir(parents=True, exist_ok=True)
    db = Database(approvals_db_url(db_path))
    try:
        await db.create_all()
        return await fn(Approvals(db, EventBus(db)))
    finally:
        await db.engine.dispose()


async def _find(approvals: Any, approval_id: int) -> dict | None:
    for row in await approvals.list(status="all", limit=500):
        if int(row["id"]) == int(approval_id):
            return row
    return None


def register_approval(state: dict, db_path: Path, manifest_sha: str) -> dict:
    preview = approval_preview(manifest_sha)
    current = state.get("approval") or {}
    if current.get("preview") == preview and current.get("db") == str(db_path):
        return current

    async def go(approvals: Any) -> dict:
        row = await approvals.create(APPROVAL_KIND, preview)
        return {"id": int(row["id"]), "kind": APPROVAL_KIND, "preview": preview,
                "db": str(db_path), "created_at": _now()}
    record = asyncio.run(_with_approvals(db_path, go))
    state.setdefault("history", []).append({"at": _now(), "event": "approval_requested", "id": record["id"]})
    return record


def sync_approval(state: dict, db_path: Path, decide: str | None) -> tuple[str, str]:
    """Прочитать/применить решение владельца в очереди продукта. Никаких внешних действий."""
    record = state.get("approval") or {}

    async def go(approvals: Any) -> tuple[str, str]:
        aid = int(record["id"])
        if decide:
            before = await _find(approvals, aid)
            if before is None:
                return "REFUSED", "APPROVAL_NOT_FOUND"
            if before["status"] != "pending":
                return "REFUSED", "ALREADY_DECIDED:" + before["status"]
            await approvals.decide(aid, decide == "approve", by="owner-cli")
        row = await _find(approvals, aid)
        if row is None:
            return "REFUSED", "APPROVAL_NOT_FOUND"
        if row["status"] == "pending":
            return "WAIT_APPROVAL", ""
        if row["status"] in ("rejected", "revoked", "expired"):
            return "DENIED", row["status"]
        if row["status"] == "approved":
            ok = await approvals.consume(aid, kind=record["kind"], preview=record["preview"])
            return ("APPROVED_FOR_MANUAL_DELIVERY", "") if ok else ("REFUSED", "CONSUME_FAILED")
        if row["status"] == "consumed":
            return ("APPROVED_FOR_MANUAL_DELIVERY", "REPLAY_NO_EFFECT")
        return "REFUSED", "UNKNOWN_APPROVAL_STATUS"
    return asyncio.run(_with_approvals(db_path, go))


# --------------------------------------------------------------------------- CLI

def build_parser() -> argparse.ArgumentParser:
    ap = argparse.ArgumentParser(
        prog="mvcr_prepare",
        description="HW-10: подготовка пакета ПМЖ (trvalý pobyt) до WAIT_APPROVAL. "
                    "Ничего не подаёт, не подписывает, не оплачивает.",
        epilog="Коды выхода: 0 WAIT_APPROVAL/APPROVED_FOR_MANUAL_DELIVERY/DENIED, "
               "10 PARTIAL_MISSING_DATA, 20 BLOCKED, 21 REFUSED, 2 ошибка аргументов.")
    ap.add_argument("--owner-folder", help="папка с документами владельца (только чтение)")
    ap.add_argument("--facts", required=True, help="JSON с подтверждёнными фактами (schema bossman.mvcr.facts.v1)")
    ap.add_argument("--out", required=True, help="папка проекта/пакета (вне папки владельца)")
    ap.add_argument("--offline-fixtures", help="синтетический сайт для CI (sources.json + site/)")
    ap.add_argument("--entry-url", action="append", help="доп. официальная стартовая страница (живой режим)")
    ap.add_argument("--field-map", help="JSON {имя поля PDF: ключ факта|OWNER_ONLY}")
    ap.add_argument("--as-of", help="дата расчёта ГГГГ-ММ-ДД (по умолчанию из фактов или сегодня)")
    ap.add_argument("--approvals-db", help="SQLite очереди подтверждений Command Center "
                                           "(по умолчанию <out>/approval/approvals.sqlite)")
    ap.add_argument("--decide", choices=("approve", "deny"), help="решение владельца по пакету в WAIT_APPROVAL")
    ap.add_argument("--json", action="store_true", help="машиночитаемый итог в stdout")
    return ap


def run_pipeline(args: argparse.Namespace) -> dict:
    out = Path(args.out).resolve()
    fixtures = Path(args.offline_fixtures).resolve() if args.offline_fixtures else None
    stack = contextlib.ExitStack()
    base = None
    server = None
    if fixtures:
        server, base = stack.enter_context(fixture_site(fixtures / "site"))
    fetcher = Fetcher(base)
    pipe = Pipeline(args, fetcher, fixtures)
    summary: dict = {"schema": SCHEMA, "out": str(out)}
    snapshot = None
    with stack:
        try:
            if pipe.owner is not None and pipe.owner.is_dir():
                snapshot = product().scan_scope(pipe.owner)
            if args.decide:
                pipe.writable = pipe.state_path.is_file()
                status, reason = decide_only(pipe, args)
            else:
                result = pipe.run()
                status, reason = result["status"], ""
                if status == "WAIT_APPROVAL":
                    db = Path(args.approvals_db) if args.approvals_db else out / "approval" / "approvals.sqlite"
                    pipe.state["approval"] = register_approval(pipe.state, db, result["manifest_sha256"])
                    if pipe.state.get("final_status") in ("APPROVED_FOR_MANUAL_DELIVERY", "DENIED") \
                            and pipe.state.get("decided_manifest") == result["manifest_sha256"]:
                        status = pipe.state["final_status"]
                    else:
                        status, reason = sync_approval(pipe.state, db, None)
        except Blocked as exc:
            status, reason = "BLOCKED", exc.reason
            summary["detail"] = exc.detail
        if snapshot is not None:
            changed = product().unexpected_mutations(snapshot, product().scan_scope(pipe.owner), ())
            if changed:
                status, reason = "BLOCKED", "OWNER_FOLDER_MUTATED"
        if status != "REFUSED":
            pipe.state["final_status"] = status
        pipe.state.setdefault("history", []).append({"at": _now(), "event": "run", "status": status,
                                                     "reason": reason})
        if out.exists() or status != "BLOCKED":
            pipe.save()
        pipe.log("run_finished", status=status, reason=reason, network_requests=len(fetcher.requests))
        forbidden = list(getattr(server, "forbidden_methods", []) or [])
    package = (pipe.state.get("steps", {}).get("package") or {}).get("result") or {}
    summary.update({
        "status": status, "exit_code": EXIT_CODES[status], "reason": reason,
        "package_dir": str(out / "package") if package else None,
        "review": str(out / "package" / "review.md") if package else None,
        "questions": [{"id": q["id"], "topic": q["topic"]} for q in package.get("questions", [])],
        "approval_id": (pipe.state.get("approval") or {}).get("id"),
        "network_requests": fetcher.requests,
        "forbidden_methods_seen_by_fixture_site": forbidden,
        "submitted": False,
    })
    return summary


def decide_only(pipe: Pipeline, args: argparse.Namespace) -> tuple[str, str]:
    """Решение владельца. Проверяет, что пакет не изменился с момента показа."""
    state = pipe.state
    record = state.get("approval")
    if not record:
        return "REFUSED", "NO_PENDING_APPROVAL"
    package = (state.get("steps", {}).get("package") or {}).get("result") or {}
    manifest = pipe.out / "package" / "manifest.json"
    if not manifest.is_file() or product().sha256_file(manifest) != package.get("manifest_sha256") \
            or approval_preview(package.get("manifest_sha256", "")) != record.get("preview") \
            or not pipe.artifacts_intact("package", package):
        return "REFUSED", "PACKAGE_CHANGED_SINCE_REVIEW"
    db = Path(record["db"])
    status, reason = sync_approval(state, db, args.decide)
    if status in ("APPROVED_FOR_MANUAL_DELIVERY", "DENIED"):
        state["decided_manifest"] = package["manifest_sha256"]
        _write_json(pipe.out / "approval" / "decision.json", {
            "status": status, "at": _now(), "approval_id": record["id"],
            "manifest_sha256": package["manifest_sha256"], "sent": False,
            "next_action": ("Владелец лично подаёт распечатанный и подписанный пакет по официальному "
                            "каналу из review.md; запись на приём — сам владелец."
                            if status == "APPROVED_FOR_MANUAL_DELIVERY" else
                            "Отказ записан. Ничего не отправлено; черновик сохранён.")})
    return status, reason


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    summary = run_pipeline(args)
    if args.json:
        print(json.dumps({k: v for k, v in summary.items() if k != "network_requests"}
                         | {"network_requests": len(summary["network_requests"])},
                         ensure_ascii=False, indent=2))
    else:
        print(f"HW-10 MVČR: {summary['status']}"
              + (f" ({summary['reason']})" if summary["reason"] else ""))
        if summary.get("review"):
            print(f"Пакет для проверки: {summary['review']}")
        if summary["questions"]:
            print(f"Открытых вопросов: {len(summary['questions'])} (см. review.md, раздел 5)")
        print("Ничего не подано, не подписано и не оплачено.")
    return summary["exit_code"]


if __name__ == "__main__":
    raise SystemExit(main())
