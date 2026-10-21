"""Contacts module + tender <-> company matching learned from owner votes. Synthetic tenders in a temp DB, fake Bossman,
everything through the real HTTP routes (pydantic validation included)."""
from __future__ import annotations

import json
import pathlib
import re
import threading

import pytest
from fastapi.testclient import TestClient

from conftest import TOK, TODAY, Env, H
from warterminal import contacts as C, seed_forum_20261021 as seed_mod
from warterminal.api import create_app
from warterminal.store import Store
from warterminal.translate import BossmanGateway, RouteError

UI = pathlib.Path(__file__).resolve().parents[1] / "warterminal" / "ui"
PERSON = {"person_name": "Jan Testovaci", "person_phone": "+420 601 234 567"}


def tender(key_id, title, cpv, *, source="ted", country="CZ", buyer="Test buyer", status="open", days=30, value=100000.0, cur="EUR"):
    deadline = f"{TODAY.fromordinal(TODAY.toordinal() + days).isoformat()}T23:59:59Z"
    return {"id": f"test:{key_id}", "official_notice_id": key_id, "title": title, "jurisdiction": "EU" if source == "ted" else country,
            "country": country, "category": "security_defence", "status": status, "deadline": deadline, "estimated_value": value, "currency": cur,
            "source_url": f"https://ted.europa.eu/en/notice/-/detail/{key_id}", "source_id": source, "retrieved_at": "2026-10-17T00:00:00+00:00",
            "published_at": TODAY.isoformat(), "source_version": "t", "checksum": key_id, "data_quality": "ok", "buyer": buyer, "cpv": cpv}


def add(env, *docs):
    keys = []
    for d in docs:
        keys.append(env.store.upsert_tender(d)[0])
    return keys


def contact_body(**kw):
    body = {"company": "Radar Test s.r.o.", "country": "CZ", "products": "radar", "cpv_prefixes": ["35722000"],
            "keywords": {"en": ["radar*"], "cs": ["radar*"], "uk": ["рлс"], "ru": ["радар*"]}, "status": "new"}
    body.update(kw)
    return body


def new_contact(env, role="analyst", **kw):
    r = env.req("POST", "/contacts", role, json=contact_body(**kw))
    assert r.status_code == 200, r.text
    return r.json()["id"]


def seeded(env):
    seed_mod.seed(env.store)
    return {c["company"]: c["id"] for c in env.store.list_contacts()}


class FakeBossman:
    """Translator surface + memory_write; records every call. Never reaches a network."""

    def __init__(self, fail_status=None):
        self.writes, self.fail_status = [], fail_status

    def models(self):
        return []

    def free_chat(self, *a, **k):
        raise AssertionError("no translation in these tests")

    def memory_write(self, title, content, kind="lesson", tags=None):
        if self.fail_status:
            raise RouteError(f"HTTP {self.fail_status}", self.fail_status)
        self.writes.append({"title": title, "content": content, "kind": kind, "tags": tags})
        return {"path": f"memory/{len(self.writes)}.md"}


# ------------------------------------------------------------------ seed
def test_seed_is_idempotent_keeps_owner_edits_and_has_no_personal_data(env, tmp_path, capsys):
    first = seed_mod.seed(env.store)
    assert first == {"created": 13, "existing": 0, "total": 13}
    ids = seeded(env)
    assert len(ids) == 13 and "Czechia Systems" in ids and "KV Defence Limited" in ids
    events = [e for e in env.store.audit_events() if e["event"] == "contacts.seed"]
    assert len(events) == 1
    # the owner edits a seeded record, a re-run must neither duplicate nor overwrite it
    cid = ids["Saab"]
    assert env.req("PATCH", f"/contacts/{cid}", "analyst", json={"status": "contacted", "notes": "написали"}).status_code == 200
    again = seed_mod.seed(env.store)
    assert again == {"created": 0, "existing": 13, "total": 13}
    assert len(env.store.list_contacts()) == 13
    assert env.store.get_contact(cid)["status"] == "contacted"
    assert len([e for e in env.store.audit_events() if e["event"] == "contacts.seed"]) == 1      # a no-op re-run leaves no trace
    for c in env.store.list_contacts():
        assert not c.get("person_name") and not c.get("person_phone")
        assert c["source"] == "Future Military Forum 2026, Praha, стенды (фото владельца)"
    emails = [c for c in env.store.list_contacts() if c.get("public_email")]
    assert [c["company"] for c in emails] == ["Czechia Systems"] and "листовк" in emails[0]["public_email_source"]
    kv = env.store.get_contact(ids["KV Defence Limited"])
    assert kv["cpv_prefixes"] == [] and kv["keywords"] == {} and kv["products"] == ""             # unknown products stay empty
    # CLI on a fresh data dir: same counts, second run creates nothing
    data = tmp_path / "cli"
    assert seed_mod.main(["--data", str(data)]) == 0 and seed_mod.main(["--data", str(data)]) == 0
    out = capsys.readouterr().out
    assert "created 13" in out and "created 0, already present 13" in out
    st = Store(data / "war.db")
    assert len(st.list_contacts()) == 13 and st.verify_audit()["ok"]


