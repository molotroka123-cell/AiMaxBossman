"""Studio orders (scenario B, TZ 2.5 T1): quote -> accept -> preflight -> generating -> qc -> owner_review -> approved
-> delivered -> closed, with the receipt's cost from recorded seconds.

Jobs are synthetic job.json records + a small result file in the temp data dir; nothing is generated, no model or GPU
is touched. Negative controls: unfinished job, missing/stale disclosure, face job without consent, another client's
order or job, zero/negative price (line and price list), cancelled order, exact Decimal cost math, a third-party brand
in the brief or a prompt without the owner's ownership record. Model licence: unknown/non-commercial is delivered
WITH an owner-only warning (owner decision 24.10) that never reaches the client view. T1 adds: watermark and the
owner's explicit «yes» as delivery gates, preflight (consent/brand) before rendering, idempotent retries with an
append-only event log, schema-1 migration, and F7 client identity from a server-side token (tests at the end).
"""
from __future__ import annotations

import hashlib
import json
import threading
import uuid
from decimal import Decimal
from pathlib import Path

import pytest

from bcc.direct_gen.orders import DISCLOSURE, OrderBook, OrderError, brands_named, energy_cost, job_seconds
from bcc.direct_gen.store import JobStore

A = "a" * 64
B = "b" * 64


def make_job(store: JobStore, who: str, *, kind: str = "photo", status: str = "completed", total_s: float | None = 120.0,
             faceswap_consent: bool | None = None, facelock: bool = False, model: str = "z-image-turbo",
             prompt: str = "white sneaker on a marble table, soft light") -> str:
    job_id = uuid.uuid4().hex
    job = {"job_id": job_id, "participant": who, "kind": kind, "model": model, "status": status,
           "raw_prompt": prompt, "effective_prompt": prompt, "negative": "",
           "created_at": "2026-10-24T10:00:00+00:00", "started_at": "2026-10-24T10:00:05+00:00",
           "finished_at": "2026-10-24T10:02:05+00:00" if status == "completed" else None,
           "provenance": {}, "result": None, "timeline": []}
    if total_s is not None and status == "completed":
        job["timing"] = {"total_s": total_s}
    if faceswap_consent is not None:
        job["provenance"]["consent_confirmed"] = faceswap_consent
    if facelock:
        job["facelock"] = {"requested": True, "status": "applied"}
    store.save(job)
    if status == "completed":
        res = store.job_dir(who, job_id) / "result"
        res.mkdir(parents=True, exist_ok=True)
        data = f"synthetic-{job_id}".encode()
        (res / "out.png").write_bytes(data)
        job["result"] = {"file": "out.png", "bytes": len(data), "sha256": hashlib.sha256(data).hexdigest(),
                         "mime": "image/png"}
        store.save(job)
    return job_id


def finish(store: JobStore, who: str, job_id: str, total_s: float = 60.0) -> None:
    job = store.load(who, job_id)
    res = store.job_dir(who, job_id) / "result"
    res.mkdir(parents=True, exist_ok=True)
    data = b"late-finish"
    (res / "out.png").write_bytes(data)
    job.update(status="completed", finished_at="2026-10-24T10:03:00+00:00", timing={"total_s": total_s},
               result={"file": "out.png", "bytes": len(data), "sha256": hashlib.sha256(data).hexdigest()})
    store.save(job)


@pytest.fixture
def book(tmp_path) -> OrderBook:
    return OrderBook(tmp_path / "data", JobStore(tmp_path / "data"))


def _order(book: OrderBook, who: str, *items) -> dict:
    return book.create(who, {"items": list(items)})


def to_review(book: OrderBook, who: str, oid: str) -> dict:
    """accepted -> preflight -> generating -> qc_passed -> owner_review."""
    assert book.start(who, oid)["status"] == "generating"
    return book.qc(who, oid, True, "frames look right")


def ready(book: OrderBook, who: str, oid: str, *, disclose: bool = True, watermark: bool = True) -> dict:
    """From accepted to approved with the AI label and watermark recorded (each optional for negative controls)."""
    assert to_review(book, who, oid)["status"] == "owner_review"
    if disclose:
        book.disclose(who, oid)
    if watermark:
        book.watermark(who, oid)
    return book.approve("owner", who, oid, "yes")


# ---------- unit level ----------

def test_legal_path_quote_accept_disclose_deliver_with_exact_receipt(book):
    photo = make_job(book.jobs, A, total_s=120.0)
    clip = make_job(book.jobs, A, kind="video", model="wan2.2-ti2v-5b", total_s=900.0)
    o = _order(book, A, {"kind": "photo_edit", "job_ids": [photo]}, {"kind": "clip", "job_ids": [clip]})
    assert o["status"] == "quoted"
    r = o["receipt"]
    # 1020 s x 180 W = 51 Wh = 0.051 kWh; x 0.15 = 0.00765
    assert r["price"] == "20.00" and r["seconds"] == "1020.0" and r["energy_kwh"] == "0.051000"
    assert r["cost"] == "0.007650" and r["margin"] == "19.992350" and r["cost_complete"] is True
    assert book.accept(A, o["order_id"])["status"] == "accepted"
    assert book.start(A, o["order_id"])["status"] == "generating"
    assert book.qc(A, o["order_id"], True)["status"] == "owner_review"
    d = book.disclose(A, o["order_id"])
    assert sorted(d["disclosed"]) == sorted([photo, clip])
    assert all(v["caption"] == DISCLOSURE for it in d["items"] for v in it["disclosures"].values())
    assert sorted(book.watermark(A, o["order_id"])["watermarked"]) == sorted([photo, clip])
    appr = book.approve("owner", A, o["order_id"], "yes")
    assert appr["status"] == "approved" and set(appr["owner_approval"]["hashes"]) == {photo, clip}
    done = book.deliver(A, o["order_id"])
    assert done["status"] == "delivered" and done["receipt"]["margin"] == "19.992350" and done["receipt"]["frozen_at"]
    assert done["licence_warnings"] == [] and done["receipt"]["licence_warnings"] == []      # Apache-2.0 models
    assert done["items"][1]["models"] == {clip: "wan2.2-ti2v-5b"}
    on_disk = json.loads((book.jobs.participant_dir(A) / "orders" / f"{o['order_id']}.json").read_text("utf-8"))
    assert on_disk["status"] == "delivered" and on_disk["receipt"]["cost"] == "0.007650"
    assert book.close(A, o["order_id"])["status"] == "closed"
    assert book.client_view(on_disk)["timeline"][-1]["status"] == "delivered"
    path = [(e["from"], e["to"]) for e in book.events(A, o["order_id"])]
    assert path[:7] == [(None, "quoted"), ("quoted", "accepted"), ("accepted", "preflight"), ("preflight", "generating"),
                        ("generating", "qc_passed"), ("qc_passed", "owner_review"), ("owner_review", "owner_review")]
    assert path[-3:] == [("owner_review", "approved"), ("approved", "delivered"), ("delivered", "closed")]


