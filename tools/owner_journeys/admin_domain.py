"""Deterministic domain logic for the SwapMe and Fresh Vibes admin journeys.

EVERYTHING HERE IS FAKE TEST DATA. Rates, limits, fees, opening hours and
services are invented for the harness and are not the businesses' real terms.
Nothing in this module can move money, contact a person or publish anything:
it only validates, computes and appends to local JSONL files under a data dir.

Brands stay distinct:
* ``swapme``   — Prague crypto exchange (Telegram channel); operator review only.
* ``dental``   — Fresh Vibes dental clinic (freshvibes.cz).
* ``beauty``   — Fresh Vibes Beauty aesthetic studio (freshvibesbeauty.cz).
"""
from __future__ import annotations

import hashlib
import json
import os
import re
import threading
from datetime import datetime, timedelta, timezone
from decimal import ROUND_DOWN, ROUND_HALF_UP, Decimal, InvalidOperation
from pathlib import Path
from typing import Any

FAKE_LABEL = "FAKE_TEST_DATA"

# ------------------------------------------------------------------ SwapMe (fake terms)

# price of 1 unit of `src` expressed in `dst` (fixed, invented)
SWAPME_RATES: dict[str, Decimal] = {
    "EUR/USDT": Decimal("1.0800"),
    "USDT/EUR": Decimal("0.9200"),
    "CZK/USDT": Decimal("0.0430"),
    "USDT/CZK": Decimal("23.10"),
    "EUR/BTC": Decimal("0.0000160"),
    "BTC/EUR": Decimal("61500"),
}
SWAPME_LIMITS: dict[str, tuple[Decimal, Decimal]] = {
    "EUR": (Decimal("50"), Decimal("10000")),
    "CZK": (Decimal("1000"), Decimal("250000")),
    "USDT": (Decimal("50"), Decimal("10000")),
    "BTC": (Decimal("0.001"), Decimal("0.2")),
}
EUR_VALUE: dict[str, Decimal] = {"EUR": Decimal("1"), "CZK": Decimal("0.040"),
                                 "USDT": Decimal("0.92"), "BTC": Decimal("61500")}
DECIMALS = {"EUR": 2, "CZK": 2, "USDT": 2, "BTC": 8}
FEE_LOW, FEE_HIGH = Decimal("0.015"), Decimal("0.010")    # <1000 EUR-equiv / >=1000
MIN_FEE_EUR = Decimal("5")
KYC_THRESHOLD_EUR = Decimal("1000")
SWAPME_REQUIRED = ("request_id", "client_ref", "pair", "amount")


def _q(value: Decimal, ccy: str, rounding=ROUND_HALF_UP) -> Decimal:
    return value.quantize(Decimal(1).scaleb(-DECIMALS[ccy]), rounding=rounding)


def swapme_validate(req: dict[str, Any]) -> dict[str, Any]:
    errors: list[str] = []
    flags: list[str] = []
    for f in SWAPME_REQUIRED:
        if req.get(f) in (None, ""):
            errors.append(f"missing_field:{f}")
    pair = str(req.get("pair") or "").upper().replace("-", "/").replace(" ", "")
    if pair and pair not in SWAPME_RATES:
        errors.append(f"unsupported_pair:{pair}")
    amount = None
    if req.get("amount") not in (None, ""):
        try:
            amount = Decimal(str(req["amount"]).replace(",", "."))
            if amount <= 0:
                errors.append("amount_not_positive")
        except InvalidOperation:
            errors.append("amount_not_numeric")
    if amount is not None and pair in SWAPME_RATES and amount > 0:
        src = pair.split("/")[0]
        lo, hi = SWAPME_LIMITS[src]
        if amount < lo:
            errors.append(f"amount_below_min:{lo} {src}")
        if amount > hi:
            errors.append(f"amount_above_max:{hi} {src}")
        if amount * EUR_VALUE[src] >= KYC_THRESHOLD_EUR:
            flags.append("KYC_REQUIRED_TEXT_ONLY")  # a flag for the operator; no identity data processed
    return {"ok": not errors, "errors": errors, "flags": flags, "pair": pair or None,
            "amount": str(amount) if amount is not None else None, "label": FAKE_LABEL}


def swapme_quote(pair: str, amount: str | float | Decimal) -> dict[str, Any]:
    pair = str(pair).upper().replace("-", "/").replace(" ", "")
    v = swapme_validate({"request_id": "q", "client_ref": "q", "pair": pair, "amount": amount})
    if not v["ok"]:
        return {"ok": False, "errors": v["errors"]}
    src, dst = pair.split("/")
    amt = Decimal(v["amount"])
    eur = amt * EUR_VALUE[src]
    pct = FEE_LOW if eur < KYC_THRESHOLD_EUR else FEE_HIGH
    fee = max(amt * pct, MIN_FEE_EUR / EUR_VALUE[src])
    fee = _q(fee, src)
    payout = _q((amt - fee) * SWAPME_RATES[pair], dst, ROUND_DOWN)
    return {"ok": True, "pair": pair, "amount": str(_q(amt, src)), "fee_pct": str(pct), "fee": str(fee),
            "fee_ccy": src, "rate": str(SWAPME_RATES[pair]), "payout": str(payout), "payout_ccy": dst,
            "flags": v["flags"], "label": FAKE_LABEL,
            "note": "indicative quote from a FIXED FAKE rate table; no exchange is executed"}


