"""Jeff paid documents over a MANUAL bank-transfer invoice (Telegram Stars paused).

The owner issues an invoice, the money arrives outside Bossman, the owner marks it paid; only that mark grants the
DOCS30 limits in the existing Stars ledger. No model, no network: the answer to /ask is a stub string.
"""
from __future__ import annotations

import socket
import time
from decimal import Decimal

import httpx
import pytest

from bcc.pit import manual_invoice as mi
from bcc.pit import paid_docs as pd_mod
from bcc.pit.manual_invoice import InvoiceRejected, ManualInvoices, NotOwner
from bcc.pit.stars_ledger import DAY, DOCS30, TRIAL_REQUESTS, StarsLedger
from bcc.pit.vault import PersonaVault

GUEST, OTHER = 202, 303
CONTRACT = ("Срок оплаты — 10 рабочих дней с момента подписания акта.\n\n"
            "Гарантия на выполненные работы — 12 месяцев.").encode()


@pytest.fixture
def book(tmp_path):
    return ManualInvoices(StarsLedger(tmp_path / "l.sqlite3"))


@pytest.fixture
def docs(tmp_path):
    return pd_mod.PaidDocs(tmp_path, PersonaVault(tmp_path, b"s" * 32))


@pytest.fixture
def no_network(monkeypatch):
    """Any socket connect or httpx send during the test is recorded and refused."""
    attempts: list[str] = []

    def refuse(*args, **kwargs):
        attempts.append(repr(args[1:2]))
        raise OSError("network is forbidden in this test")

    monkeypatch.setattr(socket.socket, "connect", refuse)
    monkeypatch.setattr(httpx.Client, "send", refuse)
    monkeypatch.setattr(httpx.AsyncClient, "send", refuse)
    return attempts


K = "k" * 64


def issue(book, **kw):
    args = dict(person_key=K, user_id=GUEST, package_id="docs30", amount="9.90", currency="EUR", owner=True)
    args.update(kw)
    return book.issue(**args)


# -- legal path ---------------------------------------------------------------------------------
def test_issue_grants_nothing_and_mark_paid_grants_the_docs30_limits(book):
    row = issue(book)
    assert row["status"] == "issued" and row["amount"] == "9.90" and row["currency"] == "EUR"
    assert row["ref"].startswith("JD-")
    assert book.ledger.entitlement(K)["has_paid"] is False                 # an invoice alone grants nothing
    out = book.mark_paid(row["ref"], owner=True)
    assert out.status == "granted" and out.requests == DOCS30.requests
    ent = book.ledger.entitlement(K)
    assert ent["paid_requests_left"] == DOCS30.requests and ent["doc_limit"] == DOCS30.doc_slots
    assert abs(out.valid_until - (time.time() + DOCS30.days * DAY)) < 60
    assert book.get(row["ref"])["status"] == "paid"
    pay = book.ledger.payment(mi.charge_id_of(row["ref"]))
    assert pay["status"] == "paid" and pay["stars"] == 0                   # Stars columns stay Stars-only
    assert book.ledger.report()["stars_gross_received"] == 0


def test_refs_are_unique_and_amounts_are_exact_decimals(book):
    refs = {issue(book, amount=a)["ref"] for a in ("1", "199,5", "0.01")}
    assert len(refs) == 3
    assert {r["amount"] for r in book.listing()} == {"1.00", "199.50", "0.01"}


def test_report_keeps_currencies_refunds_and_test_money_apart(tmp_path):
    ledger = StarsLedger(tmp_path / "l.sqlite3")
    real, test = ManualInvoices(ledger), ManualInvoices(ledger, test_env=True)
    a = issue(real, amount="9.90", currency="EUR")["ref"]
    b = issue(real, amount="250", currency="CZK", person_key="b" * 64)["ref"]
    c = issue(test, amount="900", currency="RUB", person_key="c" * 64)["ref"]
    for ref in (a, b, c):
        assert real.mark_paid(ref, owner=True).status == "granted"
    real.refund(b, owner=True)
    rep = real.report()["by_currency"]
    assert rep["EUR"] == {"paid": "9.90", "refunded": "0.00", "net": "9.90", "test_paid": "0.00"}
    assert rep["CZK"]["net"] == "0.00" and rep["CZK"]["refunded"] == "250.00"
    assert rep["RUB"]["paid"] == "0.00" and rep["RUB"]["test_paid"] == "900.00"
    assert ledger.payment(mi.charge_id_of(c))["test_env"] == 1
    assert ledger.report()["paying_users"] == 1                            # test payer and refunded payer excluded


