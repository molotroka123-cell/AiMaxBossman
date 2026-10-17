"""Czech ISVZ open-data connector (warterminal.connectors.cz). Offline: real records from VZ-09-2026.zip in fixtures_cz.json."""
import io
import json
import pathlib
import zipfile
from datetime import date

import jsonschema
import pytest

from warterminal.connectors import cz

HERE = pathlib.Path(__file__).resolve().parent
FIX = json.loads((HERE / "fixtures_cz.json").read_text(encoding="utf-8"))
SCHEMA = json.loads((HERE.parent / "schemas" / "tender.schema.json").read_text(encoding="utf-8"))
TODAY = date(2026, 10, 17)
SEPT = cz.FILE_URL.format(m=9, y=2026)


def rec(label):
    return FIX["data"][FIX["labels"].index(label)]


def raw_of(*labels):
    return {"files": [{"url": SEPT, **FIX["header"]}], "data": [rec(l) for l in labels]}


def doc(label):
    return cz.normalize_all(raw_of(label), "2026-10-17T08:00:00Z", "snap", TODAY)[0]


def month_zip(records, pretty=False):
    body = {**FIX["header"], "data": records}
    text = json.dumps(body, ensure_ascii=False, indent=1 if pretty else None, separators=None if pretty else (",", ":"))
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as z:
        z.writestr("VZ-09-2026.json", text)
    return buf.getvalue()


class FakeGet:
    def __init__(self, files):
        self.files, self.calls = files, []

    def __call__(self, url):
        self.calls.append(url)
        if url not in self.files:
            raise RuntimeError("404")                       # stands in for httpx.HTTPStatusError
        return self.files[url]


def test_open_nen_notice_fields():
    d = doc("open_nen")
    assert d["id"] == "cz:RVZ2600165495" and d["official_notice_id"] == "RVZ2600165495"
    assert d["title"] == "Vedolizumab II."
    assert (d["jurisdiction"], d["country"], d["demo"]) == ("CZ", "CZ", False)
    assert d["status"] == "open" and d["deadline"] == "2026-11-05T23:59:59Z"
    assert d["estimated_value"] == 35615320.0 and d["currency"] == "CZK"
    assert d["cpv"] == ["33652300"] and d["category"] == "medical"
    assert d["source_url"] == "https://nen.nipez.cz/verejne-zakazky/detail-zakazky/N006-26-V00028773"
    assert d["source_id"] == "cz_nen" and d["published_at"] == "2026-09-08"
    assert d["buyer"].startswith("Ústřední vojenská nemocnice")
    assert d["data_quality"] == "ok" and d["snapshot_sha256"] == "snap" and d["retrieved_at"] == "2026-10-17T08:00:00Z"
    assert "2.10.2" in d["source_version"] and "Z2026-048568" in d["source_version"]


def test_same_notice_is_open_before_and_closed_after_deadline():
    r = raw_of("open_nen")
    assert cz.normalize_all(r, "t", "s", date(2026, 11, 5))[0]["status"] == "open"
    assert cz.normalize_all(r, "t", "s", date(2026, 11, 6))[0]["status"] == "closed"


def test_cancelled_closed_unknown():
    assert doc("cancelled")["status"] == "cancelled"
    assert doc("closed")["status"] == "closed"
    u = doc("unknown")
    assert u["status"] == "unknown" and u["deadline"] is None and u["data_quality"] == "partial"


def test_vvz_record_links_to_vvz_page():
    d = doc("vvz")
    assert d["source_id"] == "cz_vvz"
    assert d["source_url"] == "https://vvz.nipez.cz/formulare-zakazky/Z2022-039983"


def test_lot_sum_when_no_total_value():
    d = doc("partsum")
    assert d["estimated_value"] == 22800000.0 and d["currency"] == "CZK"
    assert d["value_basis"] == "sum of lot estimates excl. VAT"


def test_zero_value_is_not_a_value():
    r = json.loads(json.dumps(rec("open_nen")))
    r["verejna_zakazka"]["predpokladana_hodnota_bez_DPH"] = 0.0
    for p in r["verejna_zakazka"]["casti_verejne_zakazky"]:
        p.pop("predpokladana_hodnota_casti_bez_DPH", None)
    d = cz.normalize(r, "t", "s", TODAY)
    assert d["estimated_value"] is None and d["data_quality"] == "partial"