def test_every_seed_record_passes_the_http_model(env):
    """The seed bypasses HTTP; the same records must also be accepted by POST /contacts (pydantic + clean)."""
    for raw in seed_mod.COMPANIES:
        body = {"event": seed_mod.EVENT, "source": seed_mod.SOURCE, "status": "new", **raw}
        r = env.req("POST", "/contacts", "analyst", json=body)
        assert r.status_code == 200, (raw["company"], r.text)


def test_seed_cpv_codes_are_cpv_2008_and_radar_is_not_sonar():
    codes = {c for raw in seed_mod.COMPANIES for c in raw.get("cpv_prefixes", [])}
    assert all(re.fullmatch(r"\d{8}", c) for c in codes)
    assert not any(c.startswith("38113") for c in codes)                  # 38113000 = sonars
    radar = {c["company"]: c["cpv_prefixes"] for c in seed_mod.COMPANIES if "radar*" in c.get("keywords", {}).get("en", [])}
    assert all(any(p.startswith(("35722", "38115", "3572")) for p in v) for v in radar.values())
    assert "38115" in C.DUAL_USE_CPV and not any(p.startswith("38113") for p in C.DUAL_USE_CPV)


# ------------------------------------------------------------------ matching + explanation
def test_matches_explain_cpv_and_keywords_and_ignore_the_buyer_name(env):
    ids = seeded(env)
    keys = add(env,
               tender("CD-1", "Supply of a mobile counter-drone system with radar and jammers", ["35730000"]),
               tender("CD-2", "Закупівля протидронових засобів РЕБ", ["35000000"], source="prozorro", country="UA"),
               tender("FUEL-1", "Diesel fuel", ["09134200"], buyer="Radar Air Base counter-drone unit"),       # buyer name only
               tender("CD-OLD", "Counter-drone radar", ["35730000"], status="closed", days=-5))
    r = env.req("GET", f"/contacts/{ids['Czechia Systems']}/matches", "viewer")
    assert r.status_code == 200, r.text
    d = r.json()
    got = [i["tender"]["key"] for i in d["items"]]
    assert got[0] == keys[0] and keys[1] in got and keys[2] not in got and keys[3] not in got     # closed hidden by default
    top = d["items"][0]
    assert top["base_score"] == 100.0 and top["cpv"][0]["kind"] == "inside" and top["cpv"][0]["company_prefix"] == "3573"
    assert any("входит в профиль компании 3573*" in x for x in top["reasons"])
    assert {k["keyword"] for k in top["keywords"]} >= {"counter-drone", "radar*", "jammer*"}
    ua = next(i for i in d["items"] if i["tender"]["key"] == keys[1])
    assert any(k["lang"] == "uk" for k in ua["keywords"]) and any(c["kind"] == "broader" for c in ua["cpv"])
    assert all(x["vote"] is None for x in d["items"])
    every = env.req("GET", f"/contacts/{ids['Czechia Systems']}/matches?status=all&k=50", "viewer").json()
    assert keys[3] in [i["tender"]["key"] for i in every["items"]]
    assert env.req("GET", f"/contacts/{ids['Czechia Systems']}/matches?status=closed", "viewer").status_code == 422
    assert env.req("GET", f"/contacts/{ids['Czechia Systems']}/matches?k=0", "viewer").status_code == 422
    assert env.req("GET", "/contacts/99999/matches", "viewer").status_code == 404


def test_keywords_are_whole_word_accent_folded_and_stems_need_a_star():
    prof = C.Profile({"company": "X", "keywords": {"uk": ["реб"], "cs": ["rušič*"], "en": ["uav"], "ru": ["радар*"]}})

    def kws(title):
        return [k["keyword"] for k in C.match({"title": title, "cpv": []}, prof)["keywords"]]
    assert kws("Ребро жорсткості") == []                                   # «реб» is a whole word, not «ребро»
    assert kws("засоби РЕБ для підрозділу") == ["реб"]
    assert kws("Dodávka rušičů dronů") == ["rušič*"] and kws("rusice") == ["rušič*"]   # accents folded both ways
    assert kws("UAVs and UAV parts") == ["uav"] and kws("UAVs only") == []
    assert kws("поставка радаров") == ["радар*"]
    assert C.match({"title": "радар", "cpv": []}, prof)["base_score"] == 15.0
    # overlapping hits are one hit, longest phrase wins (found in the browser check: 3 hits for one phrase)
    nested = C.Profile({"company": "Z", "keywords": {"en": ["radar*", "surveillance radar*", "ground surveillance radar*", "drone*"], "cs": ["dron*"]}})
    m = C.match({"title": "Ground surveillance radar and drones", "cpv": []}, nested)
    assert [k["keyword"] for k in m["keywords"]] == ["ground surveillance radar*", "drone*"] and m["base_score"] == 30.0
    assert m["keywords"][1]["found"] == "drones"
    with pytest.raises(C.ContactError):
        C.clean({"company": "Y", "keywords": {"cs": ["ra*"]}})              # a stem shorter than 3 letters would match everything


