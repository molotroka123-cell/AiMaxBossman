"""Studio orders (scenario B): quote -> accept -> disclose -> deliver, with the receipt's cost from recorded seconds.

Jobs are synthetic job.json records + a small result file in the temp data dir; nothing is generated, no model or GPU
is touched. Negative controls: unfinished job, missing/stale disclosure, face job without consent, another client's
order or job, zero/negative price (line and price list), cancelled order, exact Decimal cost math, a third-party brand
in the brief or a prompt without the owner's ownership record. Model licence: unknown/non-commercial is delivered
WITH an owner-only warning (owner decision 24.10) that never reaches the client view.
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
    assert book.start(A, o["order_id"])["status"] == "in_work"
    d = book.disclose(A, o["order_id"])
    assert sorted(d["disclosed"]) == sorted([photo, clip])
    assert all(v["caption"] == DISCLOSURE for it in d["items"] for v in it["disclosures"].values())
    done = book.deliver(A, o["order_id"])
    assert done["status"] == "delivered" and done["receipt"]["margin"] == "19.992350" and done["receipt"]["frozen_at"]
    assert done["licence_warnings"] == [] and done["receipt"]["licence_warnings"] == []      # Apache-2.0 models
    assert done["items"][1]["models"] == {clip: "wan2.2-ti2v-5b"}
    on_disk = json.loads((book.jobs.participant_dir(A) / "orders" / f"{o['order_id']}.json").read_text("utf-8"))
    assert on_disk["status"] == "delivered" and on_disk["receipt"]["cost"] == "0.007650"


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
    with pytest.raises(OrderError) as e:
        book.disclose(A, o["order_id"])
    assert e.value.code == "job_not_completed"
    with pytest.raises(OrderError) as e:
        book.deliver(A, o["order_id"])
    assert e.value.status == 409 and "job_not_completed" in e.value.message
    assert book.get(A, o["order_id"])["status"] == "accepted"
    finish(book.jobs, A, running)
    book.disclose(A, o["order_id"])
    assert book.deliver(A, o["order_id"])["status"] == "delivered"


def test_delivery_without_disclosure_is_refused(book):
    o = _order(book, A, {"kind": "image", "job_ids": [make_job(book.jobs, A)]})
    book.accept(A, o["order_id"])
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
    book.disclose(A, o["order_id"], [j1])
    with pytest.raises(OrderError) as e:
        book.deliver(A, o["order_id"])
    assert [p["job_id"] for p in e.value.problems] == [j2]
    book.disclose(A, o["order_id"], [j2])
    job = book.jobs.load(A, j1)
    job["result"]["sha256"] = "0" * 64                  # the file was replaced after it was disclosed
    book.jobs.save(job)
    with pytest.raises(OrderError) as e:
        book.deliver(A, o["order_id"])
    assert e.value.code == "disclosure_stale"


@pytest.mark.parametrize("make", [
    lambda s: make_job(s, A, kind="faceswap", model="faceswap-facefusion", faceswap_consent=False),
    lambda s: make_job(s, A, facelock=True),
])
def test_face_job_without_consent_is_refused(book, make):
    job_id = make(book.jobs)
    kind = "clip" if book.jobs.load(A, job_id)["kind"] == "faceswap" else "photo_edit"
    o = _order(book, A, {"kind": kind, "job_ids": [job_id]})
    book.accept(A, o["order_id"])
    book.disclose(A, o["order_id"])
    with pytest.raises(OrderError) as e:
        book.deliver(A, o["order_id"])
    assert e.value.code == "consent_missing"


def test_face_job_with_consent_recorded_delivers(book):
    swap = make_job(book.jobs, A, kind="faceswap", model="faceswap-facefusion", faceswap_consent=True)
    lock = make_job(book.jobs, A, facelock=True)
    marked = make_job(book.jobs, A)
    o = _order(book, A, {"kind": "clip", "job_ids": [swap]},
               {"kind": "photo_edit", "job_ids": [lock], "consent_confirmed": True},
               {"kind": "image", "job_ids": [marked], "face": True, "consent_confirmed": True})
    book.accept(A, o["order_id"])
    book.disclose(A, o["order_id"])
    assert book.deliver(A, o["order_id"])["status"] == "delivered"


def test_line_marked_face_without_consent_is_refused(book):
    o = _order(book, A, {"kind": "image", "job_ids": [make_job(book.jobs, A)], "face": True})
    book.accept(A, o["order_id"])
    book.disclose(A, o["order_id"])
    with pytest.raises(OrderError) as e:
        book.deliver(A, o["order_id"])
    assert e.value.code == "consent_missing"


def test_cross_participant_order_and_job_are_invisible(book):
    o = _order(book, A, {"kind": "image", "job_ids": [make_job(book.jobs, A)]})
    for call in (book.get, book.accept, book.deliver, book.cancel, book.quote):
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
    book.deliver(A, o["order_id"])
    with pytest.raises(OrderError):
        book.cancel(A, o["order_id"])


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
    book.disclose(A, o["order_id"])
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
    book.disclose(A, o["order_id"])
    with pytest.raises(OrderError) as e:
        book.deliver(A, o["order_id"])
    assert e.value.code == "brand_not_cleared" and e.value.problems[0]["brand"] == "Starbucks"
    with pytest.raises(OrderError) as e:                                       # a client cannot clear it
        book.set_brand_ownership(A, A, o["order_id"], ["Starbucks"])
    assert e.value.status == 403
    with pytest.raises(OrderError):                                            # clearing another brand does not help
        book.set_brand_ownership("owner", A, o["order_id"], ["Nike"])
        book.deliver(A, o["order_id"])
    cleared = book.set_brand_ownership("owner", A, o["order_id"], ["Starbucks"], "franchise contract seen")
    assert cleared["brand_ownership"]["starbucks"]["set_by"] == "owner"
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
    book.disclose(A, o["order_id"])
    with pytest.raises(OrderError) as e:                                       # a broken list refuses, never skips
        book.deliver(A, o["order_id"])
    assert e.value.code == "studio_config_invalid"


# ---------- API level (real app, temp data dir) ----------

def _api_job(env, who=A, **kw) -> str:
    return make_job(env.svc.direct_gen.store, who, **kw)


OWNER = {"X-Participant": "owner"}
AS_A = {"X-Participant": A}


async def test_api_legal_path_and_negative_controls(env):
    j = _api_job(env)
    url = "/api/studio/orders"
    r = await env.client.post(f"{url}?client={A}", json={"items": [{"kind": "image", "job_ids": [j]}]}, headers=OWNER)
    assert r.status_code == 201, r.text
    oid = r.json()["order_id"]
    assert (await env.client.get(f"{url}/{oid}?client={B}", headers=OWNER)).status_code == 404
    assert (await env.client.get(f"{url}/{oid}", headers={"X-Participant": B})).status_code == 404
    assert (await env.client.get(url, headers={"X-Participant": B})).json() == {"orders": []}
    # a client may not act as owner, name another client, deliver or create
    assert (await env.client.get(f"{url}/{oid}?client={A}", headers={"X-Participant": B})).status_code == 403
    assert (await env.client.post(f"{url}/{oid}/deliver", headers=AS_A)).status_code == 403
    assert (await env.client.post(url, json={"items": [{"kind": "image", "job_ids": [j]}]}, headers=AS_A)).status_code == 403
    accepted = await env.client.post(f"{url}/{oid}/accept", headers=AS_A)              # the client accepts its quote
    assert accepted.status_code == 200 and accepted.json()["status"] == "accepted" and "receipt" not in accepted.json()
    refused = await env.client.post(f"{url}/{oid}/deliver?client={A}", headers=OWNER)
    assert refused.status_code == 409 and refused.json()["error"]["code"] == "disclosure_missing"
    assert refused.json()["error"]["problems"] == [{"job_id": j, "code": "disclosure_missing"}]
    assert (await env.client.post(f"{url}/{oid}/disclose?client={A}", headers=OWNER)).status_code == 200
    done = await env.client.post(f"{url}/{oid}/deliver?client={A}", headers=OWNER)
    assert done.status_code == 200 and done.json()["status"] == "delivered"
    assert done.json()["receipt"]["cost"] == "0.000900"   # 120 s x 180 W x 0.15/kWh
    bad = await env.client.post(f"{url}?client={A}", headers=OWNER,
                                json={"items": [{"kind": "image", "job_ids": [_api_job(env)], "price": "-1"}]})
    assert bad.status_code == 422 and bad.json()["error"]["code"] == "price_not_positive"
    pl = (await env.client.get("/api/studio/price-list")).json()
    assert pl["prices"] == {"photo_edit": "5.00", "image": "3.00", "clip": "15.00"} and pl["power_watts"] == "180.00"
    assert (await env.client.get("/api/studio/price-list", headers=AS_A)).status_code == 403


async def test_api_licence_warning_is_owner_only_and_brand_gate_blocks(env):
    url = "/api/studio/orders"
    j = _api_job(env, model="qwen-image-2.1", prompt="Coca-Cola bottle on ice")
    r = await env.client.post(f"{url}?client={A}", headers=OWNER,
                              json={"items": [{"kind": "image", "job_ids": [j]}], "note": "internal: check licence"})
    oid = r.json()["order_id"]
    assert r.json()["licence_warnings"][0] == {"model": "qwen-image-2.1", "licence": r.json()["licence_warnings"][0]["licence"],
                                               "status": "non_commercial"}
    await env.client.post(f"{url}/{oid}/accept?client={A}", headers=OWNER)
    await env.client.post(f"{url}/{oid}/disclose?client={A}", headers=OWNER)
    blocked = await env.client.post(f"{url}/{oid}/deliver?client={A}", headers=OWNER)
    assert blocked.status_code == 409 and blocked.json()["error"]["code"] == "brand_not_cleared"
    by_client = await env.client.post(f"{url}/{oid}/brands", headers=AS_A, json={"brands": ["Coca-Cola"]})
    assert by_client.status_code == 403
    ok = await env.client.post(f"{url}/{oid}/brands?client={A}", headers=OWNER, json={"brands": ["Coca-Cola"]})
    assert ok.status_code == 200
    done = await env.client.post(f"{url}/{oid}/deliver?client={A}", headers=OWNER)
    assert done.status_code == 200 and done.json()["receipt"]["licence_warnings"][0]["status"] == "non_commercial"
    for resp in (await env.client.get(f"{url}/{oid}", headers=AS_A), await env.client.get(url, headers=AS_A)):
        text = resp.text
        assert resp.status_code == 200
        for leak in ("licence", "qwen-image", "cost", "margin", "internal: check licence", "brand"):
            assert leak not in text, f"{leak!r} leaked to the client"


async def test_api_reads_order_files_off_the_event_loop(env, monkeypatch):
    j = _api_job(env)
    r = await env.client.post(f"/api/studio/orders?client={A}", json={"items": [{"kind": "image", "job_ids": [j]}]},
                              headers=OWNER)
    oid = r.json()["order_id"]
    loop_thread, seen = threading.get_ident(), []
    from bcc.direct_gen import orders as orders_mod
    real = orders_mod.read_text_retry

    def spy(path: Path) -> str:
        seen.append(threading.get_ident())
        return real(path)

    monkeypatch.setattr(orders_mod, "read_text_retry", spy)
    assert (await env.client.get(f"/api/studio/orders/{oid}?client={A}", headers=OWNER)).status_code == 200
    assert (await env.client.get("/api/studio/orders", headers=AS_A)).status_code == 200
    assert seen and loop_thread not in seen, "order files were read on the event-loop thread"
