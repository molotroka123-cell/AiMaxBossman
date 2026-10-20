"""Jeff paid document assistant on Telegram Stars: ledger, library, runtime wiring, end-to-end over a FAKE Bot API.

The Bot API here is an in-process fake that mimics the documented shapes (pre_checkout_query, successful_payment,
refunded_payment, sendInvoice, answerPreCheckoutQuery, refundStarPayment). It is a SIMULATION: it proves Jeff's
logic, not that Telegram's real Stars environment accepts the calls (that needs the owner's test-environment bot).
"""
from __future__ import annotations

import asyncio
import json
import time

import httpx
import pytest

from bcc.pit import paid_docs as pd_mod
from bcc.pit import runtime as rt
from bcc.pit.stars_ledger import DAILY_CAP_PAID, DAY, DOCS30, TRIAL_REQUESTS, StarsLedger
from bcc.telegram_companion import adapters
from bcc.telegram_companion.adapters import Telegram
from bcc.telegram_companion.config import CompanionError, Person

from .test_pit_runtime import FakeAdapter, make_runtime, make_settings, message, warm
from .test_pit_telegram_fake import FakeBotAPI, run_until

OWNER, GUEST, OTHER = 101, 202, 303
CONTRACT = ("Срок оплаты — 10 рабочих дней с момента подписания акта.\n\n"
            "Штраф за просрочку составляет 0.1% в день от суммы.\n\n"
            "Гарантия на выполненные работы — 12 месяцев.").encode()


@pytest.fixture(autouse=True)
def _env(monkeypatch):
    monkeypatch.setenv(pd_mod.ENV_FLAG, "1")
    monkeypatch.delenv(adapters.TEST_ENV_FLAG, raising=False)


def guest_settings(tmp_path):
    return make_settings(tmp_path, people=(Person(user_id=OWNER, chat_id=OWNER, role="owner"),
                                           Person(user_id=GUEST, chat_id=GUEST, role="guest"),
                                           Person(user_id=OTHER, chat_id=OTHER, role="guest")))


def guest_runtime(tmp_path, adapter=None):
    runtime = make_runtime(tmp_path, adapter=adapter, settings=guest_settings(tmp_path))
    for uid in (OWNER, GUEST, OTHER):
        warm(runtime, runtime.vault.key_for_telegram(uid))
    return runtime


def person_of(runtime, uid):
    return next(p for p in runtime.settings.people if p.user_id == uid)


def say(runtime, uid, text, **extra):
    return asyncio.run(runtime.handle(person_of(runtime, uid), message(text, user_id=uid, **extra)))


def upload(runtime, uid, name="договор.txt", data=CONTRACT, caption=""):
    async def fetch(file_id, max_bytes=0):
        return data
    runtime.telegram.fetch_file = fetch
    doc = {"file_id": "f-" + name, "file_name": name, "mime_type": "text/plain", "file_size": len(data)}
    return asyncio.run(runtime.handle(person_of(runtime, uid),
                                      message(caption, user_id=uid, _document=doc)))


def pay(runtime, uid, charge="ch-1"):
    key = runtime.vault.key_for_telegram(uid)
    inv = runtime.paid_docs.invoice(key, uid)
    out = runtime.paid_docs.on_successful_payment(
        key, uid, {"invoice_payload": inv["payload"], "telegram_payment_charge_id": charge,
                   "currency": "XTR", "total_amount": DOCS30.stars})
    return inv, out


# -- ledger ------------------------------------------------------------------------------------
@pytest.fixture
def ledger(tmp_path):
    return StarsLedger(tmp_path / "l.sqlite3")