def test_tender_page_lists_who_to_offer_to_and_skips_declined(env):
    ids = seeded(env)
    key = add(env, tender("R-1", "Ground surveillance radar for border guard", ["35722000"]))[0]
    d = env.req("GET", f"/tenders/{key}/matches", "viewer").json()
    names = [i["contact"]["company"] for i in d["items"]]
    assert names[:2] and {"VEX Hungary (Hungarian Defense and Space Export Agency)", "Omnipol Defence + APS (Advanced Protection Systems)"} <= set(names)
    assert "Česká zbrojovka (CZ)" not in names and "Diesel" not in json.dumps(d)
    assert all(i["badges"] for i in d["items"]) and all(i["score"] >= i["base_score"] - 1e-9 for i in d["items"])
    vex = ids["VEX Hungary (Hungarian Defense and Space Export Agency)"]
    assert env.req("PATCH", f"/contacts/{vex}", "analyst", json={"status": "declined"}).status_code == 200
    d2 = env.req("GET", f"/tenders/{key}/matches", "viewer").json()
    assert vex not in [i["contact"]["id"] for i in d2["items"]] and d2["skipped_declined"] == 1
    d3 = env.req("GET", f"/tenders/{key}/matches?include_declined=true", "viewer").json()
    assert vex in [i["contact"]["id"] for i in d3["items"]]
    assert env.req("GET", "/tenders/EU:NOPE/matches", "viewer").status_code == 404
    assert env.req("GET", f"/tenders/{key}", "viewer").status_code == 200                       # the detail route still works


# ------------------------------------------------------------------ feedback learning
def test_owner_votes_rerank_and_the_latest_vote_wins(env):
    cid = new_contact(env)
    t1, t2, t3 = add(env, tender("R-1", "Radar spare parts", ["35722000"], source="ted", country="DE"),
                     tender("R-2", "Radar spare parts", ["35722000"], source="prozorro", country="UA"),
                     tender("R-3", "Radar maintenance kit", ["35722000"], source="prozorro", country="UA"))

    def order():
        return [i["tender"]["key"] for i in env.req("GET", f"/contacts/{cid}/matches", "viewer").json()["items"]]
    assert order() == [t1, t2, t3]                                          # equal base: tie broken by key
    r = env.req("POST", "/matches/feedback", "analyst", json={"tender_key": t2, "contact_id": cid, "vote": "up", "reason": "UA buyers buy radars"})
    assert r.status_code == 200, r.text
    fb = r.json()
    assert set(fb["features"]) == {"cpv:35722", "kw:radar*", "src:prozorro", "bc:UA"} and fb["labels"] == 1
    assert order()[:2] == [t2, t3]                                          # the unlabeled UA/Prozorro tender also moved up
    item = env.req("GET", f"/contacts/{cid}/matches", "viewer").json()["items"][0]
    assert item["vote"] == "up" and item["learned_adjustment"] > 0 and item["score"] == round(item["base_score"] + item["learned_adjustment"], 1)
    assert {c["feature"] for c in item["contributions"]} == {"cpv:35722", "kw:radar*", "src:prozorro", "bc:UA"}
    w = env.req("GET", "/matches/weights", "viewer").json()["weights"]
    assert w["src:prozorro"] == {"up": 1, "down": 0, "w": round(__import__("math").log(2), 4)}
    # the owner changes his mind: the latest vote is the label, both votes are kept
    assert env.req("POST", "/matches/feedback", "analyst", json={"tender_key": t2, "contact_id": cid, "vote": "down"}).status_code == 200
    assert len(env.store.match_feedback()) == 2
    w = env.req("GET", "/matches/weights", "viewer").json()["weights"]
    assert w["src:prozorro"]["up"] == 0 and w["src:prozorro"]["down"] == 1 and w["src:prozorro"]["w"] < 0
    assert order()[0] == t1


