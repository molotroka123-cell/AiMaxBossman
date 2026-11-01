"""Codex learning readiness (owner 31.10): lessons with sources/dates in the EXISTING facts store,
intake endpoint, freshness check with a fake fetcher, synthetic business exercises with an
independent checker, and the read-only readiness probe.

Synthetic data only. No real network: the freshness fetcher is a fake on `svc._lesson_fetcher`.
Every positive path has a negative twin.
"""
from __future__ import annotations

import csv
import io
import json
import sys
from decimal import ROUND_HALF_UP, Decimal
from pathlib import Path

import pytest
import sqlalchemy as sa

from bcc import business_exercises as bx
from bcc.business_exercise_checker import check_answer
from bcc.db import facts as facts_t
from bcc.db import metadata
from bcc.features import memory_lessons as ml
from bcc.tools import ToolContext
from bcc.v2.memory import lessons as L

SYNTH_KEY = "sk-" + "proj-" + "Q" * 28            # synthetic; assembled so the CI secret scan stays quiet


@pytest.fixture(autouse=True)
def _isolated(monkeypatch, tmp_path):
    monkeypatch.setenv("BCC_EXERCISE_KEYS_DIR", str(tmp_path / "exercise-keys-outside"))
    for lim in (ml.INTAKE_LIMITER, ml.VERIFY_LIMITER, ml.FRESHNESS_LIMITER):
        lim._hits.clear()
    yield


def lesson(**over) -> dict:
    base = {
        "title": "Money math uses Decimal, never float",
        "body": "For CZK totals use Decimal with ROUND_HALF_UP to 0.01; 1000 USDT x 25 CZK/USD x 1.03 = 25750.00 CZK.",
        "category": "business", "author": "codex",
        "source": "codex-session:sess-20261031-a", "observed_at": "2026-10-31",
        "valid_from": "2026-10-31",
    }
    base.update(over)
    return base


async def _lesson_rows(env) -> list[dict]:
    async with env.svc.db.session() as s:
        res = await s.execute(sa.select(facts_t).where(facts_t.c.source_kind == "lesson"))
        return [dict(r._mapping) for r in res]


# ------------------------------------------------------------------ 1. sources + dates in the existing store

async def test_lesson_is_recorded_in_existing_facts_table_not_proven(env):
    tables_before = set(metadata.tables)
    r = await env.client.post("/api/memory/lessons", json=lesson())
    assert r.status_code == 200, r.text
    out = r.json()
    v = out["lesson"]
    assert out["status"] == v["status"] == "lesson_recorded"
    assert out["proven_learning"] is False and v["proven_learning"] is False
    assert v["source"] == "codex-session:sess-20261031-a"
    assert v["author"] == "codex" and v["observed_at"].startswith("2026-10-31")
    assert v["valid_from"].startswith("2026-10-31") and v["eval_ref"] is None
    rows = await _lesson_rows(env)
    assert len(rows) == 1 and rows[0]["subject"] == "lesson:business"
    assert set(metadata.tables) == tables_before          # no second store
    # the lesson is also a fact: the existing facts API sees it with its source kind
    facts = (await env.client.get("/api/memory/facts", params={"query": "Decimal"})).json()["items"]
    assert any(f["source_kind"] == "lesson" for f in facts)


@pytest.mark.parametrize("patch, needle", [
    ({"source": ""}, "источник"),
    ({"source": "trust me"}, "source не распознан"),
    ({"source": "http://127.0.0.1:8801/api/secret"}, "политикой"),
    ({"observed_at": "", "valid_from": ""}, "без даты"),
    ({"observed_at": "yesterday-ish"}, "не разобрал"),
    ({"observed_at": "2099-01-01"}, "будущем"),
    ({"author": "anonymous"}, "author"),
    ({"category": "nonsense"}, "категория"),
])
async def test_negative_lesson_without_valid_source_or_date_is_refused(env, patch, needle):
    r = await env.client.post("/api/memory/lessons", json=lesson(**patch))
    assert r.status_code == 422, r.text
    assert needle in r.json()["error"]["message"]
    assert await _lesson_rows(env) == []


