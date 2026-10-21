"""Prozorro connector: parser on trimmed real API answers (tests/fixtures_prozorro.json, fetched 2026-10-21), feed paging and
cursor, dedupe, polite backoff, relevance filter, API ingest/resume and the UI source badge. No network: fake transport only."""
import copy
import json
import pathlib
from datetime import date

import pytest
from fastapi.testclient import TestClient

from conftest import H, TOK
from warterminal.api import create_app
from warterminal.connectors import prozorro as pz

FIX = json.loads((pathlib.Path(__file__).parent / "fixtures_prozorro.json").read_text(encoding="utf-8"))
DET = {d["tenderID"]: d for d in FIX["details"]}
TODAY = date(2026, 10, 21)
RET = "2026-10-21T20:00:00+00:00"
DRONE, MAGS, DECON, POWER, PORK = ("UA-2026-10-20-013721-a", "UA-2026-10-16-012729-a", "UA-2026-10-14-011385-a",
                                   "UA-2026-10-21-009863-a", "UA-2026-10-21-009843-a")
RELEVANT, OTHER = (DRONE, MAGS, DECON), (POWER, PORK)


def norm(tid, today=TODAY, **patch):
    t = copy.deepcopy(DET[tid])
    t.update(patch)
    return pz.normalize(t, RET, "snap1", today)


# ---------------------------------------------------------------- parser on real fixtures
def test_real_feed_page_shape():
    page = FIX["feed_page"]
    assert isinstance(page["data"], list) and page["data"]
    assert {"id", "tenderID", "dateModified", "status", "procurementMethodType"} <= set(page["data"][0])
    assert page["next_page"]["offset"] and "/api/2.5/tenders" in page["next_page"]["uri"]


def test_normalize_required_fields_ids_links():
    required = ("id", "official_notice_id", "title", "jurisdiction", "country", "category", "status", "deadline", "estimated_value",
                "currency", "source_url", "source_id", "retrieved_at", "published_at", "source_version", "checksum", "data_quality",
                "buyer", "cpv", "demo", "snapshot_sha256")
    for tid in DET:
        d = norm(tid)
        for k in required:
            assert k in d, (tid, k)
        assert d["id"] == f"prozorro:{tid}" and d["official_notice_id"] == tid
        assert d["source_url"] == f"https://prozorro.gov.ua/tender/{tid}"
        assert d["source_id"] == "prozorro" and d["jurisdiction"] == "UA" and d["country"] == "UA" and d["demo"] is False
        assert d["prozorro_id"] == DET[tid]["id"] and d["title"] == d["title_uk"] == DET[tid]["title"].strip()
        assert d["buyer"] == DET[tid]["procuringEntity"]["name"]
        assert d["buyer_id_scheme"] == "UA-EDR" and d["buyer_id"].isdigit() and len(d["buyer_id"]) == 8      # EDRPOU
        assert all(len(c) == 8 and c.isdigit() for c in d["cpv"])                                           # check digit dropped
        assert d["currency"] == "UAH" and d["estimated_value"] > 0


def test_values_dates_utc_and_methods():
    d = norm(MAGS)
    assert d["estimated_value"] == 500000.0 and d["value_basis"] == "expected value incl. VAT"
    assert d["deadline"] == "2026-10-28T06:00:00Z"                       # 08:00+02:00 (after DST change) -> UTC
    assert d["tender_period"] == {"start": "2026-10-22T05:00:00Z", "end": "2026-10-28T06:00:00Z"}
    assert d["enquiry_period"]["end"] == "2026-10-22T05:00:00Z"
    assert d["procurement_method"] == "belowThreshold" and d["cpv"] == ["35340000"] and d["category"] == "security_defence"
    dr = norm(DRONE)
    assert dr["estimated_value"] == 69600000.0 and dr["value_basis"] == "expected value excl. VAT"
    assert dr["deadline"] == "2026-10-23T06:00:00Z" and dr["enquiry_period"] == {"start": None, "end": None}
    assert dr["procurement_method"] == "priceQuotation" and dr["data_quality"] == "ok"
    assert dr["published_at"] and len(dr["published_at"]) == 10


