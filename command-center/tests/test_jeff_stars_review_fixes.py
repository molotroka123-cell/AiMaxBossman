"""Regression tests for the independent review of the Stars paid-documents feature (12.10.2026).

Every test is a finding that was reproduced with a proof-of-concept before it was fixed. Tests ending in a
"negative control" assertion prove that the fix did not simply turn the behaviour off.
"""
from __future__ import annotations

import asyncio
import hashlib
import io
import time
import zipfile

import pytest

from bcc.pit import paid_docs as pd_mod
from bcc.pit import runtime as rt
from bcc.pit.models import ConsentState
from bcc.pit.stars_ledger import DAY, DOCS30, MAX_OPEN_TICKETS, MAX_VOIDS_PER_DAY, TRIAL_REQUESTS, StarsLedger

from .test_jeff_stars_paid_docs import (GUEST, OTHER, OWNER, StarsBotAPI, _env, guest_runtime, msg_update,  # noqa: F401
                                        person_of, say, sp, stars_runtime, upload)
from .test_pit_runtime import FakeAdapter


def test_review1_reply_wording_cannot_give_a_request_back(tmp_path):
    # The model is asked (by the user or by the document) to open with the outage sentence; the request must
    # still be charged because a model DID answer.
    runtime = guest_runtime(tmp_path, FakeAdapter(rt.UNHANDLED_ERROR_PREFIX_RU + " Ответ [1]."))
    upload(runtime, GUEST)
    key = runtime.vault.key_for_telegram(GUEST)
    for expected in (TRIAL_REQUESTS - 1, TRIAL_REQUESTS - 2, TRIAL_REQUESTS - 3):
        say(runtime, GUEST, "/ask срок оплаты акт")
        assert runtime.paid_docs.ledger.entitlement(key)["trial_left"] == expected


def test_review4_a_safety_or_flood_reply_is_not_an_answer_and_costs_nothing(tmp_path, monkeypatch):
    runtime = guest_runtime(tmp_path, FakeAdapter("Ответ [1]."))
    upload(runtime, GUEST)
    key = runtime.vault.key_for_telegram(GUEST)

    async def early(ctx):                                    # what a j2 flood / boundary reply looks like
        return "Вы пишете очень быстро. Подождите."
    monkeypatch.setattr(runtime.j2, "pre_route", early)
    before = runtime.paid_docs.ledger.entitlement(key)["trial_left"]
    assert "очень быстро" in say(runtime, GUEST, "/ask срок оплаты акт")
    assert runtime.paid_docs.ledger.entitlement(key)["trial_left"] == before


def test_review4_voids_are_bounded_per_day(tmp_path):
    ledger = StarsLedger(tmp_path / "v.sqlite3")
    key = "k" * 64
    payload = ledger.create_order(key, 7)
    ledger.record_payment(person_key=key, user_id=7, payload=payload, charge_id="c", currency="XTR",
                          total_amount=DOCS30.stars)
    given = 0
    for _ in range(MAX_VOIDS_PER_DAY + 5):
        got = ledger.consume(key, "ask")
        given += ledger.void(got.usage_id)
    assert given == MAX_VOIDS_PER_DAY
    assert ledger.entitlement(key)["paid_requests_left"] == DOCS30.requests - 5


def test_review5_document_turns_leave_no_history_learning_or_training_record(tmp_path):
    runtime = guest_runtime(tmp_path, FakeAdapter("Срок — 10 рабочих дней [1]."))
    key = runtime.vault.key_for_telegram(GUEST)
    runtime.vault.set_consent(key, ConsentState(memory_enabled=True, remote_processing_enabled=True,
                                                training_use_enabled=True))
    upload(runtime, GUEST)
    assert "Источники:" in say(runtime, GUEST, "/ask Какой срок оплаты?")
    who = person_of(runtime, GUEST).key
    assert runtime.store.history(who) == []
    assert not (runtime.vault.person_dir(key) / rt.TRAINING_FILE).exists()
    quality = runtime.vault.person_dir(key) / "quality" / "scores.jsonl"
    assert not quality.exists() or "10 рабочих" not in quality.read_text(encoding="utf-8")
    # negative control: an ordinary chat turn of the same person IS recorded as before
    say(runtime, GUEST, "Расскажи, как составить план недели")
    assert runtime.store.history(who) != []


def test_review6_malformed_office_files_are_refusals_not_crashes(tmp_path):
    runtime = guest_runtime(tmp_path)
    bad_docx = io.BytesIO()
    with zipfile.ZipFile(bad_docx, "w") as z:
        z.writestr("word/document.xml", "<w:document><unclosed")
        z.writestr("[Content_Types].xml", "<Types/>")
    reply = upload(runtime, GUEST, "bad.docx", bad_docx.getvalue())
    assert "Не удалось прочитать файл" in reply
    assert "не удалось завершить" not in reply.lower()
    assert "Не удалось прочитать файл" in upload(runtime, GUEST, "t.xlsx", b"PK\x03\x04" + b"\x00" * 40)


