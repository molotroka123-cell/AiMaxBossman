"""Regression tests for the SECOND independent review of the Jeff Stars paid-documents feature (12.10.2026).

Every test is a defect that an independent proof-of-concept reproduced against the first round of fixes. A test is
written so that it fails on the code it was written against; tests with a "negative control" assertion prove the fix
did not simply turn the behaviour off.

Findings: N1 hostile office files (child process, depth/size pre-scan, parse slots) - N2 per-person library lock -
N3 discovery question - N4 refund of an elapsed package - N5 payment behind a deferred message - #1a/#1b/#1c request
accounting - #6 zip errors - #8 poison-pill payment events - #12 order cap and report - N6 web search / event loop.
"""
from __future__ import annotations

import asyncio
import io
import json
import sqlite3
import sys
import threading
import time
import zipfile

import httpx
import pytest

from bcc.pit import doc_library as dl
from bcc.pit import paid_docs as pd_mod
from bcc.pit.stars_ledger import (DAY, DOCS30, MAX_PENDING_ORDERS, MAX_VOIDS_PER_DAY, STALE_USAGE_SECONDS,
                                  TRIAL_REQUESTS, StarsLedger, TooManyOrders)
from bcc.providers import ProviderError

from .test_jeff_stars_paid_docs import (GUEST, OTHER, StarsBotAPI, _env, guest_runtime, msg_update,  # noqa: F401
                                        pay, person_of, say, sp, stars_runtime, upload)
from .test_pit_runtime import FakeAdapter, message

W = 'xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main"'
S = 'xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main"'
T0 = 20_000_000.0
PACKAGE = DOCS30.days * DAY


def key_of(runtime, uid):
    return runtime.vault.key_for_telegram(uid)


def zipped(files: dict[str, str | bytes]) -> bytes:
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as z:
        for name, data in files.items():
            z.writestr(name, data)
    return buf.getvalue()


def nested_docx(depth: int) -> bytes:
    """Deepest element sits at nesting depth ``depth + 4`` (document, body, depth x p, r, t)."""
    xml = (f'<w:document {W}><w:body>' + "<w:p>" * depth + "<w:r><w:t>vlozhennyi tekst</w:t></w:r>"
           + "</w:p>" * depth + "</w:body></w:document>")
    return zipped({"word/document.xml": xml})


def nested_xlsx(depth: int) -> bytes:
    xml = (f'<worksheet {S}><sheetData>' + "<row>" * depth + '<c r="A1"><v>7</v></c>' + "</row>" * depth
           + "</sheetData></worksheet>")
    return zipped({"xl/worksheets/sheet1.xml": xml})


def plain_docx(text: str = "Tom &amp; Jerry &lt;3") -> bytes:
    xml = f'<w:document {W}><w:body><w:p><w:r><w:t>{text}</w:t></w:r></w:p></w:body></w:document>'
    return zipped({"word/document.xml": xml})


def ledger_with_grants(tmp_path, whens, key="k" * 64):
    ledger = StarsLedger(tmp_path / "g.sqlite3")
    for n, when in enumerate(whens, 1):
        payload = ledger.create_order(key, 7, now=when)
        out = ledger.record_payment(person_key=key, user_id=7, payload=payload, charge_id=f"c{n}", currency="XTR",
                                    total_amount=DOCS30.stars, now=when)
        assert out.status == "granted"
    return ledger, key


# =============================================================================================== N1
@pytest.mark.parametrize("name, build", [("bomb.docx", nested_docx), ("bomb.xlsx", nested_xlsx)])
def test_n1_a_tiny_file_of_40000_nested_elements_is_refused_before_the_quadratic_parser(name, build):
    data = build(40_000)
    assert len(data) < 2_000                                           # a 900-byte upload burned 27 s of CPU
    started = time.monotonic()
    with pytest.raises(dl.LibraryError) as err:
        dl.extract_sections(name, data)
    assert err.value.code == "too_long" and time.monotonic() - started < 10.0


def test_n1_the_depth_limit_is_exact_and_ordinary_documents_still_parse():
    assert dl.MAX_XML_DEPTH == 64
    sections = dl.extract_sections("ok.docx", nested_docx(dl.MAX_XML_DEPTH - 4))      # exactly 64 levels
    assert sections and all("vlozhennyi tekst" in text for _ref, text in sections)
    with pytest.raises(dl.LibraryError):
        dl.extract_sections("deep.docx", nested_docx(dl.MAX_XML_DEPTH - 3))          # 65 levels
    assert dl.extract_sections("plain.docx", plain_docx())[0][1] == "Tom & Jerry <3"


def test_n1_an_xml_part_with_more_elements_than_the_cap_is_refused():
    xml = f'<w:document {W}><w:body>' + "<w:p/>" * (dl.MAX_XML_ELEMENTS + 1) + "</w:body></w:document>"
    started = time.monotonic()
    with pytest.raises(dl.LibraryError) as err:
        dl.extract_sections("many.docx", zipped({"word/document.xml": xml}))
    assert err.value.code == "too_long" and time.monotonic() - started < 15.0
    xml = f'<w:document {W}><w:body>' + "<w:p><w:r><w:t>x</w:t></w:r></w:p>" * 300 + "</w:body></w:document>"
    assert len(dl.extract_sections("fine.docx", zipped({"word/document.xml": xml}))) == 300      # negative control


