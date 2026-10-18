import pathlib
import sys
from datetime import date

import pytest
from fastapi.testclient import TestClient

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))

from fixtures import fake_get, fake_post  # noqa: E402
from warterminal.api import create_app  # noqa: E402

@pytest.fixture(autouse=True)
def _no_real_bossman(monkeypatch):
    """Tests must never reach the owner's running Bossman (:8801) through the translation gateway."""
    monkeypatch.setenv("WAR_TERMINAL_BOSSMAN_URL", "off")


TOK = {"viewer": "tv", "analyst": "ta", "compliance": "tc", "owner": "to"}
TODAY = date(2026, 10, 17)


def H(role):
    return {"Authorization": f"Bearer {TOK[role]}"}


# A valid test firm (Czech IČO 45274649 passes the mod-11 check). Test data only; the product never ships a default firm.
TEST_FIRM = {"legal_name": "Testovaci Firma s.r.o.", "country": "CZ", "reg_no": "45274649", "vat_no": "CZ45274649", "address": "Testovaci 1, Praha",
             "cpv": ["09", "15", "60"], "turnover_eur": 2_000_000, "staff": 12, "certs": ["iso9001"], "licences": [], "references": 2, "guarantee_eur": 100_000}
NOFIRM = object()


def firm_header(firm):
    import base64
    import json
    return {"X-Firm-Profile": base64.urlsafe_b64encode(json.dumps(firm, ensure_ascii=False).encode("utf-8")).decode("ascii").rstrip("=")}


SEED_ROWS = [
    ("CZ", "Dodavka motorove nafty pro obecni vozidla", "fuel_energy", ["09134200"], 38000, "EUR", 40),
    ("CZ", "Potravinove balicky pro socialni sluzby", "food", ["15800000"], 52000, "EUR", 21),
    ("CZ", "Silnicni nakladni doprava, ramcova smlouva", "logistics", ["60100000"], 240000, "EUR", 33),
    ("DE", "Werkskantine Lebensmittel", "food", ["15000000"], 95000, "EUR", 12),
    ("PL", "Uslugi spedycyjne", "logistics", ["63520000"], None, None, 28),
    ("EU", "Specialised electronics, dual-use review", "security_defence", ["35000000"], 500000, "EUR", 45),
    ("CZ", "Expired example", "logistics", ["60000000"], 30000, "EUR", -3),
]


class Env:
    def __init__(self, tmp_path, live=True, ted=None):
        self.app = create_app(tmp_path, live=live, tokens=TOK, post=fake_post(ted=ted), get=fake_get, today=TODAY)
        self.c = TestClient(self.app)
        self.store = self.app.state.store

    def req(self, method, path, role="owner", firm=TEST_FIRM, **kw):
        """firm=TEST_FIRM sends the popup data header (default); firm=None = view-only (no firm entered)."""
        headers = dict(H(role)) if role else {}
        if firm is not None:
            headers.update(firm_header(firm))
        return self.c.request(method, "/api/v1" + path, headers=headers, **kw)

    def seed(self, rows=None):
        """Insert provenance-bearing tenders directly (the real connectors need network). Test data in a temp DB only."""
        n = 0
        for i, (ctry, title, cat, cpv, val, cur, days) in enumerate(rows or SEED_ROWS, 1):
            nid = f"T-{i:03d}"
            deadline = f"{TODAY.fromordinal(TODAY.toordinal() + days).isoformat()}T23:59:59Z"
            doc = {"id": f"test:{nid}", "official_notice_id": nid, "title": title, "jurisdiction": "EU" if ctry in ("CZ", "DE", "PL", "EU") else ctry,
                   "country": ctry if ctry != "EU" else "", "category": cat, "status": "open" if days >= 0 else "closed", "deadline": deadline,
                   "estimated_value": val, "currency": cur, "source_url": f"https://ted.europa.eu/en/notice/-/detail/{nid}", "source_id": "ted",
                   "retrieved_at": "2026-10-17T00:00:00+00:00", "published_at": TODAY.isoformat(), "source_version": "t", "checksum": nid, "data_quality": "ok",
                   "buyer": "Test buyer", "cpv": cpv}
            n += self.store.upsert_tender(doc)[2]
        return n

    def ingest_ted(self):
        r = self.req("POST", "/ingest", "analyst", json={"source": "ted"})
        assert r.status_code == 200, r.text
        return r.json()

    def key(self, pub):
        return f"EU:{pub}"

    def tender(self, pub):
        return self.req("GET", f"/tenders/EU:{pub}", "viewer").json()


@pytest.fixture
def env(tmp_path):
    return Env(tmp_path)