@pytest.mark.parametrize("patch", [
    {"status": "verified_by_test"}, {"status": "refuted"}, {"verified": True},
    {"eval_ref": "business_exercise:fx_price_comparison_v1"}, {"verified_at": "2026-10-31"},
])
async def test_negative_writer_cannot_set_verified_or_eval(env, patch):
    r = await env.client.post("/api/memory/lessons", json=lesson(**patch))
    assert r.status_code == 422
    assert await _lesson_rows(env) == []


async def test_negative_cloud_or_remote_writer_cannot_write_owner_private(env):
    for body, headers in (
        (lesson(category="personal"), {}),                                    # author codex
        (lesson(category="finance_private", author="claude"), {}),
        (lesson(category="personal", author="owner"), {"X-Bossman-Writer": "cloud"}),
        (lesson(category="chats", author="bossman"), {}),
    ):
        r = await env.client.post("/api/memory/lessons", json=body, headers=headers)
        assert r.status_code == 403, (body, r.text)
    assert await _lesson_rows(env) == []
    # positive twin: the owner, locally, may
    ok = await env.client.post("/api/memory/lessons", json=lesson(category="personal", author="owner",
                                                                   source="file:notes/owner.md"))
    assert ok.status_code == 200
    # private lessons are hidden from default lists and from remote readers
    assert (await env.client.get("/api/memory/lessons")).json()["total"] == 0
    r = await env.client.get("/api/memory/lessons", params={"include_private": True},
                             headers={"X-Bossman-Writer": "cloud"})
    assert r.status_code == 403
    lid = ok.json()["lesson"]["id"]
    assert (await env.client.get(f"/api/memory/lessons/{lid}", headers={"X-Bossman-Writer": "cloud"})).status_code == 403


def test_negative_non_loopback_owner_is_remote():
    with pytest.raises(L.LessonForbidden):
        L.prepare_lesson(lesson(category="personal", author="owner"), client_host="203.0.113.7")
    assert L.prepare_lesson(lesson(category="personal", author="owner"),
                            client_host="127.0.0.1")["category"] == "personal"


# ------------------------------------------------------------------ 2. intake: redaction, idempotency, size, rate

async def test_secret_in_lesson_is_redacted_before_storage(env):
    body = f"Provider key {SYNTH_KEY} and password=hunter2hunter2 must never be stored."
    r = await env.client.post("/api/memory/lessons", json=lesson(body=body))
    assert r.status_code == 200
    assert r.json()["redacted"] is True
    stored = (await _lesson_rows(env))[0]
    blob = json.dumps(stored, default=str)
    assert SYNTH_KEY not in blob and "hunter2hunter2" not in blob
    assert "[скрыто]" in stored["statement"]


async def test_duplicate_lesson_is_idempotent(env):
    a = (await env.client.post("/api/memory/lessons", json=lesson())).json()
    b = (await env.client.post("/api/memory/lessons", json=lesson(title="money math uses   DECIMAL, never float"))).json()
    assert a["deduped"] is False and b["deduped"] is True
    assert a["lesson"]["id"] == b["lesson"]["id"]
    assert len(await _lesson_rows(env)) == 1
    # negative twin: a different source is a different lesson
    c = (await env.client.post("/api/memory/lessons", json=lesson(source="codex-session:sess-other"))).json()
    assert c["deduped"] is False and len(await _lesson_rows(env)) == 2


async def test_oversized_lesson_is_refused(env):
    r = await env.client.post("/api/memory/lessons", json=lesson(body="x" * (L.MAX_BODY + 1)))
    assert r.status_code == 422 and "слишком большой" in r.json()["error"]["message"]
    raw = json.dumps(lesson(body="y" * 20_000))
    r = await env.client.post("/api/memory/lessons", content=raw, headers={"Content-Type": "application/json"})
    assert r.status_code == 413
    assert await _lesson_rows(env) == []