def test_cost_math_is_exact_decimal_and_matches_the_float_helper_formula():
    kwh, cost = energy_cost(Decimal("0.1"), Decimal("180"), Decimal("0.15"))
    # floats would give 7.499999999999999e-07; Decimal is exact
    assert cost == Decimal("0.00000075") and kwh == Decimal("0.000005")
    kwh, cost = energy_cost(Decimal("3333.33"), Decimal("180"), Decimal("0.17"))
    assert cost == Decimal("3333.33") * Decimal("180") / Decimal(3_600_000) * Decimal("0.17")
    assert job_seconds({"timing": {"total_s": 12.34}}) == Decimal("12.34")
    assert job_seconds({"started_at": "2026-10-24T10:00:00+00:00", "finished_at": "2026-10-24T10:01:30+00:00"}) == Decimal("90.0")
    assert job_seconds({}) is None


def test_unfinished_job_blocks_delivery_then_delivers_once_finished(book):
    running = make_job(book.jobs, A, status="generating")
    o = _order(book, A, {"kind": "image", "job_ids": [running]})
    assert o["receipt"]["cost_complete"] is False
    book.accept(A, o["order_id"])
    book.start(A, o["order_id"])
    with pytest.raises(OrderError) as e:
        book.disclose(A, o["order_id"])
    assert e.value.code == "job_not_completed"
    with pytest.raises(OrderError) as e:                  # QC cannot pass an unfinished render
        book.qc(A, o["order_id"], True)
    assert e.value.status == 409 and e.value.code == "job_not_completed"
    with pytest.raises(OrderError) as e:
        book.deliver(A, o["order_id"])
    assert e.value.code == "invalid_transition"
    assert book.get(A, o["order_id"])["status"] == "generating"
    finish(book.jobs, A, running)
    book.qc(A, o["order_id"], True)
    book.disclose(A, o["order_id"])
    book.watermark(A, o["order_id"])
    book.approve("owner", A, o["order_id"], "yes")
    assert book.deliver(A, o["order_id"])["status"] == "delivered"


def test_delivery_without_disclosure_is_refused(book):
    o = _order(book, A, {"kind": "image", "job_ids": [make_job(book.jobs, A)]})
    book.accept(A, o["order_id"])
    ready(book, A, o["order_id"], disclose=False)
    with pytest.raises(OrderError) as e:
        book.deliver(A, o["order_id"])
    assert e.value.code == "disclosure_missing" and e.value.problems[0]["code"] == "disclosure_missing"
    with pytest.raises(OrderError) as e:
        book.disclose(A, o["order_id"], caption="made by us")
    assert e.value.code == "disclosure_invalid"


def test_partial_disclosure_and_file_swapped_after_disclosure_are_refused(book):
    j1, j2 = make_job(book.jobs, A), make_job(book.jobs, A)
    o = _order(book, A, {"kind": "image", "job_ids": [j1, j2]})
    book.accept(A, o["order_id"])
    ready(book, A, o["order_id"], disclose=False)
    book.disclose(A, o["order_id"], [j1])
    with pytest.raises(OrderError) as e:
        book.deliver(A, o["order_id"])
    assert [p["job_id"] for p in e.value.problems] == [j2]
    book.disclose(A, o["order_id"], [j2])
    job = book.jobs.load(A, j1)
    job["result"]["sha256"] = "0" * 64                  # the file was replaced after it was disclosed and approved
    book.jobs.save(job)
    with pytest.raises(OrderError) as e:
        book.deliver(A, o["order_id"])
    assert {(p["job_id"], p["code"]) for p in e.value.problems} == {
        (j1, "disclosure_stale"), (j1, "watermark_stale"), (j1, "approval_stale")}


@pytest.mark.parametrize("make", [
    lambda s: make_job(s, A, kind="faceswap", model="faceswap-facefusion", faceswap_consent=False),
    lambda s: make_job(s, A, facelock=True),
])
def test_face_job_without_consent_is_refused(book, make):
    job_id = make(book.jobs)
    kind = "clip" if book.jobs.load(A, job_id)["kind"] == "faceswap" else "photo_edit"
    o = _order(book, A, {"kind": kind, "job_ids": [job_id]})
    book.accept(A, o["order_id"])
    with pytest.raises(OrderError) as e:                  # preflight: nothing is rendered without consent
        book.start(A, o["order_id"])
    assert e.value.code == "consent_missing"
    assert book.get(A, o["order_id"])["status"] == "preflight"
    order = book._load(A, o["order_id"])                   # and the delivery gate checks it again
    assert {"job_id": job_id, "code": "consent_missing"} in book.delivery_problems(order)


def test_face_job_with_consent_recorded_delivers(book):
    swap = make_job(book.jobs, A, kind="faceswap", model="faceswap-facefusion", faceswap_consent=True)
    lock = make_job(book.jobs, A, facelock=True)
    marked = make_job(book.jobs, A)
    o = _order(book, A, {"kind": "clip", "job_ids": [swap]},
               {"kind": "photo_edit", "job_ids": [lock], "consent_confirmed": True},
               {"kind": "image", "job_ids": [marked], "face": True, "consent_confirmed": True})
    book.accept(A, o["order_id"])
    ready(book, A, o["order_id"])
    assert book.deliver(A, o["order_id"])["status"] == "delivered"


def test_line_marked_face_without_consent_is_refused(book):
    o = _order(book, A, {"kind": "image", "job_ids": [make_job(book.jobs, A)], "face": True})
    book.accept(A, o["order_id"])
    with pytest.raises(OrderError) as e:
        book.start(A, o["order_id"])
    assert e.value.code == "consent_missing"


def test_cross_participant_order_and_job_are_invisible(book):
    o = _order(book, A, {"kind": "image", "job_ids": [make_job(book.jobs, A)]})
    for call in (book.get, book.accept, book.start, book.deliver, book.cancel, book.quote, book.close, book.events):
        with pytest.raises(OrderError) as e:
            call(B, o["order_id"])
        assert e.value.status == 404
    assert book.list(B) == [] and len(book.list(A)) == 1
    with pytest.raises(OrderError) as e:                  # B cannot bill A's job either
        _order(book, B, {"kind": "image", "job_ids": [book.list(A)[0]["items"][0]["job_ids"][0]]})
    assert e.value.code == "unknown_job"
    with pytest.raises(OrderError):
        book.get(A, "../" + "b" * 64)


@pytest.mark.parametrize("price", [0, "0", -5, "-0.01", "abc", "NaN", "Infinity", True])
def test_zero_negative_or_garbage_line_price_is_refused(book, price):
    with pytest.raises(OrderError) as e:
        _order(book, A, {"kind": "image", "job_ids": [make_job(book.jobs, A)], "price": price})
    assert e.value.status == 422
    assert book.list(A) == []


@pytest.mark.parametrize("bad", [{"prices": {"clip": 0}}, {"prices": {"image": "-3"}}, {"power_watts": 0},
                                 {"kwh_price": -0.1}, "not json"])
