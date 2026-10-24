"""Per-client tender-lead dossier: "these open public tenders match your products" for one small supplier.

Input: a supplier profile (name, CPV codes and/or keywords, buyer countries, optional capacity) + tender records.
Output: a JSON-safe dict (the JSON sibling) and a Markdown rendering of it (`dossier_markdown`).

Deterministic, explainable, local:
  * matching = contacts.match (CPV prefix overlap + whole-word keywords, the same engine as the contact radar);
  * compliance badges = contacts.assess; regulatory track = monetization.classify_regulatory_track;
  * money = monetization.calculate_deal_economics (Decimal, never a silent 1:1 FX, unpriced -> None).

Hard rules:
  * only OPEN tenders whose UTC deadline is still in the future relative to `today` (no deadline -> excluded);
  * fast-start only by default: MILITARY_MPO_LICENCE and DUAL_USE_2021_821 matches are excluded unless
    approved="compliance" (then included and flagged CONTROLLED_COMPLIANCE_APPROVED); a restricted-country or exact
    sanctions block is never included, approval or not;
  * no model is called unless the caller passes `summarizer` (a callable tender_brief -> str | None; default None);
  * nothing is sent anywhere: the dossier is a local document (`delivery` = "NOT_SENT"); sending is a human decision.
"""
from __future__ import annotations

import json
from datetime import date, datetime, timezone
from decimal import Decimal
from typing import Callable

from . import contacts, finance, monetization, rules

D = Decimal
Q = D("0.01")
SCHEMA = "war-terminal.tender-dossier/1"
APPROVALS = ("compliance",)
FOOTER = "prepared locally by Bossman War Terminal; not legal advice; verify on the official portal"
CONTROLLED_FLAG = "CONTROLLED_COMPLIANCE_APPROVED"
EXCLUSION_REASONS = ("NOT_OPEN", "EXPIRED", "NO_DEADLINE", "COUNTRY_FILTER", "BLOCKED", "CONTROLLED_NOT_APPROVED")
MAX_ITEMS = 25


class DossierError(ValueError):
    def __init__(self, code: str, fields: list[str], messages: list[str]):
        super().__init__("; ".join(messages))
        self.code, self.fields, self.messages = code, fields, messages


def _keywords(raw) -> dict[str, list[str]]:
    """A flat list goes under "en" (matching is language-agnostic on folded text; the label only names the reason)."""
    if not raw:
        return {}
    if isinstance(raw, (list, tuple)):
        return {"en": [str(w) for w in raw]}
    if isinstance(raw, dict):
        return {str(k): [str(w) for w in (v or [])] for k, v in raw.items()}
    raise DossierError("PROFILE_INVALID", ["keywords"], ["keywords: список слов или {язык: [слова]}"])


def normalize_profile(profile: dict | None) -> dict:
    """Validate a supplier profile. Raises DossierError: PROFILE_EMPTY (no name or nothing to match on) / PROFILE_INVALID."""
    profile = profile or {}
    name = str(profile.get("name") or "").strip()
    cpv = [str(c).strip() for c in (profile.get("cpv_codes") or []) if str(c).strip()]
    kws = _keywords(profile.get("keywords"))
    if len(name) < 2 or not (cpv or any(kws.values())):
        fields = (["name"] if len(name) < 2 else []) + ([] if (cpv or any(kws.values())) else ["cpv_codes", "keywords"])
        raise DossierError("PROFILE_EMPTY", fields, ["профиль поставщика пуст: нужно название и хотя бы один код CPV или ключевое слово"])
    try:
        clean = contacts.clean({"company": name, "country": profile.get("country"), "cpv_prefixes": cpv, "keywords": kws})
    except contacts.ContactError as exc:
        raise DossierError("PROFILE_INVALID", exc.fields, exc.messages) from exc
    countries: list[str] = []
    for c in profile.get("countries") or []:
        c2 = rules.country2(str(c))
        if not c2:
            raise DossierError("PROFILE_INVALID", ["countries"], [f"countries: страна не распознана ({c})"])
        if c2 not in countries:
            countries.append(c2)
    cap = profile.get("capacity") or {}
    capacity = None
    if cap.get("max_contract_value") is not None:
        cur = str(cap.get("currency") or "").strip().upper()
        try:
            val = D(str(cap["max_contract_value"]))
        except ArithmeticError as exc:
            raise DossierError("PROFILE_INVALID", ["capacity"], ["capacity.max_contract_value: число"]) from exc
        if val <= 0 or len(cur) != 3:
            raise DossierError("PROFILE_INVALID", ["capacity"], ["capacity: max_contract_value > 0 и валюта ISO (EUR, CZK, UAH...)"])
        capacity = {"max_contract_value": str(val.quantize(Q)), "currency": cur}
    if not clean["cpv_prefixes"] and not any(clean["keywords"].values()):
        raise DossierError("PROFILE_EMPTY", ["cpv_codes", "keywords"], ["профиль поставщика пуст после проверки CPV/ключевых слов"])
    return {"name": clean["company"], "country": clean.get("country"), "cpv_codes": clean["cpv_prefixes"], "keywords": clean["keywords"],
            "countries": countries, "capacity": capacity}