def test_a_vote_never_creates_a_match(env):
    cid = new_contact(env)
    fuel = add(env, tender("F-1", "Diesel fuel", ["09134200"], source="prozorro", country="UA"))[0]
    radar = add(env, tender("R-9", "Radar", ["35722000"], source="prozorro", country="UA"))[0]
    r = env.req("POST", "/matches/feedback", "analyst", json={"tender_key": fuel, "contact_id": cid, "vote": "up"}).json()
    assert r["matched"] is False and r["base_score"] == 0
    keys = [i["tender"]["key"] for i in env.req("GET", f"/contacts/{cid}/matches", "viewer").json()["items"]]
    assert keys == [radar]


def _labels(n_up_good, n_down_bad):
    rows, i = [], 0
    for _ in range(n_up_good):
        i += 1
        rows.append({"id": i, "tender_key": f"EU:G-{i}", "contact_id": 1, "vote": "up", "features": ["src:good", "cpv:35722"], "base_score": 40.0})
    for _ in range(n_down_bad):
        i += 1
        rows.append({"id": i, "tender_key": f"EU:B-{i}", "contact_id": 1, "vote": "down", "features": ["src:bad", "cpv:35722"], "base_score": 80.0})
    return rows


def test_precision_at_5_is_insufficient_below_20_labels_and_measured_on_a_held_out_split():
    small = C.precision_report(_labels(10, 9))
    assert small["status"] == "INSUFFICIENT_EVIDENCE" and small["n_labels"] == 19 and small["precision_at_5"] is None
    rep = C.precision_report(_labels(40, 40))
    test_pairs = [r for r in _labels(40, 40) if C.is_test(r["tender_key"], 1)]
    assert rep["status"] == "OK" and rep["n_labels"] == 80 and rep["n_test"] == len(test_pairs) > 5 and rep["n_train"] == 80 - len(test_pairs)
    goods = sum(r["vote"] == "up" for r in test_pairs)
    bads = len(test_pairs) - goods
    k = min(5, len(test_pairs))
    assert rep["precision_at_5"]["baseline"] == round(max(0, k - bads) / k, 3)       # base score prefers the bad ones
    assert rep["precision_at_5"]["learned"] == round(min(k, goods) / k, 3)           # learned weights put the good ones first
    assert rep["precision_at_5"]["delta"] > 0 and rep["n_queries"] == 1 and rep["n_queries_ranked"] == 1
    # the split is deterministic: the same labels give the same report
    assert C.precision_report(_labels(40, 40)) == rep


def test_metric_endpoint_counts_real_votes_with_the_20_label_threshold(env):
    cid = new_contact(env)
    keys, i = [], 0
    while len(keys) < 20 or not any(C.is_test(k, cid) for k in keys[:20]):
        i += 1
        k = add(env, tender(f"M-{i:03d}", "Radar unit", ["35722000"], source="prozorro" if i % 2 else "ted", country="UA" if i % 2 else "DE"))[0]
        if len(keys) < 19 or C.is_test(k, cid):
            keys.append(k)
    for n, k in enumerate(keys[:19]):
        assert env.req("POST", "/matches/feedback", "analyst", json={"tender_key": k, "contact_id": cid, "vote": "up" if n % 2 else "down"}).status_code == 200
    m = env.req("GET", "/matches/metrics", "viewer").json()
    assert m["status"] == "INSUFFICIENT_EVIDENCE" and m["n_labels"] == 19 and m["min_labels"] == 20
    assert env.req("POST", "/matches/feedback", "owner", json={"tender_key": keys[19], "contact_id": cid, "vote": "up"}).status_code == 200
    m = env.req("GET", "/matches/metrics", "viewer").json()
    assert m["status"] == "OK" and m["n_labels"] == 20 and m["n_test"] >= 1 and set(m["precision_at_5"]) == {"baseline", "learned", "delta"}
    assert "not model training" in m["method"]


# ------------------------------------------------------------------ compliance: badges + sanctions
def test_badges_military_dual_use_and_sanctions_status(env):
    cid = new_contact(env, cpv_prefixes=["35722000", "34711200", "09134200"], keywords={"en": ["radar*", "uav", "fuel"]})
    mil, uav, civil_radar, fuel = add(env, tender("B-1", "Air defence radar", ["35723000"]),
                                      tender("B-2", "UAV for mapping", ["34711200"]),
                                      tender("B-3", "Airport radar maintenance", ["50000000"]),
                                      tender("B-4", "Diesel fuel", ["09134200"]))
    items = {i["tender"]["key"]: {b["id"] for b in i["badges"]} for i in env.req("GET", f"/contacts/{cid}/matches", "viewer").json()["items"]}
    assert items[mil] == {"military_licence", "dual_use", "sanctions_not_loaded"}
    assert items[uav] == {"dual_use", "sanctions_not_loaded"}
    assert items[civil_radar] == {"dual_use", "sanctions_not_loaded"}
    assert items[fuel] == {"sanctions_not_loaded"}
    one = env.req("GET", f"/contacts/{cid}/matches", "viewer").json()["items"][0]
    texts = {b["text"] for b in one["badges"]}
    assert C.MILITARY_BADGE in texts or C.DUAL_USE_BADGE in texts
    assert C.MILITARY_BADGE == "военный материал — нужна лицензия MPO (CZ)" and C.DUAL_USE_BADGE == "двойное назначение — проверить 2021/821"