def test_pre_checkout_accepts_only_the_open_order_of_that_user_and_amount(ledger):
    payload = ledger.create_order("k" * 64, 7)
    assert ledger.check_pre_checkout(payload, 7, "XTR", DOCS30.stars) == (True, "")
    assert not ledger.check_pre_checkout(payload, 8, "XTR", DOCS30.stars)[0]          # someone else's invoice
    assert not ledger.check_pre_checkout(payload, 7, "XTR", DOCS30.stars - 1)[0]      # amount changed
    assert not ledger.check_pre_checkout(payload, 7, "USD", DOCS30.stars)[0]          # not Stars
    assert not ledger.check_pre_checkout("jd1:docs30:nope", 7, "XTR", DOCS30.stars)[0]
    assert not ledger.check_pre_checkout(payload, 7, "XTR", DOCS30.stars, now=time.time() + 7200)[0]  # stale
    assert ledger.entitlement("k" * 64)["has_paid"] is False                           # pre_checkout grants nothing


def test_a_duplicate_payment_event_credits_once_also_after_restart(tmp_path):
    path = tmp_path / "l.sqlite3"
    first = StarsLedger(path)
    key = "k" * 64
    payload = first.create_order(key, 7)
    args = dict(person_key=key, user_id=7, payload=payload, charge_id="tg-1", currency="XTR",
                total_amount=DOCS30.stars)
    assert first.record_payment(**args).status == "granted"
    assert first.record_payment(**args).status == "duplicate"
    first.close()
    second = StarsLedger(path)                                      # process restart: access must survive
    assert second.record_payment(**args).status == "duplicate"
    ent = second.entitlement(key)
    assert ent["paid_requests_left"] == DOCS30.requests and ent["doc_limit"] == DOCS30.doc_slots
    assert second.report()["stars_gross_received"] == DOCS30.stars    # booked once


def test_refund_revokes_the_unused_rest_and_is_idempotent(ledger):
    key = "k" * 64
    payload = ledger.create_order(key, 7)
    ledger.record_payment(person_key=key, user_id=7, payload=payload, charge_id="tg-1", currency="XTR",
                          total_amount=DOCS30.stars)
    for _ in range(5):
        assert ledger.consume(key, "ask").ok
    out = ledger.record_refund("tg-1")
    assert (out.status, out.revoked_requests, out.user_id) == ("refunded", DOCS30.requests - 5, 7)
    ent = ledger.entitlement(key)
    assert ent["paid_requests_left"] == 0 and ent["has_paid"] is False and ent["doc_limit"] == 2
    assert ledger.record_refund("tg-1").status == "duplicate"
    assert ledger.record_refund("never-seen").status == "unknown"
    assert ledger.report()["stars_refunded"] == DOCS30.stars


def test_money_without_a_matching_order_is_kept_and_grants_nothing(ledger):
    key = "k" * 64
    other = ledger.create_order("z" * 64, 99)
    out = ledger.record_payment(person_key=key, user_id=7, payload=other, charge_id="tg-x", currency="XTR",
                                total_amount=DOCS30.stars)               # a payload that belongs to someone else
    assert out.status == "unmatched"
    assert ledger.entitlement(key)["has_paid"] is False
    assert ledger.report()["stars_unmatched_needs_owner"] == DOCS30.stars
    assert ledger.payment("tg-x")["status"] == "unmatched"


def test_trial_paid_daily_cap_and_a_voided_request_is_given_back(ledger):
    key = "k" * 64
    used = [ledger.consume(key, "ask") for _ in range(TRIAL_REQUESTS)]
    assert all(c.ok and c.pool == "trial" for c in used)
    assert ledger.consume(key, "ask").reason == "no_quota"
    ledger.void(used[0].usage_id)
    assert ledger.consume(key, "ask").ok                              # the voided one is available again
    payload = ledger.create_order(key, 7)
    ledger.record_payment(person_key=key, user_id=7, payload=payload, charge_id="tg-1", currency="XTR",
                          total_amount=DOCS30.stars)
    now = time.time()
    got = [ledger.consume(key, "ask", now) for _ in range(DAILY_CAP_PAID + 1)]
    assert sum(c.ok for c in got) == DAILY_CAP_PAID and got[-1].reason == "daily_cap"
    assert ledger.consume(key, "ask", now + DAY).ok                  # a new day