def test_status_mapping():
    assert norm(MAGS)["status"] == "open"                                # active.enquiries, deadline ahead
    assert norm(DRONE)["status"] == "open"
    assert norm(DRONE, today=date(2026, 10, 24))["status"] == "closed"   # tendering but deadline passed
    assert norm(DRONE, status="cancelled")["status"] == "cancelled"
    for st in ("complete", "active.awarded", "active.auction", "active.qualification", "unsuccessful", "active"):
        assert norm(DRONE, status=st)["status"] == "closed", st
    assert norm(DRONE, status="draft")["status"] == "unknown"
    assert norm(DRONE, tenderPeriod=None)["status"] == "unknown" and norm(DRONE, tenderPeriod=None)["data_quality"] == "partial"


def test_zero_or_missing_value_is_unknown():
    d = norm(DRONE, value={"amount": 0, "currency": "UAH"})
    assert d["estimated_value"] is None and d["currency"] is None and d["data_quality"] == "partial"
    assert norm(DRONE, value=None)["estimated_value"] is None


def test_scrub_drops_personal_contact_data():
    raw = copy.deepcopy(DET[DRONE])
    raw["procuringEntity"]["contactPoint"] = {"name": "Іван Петренко", "email": "x@ukr.net", "telephone": "+380000000000"}
    raw["procuringEntity"]["address"]["streetAddress"] = "вул. Тестова, 1"
    raw["documents"] = [{"title": "doc.pdf", "url": "https://public-docs.prozorro.gov.ua/x"}]
    raw["bids"] = [{"tenderers": [{"contactPoint": {"email": "y@ukr.net"}}]}]
    text = json.dumps(pz.scrub(raw), ensure_ascii=False)
    for bad in ("contactPoint", "x@ukr.net", "y@ukr.net", "+380", "Петренко", "streetAddress", "documents", "bids"):
        assert bad not in text, bad
    assert "14321726" in text and "Військова частина 9937" in text      # public entity + EDRPOU stay
    assert "contactPoint" not in json.dumps(FIX, ensure_ascii=False) and "@" not in json.dumps(FIX)


# ---------------------------------------------------------------- relevance filter
def test_relevance_on_real_fixtures():
    assert pz.relevance(DET[DRONE])["keywords"] == ["бпла"]              # CPV 34710000 (aircraft) - keyword hit
    assert pz.relevance(DET[MAGS])["cpv"] == ["35340000"]
    assert pz.relevance(DET[DECON])["cpv"] == ["35110000"]
    assert pz.relevance(DET[POWER]) is None and pz.relevance(DET[PORK]) is None


@pytest.mark.parametrize("title,hit", [
    ("Закупівля дронів для підрозділу", "дрон"), ("Комплекс РЕБ «Бульдог»", "реб"), ("засоби захисту від БПЛА", "засоби захисту"),
    ("Радар малої дальності", "радар"), ("Тепловізійний монокуляр", "тепловіз"), ("Bulletproof body armour plates", "body armour"),
    ("Комплекти FPV", "fpv"), ("UAV spare parts", "uav"),
])
def test_relevance_keywords(title, hit):
    r = pz.relevance({"title": title, "items": [{"classification": {"id": "30230000-0"}}]})
    assert r and hit in r["keywords"], (title, r)


@pytest.mark.parametrize("title", ["Ребро свиняче охолоджене", "Требування до послуг", "Хліб житній", "Канцелярські товари"])
def test_relevance_negatives(title):
    assert pz.relevance({"title": title, "items": [{"classification": {"id": "15110000-2"}}]}) is None


def test_buyer_name_alone_is_not_relevant():
    t = copy.deepcopy(DET[PORK])
    t["procuringEntity"]["name"] = "Військова частина А0000"
    assert pz.relevance(t) is None


# ---------------------------------------------------------------- fake transport
class HttpErr(Exception):
    def __init__(self, status, retry_after=None):
        super().__init__(f"HTTP {status}")
        self.status, self.retry_after = status, retry_after


