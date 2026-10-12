"""Daily report (markdown) and DRAFT notifications. Every number is computed from the table rows and carries the
source ids it was computed from (appendix), so a reader can check any figure against the export.

Nothing here sends anything: ``render_notifications`` only produces text for a human to review.
"""
from __future__ import annotations

import collections
import datetime as dt
from decimal import Decimal
from typing import Optional

from . import SCHEMA, __version__

_LABELS = {
    "ru": {
        "title": "Ежедневный отчёт по заявкам - Bossman Operations",
        "banner": "Пилот: ничего не отправлено, CRM не затронута. Каждое число ниже можно проверить по исходным id "
                  "(приложение A).",
        "input": "Вход", "period": "Период (UTC)", "as_of": "Состояние на (UTC)", "window": "Окно дублей, мин",
        "summary": "Сводка", "metric": "Показатель", "count": "Кол-во", "trace": "Id",
        "m_input": "Элементов на входе (сообщения/строки)", "m_unparsed": "Не удалось разобрать",
        "m_skipped": "Не заявки (пропущены, перечислены)", "m_found": "Заявок найдено (до удаления дублей)",
        "m_new": "Новые заявки (уникальные)", "m_dups": "Дублей объединено (привязаны, не удалены)",
        "m_review": "Требуют ручной проверки", "m_possible": "Возможные дубли (не объединены)",
        "check": "Сверка", "services": "Услуги (по уникальным заявкам)", "service": "Услуга", "requests": "Заявок",
        "rows": "Строки", "svc_ambiguous": "(неоднозначно)", "svc_missing": "(не указана)",
        "budget": "Бюджеты по валютам", "currency": "Валюта", "with_amount": "Заявок с суммой", "sum": "Сумма",
        "min": "Мин", "max": "Макс",
        "budget_note": "Валюты не суммируются и не пересчитываются. Дубли в суммы не входят.",
        "budget_excluded": "Не вошли в суммы: сумма неоднозначна / несколько сумм / нет валюты",
        "budget_none": "Сумма не указана", "sla": "SLA: неотвеченные", "answered_yes": "Отвечено",
        "answered_no": "Без ответа", "answered_unknown": "Статус неизвестен", "oldest": "Самая старая без ответа",
        "age_h": "возраст, ч", "breach": "Старше порога SLA", "threshold_h": "порог, ч",
        "sla_basis_tg": "Ответ = сообщение другого отправителя (или --staff) с reply-ссылкой на заявку. Ответы без "
                        "reply-ссылки в экспорте не видны - число 'без ответа' это верхняя оценка.",
        "sla_none": "Нет заявок со статусом 'без ответа'.",
        "per_day": "По дням", "day": "День", "new": "Новых", "dups": "Дублей",
        "review": "Очередь ручной проверки", "reasons": "Причины", "none": "нет",
        "possible": "Возможные дубли", "other": "Похожа на",
        "quality": "Качество разбора", "skipped_by": "Пропущено по причинам", "unparsed_list": "Не разобрано",
        "warnings": "Предупреждения",
        "appendix": "Приложение A - исходные id по каждому числу",
        "svc_msgs": "Служебные сообщения Telegram (вступления, закрепы...)", "blank_rows": "Пустые строки",
        "w_staff": "{n} сообщений являются ответами на чужие сообщения, а --staff не задан: ответы сотрудников "
                   "могут быть прочитаны как заявки. Передайте --staff <from_id>.",
        "w_nots": "{n} заявок без метки времени: окно дублей для них не проверено (объединение только по "
                  "телефону/e-mail/@нику).",
        "w_unknown": "Статус ответа неизвестен для {n} заявок (в данных нет reply-ссылок или колонки статуса).",
        "w_html": "messages.html: {n} блок(ов) сообщений без id/времени не разобраны.",
    },
    "en": {
        "title": "Daily intake report - Bossman Operations",
        "banner": "Pilot: nothing was sent, no CRM was touched. Every number below can be checked against the source "
                  "ids (Appendix A).",
        "input": "Input", "period": "Period covered (UTC)", "as_of": "As of (UTC)", "window": "Duplicate window, min",
        "summary": "Summary", "metric": "Metric", "count": "Count", "trace": "Ids",
        "m_input": "Input items (messages / rows)", "m_unparsed": "Could not be parsed",
        "m_skipped": "Not requests (skipped, listed)", "m_found": "Requests found (before de-duplication)",
        "m_new": "New requests (unique)", "m_dups": "Duplicates merged (linked, not deleted)",
        "m_review": "Need manual review", "m_possible": "Possible duplicates (not merged)",
        "check": "Reconciliation", "services": "Services (unique requests)", "service": "Service",
        "requests": "Requests", "rows": "Rows", "svc_ambiguous": "(ambiguous)", "svc_missing": "(not stated)",
        "budget": "Budgets by currency", "currency": "Currency", "with_amount": "Requests with amount",
        "sum": "Sum", "min": "Min", "max": "Max",
        "budget_note": "Currencies are never summed or converted. Duplicates are not counted in the sums.",
        "budget_excluded": "Not in the sums: ambiguous amount / several amounts / no currency",
        "budget_none": "No amount stated", "sla": "SLA: unanswered", "answered_yes": "Answered",
        "answered_no": "Unanswered", "answered_unknown": "Status unknown", "oldest": "Oldest unanswered",
        "age_h": "age, h", "breach": "Older than the SLA threshold", "threshold_h": "threshold, h",
        "sla_basis_tg": "Answered = a message from another sender (or --staff) linked to the request with a Telegram "
                        "reply. Replies without a reply link are invisible - 'unanswered' is an upper bound.",
        "sla_none": "No request has the status 'unanswered'.",
        "per_day": "By day", "day": "Day", "new": "New", "dups": "Duplicates",
        "review": "Manual review queue", "reasons": "Reasons", "none": "none",
        "possible": "Possible duplicates", "other": "Resembles",
        "quality": "Parse quality", "skipped_by": "Skipped by reason", "unparsed_list": "Not parsed",
        "warnings": "Warnings",
        "appendix": "Appendix A - source ids behind every number",
        "svc_msgs": "Telegram service messages (joins, pins, ...)", "blank_rows": "Blank rows",
        "w_staff": "{n} messages are replies to other messages and --staff was not given: staff answers may be read "
                   "as requests. Pass --staff <from_id>.",
        "w_nots": "{n} requests have no timestamp: the duplicate window was not verified for them (merged on "
                  "phone/e-mail/@handle only).",
        "w_unknown": "Answer status is unknown for {n} requests (no reply links / status column in the input).",
        "w_html": "messages.html: {n} message block(s) without id/timestamp were not parsed.",
    },
}