def test_second_payment_extends_validity_and_expiry_is_reported(ledger):
    key = "k" * 64
    t0 = 1_000_000.0
    for n in (1, 2):
        payload = ledger.create_order(key, 7, now=t0)
        ledger.record_payment(person_key=key, user_id=7, payload=payload, charge_id=f"tg-{n}", currency="XTR",
                              total_amount=DOCS30.stars, now=t0)
    ent = ledger.entitlement(key, now=t0 + 1)
    assert ent["paid_requests_left"] == 2 * DOCS30.requests
    assert ent["valid_until"] == pytest.approx(t0 + 2 * DOCS30.days * DAY)
    late_t = t0 + 3 * DOCS30.days * DAY
    late = ledger.entitlement(key, now=late_t)
    assert late["has_paid"] is False and late["expired"] is True
    for _ in range(TRIAL_REQUESTS):                    # the unused free trial is still the person's
        assert ledger.consume(key, "ask", late_t).pool == "trial"
    assert ledger.consume(key, "ask", late_t).reason == "expired"


def test_report_keeps_stars_test_withdrawn_and_expenses_apart(ledger):
    key = "k" * 64
    payload = ledger.create_order(key, 7)
    ledger.record_payment(person_key=key, user_id=7, payload=payload, charge_id="real", currency="XTR",
                          total_amount=DOCS30.stars)
    payload = ledger.create_order(key, 7)
    ledger.record_payment(person_key=key, user_id=7, payload=payload, charge_id="test", currency="XTR",
                          total_amount=DOCS30.stars, test_env=True)
    r = ledger.report()
    assert r["stars_gross_received"] == DOCS30.stars and r["test_environment_stars"] == DOCS30.stars
    assert r["usd_cash_result_recorded"] == 0 and "не прибыль" in r["note"]
    ledger.add_finance("expense", usd=5.0, note="электричество")
    r = ledger.report()
    assert r["usd_cash_result_recorded"] == -5.0 and r["stars_withdrawn"] == 0
    ledger.add_finance("withdrawal", stars=100, usd=1.3, ref="fragment")
    r = ledger.report()
    assert r["stars_withdrawn"] == 100 and r["usd_cash_result_recorded"] == -3.7
    assert r["stars_estimated_on_balance"] == DOCS30.stars - 100
    with pytest.raises(ValueError):
        ledger.add_finance("profit")


# -- documents ---------------------------------------------------------------------------------
def test_each_person_sees_only_their_own_documents(tmp_path):
    runtime = guest_runtime(tmp_path)
    assert "сохранён" in upload(runtime, GUEST)
    a = runtime.paid_docs.library(runtime.vault.key_for_telegram(GUEST))
    b = runtime.paid_docs.library(runtime.vault.key_for_telegram(OTHER))
    assert a.search("срок оплаты") and b.search("срок оплаты") == []
    assert say(runtime, OTHER, "/docs").startswith("Документов пока нет")
    assert say(runtime, OTHER, "/ask какой срок оплаты?").startswith("Сначала загрузите документ")
    assert a.root != b.root and a.root.parent.name != b.root.parent.name


def test_identical_upload_is_free_and_the_document_limit_is_enforced(tmp_path):
    runtime = guest_runtime(tmp_path)
    assert "сохранён" in upload(runtime, GUEST, "a.txt", b"alpha document one two three")
    assert "уже есть" in upload(runtime, GUEST, "a-copy.txt", b"alpha document one two three")
    assert "сохранён" in upload(runtime, GUEST, "b.txt", b"beta document four five six")
    refused = upload(runtime, GUEST, "c.txt", b"gamma document seven eight nine")
    assert "лимит документов" in refused
    assert runtime.paid_docs.library(runtime.vault.key_for_telegram(GUEST)).count() == 2


