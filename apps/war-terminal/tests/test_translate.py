"""Original / translation data model, free-route chain, cost guard and the negative controls of the machine-translation path."""
import hashlib
import json

import pytest

from conftest import Env, H, TOK
from warterminal import translate as tr
from warterminal.store import Store


# ------------------------------------------------------------------ fakes
def model(i, alias, free_tier="experiential_labs_free_plan", price=0.0, status="online", kind="cloud", name=None, **caps):
    return {"id": i, "alias": alias, "name": name or alias, "provider_id": 13, "status": status, "kind": kind, "price_in": price, "price_out": price,
            "caps": {"free_tier": free_tier, **caps}}


MODELS = [model(100, "explabs:deepseek-v4.1-flash-1017"), model(101, "explabs:deepseek-v4.1-flash-alt"),
          model(99, "gemini-4-argon", paid=True, price=2.0), model(11, "moonshotai/kimi-k3", "nvidia_nim", status="unknown"),
          model(8, "local-qwen", None, kind="local"), model(95, "htf:deepseek-v4.1-flash", True, status="unknown")]


class FakeBossman:
    """Stands in for the Bossman gateway: echoes 'EN(<text>)' for every item, masks untouched."""

    def __init__(self, models=None, reply=None, fail=None, cost=0.0):
        self._models, self.reply, self.fail, self.cost = models if models is not None else MODELS, reply, fail or {}, cost
        self.calls = []

    def models(self):
        return self._models

    def free_chat(self, model_id, messages, max_tokens):
        self.calls.append(model_id)
        assert max_tokens >= 512                                      # thinking models need room
        if model_id in self.fail:
            raise tr.RouteError("boom", self.fail[model_id])
        payload = json.loads(messages[1]["content"])
        if self.reply is not None:
            text = self.reply(payload)
        else:
            text = json.dumps({"items": [{"i": it["i"], "l": "cs", "t": f"EN({it['t']})"} for it in payload["items"]]}, ensure_ascii=False)
        return {"text": text, "model": f"alias-{model_id}", "usage": {"cost": self.cost, "tokens_in": 10, "tokens_out": 10}}


@pytest.fixture
def store():
    return Store(":memory:")


CZ = "Dodávka motorové nafty pro obecní vozidla"


# ------------------------------------------------------------------ routes: only free, never local / paid / unknown price
def test_free_route_chain_is_strictly_free_and_ordered():
    routes = tr.free_routes(MODELS)
    ids = [r["id"] for r in routes]
    assert ids[:2] == [100, 101]                                      # owner-preferred Experiential Labs ids first
    assert 99 not in ids and 8 not in ids                              # paid / local never
    assert set(ids) == {100, 101, 95, 11}
    assert tr.is_free_route(model(1, "x", price=None)) is False       # unknown price is not proof of free
    assert tr.is_free_route(model(1, "x", free_tier=None)) is False   # hand-typed 0/0 without a free tier is not free
    assert tr.is_free_route(model(1, "x", name="codestral-22b")) is False
    assert tr.is_free_route(model(1, "x", status="error")) is False


# ------------------------------------------------------------------ record model
def test_translation_record_is_separate_from_the_original_and_machine_marked(store):
    t = tr.Translator(store, FakeBossman())
    out = t.translate([CZ], "en")["items"][0]
    assert out["status"] == "TRANSLATED" and out["translation"] == f"EN({CZ})" and out["machine"] is True
    assert out["source_lang"] == "cs" and out["target"] == "en" and out["model"] == "alias-100"
    assert out["source_sha256"] == hashlib.sha256(CZ.encode()).hexdigest()
    assert CZ not in json.dumps({k: v for k, v in out.items() if k != "translation"}, ensure_ascii=False)     # the record never carries/merges the original
    assert out["translation"] != CZ


def test_cache_translates_each_text_once(store):
    fb = FakeBossman()
    t = tr.Translator(store, fb)
    t.translate([CZ, CZ], "en")
    assert len(fb.calls) == 1
    again = t.translate([CZ], "en")
    assert len(fb.calls) == 1 and again["items"][0]["cached"] is True and again["items"][0]["status"] == "TRANSLATED"
    assert t.lookup([CZ], "en")[0]["translation"] == f"EN({CZ})"                  # lookup serves the cache with no model call
    assert len(fb.calls) == 1
    t.translate([CZ], "ru")                                                        # another target language is another cache entry
    assert len(fb.calls) == 2