def test_bad_price_list_refuses_quoting_instead_of_guessing(book, bad):
    book.price_list_path.parent.mkdir(parents=True, exist_ok=True)
    book.price_list_path.write_text(bad if isinstance(bad, str) else json.dumps(bad), encoding="utf-8")
    with pytest.raises(OrderError) as e:
        _order(book, A, {"kind": "image", "job_ids": [make_job(book.jobs, A)]})
    assert e.value.code == "price_list_invalid"


def test_price_list_file_and_requote(book):
    o = _order(book, A, {"kind": "image", "job_ids": [make_job(book.jobs, A)]})
    assert o["receipt"]["price"] == "3.00" and o["pricing"]["source"] == "defaults"
    book.price_list_path.parent.mkdir(parents=True, exist_ok=True)
    book.price_list_path.write_text(json.dumps({"currency": "EUR", "prices": {"image": "4.5"}, "power_watts": 200,
                                                "kwh_price": "0.30"}), encoding="utf-8")
    q = book.quote(A, o["order_id"])
    assert q["receipt"]["price"] == "4.50" and q["receipt"]["currency"] == "EUR"
    # 120 s x 200 W = 0.006667 kWh (rounded on display); cost = 120*200/3.6e6*0.30 = 0.002 exactly
    assert q["receipt"]["cost"] == "0.002000" and q["receipt"]["margin"] == "4.498000"
    book.accept(A, o["order_id"])
    with pytest.raises(OrderError) as e:                  # the accepted price is frozen
        book.quote(A, o["order_id"])
    assert e.value.code == "invalid_transition"


def test_cancelled_order_cannot_be_delivered_and_frees_its_jobs(book):
    job_id = make_job(book.jobs, A)
    o = _order(book, A, {"kind": "image", "job_ids": [job_id]})
    with pytest.raises(OrderError) as e:                  # one job is billed once while its order is open
        _order(book, A, {"kind": "image", "job_ids": [job_id]})
    assert e.value.code == "job_already_ordered"
    book.accept(A, o["order_id"])
    book.disclose(A, o["order_id"])
    assert book.cancel(A, o["order_id"], "client changed mind")["status"] == "cancelled"
    with pytest.raises(OrderError) as e:
        book.deliver(A, o["order_id"])
    assert e.value.code == "invalid_transition"
    with pytest.raises(OrderError):
        book.accept(A, o["order_id"])
    assert _order(book, A, {"kind": "image", "job_ids": [job_id]})["status"] == "quoted"


def test_delivered_order_cannot_be_cancelled_and_quoted_order_cannot_be_delivered(book):
    o = _order(book, A, {"kind": "image", "job_ids": [make_job(book.jobs, A)]})
    book.disclose(A, o["order_id"])
    with pytest.raises(OrderError) as e:
        book.deliver(A, o["order_id"])                     # not accepted yet
    assert e.value.code == "invalid_transition"
    book.accept(A, o["order_id"])
    ready(book, A, o["order_id"])
    book.deliver(A, o["order_id"])
    with pytest.raises(OrderError) as e:
        book.cancel(A, o["order_id"])
    assert e.value.code == "invalid_transition"


def test_kind_mismatch_and_duplicates_are_refused(book):
    img = make_job(book.jobs, A)
    with pytest.raises(OrderError) as e:
        _order(book, A, {"kind": "clip", "job_ids": [img]})
    assert e.value.code == "kind_mismatch"
    with pytest.raises(OrderError) as e:
        _order(book, A, {"kind": "image", "job_ids": [img, img]})
    assert e.value.code == "duplicate_job"
    with pytest.raises(OrderError) as e:
        _order(book, A, {"kind": "banner", "job_ids": [img]})
    assert e.value.code == "invalid_kind"


# ---------- model licence: an owner-only warning, never a block ----------

@pytest.mark.parametrize("model,status", [("qwen-image-2.1", "non_commercial"), ("epicrealism-xl", "unknown"),
                                          ("never-heard-of-it", "unknown")])
def test_unknown_or_non_commercial_licence_delivers_with_owner_warning(book, model, status):
    o = _order(book, A, {"kind": "image", "job_ids": [make_job(book.jobs, A, model=model)]})
    assert o["licence_warnings"] == [{"model": model, "licence": o["licence_warnings"][0]["licence"], "status": status}]
    assert o["receipt"]["licence_warnings"] == o["licence_warnings"]          # in the quote
    book.accept(A, o["order_id"])
    ready(book, A, o["order_id"])
    done = book.deliver(A, o["order_id"])
    assert done["status"] == "delivered"                                        # not blocked
    assert done["receipt"]["licence_warnings"][0]["status"] == status           # frozen in the receipt
    client = book.get_for_client(A, o["order_id"])
    text = json.dumps(client)
    for leak in ("licence", "license", model, "cost", "margin", "warning", "note", "power_watts"):
        assert leak not in text, f"{leak!r} leaked into the client view"
    assert client["status"] == "delivered" and client["items"][0]["deliverables"][0]["disclosure"] == DISCLOSURE


def test_licence_file_can_record_a_verified_licence(book):
    book.licences_path.parent.mkdir(parents=True, exist_ok=True)
    book.licences_path.write_text(json.dumps({"epicrealism-xl": {"licence": "CreativeML OpenRAIL++-M",
                                                                 "commercial": True}}), encoding="utf-8")
    o = _order(book, A, {"kind": "image", "job_ids": [make_job(book.jobs, A, model="epicrealism-xl")]})
    assert o["licence_warnings"] == []


# ---------- trademark / brand gate: a block unless the owner records ownership ----------

def test_brand_matching_is_case_insensitive_whole_word():
    brands = ["Starbucks", "Nike", "Coca-Cola", "Apple", "McDonald's", "Disney"]
    assert brands_named("a STARBUCKS cup", brands) == ["Starbucks"]
    assert brands_named("coca cola bottle and CocaCola can", brands) == ["Coca-Cola"]
    assert brands_named("mcdonalds fries", brands) == ["McDonald's"]
    assert brands_named("nike's new shoe", brands) == ["Nike"]
    assert brands_named("pineapple, nikel plating, disneyland-free", brands) == []   # whole words only


@pytest.mark.parametrize("where", ["brief", "prompt"])
def test_brand_in_brief_or_prompt_refuses_delivery_until_owner_records_ownership(book, where):
    prompt = "latte cup with the Starbucks logo" if where == "prompt" else "latte cup, warm light"
    job_id = make_job(book.jobs, A, prompt=prompt)
    brief = "ad for our starbucks franchise" if where == "brief" else "ad for our cafe"
    o = book.create(A, {"items": [{"kind": "image", "job_ids": [job_id]}], "brief": brief})
    assert o["brand_flags"] == [{"brand": "Starbucks", "where": "brief" if where == "brief" else job_id, "cleared": False}]
    book.accept(A, o["order_id"])
    with pytest.raises(OrderError) as e:                                       # preflight: no render for a brand
        book.start(A, o["order_id"])
    assert e.value.code == "brand_not_cleared" and e.value.problems[0]["brand"] == "Starbucks"
    with pytest.raises(OrderError) as e:                                       # a client cannot clear it
        book.set_brand_ownership(A, A, o["order_id"], ["Starbucks"])
    assert e.value.status == 403
    book.set_brand_ownership("owner", A, o["order_id"], ["Nike"])               # clearing another brand does not help
    with pytest.raises(OrderError) as e:
        book.start(A, o["order_id"])
    assert e.value.code == "brand_not_cleared"
    cleared = book.set_brand_ownership("owner", A, o["order_id"], ["Starbucks"], "franchise contract seen")
    assert cleared["brand_ownership"]["starbucks"]["set_by"] == "owner"
    assert book.start(A, o["order_id"])["status"] == "generating"              # preflight re-checked and passed
    book.qc(A, o["order_id"], True)
    book.disclose(A, o["order_id"])
    book.watermark(A, o["order_id"])
    book.approve("owner", A, o["order_id"], "yes")
    assert book.deliver(A, o["order_id"])["status"] == "delivered"