def test_an_unreadable_or_empty_file_is_refused_honestly(tmp_path):
    runtime = guest_runtime(tmp_path)
    assert "не найден текст" in upload(runtime, GUEST, "empty.txt", b"   \n  ")
    assert runtime.paid_docs.library(runtime.vault.key_for_telegram(GUEST)).count() == 0


# -- runtime: quota, citations, honesty --------------------------------------------------------
def test_answer_cites_real_fragments_and_drops_an_invented_reference(tmp_path):
    adapter = FakeAdapter("Срок оплаты — 10 рабочих дней [1]. Штраф есть [9].")
    runtime = guest_runtime(tmp_path, adapter)
    upload(runtime, GUEST)
    answer = say(runtime, GUEST, "/ask Какой срок оплаты?")
    assert "10 рабочих дней [1]" in answer and "[9]" not in answer
    assert "Источники:" in answer and "договор.txt" in answer
    prompt = " ".join(str(m.get("content", "")) for m in adapter.calls[-1][1])
    assert "недоверенные данные" in prompt and "Срок оплаты" in prompt


def test_the_quoted_source_is_the_supporting_sentence_not_the_start_of_the_fragment(tmp_path):
    # Found on the real local model (12.10): the answer was right but «Источники» showed the fragment's first
    # sentence, which is about another section. One fragment holds several sections.
    doc = ("Раздел 1. Оплата в течение 5 рабочих дней. Раздел 2. Приёмка работ осуществляется письменно. "
           "Раздел 3. Штраф за просрочку составляет 0.07% в день от суммы договора. Раздел 4. Споры решает суд.")
    runtime = guest_runtime(tmp_path, FakeAdapter("Штраф составляет 0.07% в день [1]."))
    upload(runtime, GUEST, "dogovor.txt", doc.encode())
    answer = say(runtime, GUEST, "/ask Какой штраф за просрочку?")
    sources = answer.split("Источники:")[1]
    assert "0.07% в день" in sources and "Приёмка работ" not in sources


def test_an_answer_without_citations_is_flagged_not_trusted(tmp_path):
    runtime = guest_runtime(tmp_path, FakeAdapter("Оплата в течение десяти дней."))
    upload(runtime, GUEST)
    answer = say(runtime, GUEST, "/ask Какой срок оплаты?")
    assert pd_mod.UNCITED_WARNING_RU in answer and "Найденные фрагменты:" in answer


def test_trial_is_limited_then_the_package_is_offered_and_paid_access_opens(tmp_path):
    runtime = guest_runtime(tmp_path, FakeAdapter("Ответ [1]."))
    upload(runtime, GUEST)
    for _ in range(TRIAL_REQUESTS):
        assert "Источники:" in say(runtime, GUEST, "/ask срок оплаты акт")
    refused = say(runtime, GUEST, "/ask срок оплаты акт")
    assert "Пробные запросы закончились" in refused and "/buy" in refused
    pay(runtime, GUEST)
    assert "Источники:" in say(runtime, GUEST, "/ask срок оплаты акт")
    assert f"осталось запросов — {DOCS30.requests - 1}" in say(runtime, GUEST, "/plan")


def test_a_failed_model_turn_does_not_consume_a_request(tmp_path, monkeypatch):
    runtime = guest_runtime(tmp_path, FakeAdapter("Ответ [1]."))
    upload(runtime, GUEST)

    async def down(*args, **kwargs):
        return rt.NO_MODEL_RU
    monkeypatch.setattr(runtime, "_chat_route", down)
    assert say(runtime, GUEST, "/ask срок оплаты акт") == rt.NO_MODEL_RU
    key = runtime.vault.key_for_telegram(GUEST)
    assert runtime.paid_docs.ledger.entitlement(key)["trial_left"] == TRIAL_REQUESTS