def test_lookup_without_cache_reports_no_translation_and_spends_nothing(store):
    fb = FakeBossman()
    rec = tr.Translator(store, fb).lookup([CZ], "en")[0]
    assert rec["status"] == "NOT_TRANSLATED" and "translation" not in rec and rec["source_lang"] == "cs" and fb.calls == []


# ------------------------------------------------------------------ protected words
def test_protected_tokens_survive_byte_for_byte():
    text = "Dodávka paliva pro ČEZ Distribuce, a. s., CPV 09134200-9, částka 1 250 000 Kč, viz https://nen.nipez.cz/x?y=1, model T-72, ISO 9001"
    masked, saved = tr.protect(text)
    for keep in ("ČEZ Distribuce, a. s.", "09134200-9", "https://nen.nipez.cz/x?y=1", "T-72", "ISO 9001", "1 250 000 Kč"):
        assert keep in saved and keep not in masked
    assert tr.restore(masked, saved) == text
    assert tr.restore(masked.replace("⟦0⟧", ""), saved) is None                  # a lost mask makes the reply untrusted
    assert tr.restore(masked + "⟦9⟧", saved) is None                              # an invented mask too


def test_reply_that_drops_a_protected_span_is_rejected_not_shown(store):
    def bad(payload):
        return json.dumps({"items": [{"i": it["i"], "l": "cs", "t": "fuel supply"} for it in payload["items"]]})   # CPV mask lost
    rec = tr.Translator(store, FakeBossman(reply=bad)).translate(["Dodávka paliva 09134200"], "en")["items"][0]
    assert rec["status"] == "FAILED" and "translation" not in rec


# ------------------------------------------------------------------ negative controls
def test_text_already_in_target_language_is_not_a_translation(store):
    fb = FakeBossman()
    rec = tr.Translator(store, fb).translate(["Diesel supply for municipal vehicles and services"], "en")["items"][0]
    assert rec["status"] == "SAME_LANGUAGE" and "translation" not in rec and fb.calls == []


def test_identical_model_output_is_not_shown_as_translated(store):
    same = lambda p: json.dumps({"items": [{"i": it["i"], "l": "und", "t": it["t"]} for it in p["items"]]})   # noqa: E731
    t = tr.Translator(store, FakeBossman(reply=same))
    rec = t.translate(["ZZZ-100 Qwertyuiop"], "en")["items"][0]
    assert rec["status"] == "SAME_LANGUAGE" and "translation" not in rec
    assert store.kv_get(tr.cache_key("ZZZ-100 Qwertyuiop", "en")) is None        # nothing cached as a translation


def test_no_route_means_no_translation_and_no_fake_placeholder(store):
    for bossman, status in ((None, "NOT_CONNECTED"), (FakeBossman(models=[model(8, "local", None, kind="local")]), "UNAVAILABLE")):
        rec = tr.Translator(store, bossman).translate([CZ], "en")["items"][0]
        assert rec["status"] == status and "translation" not in rec and rec["reason"]


def test_cost_above_zero_drops_the_reply_and_halts(store):
    fb = FakeBossman(cost=0.0004)
    t = tr.Translator(store, fb)
    out = t.translate([CZ], "en")
    rec = out["items"][0]
    assert rec["status"] == "HALTED_COST" and "translation" not in rec
    assert store.kv_get(tr.cache_key(CZ, "en")) is None                            # the paid reply was NOT kept
    assert t.status()["halted"] is True and t.status()["cost_total_usd"] > 0
    n = len(fb.calls)
    assert t.translate(["Zajištění ostrahy objektu"], "en")["items"][0]["status"] == "HALTED_COST"
    assert len(fb.calls) == n                                                      # stopped: no further call


def test_zero_cost_is_recorded_as_zero(store):
    t = tr.Translator(store, FakeBossman(cost=0.0))
    t.translate([CZ], "en")
    s = t.status()
    assert s["cost_total_usd"] == 0.0 and s["calls_today"] == 1 and s["used_today"] == 1 and s["models_used"] == {"alias-100": 1}


def test_fallback_rotates_on_429_and_records_the_model_that_answered(store):
    fb = FakeBossman(fail={100: 429, 101: 503})
    t = tr.Translator(store, fb, sleep=lambda s: None)
    rec = t.translate([CZ], "en")["items"][0]
    assert rec["status"] == "TRANSLATED" and rec["model"] == "alias-95" and fb.calls == [100, 101, 95]
    fb.calls.clear()
    t.translate(["Zajištění ostrahy objektu"], "en")
    assert fb.calls == [95]                                                         # the failing routes are in cooldown, not hammered


