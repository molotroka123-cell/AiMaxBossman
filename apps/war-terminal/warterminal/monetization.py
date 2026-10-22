"""Deterministic Tender Monetization & Deal Playbook Engine for War Terminal + Bossman.

Turns matched (forum_contact, open_tender) pairs into concrete, rule-compliant monetization calculations:
  1. Track A — Agency / Introducer Commission (2% / 3% / 5% scouting & B2B introduction fee, $0 working capital).
  2. Track B — Back-to-Back / Prime Supply (exact landed_cost, margin vs markup, cashflow peak need, +30/+60/+90d delay
     stress, and $50,000 USD capital cap check via warterminal.finance).
  3. Track C — Local Consortium / Subcontracting (5–8% local representation, translation, and bid coordination when
     supplier country != buyer country).

Safety & provenance invariants:
  * Never fabricates tenders, prices, or FX rates: if a tender has no estimated_value, monetary outputs are None and the
    opportunity is marked FRAMEWORK_OR_UNPRICED. If an FX rate to USD/EUR is missing in store FX, USD/EUR fields stay
    None with fx_status="FX_RATE_MISSING" (never a silent 1:1 fallback).
  * Never overrides compliance gates: military_licence (MPO CZ) and dual_use (EU 2021/821) badges are preserved and
    determine the regulatory_track.
  * Never leaks personal data (person_name, person_phone, public_email, notes).
"""
from __future__ import annotations

from datetime import date, datetime, timezone
from decimal import Decimal

from . import contacts, finance, rules
from .ai import redact

D = Decimal
Q = D("0.01")

REGULATORY_TRACKS = (
    "UNCONTROLLED_FAST_START",
    "DUAL_USE_2021_821",
    "MILITARY_MPO_LICENCE",
)


def classify_regulatory_track(item: dict) -> dict:
    """Classify an open tender match into one of the 3 regulatory tracks."""
    badge_ids = {b.get("id") for b in (item.get("badges") or [])}
    t = item.get("tender") or {}
    if "military_licence" in badge_ids or rules.is_controlled(t):
        return {
            "track": "MILITARY_MPO_LICENCE",
            "label_ru": "Военный материал (лицензия MPO CZ / прямая подача производителем)",
            "fast_start": False,
            "recommended_model": "agency_introducer",
            "compliance_note_ru": (
                "Требуется разрешение MPO ЧР на торговлю военным материалом (Закон № 38/1994 Сб.) "
                "при прямой поставке; без лицензии допустима только информационная стыковка с производителем "
                "после юридической проверки (НЕ РЕКОМЕНДАЦИЯ К ПРОДАЖЕ без лицензии)."
            ),
        }
    if "dual_use" in badge_ids:
        return {
            "track": "DUAL_USE_2021_821",
            "label_ru": "Двойное назначение (проверка Регламента ЕС 2021/821)",
            "fast_start": False,
            "recommended_model": "agency_introducer",
            "compliance_note_ru": (
                "Товар двойного назначения (БПЛА, радар, РЭБ, оптика): перед экспортом за пределы ЕС "
                "обязательна классификация по Регламенту (ЕС) 2021/821; внутри ЕС или при прямой подаче "
                "производителем — приоритет агентской/консорциумной модели."
            ),
        }
    return {
        "track": "UNCONTROLLED_FAST_START",
        "label_ru": "Быстрый старт (без лицензии военного материала MPO)",
        "fast_start": True,
        "recommended_model": "back_to_back_or_agency",
        "compliance_note_ru": (
            "Открытый тендер без признаков контролируемого военного материала и без санкционных блокировок: "
            "доступна как агентская комиссия, так и поставка back-to-back в пределах лимита капитала $50 000."
        ),
    }


def _convert_optional(amount: Decimal, currency: str | None, target: str, fx_rates: dict) -> Decimal | None:
    if not currency:
        return None
    cur = currency.strip().upper()
    if cur == target:
        return amount.quantize(Q)
    try:
        return finance.to_base(amount, cur, target, fx_rates).quantize(Q)
    except ValueError:
        return None


