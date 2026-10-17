"""Deterministic legal hard gates. A model never overrides a gate.

Statuses: PASS | BLOCKED | REVIEW_REQUIRED. An unknown critical fact is REVIEW_REQUIRED, never PASS.
The jurisdiction lists below are POLICY SEEDS, not a legal source: they are marked unverified and must be
checked against the official sanctions maps (sanctionsmap.eu, OFAC) by counsel before any live bid support.
"""
from __future__ import annotations

import re
import unicodedata
from datetime import date, datetime, timezone

SEED_VERIFIED_AT = None            # nobody has verified these lists yet; the UI shows this
RESTRICTED_SEED = {"RU", "BY", "IR", "KP", "SY", "CU"}                     # comprehensive restrictions commonly applied
HIGH_RISK_SEED = {"AF", "MM", "VE", "SD", "SS", "LY", "YE", "SO", "CD", "ML", "BF", "NE", "CF", "IQ", "LB", "HT", "ZW", "NI"}
LOW_RISK_SEED = {"AT", "BE", "BG", "HR", "CY", "CZ", "DK", "EE", "FI", "FR", "DE", "GR", "HU", "IE", "IT", "LV", "LT", "LU", "MT",
                 "NL", "PL", "PT", "RO", "SK", "SI", "ES", "SE", "IS", "LI", "NO", "CH", "GB", "US", "CA", "AU", "NZ", "JP"}
EU_EEA = {"AT", "BE", "BG", "HR", "CY", "CZ", "DK", "EE", "FI", "FR", "DE", "GR", "HU", "IE", "IT", "LV", "LT", "LU", "MT", "NL", "PL",
          "PT", "RO", "SK", "SI", "ES", "SE", "IS", "LI", "NO"}
ISO3_TO_2 = {"CZE": "CZ", "DEU": "DE", "POL": "PL", "FRA": "FR", "ESP": "ES", "ITA": "IT", "LTU": "LT", "LVA": "LV", "EST": "EE", "FIN": "FI",
             "SWE": "SE", "NLD": "NL", "BEL": "BE", "AUT": "AT", "PRT": "PT", "ROU": "RO", "BGR": "BG", "HUN": "HU", "SVK": "SK", "SVN": "SI",
             "HRV": "HR", "GRC": "GR", "DNK": "DK", "IRL": "IE", "LUX": "LU", "MLT": "MT", "CYP": "CY", "GBR": "GB", "USA": "US", "NOR": "NO",
             "CHE": "CH", "GIN": "GN", "UKR": "UA", "RUS": "RU", "BLR": "BY", "IRN": "IR", "PRK": "KP", "SYR": "SY", "CUB": "CU"}
CONTROLLED_CPV_PREFIXES = ("353", "354", "355", "356", "357")       # weapons, ammunition, military vessels/vehicles/systems
# EU Common Military List-type goods by name (EN/RU/CZ/DE/PL), incl. body armour (ML13): found live 17.10 — Czech
# "vesta balistická" passed the CPV check because police notices use CPV 35113400/18xxx, not 353.
CONTROLLED_WORDS = ("ammunition", "weapon", "explosive", "missile", "body armour", "body armor", "ballistic", "боеприпас", "бронежилет",
                    "оружи", "náboj", "střelivo", "munice", "zbraň", "balistick", "výbušn", "munition", "waffe", "ballistisch",
                    "amunicj", "broń", "kamizelk")
DUAL_USE_WORDS = ("dual-use", "dual use", "двойного назначения", "double-purpose", "encryption", "night vision", "thermal imag",
                  "drone", "uav", "unmanned", "radar", "military", "ammunition", "weapon", "explosive", "missile")


def country2(raw: str | None) -> str | None:
    if not raw:
        return None
    raw = raw.strip().upper()
    if raw in ISO3_TO_2:
        return ISO3_TO_2[raw]
    if len(raw) == 2:
        return raw
    from . import geo                                   # full Natural Earth table for every other ISO3 / name
    iso3 = geo.to_iso3(raw)
    if not iso3:
        return None
    return next((i2 for i2, i3 in geo.index()["by_iso2"].items() if i3 == iso3), None)


def _gate(gid: str, status: str, reason: str, evidence: str = "") -> dict:
    return {"id": gid, "status": status, "reason": reason, "evidence_needed": evidence}


