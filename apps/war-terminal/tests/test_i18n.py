"""UI language switch: every Russian UI string has an English entry, no raw key leaks, server explanations have an English form."""
import json
import pathlib
import re

from conftest import Env, TODAY
from fixtures import ted_payload

UI = pathlib.Path(__file__).resolve().parents[1] / "warterminal" / "ui"
CYR = re.compile("[А-Яа-яЁё]")


def js_literals(src: str):
    """(literal_text, is_inside_T_call) for every string literal with Cyrillic in app.js; comments are skipped."""
    out, i, n = [], 0, len(src)
    while i < n:
        c = src[i]
        if src.startswith("//", i):
            i = src.find("\n", i)
            i = n if i < 0 else i
        elif src.startswith("/*", i):
            i = src.find("*/", i) + 2
        elif c in "\"'`":
            j = i + 1
            while src[j] != c:
                j += 2 if src[j] == "\\" else 1
            lit = src[i + 1:j]
            if CYR.search(lit):
                head = src[max(0, i - 3):i]
                out.append((json.loads(src[i:j + 1]) if c == '"' else lit, head.endswith("T(") or head.endswith("Tf(")))
            i = j + 1
        else:
            i += 1
    return out


def load_en():
    src = (UI / "i18n.js").read_text(encoding="utf-8")
    body = src[src.index("const EN = ") + len("const EN = "):src.index(";\nfunction T(")]
    return json.loads(body)


def load_srv():
    src = (UI / "i18n.js").read_text(encoding="utf-8")
    block = src[src.index("const SRV = ["):src.index("const SRV_RX")]
    return [(json.loads('"' + a + '"'), json.loads('"' + b + '"')) for a, b in re.findall(r'\["((?:[^"\\]|\\.)*)",\s*"((?:[^"\\]|\\.)*)"\]', block)]


def test_every_ui_string_goes_through_T_and_has_an_english_entry():
    lits = js_literals((UI / "app.js").read_text(encoding="utf-8"))
    en = load_en()
    assert len(lits) > 300
    loose = [t for t, wrapped in lits if not wrapped]
    assert loose == [], f"Russian literals outside T()/Tf(): {loose[:5]}"
    missing = sorted({t for t, _ in lits} - set(en))
    assert missing == [], f"no English entry for: {missing[:5]}"


def test_english_entries_are_english_and_keep_placeholders():
    en = load_en()
    for ru, e in en.items():
        assert e.strip(), ru
        assert not CYR.search(e), f"Cyrillic left in EN entry: {ru!r} -> {e!r}"
        assert re.findall(r"\{\d+\}", ru) == re.findall(r"\{\d+\}", e), (ru, e)
        assert e != ru
        assert not re.fullmatch(r"[a-z_]+\.[a-z_.]+", e), f"raw key leaked: {e!r}"         # a key such as ui.tenders.title would be a leak


def test_static_html_strings_are_covered_in_both_languages():
    html = (UI / "index.html").read_text(encoding="utf-8")
    en = load_en()
    texts = set(re.findall(r">([^<>]*[А-Яа-я][^<>]*)<", html)) | set(re.findall(r'(?:aria-label|placeholder|title)="([^"]*[А-Яа-я][^"]*)"', html))
    texts = {t.strip() for t in texts}
    assert texts and all(t in en for t in texts), sorted(t for t in texts if t not in en)
    assert 'data-l="ru"' in html and 'data-l="en"' in html and 'data-m="orig"' in html and 'data-m="tr"' in html and 'data-m="both"' in html


def test_language_is_remembered_per_browser_with_safe_storage_and_defaults_to_russian():
    i18n = (UI / "i18n.js").read_text(encoding="utf-8")
    app = (UI / "app.js").read_text(encoding="utf-8")
    assert 'localStorage.getItem("wt_lang") === "en" ? "en" : "ru"' in i18n and "catch" in i18n.split("const EN")[0]
    assert 'localStorage.setItem("wt_lang"' in app and 'localStorage.setItem("wt_trmode"' in app
    assert app.count("try {") >= 3