#: customer-facing names of the default taxonomy services (unknown / custom names are used as written)
SERVICE_TITLES = {
    "ru": {"video_editing": "видеомонтаж", "face_swap": "замена лица в видео", "voice_dubbing": "озвучка / дубляж",
           "design": "дизайн", "website": "сайт", "bot_dev": "чат-бот", "translation": "перевод",
           "bookkeeping": "бухгалтерия", "photo_retouch": "ретушь фото", "copywriting": "копирайтинг",
           "consulting": "консультация"},
    "en": {"video_editing": "video editing", "face_swap": "face swap", "voice_dubbing": "voice-over / dubbing",
           "design": "design", "website": "website", "bot_dev": "chat bot", "translation": "translation",
           "bookkeeping": "bookkeeping", "photo_retouch": "photo retouching", "copywriting": "copywriting",
           "consulting": "consulting"},
}
REASON_GLOSS = {
    "ru": {"phone_ambiguous": "телефон неоднозначен (нет кода страны / неверная длина)",
           "phone_multiple": "несколько телефонов - какой основной?",
           "email_invalid": "e-mail не похож на адрес", "amount_ambiguous": "сумма неоднозначна (5.000 = 5000 или 5?)",
           "amount_multiple": "несколько сумм в одном сообщении", "amount_currency_missing": "сумма без валюты",
           "service_ambiguous": "несколько услуг в одном сообщении", "service_missing": "услуга не названа",
           "deadline_ambiguous": "срок неоднозначен", "possible_duplicate": "возможный дубль, не объединён",
           "empty_request": "нет содержания заявки", "contact_missing": "нет контакта",
           "name_missing": "нет имени", "phone_missing": "нет телефона", "email_missing": "нет e-mail",
           "amount_missing": "нет суммы", "deadline_missing": "нет срока"},
    "en": {"phone_ambiguous": "phone is ambiguous (no country code / wrong length)",
           "phone_multiple": "several phones - which one is primary?",
           "email_invalid": "e-mail does not look like an address",
           "amount_ambiguous": "amount is ambiguous (5.000 = 5000 or 5?)",
           "amount_multiple": "several amounts in one message", "amount_currency_missing": "amount without currency",
           "service_ambiguous": "several services in one message", "service_missing": "service not stated",
           "deadline_ambiguous": "deadline is ambiguous", "possible_duplicate": "possible duplicate, not merged",
           "empty_request": "no request content", "contact_missing": "no contact",
           "name_missing": "no name", "phone_missing": "no phone", "email_missing": "no e-mail",
           "amount_missing": "no amount", "deadline_missing": "no deadline"},
}