def is_controlled(tender: dict) -> bool:
    text = f"{tender.get('title', '')} {tender.get('description', '')}".lower()
    return any(c.startswith(CONTROLLED_CPV_PREFIXES) for c in tender.get("cpv", [])) or any(w in text for w in CONTROLLED_WORDS)


def _days_left(deadline: str | None, today: date) -> int | None:
    if not deadline:
        return None
    try:
        return (datetime.fromisoformat(deadline.replace("Z", "+00:00")).date() - today).days
    except ValueError:
        return None


def evaluate_gates(tender: dict, profile: dict | None, reviews: dict[str, dict] | None = None,
                   sanctions: list[str] | None = None, today: date | None = None) -> list[dict]:
    """All gates for one tender. A gate that a human reviewer cleared (signed review) becomes PASS with the signature,
    except hard BLOCKED gates (embargo, expired, cancelled) which a review cannot lift."""
    today = today or datetime.now(timezone.utc).date()
    profile = profile or {}
    gates: list[dict] = []
    ctry = country2(tender.get("country")) or country2(tender.get("jurisdiction"))

    # 1. jurisdiction / sanctions
    if ctry in RESTRICTED_SEED:
        gates.append(_gate("jurisdiction", "BLOCKED", f"{ctry}: ограничения/эмбарго по seed-списку (не верифицирован)", "заключение юриста по санкционной карте"))
    elif ctry in LOW_RISK_SEED:
        gates.append(_gate("jurisdiction", "PASS", f"{ctry}: в seed-списке низкого риска (не верифицирован)"))
    elif ctry:
        gates.append(_gate("jurisdiction", "REVIEW_REQUIRED", f"{ctry}: нет в списке проверенных юрисдикций", "проверка санкций/эмбарго/risk для страны"))
    else:
        gates.append(_gate("jurisdiction", "REVIEW_REQUIRED", "страна исполнения неизвестна", "страна заказчика и места поставки"))

    # 2. controlled / dual-use goods and end-use
    text = f"{tender.get('title', '')} {tender.get('description', '')}".lower()
    if is_controlled(tender):
        gates.append(_gate("controlled_goods", "REVIEW_REQUIRED", "военная номенклатура: только исследование и юридическая проверка, без автоматических действий",
                           "лицензия на экспорт/посредничество, сертификат конечного пользователя, заключение юриста"))
    elif any(w in text for w in DUAL_USE_WORDS):
        gates.append(_gate("controlled_goods", "REVIEW_REQUIRED", "признаки двойного назначения: конечное использование неизвестно", "классификация по списку двойного назначения и end-use"))
    else:
        gates.append(_gate("controlled_goods", "PASS", "признаков контролируемых товаров в названии/CPV нет (проверка по тексту, не по документам)"))

    # 3. status and deadline
    if tender.get("status") in ("closed", "cancelled"):
        gates.append(_gate("deadline", "BLOCKED", f"тендер {tender['status']}", ""))
    else:
        left = _days_left(tender.get("deadline"), today)
        need = int(profile.get("min_prep_days", 7))
        if left is None:
            gates.append(_gate("deadline", "REVIEW_REQUIRED", "срок подачи неизвестен", "срок в документации тендера"))
        elif left < 0:
            gates.append(_gate("deadline", "BLOCKED", "срок подачи прошёл", ""))
        elif left < need:
            gates.append(_gate("deadline", "REVIEW_REQUIRED", f"до срока {left} дн., нужно не меньше {need}", "решение владельца о сжатых сроках"))
        else:
            gates.append(_gate("deadline", "PASS", f"до срока {left} дн."))

    # 4. money facts
    if tender.get("estimated_value") is not None and not tender.get("currency"):
        gates.append(_gate("currency", "REVIEW_REQUIRED", "сумма есть, валюта не указана", "валюта из документации"))
    else:
        gates.append(_gate("currency", "PASS", "валюта указана или сумма не заявлена"))

    # 5. eligibility of OUR company
    if not profile:
        gates.append(_gate("eligibility", "REVIEW_REQUIRED", "профиль компании не заполнен", "профиль компании и допуск"))
    elif not profile.get("registered"):
        gates.append(_gate("eligibility", "REVIEW_REQUIRED", "компания ещё не зарегистрирована (планируемая)", "регистрация компании, VAT ID"))
    elif tender.get("jurisdiction") == "US" and not profile.get("sam_uei"):
        gates.append(_gate("eligibility", "REVIEW_REQUIRED", "для США нужна регистрация SAM.gov (UEI)", "регистрация SAM.gov"))
    elif tender.get("jurisdiction") == "EU" and profile.get("country") not in EU_EEA:
        gates.append(_gate("eligibility", "REVIEW_REQUIRED", "компания вне ЕС/ЕЭЗ: допуск зависит от соглашений", "проверка допуска третьих стран"))
    else:
        gates.append(_gate("eligibility", "PASS", "профиль допускает подачу (по заявленным данным)"))

    # 6. documents / certificates / guarantee: attachments are NOT parsed in v0.1 -> always a human step
    gates.append(_gate("documents", "REVIEW_REQUIRED", "вложения тендера не анализируются автоматически: сертификаты, гарантии, квалификация неизвестны",
                       "чтение документации тендера человеком"))

    # 7. counterparties vs sanctions list
    if not sanctions:
        gates.append(_gate("counterparty_screening", "REVIEW_REQUIRED", "санкционные списки не загружены (NOT_CONNECTED)", "импорт EU/OFAC/UN списка и проверка заказчика и поставщика"))
    else:
        hit = screen_party(tender.get("buyer", ""), sanctions)
        if hit and hit["exact"]:
            gates.append(_gate("counterparty_screening", "BLOCKED", f"заказчик совпал со списком: {hit['entry']}", ""))
        elif hit:
            gates.append(_gate("counterparty_screening", "REVIEW_REQUIRED", f"возможное совпадение (ложное срабатывание?): {hit['entry']}", "ручная сверка идентификаторов"))
        else:
            gates.append(_gate("counterparty_screening", "PASS", "заказчик не найден в загруженном списке"))

    reviews = reviews or {}
    for g in gates:
        r = reviews.get(g["id"])
        if r and r["decision"] == "cleared" and g["status"] == "REVIEW_REQUIRED":
            g.update(status="PASS", reason=f"{g['reason']} -> снято проверкой: {r['reviewer']} ({r['created_at'][:10]}): {r['note']}")
    return gates