def test_all_fixture_docs_match_schema_and_checksums_are_stable():
    docs = cz.normalize_all({"files": [{"url": SEPT, **FIX["header"]}], "data": FIX["data"]}, "2026-10-17T08:00:00Z", "s", TODAY)
    assert len(docs) == len(FIX["data"]) and len({d["id"] for d in docs}) == len(docs)
    for d in docs:
        jsonschema.validate(d, SCHEMA, format_checker=jsonschema.FormatChecker())
        assert d["id"].startswith("cz:RVZ") and d["source_id"] in ("cz_nen", "cz_vvz")
        assert d["source_url"].startswith("https://")
    again = cz.normalize_all({"files": [{"url": SEPT}], "data": FIX["data"]}, "x", "y", TODAY)
    assert [d["checksum"] for d in docs] == [d["checksum"] for d in again]


def test_months_for_window():
    assert cz.months_for(30, TODAY) == [(2026, 10), (2026, 9), (2026, 8)]
    assert cz.months_for(5, date(2026, 1, 3)) == [(2026, 1), (2025, 12), (2025, 11)]


@pytest.mark.parametrize("pretty", [False, True])
def test_fetch_streams_zip_and_keeps_open_or_recent(pretty):
    get = FakeGet({SEPT: month_zip(FIX["data"], pretty)})
    req, raw = cz.fetch(get, days=30, limit=100, today=TODAY)
    assert get.calls == [cz.FILE_URL.format(m=10, y=2026), SEPT]  # October not published yet -> no fallback to August
    assert req["files"] == [SEPT] and req["missing"][0]["url"].endswith("VZ-10-2026.zip")
    f = raw["files"][0]
    assert f["records_in_file"] == len(FIX["data"]) and f["verze"] == "2.10.2" and len(f["sha256"]) == 64
    assert [r["verejna_zakazka"]["identifikator_NIPEZ"] for r in raw["data"]] == ["RVZ2600165495"]  # only the open one
    docs = cz.normalize_all(raw, "t", "s", TODAY)
    assert docs[0]["status"] == "open"


def test_fetch_recent_window_and_limit():
    recent = json.loads(json.dumps(rec("closed")))
    for p in recent["verejna_zakazka"]["casti_verejne_zakazky"]:
        p["zadavaci_postup_pro_cast"]["datum_zahajeni_zadavaciho_postupu"] = "2026-09-25T00:00:00.0000000"
    get = FakeGet({SEPT: month_zip([recent, rec("open_nen"), rec("cancelled")])})
    _, raw = cz.fetch(get, days=30, limit=10, today=TODAY)
    ids = [r["verejna_zakazka"]["identifikator_NIPEZ"] for r in raw["data"]]
    assert ids == ["RVZ2600165495", "RVZ2400007949"]          # open first, then recently started; old cancelled dropped
    _, raw1 = cz.fetch(FakeGet({SEPT: month_zip([recent, rec("open_nen")])}), days=30, limit=1, today=TODAY)
    assert [r["verejna_zakazka"]["identifikator_NIPEZ"] for r in raw1["data"]] == ["RVZ2600165495"]


def test_fetch_falls_back_to_previous_month_and_skips_non_zip():
    oct3 = date(2026, 10, 3)
    get = FakeGet({cz.FILE_URL.format(m=10, y=2026): b"<html>404</html>", SEPT: month_zip(FIX["data"])})
    req, raw = cz.fetch(get, days=1, limit=5, today=oct3)
    assert req["files"] == [SEPT]
    assert req["missing"] == [{"url": cz.FILE_URL.format(m=10, y=2026), "error": "not a zip"}]


def test_fetch_nothing_published():
    req, raw = cz.fetch(FakeGet({}), days=30, limit=5, today=TODAY)
    assert raw["data"] == [] and raw["files"] == [] and len(req["missing"]) == 3
    assert cz.normalize_all(raw, "t", "s", TODAY) == []


def test_pruned_records_drop_bulky_subtrees():
    r = {"historie_lhut": [1], "verejna_zakazka": {"casti_verejne_zakazky": [{"zadavaci_postup_pro_cast": {"ucastnici": [1], "lhuty": [2]}}]}}
    p = cz._prune(r)
    assert "historie_lhut" not in p
    assert p["verejna_zakazka"]["casti_verejne_zakazky"][0]["zadavaci_postup_pro_cast"] == {"lhuty": [2]}
    assert r["verejna_zakazka"]["casti_verejne_zakazky"][0]["zadavaci_postup_pro_cast"]["ucastnici"] == [1]  # input untouched