def test_brand_added_to_the_denylist_after_approval_still_blocks_delivery(book):
    o = book.create(A, {"items": [{"kind": "image", "job_ids": [make_job(book.jobs, A)]}], "brief": "mug by Acme"})
    book.accept(A, o["order_id"])
    ready(book, A, o["order_id"])
    book.brands_path.parent.mkdir(parents=True, exist_ok=True)
    book.brands_path.write_text(json.dumps({"denylist": ["Acme"]}), encoding="utf-8")
    with pytest.raises(OrderError) as e:
        book.deliver(A, o["order_id"])
    assert e.value.code == "brand_not_cleared"
    book.set_brand_ownership("owner", A, o["order_id"], ["Acme"])
    assert book.deliver(A, o["order_id"])["status"] == "delivered"


def test_negative_prompt_is_not_scanned_and_brand_list_is_configurable(book):
    job_id = make_job(book.jobs, A)
    job = book.jobs.load(A, job_id)
    job["negative"] = "no Nike logo, no Apple logo"
    book.jobs.save(job)
    o = book.create(A, {"items": [{"kind": "image", "job_ids": [job_id]}], "brief": "sneaker by Acme"})
    assert o["brand_flags"] == []
    book.brands_path.parent.mkdir(parents=True, exist_ok=True)
    book.brands_path.write_text(json.dumps({"denylist": ["Acme"]}), encoding="utf-8")
    assert [f["brand"] for f in book.get(A, o["order_id"])["brand_flags"]] == ["Acme"]
    book.brands_path.write_text(json.dumps({"denylist": "Acme"}), encoding="utf-8")
    book.accept(A, o["order_id"])
    with pytest.raises(OrderError) as e:                                       # a broken list refuses, never skips
        book.start(A, o["order_id"])
    assert e.value.code == "studio_config_invalid"


# ---------- T1: owner «yes», watermark, state machine ----------

def test_approval_needs_the_owner_and_an_explicit_yes(book):
    o = _order(book, A, {"kind": "image", "job_ids": [make_job(book.jobs, A)]})
    book.accept(A, o["order_id"])
    to_review(book, A, o["order_id"])
    book.disclose(A, o["order_id"])
    book.watermark(A, o["order_id"])
    with pytest.raises(OrderError) as e:                                       # no «yes» yet: not deliverable
        book.deliver(A, o["order_id"])
    assert e.value.code == "invalid_transition"
    assert {"code": "approval_missing"} in book.delivery_problems(book._load(A, o["order_id"]))
    for answer in ("", "ok", "y", "yes please", "no"):
        with pytest.raises(OrderError) as e:
            book.approve("owner", A, o["order_id"], answer)
        assert e.value.code == "approval_answer_invalid"
    with pytest.raises(OrderError) as e:                                       # a client cannot approve
        book.approve(A, A, o["order_id"], "yes")
    assert e.value.status == 403
    assert book.get(A, o["order_id"])["status"] == "owner_review"
    assert book.approve("owner", A, o["order_id"], " YES ")["status"] == "approved"
    assert book.deliver(A, o["order_id"])["status"] == "delivered"


def test_watermark_gate_missing_then_recorded_or_stamped_by_the_job(book):
    j1, j2 = make_job(book.jobs, A), make_job(book.jobs, A)
    o = _order(book, A, {"kind": "image", "job_ids": [j1, j2]})
    book.accept(A, o["order_id"])
    ready(book, A, o["order_id"], watermark=False)
    with pytest.raises(OrderError) as e:
        book.deliver(A, o["order_id"])
    assert e.value.code == "watermark_missing" and {p["job_id"] for p in e.value.problems} == {j1, j2}
    with pytest.raises(OrderError) as e:
        book.watermark(A, o["order_id"], mark="  ")
    assert e.value.code == "watermark_invalid"
    book.watermark(A, o["order_id"], [j1])
    job = book.jobs.load(A, j2)                                                # the render pipeline stamped j2 itself
    job["provenance"]["watermark"] = {"mark": "Bossman Motion Studio", "sha256": job["result"]["sha256"]}
    book.jobs.save(job)
    assert book.deliver(A, o["order_id"])["status"] == "delivered"


def test_rework_voids_the_approval_and_reconcile_never_reruns_silently(book):
    o = _order(book, A, {"kind": "image", "job_ids": [make_job(book.jobs, A)]})
    oid = o["order_id"]
    book.accept(A, oid)
    ready(book, A, oid)
    assert book.rework(A, oid, "client asked for a warmer light")["status"] == "rework"
    assert "owner_approval" not in book.get(A, oid)
    with pytest.raises(OrderError) as e:
        book.deliver(A, oid)
    assert e.value.code == "invalid_transition"
    assert book.start(A, oid)["attempt"] == 2
    assert book.reconcile(A, oid, "worker died mid-render")["status"] == "reconcile_required"
    for step in (lambda: book.deliver(A, oid), lambda: book.approve("owner", A, oid, "yes")):
        with pytest.raises(OrderError) as e:
            step()
        assert e.value.code == "invalid_transition"
    assert book.start(A, oid)["status"] == "generating" and book.get(A, oid)["attempt"] == 3
    assert book.qc(A, oid, False, "flicker")["status"] == "qc_failed"
    assert book.start(A, oid)["attempt"] == 4


@pytest.mark.parametrize("status,step", [
    ("quoted", "start"), ("quoted", "qc"), ("accepted", "deliver"), ("accepted", "close"), ("generating", "approve"),
    ("generating", "close"), ("owner_review", "deliver"), ("delivered", "rework"), ("delivered", "cancel"),
    ("cancelled", "start"), ("closed", "cancel")])