def entry(tid, status="active.tendering", method="aboveThreshold"):
    d = DET.get(tid)
    return {"id": d["id"] if d else tid, "tenderID": tid, "status": status, "procurementMethodType": method, "dateModified": "2026-10-21T10:00:00+03:00"}


class FakeProzorro:
    """pages: offset -> (entries, next_offset). details by hex id. fail: {(url_kind, key): [statuses to raise first]}."""

    def __init__(self, pages, details=None, fail=None):
        self.pages, self.fail, self.calls = pages, fail or {}, []
        self.details = {d["id"]: d for d in (details if details is not None else FIX["details"])}

    def __call__(self, url, params=None):
        key = params["offset"] if params else url.rsplit("/", 1)[1]
        self.calls.append(("feed" if params else "detail", key))
        queue = self.fail.get(key)
        if queue:
            raise HttpErr(queue.pop(0))
        if params:
            assert url == pz.URL and params["opt_fields"] == ",".join(pz.FEED_FIELDS)
            ents, nxt = self.pages.get(key, ([], key))
            return {"data": copy.deepcopy(ents), "next_page": {"offset": nxt}}
        if key not in self.details:
            raise HttpErr(404)
        return {"data": copy.deepcopy(self.details[key])}


def two_pages():
    start = pz.start_offset(pz.DEFAULT_DAYS, TODAY)
    return start, {
        start: ([entry(DRONE), entry("UA-X-reporting", "complete", "reporting"), entry(POWER), entry(MAGS, "active.enquiries", "belowThreshold")], "c2"),
        "c2": ([entry(DECON), entry(PORK), entry(DRONE)], "c3"),       # DRONE modified again during the run
        "c3": ([], "c3"),
    }


# ---------------------------------------------------------------- paging / cursor / dedupe
def test_iter_pages_cursor_prefilter_and_dedupe():
    start, pages = two_pages()
    fake = FakeProzorro(pages)
    out = list(pz.iter_pages(fake, cursor=None, today=TODAY))
    assert start == "1792454400"                                         # 2026-10-20 00:00 UTC: DEFAULT_DAYS back
    assert [p["offset"] for p in out] == [start, "c2", "c3"] and [p["next_offset"] for p in out] == ["c2", "c3", "c3"]
    assert out[-1]["caught_up"] and not out[0]["caught_up"]
    assert [d["tenderID"] for d in out[0]["details"]] == [DRONE, MAGS] and [d["tenderID"] for d in out[1]["details"]] == [DECON]
    assert out[0]["skipped"] == 1 and out[1]["duplicates"] == 1         # reporting not fetched; DRONE fetched once
    details = [k for kind, k in fake.calls if kind == "detail"]
    assert len(details) == len(set(details)) == 5                        # POWER/PORK fetched (biddable) but not kept


def test_iter_pages_resumes_from_stored_cursor_and_refreshes_known():
    _, pages = two_pages()
    fake = FakeProzorro(pages)
    out = list(pz.iter_pages(fake, cursor="c2", today=TODAY, known={DET[PORK]["id"]}))
    assert out[0]["offset"] == "c2" and fake.calls[0] == ("feed", "c2")
    assert {d["tenderID"] for d in out[0]["details"]} == {DECON, DRONE, PORK}   # known tender kept for status refresh


def test_iter_pages_respects_detail_budget_between_pages():
    _, pages = two_pages()
    fake = FakeProzorro(pages)
    out = list(pz.iter_pages(fake, cursor=None, today=TODAY, max_details=1))
    assert len(out) == 1 and out[0]["details_fetched"] == 3              # page finished, then stop
    assert out[0]["next_offset"] == "c2"


# ---------------------------------------------------------------- politeness
class Clock:
    def __init__(self):
        self.t, self.sleeps = 0.0, []

    def sleep(self, s):
        self.sleeps.append(round(s, 3))
        self.t += s

    def now(self):
        return self.t