# ------------------------------------------------------------------ append-only journal

class Journal:
    """Append-only JSONL with a SHA-256 hash chain (tamper-evident), fsync on write."""

    _lock = threading.Lock()

    def __init__(self, path: Path):
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)

    def rows(self) -> list[dict[str, Any]]:
        if not self.path.is_file():
            return []
        return [json.loads(x) for x in self.path.read_text(encoding="utf-8").splitlines() if x.strip()]

    def append(self, kind: str, data: dict[str, Any]) -> dict[str, Any]:
        with self._lock:
            rows = self.rows()
            prev = rows[-1]["hash"] if rows else "0" * 64
            rec = {"seq": len(rows) + 1, "ts": datetime.now(timezone.utc).isoformat(timespec="seconds"),
                   "kind": kind, "data": data, "prev": prev}
            rec["hash"] = hashlib.sha256(json.dumps({k: rec[k] for k in ("seq", "ts", "kind", "data", "prev")},
                                                    sort_keys=True, ensure_ascii=False).encode()).hexdigest()
            with self.path.open("a", encoding="utf-8") as fh:
                fh.write(json.dumps(rec, ensure_ascii=False) + "\n")
                fh.flush()
                os.fsync(fh.fileno())
            return rec

    def verify_chain(self) -> bool:
        prev = "0" * 64
        for i, rec in enumerate(self.rows(), start=1):
            body = {k: rec[k] for k in ("seq", "ts", "kind", "data", "prev")}
            if rec["seq"] != i or rec["prev"] != prev:
                return False
            if hashlib.sha256(json.dumps(body, sort_keys=True, ensure_ascii=False).encode()).hexdigest() != rec["hash"]:
                return False
            prev = rec["hash"]
        return True

    def latest(self, kind: str, key: str, value: Any) -> dict[str, Any] | None:
        for rec in reversed(self.rows()):
            if rec["kind"] == kind and rec["data"].get(key) == value:
                return rec
        return None


# ------------------------------------------------------------------ Fresh Vibes (fake facts)

BRANDS: dict[str, dict[str, Any]] = {
    "dental": {
        "site": "freshvibes.cz", "kind": "dental clinic",
        "facts": {
            "hours": "Opening hours (TEST): Mon-Fri 08:00-18:00, closed on weekends.",
            "services": "Services (TEST): dental check-up, dental hygiene, whitening consultation.",
            "booking": "Bookings (TEST) are confirmed by clinic staff before they are final.",
            "languages": "Languages (TEST): Czech and English.",
        },
        "services": ["dental check-up", "dental hygiene", "whitening consultation"],
        "referral": ("I can't give medical or dental advice. Please contact a licensed dentist; "
                     "for an emergency call 155. Our staff can book you an examination."),
    },
    "beauty": {
        "site": "freshvibesbeauty.cz", "kind": "aesthetic studio",
        "facts": {
            "hours": "Opening hours (TEST): Tue-Sat 10:00-19:00, closed Sun-Mon.",
            "services": "Services (TEST): skin consultation, facial treatment, lash lift.",
            "booking": "Bookings (TEST) are confirmed by studio staff before they are final.",
            "languages": "Languages (TEST): Czech, English and Russian.",
        },
        "services": ["skin consultation", "facial treatment", "lash lift"],
        "referral": ("I can't give medical advice or dosing. Please consult a licensed physician; "
                     "for an emergency call 155. We can book a consultation with our specialist."),
    },
}

FAQ_TOPICS = {
    "hours": ("hour", "open", "close", "when are you", "weekend", "otevír"),
    "services": ("service", "offer", "treatment", "do you do", "procedure", "price list"),
    "booking": ("book", "appointment", "reserve", "reservation", "confirm"),
    "languages": ("language", "english", "czech", "russian", "speak"),
}

MEDICAL_RE = re.compile(
    r"\b(dose|dosage|dosing|mg|milligram|ml\b|units? of|how much .{0,30}(ibuprofen|paracetamol|antibiotic|botox|filler)"
    r"|ibuprofen|paracetamol|antibiotic|amoxicillin|prescri|diagnos|infect|abscess|swelling|swollen|bleeding"
    r"|pain|hurts?|allerg|rash|side effect|pregnan|is it normal|should i take|medication|medicine)\b",
    re.IGNORECASE,
)
DOSAGE_OUT_RE = re.compile(r"\b\d+(?:[.,]\d+)?\s*(mg|ml|milligrams?|units?|tablets?|pills?)\b", re.IGNORECASE)