def test_sanctioned_buyer_company_or_restricted_country_blocks_the_match(env):
    good = new_contact(env, company="Radar Test s.r.o.")
    listed = new_contact(env, company="Rosoboronexport")
    k_ok, k_bad, k_ru, k_maybe = add(env, tender("S-1", "Radar", ["35722000"], buyer="Ministerstvo obrany"),
                                     tender("S-2", "Radar", ["35722000"], buyer="JSC Rosoboronexport"),
                                     tender("S-3", "Radar", ["35722000"], country="RU", source="worldbank"),
                                     tender("S-4", "Radar", ["35722000"], buyer="Rosoboronexport Moscow Branch"))
    assert env.req("POST", "/sanctions/import", "compliance", json={"entries": ["ROSOBORONEXPORT"], "source": "fixture", "list_version": "t",
                                                                   "as_of": "2026-10-17"}).status_code == 200
    d = env.req("GET", f"/contacts/{good}/matches", "viewer").json()
    keys = [i["tender"]["key"] for i in d["items"]]
    assert k_ok in keys and k_bad not in keys and k_ru not in keys and k_maybe in keys
    blocked = {b["tender_key"]: " ".join(b["reasons"]) for b in d["blocked"]}
    assert "санкций" in blocked[k_bad] and "RU" in blocked[k_ru] and d["blocked_total"] == 2
    maybe = next(i for i in d["items"] if i["tender"]["key"] == k_maybe)
    assert "sanctions_possible" in {b["id"] for b in maybe["badges"]}
    assert "sanctions_not_loaded" not in {b["id"] for b in d["items"][0]["badges"]}
    # the listed company itself is never offered
    assert env.req("GET", f"/contacts/{listed}/matches", "viewer").json()["items"] == []
    t = env.req("GET", f"/tenders/{k_ok}/matches", "viewer").json()
    assert [i["contact"]["id"] for i in t["items"]] == [good] and t["blocked"][0]["contact_id"] == listed
    # the pitch never lists a blocked tender either
    md = env.req("GET", f"/contacts/{good}/pitch", "viewer").text
    assert "S-2" not in md and "S-3" not in md


# ------------------------------------------------------------------ pitch
def test_pitch_is_markdown_with_top_5_open_tenders_and_no_personal_data(env):
    cid = new_contact(env, website="www.radartest.cz", public_email="info@radartest.cz", public_email_source="flyer on the stand",
                      notes="private note about the owner's meeting", **PERSON)
    add(env, *[tender(f"P-{i}", f"Radar lot {i}", ["35722000"], value=1000.0 * i, days=10 + i) for i in range(1, 8)],
        tender("P-CLOSED", "Radar closed", ["35722000"], status="closed", days=-2))
    r = env.req("GET", f"/contacts/{cid}/pitch", "viewer")
    assert r.status_code == 200 and r.headers["content-type"].startswith("text/markdown")
    assert 'filename="pitch-contact-' in r.headers["content-disposition"]
    md = r.text
    assert md.startswith("# Radar Test s.r.o.: подходящие открытые тендеры")
    assert md.count("\n## ") == 5 and "Radar closed" not in md
    for part in ("Заказчик: Test buyer", "Стоимость: ", "Срок подачи: 2026-", "Ссылка: https://ted.europa.eu/", "Почему подходит: CPV тендера 35722000",
                 C.MILITARY_BADGE, "https://www.radartest.cz"):
        assert part in md, part
    for secret in ("Jan Testovaci", "601 234 567", "info@radartest.cz", "private note"):
        assert secret not in md
    empty = new_contact(env, company="Nothing Matches a.s.", cpv_prefixes=["03000000"], keywords={})
    assert "Открытых подходящих тендеров сейчас нет." in env.req("GET", f"/contacts/{empty}/pitch", "viewer").text


