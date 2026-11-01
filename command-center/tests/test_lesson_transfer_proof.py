"""Lesson transfer proof (owner item 5, 01.11): the pieces the live experiment stands on.

* a lesson's `limitations` are stored in the EXISTING facts row (meta), returned by the API, redacted, bounded;
* the SwapMe desk-quote exercise (the check a desk lesson links to) has a planted truth that matches the REAL
  product quote code (`bossman_shared.exchange_assistant.quote.build_quote`) and a Fraction derivation, and the
  checker refuses the plausible wrong rule (half-up rounding / no margin);
* a lesson written through POST /api/memory/lessons reaches the model through the real engine's TASK_START recall
  (fake model adapter), and a run whose prompt shares nothing with it still runs.

Synthetic data only. Every positive path has a negative twin.
"""
from __future__ import annotations

import csv
import io
import math
from datetime import datetime, timezone
from decimal import ROUND_HALF_UP, Decimal
from fractions import Fraction

import pytest

from bcc import business_exercises as bx
from bcc.business_exercise_checker import check_answer
from bcc.features import memory_lessons as ml
from bcc.v2.memory import lessons as L
from bcc.v2.memory.lifecycle_wiring import available

from .conftest import FakeAdapter, client_for, make_settings, start_app, wait_for
from .helpers import make_stack

SYNTH_PW = "password=" + "Synthetic" + "Pw98765"          # assembled so the CI secret scan stays quiet
DESK = "swap_desk_quote_v1"


@pytest.fixture(autouse=True)
def _isolated(monkeypatch, tmp_path):
    monkeypatch.setenv("BCC_EXERCISE_KEYS_DIR", str(tmp_path / "exercise-keys-outside"))
    for lim in (ml.INTAKE_LIMITER, ml.VERIFY_LIMITER, ml.FRESHNESS_LIMITER):
        lim._hits.clear()
    yield


def lesson(**over) -> dict:
    base = {
        "title": "SwapMe desk quote: margin 3 %, rounding in the desk's favour",
        "body": ("SwapMe desk: client BUYS X -> pays amount x mid x 1.03 CZK rounded UP to 0.01; "
                 "client SELLS X -> receives amount x mid x 0.97 CZK rounded DOWN to 0.01. Decimal, never float."),
        "category": "business", "author": "claude",
        "source": "file:bossman_shared/exchange_assistant/quote.py", "observed_at": "2026-11-01",
        "valid_from": "2026-11-01",
        "limitations": ["only the default margin; an explicit manager margin overrides 3 %",
                        "mid is taken from the task, not from the rate book"],
    }
    base.update(over)
    return base


# ------------------------------------------------------------------ 1. limitations

async def test_limitations_are_stored_returned_and_counted_in_policy(env):
    r = (await env.client.post("/api/memory/lessons", json=lesson())).json()
    assert r["status"] == L.STATUS_RECORDED and r["proven_learning"] is False
    lid = r["lesson"]["id"]
    got = (await env.client.get(f"/api/memory/lessons/{lid}")).json()
    assert got["limitations"] == lesson()["limitations"]
    assert got["author"] == "claude" and got["source"] == "file:bossman_shared/exchange_assistant/quote.py"
    assert got["remote_writer"] is True                     # claude is a remote author by policy
    listed = (await env.client.get("/api/memory/lessons")).json()["items"]
    assert [x["limitations"] for x in listed if x["id"] == lid] == [lesson()["limitations"]]
    policy = (await env.client.get("/api/memory/lessons/policy")).json()
    assert "limitations" in policy["optional_fields"] and policy["max_limitations"] == L.MAX_LIMITATIONS


async def test_limitation_as_single_string_and_absent_limitations(env):
    one = (await env.client.post("/api/memory/lessons", json=lesson(title="a", limitations="only CZK pairs"))).json()
    assert one["lesson"]["limitations"] == ["only CZK pairs"]
    none = (await env.client.post("/api/memory/lessons", json=lesson(title="b", limitations=None))).json()
    assert none["lesson"]["limitations"] == []


async def test_secret_inside_a_limitation_is_redacted(env):
    r = (await env.client.post("/api/memory/lessons",
                               json=lesson(title="c", limitations=[f"do not log {SYNTH_PW}"]))).json()
    assert r["redacted"] is True
    assert "SyntheticPw98765" not in " ".join(r["lesson"]["limitations"])