async def test_intake_is_rate_limited(env, monkeypatch):
    monkeypatch.setattr(ml, "INTAKE_LIMITER", L.RateLimiter(per_minute=2, per_hour=10))
    codes = [(await env.client.post("/api/memory/lessons", json=lesson(title=f"Lesson number {i}"))).status_code
             for i in range(3)]
    assert codes == [200, 200, 429]


# ------------------------------------------------------------------ verified only through a server-run eval

FX_RIGHT = {"totals_czk": {"OFF-A": "25750.00", "OFF-B": "25261.32", "OFF-C": "25527.00"}, "cheapest": "OFF-B"}


async def test_verified_only_after_linked_eval_passes(env):
    lid = (await env.client.post("/api/memory/lessons", json=lesson())).json()["lesson"]["id"]
    url = f"/api/memory/lessons/{lid}/verify"
    # negatives: no eval, an unverifiable claim, a missing answer, a skill evaluation that does not exist
    for body in ({}, {"eval_ref": "pytest:tests/test_x.py::test_ok"}, {"eval_ref": "trust-me"},
                 {"eval_ref": "business_exercise:fx_price_comparison_v1"},
                 {"eval_ref": "skill_evaluation:999999"}):
        r = await env.client.post(url, json=body)
        assert r.status_code == 422, (body, r.text)
    # wrong answer -> eval ran, failed, status unchanged
    wrong = dict(FX_RIGHT, cheapest="OFF-A")
    r = (await env.client.post(url, json={"eval_ref": "business_exercise:fx_price_comparison_v1",
                                          "answer": wrong})).json()
    assert r["eval_passed"] is False and r["lesson"]["status"] == "lesson_recorded"
    assert r["receipt"]["failed_fields"] == ["cheapest"]
    assert "OFF-B" not in json.dumps(r["receipt"])          # the refusal does not leak the truth
    # right answer -> verified_by_test with the eval id and receipt hash stored
    r = (await env.client.post(url, json={"eval_ref": "business_exercise:fx_price_comparison_v1",
                                          "answer": FX_RIGHT})).json()
    v = r["lesson"]
    assert r["eval_passed"] is True and v["status"] == "verified_by_test"
    assert v["eval_ref"] == "business_exercise:fx_price_comparison_v1"
    assert len(v["eval_receipt_sha256"]) == 64 and v["proven_learning"] is False
    # refutation with a source wins over the earlier pass
    bad = await env.client.post(f"/api/memory/lessons/{lid}/refute", json={"reason": "x", "source": "", "author": "owner"})
    assert bad.status_code == 422
    r = await env.client.post(f"/api/memory/lessons/{lid}/refute", json={
        "reason": "Rate table changed", "source": "https://example.org/rates", "author": "owner"})
    assert r.json()["lesson"]["status"] == "refuted"


async def test_record_eval_result_requires_reference_and_receipt(env):
    lid = (await env.client.post("/api/memory/lessons", json=lesson())).json()["lesson"]["id"]
    async with env.svc.db.session() as s:
        with pytest.raises(L.LessonError):
            await L.record_eval_result(s, lid, eval_ref="", passed=True, receipt_sha256="a" * 64)
        with pytest.raises(L.LessonError):
            await L.record_eval_result(s, lid, eval_ref="business_exercise:x", passed=True, receipt_sha256="nope")


# ------------------------------------------------------------------ 3. freshness with a fake fetcher

class FakeFetcher:
    def __init__(self, text: str | None = None, exc: Exception | None = None):
        self.text, self.exc, self.calls = text, exc, []

    async def __call__(self, url: str) -> str:
        self.calls.append(url)
        if self.exc:
            raise self.exc
        return self.text or ""