def test_backoff_on_429_and_5xx_honours_retry_after():
    clk = Clock()
    fake = FakeProzorro({"o": ([], "o")}, fail={"o": [429, 503]})
    g = pz.polite(fake, sleep=clk.sleep, clock=clk.now, base_delay=1.0)
    assert g(pz.URL, {"offset": "o", "limit": 1, "opt_fields": ",".join(pz.FEED_FIELDS)})["data"] == []
    assert clk.sleeps[:2] == [1.0, 2.0]                                   # exponential
    clk2 = Clock()
    seq = [HttpErr(429, retry_after=7), None]

    def flaky(url, params=None):
        e = seq.pop(0)
        if e:
            raise e
        return {"ok": 1}
    assert pz.polite(flaky, sleep=clk2.sleep, clock=clk2.now)(pz.URL) == {"ok": 1}
    assert clk2.sleeps[0] == 7                                            # Retry-After wins


def test_backoff_gives_up_and_does_not_retry_client_errors():
    clk = Clock()
    fake = FakeProzorro({}, fail={"o": [429] * 10})
    g = pz.polite(fake, sleep=clk.sleep, clock=clk.now, retries=3)
    with pytest.raises(HttpErr):
        g(pz.URL, {"offset": "o", "limit": 1, "opt_fields": ",".join(pz.FEED_FIELDS)})
    assert len(fake.calls) == 4
    fake404 = FakeProzorro({}, details=[])
    with pytest.raises(HttpErr):
        pz.polite(fake404, sleep=clk.sleep, clock=clk.now)(f"{pz.URL}/nope")
    assert len(fake404.calls) == 1


def test_httpx_status_error_is_classified():
    import httpx
    req = httpx.Request("GET", pz.URL)
    exc = httpx.HTTPStatusError("x", request=req, response=httpx.Response(429, headers={"Retry-After": "3"}, request=req))
    assert pz._status_of(exc) == (429, 3.0)


def test_rate_limit_two_per_second():
    clk = Clock()
    g = pz.polite(lambda url, params=None: {}, sleep=clk.sleep, clock=clk.now)
    for _ in range(5):
        g(pz.URL)
    assert clk.sleeps == [0.5] * 4                                        # <= 2 requests per second


# ---------------------------------------------------------------- API ingest / resume / filter / status
def app_env(tmp_path, fake):
    app = create_app(tmp_path, live=True, tokens=TOK, get=fake, today=TODAY, sleep=lambda s: None)
    return app, TestClient(app)


def ingest(c, **body):
    return c.post("/api/v1/ingest", headers=H("analyst"), json={"source": "prozorro", **body})


def test_api_ingest_stores_relevant_saves_cursor_and_dedupes(tmp_path):
    _, pages = two_pages()
    app, c = app_env(tmp_path, FakeProzorro(pages))
    r = ingest(c)
    assert r.status_code == 200, r.text
    j = r.json()
    assert j["new"] == 3 and j["relevant"] == 3 and j["pages"] == 3 and j["caught_up"] is True and j["cursor"] == "c3"
    st = app.state.store
    assert st.kv_get("cursor:prozorro") == "c3"
    keys = {t["key"] for t in st.latest_tenders()}
    assert keys == {f"UA:{x}" for x in RELEVANT}
    # second run: same feed from the start again -> nothing new (dedupe by tender id)
    st.kv_set("cursor:prozorro", None)
    j2 = ingest(c).json()
    assert j2["new"] == 0 and j2["updated"] == 0 and j2["duplicates"] == 3                # each relevant tender once per run
    assert st.count_tenders() == {"rows": 3, "keys": 3, "visible_keys": 3}
    ev = [e for e in st.audit_events() if e["event"] == "ingest.prozorro"]
    assert len(ev) == 2 and ev[-1]["payload"]["cursor_to"] == "c3"