def test_invalid_transitions_are_refused_and_leave_no_event(book, status, step):
    o = _order(book, A, {"kind": "image", "job_ids": [make_job(book.jobs, A)]})
    oid = o["order_id"]
    walk = {"accepted": ["accept"], "generating": ["accept", "start"], "owner_review": ["accept", "start", "qc"],
            "delivered": ["accept", "ready", "deliver"], "closed": ["accept", "ready", "deliver", "close"],
            "cancelled": ["cancel"]}.get(status, [])
    for w in walk:
        {"accept": lambda: book.accept(A, oid), "start": lambda: book.start(A, oid),
         "qc": lambda: book.qc(A, oid, True), "ready": lambda: ready(book, A, oid),
         "deliver": lambda: book.deliver(A, oid), "close": lambda: book.close(A, oid),
         "cancel": lambda: book.cancel(A, oid)}[w]()
    assert book.get(A, oid)["status"] == status
    before = book.events(A, oid)
    call = {"start": lambda: book.start(A, oid), "qc": lambda: book.qc(A, oid, True),
            "deliver": lambda: book.deliver(A, oid), "close": lambda: book.close(A, oid),
            "approve": lambda: book.approve("owner", A, oid, "yes"), "rework": lambda: book.rework(A, oid),
            "cancel": lambda: book.cancel(A, oid, "late")}[step]
    with pytest.raises(OrderError) as e:
        call()
    assert e.value.status == 409 and e.value.code == "invalid_transition"
    assert book.get(A, oid)["status"] == status and book.events(A, oid) == before


def test_client_view_hides_internal_production_states(book):
    o = _order(book, A, {"kind": "image", "job_ids": [make_job(book.jobs, A)]})
    book.accept(A, o["order_id"])
    to_review(book, A, o["order_id"])
    client = book.get_for_client(A, o["order_id"])
    assert client["status"] == "in_work"
    assert [t["status"] for t in client["timeline"]] == ["quoted", "accepted", "in_work"]
    assert "owner_review" not in json.dumps(client) and "qc" not in json.dumps(client)


def test_draft_is_priced_by_quote(book):
    o = book.create(A, {"items": [{"kind": "image", "job_ids": [make_job(book.jobs, A)]}], "draft": True})
    assert o["status"] == "draft"
    assert book.list_for_client(A) == []                                       # a draft is not shown to the client
    with pytest.raises(OrderError) as e:
        book.get_for_client(A, o["order_id"])
    assert e.value.status == 404
    with pytest.raises(OrderError) as e:
        book.accept(A, o["order_id"])
    assert e.value.code == "invalid_transition"
    assert book.quote(A, o["order_id"])["status"] == "quoted"
    assert book.accept(A, o["order_id"])["status"] == "accepted"
    assert book.get_for_client(A, o["order_id"])["status"] == "accepted"


def test_every_new_attempt_rechecks_preflight(book):
    """Swarm review 25.10 (Gemini + Nemotron): rework -> generating must not skip consent/brand checks."""
    o = book.create(A, {"items": [{"kind": "image", "job_ids": [make_job(book.jobs, A)]}], "brief": "mug by Acme"})
    oid = o["order_id"]
    book.accept(A, oid)
    ready(book, A, oid)
    book.rework(A, oid, "client wants their logo")
    book.brands_path.parent.mkdir(parents=True, exist_ok=True)
    book.brands_path.write_text(json.dumps({"denylist": ["Acme"]}), encoding="utf-8")
    with pytest.raises(OrderError) as e:
        book.start(A, oid)
    assert e.value.code == "brand_not_cleared" and book.get(A, oid)["status"] == "rework"
    book.set_brand_ownership("owner", A, oid, ["Acme"])
    assert book.start(A, oid)["status"] == "generating"


def test_replay_is_per_actor(book):
    o = _order(book, A, {"kind": "image", "job_ids": [make_job(book.jobs, A)]})
    book.cancel(A, o["order_id"], "", actor="owner")
    with pytest.raises(OrderError) as e:                                       # the client's cancel is its own request
        book.cancel(A, o["order_id"], "", actor="client")
    assert e.value.code == "invalid_transition"
    assert book.cancel(A, o["order_id"], "", actor="owner")["replayed"] is True


# ---------- T1: idempotent retries + append-only event log ----------

def test_lost_response_retry_is_a_replay_without_a_second_event(book):
    o = _order(book, A, {"kind": "image", "job_ids": [make_job(book.jobs, A)]})
    oid = o["order_id"]
    first = book.accept(A, oid)
    again = book.accept(A, oid)
    assert first["status"] == again["status"] == "accepted" and again["replayed"] is True and "replayed" not in first
    assert [e["step"] for e in book.events(A, oid)] == ["create", "accept"]
    ready(book, A, oid)
    done = book.deliver(A, oid)
    replay = book.deliver(A, oid)
    assert replay["replayed"] is True and replay["receipt"]["frozen_at"] == done["receipt"]["frozen_at"]
    assert [e["step"] for e in book.events(A, oid)].count("deliver") == 1
    book.close(A, oid)
    with pytest.raises(OrderError) as e:                                       # not the last step any more
        book.deliver(A, oid)
    assert e.value.code == "invalid_transition"


def test_idempotency_key_replays_same_request_and_refuses_another(book):
    o = _order(book, A, {"kind": "image", "job_ids": [make_job(book.jobs, A)]})
    oid = o["order_id"]
    book.accept(A, oid, key="k-accept-1")
    book.start(A, oid)
    assert book.accept(A, oid, key="k-accept-1")["replayed"] is True             # late retry, state moved on
    with pytest.raises(OrderError) as e:
        book.cancel(A, oid, "x", key="k-accept-1")
    assert e.value.code == "idempotency_conflict"
    with pytest.raises(OrderError) as e:
        book.accept(A, oid, key="bad key with spaces")
    assert e.value.code == "invalid_idempotency_key"
    assert book.get(A, oid)["status"] == "generating"
    assert book.qc(A, oid, False, "blur", key="qc-1")["status"] == "qc_failed"
    assert book.qc(A, oid, False, "blur", key="qc-1")["replayed"] is True
    with pytest.raises(OrderError) as e:                                       # same key, other arguments
        book.qc(A, oid, True, "", key="qc-1")
    assert e.value.code == "idempotency_conflict"
    assert "idempotency" not in book.get(A, oid)                                # internal, not in the view


def test_record_steps_rebind_after_a_file_change_instead_of_replaying(book):
    j = make_job(book.jobs, A)
    o = _order(book, A, {"kind": "image", "job_ids": [j]})
    book.accept(A, o["order_id"])
    book.disclose(A, o["order_id"])
    job = book.jobs.load(A, j)
    job["result"]["sha256"] = "1" * 64
    book.jobs.save(job)
    again = book.disclose(A, o["order_id"])
    assert "replayed" not in again and again["items"][0]["disclosures"][j]["sha256"] == "1" * 64