# ------------------------------------------------------------------ roles + privacy
def test_roles_viewer_reads_analyst_writes_and_person_data_stays_private(env):
    assert env.req("GET", "/contacts", None).status_code == 401
    assert env.req("POST", "/contacts", "viewer", json=contact_body()).status_code == 403
    cid = new_contact(env, **PERSON)
    key = add(env, tender("X-1", "Radar", ["35722000"]))[0]
    assert env.req("PATCH", f"/contacts/{cid}", "viewer", json={"status": "contacted"}).status_code == 403
    assert env.req("POST", "/matches/feedback", "viewer", json={"tender_key": key, "contact_id": cid, "vote": "up"}).status_code == 403
    assert env.req("POST", f"/contacts/{cid}/lesson", "viewer").status_code == 403
    for path in ("/contacts", f"/contacts/{cid}", f"/contacts/{cid}/matches", f"/contacts/{cid}/pitch", f"/tenders/{key}/matches",
                 "/matches/metrics", "/matches/weights"):
        r = env.req("GET", path, "viewer")
        assert r.status_code == 200, path
        assert "Jan Testovaci" not in r.text and "601 234 567" not in r.text, path
    v = env.req("GET", f"/contacts/{cid}", "viewer").json()["contact"]
    assert "person_name" not in v and v["has_person"] is True
    a = env.req("GET", f"/contacts/{cid}", "analyst").json()["contact"]
    assert a["person_name"] == "Jan Testovaci" and a["person_phone"] == "+420 601 234 567"
    assert env.req("POST", "/matches/feedback", "analyst", json={"tender_key": key, "contact_id": cid, "vote": "up",
                                                              "reason": "Jan Testovaci said yes"}).status_code == 200
    assert env.req("PATCH", f"/contacts/{cid}", "analyst", json={"person_phone": "", "status": "meeting"}).json()["changed"] == ["person_phone", "status"]
    assert env.store.get_contact(cid)["person_phone"] is None
    audit = json.dumps(env.req("GET", "/audit/events?limit=500", "compliance").json(), ensure_ascii=False)
    assert "Jan Testovaci" not in audit and "601 234 567" not in audit and "said yes" not in audit and "Radar Test" not in audit
    ev = {e["event"]: e["payload"] for e in env.req("GET", "/audit/events", "compliance").json()["items"]}
    assert ev["contact.created"] == {"contact_id": cid}
    assert ev["contact.updated"] == {"contact_id": cid, "fields": ["person_phone", "status"]}
    assert set(ev["match.feedback"]) == {"feedback_id", "contact_id", "tender", "vote"}
    assert env.req("GET", "/audit/verify", "compliance").json()["ok"] is True


def test_list_filters_and_search(env):
    seeded(env)
    assert env.req("GET", "/contacts", "viewer").json()["total"] == 13
    radar = {c["company"] for c in env.req("GET", "/contacts?q=radar", "viewer").json()["items"]}    # keywords are searched too
    assert {"Czechia Systems", "VEX Hungary (Hungarian Defense and Space Export Agency)"} <= radar and "Saab" not in radar
    assert "Saab" in {c["company"] for c in env.req("GET", "/contacts?q=безоткатное", "viewer").json()["items"]}   # products text, folded
    assert env.req("GET", "/contacts?q=czechia", "viewer").json()["items"][0]["company"] == "Czechia Systems"
    assert env.req("GET", "/contacts?status=new", "viewer").json()["total"] == 13
    assert env.req("GET", "/contacts?status=partner", "viewer").json()["total"] == 0
    assert env.req("GET", "/contacts?status=bogus", "viewer").status_code == 422


# ------------------------------------------------------------------ HTTP validation (every enum value through the real routes)
@pytest.mark.parametrize("status", C.STATUSES)
def test_every_status_is_accepted_on_create_and_patch(env, status):
    cid = new_contact(env, status=status)
    assert env.store.get_contact(cid)["status"] == status
    other = next(s for s in C.STATUSES if s != status)
    assert env.req("PATCH", f"/contacts/{cid}", "analyst", json={"status": other}).json()["contact"]["status"] == other
    assert env.req("PATCH", f"/contacts/{cid}", "analyst", json={"status": status}).json()["contact"]["status"] == status


@pytest.mark.parametrize("vote", C.VOTES)
def test_every_vote_is_accepted(env, vote):
    cid = new_contact(env)
    key = add(env, tender("V-1", "Radar", ["35722000"]))[0]
    r = env.req("POST", "/matches/feedback", "analyst", json={"tender_key": key, "contact_id": cid, "vote": vote})
    assert r.status_code == 200 and r.json()["vote"] == vote