def calculate_deal_economics(
    tender: dict,
    contact: dict,
    *,
    fx_rates: dict | None = None,
    fee_pct: float = 3.0,
    target_margin_pct: float = 15.0,
    supplier_prepay_pct: float = 30.0,
    delivery_day: int = 30,
    acceptance_day: int = 40,
    customer_pay_delay_days: int = 30,
    capital_limit_usd: float = 50000.0,
) -> dict:
    """Deterministic financial breakdown for a (contact, tender) opportunity using warterminal.finance."""
    fx_rates = fx_rates or {}
    raw_val = tender.get("estimated_value")
    currency = (tender.get("currency") or "").strip().upper() or None
    cross_border = bool(
        contact.get("country")
        and (tender.get("country") or tender.get("jurisdiction"))
        and contact.get("country") != rules.country2(tender.get("country"))
    )

    if raw_val is None or float(raw_val) <= 0 or not currency:
        return {
            "priced": False,
            "pricing_status": "FRAMEWORK_OR_UNPRICED",
            "currency": currency,
            "estimated_value": None,
            "estimated_value_usd": None,
            "estimated_value_eur": None,
            "fx_status": "NO_TENDER_VALUE",
            "agency_introducer": {
                "model": "agency_introducer",
                "label_ru": "Агентская комиссия / Introducer Fee (2–5 % от рамочного наряда)",
                "fee_pct": str(D(str(fee_pct)).quantize(Q)),
                "commission_2pct": None,
                "commission_base": None,
                "commission_5pct": None,
                "commission_base_usd": None,
                "working_capital_usd": "0.00",
                "capital_status": "WITHIN_LIMIT",
                "note_ru": "Рамочный договор или цена не указана в извещении: запросить спецификацию лота и зафиксировать 2–5 % агентского вознаграждения от фактически выбранного объёма.",
            },
            "back_to_back_supply": None,
            "local_subcontract": {
                "model": "local_subcontract",
                "applicable": cross_border,
                "fee_pct": "5.00",
                "fee_amount": None,
                "fee_amount_usd": None,
            },
        }

    bid = D(str(raw_val)).quantize(Q)
    val_usd = _convert_optional(bid, currency, "USD", fx_rates)
    val_eur = _convert_optional(bid, currency, "EUR", fx_rates)
    fx_status = "OK" if val_usd is not None else "FX_RATE_MISSING"

    # 1. Agency / Introducer Commission ($0 working capital)
    fee_d = D(str(fee_pct))
    comm_2 = (bid * D("2") / D("100")).quantize(Q)
    comm_base = (bid * fee_d / D("100")).quantize(Q)
    comm_5 = (bid * D("5") / D("100")).quantize(Q)
    comm_base_usd = _convert_optional(comm_base, currency, "USD", fx_rates)
    comm_2_usd = _convert_optional(comm_2, currency, "USD", fx_rates)
    comm_5_usd = _convert_optional(comm_5, currency, "USD", fx_rates)

    agency = {
        "model": "agency_introducer",
        "label_ru": "Агентская комиссия / Introducer Fee (без риска оборотного капитала)",
        "fee_pct": str(fee_d.quantize(Q)),
        "commission_2pct": str(comm_2),
        "commission_base": str(comm_base),
        "commission_5pct": str(comm_5),
        "commission_2pct_usd": str(comm_2_usd) if comm_2_usd is not None else None,
        "commission_base_usd": str(comm_base_usd) if comm_base_usd is not None else None,
        "commission_5pct_usd": str(comm_5_usd) if comm_5_usd is not None else None,
        "working_capital_usd": "0.00",
        "capital_status": "WITHIN_LIMIT",
        "note_ru": "Производитель подаёт заявку напрямую; вознаграждение за квалифицированный лид и подготовку досье.",
    }

    # 2. Back-to-Back Distribution / Prime Supply (exact warterminal.finance math)
    # Target landed_cost = bid * (1 - target_margin_pct/100).
    # With freight=2% of purchase, insurance=0.3% of purchase, certification=0.5% of purchase, risk_reserve_pct=2.0%:
    # landed_cost = purchase * (1 + 0.02 + 0.003 + 0.005 + 0.02) = purchase * 1.048
    target_margin_d = D(str(target_margin_pct))
    target_landed = (bid * (D("100") - target_margin_d) / D("100")).quantize(Q)
    purchase = (target_landed / D("1.048")).quantize(Q)
    freight = (purchase * D("0.02")).quantize(Q)
    insurance = (purchase * D("0.003")).quantize(Q)
    certification = (purchase * D("0.005")).quantize(Q)

    lc = finance.landed_cost(
        purchase=purchase,
        freight=freight,
        insurance=insurance,
        duties_pct=D("0"),
        certification=certification,
        financing=D("0"),
        risk_reserve_pct=D("2.0"),
        overhead=D("0"),
    )
    metrics = finance.price_metrics(bid, lc["landed_cost"])

    def _events(extra_days: int):
        return finance.build_events(
            supplier_prepay_pct=D(str(supplier_prepay_pct)),
            supplier_total=purchase,
            delivery_day=delivery_day,
            acceptance_day=acceptance_day,
            bid_price=bid,
            customer_pay_delay_days=customer_pay_delay_days + extra_days,
            logistics_cost=freight,
            other_costs=insurance + certification,
        )

    cf = finance.cashflow(_events(0))
    st_rows = finance.stress(_events, bid_price=bid, landed=lc["landed_cost"], annual_rate=D("0.12"))
    peak_usd = _convert_optional(cf["peak_funding_need"], currency, "USD", fx_rates)
    profit_usd = _convert_optional(metrics["profit"], currency, "USD", fx_rates)

    if peak_usd is not None:
        cap_unmatched = finance.capital_check(peak_usd, limit_usd=D(str(capital_limit_usd)), order_confirmed=False, supplier_contract_signed=False)
        cap_matched = finance.capital_check(peak_usd, limit_usd=D(str(capital_limit_usd)), order_confirmed=True, supplier_contract_signed=True)
    else:
        cap_unmatched = {
            "status": "FX_REQUIRED",
            "allowed": False,
            "needs_owner_approval": True,
            "reason": f"нет курса {currency}->USD для проверки лимита {capital_limit_usd} USD",
        }
        cap_matched = cap_unmatched

    back_to_back = {
        "model": "back_to_back_supply",
        "label_ru": "Поставка Back-to-Back (закупка у производителя + поставка заказчику)",
        "max_supplier_purchase": str(purchase),
        "landed_cost": str(lc["landed_cost"]),
        "gross_profit": str(metrics["profit"]),
        "gross_profit_usd": str(profit_usd) if profit_usd is not None else None,
        "margin_pct": str(metrics["margin_pct"]),
        "markup_pct": str(metrics["markup_pct"]),
        "peak_funding_need": str(cf["peak_funding_need"]),
        "peak_funding_need_usd": str(peak_usd) if peak_usd is not None else None,
        "peak_day": cf["peak_day"],
        "stress_90d_profit": str(st_rows[-1]["profit"]),
        "stress_90d_margin_pct": str(st_rows[-1]["margin_pct"]),
        "capital_unmatched": cap_unmatched,
        "capital_if_matched_deal": cap_matched,
    }

    # 3. Local Subcontract / Consortium (5% fee)
    sub_fee = (bid * D("5") / D("100")).quantize(Q)
    sub_fee_usd = _convert_optional(sub_fee, currency, "USD", fx_rates)
    local_sub = {
        "model": "local_subcontract",
        "applicable": cross_border,
        "label_ru": "Локальное партнёрство / тендерное сопровождение (5 %)",
        "fee_pct": "5.00",
        "fee_amount": str(sub_fee),
        "fee_amount_usd": str(sub_fee_usd) if sub_fee_usd is not None else None,
    }

    return {
        "priced": True,
        "pricing_status": "PRICED",
        "currency": currency,
        "estimated_value": str(bid),
        "estimated_value_usd": str(val_usd) if val_usd is not None else None,
        "estimated_value_eur": str(val_eur) if val_eur is not None else None,
        "fx_status": fx_status,
        "agency_introducer": agency,
        "back_to_back_supply": back_to_back,
        "local_subcontract": local_sub,
    }