async def test_freshness_check_and_stale_flag_with_fake_fetcher(env):
    body = lesson(source="https://example.org/fx-rules", observed_at="2026-09-01", valid_from="2026-09-01",
                  expect_text="ROUND_HALF_UP")
    lid = (await env.client.post("/api/memory/lessons", json=body)).json()["lesson"]["id"]
    stale = (await env.client.get("/api/memory/lessons/stale", params={"max_age_days": 30})).json()
    assert [x["id"] for x in stale["items"]] == [lid]

    env.svc._lesson_fetcher = FakeFetcher(text="Rounding: use round_half_up for every invoice line.")
    r = (await env.client.post(f"/api/memory/lessons/{lid}/freshness", json={})).json()
    assert r["check"]["outcome"] == "confirmed"
    assert env.svc._lesson_fetcher.calls == ["https://example.org/fx-rules"]
    v = r["lesson"]
    assert v["web_last_verified_at"] and v["stale"] is False
    assert v["status"] == "lesson_recorded"                  # a web confirmation is not a passed test
    assert (await env.client.get("/api/memory/lessons/stale")).json()["total"] == 0

    env.svc._lesson_fetcher = FakeFetcher(text="This page now talks about something else.")
    r = (await env.client.post(f"/api/memory/lessons/{lid}/freshness", json={})).json()
    assert r["check"]["outcome"] == "not_found"
    env.svc._lesson_fetcher = FakeFetcher(exc=ConnectionError("offline"))
    r = (await env.client.post(f"/api/memory/lessons/{lid}/freshness", json={})).json()
    assert r["check"]["outcome"] == "unreachable"
    # the last CONFIRMED check still anchors freshness
    assert r["lesson"]["web_last_verified_at"] == v["web_last_verified_at"]


async def test_negative_freshness_needs_web_source_and_safe_url(env):
    lid = (await env.client.post("/api/memory/lessons", json=lesson())).json()["lesson"]["id"]
    env.svc._lesson_fetcher = FakeFetcher(text="x")
    r = await env.client.post(f"/api/memory/lessons/{lid}/freshness", json={})
    assert r.status_code == 422                               # codex-session source: nothing to fetch
    r = await env.client.post(f"/api/memory/lessons/{lid}/freshness", json={"url": "http://169.254.169.254/latest"})
    assert r.status_code == 422                               # SSRF policy, before any fetch
    assert env.svc._lesson_fetcher.calls == []


# ------------------------------------------------------------------ 5. business exercises: planted truth, independent check

def _csv(text: str) -> list[dict]:
    return list(csv.DictReader(io.StringIO(text)))


def _q(x: Decimal) -> str:
    return str(x.quantize(Decimal("0.01"), rounding=ROUND_HALF_UP))