def test_invalid_input_is_rejected_with_field_names(env):
    key = add(env, tender("V-2", "Radar", ["35722000"]))[0]
    cid = new_contact(env)
    bad = [
        contact_body(status="won"),                                          # not a contact status
        contact_body(public_email="info@x.cz"),                              # e-mail without the material it was printed on
        contact_body(cpv_prefixes=["35A"]),
        contact_body(keywords={"de": ["radar"]}),                            # unsupported language
        contact_body(country="Atlantis"),
        contact_body(next_step_date="2026-13-40"),
        contact_body(website="not a site"),
        contact_body(person_phone="call me maybe"),
        dict(contact_body(), unknown_field=1),                               # typos are not silently dropped
        contact_body(company="X"),
    ]
    for body in bad:
        r = env.req("POST", "/contacts", "analyst", json=body)
        assert r.status_code == 422, (body, r.text)
    r = env.req("POST", "/contacts", "analyst", json=contact_body(public_email="info@x.cz"))
    assert r.json()["detail"]["code"] == "CONTACT_INVALID" and r.json()["detail"]["fields"] == ["public_email_source"]
    ok = env.req("POST", "/contacts", "analyst", json=contact_body(country="Czechia", next_step_date="", next_step="call", cpv_prefixes=["35722000-4"]))
    assert ok.status_code == 200 and ok.json()["contact"]["country"] == "CZ" and ok.json()["contact"]["cpv_prefixes"] == ["35722000"]
    assert env.req("POST", "/matches/feedback", "analyst", json={"tender_key": key, "contact_id": cid, "vote": "maybe"}).status_code == 422
    assert env.req("POST", "/matches/feedback", "analyst", json={"tender_key": key, "contact_id": 0, "vote": "up"}).status_code == 422
    assert env.req("POST", "/matches/feedback", "analyst", json={"tender_key": "EU:NOPE", "contact_id": cid, "vote": "up"}).status_code == 404
    assert env.req("POST", "/matches/feedback", "analyst", json={"tender_key": key, "contact_id": 999, "vote": "up"}).status_code == 404
    assert env.req("PATCH", f"/contacts/{cid}", "analyst", json={"status": None}).json()["contact"]["status"] == "new"   # null never resets
    patched = env.req("PATCH", f"/contacts/{cid}", "analyst", json={"keywords": {"en": ["lidar"]}}).json()["contact"]["keywords"]
    assert patched["en"] == ["lidar"] and patched["cs"] == ["radar*"]          # languages not sent are kept
    assert env.req("PATCH", "/contacts/999", "analyst", json={"status": "new"}).status_code == 404


def test_ui_enums_match_the_server():
    app = (UI / "app.js").read_text(encoding="utf-8")
    m = re.search(r"const CONTACT_STATUSES = \[(.*?)\];", app, re.S)
    assert m, "app.js must declare CONTACT_STATUSES"
    assert tuple(re.findall(r'\["(\w+)",', m.group(1))) == C.STATUSES
    for v in C.VOTES:
        assert f'vote: "{v}"' in app or f'"{v}"' in app


# ------------------------------------------------------------------ Bossman lesson: explicit action only, no personal data
def _app(tmp_path, bossman):
    app = create_app(tmp_path, live=True, tokens=TOK, today=TODAY, bossman=bossman)
    return app, TestClient(app)


def test_lesson_goes_to_bossman_only_on_click_and_without_personal_data(tmp_path):
    fake = FakeBossman()
    app, c = _app(tmp_path, fake)
    st = app.state.store
    seed_mod.seed(st)
    cz = next(x["id"] for x in st.list_contacts() if x["company"] == "Czechia Systems")
    assert c.patch(f"/api/v1/contacts/{cz}", headers=H("analyst"), json={**PERSON, "notes": "met at stand B12"}).status_code == 200
    key = st.upsert_tender(tender("L-1", "Counter-drone radar system", ["35730000"]))[0]
    assert c.post("/api/v1/matches/feedback", headers=H("analyst"), json={"tender_key": key, "contact_id": cz, "vote": "up",
                                                                         "reason": "Jan liked it"}).status_code == 200
    c.get(f"/api/v1/contacts/{cz}/matches", headers=H("viewer"))
    c.get(f"/api/v1/contacts/{cz}/pitch", headers=H("viewer"))
    assert fake.writes == []                                                # never automatic
    r = c.post(f"/api/v1/contacts/{cz}/lesson", headers=H("analyst"))
    assert r.status_code == 200, r.text
    assert r.json()["status"] == "SAVED" and len(fake.writes) == 1
    w = fake.writes[0]
    assert w["title"] == "War Terminal: что совпадает для Czechia Systems" and w["kind"] == "lesson"
    body = w["content"]
    assert "cpv:3573" in body and "👍 1" in body and "Counter-drone radar system" in body and "INSUFFICIENT_EVIDENCE" in body
    for secret in ("Jan Testovaci", "601 234 567", "info@czechiasystems.com", "B12", "Jan liked it"):
        assert secret not in body and secret not in w["title"]
    ev = [e for e in st.audit_events() if e["event"] == "contact.lesson"]
    assert ev[0]["payload"] == {"contact_id": cz, "status": "SAVED"}
    assert c.post(f"/api/v1/contacts/{cz}/lesson", headers=H("owner")).status_code == 200 and len(fake.writes) == 2