def test_review2_a_zip_bomb_is_refused_before_inflation(tmp_path):
    from bcc.pit.doc_library import MAX_UNCOMPRESSED_BYTES
    runtime = guest_runtime(tmp_path)
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as z:
        z.writestr("word/document.xml", "<w:p>" + "a" * (MAX_UNCOMPRESSED_BYTES + 1024) + "</w:p>")
    assert len(buf.getvalue()) < 100_000                              # tiny upload, huge inflation
    started = time.monotonic()
    reply = upload(runtime, GUEST, "bomb.docx", buf.getvalue())
    assert "слишком большой после распаковки" in reply and time.monotonic() - started < 2.0
    assert runtime.paid_docs.library(runtime.vault.key_for_telegram(GUEST)).count() == 0


def test_review2_parsing_runs_off_the_event_loop_and_uploads_are_rate_limited(tmp_path):
    runtime = guest_runtime(tmp_path)
    seen = {}
    real = runtime.paid_docs.add_document

    def spy(*args, **kwargs):
        import threading
        seen["thread"] = threading.current_thread().name
        return real(*args, **kwargs)
    runtime.paid_docs.add_document = spy
    upload(runtime, GUEST, "a.txt", b"alpha one two three")
    assert seen["thread"] != "MainThread"                              # not on the loop thread
    runtime.paid_docs.add_document = real
    replies = [upload(runtime, GUEST, f"d{i}.txt", f"unique text {i} number".encode())
               for i in range(pd_mod.PARSES_PER_WINDOW + 1)]
    assert "Слишком много загрузок" in replies[-1]


def test_review3_pre_checkout_is_answered_even_when_a_deferred_message_precedes_it(tmp_path):
    api = StarsBotAPI([])
    runtime = stars_runtime(tmp_path, api)
    key = runtime.vault.key_for_telegram(GUEST)
    payload = runtime.paid_docs.invoice(key, GUEST)["payload"]
    other = person_of(runtime, OTHER)
    for n in range(1, 5):                                              # OTHER's chat lane is full
        runtime.store.ingest(n, other.key, {"_user_id": OTHER, "_chat_id": OTHER, "_message_id": n,
                                            "text": f"вопрос {n} про план"})
    batch = [msg_update(10, OTHER, text="пятое сообщение"),
             {"update_id": 11, "pre_checkout_query": {"id": "pcq-late", "from": {"id": GUEST, "is_bot": False},
                                                      "currency": "XTR", "total_amount": DOCS30.stars,
                                                      "invoice_payload": payload}}]

    async def one_poll():
        # the same two passes _poll performs
        for update in batch:
            if isinstance(update.get("pre_checkout_query"), dict):
                await runtime._answer_pre_checkout(update, ack=False)
        for update in batch:
            if isinstance(update.get("pre_checkout_query"), dict):
                await runtime._answer_pre_checkout(update, ack=True)
                continue
            if runtime._ingest_update(update) is False:
                break
    asyncio.run(one_poll())
    assert [a["pre_checkout_query_id"] for a in api.pre_checkout_answers] == ["pcq-late"]
    assert api.pre_checkout_answers[0]["ok"] is True
    # and the second pass did not answer it twice
    asyncio.run(runtime._answer_pre_checkout(batch[1], ack=True))
    assert len(api.pre_checkout_answers) == 1


def test_review7_a_crisis_phrase_after_a_command_gets_the_crisis_reply_and_costs_nothing(tmp_path):
    runtime = guest_runtime(tmp_path, FakeAdapter("Ответ [1]."))
    upload(runtime, GUEST)
    key = runtime.vault.key_for_telegram(GUEST)
    reply = say(runtime, GUEST, "/ask я не хочу больше жить, как составить план")
    assert "Источники:" not in reply
    assert runtime.paid_docs.ledger.entitlement(key)["trial_left"] == TRIAL_REQUESTS