def _gloss(reasons: str, lang: str) -> str:
    g = REASON_GLOSS[lang]
    return "; ".join(f"{r} - {g[r]}" if r in g else r for r in (x.strip() for x in reasons.split(";")) if r)


def _md(s) -> str:
    return str(s if s is not None else "").replace("|", "\\|").replace("\n", " ").replace("`", "'")


def _ids(xs, limit: Optional[int] = None) -> str:
    xs = list(xs)
    if not xs:
        return "-"
    if limit and len(xs) > limit:
        return ", ".join(xs[:limit]) + f", ... (+{len(xs) - limit})"
    return ", ".join(xs)


def _num(d: Decimal) -> str:
    return format(d.normalize(), "f") if d == d.to_integral() else str(d.normalize())


# ----------------------------------------------------------------------------- summary
def summarize(*, load, extracted: list, result, rows: list, params: dict) -> dict:
    requests = [e for e in extracted if e.is_request]
    skipped = [e for e in extracted if not e.is_request]
    unparsed_n = sum(int(u.get("count", 1)) for u in load.unparsed)
    unique_ids = [c.first_src_id for c in result.clusters]
    dup_ids = [l.dup.src_id for l in result.links]
    review_rows = [r for r in rows if r["needs_review"] == "yes"]
    possible_rows = [r for r in rows if r["possible_duplicate_of"]]
    skipped_by: dict = collections.OrderedDict()
    for e in skipped:
        skipped_by.setdefault(e.skip_reason, []).append(e.src_id)
    stamps = [e.ts_utc for e in extracted if e.ts_utc]
    as_of = params.get("as_of") or (max(stamps) if stamps else None)
    tz = dt.timedelta(minutes=params.get("tz_offset_min", 0))

    counts = {
        "input_items": load.total_items, "unparsed": unparsed_n, "skipped": len(skipped),
        "requests_found": len(requests), "unique_requests": len(rows), "duplicates_merged": len(dup_ids),
        "needs_review": len(review_rows), "possible_duplicates": len(possible_rows),
        "service_messages_ignored": int(load.ignored.get("service_messages", 0)),
    }
    ids = {
        "input_items": [e.src_id for e in extracted] + [u["ref"] for u in load.unparsed],
        "unparsed": [u["ref"] for u in load.unparsed], "skipped": [e.src_id for e in skipped],
        "requests_found": [e.src_id for e in requests], "unique_requests": unique_ids,
        "duplicates_merged": dup_ids, "needs_review": [r["first_src_id"] for r in review_rows],
        "possible_duplicates": [r["first_src_id"] for r in possible_rows],
    }
    reconcile = {
        "input_items == unparsed + skipped + requests_found":
            counts["input_items"] == counts["unparsed"] + counts["skipped"] + counts["requests_found"],
        "requests_found == unique_requests + duplicates_merged":
            counts["requests_found"] == counts["unique_requests"] + counts["duplicates_merged"],
    }

    # services
    svc: dict = collections.OrderedDict()
    for r in rows:
        key = r["service"] or ("(ambiguous)" if r["service_candidates"] else "(not stated)")
        svc.setdefault(key, []).append(r)
    top_services = sorted(({"service": k, "requests": len(v), "row_ids": [r["row_id"] for r in v],
                            "src_ids": [r["first_src_id"] for r in v]} for k, v in svc.items()),
                          key=lambda s: (-s["requests"], s["service"]))

    # budgets, never summed across currencies
    by_ccy: dict = {}
    excluded, none_stated = [], []
    for r in rows:
        if r["amount"] and r["currency"]:
            by_ccy.setdefault(r["currency"], []).append(r)
        elif r["amount"] or r["amount_raw"] or r["amount_candidates"]:
            excluded.append(r)
        else:
            none_stated.append(r)
    budgets = {}
    for ccy, rs in sorted(by_ccy.items()):
        vals = [Decimal(r["amount"]) for r in rs]
        budgets[ccy] = {"requests": len(rs), "sum": _num(sum(vals, Decimal(0))), "min": _num(min(vals)),
                        "max": _num(max(vals)), "row_ids": [r["row_id"] for r in rs],
                        "src_ids": [r["first_src_id"] for r in rs]}
    budget_excluded = {"count": len(excluded), "row_ids": [r["row_id"] for r in excluded],
                       "src_ids": [r["first_src_id"] for r in excluded]}
    budget_none = {"count": len(none_stated), "src_ids": [r["first_src_id"] for r in none_stated]}

    # SLA
    def age_h(r) -> Optional[float]:
        if not (as_of and r["ts_utc"]):
            return None
        return round((as_of - dt.datetime.strptime(r["ts_utc"], "%Y-%m-%d %H:%M:%S")).total_seconds() / 3600, 2)

    unanswered = [r for r in rows if r["answered"] == "no" and r["ts_utc"]]
    unanswered.sort(key=lambda r: (r["ts_utc"], r["row_id"]))
    sla_h = float(params.get("sla_hours", 24))
    sla = {
        "threshold_h": sla_h, "answered": [r["first_src_id"] for r in rows if r["answered"] == "yes"],
        "unanswered": [r["first_src_id"] for r in rows if r["answered"] == "no"],
        "unknown": [r["first_src_id"] for r in rows if r["answered"] == "unknown"],
        "oldest": [{"row_id": r["row_id"], "src_id": r["first_src_id"], "ts_utc": r["ts_utc"], "age_h": age_h(r)}
                   for r in unanswered[:5]],
        "breaching": [r["first_src_id"] for r in unanswered if (age_h(r) or 0) > sla_h],
        "basis": sorted({r["answered_basis"].split(":")[0] for r in rows if r["answered_basis"]}),
    }

    # per day
    per_day: dict = collections.OrderedDict()
    for r in sorted(rows, key=lambda r: (r["ts_utc"] == "", r["ts_utc"])):
        day = (dt.datetime.strptime(r["ts_utc"], "%Y-%m-%d %H:%M:%S") + tz).date().isoformat() if r["ts_utc"] \
            else "(no timestamp)"
        d = per_day.setdefault(day, {"new": [], "duplicates": []})
        d["new"].append(r["first_src_id"])
    for l in result.links:
        day = (l.dup.ts_utc + tz).date().isoformat() if l.dup.ts_utc else "(no timestamp)"
        per_day.setdefault(day, {"new": [], "duplicates": []})["duplicates"].append(l.dup.src_id)

    warnings = []
    if load.source_type == "telegram_json" and not load.staff_given and load.reply_index:
        n = sum(len(v) for v in load.reply_index.values())
        warnings.append({"code": "staff_not_given", "n": n})
    no_ts = [r["first_src_id"] for r in rows if not r["ts_utc"]]
    if no_ts:
        warnings.append({"code": "no_timestamp", "n": len(no_ts)})
    if sla["unknown"]:
        warnings.append({"code": "answer_unknown", "n": len(sla["unknown"])})
    if load.meta.get("html_blocks_unparsed"):
        warnings.append({"code": "html_blocks", "n": load.meta["html_blocks_unparsed"]})

    return {
        "counts": counts, "ids": ids, "reconciliation": reconcile, "skipped_by_reason": skipped_by,
        "top_services": top_services, "budgets": budgets, "budget_excluded": budget_excluded,
        "budget_none": budget_none, "sla": sla, "per_day": per_day, "warnings": warnings,
        "as_of": as_of.strftime("%Y-%m-%d %H:%M:%S") if as_of else None,
        "period": [min(stamps).strftime("%Y-%m-%d %H:%M:%S"), max(stamps).strftime("%Y-%m-%d %H:%M:%S")]
        if stamps else None,
    }


