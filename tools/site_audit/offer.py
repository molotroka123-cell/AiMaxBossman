"""Internal offer card from measured findings: tier, price, capped discount, strongest real problems.

Owner decision 24.10: NO outreach text for now. The card is for the owner only; it contains no e-mail,
no message body and this module has no way to deliver anything to anyone.
"""
from __future__ import annotations

import datetime as _dt
import json
import re
from pathlib import Path

from .audit import Finding, validate_findings

TIERS = {
    "landing": {"price_czk": 14900, "label": "Landing / jednoduchý web"},
    "business": {"price_czk": 24900, "label": "Firemní web (více stránek)"},
    "premium": {"price_czk": 34900, "label": "Klinika / vícejazyčný web / SEO"},
}
MAX_DISCOUNT_PCT = 10
CARD_STATUS = "internal offer card — owner only, no outreach"
SEVERITY_RANK = {"high": 0, "medium": 1, "low": 2}
# business impact order inside one severity (what a clinic owner feels first)
IMPACT_ORDER = ["http_error", "page_load_failed", "no_visible_cta", "no_online_booking", "mobile_horizontal_overflow",
                "no_viewport_meta", "broken_links", "no_https", "single_language_foreign_audience", "slow_load",
                "heavy_page", "small_text_mobile", "small_tap_targets", "mixed_content", "outdated_copyright",
                "missing_title", "missing_meta_description", "missing_h1", "images_without_alt", "missing_lang",
                "console_errors", "many_requests"]


class DiscountRefused(ValueError):
    pass


class NothingRealToOffer(ValueError):
    pass


def apply_discount(price_czk: int, discount_pct: float) -> int:
    if discount_pct < 0 or discount_pct > MAX_DISCOUNT_PCT:
        raise DiscountRefused(f"discount {discount_pct}% outside 0..{MAX_DISCOUNT_PCT}% — owner decision required")
    return int(round(price_czk * (100 - discount_pct) / 100.0))


def choose_tier(signals: dict) -> tuple[str, list[str]]:
    """Rules (owner plan §16): clinic scale / multilingual / foreign audience -> premium;
    >5 internal pages or e-shop -> business; else landing. Reasons cite the measured signals."""
    reasons: list[str] = []
    if signals.get("clinic_scale_keywords"):
        reasons.append(f"clinic-scale words on page: {signals['clinic_scale_keywords']}")
    if len(signals.get("language_versions") or []) >= 2:
        reasons.append(f"language versions: {signals['language_versions']}")
    if signals.get("foreign_audience_keywords"):
        reasons.append(f"page addresses foreign patients: {signals['foreign_audience_keywords']}")
    if reasons:
        return "premium", reasons
    pages = int(signals.get("internal_pages") or 0)
    if pages > 5:
        reasons.append(f"internal pages linked from home: {pages} (>5)")
    if signals.get("eshop_keywords"):
        reasons.append(f"e-shop words: {signals['eshop_keywords']}")
    if reasons:
        return "business", reasons
    return "landing", [f"internal pages: {pages} (<=5), no clinic-scale/multilingual/e-shop signals"]


def strongest(findings: list[Finding], n: int = 3) -> list[Finding]:
    def key(f: Finding):
        pos = IMPACT_ORDER.index(f.id) if f.id in IMPACT_ORDER else len(IMPACT_ORDER)
        return (SEVERITY_RANK[f.severity], pos)
    return sorted(findings, key=key)[:n]


def build_offer_card(audit: dict, business_name: str, *, discount_pct: float = 0) -> dict:
    findings = validate_findings(audit.get("findings") or [])  # re-check: no finding without evidence
    if not findings:
        raise NothingRealToOffer("no measured problems — nothing honest to offer")
    tier, reasons = choose_tier(audit.get("signals") or {})
    price = TIERS[tier]["price_czk"]
    final = apply_discount(price, discount_pct)
    top = strongest(findings)
    return {
        "status": CARD_STATUS,
        "business_name": business_name,
        "website": audit.get("final_url") or audit.get("url"),
        "audited_at": audit.get("audited_at"),
        "tier": tier,
        "tier_label": TIERS[tier]["label"],
        "tier_reasons": reasons,
        "price_czk": price,
        "discount_pct": discount_pct,
        "max_discount_pct": MAX_DISCOUNT_PCT,
        "final_price_czk": final,
        "first_step": "free audit + first-screen mockup (owner decides whether/how to contact)",
        "top_findings": [f.to_dict() for f in top],
        "all_finding_ids": [f.id for f in findings],
        "severity_counts": {s: sum(1 for f in findings if f.severity == s) for s in SEVERITY_RANK},
        "screenshots": audit.get("screenshots") or [],
        "outreach": None,
        "created_at": _dt.datetime.now(_dt.timezone.utc).isoformat(timespec="seconds"),
    }


def _md(card: dict) -> str:
    lines = [
        f"# Offer card — {card['business_name']}",
        "",
        f"**Status:** {card['status']}",
        "",
        f"- Web: {card['website']} (audit {card['audited_at']})",
        f"- Tier: **{card['tier_label']}** — {card['price_czk']:,} Kč".replace(",", " "),
        f"- Sleva: {card['discount_pct']} % (max {card['max_discount_pct']} %) → {card['final_price_czk']:,} Kč".replace(",", " "),
        f"- Důvod tieru: {'; '.join(card['tier_reasons'])}",
        f"- První krok: {card['first_step']}",
        "",
        "## Nejsilnější změřené problémy",
        "",
    ]
    for i, f in enumerate(card["top_findings"], 1):
        ev = json.dumps(f["evidence"], ensure_ascii=False)
        lines += [f"{i}. `{f['id']}` ({f['severity']}) — evidence: `{ev}`", f"   - Oprava: {f['fix_suggestion']}"]
    lines += ["", f"Všechny nálezy: {', '.join(card['all_finding_ids'])}", "",
              "_Žádný text pro oslovení se negeneruje (rozhodnutí vlastníka 24.10)._", ""]
    return "\n".join(lines)


def write_offer_card(card: dict, cards_dir: str | Path) -> tuple[Path, Path]:
    d = Path(cards_dir)
    d.mkdir(parents=True, exist_ok=True)
    slug = re.sub(r"[^a-z0-9]+", "-", card["business_name"].lower()).strip("-")[:50] or "lead"
    pj, pm = d / f"{slug}.offer.json", d / f"{slug}.offer.md"
    pj.write_text(json.dumps(card, ensure_ascii=False, indent=2), encoding="utf-8")
    pm.write_text(_md(card), encoding="utf-8")
    return pj, pm