def test_lesson_reports_not_connected_and_bossman_errors(tmp_path):
    _, off = _app(tmp_path / "off", False)
    cid = off.post("/api/v1/contacts", headers=H("analyst"), json=contact_body()).json()["id"]
    r = off.post(f"/api/v1/contacts/{cid}/lesson", headers=H("analyst"))
    assert r.status_code == 409 and r.json()["detail"]["code"] == "NOT_CONNECTED"
    _, dup = _app(tmp_path / "dup", FakeBossman(fail_status=409))
    cid = dup.post("/api/v1/contacts", headers=H("analyst"), json=contact_body()).json()["id"]
    r = dup.post(f"/api/v1/contacts/{cid}/lesson", headers=H("analyst"))
    assert r.status_code == 409 and r.json()["detail"]["code"] == "ALREADY_EXISTS"
    _, down = _app(tmp_path / "down", FakeBossman(fail_status=503))
    cid = down.post("/api/v1/contacts", headers=H("analyst"), json=contact_body()).json()["id"]
    assert down.post(f"/api/v1/contacts/{cid}/lesson", headers=H("analyst")).json()["detail"]["code"] == "BOSSMAN_ERROR"


def test_gateway_memory_write_sends_owner_participant_and_token_from_file(tmp_path):
    tok = tmp_path / "token"
    tok.write_text("secret-token\n", encoding="utf-8")
    sent = {}

    class Resp:
        status_code = 200

        def json(self):
            return {"path": "x.md"}

    class Http:
        def post(self, url, headers=None, json=None, timeout=None):
            sent.update(url=url, headers=headers, json=json)
            return Resp()
    gw = BossmanGateway("http://127.0.0.1:8801", str(tok), http=Http())
    assert gw.memory_write("T", "body", "lesson", ["a"]) == {"path": "x.md"}
    assert sent["url"] == "http://127.0.0.1:8801/api/memory/write"
    assert sent["headers"] == {"X-BCC-Token": "secret-token", "X-Participant": "owner"}
    assert sent["json"] == {"title": "T", "content": "body", "kind": "lesson", "tags": ["a"]}


# ------------------------------------------------------------------ store: the audit chain survives a second process on war.db
def test_audit_chain_stays_valid_with_two_connections_writing_at_once(tmp_path):
    path = tmp_path / "war.db"
    stores = [Store(path) for _ in range(4)]
    errors = []

    def work(st, n):
        try:
            for i in range(25):
                st.audit(f"p{n}", "x", {"i": i})
        except Exception as exc:                                            # noqa: BLE001 - reported below
            errors.append(repr(exc))
    threads = [threading.Thread(target=work, args=(s, n)) for n, s in enumerate(stores)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    assert errors == []
    check = Store(path)
    assert check.verify_audit() == {"ok": True, "broken_at": None} and len(check.audit_events(500)) == 100


# ------------------------------------------------------------------ English UI: server explanations of matches have an EN form
def test_match_explanations_and_badges_have_an_english_form(env):
    from test_i18n import load_srv
    srv = [re.compile(a) for a, _ in load_srv()]
    ids = seeded(env)
    add(env, tender("E-1", "Counter-drone radar and jammer", ["35730000"]), tender("E-2", "Протидронові засоби", ["35000000"], source="prozorro", country="UA"))
    texts = set()
    for cid in ids.values():
        for it in env.req("GET", f"/contacts/{cid}/matches?status=all", "viewer").json()["items"]:
            texts.update(it["reasons"])
            texts.update(b["text"] for b in it["badges"])
    add(env, tender("E-3", "Counter-drone radar", ["35730000"], buyer="Blocked Buyer Ltd"), tender("E-4", "Counter-drone radar", ["35730000"], country="RU", source="worldbank"))
    assert env.req("POST", "/sanctions/import", "compliance", json={"entries": ["Test buyer Moscow", "Blocked Buyer"], "source": "fixture", "list_version": "t",
                                                                   "as_of": "2026-10-17"}).status_code == 200
    d = env.req("GET", f"/contacts/{ids['Czechia Systems']}/matches?status=all", "viewer").json()
    for it in d["items"]:
        texts.update(b["text"] for b in it["badges"])
    texts.update(r for b in d["blocked"] for r in b["reasons"])
    texts.add(env.req("GET", "/matches/metrics", "viewer").json()["note"])
    assert len(texts) > 10 and any("совпадение со списком санкций (Blocked Buyer)" in t for t in texts) and any("страна заказчика RU" in t for t in texts)
    uncovered = sorted(t for t in texts if not any(rx.search(t) for rx in srv))
    assert uncovered == [], uncovered
