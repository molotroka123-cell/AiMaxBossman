---
name: defense-tender-monetization
description: Help the owner earn or structure legal revenue on military, defense, dual-use, and public procurement tenders using War Terminal contacts (Future Military Forum 2026) and deterministic war_terminal.* monetization tools.
---

# Defense & Procurement Tender Monetization (`defense-tender-monetization`)

Use this skill whenever the owner asks how to earn on military/defense tenders, how to monetize contacts from exhibitions/forums (such as **Future Military Forum 2026, Prague**), which open tenders match a specific supplier, or how to prepare a B2B offer / deal structure.

## 1. Available Read-Only Bossman Tools (`war_terminal.*`)

Always use the deterministic `war_terminal.*` tools first — never guess tender numbers, CPV codes, FX rates, or margins:

1. `war_terminal.monetization_radar` (`GET /api/war-terminal/monetization/opportunities`)
   - Scans all 21+ forum contacts and all open tenders in `war.db`.
   - Ranks `(supplier, open_tender)` pairs by deterministic `priority_score` and computes **3 legal revenue models**:
     - **Model A — Agency / Introducer Commission (`agency_introducer`, 2–5%, default 3%):** `$0.00` working capital required (`WITHIN_LIMIT`). You bring a qualified tender dossier to the forum manufacturer and sign an introducer/scouting or local tender representation agreement.
     - **Model B — Back-to-Back / Prime Supply (`back_to_back_supply`, default 15% gross margin):** Exact `landed_cost`, `gross_profit`, `margin_pct`, `markup_pct`, `peak_funding_need_usd`, `+90d` payment delay stress, and `$50,000 USD` capital limit check (`WITHIN_LIMIT` vs `OVER_LIMIT` / `MATCHED_DEAL`).
     - **Model C — Local Subcontract / Consortium (`local_subcontract`, 5%):** When `supplier.country != tender.country`, local tender preparation, translation, certification, or MRO support.
2. `war_terminal.list_forum_contacts` (`GET /api/war-terminal/contacts`)
   - Lists forum suppliers (`q`, `status`), their countries, CPV prefixes, and product lines.
3. `war_terminal.match_supplier_tenders` (`GET /api/war-terminal/contacts/{cid}/matches`)
   - Returns top open matching tenders for a specific supplier contact ID, with CPV/keyword explanations and compliance badges.
4. `war_terminal.draft_supplier_pitch` (`GET /api/war-terminal/contacts/{cid}/pitch`)
   - Generates a ready-to-send Markdown pitch (`.md`) for the supplier with top-5 open tenders and zero personal data.
5. `war_terminal.analyze_tender`, `war_terminal.check_compliance`, `war_terminal.estimate_cashflow`, `war_terminal.draft_bid_checklist`
   - Deep-dive compliance gates, custom landed-cost cashflow simulation, and step-by-step bid checklist for a specific `tender_key`.

## 2. Three Regulatory Tracks (Strict Compliance Rules)

Every matched opportunity is classified into one of three tracks:

1. `UNCONTROLLED_FAST_START` (**Быстрый старт без лицензии военного материала MPO**):
   - Protective helmets/gloves (`18444000`), standard security cameras (`35125000`), IT/eGate infrastructure, fuel, food, logistics.
   - Can be executed immediately via **Back-to-Back supply** (if `peak_funding_need_usd <= $50,000 USD`) or **Agency Commission**.
2. `DUAL_USE_2021_821` (**Двойное назначение — Регламент ЕС 2021/821**):
   - UAVs (`35613000`, `34711200`), radars (`35722000`, `38115000`), thermal/night-vision optics (`38636000`).
   - Recommend **Model A (Agency / Introducer)** or **Model C (Local Subcontract)** so the manufacturer handles export classification under EU Regulation 2021/821.
3. `MILITARY_MPO_LICENCE` (**Военный материал — лицензия MPO CZ / Закон № 38/1994 Сб.**):
   - Ammunition (`35331000`), firearms (`35321000`), ballistic vests (`35812000`), EW/C-UAS (`35730000`), missiles/armored vehicles.
   - Without an MPO military material license, **never** recommend buying/reselling directly. Only recommend **information/scouting introduction to the licensed manufacturer** after legal review (`REVIEW_REQUIRED — НЕ РЕКОМЕНДАЦИЯ К ПРОДАЖЕ`).

## 3. Hard Safety Invariants

- **Never fabricate tenders, prices, or FX rates.** If a notice is a framework agreement without `estimated_value`, report it as `FRAMEWORK_OR_UNPRICED`.
- **Respect the `$50,000 USD` capital limit.** Any deal with `peak_funding_need_usd > $50,000` is `OVER_LIMIT` unless `order_confirmed=True` and `supplier_contract_signed=True` (`MATCHED_DEAL`) + explicit owner approval.
- **No automated bidding or outreach.** `/actions/*` is permanently disabled (`403`); Bossman prepares the `.md` pitch and dossier for the human owner to review and send.