def test_full_flow_through_paid_docs_with_a_stub_answer(docs, no_network):
    key = docs.vault.key_for_telegram(GUEST)
    text = docs.manual_command("/invoice", f"{GUEST} docs30 9.90 EUR", owner=True)
    assert "выписан" in text and "Участнику ничего не отправлено" in text
    ref = docs.invoices.listing()[0]["ref"]
    assert "сохранён" in docs.add_document(key, "договор.txt", CONTRACT, owner=False)[0]
    for _ in range(TRIAL_REQUESTS):                                        # burn the free trial first
        prepared = docs.prepare(key, "ask", "срок оплаты", owner=False)
        docs.finish(prepared, "Срок оплаты — 10 рабочих дней [1].")
    assert isinstance(docs.prepare(key, "ask", "срок оплаты", owner=False), pd_mod.Refusal)
    assert "отмечен оплаченным" in docs.manual_command("/paid", ref, owner=True)
    prepared = docs.prepare(key, "ask", "срок оплаты", owner=False)
    assert isinstance(prepared, pd_mod.Prepared) and prepared.pool == "paid"
    answer = docs.finish(prepared, "Срок оплаты — 10 рабочих дней [1].")    # STUB: no model is called
    assert "Источники:" in answer
    assert docs.ledger.entitlement(key)["paid_requests_left"] == DOCS30.requests - 1
    assert "Счета на перевод" in docs.payments_text() and "EUR: оплачено 9.90" in docs.payments_text()
    assert no_network == []


# -- negative controls --------------------------------------------------------------------------
def test_non_owner_cannot_issue_mark_paid_cancel_or_refund(book, docs):
    ref = issue(book)["ref"]
    for call in (lambda: book.mark_paid(ref, owner=False), lambda: book.cancel(ref, owner=False),
                 lambda: book.refund(ref, owner=False), lambda: issue(book, owner=False),
                 lambda: book.mark_paid(ref, owner="yes")):                # truthy is not the owner
        with pytest.raises(NotOwner):
            call()
    assert book.ledger.entitlement(K)["has_paid"] is False
    # Through paid_docs a guest's /paid is not even recognised (None: the runtime keeps dispatching).
    docs_ref = docs.invoices.issue(docs.vault.key_for_telegram(GUEST), GUEST, "docs30", "5", "EUR", owner=True)["ref"]
    for command, arg in (("/paid", docs_ref), ("/invoice", f"{GUEST} docs30 5 EUR"), ("/invoices", ""),
                         ("/cancel_invoice", docs_ref), ("/refund_invoice", docs_ref)):
        assert docs.manual_command(command, arg, owner=False) is None
    assert docs.ledger.entitlement(docs.vault.key_for_telegram(GUEST))["has_paid"] is False


def test_unknown_ref_grants_nothing(book, docs):
    assert book.mark_paid("JD-19700101-000000", owner=True).status == "unknown"
    assert book.refund("nope", owner=True).status == "unknown"
    assert book.cancel("nope", owner=True).status == "unknown"
    assert "не найден" in docs.manual_command("/paid", "JD-nope", owner=True)
    assert book.ledger.entitlement(K)["has_paid"] is False


def test_double_mark_paid_does_not_double_the_entitlement_also_after_restart(tmp_path):
    path = tmp_path / "l.sqlite3"
    first = ManualInvoices(StarsLedger(path))
    ref = issue(first)["ref"]
    assert first.mark_paid(ref, owner=True).status == "granted"
    assert first.mark_paid(ref, owner=True).status == "duplicate"
    first.ledger.close()
    second = ManualInvoices(StarsLedger(path))                            # restart: no regrant, access kept
    assert second.mark_paid(ref, owner=True).status == "duplicate"
    ent = second.ledger.entitlement(K)
    assert ent["paid_requests_left"] == DOCS30.requests
    assert abs(ent["valid_until"] - (time.time() + DOCS30.days * DAY)) < 60   # not 60 days
    assert len(second.ledger.person_payments(K)) == 1