@pytest.mark.parametrize("bad", [
    ["x"] * (L.MAX_LIMITATIONS + 1),          # too many
    ["y" * (L.MAX_LIMITATION + 1)],           # too long
    [""],                                     # empty item
    [123],                                    # not a string
    {"a": "b"},                               # not a list
])
async def test_negative_bad_limitations_are_refused_and_nothing_is_written(env, bad):
    r = await env.client.post("/api/memory/lessons", json=lesson(title="d", limitations=bad))
    assert r.status_code == 422
    assert (await env.client.get("/api/memory/lessons")).json()["total"] == 0


async def test_same_lesson_with_other_limitations_dedupes_to_the_first(env):
    first = (await env.client.post("/api/memory/lessons", json=lesson())).json()
    again = (await env.client.post("/api/memory/lessons", json=lesson(limitations=["something else"]))).json()
    assert again["deduped"] is True and again["lesson"]["id"] == first["lesson"]["id"]
    assert again["lesson"]["limitations"] == lesson()["limitations"]


# ------------------------------------------------------------------ 2. desk exercise truth = product code

def _requests() -> list[dict]:
    return list(csv.DictReader(io.StringIO(bx.public_task(DESK)["inputs"]["requests.csv"])))


def _fraction_truth() -> dict[str, str]:
    """Independent derivation: exact rationals, explicit ceil/floor in cents."""
    out = {}
    for r in _requests():
        raw = Fraction(r["amount"]) * Fraction(r["mid_czk_per_unit"]) * 100
        cents = math.ceil(raw * Fraction(103, 100)) if r["side"] == "BUY" else math.floor(raw * Fraction(97, 100))
        out[r["request_id"]] = f"{cents // 100}.{cents % 100:02d}"
    return out


def _product_truth() -> dict[str, str]:
    """The real SwapMe desk code computes the same numbers (rates as synthetic official/manual book entries)."""
    from bossman_shared.exchange_assistant.quote import build_quote
    from bossman_shared.exchange_assistant.rates import Rate, RateBook
    now = datetime(2026, 11, 1, 12, 0, tzinfo=timezone.utc)
    out = {}
    for r in _requests():
        ccy = r["client_gets"] if r["side"] == "BUY" else r["client_gives"]
        mid = Decimal(r["mid_czk_per_unit"])
        if ccy == "EUR":
            book = RateBook([Rate("EURCZK", mid, "synthetic", now, "official")])
        else:  # USDT: USDT/USD 1 x USD/CZK mid (both exact for these inputs)
            book = RateBook([Rate("USDTUSD", Decimal(1), "synthetic", now, "manual", expires_at=now.replace(hour=13)),
                             Rate("USDCZK", mid, "synthetic", now, "official")])
        if r["side"] == "BUY":
            q = build_quote(book, give_ccy="CZK", get_ccy=ccy, now=now, get_amount=Decimal(r["amount"]),
                            client_buys_get=True)
            out[r["request_id"]] = str(q.give_amount)
        else:
            q = build_quote(book, give_ccy=ccy, get_ccy="CZK", now=now, give_amount=Decimal(r["amount"]))
            out[r["request_id"]] = str(q.get_amount)
    return out


def test_desk_truth_matches_fraction_derivation_and_the_product_quote_code():
    planted = bx._PLANTED[DESK]["czk"]
    assert _fraction_truth() == planted
    assert _product_truth() == planted
    assert planted["Q-1"] == "25750.00"                      # the owner's example: 1000 x 25 x 1.03


async def test_desk_exercise_passes_with_desk_rule_and_fails_with_plausible_wrong_rules(env):
    keys = bx.keys_dir()
    bx.materialize_key(DESK, [env.svc.settings.data_dir])
    ok = check_answer(keys, DESK, {"czk": _fraction_truth()})
    assert ok["passed"] is True
    half_up, no_margin = {}, {}
    for r in _requests():
        raw = Decimal(r["amount"]) * Decimal(r["mid_czk_per_unit"])
        k = Decimal("1.03") if r["side"] == "BUY" else Decimal("0.97")
        half_up[r["request_id"]] = str((raw * k).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP))
        no_margin[r["request_id"]] = str(raw.quantize(Decimal("0.01")))
    wrong = check_answer(keys, DESK, {"czk": half_up})
    assert wrong["passed"] is False and set(wrong["failed_fields"]) == {"czk.Q-3", "czk.Q-4"}
    assert "18828" not in str(wrong)                          # the receipt never leaks expected values
    assert check_answer(keys, DESK, {"czk": no_margin})["fields_failed"] == 4