# ----------------------------------------------------------------------------- daily report
def render_report(summary: dict, rows: list, load, params: dict, lang: str = "ru") -> str:
    t = _LABELS[lang]
    c, ids = summary["counts"], summary["ids"]
    out: list = []
    w = out.append
    w(f"# {t['title']}")
    w("")
    w(f"> {t['banner']}")
    w("")
    sha = params.get("input_sha256", "")
    w(f"- {t['input']}: `{_md(params.get('input_name', ''))}` - sha256 `{sha[:16]}...` - {load.source_type}")
    if summary["period"]:
        w(f"- {t['period']}: {summary['period'][0]} - {summary['period'][1]}")
    w(f"- {t['as_of']}: {summary['as_of'] or '-'}; {t['window']}: {params.get('window_min')}")
    w(f"- schema `{SCHEMA}` v{__version__}")
    w("")
    w(f"## {t['summary']}")
    w("")
    w(f"| {t['metric']} | {t['count']} | {t['trace']} |")
    w("|---|---:|---|")
    metrics = [("m_input", "input_items", "A1"), ("m_unparsed", "unparsed", "A2"), ("m_skipped", "skipped", "A3"),
               ("m_found", "requests_found", "A4"), ("m_new", "unique_requests", "A5"),
               ("m_dups", "duplicates_merged", "A6"), ("m_review", "needs_review", "A7"),
               ("m_possible", "possible_duplicates", "A8")]
    for label, key, ref in metrics:
        w(f"| {t[label]} | **{c[key]}** | [{ref}] |")
    w("")
    w(f"{t['check']}: A1 = A2 + A3 + A4 -> {c['input_items']} = {c['unparsed']} + {c['skipped']} + "
      f"{c['requests_found']}; A4 = A5 + A6 -> {c['requests_found']} = {c['unique_requests']} + "
      f"{c['duplicates_merged']}. " + ("OK" if all(summary["reconciliation"].values()) else "MISMATCH"))
    w("")
    # services
    w(f"## {t['services']}")
    w("")
    w(f"| {t['service']} | {t['requests']} | {t['rows']} |")
    w("|---|---:|---|")
    for s in summary["top_services"]:
        name = s["service"]
        name = t["svc_ambiguous"] if name == "(ambiguous)" else t["svc_missing"] if name == "(not stated)" else name
        w(f"| {_md(name)} | {s['requests']} | {_ids(s['row_ids'], 12)} |")
    w("")
    # budgets
    w(f"## {t['budget']}")
    w("")
    if summary["budgets"]:
        w(f"| {t['currency']} | {t['with_amount']} | {t['sum']} | {t['min']} | {t['max']} | {t['rows']} |")
        w("|---|---:|---:|---:|---:|---|")
        for ccy, b in summary["budgets"].items():
            w(f"| {ccy} | {b['requests']} | {b['sum']} | {b['min']} | {b['max']} | {_ids(b['row_ids'], 10)} |")
        w("")
    w(f"{t['budget_note']}")
    w("")
    be, bn = summary["budget_excluded"], summary["budget_none"]
    w(f"- {t['budget_excluded']}: **{be['count']}** ({_ids(be['row_ids'], 12)})")
    w(f"- {t['budget_none']}: **{bn['count']}**")
    w("")
    # SLA
    sla = summary["sla"]
    w(f"## {t['sla']}")
    w("")
    w(f"- {t['answered_yes']}: **{len(sla['answered'])}**; {t['answered_no']}: **{len(sla['unanswered'])}**; "
      f"{t['answered_unknown']}: **{len(sla['unknown'])}** [A9]")
    if sla["oldest"]:
        w(f"- {t['oldest']}:")
        for o in sla["oldest"]:
            w(f"  - {o['row_id']} / {o['src_id']} - {o['ts_utc']} UTC - {t['age_h']}: {o['age_h']}")
        w(f"- {t['breach']} ({t['threshold_h']}: {_num(Decimal(str(sla['threshold_h'])))}): "
          f"**{len(sla['breaching'])}** [A10]")
    else:
        w(f"- {t['sla_none']}")
    if load.source_type.startswith("telegram"):
        w(f"- {t['sla_basis_tg']}")
    w("")
    # per day
    w(f"## {t['per_day']}")
    w("")
    w(f"| {t['day']} | {t['new']} | {t['dups']} |")
    w("|---|---:|---:|")
    for day, d in summary["per_day"].items():
        w(f"| {day} | {len(d['new'])} | {len(d['duplicates'])} |")
    w("")
    # review queue
    w(f"## {t['review']} ({c['needs_review']})")
    w("")
    rv = [r for r in rows if r["needs_review"] == "yes"]
    if rv:
        w(f"| row | id | {t['reasons']} |")
        w("|---|---|---|")
        for r in rv:
            w(f"| {r['row_id']} | {r['first_src_id']} | {_md(_gloss(r['review_reasons'], lang))} |")
    else:
        w(t["none"])
    w("")
    pr = [r for r in rows if r["possible_duplicate_of"]]
    if pr:
        w(f"### {t['possible']} ({len(pr)})")
        w("")
        for r in pr:
            w(f"- {r['row_id']} ({r['first_src_id']}) - {t['other']}: {r['possible_duplicate_of']}")
        w("")
    # quality
    w(f"## {t['quality']}")
    w("")
    w(f"- {t['m_unparsed']}: **{c['unparsed']}** [A2]")
    for u in load.unparsed[:20]:
        w(f"  - {_md(u.get('ref'))}: {_md(u.get('reason'))}")
    if len(load.unparsed) > 20:
        w(f"  - ... (+{len(load.unparsed) - 20})")
    w(f"- {t['skipped_by']} [A3]:")
    for reason, lst in summary["skipped_by_reason"].items():
        w(f"  - {reason}: **{len(lst)}**")
    if load.ignored.get("service_messages"):
        w(f"- {t['svc_msgs']}: {load.ignored['service_messages']}")
    if load.ignored.get("blank_rows"):
        w(f"- {t['blank_rows']}: {load.ignored['blank_rows']}")
    for wn in summary["warnings"]:
        key = {"staff_not_given": "w_staff", "no_timestamp": "w_nots", "answer_unknown": "w_unknown",
               "html_blocks": "w_html"}[wn["code"]]
        w(f"- **{t['warnings']}**: " + t[key].format(n=wn["n"]))
    w("")
    # appendix
    w(f"## {t['appendix']}")
    w("")
    appendix = [("A1", "m_input", ids["input_items"]), ("A2", "m_unparsed", ids["unparsed"]),
                ("A3", "m_skipped", ids["skipped"]), ("A4", "m_found", ids["requests_found"]),
                ("A5", "m_new", ids["unique_requests"]), ("A6", "m_dups", ids["duplicates_merged"]),
                ("A7", "m_review", ids["needs_review"]), ("A8", "m_possible", ids["possible_duplicates"]),
                ("A9", "sla", sla["unanswered"]), ("A10", "breach", sla["breaching"])]
    for ref, label, lst in appendix:
        name = t[label]
        w(f"**[{ref}] {name} - {len(lst)}**: {_ids(lst)}")
        w("")
    return "\n".join(out).rstrip() + "\n"