def medical_guard(text: str) -> dict[str, Any]:
    hit = MEDICAL_RE.search(text or "")
    return {"medical": bool(hit), "trigger": hit.group(0).lower() if hit else None}


def faq_answer(brand: str, question: str) -> dict[str, Any]:
    if brand not in BRANDS:
        return {"ok": False, "status": "UNKNOWN_BRAND"}
    b = BRANDS[brand]
    guard = medical_guard(question)
    if guard["medical"]:
        return {"ok": True, "status": "SAFE_REFERRAL", "answer": b["referral"], "brand": brand,
                "trigger": guard["trigger"]}
    q = (question or "").lower()
    for topic, keys in FAQ_TOPICS.items():
        if any(k in q for k in keys):
            return {"ok": True, "status": "APPROVED_FACT", "fact_id": f"{brand}.{topic}",
                    "answer": b["facts"][topic], "brand": brand, "site": b["site"]}
    return {"ok": True, "status": "NO_APPROVED_FACT", "brand": brand,
            "answer": "No approved fact covers this; forward to the owner, do not improvise."}


def capture_lead(store: Journal, brand: str, lead: dict[str, Any]) -> dict[str, Any]:
    if brand not in BRANDS:
        return {"ok": False, "errors": ["unknown_brand"]}
    errors = [f"missing_field:{f}" for f in ("name", "contact", "interest") if not lead.get(f)]
    if lead.get("consent") is not True:
        errors.append("consent_missing")
    contact = str(lead.get("contact") or "")
    if contact and not (contact.endswith(".invalid") or contact.startswith("+420 000")):
        errors.append("non_test_contact_rejected")  # the harness only accepts obviously fake contacts
    if errors:
        return {"ok": False, "errors": errors}
    lead_id = f"{brand}-lead-{len([r for r in store.rows() if r['kind'] == 'lead']) + 1:03d}"
    store.append("lead", {"lead_id": lead_id, "brand": brand, "interest": lead["interest"],
                          "name": lead["name"], "contact": contact, "label": FAKE_LABEL})
    return {"ok": True, "lead_id": lead_id, "brand": brand}


def find_lead(store: Journal, brand: str, key: str) -> dict[str, Any] | None:
    """Resolve a lead by its id or by the (fake) contact captured for this brand."""
    lead = store.latest("lead", "lead_id", key)
    if lead is None:
        for rec in reversed(store.rows()):
            if rec["kind"] == "lead" and rec["data"].get("contact") == key and rec["data"].get("brand") == brand:
                return rec
    return lead


def request_booking(store: Journal, brand: str, lead_id: str, service: str, slot_iso: str) -> dict[str, Any]:
    if brand not in BRANDS:
        return {"ok": False, "errors": ["unknown_brand"]}
    lead = find_lead(store, brand, lead_id)
    if lead is None:
        return {"ok": False, "errors": ["unknown_lead"]}
    lead_id = lead["data"]["lead_id"]
    if lead["data"]["brand"] != brand:
        return {"ok": False, "errors": ["lead_belongs_to_other_brand"]}
    svc = str(service or "").strip().lower()
    if svc not in BRANDS[brand]["services"]:
        return {"ok": False, "errors": [f"service_not_offered_by_{brand}"]}
    try:
        slot = datetime.fromisoformat(slot_iso)
    except (TypeError, ValueError):
        return {"ok": False, "errors": ["slot_not_iso"]}
    booking_id = f"{brand}-bk-{len([r for r in store.rows() if r['kind'] == 'booking']) + 1:03d}"
    store.append("booking", {"booking_id": booking_id, "brand": brand, "lead_id": lead_id, "service": svc,
                             "slot": slot.isoformat(), "status": "CONFIRMED_BY_OWNER", "label": FAKE_LABEL})
    return {"ok": True, "booking_id": booking_id, "status": "CONFIRMED_BY_OWNER"}


def queue_reminder_draft(store: Journal, booking_id: str) -> dict[str, Any]:
    bk = store.latest("booking", "booking_id", booking_id)
    if not bk or bk["data"]["status"] != "CONFIRMED_BY_OWNER":
        return {"ok": False, "errors": ["booking_not_owner_confirmed"]}
    slot = datetime.fromisoformat(bk["data"]["slot"])
    brand = bk["data"]["brand"]
    draft = {"booking_id": booking_id, "brand": brand, "channel": "NONE_DRAFT_ONLY",
             "send_at": (slot - timedelta(hours=24)).isoformat(), "status": "QUEUED_NOT_SENT",
             "text": f"Reminder (draft, not sent): your {bk['data']['service']} at {BRANDS[brand]['site']} "
                     f"on {slot.strftime('%Y-%m-%d %H:%M')}.", "label": FAKE_LABEL}
    store.append("reminder_draft", draft)
    return {"ok": True, **draft}