def test_a_question_with_no_matching_fragment_costs_nothing(tmp_path):
    adapter = FakeAdapter("не должно вызываться")
    runtime = guest_runtime(tmp_path, adapter)
    upload(runtime, GUEST)
    n = len(adapter.calls)
    assert say(runtime, GUEST, "/ask квантовая хромодинамика").startswith(pd_mod.NOT_FOUND_RU)
    assert len(adapter.calls) == n
    assert runtime.paid_docs.ledger.entitlement(runtime.vault.key_for_telegram(GUEST))["trial_left"] == TRIAL_REQUESTS


def test_owner_is_not_limited_and_owner_commands_are_hidden_from_guests(tmp_path):
    runtime = guest_runtime(tmp_path, FakeAdapter("Ответ [1]."))
    upload(runtime, OWNER)
    for _ in range(TRIAL_REQUESTS + 2):
        assert "Источники:" in say(runtime, OWNER, "/ask срок оплаты акт")
    assert "Платежи Stars" in say(runtime, OWNER, "/payments")
    assert say(runtime, GUEST, "/payments") == rt.UNKNOWN_COMMAND_RU
    assert say(runtime, GUEST, "/refund ch-1") == rt.UNKNOWN_COMMAND_RU
    assert say(runtime, GUEST, "/ledger_add expense 5 x") == rt.UNKNOWN_COMMAND_RU


def test_paid_features_are_absent_without_the_flag(tmp_path, monkeypatch):
    monkeypatch.delenv(pd_mod.ENV_FLAG)
    runtime = guest_runtime(tmp_path)
    assert runtime.paid_docs is None
    assert say(runtime, GUEST, "/plan") == rt.UNKNOWN_COMMAND_RU
    assert say(runtime, GUEST, "/help") == rt.HELP_RU


def test_terms_and_paysupport_exist_and_a_ticket_reaches_the_owner(tmp_path):
    runtime = guest_runtime(tmp_path)
    terms = say(runtime, GUEST, "/terms")
    assert "Условия пакета" in terms and "Telegram Stars" in terms and "/paysupport" in terms
    assert "Напишите: /paysupport" in say(runtime, GUEST, "/paysupport")
    assert "Обращение №1 принято" in say(runtime, GUEST, "/paysupport оплатил, доступа нет")
    assert "оплатил, доступа нет" in say(runtime, OWNER, "/tickets")
    assert "закрыто" in say(runtime, OWNER, "/tickets close 1")
    assert "нет" in say(runtime, OWNER, "/tickets")


# -- end to end over a fake Bot API --------------------------------------------------------------
class StarsBotAPI(FakeBotAPI):
    def __init__(self, updates):
        super().__init__(updates)
        self.invoices: list[dict] = []
        self.pre_checkout_answers: list[dict] = []
        self.refunds: list[dict] = []
        self.get_updates_bodies: list[dict] = []
        self.refund_ok = True

    def __call__(self, request: httpx.Request) -> httpx.Response:
        method = request.url.path.rsplit("/", 1)[-1]
        body = json.loads(request.content or b"{}")
        if method == "getUpdates":
            self.get_updates_bodies.append(body)
        if method == "sendInvoice":
            self.methods.append(method)
            self.invoices.append(body)
            return httpx.Response(200, json={"ok": True, "result": {"message_id": 4000 + len(self.invoices)}})
        if method == "answerPreCheckoutQuery":
            self.methods.append(method)
            self.pre_checkout_answers.append(body)
            return httpx.Response(200, json={"ok": True, "result": True})
        if method == "refundStarPayment":
            self.methods.append(method)
            self.refunds.append(body)
            if not self.refund_ok:
                return httpx.Response(400, json={"ok": False, "error_code": 400,
                                                 "description": "Bad Request: CHARGE_NOT_FOUND"})
            return httpx.Response(200, json={"ok": True, "result": True})
        return super().__call__(request)


def stars_runtime(tmp_path, api, adapter=None):
    settings = guest_settings(tmp_path)
    runtime = rt.ParticipantRuntime(settings)
    runtime.telegram = Telegram(rt._transport_settings(settings), transport=api)
    runtime.adapter = adapter or FakeAdapter("Ответ [1].")
    for uid in (OWNER, GUEST, OTHER):
        warm(runtime, runtime.vault.key_for_telegram(uid))
    return runtime


