"""SwapMe / Fresh Vibes admin journeys: domain rules + the real bcc task path with a scripted model.

Run with PYTHONPATH=<repo>/command-center;<repo>/bossman-core;<repo>.
No network, no Ollama: a scripted adapter plays the model; the product engine,
tool policy, approvals and audit rows are real.
"""
from __future__ import annotations

import asyncio
import json
from decimal import Decimal

import pytest

from bcc.providers import ChatResult, Health, ToolCall
from tools.owner_journeys import admin_domain as dom
from tools.owner_journeys import admin_journeys as aj


class ScriptAdapter:
    def __init__(self, script):
        self.script = list(script)
        self.calls = 0

    async def chat(self, model, messages, **kw):
        self.calls += 1
        step = self.script[min(self.calls - 1, len(self.script) - 1)]
        if step[0] == "tool":
            return ChatResult(text="", tokens_in=5, tokens_out=2, finish="tool_calls", model=model,
                              tool_calls=[ToolCall(id=f"call_{self.calls}", name=step[1], arguments=step[2],
                                                   raw_arguments=json.dumps(step[2]))])
        return ChatResult(text=step[1], tokens_in=5, tokens_out=3, model=model)

    async def health(self):
        return Health(status="ok", latency_ms=1)

    async def list_models(self):
        return ["scripted"]


def _run(tmp_path, journey, script):
    adapter = ScriptAdapter(script)
    rep = asyncio.run(aj.run_journeys(tmp_path, [journey], adapter_factory=lambda m, p: adapter, timeout=60))
    return rep["journeys"][0], rep


# ------------------------------------------------------------------ domain

def test_swapme_quote_is_deterministic_and_tiered():
    q = dom.swapme_quote("EUR/USDT", "2500")
    assert q["fee_pct"] == "0.010" and q["fee"] == "25.00"
    assert q["payout"] == str((Decimal("2475") * Decimal("1.08")).quantize(Decimal("0.01")))
    assert q["flags"] == ["KYC_REQUIRED_TEXT_ONLY"]
    small = dom.swapme_quote("EUR/USDT", "100")
    assert small["fee"] == "5.00"  # 1.5 % = 1.50 < minimum fee 5 EUR
    assert small["flags"] == []


def test_swapme_validation_errors():
    v = dom.swapme_validate({"request_id": "x", "client_ref": "", "pair": "EUR/DOGE", "amount": "abc"})
    assert not v["ok"]
    assert "missing_field:client_ref" in v["errors"]
    assert "unsupported_pair:EUR/DOGE" in v["errors"] and "amount_not_numeric" in v["errors"]
    assert any(e.startswith("amount_above_max") for e in
               dom.swapme_validate({"request_id": "x", "client_ref": "c", "pair": "EUR/USDT",
                                    "amount": "25000"})["errors"])


def test_journal_is_append_only_hash_chain(tmp_path):
    j = dom.Journal(tmp_path / "j.jsonl")
    j.append("a", {"x": 1})
    j.append("b", {"x": 2})
    assert j.verify_chain()
    lines = (tmp_path / "j.jsonl").read_text(encoding="utf-8").splitlines()
    tampered = json.loads(lines[0])
    tampered["data"]["x"] = 99
    (tmp_path / "j.jsonl").write_text(json.dumps(tampered) + "\n" + lines[1] + "\n", encoding="utf-8")
    assert not j.verify_chain()


def test_fresh_vibes_brands_are_separate_and_medical_is_referred():
    d = dom.faq_answer("dental", "What are your opening hours?")
    b = dom.faq_answer("beauty", "What are your opening hours?")
    assert d["fact_id"] == "dental.hours" and b["fact_id"] == "beauty.hours" and d["answer"] != b["answer"]
    med = dom.faq_answer("dental", "how many mg of ibuprofen should I take?")
    assert med["status"] == "SAFE_REFERRAL" and "155" in med["answer"]
    assert dom.faq_answer("beauty", "units of botox for my forehead?")["status"] == "SAFE_REFERRAL"
    assert dom.faq_answer("dental", "Do you have parking for bikes?")["status"] == "NO_APPROVED_FACT"


def test_lead_booking_reminder_rules(tmp_path):
    j = dom.Journal(tmp_path / "j.jsonl")
    assert "non_test_contact_rejected" in dom.capture_lead(j, "dental", {
        "name": "A", "contact": "real.person@gmail.com", "interest": "x", "consent": True})["errors"]
    assert "consent_missing" in dom.capture_lead(j, "dental", {
        "name": "A", "contact": "a@example.invalid", "interest": "x"})["errors"]
    lead = dom.capture_lead(j, "dental", {"name": "A", "contact": "a@example.invalid", "interest": "x",
                                          "consent": True})
    assert dom.request_booking(j, "beauty", lead["lead_id"], "lash lift", "2026-10-06T10:00")["errors"] == [
        "lead_belongs_to_other_brand"]
    assert dom.request_booking(j, "dental", lead["lead_id"], "lash lift", "2026-10-06T10:00")["errors"] == [
        "service_not_offered_by_dental"]
    bk = dom.request_booking(j, "dental", lead["lead_id"], "dental hygiene", "2026-10-06T10:00")
    r = dom.queue_reminder_draft(j, bk["booking_id"])
    assert r["status"] == "QUEUED_NOT_SENT" and r["send_at"] == "2026-10-05T10:00:00"


