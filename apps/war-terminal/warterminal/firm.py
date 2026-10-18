"""The owner's firm data, entered in the browser popup on every opening of the terminal.

PRIVACY CONTRACT (tests/test_firm.py enforces it):
  * the server NEVER stores firm data: no database row, no kv, no audit payload, no snapshot, no log line;
  * it arrives per request in the X-Firm-Profile header (base64url JSON) over the loopback connection and lives for that request only;
  * it is never passed to the translation gateway, the AI classifier or any export; assessments that get saved are stored BEFORE firm
    facts are attached, and the attached `firm_checks` carry no identifiers (no name, no registration or VAT number, no address).
"""
from __future__ import annotations

import base64
import json
import re
from decimal import Decimal

HEADER = "x-firm-profile"
MAX_HEADER = 8192
CERTS = ("iso9001", "iso27001", "nato_aqap", "facility_clearance")
LICENCES = ("dual_use", "military_export", "brokering")
CPV_RX = re.compile(r"^\d{2,8}(-\d)?$")
CCY_EU = {"AT", "BE", "BG", "HR", "CY", "CZ", "DK", "EE", "FI", "FR", "DE", "GR", "HU", "IE", "IT", "LV", "LT", "LU", "MT", "NL", "PL", "PT", "RO", "SK", "SI", "ES", "SE",
          "IS", "LI", "NO"}


class FirmError(ValueError):
    """Carries field names only, never the entered values."""

    def __init__(self, fields: list[str]):
        super().__init__("invalid firm fields: " + ", ".join(fields))
        self.fields = fields


def ico_valid(value: str) -> bool:
    """Czech IČO: 8 digits, the last one is a mod-11 check digit (weights 8..2 over the first seven)."""
    v = re.sub(r"\s+", "", value or "")
    if not re.fullmatch(r"\d{8}", v):
        return False
    s = sum(int(c) * w for c, w in zip(v[:7], range(8, 1, -1)))
    r = s % 11
    check = 1 if r == 0 else 0 if r == 1 else 11 - r
    return check == int(v[7])


def dic_valid(value: str) -> bool:
    """Czech DIČ: CZ + 8 digits (legal entity: the 8 digits are a valid IČO) or CZ + 9-10 digits (natural person / birth number)."""
    v = re.sub(r"\s+", "", value or "").upper()
    m = re.fullmatch(r"CZ(\d{8,10})", v)
    if not m:
        return False
    d = m.group(1)
    return ico_valid(d) if len(d) == 8 else True


def vat_valid(country: str, value: str) -> bool:
    v = re.sub(r"\s+", "", value or "").upper()
    if country == "CZ":
        return dic_valid(v)
    return bool(re.fullmatch(r"[A-Z]{2}[A-Z0-9]{2,12}", v))


def reg_valid(country: str, value: str) -> bool:
    v = (value or "").strip()
    if country == "CZ":
        return ico_valid(v)
    return bool(re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9 ./\-]{2,29}", v))


def _num(d: dict, key: str, bad: list[str], *, integer=False, lo=0.0):
    v = d.get(key)
    if v is None or v == "":
        return None
    try:
        x = float(v)
    except (TypeError, ValueError):
        bad.append(key)
        return None
    if x != x or x < lo or x == float("inf"):
        bad.append(key)
        return None
    if integer:
        if x != int(x):
            bad.append(key)
            return None
        return int(x)
    return x