def msg_update(update_id, uid, **fields):
    return {"update_id": update_id, "message": {"message_id": update_id, "from": {"id": uid, "is_bot": False},
                                                "chat": {"id": uid, "type": "private"}, **fields}}


def sp(payload, charge, amount=DOCS30.stars):
    return {"currency": "XTR", "total_amount": amount, "invoice_payload": payload,
            "telegram_payment_charge_id": charge, "provider_payment_charge_id": ""}


def test_buy_invoice_shape_and_order_are_recorded(tmp_path):
    api = StarsBotAPI([])
    runtime = stars_runtime(tmp_path, api)
    runtime.outbound_guard.note_inbound(GUEST)      # the /buy just arrived (in production: via getUpdates)
    out = asyncio.run(runtime._paid_docs_command(person_of(runtime, GUEST),
                                                  runtime.vault.key_for_telegram(GUEST), "/buy", ""))
    assert out == "" and len(api.invoices) == 1
    inv = api.invoices[0]
    assert inv["currency"] == "XTR" and inv["provider_token"] == "" and inv["chat_id"] == GUEST
    assert inv["prices"] == [{"label": DOCS30.title[:32], "amount": DOCS30.stars}]       # exactly one price item
    assert 1 <= len(inv["payload"].encode()) <= 128 and len(inv["title"]) <= 32 and len(inv["description"]) <= 255
    assert runtime.paid_docs.ledger.check_pre_checkout(inv["payload"], GUEST, "XTR", DOCS30.stars)[0]


def test_stars_flow_pre_checkout_payment_duplicate_refund_over_the_poll_loop(tmp_path):
    api = StarsBotAPI([])
    runtime = stars_runtime(tmp_path, api)
    key = runtime.vault.key_for_telegram(GUEST)
    payload = runtime.paid_docs.invoice(key, GUEST)["payload"]
    api.pending = [
        {"update_id": 1, "pre_checkout_query": {"id": "pcq-1", "from": {"id": GUEST, "is_bot": False},
                                                "currency": "XTR", "total_amount": DOCS30.stars,
                                                "invoice_payload": payload}},
        {"update_id": 2, "pre_checkout_query": {"id": "pcq-2", "from": {"id": GUEST, "is_bot": False},
                                                "currency": "XTR", "total_amount": 1,
                                                "invoice_payload": payload}},
        msg_update(3, GUEST, successful_payment=sp(payload, "tg-ch-1")),
        msg_update(4, GUEST, successful_payment=sp(payload, "tg-ch-1")),          # redelivered event, new update id
        msg_update(5, GUEST, refunded_payment={"currency": "XTR", "total_amount": DOCS30.stars,
                                               "invoice_payload": payload, "telegram_payment_charge_id": "tg-ch-1"}),
    ]
    run_until(runtime, api, replies=3)
    answers = {a["pre_checkout_query_id"]: a for a in api.pre_checkout_answers}
    assert answers["pcq-1"]["ok"] is True
    assert answers["pcq-2"]["ok"] is False and "не совпадает" in answers["pcq-2"]["error_message"]
    assert all(b["allowed_updates"] == ["message", "pre_checkout_query"] for b in api.get_updates_bodies)
    texts = [row["text"] for row in api.sent]
    # by the time the redelivered event is answered the payment is already refunded: the notice says so
    assert "Оплата получена" in texts[0] and "был возвращён" in texts[1] and "Оплата возвращена" in texts[2]
    ledger = runtime.paid_docs.ledger
    assert ledger.payment("tg-ch-1")["status"] == "refunded"
    ent = ledger.entitlement(key)
    assert ent["has_paid"] is False and ent["paid_requests_left"] == 0
    assert ledger.report()["stars_gross_received"] == DOCS30.stars and ledger.report()["stars_refunded"] == DOCS30.stars