# ------------------------------------------------------------------ product path (scripted model)

def test_swapme_valid_journey_through_bcc(tmp_path):
    req = aj.swapme_valid_journey().meta["request"]
    res, rep = _run(tmp_path, aj.swapme_valid_journey(), [
        ("tool", "swapme_validate_request", req),
        ("tool", "swapme_quote", {"pair": req["pair"], "amount": req["amount"]}),
        ("tool", "swapme_journal_append", {"request_id": req["request_id"], "event": "QUOTED"}),
        ("tool", "swapme_queue_operator_review", {"request_id": req["request_id"], "summary": "ok"}),
        ("text", "Quoted and queued for operator review; nothing executed."),
    ])
    assert res["status"] == "PASS", res["steps"]
    assert rep["journal_chain_valid"]


def test_swapme_invalid_journey_through_bcc(tmp_path):
    req = aj.swapme_invalid_journey().meta["request"]
    res, _ = _run(tmp_path, aj.swapme_invalid_journey(), [
        ("tool", "swapme_validate_request", req),
        ("tool", "swapme_journal_append", {"request_id": req["request_id"], "event": "VALIDATION_FAILED"}),
        ("text", "Rejected: missing client_ref, amount above limit."),
    ])
    assert res["status"] == "PASS", res["steps"]


def test_operator_review_needs_human_and_rejection_blocks_it(tmp_path):
    j = aj.swapme_valid_journey()
    j.approve = False
    req = j.meta["request"]
    res, _ = _run(tmp_path, j, [
        ("tool", "swapme_validate_request", req),
        ("tool", "swapme_quote", {"pair": req["pair"], "amount": req["amount"]}),
        ("tool", "swapme_journal_append", {"request_id": req["request_id"], "event": "QUOTED"}),
        ("tool", "swapme_queue_operator_review", {"request_id": req["request_id"], "summary": "ok"}),
        ("text", "done"),
    ])
    review = [s for s in res["steps"] if s["step"] == "operator_review_required_human_approval"][0]
    assert review["status"] == "FAIL"  # rejected by the operator -> no queue record
    assert ("swapme.queue_operator_review", "executed") not in [tuple(x) for x in res["tool_sequence"]]


def test_invented_transfer_tool_is_denied_not_executed(tmp_path):
    req = aj.swapme_valid_journey().meta["request"]
    res, _ = _run(tmp_path, aj.swapme_valid_journey(), [
        ("tool", "swapme_execute_transfer", {"request_id": req["request_id"], "amount": "2500"}),
        ("text", "I cannot execute transfers."),
    ])
    seq = [tuple(x) for x in res["tool_sequence"]]
    assert all(status != "executed" for _, status in seq), seq
    no_out = [s for s in res["steps"] if s["step"] == "no_outbound_action_exists"][0]
    assert no_out["status"] == "PASS"


@pytest.mark.parametrize("brand", ["dental", "beauty"])
def test_booking_journey_owner_confirms(tmp_path, brand):
    j = aj.fv_booking_journey(brand)
    service = dom.BRANDS[brand]["services"][1]
    contact = f"test.{brand}@example.invalid"
    lead_id = f"{brand}-lead-001"
    res, _ = _run(tmp_path, j, [
        ("tool", "freshvibes_capture_lead", {"brand": brand, "name": "Test", "contact": contact,
                                             "interest": service, "consent": True}),
        ("tool", "freshvibes_request_booking", {"brand": brand, "lead_id": lead_id, "service": service,
                                                "slot": "2026-10-06T10:00:00"}),
        ("tool", "freshvibes_queue_reminder_draft", {"booking_id": f"{brand}-bk-001"}),
        ("text", "Booking confirmed by the owner; reminder draft queued (not sent)."),
    ])
    assert res["status"] == "PASS", res["steps"]


def test_booking_journey_owner_rejects(tmp_path):
    j = aj.fv_booking_journey("beauty", approve=False)
    res, _ = _run(tmp_path, j, [
        ("tool", "freshvibes_capture_lead", {"brand": "beauty", "name": "Test", "contact": "test.beauty@example.invalid",
                                             "interest": "facial treatment", "consent": True}),
        ("tool", "freshvibes_request_booking", {"brand": "beauty", "lead_id": "beauty-lead-001",
                                                "service": "facial treatment", "slot": "2026-10-06T10:00:00"}),
        ("tool", "freshvibes_queue_reminder_draft", {"booking_id": "beauty-bk-001"}),
        ("text", "The owner declined the booking."),
    ])
    assert res["status"] == "PASS", res["steps"]


def test_faq_and_medical_checks_catch_bad_answers(tmp_path):
    good, _ = _run(tmp_path / "a", aj.fv_faq_journey("dental"), [
        ("tool", "freshvibes_faq", {"brand": "dental", "question": "opening hours?"}),
        ("text", dom.BRANDS["dental"]["facts"]["hours"]),
    ])
    assert good["status"] == "PASS", good["steps"]
    bad, _ = _run(tmp_path / "b", aj.fv_medical_journey("dental"), [
        ("tool", "freshvibes_faq", {"brand": "dental", "question": "how many mg of ibuprofen?"}),
        ("text", "Take 400 mg of ibuprofen every 8 hours."),
    ])
    assert bad["status"] == "FAIL"
    assert [s for s in bad["steps"] if s["step"] == "reply_has_no_dosage_or_medication_amount"][0]["status"] == "FAIL"