# ----------------------------------------------------------------------------- draft notifications
_ASK = {
    "ru": {
        "phone_ambiguous": "Уточните, пожалуйста, номер телефона целиком, с кодом страны (вы указали: {phone_raw}).",
        "phone_multiple": "Укажите, пожалуйста, по какому из номеров с вами связаться: {phones}.",
        "email_invalid": "Уточните, пожалуйста, адрес электронной почты (вы указали: {email_raw}).",
        "amount_ambiguous": "Уточните, пожалуйста, сумму и валюту бюджета (вы указали: {amount_raw}).",
        "amount_multiple": "Уточните, пожалуйста, бюджет: в сообщении несколько сумм ({amount_candidates}).",
        "amount_currency_missing": "Уточните, пожалуйста, валюту бюджета ({amount}).",
        "service_missing": "Уточните, пожалуйста, какая услуга вам нужна.",
        "service_ambiguous": "Уточните, пожалуйста, что нужно в первую очередь: {service_candidates}.",
        "deadline_ambiguous": "Уточните, пожалуйста, срок (вы указали: {deadline_raw}).",
        "contact_missing": "Оставьте, пожалуйста, телефон или e-mail для связи.",
    },
    "en": {
        "phone_ambiguous": "Could you confirm the full phone number including the country code (you wrote: {phone_raw})?",
        "phone_multiple": "Which of these numbers should we use to reach you: {phones}?",
        "email_invalid": "Could you confirm your e-mail address (you wrote: {email_raw})?",
        "amount_ambiguous": "Could you confirm the budget amount and currency (you wrote: {amount_raw})?",
        "amount_multiple": "Could you confirm the budget: the message mentions several amounts ({amount_candidates})?",
        "amount_currency_missing": "Could you confirm the currency of the budget ({amount})?",
        "service_missing": "Could you tell us which service you need?",
        "service_ambiguous": "Could you tell us what comes first: {service_candidates}?",
        "deadline_ambiguous": "Could you confirm the deadline (you wrote: {deadline_raw})?",
        "contact_missing": "Please leave a phone number or an e-mail so we can reach you.",
    },
}
_ACK = {
    "ru": {"hello": "Здравствуйте{name}!", "got": "Получили вашу заявку", "svc": " на услугу «{service}»",
           "amount": " Бюджет, как мы поняли: {amount} {currency}.", "deadline": " Срок: {deadline}.",
           "tail": " Ответим вам в ближайшее время."},
    "en": {"hello": "Hello{name}!", "got": "We received your request", "svc": " for \"{service}\"",
           "amount": " Budget as we understood it: {amount} {currency}.", "deadline": " Deadline: {deadline}.",
           "tail": " We will get back to you shortly."},
}
_TEAM = {
    "ru": {
        "title": "Черновики уведомлений - Bossman Operations",
        "banner": "ЧЕРНОВИКИ. Ничего не отправлено: инструмент не умеет отправлять. Перед отправкой каждый текст "
                  "проверяет человек.",
        "team": "Сводка для команды", "customer": "Ответ заказчику", "channel": "Куда ответить", "lang": "Язык",
        "based": "Основано на", "status": "СТАТУС: ЧЕРНОВИК, НЕ ОТПРАВЛЕНО", "asks": "Что уточнить",
        "digest": "За период: новых заявок {new}, дублей объединено {dups}, на ручную проверку {review}, "
                  "не разобрано {unparsed}.",
        "oldest": "Самая старая без ответа: {row} ({src}), возраст {age} ч.",
        "oldest_none": "Заявок без ответа не найдено.",
        "review_head": "На ручную проверку:", "tg": "Telegram: ответить на сообщение {src} (отправитель {sender})",
        "none": "нет данных для связи",
    },
    "en": {
        "title": "Draft notifications - Bossman Operations",
        "banner": "DRAFTS. Nothing was sent: this tool cannot send. A human reviews every text before it goes out.",
        "team": "Team digest", "customer": "Reply to the customer", "channel": "Reply channel", "lang": "Language",
        "based": "Based on", "status": "STATUS: DRAFT, NOT SENT", "asks": "To clarify",
        "digest": "For the period: {new} new requests, {dups} duplicates merged, {review} need manual review, "
                  "{unparsed} not parsed.",
        "oldest": "Oldest unanswered: {row} ({src}), age {age} h.",
        "oldest_none": "No unanswered requests found.",
        "review_head": "For manual review:", "tg": "Telegram: reply to message {src} (sender {sender})",
        "none": "no contact data",
    },
}