def independent_answers() -> dict:
    """Third, independent derivation of the truth straight from the input CSVs."""
    t = {k: bx.public_task(k)["inputs"] for k in bx.EXERCISES}
    inv = _csv(t["invoice_reconciliation_v1"]["invoices.csv"])
    pays = _csv(t["invoice_reconciliation_v1"]["payments.csv"])
    paid: dict[str, Decimal] = {}
    dups = []
    for p in pays:
        due = next(Decimal(i["amount_czk"]) for i in inv if i["invoice_id"] == p["reference"])
        if paid.get(p["reference"], Decimal(0)) >= due:
            dups.append(p["payment_id"])
        paid[p["reference"]] = paid.get(p["reference"], Decimal(0)) + Decimal(p["amount_czk"])
    rec = {"unpaid": [i["invoice_id"] for i in inv if i["invoice_id"] not in paid],
           "underpaid": {i["invoice_id"]: _q(Decimal(i["amount_czk"]) - paid[i["invoice_id"]])
                         for i in inv if i["invoice_id"] in paid and paid[i["invoice_id"]] < Decimal(i["amount_czk"])},
           "duplicate_payments": dups}
    rates = {r["pair"]: Decimal(r["rate"]) for r in _csv(t["fx_price_comparison_v1"]["rates.csv"])}
    to_czk = {"CZK": Decimal(1), "EUR": rates["EUR/CZK"], "USDT": rates["USDT/USD"] * rates["USD/CZK"]}
    totals = {o["offer_id"]: Decimal(o["amount"]) * to_czk[o["currency"]] * (1 + Decimal(o["fee_percent"]) / 100)
              for o in _csv(t["fx_price_comparison_v1"]["offers.csv"])}
    fx = {"totals_czk": {k: _q(v) for k, v in totals.items()}, "cheapest": min(totals, key=totals.get)}
    leads = {"qualified": [x["lead_id"] for x in _csv(t["lead_qualification_v1"]["leads.csv"])
                           if x["country"] in {"CZ", "SK"} and int(x["employees"]) >= 10
                           and int(x["budget_czk"]) >= 50000 and x["consent"] == "yes"
                           and not x["email"].endswith("@freemail.test")]}
    per: dict[str, dict[str, Decimal]] = {}
    for line in _csv(t["vat_invoice_totals_v1"]["lines.csv"]):
        per.setdefault(line["invoice_id"], {}).setdefault(line["vat_percent"], Decimal(0))
        per[line["invoice_id"]][line["vat_percent"]] += int(line["qty"]) * Decimal(line["unit_price_net_czk"])
    vat_tot = {}
    for inv_id, by_rate in per.items():
        net = sum(by_rate.values(), Decimal(0))
        vat = sum((Decimal(_q(v * Decimal(r) / 100)) for r, v in by_rate.items()), Decimal(0))
        vat_tot[inv_id] = {"net": _q(net), "vat": _q(vat), "gross": _q(net + vat)}
    # SwapMe desk: BUY pays amount x mid x 1.03 rounded UP, SELL receives amount x mid x 0.97 rounded DOWN.
    desk = {}
    for r in _csv(t["swap_desk_quote_v1"]["requests.csv"]):
        raw = Decimal(r["amount"]) * Decimal(r["mid_czk_per_unit"])
        if r["side"] == "BUY":
            desk[r["request_id"]] = str((raw * Decimal("1.03")).quantize(Decimal("0.01"), rounding="ROUND_UP"))
        else:
            desk[r["request_id"]] = str((raw * Decimal("0.97")).quantize(Decimal("0.01"), rounding="ROUND_DOWN"))
    return {"invoice_reconciliation_v1": rec, "fx_price_comparison_v1": fx,
            "lead_qualification_v1": leads, "vat_invoice_totals_v1": {"totals": vat_tot},
            "swap_desk_quote_v1": {"czk": desk}}


def test_owner_example_currency_math():
    assert Decimal(1000) * Decimal(25) * Decimal("1.03") == Decimal("25750.00")


async def test_business_exercises_pass_with_independent_answers_and_fail_with_wrong(env, tmp_path):
    cat = (await env.client.get("/api/learning/business-exercises")).json()["items"]
    assert len(cat) >= 3
    answers = independent_answers()
    assert answers["fx_price_comparison_v1"]["totals_czk"]["OFF-A"] == "25750.00"
    for item in cat:
        eid = item["exercise_id"]
        task = (await env.client.get(f"/api/learning/business-exercises/{eid}")).json()
        assert task["simulated"] is True and "truth" not in json.dumps(task)
        r = (await env.client.post(f"/api/learning/business-exercises/{eid}/check",
                                   json={"answer": answers[eid]})).json()
        assert r["passed"] is True, (eid, r)
        assert len(r["receipt_sha256"]) == 64
        r = (await env.client.post(f"/api/learning/business-exercises/{eid}/check", json={"answer": {}})).json()
        assert r["passed"] is False and r["fields_failed"] >= 1
    # the ground truth file lives outside the data dir and the repo
    keys = bx.keys_dir()
    assert (keys / "fx_price_comparison_v1.json").is_file()
    assert not str(keys).startswith(str(env.svc.settings.data_dir.resolve()))
    assert not str(keys).startswith(str(Path(bx.__file__).resolve().parents[2]))