def _key(t: dict) -> str:
    return t.get("key") or f"{t.get('jurisdiction')}:{t.get('official_notice_id')}"


def _capacity_fit(t: dict, econ: dict, capacity: dict | None, fx_rates: dict) -> dict:
    if not capacity:
        return {"status": "NOT_GIVEN"}
    if not econ["priced"]:
        return {"status": "UNPRICED"}
    try:
        value = finance.to_base(D(econ["estimated_value"]), econ["currency"], capacity["currency"], fx_rates).quantize(Q)
    except ValueError:
        return {"status": "FX_RATE_MISSING", "reason": f"нет курса {econ['currency']}->{capacity['currency']}"}
    limit = D(capacity["max_contract_value"])
    return {"status": "WITHIN" if value <= limit else "EXCEEDS", "tender_value_in_capacity_currency": str(value),
            "capacity": capacity["max_contract_value"], "currency": capacity["currency"]}


def _next_steps(t: dict, reg: dict, econ: dict, fit: dict, days_left: int | None, approved_controlled: bool) -> list[str]:
    steps = [f"Open the official notice and download the tender documents: {t.get('source_url') or 'link missing - find it on the official portal'}"]
    if days_left is not None:
        steps.append(f"Deadline in {days_left} day(s): confirm the qualification documents (registration, references, certificates) are ready in time.")
    if not econ["priced"]:
        steps.append("No estimated value is published (framework or unpriced): ask the buyer for the lot specification before quoting.")
    elif fit["status"] == "EXCEEDS":
        steps.append("The lot is larger than the stated capacity: bid for a lot/part, form a consortium, or subcontract to a prime bidder.")
    elif fit["status"] == "FX_RATE_MISSING":
        steps.append("Enter an official FX rate for this currency before comparing the lot with your capacity (no rate is guessed).")
    if econ["priced"] and econ.get("back_to_back_supply"):
        b2b = econ["back_to_back_supply"]
        steps.append(f"Price check: at a {b2b['margin_pct']}% target margin your landed cost must stay under {b2b['landed_cost']} {econ['currency']}.")
    if approved_controlled:
        steps.append("CONTROLLED ITEM (compliance-approved for review only): the supplier must hold the export/trade licence itself; "
                     "no introduction before the licence is verified.")
    steps.append("Decide yourself whether to bid; War Terminal does not submit bids or contact buyers.")
    return steps


def _why_en(m: dict) -> list[str]:
    """English form of contacts.match reasons, built from the structured hits (the Russian text stays in `why_ru`)."""
    out = []
    for h in m["cpv"]:
        if h["kind"] == "inside":
            out.append(f"tender CPV {h['tender_cpv']} is inside your CPV {h['company_prefix']}*")
        else:
            out.append(f"tender CPV {h['tender_cpv']} is broader than your CPV {h['company_prefix']}* (partial match {h['strength']:.0%})")
    for f in m["keywords"]:
        out.append(f"keyword \"{f['keyword']}\" found in the tender text: \"{f['found']}\"")
    return out