def _next_step_ru(contact: dict, tender: dict, reg: dict, econ: dict) -> str:
    comp = contact.get("company") or "Поставщик"
    tkey = tender.get("key") or ""
    dl = (tender.get("deadline") or "")[:10] or "без даты"
    if not econ["priced"]:
        return (
            f"1) Скачать черновик КП (.md) для {comp} по рамочному тендеру {tkey} (дедлайн {dl}); "
            "2) Запросить у производителя готовность участвовать и согласовать агентский договор 2–5 %."
        )
    b2b = econ.get("back_to_back_supply") or {}
    cap_status = (b2b.get("capital_unmatched") or {}).get("status")
    if reg["track"] == "UNCONTROLLED_FAST_START" and cap_status == "WITHIN_LIMIT":
        return (
            f"БЫСТРЫЙ СТАРТ (в лимите $50k): запросить у {comp} оптовый прайс не выше "
            f"{b2b.get('max_supplier_purchase')} {econ['currency']} для тендера {tkey} (дедлайн {dl}) "
            f"или зафиксировать агентскую комиссию {econ['agency_introducer']['commission_base']} {econ['currency']}."
        )
    if reg["track"] == "UNCONTROLLED_FAST_START" and cap_status != "WITHIN_LIMIT":
        return (
            f"Крупный неконтролируемый лот {tkey} (дедлайн {dl}): пик капитала выше $50k — "
            f"идти через агентскую комиссию ({econ['agency_introducer']['commission_base']} {econ['currency']}) "
            "либо оформить MATCHED_DEAL (подтверждённый заказ + контракт с поставщиком + одобрение владельца)."
        )
    return (
        f"Контролируемый/двухцелевой тендер {tkey} (дедлайн {dl}): предложить {comp} прямую подачу от лица производителя "
        f"(агентский/консорциумный сбор {econ['agency_introducer']['commission_base']} {econ['currency']}) после юридической проверки."
    )