def test_n1_an_entity_declaration_is_refused_not_expanded():
    # 90 characters x 20 000 references: under expat's own amplification limit, so the OLD code parsed and returned it
    xml = ('<?xml version="1.0"?><!DOCTYPE d [<!ENTITY a "' + "A" * 90 + '">]>'
           f'<w:document {W}><w:body><w:p><w:r><w:t>' + "&a;" * 20_000 + "</w:t></w:r></w:p></w:body></w:document>")
    started = time.monotonic()
    with pytest.raises(dl.LibraryError):
        dl.extract_sections("bang.docx", zipped({"word/document.xml": xml}))
    assert time.monotonic() - started < 8.0
    assert dl.extract_sections("amp.docx", plain_docx("a &amp; b"))[0][1] == "a & b"             # negative control


def test_n1_a_parse_that_overruns_is_killed_together_with_everything_it_started(tmp_path, monkeypatch):
    psutil = pytest.importorskip("psutil")
    pidfile = tmp_path / "pids.txt"
    script = ("import os, subprocess, sys, time\n"
              "g = subprocess.Popen([sys.executable, '-c', 'import time; time.sleep(120)'])\n"
              f"open({str(pidfile)!r}, 'w').write(str(os.getpid()) + ' ' + str(g.pid))\n"
              "time.sleep(120)\n")
    monkeypatch.setattr(dl, "_child_argv", lambda tmp, name: [sys.executable, "-c", script])
    monkeypatch.setattr(dl, "CHILD_TIMEOUT_SECONDS", 3.0)
    started = time.monotonic()
    with pytest.raises(dl.LibraryError) as err:
        dl.extract_sections("slow.txt", b"hello hello hello")
    assert err.value.code == "too_long" and time.monotonic() - started < 20.0
    pids = [int(p) for p in pidfile.read_text().split()]
    assert len(pids) == 2
    deadline = time.monotonic() + 5.0
    while time.monotonic() < deadline and any(psutil.pid_exists(p) for p in pids):
        time.sleep(0.1)
    assert not any(psutil.pid_exists(p) for p in pids)                 # the child AND its child are gone