def _tender_brief(t: dict) -> dict:
    return {f: t.get(f) for f in ("title", "title_en", "buyer", "country", "deadline", "estimated_value", "currency", "cpv", "source_url")}


def build_dossier(
    profile: dict,
    tenders: list[dict],
    *,
    today: date | datetime | None = None,
    fx_rates: dict | None = None,
    fx_meta: dict | None = None,
    sanctions=None,
    approved: str | None = None,
    fee_pct: float = 3.0,
    target_margin_pct: float = 15.0,
    max_items: int = MAX_ITEMS,
    summarizer: Callable[[dict], str | None] | None = None,
) -> dict:
    """Build one supplier's dossier. `approved` is None or "compliance" (the caller has checked the role)."""
    if approved is not None and approved not in APPROVALS:
        raise DossierError("APPROVAL_INVALID", ["approved"], ["approved: только 'compliance' (или пусто)"])
    prof = normalize_profile(profile)
    ref = today if today is not None else datetime.now(timezone.utc)
    fx_rates = {k.upper(): v for k, v in (fx_rates or {}).items() if v is not None and float(v) > 0}
    if isinstance(sanctions, (list, tuple, set)):
        sanctions = rules.SanctionsIndex(list(sanctions)) if sanctions else None
    contact = {"id": 0, "company": prof["name"], "country": prof["country"], "cpv_prefixes": prof["cpv_codes"], "keywords": prof["keywords"]}
    cprof = contacts.Profile(contact)

    items: list[dict] = []
    excluded: list[dict] = []
    no_match = 0
    for raw in tenders:
        t = {**raw, "key": _key(raw)}
        m = contacts.match(t, cprof)
        if not m["matched"]:
            no_match += 1
            continue
        eff = rules.effective_status(t, ref)
        days_left = rules._days_left(t.get("deadline"), ref)
        reason = None
        if rules.parse_deadline_utc(t.get("deadline")) is None:
            reason = "NO_DEADLINE"
        elif rules.is_deadline_expired(t.get("deadline"), ref):
            reason = "EXPIRED"
        elif eff != "open":
            reason = "NOT_OPEN"
        elif prof["countries"] and contacts.buyer_country(t) not in prof["countries"]:
            reason = "COUNTRY_FILTER"
        badges, blocks = contacts.assess(t, cprof, m, sanctions)
        reg = monetization.classify_regulatory_track({"badges": badges, "tender": t})
        if reason is None and blocks:
            reason = "BLOCKED"
        if reason is None and not reg["fast_start"] and approved != "compliance":
            reason = "CONTROLLED_NOT_APPROVED"
        if reason:
            excluded.append({"tender_key": t["key"], "title": t.get("title"), "reason": reason, "track": reg["track"],
                             "deadline": t.get("deadline"), "blocks": blocks})
            continue
        econ = monetization.calculate_deal_economics(t, contact, fx_rates=fx_rates, fee_pct=fee_pct, target_margin_pct=target_margin_pct)
        fit = _capacity_fit(t, econ, prof["capacity"], fx_rates)
        controlled = not reg["fast_start"]
        summary, summary_status = None, "NO_HOOK"
        if summarizer is not None:
            try:
                summary = summarizer(_tender_brief(t))
                summary_status = "OK" if summary else "EMPTY"
            except Exception as exc:                               # noqa: BLE001 - a broken local model must not break the dossier
                summary_status = f"HOOK_ERROR:{type(exc).__name__}"
        items.append({
            "tender_key": t["key"],
            "title": t.get("title"),
            "title_en": t.get("title_en"),
            "buyer": t.get("buyer"),
            "buyer_country": contacts.buyer_country(t),
            "deadline": t.get("deadline"),
            "days_left": days_left,
            "estimated_value": econ["estimated_value"],
            "currency": econ["currency"],
            "estimated_value_usd": econ["estimated_value_usd"],
            "estimated_value_eur": econ["estimated_value_eur"],
            "fx_status": econ["fx_status"],
            "pricing_status": econ["pricing_status"],
            "cpv": t.get("cpv") or [],
            "source_id": t.get("source_id"),
            "source_url": t.get("source_url"),
            "match_score": m["base_score"],
            "why": _why_en(m),
            "why_ru": m["reasons"],
            "badges": badges,
            "regulatory": reg,
            "controlled": controlled,
            "flags": [CONTROLLED_FLAG] if controlled else [],
            "economics": econ,
            "capacity_fit": fit,
            "summary": summary,
            "summary_status": summary_status,
            "next_steps": _next_steps(t, reg, econ, fit, days_left, controlled),
        })

    def _order(i: dict):
        usd = i["estimated_value_usd"]
        native = D(i["estimated_value"]) if i["estimated_value"] is not None else None
        # USD-comparable first; without a rate, the native value is compared only inside the same currency (never cross-currency)
        return (i["controlled"], -i["match_score"], usd is None, -(D(usd) if usd is not None else D(0)), native is None,
                i["currency"] or "", -(native or D(0)), i["tender_key"])

    items.sort(key=_order)
    excluded.sort(key=lambda e: (EXCLUSION_REASONS.index(e["reason"]), e["tender_key"]))
    counts = {r: sum(1 for e in excluded if e["reason"] == r) for r in EXCLUSION_REASONS}
    selected = items[:max_items]
    pipeline_usd = sum((D(i["estimated_value_usd"]) for i in selected if i["estimated_value_usd"] is not None), D("0.00"))
    fee_usd = sum((D(i["economics"]["agency_introducer"]["commission_base_usd"]) for i in selected
                   if i["economics"]["agency_introducer"].get("commission_base_usd") is not None), D("0.00"))
    as_of = (ref.astimezone(timezone.utc) if isinstance(ref, datetime) and ref.tzinfo else ref)
    return {
        "schema": SCHEMA,
        "as_of": as_of.isoformat(),
        "supplier": prof,
        "approved": approved,
        "parameters": {"fee_pct": str(D(str(fee_pct)).quantize(Q)), "target_margin_pct": str(D(str(target_margin_pct)).quantize(Q)),
                       "max_items": max_items, "fast_start_only": approved != "compliance"},
        "fx": {"as_of": (fx_meta or {}).get("as_of"), "source": (fx_meta or {}).get("source"), "pairs": sorted(fx_rates)},
        "sanctions_screened": bool(sanctions),
        "summary": {
            "tenders_considered": len(tenders),
            "no_match": no_match,
            "matched_open": len(items),
            "listed": len(selected),
            "controlled_listed": sum(1 for i in selected if i["controlled"]),
            "excluded": counts,
            "unpriced_listed": sum(1 for i in selected if not i["economics"]["priced"]),
            "fx_missing_listed": sum(1 for i in selected if i["fx_status"] == "FX_RATE_MISSING"),
            "listed_value_usd": str(pipeline_usd.quantize(Q)),
            "listed_introducer_fee_usd": str(fee_usd.quantize(Q)),
        },
        "items": selected,
        "excluded": excluded,
        "delivery": "NOT_SENT",
        "footer": FOOTER,
    }


