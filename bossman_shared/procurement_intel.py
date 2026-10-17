"""Bosman Terminal, ядро закупочной разведки: публичные тендеры и награждённые контракты.

Источники (без ключей, проверены 17.10.2026):
  * TED (ЕС): тендеры и уведомления, поиск https://api.ted.europa.eu/v3/notices/search
  * USAspending (США): награждённые контракты — кто побеждает и на какие суммы (карта поставщиков и конкурентов)

Что модуль НЕ делает: не обходит лицензирование. Военные и двойные товары (CPV 35*, амуниция, оружие) получают
флаг `licence_check`: экспорт и посредничество требуют разрешений (ITAR/EAR, ЕС Common Position 2008/944/CFSP,
национальные реестры), а сертификаты конечного пользователя подлинные. Скоринг «реализуемо» — подсказка для
человека, а не решение.

Сеть вынесена в параметр `post`, поэтому разбор и скоринг тестируются без интернета.
"""
from __future__ import annotations

import json
import subprocess
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal, InvalidOperation
from typing import Callable

TED_URL = "https://api.ted.europa.eu/v3/notices/search"
USA_URL = "https://api.usaspending.gov/api/v2/search/spending_by_award/"
# CPV 353 оружие и боеприпасы, 354 военные корабли/машины, 355-357 военные системы; 351/352/358 (пожарные, полиция, форма) гражданские
MILITARY_CPV_PREFIXES = ("353", "354", "355", "356", "357")
MILITARY_WORDS = ("ammunition", "weapon", "munition", "explosive", "missile", "armament", "боеприпас", "оружи")


@dataclass(slots=True)
class Opportunity:
    source: str                     # "ted" | "usaspending"
    kind: str                       # "tender" (можно подать) | "award" (уже выдан: разведка поставщиков)
    id: str
    title: str
    buyer: str
    country: str
    value: Decimal | None
    currency: str
    published: str
    deadline: str
    cpv: list[str] = field(default_factory=list)
    url: str = ""


def _dec(raw) -> Decimal | None:
    try:
        v = Decimal(str(raw))
        return v if v.is_finite() else None
    except (InvalidOperation, ValueError):
        return None


def _first(d: dict, key: str) -> str:
    v = d.get(key)
    if isinstance(v, dict):                       # многоязычные поля TED: {"eng": [...], "por": [...]}
        for lang in ("eng", *v):                  # английский, иначе первый непустой
            vals = v.get(lang)
            if vals:
                return str(vals[0] if isinstance(vals, list) else vals)
        return ""
    if isinstance(v, list):
        return str(v[0]) if v else ""
    return str(v or "")


def parse_ted(payload: dict) -> list[Opportunity]:
    out = []
    for n in payload.get("notices", []):
        vals = [x for x in (_dec(v) for v in (n.get("estimated-value-lot") or [])) if x is not None]
        pdf = (n.get("links", {}).get("pdf", {}) or {})
        url = pdf.get("ENG") or next(iter(pdf.values()), "") or n.get("links", {}).get("xml", {}).get("MUL", "")
        out.append(Opportunity(
            source="ted", kind="tender", id=str(n.get("publication-number", "")),
            title=_first(n, "notice-title"), buyer=_first(n, "buyer-name"),
            country=_first(n, "place-of-performance-country-lot"),
            value=sum(vals, Decimal(0)) if vals else None,
            currency=_first(n, "estimated-value-cur-lot") or "EUR",
            published=_first(n, "publication-date")[:10], deadline=_first(n, "deadline-receipt-tender-date-lot")[:10],
            cpv=sorted({str(c) for c in (n.get("classification-cpv") or [])}), url=url))
    return out


def parse_usaspending(payload: dict) -> list[Opportunity]:
    out = []
    for r in payload.get("results", []):
        slug = r.get("generated_internal_id", "")
        desc = str(r.get("Description") or "")
        title = f"{r.get('Recipient Name', '')}: {desc}".strip(": ")
        out.append(Opportunity(
            source="usaspending", kind="award", id=str(r.get("Award ID", "")),
            title=title, buyer=str(r.get("Awarding Agency", "")),
            country="USA", value=_dec(r.get("Award Amount")), currency="USD", published="", deadline="",
            url=f"https://www.usaspending.gov/award/{slug}" if slug else ""))
    return out


