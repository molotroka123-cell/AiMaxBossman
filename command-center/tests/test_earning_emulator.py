"""bcc.earning_emulator: a SIMULATION of earning on a public remote listing. It never applies, messages, logs in, pays or touches the network."""
from __future__ import annotations

import ast
import json
from pathlib import Path

import pytest

from bcc import earning_emulator as em

GOOD = "Remote QA Tester\nWe need a tester for 10 hours of testing, $20-30/hr. You will write bug reports and test cases for our web app."
FIXED = "Remote blog writer wanted. Write five articles about gardening. $250 fixed for the whole project. Work from anywhere in the world."
NOPAY = "Remote researcher: collect public facts about local bakeries and put them in a spreadsheet for us, anywhere."
SCAM = "Remote data entry assistant. Pay a small registration fee of $50 for training and buy gift cards for the equipment. $30/hr."


def test_parse_reads_title_kind_remote_and_the_midpoint_of_a_rate_range():
    l = em.parse_listing(GOOD)
    assert (l.title, l.remote, l.kind, l.pay_unit, l.currency, l.pay_amount, l.hours_hint) == ("Remote QA Tester", True, "testing", "hour", "$", 25.0, 10.0)
    f = em.parse_listing(FIXED)
    assert f.pay_unit == "fixed" and f.pay_amount == 250.0 and f.kind == "writing"
    assert em.parse_listing(NOPAY).pay_unit == "unknown"


def test_invoice_is_always_simulated_and_its_basis_says_where_the_number_comes_from():
    inv = em.simulated_invoice(em.parse_listing(GOOD))
    assert inv["simulated"] is True and inv["amount"] == 250.0 and "из объявления" in inv["basis"]
    assert em.simulated_invoice(em.parse_listing(FIXED))["amount"] == 250.0
    none = em.simulated_invoice(em.parse_listing(NOPAY))
    assert none["amount"] == 0.0 and "не указана" in none["basis"]
    assumed = em.simulated_invoice(em.parse_listing("Remote translator, $15/hr, anywhere."))
    assert "допущение" in assumed["basis"] and assumed["amount"] == 30.0


@pytest.mark.parametrize("sign", ["pay a registration fee", "gift cards", "send us money", "passport", "seed phrase", "recruitment fee"])
def test_scam_signals_are_refused_and_nothing_is_invoiced(sign):
    rec = em.run(f"Remote helper wanted anywhere. $20/hr. Please {sign} first.", "synthetic")
    assert rec["verdict"] == "REFUSED" and rec["invoice"] is None and rec["deliverable_sha256"] is None and rec["reasons"]


def test_an_offline_or_unclear_listing_is_refused_as_not_remote():
    rec = em.run("Warehouse packer, on site, $18/hr.", "synthetic")
    assert rec["verdict"] == "REFUSED" and any("удалённая" in r for r in rec["reasons"])


def test_a_good_listing_gets_a_deliverable_and_a_simulated_invoice_and_money_is_zero(tmp_path):
    led = tmp_path / "ledger.jsonl"
    rec = em.run(GOOD, "SYNTHETIC_FIXTURE", ledger=led, now=0)
    assert rec["verdict"] == "SIMULATED_DONE" and rec["SIMULATED"] is True and rec["real_money"] == 0
    assert rec["invoice"]["simulated"] is True and rec["invoice"]["amount"] == 250.0
    assert rec["deliverable_preview"].startswith("ШАБЛОН БЕЗ МОДЕЛИ") and len(rec["deliverable_sha256"]) == 64
    assert rec["ts"] == "1970-01-01T00:00:00Z"
    for done in ("не откликались", "не писали", "не входили", "не платили"):
        assert any(done in x for x in rec["not_done"])


def test_the_ledger_is_append_only_and_each_line_is_a_complete_record(tmp_path):
    led = tmp_path / "l" / "ledger.jsonl"
    a = em.run(GOOD, "s1", ledger=led)
    b = em.run(FIXED, "s2", ledger=led)
    lines = [json.loads(x) for x in led.read_text(encoding="utf-8").splitlines()]
    assert [x["id"] for x in lines] == [a["id"], b["id"]] and a["id"] != b["id"]
    em.run(NOPAY, "s3", ledger=led)
    assert len(led.read_text(encoding="utf-8").splitlines()) == 3


@pytest.mark.parametrize("name", ["gpt-5", "claude-opus", "openrouter-paid", "", "some-unknown"])
def test_only_free_or_local_workers_are_accepted(name):
    with pytest.raises(ValueError):
        em.run(GOOD, "s", worker_name=name)


def test_a_worker_output_is_capped_and_a_failing_worker_is_not_hidden():
    rec = em.run(GOOD, "s", worker=lambda p: "x" * 100000, worker_name="local")
    assert rec["deliverable_chars"] == em.MAX_DELIVERABLE

    def boom(prompt):
        raise RuntimeError("model down")
    with pytest.raises(RuntimeError):
        em.run(GOOD, "s", worker=boom, worker_name="local")                 # nothing is faked: no invoice for work that failed


def test_the_module_performs_no_network_and_no_process_calls():
    """By construction: the emulator imports nothing that can reach the network, a shell or an account."""
    tree = ast.parse(Path(em.__file__).read_text(encoding="utf-8"))
    imported = {a.name.split(".")[0] for n in ast.walk(tree) if isinstance(n, ast.Import) for a in n.names}
    imported |= {n.module.split(".")[0] for n in ast.walk(tree) if isinstance(n, ast.ImportFrom) and n.module}
    assert not imported & {"socket", "urllib", "requests", "httpx", "aiohttp", "subprocess", "smtplib", "http", "playwright", "selenium", "os"}
