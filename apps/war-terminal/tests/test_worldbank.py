"""World Bank procurement connector: offline, on a trimmed real API sample (tests/fixtures_worldbank.json)."""
import copy
import json
from datetime import date
from pathlib import Path

from warterminal.connectors import worldbank as wb

FIX = json.loads((Path(__file__).parent / "fixtures_worldbank.json").read_text(encoding="utf-8"))
TODAY = date(2026, 10, 17)
RET = "2026-10-17T12:00:00Z"


def _docs(payload=None):
    return {d["official_notice_id"]: d for d in wb.normalize_all(payload or FIX, RET, "snap123", today=TODAY)}


def test_ids_urls_and_required_fields():
    docs = _docs()
    assert len(docs) == 5
    required = ("id", "official_notice_id", "title", "jurisdiction", "country", "category", "status", "deadline", "estimated_value",
                "currency", "source_url", "source_id", "retrieved_at", "published_at", "source_version", "checksum", "data_quality",
                "buyer", "cpv", "demo", "snapshot_sha256")
    for nid, d in docs.items():
        for k in required:
            assert k in d, (nid, k)
        assert d["id"] == f"wb:{nid}"
        assert d["source_url"] == f"https://projects.worldbank.org/en/projects-operations/procurement-detail/{nid}"
        assert d["jurisdiction"] == "WB" and d["source_id"] == "worldbank"
        assert d["demo"] is False and d["cpv"] == [] and d["snapshot_sha256"] == "snap123"
        assert d["title"] and d["buyer"]
        assert d["status"] in ("open", "closed", "unknown")


def test_status_by_deadline_and_award():
    docs = _docs()
    assert docs["OP00465889"]["status"] == "open"           # IFB, deadline 2026-11-05
    assert docs["OP00465889"]["deadline"] == "2026-11-05T23:59:59Z"
    assert docs["OP00474814"]["status"] == "open"           # REOI, deadline 2026-10-30
    assert docs["OP00473944"]["status"] == "closed"         # REOI, deadline 2026-10-14 already passed
    assert docs["OP00475883"]["status"] == "closed"         # IFB, deadline 2026-10-16 = yesterday
    assert docs["OP00475704"]["status"] == "closed"         # Contract Award
    assert docs["OP00475704"]["deadline"] is None
    # deadline day itself still counts as open
    assert wb.normalize_all(FIX, RET, "s", today=date(2026, 11, 5))[1]["status"] == "open"


def test_no_deadline_non_award_is_unknown():
    p = copy.deepcopy(FIX)
    n = p["procnotices"][1]
    n["notice_type"] = "General Procurement Notice"
    n.pop("submission_deadline_date")
    d = wb.normalize_all(p, RET, "s", today=TODAY)[1]
    assert d["status"] == "unknown" and d["deadline"] is None and d["data_quality"] == "partial"


def test_value_currency_and_none_when_absent():
    docs = _docs()
    assert docs["OP00465889"]["estimated_value"] == 20531616.1 and docs["OP00465889"]["currency"] == "USD"
    assert docs["OP00465889"]["data_quality"] == "ok"
    p = copy.deepcopy(FIX)
    for n in p["procnotices"]:
        n.pop("bid_estimate_amount", None)
        n.pop("bid_currency_code", None)
    for d in wb.normalize_all(p, RET, "s", today=TODAY):
        assert d["estimated_value"] is None and d["currency"] is None and d["data_quality"] == "partial"
    p2 = copy.deepcopy(FIX)
    p2["procnotices"][0]["bid_estimate_amount"] = "0"       # zero estimate = unknown, never a real value of 0
    assert wb.normalize_all(p2, RET, "s", today=TODAY)[0]["estimated_value"] is None


def test_country_published_category_buyer():
    docs = _docs()
    assert docs["OP00473944"]["country"] == "TZ"
    assert docs["OP00475704"]["country"] == "RS"            # WB legacy code YF -> ISO RS
    assert docs["OP00474814"]["country"] == "Western and Central Africa"   # regional code 3W -> name
    assert docs["OP00473944"]["published_at"] == "2026-10-16"
    assert docs["OP00475704"]["category"] == "it_office"    # UNSPSC segment 43
    assert docs["OP00473944"]["category"] == "consultancy"  # UNSPSC 70 not mapped -> procurement group CS
    assert docs["OP00465889"]["category"] == "goods"
    assert docs["OP00475883"]["category"] == "services"     # NC
    assert docs["OP00465889"]["buyer"] == "Peshawar Electric Supply Company"
    assert docs["OP00475704"]["source_version"] == "Contract Award"


def test_checksum_changes_with_content():
    a = _docs()["OP00465889"]["checksum"]
    p = copy.deepcopy(FIX)
    p["procnotices"][1]["submission_deadline_date"] = "2026-11-20T00:00:00Z"
    b = wb.normalize_all(p, RET, "s", today=TODAY)[1]["checksum"]
    assert a and b and a != b


def test_fetch_builds_params_with_fake_get():
    calls = []

    def get(url, params):
        calls.append((url, params))
        return copy.deepcopy(FIX)

    req, raw = wb.fetch(get, query="fuel", rows=5)
    assert calls[0][0] == "https://search.worldbank.org/api/v2/procnotices"
    p = calls[0][1]
    assert p["format"] == "json" and p["rows"] == 5 and p["qterm"] == "fuel" and p["os"] == 0
    assert "id" in p["fl"].split(",") and "submission_deadline_date" in p["fl"].split(",")
    assert "notice_type_exact" not in p and "project_ctry_code" not in p
    assert req["params"] == p and raw["procnotices"]

    wb.fetch(get, rows=5000, notice_type="Invitation for Bids", country="ke")
    p = calls[1][1]
    assert p["rows"] == 1000 and "qterm" not in p
    assert p["notice_type_exact"] == "Invitation for Bids" and p["project_ctry_code"] == "KE"

    wb.fetch(get, country="Guinea")
    assert calls[2][1]["project_ctry_name_exact"] == "Guinea" and calls[2][1]["rows"] == 50


def test_empty_payload():
    assert wb.normalize_all({"procnotices": []}, RET, "s") == []
    assert wb.normalize_all({}, RET, "s") == []