def score(op: Opportunity, *, max_value: Decimal = Decimal("250000"), cpv_prefixes: tuple[str, ...] = (),
          today: date | None = None) -> dict:
    """Оценка 0..100 «можем ли реально выполнить» + причины + флаги. Подсказка человеку, не решение."""
    today = today or datetime.now(timezone.utc).date()
    pts, why, flags = 50, [], []
    text = f"{op.title} {op.buyer}".lower()
    military = any(c.startswith(MILITARY_CPV_PREFIXES) for c in op.cpv) or any(w in text for w in MILITARY_WORDS)
    if military:
        flags.append("licence_check")
        why.append("военный/двойной товар: нужны лицензии на экспорт/посредничество и законный конечный пользователь")
    if op.kind == "award":
        flags.append("intel_only")
        why.append("контракт уже выдан: использовать для карты победителей и поставщиков, подать нельзя")
        return dict(score=0, reasons=why, flags=flags)
    if op.value is None:
        pts -= 10
        why.append("сумма не указана")
    elif op.value <= max_value:
        pts += 20
        why.append(f"сумма {op.value} {op.currency} в пределах лимита {max_value}")
    else:
        pts -= 25
        flags.append("value_over_cap")
        why.append(f"сумма {op.value} {op.currency} выше лимита {max_value}: нужна репутация и оборотный капитал")
    if op.deadline:
        try:
            left = (date.fromisoformat(op.deadline) - today).days
            if left < 0:
                return dict(score=0, reasons=why + ["срок подачи прошёл"], flags=flags + ["expired"])
            pts += 15 if left >= 14 else 0 if left >= 5 else -15
            why.append(f"до срока {left} дн.")
        except ValueError:
            pass
    if cpv_prefixes:
        hit = any(c.startswith(cpv_prefixes) for c in op.cpv)
        pts += 15 if hit else -20
        why.append("CPV в зоне интереса" if hit else "CPV вне зоны интереса")
    return dict(score=max(0, min(100, pts)), reasons=why, flags=flags)


def _curl_json(url: str, body: dict) -> dict:
    """POST JSON через curl: у Python на этой машине нет корня сертификатов USAspending, у curl он есть (schannel)."""
    p = subprocess.run(["curl", "-s", "-m", "60", "-X", "POST", url, "-H", "Content-Type: application/json",
                        "-H", "User-Agent: bosman-terminal", "-d", json.dumps(body)],
                       capture_output=True, timeout=90)
    if p.returncode != 0:
        raise RuntimeError(f"curl code {p.returncode}")
    return json.loads(p.stdout.decode("utf-8", "replace"))


def fetch_ted(cpv_prefix: str, days: int, limit: int = 50, post: Callable[[str, dict], dict] = _curl_json):
    since = (date.today() - timedelta(days=days)).strftime("%Y%m%d")
    body = {"query": f"classification-cpv={cpv_prefix}* AND publication-date>={since}", "limit": limit,
            "fields": ["publication-number", "notice-title", "buyer-name", "publication-date",
                       "deadline-receipt-tender-date-lot", "estimated-value-lot", "estimated-value-cur-lot",
                       "classification-cpv", "place-of-performance-country-lot"]}
    return parse_ted(post(TED_URL, body))


def fetch_awards(keyword: str, days: int, limit: int = 25, post: Callable[[str, dict], dict] = _curl_json):
    start = (date.today() - timedelta(days=days)).isoformat()
    body = {"filters": {"award_type_codes": ["A", "B", "C", "D"], "keywords": [keyword],
                        "time_period": [{"start_date": start, "end_date": date.today().isoformat()}]},
            "fields": ["Award ID", "Recipient Name", "Award Amount", "Awarding Agency", "Description"],
            "sort": "Award Amount", "order": "desc", "limit": limit}
    return parse_usaspending(post(USA_URL, body))


def main(argv: list[str] | None = None) -> int:
    import argparse
    ap = argparse.ArgumentParser(description="Bosman Terminal: тендеры TED и контракты USAspending")
    ap.add_argument("--cpv", default="35", help="префикс CPV для TED (35 = безопасность/оборона, 15 = еда, 09 = топливо)")
    ap.add_argument("--award-keyword", default="", help="ключевое слово для награждённых контрактов USAspending")
    ap.add_argument("--days", type=int, default=14)
    ap.add_argument("--max-value", default="250000")
    args = ap.parse_args(argv)
    ops = fetch_ted(args.cpv, args.days)
    if args.award_keyword:
        ops += fetch_awards(args.award_keyword, args.days)
    rows = sorted(((score(o, max_value=Decimal(args.max_value), cpv_prefixes=(args.cpv,)), o) for o in ops),
                  key=lambda t: -t[0]["score"])
    for s, o in rows:
        print(f"[{s['score']:>3}] {o.source}/{o.kind} {o.id} | {o.buyer[:40]} | {o.value} {o.currency} | до {o.deadline or '-'} | "
              f"{','.join(s['flags']) or '-'} | {o.title[:70]} | {o.url}")
    print(f"всего: {len(rows)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