def test_concurrent_retries_commit_exactly_once(book):
    o = _order(book, A, {"kind": "image", "job_ids": [make_job(book.jobs, A)]})
    oid, results, errors = o["order_id"], [], []

    def worker():
        try:
            results.append(book.accept(A, oid))
        except OrderError as exc:                                              # pragma: no cover - must not happen
            errors.append(exc.code)
    threads = [threading.Thread(target=worker) for _ in range(12)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    assert not errors and len(results) == 12
    assert sum(1 for r in results if not r.get("replayed")) == 1
    assert [e["step"] for e in book.events(A, oid)] == ["create", "accept"]


def test_event_log_is_append_only_and_matches_the_order_rev(book):
    o = _order(book, A, {"kind": "image", "job_ids": [make_job(book.jobs, A)]})
    oid = o["order_id"]
    path = book.jobs.participant_dir(A) / "orders" / f"{oid}.events.jsonl"
    snapshots = [path.read_bytes()]
    for step in (lambda: book.accept(A, oid), lambda: book.start(A, oid), lambda: book.qc(A, oid, True),
                 lambda: book.disclose(A, oid), lambda: book.watermark(A, oid),
                 lambda: book.approve("owner", A, oid, "yes"), lambda: book.deliver(A, oid)):
        step()
        now = path.read_bytes()
        assert now.startswith(snapshots[-1]) and len(now) > len(snapshots[-1])   # earlier lines never rewritten
        snapshots.append(now)
    events = book.events(A, oid)
    assert events[-1]["rev"] == book.get(A, oid)["rev"] == 8
    assert all(e["at"] and e["actor"] for e in events)
    with pytest.raises(OrderError):                                            # a refused step appends nothing
        book.start(A, oid)
    assert path.read_bytes() == snapshots[-1]


def test_failed_preflight_is_committed_and_logged_then_retried(book):
    j = make_job(book.jobs, A, facelock=True)
    o = _order(book, A, {"kind": "photo_edit", "job_ids": [j]})
    oid = o["order_id"]
    book.accept(A, oid)
    with pytest.raises(OrderError):
        book.start(A, oid)
    with pytest.raises(OrderError):                                            # a retry re-checks, never replays
        book.start(A, oid)
    assert [(e["step"], e["to"]) for e in book.events(A, oid)][-2:] == [("accept", "accepted"),
                                                                        ("start", "preflight")]
    job = book.jobs.load(A, j)
    job["provenance"]["consent_confirmed"] = True
    book.jobs.save(job)
    assert book.start(A, oid)["status"] == "generating"


# ---------- T1: migration of existing (schema 1) orders ----------

def _legacy_order(book: OrderBook, who: str, status: str, job_id: str) -> str:
    oid = uuid.uuid4().hex
    order = {"order_id": oid, "participant": who, "status": status, "note": "", "brief": "",
             "items": [{"kind": "image", "job_ids": [job_id], "face": False, "disclosures": {},
                        "models": {job_id: "z-image-turbo"}, "price": "3.00"}],
             "pricing": {"currency": "USD", "power_watts": "180.00", "kwh_price": "0.15",
                         "price_list": {"photo_edit": "5.00", "image": "3.00", "clip": "15.00"}, "source": "defaults"},
             "brand_ownership": {}, "licence_warnings": [], "created_at": "2026-10-24T10:00:00+00:00",
             "updated_at": "2026-10-24T10:05:00+00:00",
             "timeline": [{"status": "quoted", "at": "2026-10-24T10:00:00+00:00"},
                          {"status": "accepted", "at": "2026-10-24T10:01:00+00:00"}]}
    folder = book.jobs.participant_dir(who) / "orders"
    folder.mkdir(parents=True, exist_ok=True)
    (folder / f"{oid}.json").write_text(json.dumps(order), encoding="utf-8")
    return oid


@pytest.mark.parametrize("legacy,now", [("in_work", "generating"), ("accepted", "accepted"),
                                        ("delivered", "delivered"), ("weird", "reconcile_required")])
def test_legacy_orders_migrate_without_loss(book, legacy, now):
    j = make_job(book.jobs, A)
    oid = _legacy_order(book, A, legacy, j)
    raw_before = (book.jobs.participant_dir(A) / "orders" / f"{oid}.json").read_text("utf-8")
    view = book.get(A, oid)
    assert view["status"] == now and view["schema"] == 2 and view["migrated_from"]["status"] == legacy
    assert view["rev"] == 2 and view["items"][0]["job_ids"] == [j] and view["receipt"]["price"] == "3.00"
    assert (book.jobs.participant_dir(A) / "orders" / f"{oid}.json").read_text("utf-8") == raw_before  # read = no write
    assert [o["order_id"] for o in book.list(A)] == [oid]


def test_migrated_in_work_order_completes_the_new_flow_and_logs_migration(book):
    j = make_job(book.jobs, A)
    oid = _legacy_order(book, A, "in_work", j)
    assert book.qc(A, oid, True)["status"] == "owner_review"
    events = book.events(A, oid)
    assert events[0] == {**events[0], "step": "migrated", "from": "in_work", "to": "generating", "rev": 3}
    on_disk = json.loads((book.jobs.participant_dir(A) / "orders" / f"{oid}.json").read_text("utf-8"))
    assert on_disk["schema"] == 2 and "_migrating" not in on_disk
    book.disclose(A, oid)
    book.watermark(A, oid)
    book.approve("owner", A, oid, "yes")
    assert book.deliver(A, oid)["status"] == "delivered"


# ---------- F7: client tokens (unit) ----------

def test_client_token_is_hashed_at_rest_rotates_and_revokes(book):
    issued = book.tokens.issue(A, "Acme cafe")
    token = issued["token"]
    assert token.startswith("stc_") and len(token) > 40 and issued["client"] == A
    stored = book.tokens.path.read_text("utf-8")
    assert token not in stored and hashlib.sha256(token.encode()).hexdigest() in stored
    assert book.tokens.resolve(token) == A
    assert book.tokens.resolve(token + "x") is None and book.tokens.resolve("") is None
    assert book.tokens.resolve(A) is None                                      # the key itself is not a credential
    rotated = book.tokens.issue(A)["token"]
    assert book.tokens.resolve(token) is None and book.tokens.resolve(rotated) == A
    assert book.tokens.revoke(A)["active"] is False and book.tokens.resolve(rotated) is None
    fresh = book.tokens.issue(None, "new client")
    assert len(fresh["client"]) == 64 and book.tokens.resolve(fresh["token"]) == fresh["client"]
    with pytest.raises(OrderError) as e:
        book.tokens.issue("owner")
    assert e.value.code == "invalid_participant"
    with pytest.raises(OrderError) as e:
        book.tokens.revoke(B)
    assert e.value.status == 404


@pytest.mark.parametrize("bad_key", ["owner", "OWNER", "a" * 63, "../" + "a" * 61])
def test_hand_edited_token_file_never_resolves_to_the_owner_or_a_bad_key(book, bad_key):
    token = "stc_" + "t" * 43
    book.tokens.path.parent.mkdir(parents=True, exist_ok=True)
    book.tokens.path.write_text(json.dumps({"clients": {bad_key: {"token_sha256": hashlib.sha256(token.encode())
                                                                  .hexdigest()}}}), encoding="utf-8")
    assert book.tokens.resolve(token) is None


def test_revoked_marker_wins_even_if_a_hash_survived(book):
    token = book.tokens.issue(A)["token"]
    data = json.loads(book.tokens.path.read_text("utf-8"))
    data["clients"][A]["revoked_at"] = "2026-10-25T00:00:00+00:00"           # hand-edited: hash kept, marked revoked
    book.tokens.path.write_text(json.dumps(data), encoding="utf-8")
    assert book.tokens.resolve(token) is None and book.tokens.list()[0]["active"] is False


def test_broken_token_file_fails_closed(book):
    book.tokens.path.parent.mkdir(parents=True, exist_ok=True)
    book.tokens.path.write_text("{not json", encoding="utf-8")
    with pytest.raises(OrderError) as e:
        book.tokens.resolve("stc_" + "t" * 43)
    assert e.value.code == "studio_config_invalid"


def test_expired_client_token_is_refused(book):
    token = book.tokens.issue(A, ttl_days=1)["token"]
    data = json.loads(book.tokens.path.read_text("utf-8"))
    data["clients"][A]["expires_at"] = "2026-01-01T00:00:00+00:00"
    book.tokens.path.write_text(json.dumps(data), encoding="utf-8")
    assert book.tokens.resolve(token) is None
    assert book.client_tokens()["clients"][0]["active"] is False


def test_migration_list_names_clients_with_orders_but_no_token(book):
    _order(book, A, {"kind": "image", "job_ids": [make_job(book.jobs, A)]})
    _order(book, B, {"kind": "image", "job_ids": [make_job(book.jobs, B)]})
    assert book.client_tokens()["needs_token"] == [A, B]
    book.tokens.issue(A)
    assert book.client_tokens()["needs_token"] == [B]
    assert "token_sha256" not in json.dumps(book.client_tokens())


# ---------- API level (real app, temp data dir) ----------

def _api_job(env, who=A, **kw) -> str:
    return make_job(env.svc.direct_gen.store, who, **kw)


OWNER = {"X-Participant": "owner"}
URL = "/api/studio/orders"


async def _token(env, client: str) -> dict:
    r = await env.client.post("/api/studio/clients", json={"client": client, "label": "synthetic"})
    assert r.status_code == 201, r.text
    return {"X-Studio-Client-Token": r.json()["token"]}


async def _owner_ready(env, oid: str, client: str = A) -> None:
    for step, body in (("start", None), ("qc", {"passed": True}), ("disclose", None), ("watermark", None),
                       ("approve", {"answer": "yes"})):
        r = await env.client.post(f"{URL}/{oid}/{step}?client={client}", json=body, headers=OWNER)
        assert r.status_code == 200, (step, r.text)


async def test_api_legal_path_and_negative_controls(env):
    j = _api_job(env)
    as_a = await _token(env, A)
    as_b = await _token(env, B)
    r = await env.client.post(f"{URL}?client={A}", json={"items": [{"kind": "image", "job_ids": [j]}]}, headers=OWNER)
    assert r.status_code == 201, r.text
    oid = r.json()["order_id"]
    assert (await env.client.get(f"{URL}/{oid}?client={B}", headers=OWNER)).status_code == 404
    assert (await env.client.get(f"{URL}/{oid}", headers=as_b)).status_code == 404          # cross-client: 404
    assert (await env.client.get(URL, headers=as_b)).json() == {"orders": []}
    # a client may not act as owner, name another client, deliver, approve or create
    assert (await env.client.get(f"{URL}/{oid}?client={A}", headers=as_b)).status_code == 403
    assert (await env.client.post(f"{URL}/{oid}/deliver", headers=as_a)).status_code == 403
    assert (await env.client.post(f"{URL}/{oid}/approve", headers=as_a, json={"answer": "yes"})).status_code == 403
    assert (await env.client.get(f"{URL}/{oid}/events", headers=as_a)).status_code == 403
    assert (await env.client.post(URL, json={"items": [{"kind": "image", "job_ids": [j]}]}, headers=as_a)).status_code == 403
    accepted = await env.client.post(f"{URL}/{oid}/accept", headers=as_a)                   # the client accepts its quote
    assert accepted.status_code == 200 and accepted.json()["status"] == "accepted" and "receipt" not in accepted.json()
    again = await env.client.post(f"{URL}/{oid}/accept", headers=as_a)                      # lost-response retry
    assert again.status_code == 200 and again.json()["replayed"] is True and "receipt" not in again.json()
    for step, body in (("start", None), ("qc", {"passed": True}), ("watermark", None), ("approve", {"answer": "yes"})):
        assert (await env.client.post(f"{URL}/{oid}/{step}?client={A}", json=body, headers=OWNER)).status_code == 200
    refused = await env.client.post(f"{URL}/{oid}/deliver?client={A}", headers=OWNER)
    assert refused.status_code == 409 and refused.json()["error"]["code"] == "disclosure_missing"
    assert refused.json()["error"]["problems"] == [{"job_id": j, "code": "disclosure_missing"}]
    assert (await env.client.post(f"{URL}/{oid}/disclose?client={A}", headers=OWNER)).status_code == 200
    done = await env.client.post(f"{URL}/{oid}/deliver?client={A}", headers={**OWNER, "Idempotency-Key": "d1"})
    assert done.status_code == 200 and done.json()["status"] == "delivered"
    assert done.json()["receipt"]["cost"] == "0.000900"   # 120 s x 180 W x 0.15/kWh
    retry = await env.client.post(f"{URL}/{oid}/deliver?client={A}", headers={**OWNER, "Idempotency-Key": "d1"})
    assert retry.status_code == 200 and retry.json()["replayed"] is True
    ev = (await env.client.get(f"{URL}/{oid}/events?client={A}", headers=OWNER)).json()["events"]
    assert [e["step"] for e in ev].count("deliver") == 1 and ev[1]["actor"] == "client"
    seen = await env.client.get(f"{URL}/{oid}", headers=as_a)
    assert seen.status_code == 200 and seen.json()["status"] == "delivered"
    bad = await env.client.post(f"{URL}?client={A}", headers=OWNER,
                                json={"items": [{"kind": "image", "job_ids": [_api_job(env)], "price": "-1"}]})
    assert bad.status_code == 422 and bad.json()["error"]["code"] == "price_not_positive"
    pl = (await env.client.get("/api/studio/price-list")).json()
    assert pl["prices"] == {"photo_edit": "5.00", "image": "3.00", "clip": "15.00"} and pl["power_watts"] == "180.00"
    assert (await env.client.get("/api/studio/price-list", headers=as_a)).status_code == 403


async def test_api_f7_header_alone_is_not_a_client_credential(env):
    """F7 negative controls: X-Participant alone (any client key) no longer grants client access."""
    j = _api_job(env)
    oid = (await env.client.post(f"{URL}?client={A}", json={"items": [{"kind": "image", "job_ids": [j]}]},
                                 headers=OWNER)).json()["order_id"]
    for hdr in ({"X-Participant": A}, {"X-BCC-Participant": A}, {"X-Participant": B}):
        for method, path in (("GET", URL), ("GET", f"{URL}/{oid}"), ("POST", f"{URL}/{oid}/accept"),
                             ("POST", f"{URL}/{oid}/cancel")):
            r = await env.client.request(method, path, headers=hdr)
            assert r.status_code == 403 and r.json()["error"]["code"] == "client_token_required", (hdr, path, r.text)
    assert (await env.client.get(f"{URL}/{oid}?client={A}", headers=OWNER)).json()["status"] == "quoted"  # nothing moved


async def test_api_f7_wrong_revoked_and_mismatched_tokens(env):
    j = _api_job(env)
    oid = (await env.client.post(f"{URL}?client={A}", json={"items": [{"kind": "image", "job_ids": [j]}]},
                                 headers=OWNER)).json()["order_id"]
    as_a = await _token(env, A)
    for bad in ("stc_" + "x" * 43, "not-a-token", A, ""):
        r = await env.client.get(f"{URL}/{oid}", headers={"X-Studio-Client-Token": bad})
        assert r.status_code == 401 and r.json()["error"]["code"] == "invalid_client_token", (bad, r.text)
    r = await env.client.get(f"{URL}/{oid}", headers={**as_a, "X-Participant": B})       # token A + header B
    assert r.status_code == 403 and r.json()["error"]["code"] == "participant_mismatch"
    assert (await env.client.get(f"{URL}/{oid}", headers={**as_a, "X-Participant": A})).status_code == 200
    # the token wins over a header claiming the owner: never an owner view for a client token
    r = await env.client.get(f"{URL}/{oid}", headers={**as_a, "X-Participant": "owner"})
    assert r.status_code == 403
    listing = (await env.client.get("/api/studio/clients")).json()
    assert [c["client"] for c in listing["clients"]] == [A]
    assert "stc_" not in json.dumps(listing) and "token_sha256" not in json.dumps(listing)
    assert (await env.client.post("/api/studio/clients", json={"client": A}, headers=as_a)).status_code == 403
    revoked = await env.client.post(f"/api/studio/clients/{A}/revoke")
    assert revoked.status_code == 200 and revoked.json()["active"] is False
    r = await env.client.get(f"{URL}/{oid}", headers=as_a)
    assert r.status_code == 401
    assert (await env.client.post("/api/studio/clients", json={"client": "owner"})).status_code == 422


async def test_api_f7_owner_token_works_and_agent_token_is_not_the_owner(env):
    j = _api_job(env)
    oid = (await env.client.post(f"{URL}?client={A}", json={"items": [{"kind": "image", "job_ids": [j]}]},
                                 headers=OWNER)).json()["order_id"]
    owner_view = await env.client.get(f"{URL}/{oid}?client={A}")                            # owner token, no header
    assert owner_view.status_code == 200 and "receipt" in owner_view.json()
    agent = env.svc.auth.issue_agent_token("studio-probe", scopes=("read",))
    for path in (f"{URL}/{oid}?client={A}", URL, "/api/studio/price-list", "/api/studio/clients"):
        r = await env.client.get(path, headers={"X-BCC-Token": agent})
        assert r.status_code == 403 and r.json()["error"]["code"] == "owner_credential_required", (path, r.text)
    r = await env.client.get(f"{URL}/{oid}?client={A}", headers={"X-BCC-Token": "wrong"})  # transport auth: 401
    assert r.status_code == 401


async def test_api_f7_cross_client_isolation_with_tokens(env):
    ja, jb = _api_job(env, A), _api_job(env, B)
    oa = (await env.client.post(f"{URL}?client={A}", json={"items": [{"kind": "image", "job_ids": [ja]}]},
                                headers=OWNER)).json()["order_id"]
    ob = (await env.client.post(f"{URL}?client={B}", json={"items": [{"kind": "image", "job_ids": [jb]}]},
                                headers=OWNER)).json()["order_id"]
    as_a, as_b = await _token(env, A), await _token(env, B)
    assert [o["order_id"] for o in (await env.client.get(URL, headers=as_a)).json()["orders"]] == [oa]
    assert [o["order_id"] for o in (await env.client.get(URL, headers=as_b)).json()["orders"]] == [ob]
    for method, path in (("GET", f"{URL}/{ob}"), ("POST", f"{URL}/{ob}/accept"), ("POST", f"{URL}/{ob}/cancel")):
        r = await env.client.request(method, path, headers=as_a)
        assert r.status_code == 404, (path, r.text)
    assert (await env.client.get(f"{URL}/{ob}?client={B}", headers=OWNER)).json()["status"] == "quoted"


async def test_api_licence_warning_is_owner_only_and_brand_gate_blocks(env):
    j = _api_job(env, model="qwen-image-2.1", prompt="Coca-Cola bottle on ice")
    as_a = await _token(env, A)
    r = await env.client.post(f"{URL}?client={A}", headers=OWNER,
                              json={"items": [{"kind": "image", "job_ids": [j]}], "note": "internal: check licence"})
    oid = r.json()["order_id"]
    assert r.json()["licence_warnings"][0] == {"model": "qwen-image-2.1", "licence": r.json()["licence_warnings"][0]["licence"],
                                               "status": "non_commercial"}
    await env.client.post(f"{URL}/{oid}/accept?client={A}", headers=OWNER)
    blocked = await env.client.post(f"{URL}/{oid}/start?client={A}", headers=OWNER)
    assert blocked.status_code == 409 and blocked.json()["error"]["code"] == "brand_not_cleared"
    by_client = await env.client.post(f"{URL}/{oid}/brands", headers=as_a, json={"brands": ["Coca-Cola"]})
    assert by_client.status_code == 403
    ok = await env.client.post(f"{URL}/{oid}/brands?client={A}", headers=OWNER, json={"brands": ["Coca-Cola"]})
    assert ok.status_code == 200
    await _owner_ready(env, oid)
    done = await env.client.post(f"{URL}/{oid}/deliver?client={A}", headers=OWNER)
    assert done.status_code == 200 and done.json()["receipt"]["licence_warnings"][0]["status"] == "non_commercial"
    for resp in (await env.client.get(f"{URL}/{oid}", headers=as_a), await env.client.get(URL, headers=as_a)):
        text = resp.text
        assert resp.status_code == 200
        for leak in ("licence", "qwen-image", "cost", "margin", "internal: check licence", "brand", "watermark",
                     "owner_approval", "idempotency"):
            assert leak not in text, f"{leak!r} leaked to the client"


async def test_api_reads_order_files_off_the_event_loop(env, monkeypatch):
    j = _api_job(env)
    as_a = await _token(env, A)
    r = await env.client.post(f"{URL}?client={A}", json={"items": [{"kind": "image", "job_ids": [j]}]},
                              headers=OWNER)
    oid = r.json()["order_id"]
    loop_thread, seen = threading.get_ident(), []
    from bcc.direct_gen import orders as orders_mod
    real = orders_mod.read_text_retry

    def spy(path: Path) -> str:
        seen.append(threading.get_ident())
        return real(path)

    monkeypatch.setattr(orders_mod, "read_text_retry", spy)
    assert (await env.client.get(f"{URL}/{oid}?client={A}", headers=OWNER)).status_code == 200
    assert (await env.client.get(URL, headers=as_a)).status_code == 200
    assert seen and loop_thread not in seen, "order files were read on the event-loop thread"