def test_review8_a_full_lane_defers_the_payment_before_booking_and_the_notice_states_real_state(tmp_path):
    api = StarsBotAPI([])
    runtime = stars_runtime(tmp_path, api)
    key = runtime.vault.key_for_telegram(GUEST)
    payload = runtime.paid_docs.invoice(key, GUEST)["payload"]
    me = person_of(runtime, GUEST)
    for n in range(1, 5):
        runtime.store.ingest(n, me.key, {"_user_id": GUEST, "_chat_id": GUEST, "_message_id": n, "text": f"вопрос {n}"})
    update = msg_update(20, GUEST, successful_payment=sp(payload, "tg-late"))
    assert runtime._ingest_update(update) is False                    # deferred, NOT booked and NOT dropped
    assert runtime.paid_docs.ledger.payment("tg-late") is None
    # a redelivered event after the booking must not read as "nothing happened"
    runtime.paid_docs.ledger.record_payment(person_key=key, user_id=GUEST, payload=payload, charge_id="tg-late",
                                            currency="XTR", total_amount=DOCS30.stars)
    dup = runtime.paid_docs.payment_notice({"kind": "paid", "status": "duplicate", "charge_id": "tg-late"}, key)
    assert "учтён один раз" in dup and "Пакет активен" in dup
    runtime.paid_docs.ledger.record_refund("tg-late")
    echo = runtime.paid_docs.payment_notice({"kind": "refunded", "status": "duplicate", "stars": 199,
                                             "charge_id": "tg-late"}, key)
    assert "Оплата возвращена" in echo and "уже учтён" not in echo


def test_review9_with_the_flag_unset_the_command_lane_is_unchanged(monkeypatch):
    monkeypatch.delenv(pd_mod.ENV_FLAG)
    assert rt.PITStore.lane({"text": "/ask что-то"}) == "control"
    monkeypatch.setenv(pd_mod.ENV_FLAG, "1")
    assert rt.PITStore.lane({"text": "/ask что-то"}) == "chat"
    assert rt.PITStore.lane({"text": "/plan"}) == "control"           # negative control: only model-bound commands


def test_review10_a_refund_of_an_earlier_package_shortens_a_stacked_one_back(tmp_path):
    ledger = StarsLedger(tmp_path / "s.sqlite3")
    key = "k" * 64
    t0 = 5_000_000.0
    out = None
    for n, when in ((1, t0), (2, t0 + DAY)):
        payload = ledger.create_order(key, 7, now=when)
        out = ledger.record_payment(person_key=key, user_id=7, payload=payload, charge_id=f"s{n}",
                                    currency="XTR", total_amount=DOCS30.stars, now=when)
    assert out.valid_until == pytest.approx(t0 + 2 * DOCS30.days * DAY)
    ledger.record_refund("s1", now=t0 + 2 * DAY)
    ent = ledger.entitlement(key, now=t0 + 2 * DAY)
    # the refunded package ran for 2 days, so the later one starts when that stopped and gets its OWN full 30 days
    # (not 60: the refunded days are not added; not shorter: it never gives less than was paid for)
    assert ent["valid_until"] == pytest.approx(t0 + 2 * DAY + DOCS30.days * DAY)
    assert ent["paid_requests_left"] == DOCS30.requests


def test_review11_zero_and_negative_document_numbers_do_not_wrap(tmp_path):
    runtime = guest_runtime(tmp_path)
    upload(runtime, GUEST, "a.txt", b"alpha one two three")
    upload(runtime, GUEST, "b.txt", b"beta four five six")
    for bad in ("0", "-1", "3", "x"):
        assert "Укажите номер" in say(runtime, GUEST, f"/deldoc {bad}")
    assert runtime.paid_docs.library(runtime.vault.key_for_telegram(GUEST)).count() == 2
    assert "удалён" in say(runtime, GUEST, "/deldoc 2")               # negative control: a valid number works


def test_review12_tickets_finance_and_parse_cache_hygiene(tmp_path):
    ledger = StarsLedger(tmp_path / "h.sqlite3")
    assert [ledger.add_ticket("k" * 64, 7, "x") > 0 for _ in range(MAX_OPEN_TICKETS)] == [True] * MAX_OPEN_TICKETS
    assert ledger.add_ticket("k" * 64, 7, "x") == 0
    assert ledger.add_ticket("z" * 64, 8, "x") > 0                    # another person is not capped
    for bad in (float("nan"), float("inf"), -1.0, 1e30):
        with pytest.raises(ValueError):
            ledger.add_finance("expense", usd=bad)
    with pytest.raises(ValueError):
        ledger.add_finance("withdrawal", stars=10 ** 30)
    runtime = guest_runtime(tmp_path / "rt")
    assert "формат" in say(runtime, OWNER, "/ledger_add expense nan x").lower()
    from bossman.exec_cache import get_cache
    data = b"unique cache probe text one two three"
    upload(runtime, GUEST, "c.txt", data)
    cache = get_cache()
    assert cache.get(cache.key("parsed_file", hashlib.sha256(data).hexdigest())) is None


def test_review12_help_on_the_web_surface_does_not_advertise_telegram_payments(tmp_path):
    runtime = guest_runtime(tmp_path)
    runtime.surface = "web"
    assert say(runtime, GUEST, "/help") == rt.HELP_RU