def dossier_json(dossier: dict) -> str:
    return json.dumps(dossier, ensure_ascii=False, indent=1, sort_keys=True)


def _money(amount, currency, usd) -> str:
    if amount is None:
        return "not published (framework / unpriced)"
    s = f"{amount} {currency}"
    return s + (f" (~{usd} USD)" if usd is not None else " (USD: no official FX rate entered)")


def dossier_markdown(d: dict) -> str:
    """Client-facing Markdown. No personal data: the profile has none and the tender records carry only public notice fields."""
    s, sup = d["summary"], d["supplier"]
    cpv = ", ".join(sup["cpv_codes"]) or "-"
    kws = ", ".join(w for ws in sup["keywords"].values() for w in ws) or "-"
    lines = [
        f"# Open tender leads for {sup['name']} ({d['as_of'][:10]})",
        "",
        f"- **Profile:** CPV {cpv}; keywords {kws}; buyer countries {', '.join(sup['countries']) or 'any'}"
        + (f"; capacity up to {sup['capacity']['max_contract_value']} {sup['capacity']['currency']}" if sup.get("capacity") else ""),
        f"- **Matching open tenders:** {s['listed']} listed (of {s['matched_open']} open matches, {s['tenders_considered']} notices checked)",
        f"- **Listed value:** "
        + (f"~{s['listed_value_usd']} USD where an official FX rate exists" if s["listed"] > s["fx_missing_listed"] + s["unpriced_listed"]
           else "no USD total (no official FX rate for the listed currencies)")
        + f" ({s['fx_missing_listed']} without a rate, {s['unpriced_listed']} unpriced)",
        f"- **Not listed:** " + ", ".join(f"{k.lower().replace('_', ' ')} {v}" for k, v in s["excluded"].items() if v) if any(s["excluded"].values())
        else "- **Not listed:** none",
    ]
    if not d["parameters"]["fast_start_only"]:
        lines.append(f"- **Compliance approval:** controlled items are included for review only ({s['controlled_listed']} flagged `{CONTROLLED_FLAG}`)")
    else:
        lines.append("- **Scope:** uncontrolled goods and services only (no military-licence or dual-use items)")
    if not d["sanctions_screened"]:
        lines.append("- **Sanctions:** lists not loaded - buyers are NOT screened yet; screen before any contact")
    lines += ["", "## Matching tenders", ""]
    if not d["items"]:
        lines.append("No open tender matches this profile right now.")
    for n, it in enumerate(d["items"], 1):
        ag = it["economics"]["agency_introducer"]
        b2b = it["economics"].get("back_to_back_supply")
        flag = f" `{CONTROLLED_FLAG}`" if it["controlled"] else ""
        lines += [
            f"### {n}. {it['title']}{flag}",
            "",
            f"- **Buyer:** {it.get('buyer') or 'not stated'} ({it['buyer_country']})",
            f"- **Deadline:** {(it.get('deadline') or '-')[:16].replace('T', ' ')} UTC ({it['days_left']} day(s) left)",
            f"- **Estimated value:** {_money(it['estimated_value'], it['currency'], it['estimated_value_usd'])}",
            f"- **Why it matches (score {it['match_score']}):** " + "; ".join(it["why"]),
            f"- **Track:** {it['regulatory']['track']}",
        ]
        if b2b:
            lines.append(f"- **Estimated margin (supply, target {d['parameters']['target_margin_pct']}%):** gross profit {b2b['gross_profit']} "
                         f"{it['currency']} at margin {b2b['margin_pct']}%, max purchase {b2b['max_supplier_purchase']} {it['currency']}")
        if it["economics"]["priced"]:
            lines.append(f"- **Introducer fee ({ag['fee_pct']}%):** {ag['commission_base']} {it['currency']}"
                         + (f" (~{ag['commission_base_usd']} USD)" if ag.get("commission_base_usd") else ""))
        if it["capacity_fit"]["status"] not in ("NOT_GIVEN",):
            lines.append(f"- **Capacity:** {it['capacity_fit']['status']}")
        if it.get("summary"):
            lines.append(f"- **Summary (local model):** {it['summary']}")
        lines.append("- **Next steps:**")
        lines += [f"  {i}. {step}" for i, step in enumerate(it["next_steps"], 1)]
        lines += [f"- **Official notice:** {it.get('source_url') or '-'}", ""]
    lines += ["---", "Figures are deterministic estimates from the published notice and official FX rates; not a win probability.",
              "", f"_{d['footer']}_", ""]
    return "\n".join(lines)