def test_an_expired_invoice_cannot_be_marked_paid(book):
    ref = issue(book)["ref"]
    late = time.time() + (mi.INVOICE_TTL_DAYS + 1) * DAY
    assert book.listing(now=late)[0]["status"] == "expired"
    assert book.mark_paid(ref, owner=True, now=late).status == "expired"
    assert book.mark_paid(ref, owner=True).status == "expired"           # stays closed, also "back in time"
    assert book.ledger.entitlement(K)["has_paid"] is False


def test_cancelled_then_paid_is_refused(book, docs):
    ref = issue(book)["ref"]
    assert book.cancel(ref, owner=True).status == "cancelled"
    assert book.mark_paid(ref, owner=True).status == "cancelled"
    assert book.ledger.entitlement(K)["has_paid"] is False
    paid = issue(book)["ref"]
    book.mark_paid(paid, owner=True)
    assert book.cancel(paid, owner=True).status == "paid"                 # a paid invoice is refunded, not cancelled
    assert book.ledger.entitlement(K)["has_paid"] is True


def test_refund_revokes_the_unused_rest_and_is_idempotent(book):
    ref = issue(book)["ref"]
    assert book.refund(ref, owner=True).status == "not_paid"
    book.mark_paid(ref, owner=True)
    for _ in range(4):
        assert book.ledger.consume(K, "ask").pool == "paid"
    out = book.refund(ref, owner=True)
    assert (out.status, out.revoked_requests) == ("refunded", DOCS30.requests - 4)
    assert book.refund(ref, owner=True).status == "duplicate"
    assert book.mark_paid(ref, owner=True).status == "refunded"          # a refunded invoice never regrants
    ent = book.ledger.entitlement(K)
    assert ent["has_paid"] is False and ent["paid_requests_left"] == 0
    assert book.ledger.payment(mi.charge_id_of(ref))["status"] == "refunded"


@pytest.mark.parametrize("amount", ["0", "-5", "0.00", "abc", "", "nan", "inf", "1.001", "1e7", "10000000"])
def test_bad_amounts_are_refused(book, amount):
    with pytest.raises(InvoiceRejected):
        issue(book, amount=amount)
    assert book.listing() == []


@pytest.mark.parametrize("currency", ["USD", "XTR", "", "eurx", "€"])
def test_bad_currency_package_or_user_is_refused(book, currency):
    with pytest.raises(InvoiceRejected):
        issue(book, currency=currency)
    with pytest.raises(InvoiceRejected):
        issue(book, package_id="docs999")
    with pytest.raises(InvoiceRejected):
        issue(book, user_id=0)
    assert book.listing() == []


def test_owner_command_texts_refuse_bad_input_without_side_effects(docs):
    assert docs.manual_command("/invoice", f"{GUEST} docs30 0 EUR", owner=True).startswith("Сумма")
    assert docs.manual_command("/invoice", f"{GUEST} docs30 5 USD", owner=True).startswith("Валюта")
    assert docs.manual_command("/invoice", "bob docs30 5 EUR", owner=True).startswith("Формат")
    assert docs.manual_command("/paid", "", owner=True).startswith("Формат")
    assert docs.manual_command("/refund", "x", owner=True) is None        # the Stars /refund is not ours
    assert docs.invoices.listing() == []


def test_open_invoices_per_person_are_bounded(book):
    for _ in range(mi.MAX_OPEN_INVOICES):
        issue(book)
    with pytest.raises(InvoiceRejected):
        issue(book)


def test_nothing_is_sent_and_the_module_has_no_network_path(book, no_network):
    ref = issue(book)["ref"]
    book.mark_paid(ref, owner=True)
    book.refund(ref, owner=True)
    assert no_network == []
    source = open(mi.__file__, encoding="utf-8").read()
    for forbidden in ("httpx", "telegram", "payment_call", "sendMessage", "urllib", "socket", "import requests"):
        assert forbidden not in source.split('"""', 2)[2], forbidden      # code below the module docstring


def test_amount_parser_returns_decimal():
    assert mi.parse_amount("199,5") == Decimal("199.50")
    assert mi.parse_currency(" czk ") == "CZK"