def test_api_ingest_changed_tender_gets_new_version(tmp_path):
    _, pages = two_pages()
    fake = FakeProzorro(pages)
    app, c = app_env(tmp_path, fake)
    ingest(c)
    for d in fake.details.values():
        if d["tenderID"] == DRONE:
            d["status"] = "cancelled"
    app.state.store.kv_set("cursor:prozorro", None)
    j = ingest(c).json()
    assert j["updated"] == 1
    t = app.state.store.get_tender(f"UA:{DRONE}")
    assert t["version"] == 2 and t["status"] == "cancelled"


def test_api_ingest_resumes_after_failure(tmp_path):
    start, pages = two_pages()
    decon_id = DET[DECON]["id"]
    fake = FakeProzorro(pages, fail={decon_id: [500] * 10})               # page 2 keeps failing past the retries
    app, c = app_env(tmp_path, fake)
    r = ingest(c)
    assert r.status_code == 502 and r.json()["detail"]["source"] == "prozorro"
    st = app.state.store
    assert st.kv_get("cursor:prozorro") == "c2"                           # page 1 stored, page 2 not
    assert {t["official_notice_id"] for t in st.latest_tenders()} == {DRONE, MAGS}
    assert st.kv_get("error:prozorro")
    fake.fail.clear()
    fake.calls.clear()
    j = ingest(c).json()
    assert fake.calls[0] == ("feed", "c2") and j["new"] == 1               # resumed at page 2
    assert {t["official_notice_id"] for t in st.latest_tenders()} == set(RELEVANT)
    assert st.kv_get("error:prozorro") is None


def test_api_reset_cursor_and_limit(tmp_path):
    _, pages = two_pages()
    fake = FakeProzorro(pages)
    app, c = app_env(tmp_path, fake)
    j = ingest(c, limit=1).json()
    assert j["pages"] == 1 and app.state.store.kv_get("cursor:prozorro") == "c2"   # stops after the page that hit the limit
    fake.calls.clear()
    ingest(c, reset_cursor=True, days=3)
    assert fake.calls[0] == ("feed", pz.start_offset(3, TODAY))


def test_api_source_filter_and_status(tmp_path):
    _, pages = two_pages()
    app, c = app_env(tmp_path, FakeProzorro(pages))
    ingest(c)
    app.state.store.upsert_tender({"id": "ted:1-2026", "official_notice_id": "1-2026", "title": "x", "jurisdiction": "EU", "status": "open",
                                   "source_url": "https://ted.europa.eu/en/notice/1-2026", "source_id": "ted", "retrieved_at": RET, "cpv": []})
    r = c.get("/api/v1/tenders?source=prozorro", headers=H("viewer")).json()
    assert r["total"] == 3 and {i["source_id"] for i in r["items"]} == {"prozorro"}
    assert c.get("/api/v1/tenders", headers=H("viewer")).json()["total"] == 4
    one = next(i for i in r["items"] if i["official_notice_id"] == MAGS)
    assert one["currency"] == "UAH" and one["assessment"]["compliance_status"] in ("REVIEW_REQUIRED", "BLOCKED")   # military goods: never auto-PASS
    src = {s["id"]: s for s in c.get("/api/v1/sources/status", headers=H("viewer")).json()["sources"]}["prozorro"]
    assert src["status"] == "LIVE" and src["last_count"] == 3 and "🇺🇦" in src["name"]


def test_api_rejects_when_live_disabled(tmp_path):
    app = create_app(tmp_path, live=False, tokens=TOK, get=FakeProzorro({}), today=TODAY, sleep=lambda s: None)
    r = TestClient(app).post("/api/v1/ingest", headers=H("analyst"), json={"source": "prozorro"})
    assert r.status_code == 403


# ---------------------------------------------------------------- UI
def test_ui_source_badge_filter_and_trigger():
    ui = pathlib.Path(__file__).resolve().parents[1] / "warterminal" / "ui"
    live, app = (ui / "live.js").read_text(encoding="utf-8"), (ui / "app.js").read_text(encoding="utf-8")
    assert 'prozorro: "Prozorro 🇺🇦"' in live
    assert '{ source: "prozorro" }' in app and '"prozorro"].map((id) => [id, srcName(id)])' in app
    assert 'p.set("source", f.source)' in app
