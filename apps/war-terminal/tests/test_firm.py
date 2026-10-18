"""Firm-data popup contract: validation (IČO mod-11), view-only mode, per-request transport, and the PRIVACY negative control:
firm fields never reach the translation / AI paths, the database, the audit log, snapshots, exports or any response that gets stored."""
import json
import pathlib
import re

from fastapi.testclient import TestClient

from conftest import Env, H, TEST_FIRM, TODAY, firm_header, TOK
from fixtures import fake_get, fake_post
from test_translate import FakeBossman
from warterminal import firm as fm
from warterminal.api import create_app

UI = pathlib.Path(__file__).resolve().parents[1] / "warterminal" / "ui"

# unique markers that can be searched for everywhere
MARK = {"legal_name": "ZZQ Secret Holdings s.r.o.", "address": "Zzqova 99, 11000 Praha ZZQADDR", "reg_no": "45274649", "vat_no": "CZ45274649"}
SECRET_FIRM = dict(TEST_FIRM, **MARK, turnover_eur=7_654_321, staff=4321, guarantee_eur=1_234_567)
NEEDLES = ["ZZQ Secret", "ZZQADDR", "Zzqova", "45274649", "7654321", "1234567"]


# ------------------------------------------------------------------ validation
def test_ico_mod11_checksum():
    assert fm.ico_valid("45274649") and fm.ico_valid("00177041") and fm.ico_valid("452 746 49")      # CEZ, Skoda Auto
    assert not fm.ico_valid("45274640") and not fm.ico_valid("4527464") and not fm.ico_valid("abcdefgh") and not fm.ico_valid("")
    # a check digit that would be 0 / 1 by the remainder rule: 25596641 -> sum%11 gives a valid check digit, tampering breaks it
    assert fm.ico_valid("25596641") and not fm.ico_valid("25596642")


def test_dic_and_country_rules():
    assert fm.dic_valid("CZ45274649") and not fm.dic_valid("CZ45274640") and fm.dic_valid("CZ7001011234") and not fm.dic_valid("45274649")
    assert fm.reg_valid("DE", "HRB 12345") and not fm.reg_valid("DE", "!") and not fm.reg_valid("CZ", "HRB 12345")


def test_clean_reports_field_names_only_and_never_echoes_values():
    bad = dict(SECRET_FIRM, reg_no="45274640", vat_no="CZ1", cpv=["xx"], turnover_eur=-5)
    try:
        fm.clean(bad)
    except fm.FirmError as exc:
        assert exc.fields == ["cpv", "reg_no", "turnover_eur", "vat_no"]
        assert not any(n in str(exc) for n in NEEDLES)
    else:
        raise AssertionError("invalid firm accepted")
    ok = fm.clean(SECRET_FIRM)
    assert ok["country"] == "CZ" and ok["staff"] == 4321 and "bogus" not in ok
    assert fm.clean(dict(TEST_FIRM, turnover_eur=None, staff="", references=None, guarantee_eur=None))["turnover_eur"] is None     # optional numbers stay "no data"


def test_validate_endpoint_and_bad_header(env):
    assert env.req("POST", "/firm/validate", "viewer", firm=None, json=SECRET_FIRM).json() == {"valid": True, "fields": []}
    r = env.req("POST", "/firm/validate", "viewer", firm=None, json=dict(SECRET_FIRM, reg_no="45274640")).json()
    assert r == {"valid": False, "fields": ["reg_no"]}
    r = env.c.get("/api/v1/tenders", headers=H("viewer") | {"X-Firm-Profile": "!!not-base64!!"})
    assert r.status_code == 422 and r.json()["detail"]["code"] == "FIRM_INVALID"
    r = env.c.get("/api/v1/tenders", headers=H("viewer") | firm_header(dict(SECRET_FIRM, reg_no="45274640")))
    assert r.status_code == 422 and r.json()["detail"]["fields"] == ["reg_no"] and not any(n in r.text for n in NEEDLES)


# ------------------------------------------------------------------ view-only vs firm entered
def test_view_only_disables_fit_and_finance_and_says_why(env):
    env.ingest_ted()
    lst = env.req("GET", "/tenders", "viewer", firm=None).json()
    assert lst["firm_entered"] is False and all(i["assessment"]["fit_score"] is None for i in lst["items"])
    d = env.req("GET", "/tenders/EU:100001-2026", "viewer", firm=None).json()
    assert d["assessment"]["fit"]["status"] == "NO_FIRM" and d["firm_checks"] is None and d["assessment"]["tier_if_cleared"] is None
    body = {"currency": "EUR", "fx": {"EURUSD": 1.1}, "purchase": 30000, "bid_price": 40000}
    r = env.req("POST", "/finance/estimate", "analyst", firm=None, json=body)
    assert r.status_code == 409 and r.json()["detail"]["code"] == "FIRM_REQUIRED"
    ok = env.req("POST", "/finance/estimate", "analyst", json=body)
    assert ok.status_code == 200 and "firm_context" in ok.json()