def monetization_opportunities(
    store,
    sanctions,
    *,
    k_per_contact: int = 5,
    max_items: int = 50,
    today: date | datetime | None = None,
    fx_override: dict | None = None,
    fee_pct: float = 3.0,
    target_margin_pct: float = 15.0,
) -> dict:
    """Scan all contacts and open tenders in `store`, returning ranked monetization opportunities and summary KPIs."""
    ref = today if today is not None else datetime.now(timezone.utc)
    if isinstance(sanctions, (list, tuple, set)):
        sanctions = rules.SanctionsIndex(list(sanctions)) if sanctions else None
    fx_doc = store.kv_get("fx") or {}
    fx_rates = dict(fx_doc.get("rates") or {})
    if fx_override:
        fx_rates.update({k.upper(): v for k, v in fx_override.items() if v is not None and float(v) > 0})

    open_tenders = [t for t in store.latest_tenders() if rules.effective_status(t, ref) == "open"]
    contacts_all = [c for c in store.list_contacts() if c.get("status") != "declined"]
    opportunities: list[dict] = []
    contacts_with_matches = 0

    for c in contacts_all:
        ranked = contacts.rank_for_contact(store, c, sanctions, status="open", k=k_per_contact, today=ref, tenders=open_tenders)
        items = ranked.get("items") or []
        if items:
            contacts_with_matches += 1
        for it in items:
            t = it["tender"]
            reg = classify_regulatory_track(it)
            econ = calculate_deal_economics(
                t,
                c,
                fx_rates=fx_rates,
                fee_pct=fee_pct,
                target_margin_pct=target_margin_pct,
            )
            days_left = rules._days_left(t.get("deadline"), ref)
            # Deterministic priority score: match score + bonus for priced notice + bonus for fast-start
            prio = float(it["score"]) + (15.0 if econ["priced"] else 0.0) + (10.0 if reg["fast_start"] else 0.0)
            opportunities.append({
                "priority_score": round(prio, 1),
                "match_score": it["score"],
                "base_score": it["base_score"],
                "contact": {
                    "id": c["id"],
                    "company": c["company"],
                    "country": c.get("country"),
                    "website": c.get("website"),
                    "products": redact(c.get("products") or ""),
                    "status": c.get("status"),
                },
                "tender": {
                    "key": t["key"],
                    "title": t.get("title"),
                    "buyer": t.get("buyer"),
                    "country": t.get("country") or t.get("jurisdiction"),
                    "deadline": t.get("deadline"),
                    "days_left": days_left,
                    "estimated_value": t.get("estimated_value"),
                    "currency": t.get("currency"),
                    "source_id": t.get("source_id"),
                    "source_url": t.get("source_url"),
                    "cpv": t.get("cpv") or [],
                },
                "reasons": it.get("reasons") or [],
                "badges": it.get("badges") or [],
                "recommendable": it.get("recommendable", False),
                "recommendation_status": it.get("recommendation_status"),
                "regulatory": reg,
                "economics": econ,
                "next_step_ru": _next_step_ru(c, t, reg, econ),
            })

    opportunities.sort(
        key=lambda o: (
            -o["priority_score"],
            -o["match_score"],
            -(float(o["economics"]["estimated_value_usd"] or 0)),
            o["tender"]["key"],
            o["contact"]["id"],
        )
    )
    selected = opportunities[:max_items]

    # Deduplicate by tender_key when summing addressable pipeline so multi-supplier matches on the same tender aren't double-counted
    seen_tenders: set[str] = set()
    pipeline_usd = D("0.00")
    agency_base_usd = D("0.00")
    b2b_within_limit_count = 0
    fast_start_count = 0

    for o in opportunities:
        if o["regulatory"]["fast_start"]:
            fast_start_count += 1
        b2b = o["economics"].get("back_to_back_supply") or {}
        if (b2b.get("capital_unmatched") or {}).get("status") == "WITHIN_LIMIT":
            b2b_within_limit_count += 1
        tkey = o["tender"]["key"]
        if tkey not in seen_tenders:
            seen_tenders.add(tkey)
            v_usd = o["economics"].get("estimated_value_usd")
            c_usd = o["economics"]["agency_introducer"].get("commission_base_usd")
            if v_usd is not None:
                pipeline_usd += D(v_usd)
            if c_usd is not None:
                agency_base_usd += D(c_usd)

    return {
        "as_of": (ref.astimezone(timezone.utc).date() if isinstance(ref, datetime) else ref).isoformat(),
        "fx_as_of": fx_doc.get("as_of"),
        "fx_source": fx_doc.get("source"),
        "parameters": {
            "fee_pct": str(D(str(fee_pct)).quantize(Q)),
            "target_margin_pct": str(D(str(target_margin_pct)).quantize(Q)),
            "capital_limit_usd": "50000.00",
        },
        "summary": {
            "contacts_total": len(contacts_all),
            "contacts_with_open_matches": contacts_with_matches,
            "matched_pairs_total": len(opportunities),
            "unique_open_tenders_matched": len(seen_tenders),
            "fast_start_uncontrolled_pairs": fast_start_count,
            "b2b_within_50k_limit_pairs": b2b_within_limit_count,
            "unique_pipeline_usd": str(pipeline_usd.quantize(Q)),
            "unique_agency_fee_base_usd": str(agency_base_usd.quantize(Q)),
        },
        "items": selected,
        "disclaimer": (
            "Расчёт детерминированный по формулам warterminal.finance и курсам ЦБ. "
            "Не является вероятностью победы или юридическим заключением. "
            "Подача заявок, контакт с контрагентами и закупка контролируемых товаров требуют ручного решения владельца и лицензии."
        ),
    }