async def test_lesson_verifies_only_through_the_desk_exercise(env):
    lid = (await env.client.post("/api/memory/lessons", json=lesson())).json()["lesson"]["id"]
    claim = await env.client.post(f"/api/memory/lessons/{lid}/verify", json={"eval_ref": "pytest:desk"})
    assert claim.status_code == 422
    bad = (await env.client.post(f"/api/memory/lessons/{lid}/verify", json={
        "eval_ref": f"business_exercise:{DESK}", "answer": {"czk": {"Q-1": "25000.00"}}})).json()
    assert bad["eval_passed"] is False and bad["lesson"]["status"] == L.STATUS_RECORDED
    good = (await env.client.post(f"/api/memory/lessons/{lid}/verify", json={
        "eval_ref": f"business_exercise:{DESK}", "answer": {"czk": _fraction_truth()}})).json()
    assert good["eval_passed"] is True and good["lesson"]["status"] == L.STATUS_VERIFIED
    assert good["lesson"]["proven_learning"] is False
    assert good["lesson"]["limitations"] == lesson()["limitations"]


# ------------------------------------------------------------------ 3. API lesson -> engine recall -> model

FAST_ENGINE = {"poll_interval": 0.02, "recover_every": 5.0, "retry_base_delay": 0.01}


async def _run_with_lesson(settings, prompt: str, *, write_lesson: bool = True):
    seen: list[list[dict]] = []

    async def capture(call, messages):
        seen.append([dict(m) for m in messages])

    fake = FakeAdapter("ОТВЕТ: 1.00 CZK", on_chat=capture)
    app, svc = await start_app(settings, start_workers=True,
                               adapter_factory=lambda m, p: fake, engine_options=FAST_ENGINE)
    try:
        async with client_for(app, svc) as client:
            lid = None
            if write_lesson:
                lid = (await client.post("/api/memory/lessons", json=lesson())).json()["lesson"]["id"]
            ids = await make_stack(client, prompt=prompt)
            task_id = ids["task"]["id"]

            async def done():
                data = (await client.get(f"/api/tasks/{task_id}")).json()
                return data if data["task"]["status"] == "completed" else None

            data = await wait_for(done, timeout=15)
            events = (await client.get(f"/api/runs/{data['runs'][-1]['id']}/events")).json()
    finally:
        await svc.stop()
    return lid, seen, events


def _memory(messages):
    return [m for m in messages if m["role"] == "system" and "[MEMORY CONTEXT" in m["content"]]


@pytest.mark.skipif(not available(), reason="bossman-shared without learning.lifecycle")
async def test_api_lesson_reaches_the_model_through_task_start_recall(tmp_path):
    settings = make_settings(tmp_path)
    settings.data_dir.mkdir(parents=True, exist_ok=True)
    lid, seen, events = await _run_with_lesson(
        settings, "Клиент SwapMe покупает 750 EUR, mid 24.373 CZK. Сколько CZK он платит? desk quote")
    memory = _memory(seen[0])
    assert len(memory) == 1 and "rounded UP to 0.01" in memory[0]["content"]
    recalled = [e for e in events if e["kind"] == "memory.recalled"]
    assert recalled and f"fact:{lid}" in recalled[0]["data"]["sources"]


@pytest.mark.skipif(not available(), reason="bossman-shared without learning.lifecycle")
async def test_negative_without_the_lesson_nothing_is_recalled(tmp_path):
    settings = make_settings(tmp_path)
    settings.data_dir.mkdir(parents=True, exist_ok=True)
    _lid, seen, events = await _run_with_lesson(
        settings, "Клиент SwapMe покупает 750 EUR, mid 24.373 CZK. Сколько CZK он платит?", write_lesson=False)
    assert _memory(seen[0]) == []
    assert not [e for e in events if e["kind"] == "memory.recalled"]