def clean(raw: dict) -> dict:
    """Validate the popup payload. Raises FirmError(fields) listing field names only. Unknown keys are dropped."""
    if not isinstance(raw, dict):
        raise FirmError(["firm"])
    bad: list[str] = []
    s = lambda k, n=200: str(raw.get(k) or "").strip()[:n]            # noqa: E731
    country = s("country", 2).upper()
    if not re.fullmatch(r"[A-Z]{2}", country):
        bad.append("country")
    name = s("legal_name")
    if len(name) < 2:
        bad.append("legal_name")
    reg = re.sub(r"\s+", "", s("reg_no", 30)) if country == "CZ" else s("reg_no", 30)
    if not reg_valid(country, reg):
        bad.append("reg_no")
    vat = re.sub(r"\s+", "", s("vat_no", 20)).upper()
    if vat and not vat_valid(country, vat):
        bad.append("vat_no")
    cpv = [str(x).strip() for x in (raw.get("cpv") or []) if str(x).strip()]
    if any(not CPV_RX.match(c) for c in cpv):
        bad.append("cpv")
    certs = [c for c in (raw.get("certs") or []) if c in CERTS]
    lic = [c for c in (raw.get("licences") or []) if c in LICENCES]
    out = {"legal_name": name, "country": country, "reg_no": reg, "vat_no": vat, "address": s("address", 300), "cpv": sorted(set(cpv))[:40],
           "turnover_eur": _num(raw, "turnover_eur", bad), "staff": _num(raw, "staff", bad, integer=True), "certs": certs, "licences": lic,
           "references": _num(raw, "references", bad, integer=True), "guarantee_eur": _num(raw, "guarantee_eur", bad)}
    if bad:
        raise FirmError(sorted(set(bad)))
    return out


def decode_header(value: str | None) -> dict | None:
    """X-Firm-Profile -> validated firm dict, None when absent. Raises FirmError."""
    if not value:
        return None
    if len(value) > MAX_HEADER:
        raise FirmError(["firm"])
    try:
        raw = json.loads(base64.urlsafe_b64decode(value + "=" * (-len(value) % 4)).decode("utf-8"))
    except Exception as exc:                                         # noqa: BLE001 - never echo the payload
        raise FirmError(["firm"]) from exc
    return clean(raw)


def to_profile(firm: dict) -> dict:
    """Map the popup data to the profile keys that rules.py / scoring.py read. Nothing is defaulted: a missing value stays missing."""
    return {"name": firm["legal_name"], "country": firm["country"], "registered": True, "vat_id": firm["vat_no"] or None,
            "cpv_capabilities": firm["cpv"], "certificates": firm["certs"], "licences": firm["licences"], "turnover_eur": firm["turnover_eur"],
            "staff": firm["staff"], "references": firm["references"], "guarantee_eur": firm["guarantee_eur"]}


def checks(tender: dict, firm: dict, value_eur: Decimal | None, controlled: bool) -> list[dict]:
    """Facts that compare the tender with the entered firm data. No identifiers are returned. A missing input gives NO_DATA, never a guess."""
    out = []
    t = firm.get("turnover_eur")
    if value_eur is None:
        out.append({"id": "turnover", "status": "NO_DATA", "note": "стоимость тендера в EUR неизвестна (нет суммы или курса)"})
    elif t is None:
        out.append({"id": "turnover", "status": "NO_DATA", "note": "оборот фирмы не введён"})
    else:
        ratio = (value_eur / Decimal(str(t)) * 100) if t else None
        out.append({"id": "turnover", "status": "INFO", "ratio_pct": None if ratio is None else float(ratio.quantize(Decimal("0.1"))),
                    "note": "доля стоимости тендера в годовом обороте фирмы"})
    g = firm.get("guarantee_eur")
    if value_eur is None or g is None:
        out.append({"id": "guarantee", "status": "NO_DATA", "note": "нужны стоимость тендера и лимит банковской гарантии"})
    else:
        out.append({"id": "guarantee", "status": "INFO", "ratio_pct": float((Decimal(str(g)) / value_eur * 100).quantize(Decimal("0.1"))),
                    "note": "лимит банковской гарантии как доля стоимости тендера"})
    if controlled:
        have = firm.get("licences") or []
        miss = [c for c in ("military_export",) if c not in have]
        out.append({"id": "licences", "status": "MISSING" if miss else "DECLARED",
                    "note": "для военной номенклатуры лицензия на экспорт не заявлена" if miss else "лицензия на экспорт заявлена (подтверждается документом, не этим приложением)"})
        sec = [c for c in ("nato_aqap", "facility_clearance") if c in (firm.get("certs") or [])]
        out.append({"id": "security_certs", "status": "DECLARED" if sec else "MISSING", "note": "сертификаты AQAP / допуск к секретным работам: заявлены" if sec
                    else "AQAP / допуск к секретным работам не заявлены"})
    return out