def test_n1_the_real_child_works_from_any_working_directory_without_pythonpath(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    monkeypatch.delenv("PYTHONPATH", raising=False)
    sections = dl.extract_sections("a.txt", "Срок оплаты — десять дней.".encode())   # negative control of the kill test
    assert sections == [("text=1", "Срок оплаты — десять дней.")]
    assert dl.extract_sections("b.md", b"# Title\n\nbody text here")[0][0].startswith("section=")


def test_n1_at_most_two_parses_run_at_once(monkeypatch):
    state = {"now": 0, "max": 0}
    guard = threading.Lock()

    def fake_child(tmp, name):
        with guard:
            state["now"] += 1
            state["max"] = max(state["max"], state["now"])
        time.sleep(0.3)
        with guard:
            state["now"] -= 1
        return [("text=1", "x")]
    monkeypatch.setattr(dl, "_run_child", fake_child)
    threads = [threading.Thread(target=dl.extract_sections, args=(f"f{i}.txt", b"abc")) for i in range(6)]
    [t.start() for t in threads]
    [t.join() for t in threads]
    assert dl.MAX_CONCURRENT_PARSES == 2 and state["max"] == 2         # bounded, and not accidentally serial


def test_n1_hostile_parses_do_not_stall_the_event_loop():
    bomb = nested_docx(40_000)
    gaps: list[float] = []

    async def main():
        stop = False

        async def beat():
            last = time.perf_counter()
            while not stop:
                await asyncio.sleep(0.02)
                now = time.perf_counter()
                gaps.append(now - last)
                last = now
        heartbeat = asyncio.create_task(beat())
        results = await asyncio.gather(*[asyncio.to_thread(dl.extract_sections, f"b{i}.docx", bomb)
                                         for i in range(3)], return_exceptions=True)
        stop = True
        await heartbeat
        return results
    results = asyncio.run(main())
    assert all(isinstance(r, dl.LibraryError) for r in results)
    assert len(gaps) > 5 and max(gaps) < 1.0                           # the loop kept running the whole time


def test_n1_the_shared_parse_cache_helper_is_gone_and_the_parent_never_caches_a_document():
    assert not hasattr(dl, "_forget_parsed")                           # the thread-unsafe invalidate race
    from bossman.exec_cache import get_cache
    import hashlib
    texts = [f"unique race text number {n} one two three".encode() for n in range(6)]
    threads = [threading.Thread(target=dl.extract_sections, args=(f"r{n}.txt", t)) for n, t in enumerate(texts)]
    [t.start() for t in threads]
    [t.join() for t in threads]
    cache = get_cache()
    assert all(cache.get(cache.key("parsed_file", hashlib.sha256(t).hexdigest())) is None for t in texts)


# =============================================================================================== N2
def _in_flight_upload(runtime, monkeypatch, name, data, mid_action, delay=1.2):
    """Start an upload whose parse takes ``delay`` s, run ``mid_action`` while it parses, return both results."""
    orig = dl.extract_sections

    def slow(n, d):
        time.sleep(delay)
        return orig(n, d)
    monkeypatch.setattr(dl, "extract_sections", slow)

    async def fetch(file_id, max_bytes=0):
        return data
    runtime.telegram.fetch_file = fetch
    doc = {"file_id": "f-" + name, "file_name": name, "mime_type": "text/plain", "file_size": len(data)}

    async def go():
        up = asyncio.create_task(runtime.handle(person_of(runtime, GUEST), message("", user_id=GUEST, _document=doc)))
        await asyncio.sleep(0.5)                                       # the file is being parsed right now
        mid = await mid_action(runtime)
        return mid, await up
    return asyncio.run(go())


def test_n2_deldoc_during_an_in_flight_upload_is_not_undone_and_does_not_swallow_the_new_file(tmp_path, monkeypatch):
    runtime = guest_runtime(tmp_path, FakeAdapter("Ответ [1]."))
    upload(runtime, GUEST, "a.txt", b"alpha document one two three")

    async def mid(rt_):
        return await rt_.handle(person_of(rt_, GUEST), message("/deldoc 1", user_id=GUEST, message_id=2))
    deleted, stored = _in_flight_upload(runtime, monkeypatch, "b.txt", b"beta document four five six", mid)
    listing = runtime.paid_docs.docs_text(key_of(runtime, GUEST))
    assert "удалён" in deleted
    assert "a.txt" not in listing                                      # the stale index copy did not bring it back
    assert "b.txt" in listing and "сохранён" in stored                 # negative control: the new file is there


def test_n2_delete_me_during_an_in_flight_upload_leaves_nothing_and_a_new_upload_still_works(tmp_path, monkeypatch):
    runtime = guest_runtime(tmp_path, FakeAdapter("Ответ [1]."))
    key = key_of(runtime, GUEST)
    upload(runtime, GUEST, "a.txt", b"alpha document one two three")
    real_extract = dl.extract_sections

    async def mid(rt_):
        person = person_of(rt_, GUEST)
        await rt_.handle(person, message("/delete_me", user_id=GUEST, message_id=3))
        return await rt_.handle(person, message("подтверждаю", user_id=GUEST, message_id=4))
    _, stored = _in_flight_upload(runtime, monkeypatch, "b.txt", b"beta document four five six", mid)
    docs_dir = runtime.vault.person_dir(key) / "docs"
    assert not docs_dir.exists() and runtime.paid_docs.library(key).count() == 0
    assert "не сохранён" in stored
    monkeypatch.setattr(dl, "extract_sections", real_extract)
    assert "сохранён" in upload(runtime, GUEST, "c.txt", b"gamma document seven eight nine")   # negative control
    assert runtime.paid_docs.library(key).count() == 1


def test_n2_a_profile_removed_underneath_an_upload_is_not_recreated(tmp_path, monkeypatch):
    runtime = guest_runtime(tmp_path, FakeAdapter("Ответ [1]."))
    key = key_of(runtime, GUEST)

    async def mid(rt_):
        return rt_.vault.delete(key)                                   # a path that never calls forget_person
    deleted, stored = _in_flight_upload(runtime, monkeypatch, "b.txt", b"beta document four five six", mid)
    assert deleted is True
    assert not runtime.vault.person_dir(key).exists()                  # no bare folder holding the file
    assert "не сохранён" in stored


def test_n2_the_library_never_recreates_a_missing_person_directory(tmp_path):
    person_dir = tmp_path / "person"
    lib = dl.DocLibrary(person_dir / "docs")
    with pytest.raises(dl.LibraryError) as err:
        lib.add("a.txt", b"alpha document one two three", limit=5)
    assert err.value.code == "gone" and not person_dir.exists()
    person_dir.mkdir()                                                 # negative control: an existing profile works
    rec, new = lib.add("a.txt", b"alpha document one two three", limit=5)
    assert new and lib.count() == 1 and rec.name == "a.txt"


def test_n2_deldoc_waits_for_a_commit_without_blocking_the_event_loop(tmp_path, monkeypatch):
    runtime = guest_runtime(tmp_path)
    key = key_of(runtime, GUEST)
    upload(runtime, GUEST, "a.txt", b"alpha document one two three")
    monkeypatch.setattr(pd_mod, "LOCK_WAIT_SECONDS", 0.6)
    lock = runtime.paid_docs.person_lock(key)
    lock.acquire()                                                     # a commit is writing this person's files
    gaps: list[float] = []

    async def go():
        stop = False

        async def beat():
            last = time.perf_counter()
            while not stop:
                await asyncio.sleep(0.02)
                now = time.perf_counter()
                gaps.append(now - last)
                last = now
        hb = asyncio.create_task(beat())
        reply = await runtime.handle(person_of(runtime, GUEST), message("/deldoc 1", user_id=GUEST, message_id=5))
        stop = True
        await hb
        return reply
    reply = asyncio.run(go())
    lock.release()
    assert reply == pd_mod.UPLOAD_BUSY_RU and max(gaps) < 0.4          # honest text, loop never held
    assert runtime.paid_docs.library(key).count() == 1                 # nothing was deleted half way
    assert "удалён" in say(runtime, GUEST, "/deldoc 1")                # negative control: free lock, it works


# =============================================================================================== N3
def test_n3_a_document_turn_does_not_swallow_a_pending_discovery_question_into_the_log(tmp_path):
    runtime = guest_runtime(tmp_path, FakeAdapter("Срок — 10 рабочих дней [1]."))
    upload(runtime, GUEST)
    who = person_of(runtime, GUEST).key
    pending = {"key": "hobbies", "question": "Чем вы увлекаетесь?"}
    runtime.store.put(f"discovery:{who}", pending)
    assert "Источники:" in say(runtime, GUEST, "/ask Какой срок оплаты?")
    assert runtime.store.log_entries(who) == []                        # no document text in the learning log
    assert runtime.store.get(f"discovery:{who}") == pending            # and the question is still waiting
    # negative control: the participant's next ORDINARY message is still taken as the answer
    runtime.adapter = FakeAdapter("Хорошо, понял.")
    say(runtime, GUEST, "Люблю шахматы и походы")
    assert any("[discovery]" in str(row) for row in runtime.store.log_entries(who))


# =============================================================================================== N4
def test_n4_refunding_an_elapsed_package_does_not_destroy_the_later_one(tmp_path):
    ledger, key = ledger_with_grants(tmp_path, [T0, T0 + DAY])
    now = T0 + 35 * DAY                                                # c1 ended at day 30, c2 runs day 30..60
    assert ledger.record_refund("c1", now=now).status == "refunded"
    ent = ledger.entitlement(key, now=now)
    assert ent["has_paid"] is True and ent["valid_until"] == pytest.approx(T0 + 2 * PACKAGE)
    assert ent["paid_requests_left"] == DOCS30.requests
    assert ledger.entitlement(key, now=T0 + 2 * PACKAGE - 1)["has_paid"] is True
    assert ledger.entitlement(key, now=T0 + 2 * PACKAGE)["has_paid"] is False
    # negative control: refunding the LATER package leaves the earlier one exactly as it was
    (tmp_path / "x").mkdir()
    other, okey = ledger_with_grants(tmp_path / "x", [T0, T0 + DAY])
    other.record_refund("c2", now=T0 + 5 * DAY)
    assert other.entitlement(okey, now=T0 + 5 * DAY)["valid_until"] == pytest.approx(T0 + PACKAGE)


def test_n4_refund_before_the_later_package_started_and_three_stacked(tmp_path):
    # refund before start: c2 is still queued behind c1 when it is refunded; c3 keeps its place
    ledger, key = ledger_with_grants(tmp_path, [T0, T0 + DAY, T0 + 2 * DAY])
    assert ledger.entitlement(key, now=T0 + 3 * DAY)["valid_until"] == pytest.approx(T0 + 3 * PACKAGE)
    ledger.record_refund("c2", now=T0 + 3 * DAY)
    ent = ledger.entitlement(key, now=T0 + 3 * DAY)
    assert ent["valid_until"] == pytest.approx(T0 + 2 * PACKAGE)       # c1 then c3, back to back
    assert ent["paid_requests_left"] == 2 * DOCS30.requests


def test_n4_refund_half_way_gives_the_later_package_its_full_length_from_the_refund(tmp_path):
    ledger, key = ledger_with_grants(tmp_path, [T0, T0 + DAY])
    ledger.record_refund("c1", now=T0 + 10 * DAY)                      # c1 ran 10 of its 30 days
    ent = ledger.entitlement(key, now=T0 + 10 * DAY)
    assert ent["valid_until"] == pytest.approx(T0 + 10 * DAY + PACKAGE)   # not t0+31d (a past-overlap) and not t0+60d
    assert ent["paid_requests_left"] == DOCS30.requests


def test_n4_three_stacked_packages_refund_the_first_after_elapse_then_the_second_half_way(tmp_path):
    ledger, key = ledger_with_grants(tmp_path, [T0, T0 + 3600, T0 + 7200])
    assert ledger.entitlement(key, now=T0 + 40 * DAY)["valid_until"] == pytest.approx(T0 + 3 * PACKAGE)
    ledger.record_refund("c1", now=T0 + 35 * DAY)                      # elapsed: c2 and c3 must not move
    ent = ledger.entitlement(key, now=T0 + 35 * DAY)
    assert ent["valid_until"] == pytest.approx(T0 + 3 * PACKAGE)
    assert ent["paid_requests_left"] == 2 * DOCS30.requests
    ledger.record_refund("c2", now=T0 + 45 * DAY)                      # c2 ran day 30..45; c3 starts at day 45
    ent = ledger.entitlement(key, now=T0 + 45 * DAY)
    assert ent["valid_until"] == pytest.approx(T0 + 45 * DAY + PACKAGE)
    assert ent["paid_requests_left"] == DOCS30.requests and ent["has_paid"] is True


def test_n4_the_owner_report_counts_active_entitlements_from_the_recomputed_chain(tmp_path):
    ledger, key = ledger_with_grants(tmp_path, [T0, T0 + DAY])
    ledger.record_refund("c1", now=T0 + 2 * DAY)                       # c2 now runs day 2..32; stored end said day 60
    assert ledger.report(now=T0 + 10 * DAY)["active_entitlements"] == 1            # negative control
    assert ledger.report(now=T0 + 45 * DAY)["active_entitlements"] == 0
    assert ledger.entitlement(key, now=T0 + 45 * DAY)["has_paid"] is False


# =============================================================================================== N5
class BatchAPI(StarsBotAPI):
    """getUpdates honours offset and limit and hands out whole batches, like Telegram."""

    def __init__(self):
        super().__init__([])
        self.queue: list[dict] = []

    def __call__(self, request: httpx.Request) -> httpx.Response:
        if request.url.path.rsplit("/", 1)[-1] == "getUpdates":
            body = json.loads(request.content or b"{}")
            offset = int(body.get("offset", 0))
            self.queue = [u for u in self.queue if u["update_id"] >= offset]
            return httpx.Response(200, json={"ok": True, "result": self.queue[: int(body.get("limit", 100))]})
        return super().__call__(request)


async def _poll_for(runtime, seconds):
    task = asyncio.create_task(runtime._poll())
    await asyncio.sleep(seconds)
    task.cancel()
    return await asyncio.gather(task, return_exceptions=True)


def _fill_lane(runtime, uid, n=4):
    who = person_of(runtime, uid).key
    for i in range(1, n + 1):
        runtime.store.ingest(i, who, {"_user_id": uid, "_chat_id": uid, "_message_id": i, "text": f"вопрос {i} про план"})


def test_n5_a_payment_behind_another_persons_deferred_message_is_booked_in_the_same_poll(tmp_path):
    api = BatchAPI()
    runtime = stars_runtime(tmp_path, api)
    key = key_of(runtime, GUEST)
    payload = runtime.paid_docs.invoice(key, GUEST)["payload"]
    _fill_lane(runtime, OTHER)                                         # OTHER's chat lane is full: their update defers
    api.queue = [msg_update(10, OTHER, text="пятое сообщение"),
                 msg_update(12, GUEST, successful_payment=sp(payload, "tg-hol"))]
    asyncio.run(_poll_for(runtime, 3.5))
    booked = runtime.paid_docs.ledger.payment("tg-hol")
    assert booked is not None and booked["status"] == "paid"           # Stars were taken: booked now, not "later"
    assert runtime.store.get("offset") <= 10                           # the deferred message was NOT dropped
    # the notice is that of the FIRST booking, not "already counted", once there is room for it
    runtime.store.db.execute("UPDATE inbox SET phase='done' WHERE who=?", (person_of(runtime, OTHER).key,))
    asyncio.run(_poll_for(runtime, 2.5))
    row = runtime.store.db.execute("SELECT body FROM inbox WHERE id=12").fetchone()
    assert row is not None
    event = runtime.store.open(row[0])["_payment"]
    assert event["status"] == "granted" and event["kind"] == "paid"
    assert runtime.paid_docs.ledger.report()["stars_gross_received"] == DOCS30.stars       # booked exactly once
    # negative control: without any deferral the payment is booked by the ordinary path as before
    runtime2 = stars_runtime(tmp_path / "second", BatchAPI())
    payload2 = runtime2.paid_docs.invoice(key_of(runtime2, GUEST), GUEST)["payload"]
    assert runtime2._ingest_update(msg_update(30, GUEST, successful_payment=sp(payload2, "tg-plain"))) is True
    assert runtime2.paid_docs.ledger.payment("tg-plain")["status"] == "paid"


# =============================================================================================== #8
def test_8_a_payment_without_a_charge_id_or_outside_a_private_chat_is_recorded_and_acknowledged(tmp_path):
    api = BatchAPI()
    runtime = stars_runtime(tmp_path, api)
    key = key_of(runtime, GUEST)
    payload = runtime.paid_docs.invoice(key, GUEST)["payload"]
    bad = msg_update(60, GUEST, successful_payment={"currency": "XTR", "total_amount": DOCS30.stars,
                                                    "invoice_payload": payload})
    assert runtime._ingest_update(bad) is None                         # used to raise ValueError out of the poller
    assert runtime.store.get("offset") == 61
    assert "rejected" in str(runtime.store.get("payment_error"))
    group = msg_update(70, GUEST, successful_payment=sp(payload, "tg-grp"))
    group["message"]["chat"] = {"id": -100123, "type": "supergroup"}
    assert runtime._ingest_update(group) is None
    assert runtime.store.get("offset") == 71                           # acknowledged, not redelivered forever
    assert "outside a private chat" in str(runtime.store.get("payment_error"))
    assert runtime.paid_docs.ledger.payment("tg-grp") is None
    # through the real poll loop: the poller survives and the next, well-formed payment is booked
    api.queue = [bad, msg_update(80, GUEST, successful_payment=sp(payload, "tg-ok"))]
    results = asyncio.run(_poll_for(runtime, 2.5))
    assert not any(isinstance(r, ValueError) for r in results)
    assert runtime.paid_docs.ledger.payment("tg-ok")["status"] == "paid"            # negative control


def test_8_the_duplicate_of_an_unmatched_payment_says_it_is_not_matched(tmp_path):
    runtime = guest_runtime(tmp_path)
    pd = runtime.paid_docs
    key = key_of(runtime, GUEST)
    foreign = pd.ledger.create_order(key_of(runtime, OTHER), 99)       # an order that belongs to somebody else
    first = pd.on_successful_payment(key, GUEST, {"invoice_payload": foreign, "telegram_payment_charge_id": "u1",
                                                  "currency": "XTR", "total_amount": DOCS30.stars})
    dup = pd.on_successful_payment(key, GUEST, {"invoice_payload": foreign, "telegram_payment_charge_id": "u1",
                                                "currency": "XTR", "total_amount": DOCS30.stars})
    assert (first.status, dup.status) == ("unmatched", "duplicate")
    note = pd.payment_notice({"kind": "paid", "status": dup.status, "stars": DOCS30.stars, "charge_id": "u1"}, key)
    assert "не сопоставлен" in note and "учтён один раз" not in note
    # negative control: the duplicate of a GRANTED payment still reads as "counted once"
    _, granted = pay(runtime, GUEST, "ok-1")
    again = pd.on_successful_payment(key, GUEST, {"invoice_payload": "x", "telegram_payment_charge_id": "ok-1",
                                                  "currency": "XTR", "total_amount": DOCS30.stars})
    assert granted.status == "granted" and again.status == "duplicate"
    assert "учтён один раз" in pd.payment_notice({"kind": "paid", "status": "duplicate", "charge_id": "ok-1"}, key)


# =============================================================================================== #12
def test_12_the_order_cap_never_evicts_an_order_that_passed_pre_checkout(tmp_path):
    ledger = StarsLedger(tmp_path / "o.sqlite3")
    key = "k" * 64
    payloads = [ledger.create_order(key, 7, now=1000.0 + i) for i in range(MAX_PENDING_ORDERS)]
    assert ledger.check_pre_checkout(payloads[0], 7, "XTR", DOCS30.stars, now=1020.0) == (True, "")
    with pytest.raises(TooManyOrders):
        ledger.create_order(key, 7, now=1030.0)                        # the 11th is refused, nothing is purged
    out = ledger.record_payment(person_key=key, user_id=7, payload=payloads[0], charge_id="late", currency="XTR",
                                total_amount=DOCS30.stars, now=1040.0)
    assert out.status == "granted"                                     # the approved order is still there
    # negative control: once the open invoices have expired a new one is issued again, and other people are free
    assert ledger.create_order(key, 7, now=1000.0 + 7200.0)
    assert ledger.create_order("z" * 64, 8, now=1030.0)


def test_12_buy_tells_the_participant_when_there_are_too_many_open_invoices(tmp_path):
    api = StarsBotAPI([])
    runtime = stars_runtime(tmp_path, api)
    person, key = person_of(runtime, GUEST), key_of(runtime, GUEST)
    for _ in range(MAX_PENDING_ORDERS):
        assert asyncio.run(runtime._paid_docs_command(person, key, "/buy", "")) == ""
    reply = asyncio.run(runtime._paid_docs_command(person, key, "/buy", ""))
    assert "слишком много открытых счетов: оплатите или подождите час" in reply
    assert len(api.invoices) == MAX_PENDING_ORDERS                     # no 11th invoice was sent


# =============================================================================================== #1a / #1b / #1c
def test_1a_a_cancel_after_the_model_answered_stays_charged_and_before_it_is_given_back(tmp_path, monkeypatch):
    runtime = guest_runtime(tmp_path, FakeAdapter("Ответ [1]."))
    upload(runtime, GUEST)
    key = key_of(runtime, GUEST)
    ledger = runtime.paid_docs.ledger

    async def slow_post(ctx, reply):
        await asyncio.sleep(30)
        return reply
    monkeypatch.setattr(runtime.j2, "post_reply", slow_post)

    async def cancelled_run():
        task = asyncio.create_task(runtime.handle(person_of(runtime, GUEST),
                                                  message("/ask срок оплаты акт", user_id=GUEST)))
        await asyncio.sleep(0.8)
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task
    asyncio.run(cancelled_run())
    assert len(runtime.adapter.calls) == 1                             # the model DID answer
    assert ledger.entitlement(key)["trial_left"] == TRIAL_REQUESTS - 1
    assert ledger._db.execute("SELECT final FROM usage").fetchone()[0] == 1       # settled, not left for the sweep

    # negative control: a cancel BEFORE any model answer gives the request back
    class Hang(FakeAdapter):
        async def chat(self, model, messages, **kw):
            self.calls.append((model, messages))
            await asyncio.sleep(30)

    other = guest_runtime(tmp_path / "b", Hang("x"))
    upload(other, GUEST)

    async def early_cancel():
        task = asyncio.create_task(other.handle(person_of(other, GUEST),
                                                message("/ask срок оплаты акт", user_id=GUEST)))
        await asyncio.sleep(0.8)
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task
    asyncio.run(early_cancel())
    assert other.paid_docs.ledger.entitlement(key_of(other, GUEST))["trial_left"] == TRIAL_REQUESTS


def test_1b_an_outage_is_always_given_back_and_a_replaced_model_answer_is_bounded_and_says_so(tmp_path, monkeypatch):
    monkeypatch.setenv("BOSSMAN_JEFF_J2_SAFETY_FLOOD_MAX", "1000")
    monkeypatch.setenv("BOSSMAN_JEFF_J2_SAFETY_RATE_MAX", "1000")

    class Down(FakeAdapter):
        async def chat(self, model, messages, **kw):
            self.calls.append((model, messages))
            raise ProviderError("down", kind="network")
    runtime = guest_runtime(tmp_path, Down("x"))
    upload(runtime, GUEST)
    pay(runtime, GUEST, "ch-out")
    key = key_of(runtime, GUEST)
    asks = MAX_VOIDS_PER_DAY + 3
    replies = [say(runtime, GUEST, "/ask срок оплаты акт") for _ in range(asks)]
    assert runtime.paid_docs.ledger.entitlement(key)["paid_requests_left"] == DOCS30.requests   # nothing charged
    assert not any(pd_mod.KEPT_REQUEST_RU in r for r in replies)

    # negative control: the model DID write text (here: speaking as itself) and it was replaced: bounded per day
    runtime2 = guest_runtime(tmp_path / "b", FakeAdapter("Я языковая модель, обученная компанией Google."))
    upload(runtime2, GUEST)
    pay(runtime2, GUEST, "ch-sd")
    key2 = key_of(runtime2, GUEST)
    out = [say(runtime2, GUEST, "/ask срок оплаты акт") for _ in range(MAX_VOIDS_PER_DAY + 2)]
    assert runtime2.paid_docs.ledger.entitlement(key2)["paid_requests_left"] == DOCS30.requests - 2
    assert pd_mod.KEPT_REQUEST_RU not in out[MAX_VOIDS_PER_DAY - 1]
    assert pd_mod.KEPT_REQUEST_RU in out[MAX_VOIDS_PER_DAY] and pd_mod.KEPT_REQUEST_RU in out[-1]


def test_1c_an_unfinished_request_is_swept_after_fifteen_minutes_and_never_sooner(tmp_path):
    ledger = StarsLedger(tmp_path / "s.sqlite3")
    key = "k" * 64
    old_one = ledger.consume(key, "ask", now=T0).usage_id              # crashed turn
    done = ledger.consume(key, "ask", now=T0).usage_id                 # delivered turn
    ledger.finalize(done)
    owner_row = ledger.consume("o" * 64, "ask", now=T0, owner=True).usage_id
    young = ledger.consume(key, "ask", now=T0 + STALE_USAGE_SECONDS - 60).usage_id    # may be mid-turn elsewhere
    assert ledger.sweep_unfinished(now=T0 + STALE_USAGE_SECONDS + 1) == 1
    state = {r["id"]: (r["voided"], r["final"]) for r in ledger._db.execute("SELECT id, voided, final FROM usage")}
    assert state[old_one][0] == 2 and state[done][0] == 0
    assert state[young][0] == 0 and state[owner_row][0] == 0           # negative controls
    assert ledger.entitlement(key, now=T0 + 1000)["trial_left"] == TRIAL_REQUESTS - 2


def test_1c_a_crash_between_consume_and_answer_is_given_back_when_paid_docs_starts_again(tmp_path):
    runtime = guest_runtime(tmp_path, FakeAdapter("Ответ [1]."))
    upload(runtime, GUEST)
    key = key_of(runtime, GUEST)
    pd = runtime.paid_docs
    prepared = pd.prepare(key, "ask", "срок оплаты", owner=False)      # reserved, then the process "dies"
    assert not isinstance(prepared, pd_mod.Refusal)
    assert pd.ledger.entitlement(key)["trial_left"] == TRIAL_REQUESTS - 1
    data_dir = runtime.paid_docs.data_dir
    restarted = pd_mod.PaidDocs(data_dir, runtime.vault)               # a young row is left alone
    assert restarted.ledger.entitlement(key)["trial_left"] == TRIAL_REQUESTS - 1
    restarted.ledger._db.execute("UPDATE usage SET ts = ts - ?", (STALE_USAGE_SECONDS + 60,))
    restarted.ledger.close()
    final = pd_mod.PaidDocs(data_dir, runtime.vault)                   # now it is old: swept at start
    assert final.ledger.entitlement(key)["trial_left"] == TRIAL_REQUESTS


def test_1c_a_ledger_written_before_the_final_column_is_migrated_without_sweeping_its_history(tmp_path):
    path = tmp_path / "old.sqlite3"
    db = sqlite3.connect(path)
    db.executescript("""
        CREATE TABLE usage(id INTEGER PRIMARY KEY AUTOINCREMENT, person_key TEXT NOT NULL, kind TEXT NOT NULL,
          pool TEXT NOT NULL, charge_id TEXT, ts REAL NOT NULL, voided INTEGER NOT NULL DEFAULT 0);
        INSERT INTO usage(person_key,kind,pool,charge_id,ts) VALUES('%s','ask','trial',NULL,1000.0);""" % ("k" * 64))
    db.commit()
    db.close()
    ledger = StarsLedger(path)
    assert ledger.sweep_unfinished(now=time.time()) == 0               # history is settled, never "in progress"
    assert ledger.entitlement("k" * 64)["trial_left"] == TRIAL_REQUESTS - 1


def test_1c_the_model_guard_apology_is_not_an_answer(tmp_path, monkeypatch):
    from bcc.pit.j2.model_guard import APOLOGY
    runtime = guest_runtime(tmp_path, FakeAdapter("Срок — 10 дней [1]."))
    upload(runtime, GUEST)
    key = key_of(runtime, GUEST)
    real_post = runtime.j2.post_reply

    async def garbled(ctx, reply):                                     # what the guard does to a garbled LOCAL answer
        return APOLOGY
    monkeypatch.setattr(runtime.j2, "post_reply", garbled)
    reply = say(runtime, GUEST, "/ask срок оплаты акт")
    assert reply.strip() == APOLOGY.strip()                            # the participant sees the apology only
    assert len(runtime.adapter.calls) == 1                             # although a model did answer
    assert runtime.paid_docs.ledger.entitlement(key)["trial_left"] == TRIAL_REQUESTS      # ... so it was free
    monkeypatch.setattr(runtime.j2, "post_reply", real_post)           # negative control: a normal answer is charged
    assert "Источники:" in say(runtime, GUEST, "/ask срок оплаты акт")
    assert runtime.paid_docs.ledger.entitlement(key)["trial_left"] == TRIAL_REQUESTS - 1


# =============================================================================================== #6
def _docx_with_cd_patch(patch):
    data = bytearray(plain_docx())
    patch(data, bytes(data).rfind(b"PK\x01\x02"))
    return bytes(data)


def test_6_zip_structure_errors_are_refusals_not_exceptions(tmp_path):
    def new_version(buf, at):
        buf[at + 6] = 99                                               # "version needed to extract" 9.9

    def bad_name(buf, at):
        buf[at + 8] |= 0x08                                            # flag: file name is UTF-8 ...
        buf[at + 46] = 0xE5                                            # ... and it is not
    for patch in (new_version, bad_name):
        data = _docx_with_cd_patch(patch)
        try:
            dl._refuse_zip_bomb(data)                                  # either passes quietly or refuses honestly
        except dl.LibraryError as exc:
            assert exc.code in {"unreadable", "too_long"}
        with pytest.raises(dl.LibraryError):
            dl.extract_sections("broken.docx", data)
    runtime = guest_runtime(tmp_path)
    for patch in (new_version, bad_name):
        reply = upload(runtime, GUEST, "v.docx", _docx_with_cd_patch(patch))
        assert "Не удалось прочитать файл" in reply and "не удалось завершить" not in reply.lower()
    assert "сохранён" in upload(runtime, GUEST, "ok.docx", plain_docx())          # negative control


# =============================================================================================== N6
def test_n6_document_fragments_never_trigger_a_web_search_and_prepare_runs_off_the_loop(tmp_path):
    calls: list[str] = []
    runtime = guest_runtime(tmp_path, FakeAdapter("Срок оплаты — 10 дней [1]."))

    async def fake_web(query):
        calls.append(query)
        return [{"title": "Погода", "url": "https://example.org/weather"}]
    runtime.models.web_results = fake_web
    seen = {}
    real = runtime.paid_docs.prepare

    def spy(*args, **kwargs):
        seen["thread"] = threading.current_thread().name
        return real(*args, **kwargs)
    runtime.paid_docs.prepare = spy
    upload(runtime, GUEST, "dog.txt", "Договор от 12.01.2026. Срок оплаты акта — 10 рабочих дней.".encode())
    answer = say(runtime, GUEST, "/ask Какой срок оплаты акта?", _message_id=57)
    assert calls == [] and "example.org" not in answer                 # the fragment says "2026": not a news query
    assert seen["thread"] != "MainThread"                              # retrieval did not run on the event loop
    assert "57" not in runtime._no_learn_messages                      # and no dead id is left behind
    # negative control: an ordinary message about the weather still goes to the web search
    runtime.adapter = FakeAdapter("Сегодня тепло.")
    say(runtime, GUEST, "Какая сегодня погода?")
    assert len(calls) == 1