async def test_negative_keys_inside_agent_root_are_refused(env, monkeypatch):
    monkeypatch.setenv("BCC_EXERCISE_KEYS_DIR", str(env.svc.settings.data_dir / "keys"))
    r = await env.client.post("/api/learning/business-exercises/lead_qualification_v1/check",
                              json={"answer": {"qualified": []}})
    assert r.status_code == 409
    assert not (env.svc.settings.data_dir / "keys").exists()


def test_checker_is_independent_of_generator_module():
    import ast
    src = Path(sys.modules["bcc.business_exercise_checker"].__file__).read_text(encoding="utf-8")
    imported = set()
    for node in ast.walk(ast.parse(src)):
        if isinstance(node, ast.ImportFrom):
            imported.add(node.module or "")
            imported.update(a.name for a in node.names)
        elif isinstance(node, ast.Import):
            imported.update(a.name for a in node.names)
    assert not any("business_exercises" in name for name in imported), imported
    assert "_PLANTED" not in src


def test_checker_refuses_missing_key_and_oversized_answer(tmp_path):
    from bcc.business_exercise_checker import CheckerError
    with pytest.raises(CheckerError):
        check_answer(tmp_path, "fx_price_comparison_v1", FX_RIGHT)
    bx.materialize_key("fx_price_comparison_v1", [])
    with pytest.raises(CheckerError):
        check_answer(bx.keys_dir(), "fx_price_comparison_v1", {"x": "y" * 20_000})


# ------------------------------------------------------------------ tools

async def test_lesson_tool_writes_as_bossman_and_never_private(env):
    ctx = ToolContext(svc=env.svc, task={}, run_id=0, agent={})
    spec_add = next(s for s in ml.SPECS if s.name == "memory.lesson.add")
    assert spec_add.default_effect == "ask"
    res = await ml.tool_lesson_add({"title": "Tool lesson", "body": "Reconcile invoices by reference id.",
                                    "category": "business", "observed_at": "2026-10-31",
                                    "author": "owner"}, ctx)
    assert not res.error and res.data["lesson"]["author"] == "bossman"
    assert res.data["lesson"]["source"] == "bossman-run:0"
    res = await ml.tool_lesson_add({"title": "x", "body": "y", "category": "personal",
                                    "observed_at": "2026-10-31"}, ctx)
    assert res.error
    res = await ml.tool_lesson_add({"title": "x", "body": "y", "category": "business",
                                    "observed_at": "2026-10-31", "status": "verified_by_test"}, ctx)
    assert res.error
    found = await ml.tool_lesson_search({"query": "reconcile"}, ctx)
    assert "status=lesson_recorded" in found.content


# ------------------------------------------------------------------ 4. read-only readiness probe

def _load_probe():
    import importlib.util
    path = Path(__file__).resolve().parents[2] / "tools" / "codex_learning_readiness.py"
    spec = importlib.util.spec_from_file_location("codex_learning_readiness", path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


async def test_readiness_probe_is_read_only_and_reports_capabilities(env):
    mod = _load_probe()
    with pytest.raises(mod.ReadOnlyViolation):
        await mod.ReadOnlyClient(env.client).request("POST", "/api/computer/stop")
    with pytest.raises(mod.ReadOnlyViolation):
        mod.ReadOnlyClient(env.client).post
    report = await mod.run_probes(env.client)
    by = {r["capability"]: r["status"] for r in report["results"]}
    for cap in ("memory_lessons", "memory_lessons_list", "freshness_check", "facts_store",
                "business_exercises", "browser_gates", "approvals"):
        assert by.get(cap) == "PASS", (cap, report)
    assert report["read_only"] is True