def _draft_lang(row_lang: str, default: str) -> str:
    return "ru" if row_lang in ("ru", "uk") else "en" if row_lang == "en" else default


def render_notifications(summary: dict, rows: list, lang: str = "ru") -> str:
    tt = _TEAM[lang]
    c = summary["counts"]
    out: list = []
    w = out.append
    w(f"# {tt['title']}")
    w("")
    w(f"> **{tt['banner']}**")
    w("")
    w(f"## {tt['team']} - {tt['status']}")
    w("")
    w(tt["digest"].format(new=c["unique_requests"], dups=c["duplicates_merged"], review=c["needs_review"],
                          unparsed=c["unparsed"]))
    o = summary["sla"]["oldest"]
    w("")
    w(tt["oldest"].format(row=o[0]["row_id"], src=o[0]["src_id"], age=o[0]["age_h"]) if o else tt["oldest_none"])
    rv = [r for r in rows if r["needs_review"] == "yes"]
    if rv:
        w("")
        w(tt["review_head"])
        for r in rv:
            w(f"- {r['row_id']} ({r['first_src_id']}): {_md(_gloss(r['review_reasons'], lang))}")
    w("")
    n = 0
    for r in rows:
        n += 1
        dl = _draft_lang(r["language"], lang)
        ack, ask = _ACK[dl], _ASK[dl]
        flags = [f.strip() for f in r["flags"].split(";") if f.strip()]
        reasons = [f.strip() for f in r["review_reasons"].split(";") if f.strip()]
        name = f", {r['name']}" if r["name"] else ""
        text = ack["hello"].format(name=name) + " " + ack["got"]
        if r["service"]:
            text += ack["svc"].format(service=SERVICE_TITLES[dl].get(r["service"], r["service"]))
        text += "."
        if r["amount"] and r["currency"]:
            text += ack["amount"].format(amount=r["amount"], currency=r["currency"])
        if r["deadline_raw"] and r["deadline_basis"] not in ("urgent", "ambiguous"):
            text += ack["deadline"].format(deadline=r["deadline_raw"])
        text += ack["tail"]
        asks = []
        keys = [f for f in reasons + flags if f in ask]
        fmt = {"phone_raw": r["phone_raw"], "phones": ", ".join(filter(None, [r["phone"]] + r["phone_alt"].split("; "))),
               "email_raw": r["email_raw"], "amount_raw": r["amount_raw"], "amount_candidates": r["amount_candidates"],
               "amount": r["amount"], "service_candidates": r["service_candidates"], "deadline_raw": r["deadline_raw"]}
        for k in dict.fromkeys(keys):
            if k in ("service_missing", "contact_missing") and k not in reasons:
                continue                # an absence is only asked about when the operator made it required
            asks.append(ask[k].format(**fmt))
        if r["source"] == "telegram_json" or r["source"] == "telegram_html":
            channel = tt["tg"].format(src=r["first_src_id"], sender=r["sender"] or "?")
        else:
            channel = "; ".join(x for x in (r["phone"], r["email"], r["handle"]) if x) or tt["none"]
        w(f"## {n}. {r['row_id']} - {tt['customer']} - {tt['status']}")
        w("")
        w(f"- {tt['channel']}: {_md(channel)}")
        w(f"- {tt['lang']}: {dl}")
        merged = f" + {r['merged_src_ids']}" if r["merged_src_ids"] else ""
        w(f"- {tt['based']}: {r['first_src_id']}{merged}")
        w("")
        w("> " + _md(text + (" " + " ".join(asks) if asks else "")))
        w("")
    return "\n".join(out).rstrip() + "\n"