def test_entered_firm_drives_fit_and_missing_fields_stay_no_data(env):
    env.ingest_ted()
    full = env.req("GET", "/tenders/EU:100001-2026", "viewer").json()
    assert isinstance(full["assessment"]["fit"]["fit_score"], int) and full["firm_entered"] is True
    comps = full["assessment"]["fit"]["components"]
    assert comps["qualification"]["value"] is not None and "референсов: 2" in comps["experience"]["why"]
    poor = dict(TEST_FIRM, cpv=[], references=None, turnover_eur=None, guarantee_eur=None)
    d = env.req("GET", "/tenders/EU:100001-2026", "viewer", firm=poor).json()
    comps = d["assessment"]["fit"]["components"]
    assert comps["qualification"]["value"] is None and comps["experience"]["value"] is None            # nothing assumed
    assert "qualification" in d["assessment"]["fit"]["unknowns"] and "experience" in d["assessment"]["fit"]["unknowns"]
    assert {c["id"]: c["status"] for c in d["firm_checks"]} == {"turnover": "NO_DATA", "guarantee": "NO_DATA"}


def test_controlled_goods_check_uses_declared_licences_without_identifiers(env):
    env.ingest_ted()
    key = "EU:100005-2026"                                      # ammunition
    none = env.req("GET", f"/tenders/{key}", "viewer").json()["firm_checks"]
    assert {c["id"]: c["status"] for c in none}["licences"] == "MISSING"
    lic = env.req("GET", f"/tenders/{key}", "viewer", firm=dict(SECRET_FIRM, licences=["military_export"], certs=["nato_aqap"])).json()
    st = {c["id"]: c["status"] for c in lic["firm_checks"]}
    assert st["licences"] == "DECLARED" and st["security_certs"] == "DECLARED"
    assert not any(n in json.dumps(lic) for n in NEEDLES)


# ------------------------------------------------------------------ PRIVACY negative control
class SpyGateway(FakeBossman):
    """Records the full message content of every translation call."""

    def __init__(self):
        super().__init__()
        self.sent = []

    def free_chat(self, model_id, messages, max_tokens):
        self.sent.append(json.dumps(messages, ensure_ascii=False))
        return super().free_chat(model_id, messages, max_tokens)


class SpyChat:
    def __init__(self):
        self.seen = []

    def __call__(self, messages):
        self.seen.append(json.dumps(messages, ensure_ascii=False))
        return '{"tier": "MEDIUM", "rationale": "ok", "risks": []}'


def _all_stored_text(app) -> str:
    return "\n".join(app.state.store.db.iterdump())


def test_firm_data_never_reaches_translation_ai_storage_or_exports(tmp_path):
    gw, chat = SpyGateway(), SpyChat()
    app = create_app(tmp_path, live=True, tokens=TOK, post=fake_post(), get=fake_get, today=TODAY, bossman=gw, chat=chat)
    c = TestClient(app)
    hdr = lambda role: H(role) | firm_header(SECRET_FIRM)                       # noqa: E731
    assert c.post("/api/v1/ingest", headers=hdr("analyst"), json={"source": "ted"}).status_code == 200
    items = c.get("/api/v1/tenders", headers=hdr("viewer")).json()["items"]
    for it in items:                                                           # every view with the firm header attached
        k = it["key"]
        assert c.get(f"/api/v1/tenders/{k}", headers=hdr("viewer")).status_code == 200
        assert c.get(f"/api/v1/tenders/{k}/checklist", headers=hdr("viewer")).status_code == 200
        a = c.post("/api/v1/assessments", headers=hdr("analyst"), json={"tender_key": k, "dry_run": False, "use_ai": True})   # saved + AI classifier
        assert a.status_code == 200 and a.json()["firm_entered"] is True
        assert c.post("/api/v1/learning/decisions", headers=hdr("owner"), json={"tender_key": k, "decision": "watch", "reasons": "looks fine"}).status_code == 200
    texts = [i["title"] for i in items]
    for path in ("/api/v1/translate/lookup", "/api/v1/translate"):             # translation requests made while the firm header is attached
        assert c.post(path, headers=hdr("analyst"), json={"texts": texts, "target": "ru"}).status_code == 200
    body = {"currency": "EUR", "fx": {"EURUSD": 1.1}, "purchase": 30000, "bid_price": 40000}
    assert c.post("/api/v1/finance/estimate", headers=hdr("analyst"), json=body).status_code == 200
    assert c.get("/api/v1/learning/dataset", headers=hdr("owner")).status_code == 200
    export = c.get("/api/v1/learning/dataset", headers=hdr("owner")).text

    assert gw.sent and chat.seen, "the translation and AI paths must have been exercised"
    sent = " ".join(chat.seen) + " " + " ".join(gw.sent)
    assert not any(n in sent for n in NEEDLES)
    assert not any(n in export for n in NEEDLES)
    stored = _all_stored_text(app)
    assert not any(n in stored for n in NEEDLES), "firm data leaked into the database (incl. audit, snapshots, kv, assessments, decisions)"
    log_text = "\n".join(json.dumps(e["payload"]) for e in app.state.store.audit_events(500))
    assert not any(n in log_text for n in NEEDLES)