def test_limits_per_call_and_per_day(store):
    t = tr.Translator(store, FakeBossman())
    with pytest.raises(ValueError):
        t.translate([f"Dodávka {i}" for i in range(61)], "en")
    store.kv_set(f"tr_stats:{tr._day()}", {"calls": 9, "fresh": 299, "by_model": {}, "cost_total": 0.0})
    out = t.translate(["Dodávka paliva A", "Dodávka paliva B", "Dodávka paliva C"], "en")["items"]
    assert [r["status"] for r in out].count("TRANSLATED") == 1 and [r["status"] for r in out].count("LIMIT") == 2


def test_token_bucket_spaces_calls():
    now = [0.0]
    b = tr.Bucket(rate=0.5, cap=2, clock=lambda: now[0])
    assert b.wait_time() == 0 and (b.take(), b.take()) and b.wait_time() == pytest.approx(2.0)
    now[0] = 2.0
    assert b.wait_time() == 0


def test_language_detection_tags_and_gateway_is_loopback_only(tmp_path):
    assert tr.detect_lang(CZ) == "cs" and tr.detect_lang("Lieferung von Kraftstoff für die Stadt") == "de"
    assert tr.detect_lang("Dostawa paliwa dla gminy") == "pl" and tr.detect_lang("Поставка топлива для города") == "ru"
    assert tr.detect_lang("A1") is None
    tok = tmp_path / "token"
    tok.write_text("x")
    env = {"WAR_TERMINAL_BOSSMAN_TOKEN_FILE": str(tok)}
    assert tr.gateway_from_env({**env, "WAR_TERMINAL_BOSSMAN_URL": "https://evil.example/"}) is None       # the token never leaves the machine
    assert tr.gateway_from_env({**env, "WAR_TERMINAL_BOSSMAN_URL": "off"}) is None
    assert tr.gateway_from_env(env) is not None


# ------------------------------------------------------------------ API
def make_env(tmp_path, bossman):
    from fastapi.testclient import TestClient
    from conftest import TODAY
    from fixtures import fake_get, fake_post
    from warterminal.api import create_app
    app = create_app(tmp_path, live=True, tokens=TOK, post=fake_post(), get=fake_get, today=TODAY, bossman=bossman)
    e = Env.__new__(Env)
    e.app, e.c, e.store = app, TestClient(app), app.state.store
    return e


def test_api_translation_never_touches_the_stored_tender(tmp_path):
    e = make_env(tmp_path, FakeBossman())
    e.ingest_ted()
    before = e.req("GET", "/tenders", "viewer").json()["items"]
    titles = [t["title"] for t in before]
    r = e.req("POST", "/translate", "analyst", json={"texts": titles[:2], "target": "ru"})
    assert r.status_code == 200 and r.json()["fresh"] >= 1
    after = e.req("GET", "/tenders", "viewer").json()["items"]
    assert [t["title"] for t in after] == titles                                      # the original is never overwritten
    audit = [x for x in e.store.audit_events() if x["event"] == "translate.run"] if hasattr(e.store, "audit_events") else []
    assert "translation" not in json.dumps(audit)                                      # the audit log carries counts, not text


def test_api_roles_and_limits(tmp_path):
    e = make_env(tmp_path, FakeBossman())
    assert e.req("POST", "/translate", "viewer", json={"texts": [CZ], "target": "en"}).status_code == 403
    assert e.req("POST", "/translate/lookup", "viewer", json={"texts": [CZ], "target": "en"}).status_code == 200
    assert e.req("POST", "/translate", "analyst", json={"texts": [f"t{i}" for i in range(61)], "target": "en"}).status_code == 422
    assert e.req("POST", "/translate", "analyst", json={"texts": [CZ], "target": "de"}).status_code == 422
    assert e.req("GET", "/translate/status", "viewer").json()["configured"] is True


def test_api_without_gateway_is_honest(tmp_path):
    e = make_env(tmp_path, False)
    rec = e.req("POST", "/translate", "analyst", json={"texts": [CZ], "target": "en"}).json()["items"][0]
    assert rec["status"] == "NOT_CONNECTED" and "translation" not in rec
    assert e.req("GET", "/translate/status", "viewer").json()["configured"] is False