def overall(gates: list[dict]) -> str:
    st = {g["status"] for g in gates}
    return "BLOCKED" if "BLOCKED" in st else "REVIEW_REQUIRED" if "REVIEW_REQUIRED" in st else "PASS"


def _norm(s: str) -> set[str]:
    s = unicodedata.normalize("NFKD", s).encode("ascii", "ignore").decode().lower()
    return {t for t in re.split(r"[^a-z0-9]+", s) if len(t) > 1 and t not in ("ltd", "llc", "inc", "gmbh", "sro", "as", "the", "of", "co", "company")}


class SanctionsIndex:
    """Inverted token index over tens of thousands of names: screening one party touches only entries sharing a token."""

    def __init__(self, entries: list[str]) -> None:
        self.entries = entries
        self.tokens = [_norm(e) for e in entries]
        self.postings: dict[str, list[int]] = {}
        for i, toks in enumerate(self.tokens):
            for t in toks:
                self.postings.setdefault(t, []).append(i)

    def __len__(self) -> int:
        return len(self.entries)

    def candidates(self, toks: set[str]):
        seen: set[int] = set()
        for t in toks:
            for i in self.postings.get(t, ()):
                if i not in seen:
                    seen.add(i)
                    yield self.entries[i], self.tokens[i]


def screen_party(name: str, sanctions) -> dict | None:
    """Token overlap against a sanctions list. exact=all tokens of the entry match (BLOCK); partial >=0.6 => human review."""
    a = _norm(name)
    best = None
    pairs = sanctions.candidates(a) if isinstance(sanctions, SanctionsIndex) else ((e, _norm(e)) for e in sanctions)
    for entry, b in pairs:
        if not a or not b:
            continue
        j = len(a & b) / len(a | b)
        if best is None or j > best["score"]:
            best = {"entry": entry, "score": j, "exact": a == b}
    return best if best and best["score"] >= 0.6 else None