def playbook_markdown(report: dict) -> str:
    """Render an owner-ready Markdown Monetization Playbook from `monetization_opportunities()`."""
    s = report["summary"]
    p = report["parameters"]
    lines = [
        f"# Радар монетизации военных и оборонных тендеров ({report['as_of']})",
        "",
        "Подготовлено War Terminal (локально, детерминированный расчёт). Никаких персональных данных, автоматических писем или автоподачи заявок.",
        "",
        "## 1. Сводка по базе контактов форума и открытым тендерам",
        "",
        f"- Контактов в работе: **{s['contacts_total']}** (с открытыми совпадениями: **{s['contacts_with_open_matches']}**)",
        f"- Всего открытых пар «поставщик ↔ тендер»: **{s['matched_pairs_total']}** (уникальных открытых тендеров: **{s['unique_open_tenders_matched']}**)",
        f"- Быстрый старт без лицензии военного материала MPO: **{s['fast_start_uncontrolled_pairs']}** пар",
        f"- Сделок Back-to-Back в пределах лимита капитала **$50 000 USD**: **{s['b2b_within_50k_limit_pairs']}** пар",
        f"- Оценённый объём уникальных открытых тендеров (с курсом ЦБ к USD): **${s['unique_pipeline_usd']} USD**",
        f"- Потенциал агентской комиссии ({p['fee_pct']}% базовый сценарий, $0 риск капитала): **${s['unique_agency_fee_base_usd']} USD**",
        "",
        "## 2. Три легальные модели заработка в War Terminal",
        "",
        "1. **Модель А — Агентская / Introducer комиссия (2–5 %, риск капитала $0):** вы передаёте производителю с форума готовое досье открытого тендера (CPV, дедлайн, прямая ссылка, требования) и фиксируете агентский процент от контракта или локальное сопровождение.",
        "2. **Модель Б — Поставка Back-to-Back / Prime Bid (целевая маржа " + f"{p['target_margin_pct']}%):** ваша фирма подаёт заявку, опираясь на твёрдый оффер производителя. До **$50 000 USD** пиковой потребности в капитале — статус `WITHIN_LIMIT`; свыше **$50 000 USD** — только `MATCHED_DEAL` (подтверждённый заказ + подписанный контракт с поставщиком + одобрение владельца).",
        "3. **Модель В — Локальный партнёр / субподряд (5 %):** для иностранных участников форума (SE, IL, AT, TR, HU, IN, UA) — подготовка чешской/ЕС или украинской тендерной документации, перевод, сервис и логистика.",
        "",
        "## 3. Топ возможностей монетизации (открытые тендеры)",
        "",
    ]
    items = report.get("items") or []
    if not items:
        lines.append("Открытых подходящих тендеров сейчас нет.")
    for idx, it in enumerate(items[:20], 1):
        c = it["contact"]
        t = it["tender"]
        reg = it["regulatory"]
        econ = it["economics"]
        ag = econ["agency_introducer"]
        b2b = econ.get("back_to_back_supply")
        val_str = f"{econ['estimated_value']} {econ['currency']}" if econ["priced"] else "рамочный / цена не указана"
        if econ.get("estimated_value_usd"):
            val_str += f" (~${econ['estimated_value_usd']} USD)"
        lines += [
            f"### {idx}. {c['company']} ({c.get('country') or '—'}) ↔ {t['key']}: {t.get('title')}",
            "",
            f"- **Заказчик:** {t.get('buyer') or 'не указан'} ({t.get('country') or '—'}) | **Дедлайн:** {(t.get('deadline') or '—')[:10]}",
            f"- **Объём тендера:** {val_str}",
            f"- **Оценка совпадения:** {it['match_score']} (приоритет {it['priority_score']}) | **Трек:** {reg['label_ru']}",
            f"- **Модель А (Агентская {ag['fee_pct']}%):** "
            + (
                f"{ag['commission_base']} {econ['currency']}"
                + (f" (~${ag['commission_base_usd']} USD)" if ag.get("commission_base_usd") else "")
                + f" [диапазон 2–5 %: {ag['commission_2pct']} – {ag['commission_5pct']} {econ['currency']}], капитал $0"
                if econ["priced"]
                else "2–5 % от выбранного объёма по рамочному договору, капитал $0"
            ),
        ]
        if b2b:
            cap = b2b["capital_unmatched"]
            lines.append(
                f"- **Модель Б (Back-to-Back, маржа {b2b['margin_pct']}% / наценка {b2b['markup_pct']}%):** "
                f"макс. закупка у поставщика {b2b['max_supplier_purchase']} {econ['currency']}, "
                f"валовая прибыль **{b2b['gross_profit']} {econ['currency']}**"
                + (f" (~${b2b['gross_profit_usd']} USD)" if b2b.get("gross_profit_usd") else "")
                + f", пик капитала {b2b['peak_funding_need']} {econ['currency']}"
                + (f" (~${b2b['peak_funding_need_usd']} USD)" if b2b.get("peak_funding_need_usd") else "")
                + f" → `{cap['status']}`"
            )
        lines += [
            f"- **Следующий шаг:** {it['next_step_ru']}",
            f"- **Ссылка:** {t.get('source_url')}",
            "",
        ]
    lines += ["---", report["disclaimer"], ""]
    return "\n".join(lines)