def test_access_survives_a_restart_and_a_foreign_invoice_is_refused(tmp_path):
    api = StarsBotAPI([])
    runtime = stars_runtime(tmp_path, api)
    key = runtime.vault.key_for_telegram(GUEST)
    payload = runtime.paid_docs.invoice(key, GUEST)["payload"]
    api.pending = [msg_update(1, GUEST, successful_payment=sp(payload, "tg-ch-9"))]
    run_until(runtime, api, replies=1)
    runtime.paid_docs.ledger.close()
    restarted = stars_runtime(tmp_path, StarsBotAPI([]))              # a brand-new process over the same data
    assert restarted.paid_docs.ledger.entitlement(key)["paid_requests_left"] == DOCS30.requests
    assert "Пакет активен" in say(restarted, GUEST, "/plan")
    reply = restarted.paid_docs.pre_checkout({"id": "q", "from": {"id": OTHER}, "currency": "XTR",
                                              "total_amount": DOCS30.stars, "invoice_payload": payload})
    assert reply["ok"] is False                                       # an already paid, foreign order


def test_owner_refund_calls_telegram_then_recalculates_access(tmp_path):
    api = StarsBotAPI([])
    runtime = stars_runtime(tmp_path, api)
    key = runtime.vault.key_for_telegram(GUEST)
    _, out = pay(runtime, GUEST, "tg-ch-7")
    assert out.status == "granted"
    assert "Возврат выполнен" in say(runtime, OWNER, "/refund tg-ch-7")
    assert api.refunds == [{"user_id": GUEST, "telegram_payment_charge_id": "tg-ch-7"}]
    assert runtime.paid_docs.ledger.entitlement(key)["has_paid"] is False
    assert "уже возвращён" in say(runtime, OWNER, "/refund tg-ch-7")
    assert "не найден" in say(runtime, OWNER, "/refund nope")


def test_a_refused_refund_leaves_access_unchanged(tmp_path):
    api = StarsBotAPI([])
    api.refund_ok = False
    runtime = stars_runtime(tmp_path, api)
    key = runtime.vault.key_for_telegram(GUEST)
    pay(runtime, GUEST, "tg-ch-8")
    reply = say(runtime, OWNER, "/refund tg-ch-8")
    assert "не выполнил возврат" in reply and "CHARGE_NOT_FOUND" in reply
    assert runtime.paid_docs.ledger.entitlement(key)["has_paid"] is True


def test_deleting_a_person_removes_their_documents_but_never_the_payment_record(tmp_path):
    runtime = guest_runtime(tmp_path)
    key = runtime.vault.key_for_telegram(GUEST)
    upload(runtime, GUEST)
    pay(runtime, GUEST, "tg-ch-5")
    assert runtime.paid_docs.library(key).count() == 1
    assert runtime.vault.delete(key) is True                          # what /delete_me does
    assert runtime.paid_docs.library(key).count() == 0
    assert runtime.paid_docs.ledger.payment("tg-ch-5")["status"] == "paid"


# -- transport ---------------------------------------------------------------------------------
def test_test_environment_url_is_opt_in_and_default_is_unchanged(monkeypatch):
    assert adapters.bot_api_url("T", "getMe") == "https://api.telegram.org/botT/getMe"
    monkeypatch.setenv(adapters.TEST_ENV_FLAG, "1")
    assert adapters.bot_api_url("T", "getMe") == "https://api.telegram.org/botT/test/getMe"


def test_payment_methods_are_unreachable_through_the_generic_call_and_vice_versa(tmp_path):
    runtime = stars_runtime(tmp_path, StarsBotAPI([]))
    with pytest.raises(CompanionError):
        asyncio.run(runtime.telegram.call("refundStarPayment", {"user_id": 1, "telegram_payment_charge_id": "x"}))
    with pytest.raises(CompanionError):
        asyncio.run(runtime.telegram.payment_call("sendMessage", {"chat_id": 1, "text": "x"}))