def test_ui_separates_original_and_translation_and_never_translates_the_buyer():
    app = (UI / "app.js").read_text(encoding="utf-8")
    css = (UI / "style.css").read_text(encoding="utf-8")
    assert 'dual(t.buyer, { keep: true })' in app                                   # organisation names: original only
    assert 'class: "orig"' in app and 'class: "trans' in app                        # two separate blocks
    assert "оригинал" in app and "машинный перевод" in app
    for rule in ("body[data-trmode=orig] .dual .trans", "body[data-trmode=tr] .dual.has-tr .orig"):
        assert rule in css
    # a translation block is only filled from a server record that says TRANSLATED; every other state shows the explicit "no translation" note
    assert 'rec.status === "TRANSLATED"' in app and "перевода нет" in app


def test_server_explanations_have_an_english_form(tmp_path):
    """Gate reasons, evidence, fit explanations and checklist text come from the server in Russian: each fixed phrase must match an SRV pattern."""
    srv = [re.compile(a) for a, _ in load_srv()]
    e = Env(tmp_path)
    e.ingest_ted()
    e.req("POST", "/ingest", "analyst", json={"source": "demo"})
    texts = set()

    def collect(a):
        for g in a["gates"]:
            texts.update([g["reason"], g["evidence_needed"]])
        for c in a["fit"]["components"].values():
            texts.add(c["why"])
        texts.add(a["fit"]["disclaimer"])

    def set_profile(changes):
        r = e.req("PATCH", "/company/profile", "analyst", json={"changes": changes})
        assert r.status_code == 200, r.text
        assert e.req("POST", f"/approvals/{r.json()['approval_id']}/decide", "owner", json={"approve": True}).status_code == 200

    def sweep():
        for t in e.req("GET", "/tenders?limit=200", "viewer").json()["items"]:
            d = e.req("GET", f"/tenders/{t['key']}", "viewer").json()
            collect(d["assessment"])
            cl = e.req("GET", f"/tenders/{t['key']}/checklist", "viewer").json()
            texts.update([cl["note"]] + [x for i in cl["items"] for x in (i["step"], i.get("why"), i.get("evidence")) if x])

    sweep()                                                                                       # empty profile, no lists
    set_profile({"country": "CZ", "registered": False})
    sweep()                                                                                       # planned company
    set_profile({"country": "US", "registered": True, "cpv_capabilities": ["09", "15"], "capital_limit_usd": 50000})
    assert e.req("PUT", "/fx", "analyst", json={"rates": {"CZKUSD": 0.043, "EURUSD": 1.1}, "as_of": "2026-10-16", "source": "manual entry"}).status_code == 200
    assert e.req("POST", "/sanctions/import", "compliance", json={"entries": ["Mesto Brno", "Mesto Brn"], "source": "fixture", "list_version": "t", "as_of": "2026-10-16"}).status_code == 200
    sweep()                                                                                       # non-EU company, sanctions hit
    set_profile({"country": "CZ", "registered": True, "cpv_capabilities": ["09", "15", "60"]})
    assert e.req("POST", "/suppliers", "analyst", json={"name": "Fuel Supplier sro", "country": "CZ", "cpv_prefixes": ["09"], "confirmed": True}).status_code == 200
    a = e.req("POST", "/assessments", "analyst", json={"tender_key": e.key("100001-2026"), "dry_run": False}).json()
    assert e.req("POST", f"/assessments/{a['id']}/review", "compliance", json={"gate_id": "documents", "decision": "cleared", "note": "documentation read"}).status_code == 200
    sweep()                                                                                       # reviewed gate: reason carries a review note
    texts = {t for t in texts if t}
    assert len(texts) > 25
    uncovered = sorted(t for t in texts if not any(rx.search(t.split(" -> снято проверкой: ")[0]) for rx in srv))   # srvT() keeps the reviewer's own note
    assert uncovered == [], uncovered