def test_negative_control_the_detector_would_catch_a_leak(tmp_path):
    """If a firm field were put into a translation request, the spy would see it: proves the privacy assertion above can fail."""
    gw = SpyGateway()
    app = create_app(tmp_path, live=True, tokens=TOK, post=fake_post(), get=fake_get, today=TODAY, bossman=gw)
    c = TestClient(app)
    assert c.post("/api/v1/translate", headers=H("analyst"), json={"texts": ["dodávka motorové nafty pro obecní vozidla ZZQADDR " + MARK["address"].split()[0]], "target": "ru"}).status_code == 200
    assert any(n in " ".join(gw.sent) for n in NEEDLES)
    app.state.store.kv_set("leak", MARK["address"])
    assert any(n in _all_stored_text(app) for n in NEEDLES)


def _probe(numbers=(), firm=None):
    import shutil
    import subprocess
    import pytest
    node = shutil.which("node")
    if not node:
        pytest.skip("node is not installed")
    r = subprocess.run([node, str(pathlib.Path(__file__).with_name("probe_firm.js"))], input=json.dumps({"numbers": list(numbers), "firm": firm}),
                       capture_output=True, text=True, encoding="utf-8", timeout=60)
    assert r.returncode == 0, r.stderr
    return json.loads(r.stdout)


def test_browser_validation_matches_the_server_for_many_numbers():
    import random
    rnd = random.Random(7)
    nums = [f"{rnd.randrange(10**8):08d}" for _ in range(400)] + ["45274649", "00177041", "25596641", "45274640", "1234567", "abcdefgh", "452 746 49"]
    out = _probe(nums)
    assert out["ico"] == [fm.ico_valid(n) for n in nums]
    assert sum(out["ico"]) > 20 and not all(out["ico"])                    # both outcomes occur: the comparison is not vacuous
    assert out["regBadCz"] and "mod 11" in out["regBadCz"] and out["regOkCz"] == "" and out["regShortCz"]
    assert out["regDe"] == "" and out["dicBad"] and out["dicOk"] == ""
    assert out["nameShort"] and out["cpvBad"] and out["numNeg"] and out["staffFrac"]


def test_firm_header_is_built_only_for_the_calculation_paths_and_round_trips_to_the_server():
    firm = dict(SECRET_FIRM, legal_name="Příliš žluťoučký kůň s.r.o.")
    out = _probe(firm=firm)
    assert out["headerNoFirm"] == {}                                        # view-only: no header at all
    for path in ("headerTenders", "headerAssess", "headerFinance"):
        assert list(out[path]) == ["X-Firm-Profile"], path
    for path in ("headerTranslate", "headerLookup", "headerAi", "headerSources", "headerSanctions"):
        assert out[path] == {}, path                                        # translation / AI / unrelated calls never carry the firm
    back = fm.decode_header(out["headerTenders"]["X-Firm-Profile"])         # JS base64url(JSON) -> the server's validator
    assert back["legal_name"] == firm["legal_name"] and back["reg_no"] == firm["reg_no"] and back["cpv"] == sorted(firm["cpv"])


def test_ui_wiring_popup_every_load_skip_and_remember_checkbox():
    app_js = (UI / "app.js").read_text(encoding="utf-8")
    firm_js = (UI / "firm.js").read_text(encoding="utf-8")
    assert "X-Firm-Profile" not in app_js and "firmHeaders(path)" in app_js          # header built in one place only
    boot = app_js[app_js.index("async function boot()"):]
    boot = boot[:boot.index("\n}\n")]
    assert boot.index("await FIRM.ask()") < boot.index("render()")                    # popup on EVERY opening, before any data view renders
    assert "FIRM.data = null" in firm_js and "Пропустить — только просмотр" in firm_js     # skip -> view-only, never a trap
    assert 'remember.checked = st.remembered' in firm_js and 'localStorage.setItem(FIRM_SAVED_KEY' in firm_js
    assert 'sessionStorage.setItem(FIRM_KEY' in firm_js and "catch (x)" in firm_js           # session storage by default, every storage call guarded
    assert "if (remember.checked)" in firm_js
    for m in re.finditer(r'POST\("/translate[^)]*\)', app_js):
        assert "FIRM" not in m.group(0)


def test_server_never_stores_the_firm_even_when_asked_to_save_an_assessment(tmp_path):
    e = Env(tmp_path)
    e.ingest_ted()
    r = e.req("POST", "/assessments", "analyst", firm=SECRET_FIRM, json={"tender_key": "EU:100001-2026", "dry_run": False})
    assert r.status_code == 200 and r.json()["firm_checks"]
    saved = e.req("GET", f"/assessments/{r.json()['id']}", "viewer", firm=None).json()
    assert "firm_checks" not in saved and not any(n in json.dumps(saved) for n in NEEDLES)
    assert not any(n in "\n".join(e.store.db.iterdump()) for n in NEEDLES)
