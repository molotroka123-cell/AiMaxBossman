"""Regression tests for the THIRD independent review of the Jeff Stars paid-documents feature (12.10.2026).

Every test is a defect that the reviewer's proof-of-concept reproduced against the second round of fixes; a test is
written so that it fails on the code it was written against, and most end with a negative control (a good document
still parses, a legitimate payment is still granted exactly once...) so the fix cannot be a switched-off feature.

Findings: 1 child memory / orphan / environment - 2 poison payment message - 3 refund before payment - 4 silent
truncation - 5 executor starvation + nested tables - 6 migration race - 7 closed allowlist payer - 8 /forget residuals
- 9 "(402)" in a provider body - 10 /delete_me on the loop + ticket texts - 11 stale temp copies - 12 /terms honesty.
"""
from __future__ import annotations

import asyncio
import dataclasses
import json
import logging
import os
import sqlite3
import subprocess
import sys
import threading
import time
from pathlib import Path

import httpx
import pytest

from bcc.pit import doc_extract_child
from bcc.pit import doc_library as dl
from bcc.pit import paid_docs as pd_mod
from bcc.pit import runtime as rtmod
from bcc.pit import stars_ledger
from bcc.pit.j2 import director as director_mod
from bcc.pit.model_route import is_payment_required
from bcc.pit.stars_ledger import DOCS30, StarsLedger
from bcc.providers import OpenAICompatAdapter, ProviderError
from bcc.telegram_companion.adapters import Telegram

from .test_jeff_stars_paid_docs import (GUEST, OTHER, OWNER, _env, guest_runtime, guest_settings,  # noqa: F401
                                        msg_update, person_of, say, sp, stars_runtime, upload)
from .test_jeff_stars_review2_fixes import BatchAPI, W, _poll_for, zipped
from .test_pit_runtime import FakeAdapter, make_runtime, message, warm

T0 = 40_000_000.0
KA = "a" * 64
PACKAGE_ROOT = str(Path(dl.__file__).resolve().parents[2])        # the directory ``bcc`` is imported from
FORGOTTEN = "Зебра7"


def key_of(runtime, uid):
    return runtime.vault.key_for_telegram(uid)


def run_python(code: str, timeout: float = 90.0) -> subprocess.CompletedProcess:
    prelude = "import sys; sys.path[:0] = " + repr([PACKAGE_ROOT] + os.environ.get("PYTHONPATH", "").split(os.pathsep))
    return subprocess.run([sys.executable, "-c", prelude + "\n" + code], capture_output=True, text=True,
                          timeout=timeout)


# ===================================================================================================== 1
def epub(chapter: str, *, name: str = "ch.xhtml") -> bytes:
    container = ('<?xml version="1.0"?><container xmlns="urn:oasis:names:tc:opendocument:xmlns:container">'
                 '<rootfiles><rootfile full-path="c.opf" media-type="application/oebps-package+xml"/></rootfiles>'
                 '</container>')
    opf = ('<?xml version="1.0"?><package xmlns="http://www.idpf.org/2007/opf"><manifest>'
           f'<item id="a" href="{name}" media-type="application/xhtml+xml"/></manifest>'
           '<spine><itemref idref="a"/></spine></package>')
    return zipped({"META-INF/container.xml": container, "c.opf": opf, name: chapter})


def test_1_an_epub_member_inflating_past_the_cap_is_refused_before_any_parser_and_a_good_epub_still_parses():
    bomb = epub("<a" * 1_500_000)                                      # 3 MB of markup in a 3 KB upload
    assert len(bomb) < 10_000
    started = time.monotonic()
    with pytest.raises(dl.LibraryError) as err:
        dl.extract_sections("bomb.epub", bomb)
    assert err.value.code == "too_long" and time.monotonic() - started < 10.0
    good = dl.extract_sections("good.epub", epub("<html><body><p>Срок оплаты — десять дней.</p></body></html>"))
    assert any("Срок оплаты" in text for _ref, text in good)           # negative control: a real epub is not refused


def test_1_a_member_cap_does_not_touch_office_packages(tmp_path):
    big = "<w:p><w:r><w:t>слово</w:t></w:r></w:p>" * 60_000            # ~2.5 MB of document.xml, inside the budgets
    xml = f"<w:document {W}><w:body>{big}</w:body></w:document>"
    assert len(xml.encode()) > dl.MAX_MARKUP_MEMBER_BYTES
    sections = dl.extract_sections("long.docx", zipped({"word/document.xml": xml}))
    assert len(sections) == 400 and sections.partial and sections.partial["total"] == 60_000


def test_1_the_child_environment_is_an_allow_list_and_keeps_what_python_needs(monkeypatch):
    monkeypatch.setenv("TELEGRAM_BOT_TOKEN", "123:secret-token")
    monkeypatch.setenv("OPENROUTER_API_KEY", "sk-or-secret")
    monkeypatch.setenv("BOSSMAN_DATA_DIR", "C:/private/data")
    env = dl._child_env()
    assert not any("TOKEN" in k or "KEY" in k or k.startswith("BOSSMAN") for k in env)
    assert "PATH" in env and env["PYTHONUTF8"] == "1" and PACKAGE_ROOT in env["PYTHONPATH"]
    # end to end: what a child process really sees (a stand-in child that reports its own environment)
    script = ("import json, os\n"
              "print(json.dumps({'ok': True, 'sections': [['env', ','.join(sorted(os.environ))]]}))\n")
    monkeypatch.setattr(dl, "_child_argv", lambda tmp, name: [sys.executable, "-c", script])
    seen = dl.extract_sections("a.txt", b"hello")[0][1].split(",")
    assert not any(k in seen for k in ("TELEGRAM_BOT_TOKEN", "OPENROUTER_API_KEY", "BOSSMAN_DATA_DIR"))
    assert "PATH" in seen                                             # negative control: the allow-list lets PATH in


ALLOCATE = """
import json, sys
from bcc.pit import doc_library as dl
if {apply}:
    dl.apply_child_limits()
chunks, got = [], 0
try:
    while got < 1400:
        chunks.append(bytearray(32 * 1024 * 1024))
        got += 32
except MemoryError:
    pass
print(json.dumps({{"ok": True, "sections": [["mem", str(got)]]}}))
"""


def _allocated(monkeypatch, ceiling_mib: int, *, apply: bool) -> int:
    monkeypatch.setattr(dl, "CHILD_MEMORY_BYTES", ceiling_mib * 1024 * 1024)
    script = ALLOCATE.format(apply=apply)
    monkeypatch.setattr(dl, "_child_argv", lambda tmp, name: [sys.executable, "-c", script])
    monkeypatch.setattr(dl, "CHILD_TIMEOUT_SECONDS", 60.0)
    return int(dl.extract_sections("a.txt", b"x")[0][1])


def test_1_the_child_cannot_commit_more_memory_than_its_ceiling(monkeypatch):
    low = _allocated(monkeypatch, 300, apply=True)
    assert low <= 300                                                  # stopped by the ceiling, not by the script
    high = _allocated(monkeypatch, 1024, apply=True)
    assert high >= 512 and high > low                                  # negative control: the ceiling is the limit


@pytest.mark.skipif(os.name != "nt", reason="the parent-side Job Object is the Windows mechanism")
def test_1_the_parent_puts_the_child_in_a_job_object_before_it_can_allocate(monkeypatch):
    got = _allocated(monkeypatch, 300, apply=False)                   # the script never limits itself here
    assert got <= 300


@pytest.mark.skipif(os.name != "nt", reason="KILL_ON_JOB_CLOSE is the Windows mechanism")
def test_1_a_child_in_the_job_dies_with_a_parent_that_was_killed():
    psutil = pytest.importorskip("psutil")
    code = ("import os, subprocess, sys\n"
            "from bcc.pit import doc_library as dl\n"
            "p = subprocess.Popen([sys.executable, '-c', 'import time; time.sleep(120)'])\n"
            "job = dl._limit_child_process(p)\n"
            "assert job is not None\n"
            "print(p.pid, flush=True)\n"
            "os._exit(1)\n")                                           # the parent 'crashes' with the child running
    out = run_python(code)
    assert out.returncode == 1, out.stderr
    pid = int(out.stdout.split()[0])
    deadline = time.monotonic() + 8.0
    while time.monotonic() < deadline and psutil.pid_exists(pid):
        time.sleep(0.1)
    assert not psutil.pid_exists(pid)


def test_1_the_child_ends_itself_when_nobody_is_left_to_kill_it():
    assert callable(doc_extract_child._arm_watchdog)
    started = time.monotonic()
    out = run_python("import time\nfrom bcc.pit import doc_extract_child as c\n"
                     "c._arm_watchdog(1.0)\ntime.sleep(60)\n", timeout=40)
    assert out.returncode == 124 and time.monotonic() - started < 30.0
    assert dl.CHILD_WATCHDOG_SECONDS > dl.CHILD_TIMEOUT_SECONDS        # the parent's own kill always comes first


def test_1_an_unavailable_ceiling_is_logged_and_the_parse_still_works(monkeypatch, caplog):
    def broken(*_args, **_kwargs):
        raise OSError(5, "job objects are not available here")
    monkeypatch.setattr(dl, "_win_make_job", broken)
    monkeypatch.setattr(dl, "_REPORTED", set())
    with caplog.at_level(logging.WARNING, logger=dl.__name__):
        assert dl.extract_sections("a.txt", "Срок оплаты — десять дней.".encode())[0][1].startswith("Срок")
    if os.name == "nt":
        assert any("resource ceiling not applied" in r.getMessage() for r in caplog.records)
    # In a CHILD: the function limits its own process (RLIMIT_AS / RLIMIT_CPU on POSIX, a Job Object on Windows).
    # Called in the pytest worker it capped the whole xdist worker at 1 GiB / 30 CPU seconds on Linux CI and killed
    # it (and every Chromium it launched) - the red `pytest (py3.x)` of 94e40cd1.
    out = run_python("from bcc.pit import doc_library as dl\nprint(isinstance(dl.apply_child_limits(), str))")
    assert out.returncode == 0 and out.stdout.strip() == "True", out.stderr   # reports a reason, never raises


# ===================================================================================================== 2
def _bot_refund(update_id, uid, payload, charge):
    upd = msg_update(update_id, uid, refunded_payment={"currency": "XTR", "total_amount": DOCS30.stars,
                                                       "invoice_payload": payload,
                                                       "telegram_payment_charge_id": charge})
    upd["message"]["from"] = {"id": 777000111, "is_bot": True}         # a service message attributed to the bot
    return upd


def test_2_a_refund_notice_attributed_to_the_bot_is_booked_and_routed_to_the_user_chat(tmp_path):
    rt = stars_runtime(tmp_path, BatchAPI())
    key = key_of(rt, GUEST)
    payload = rt.paid_docs.invoice(key, GUEST)["payload"]
    assert rt._ingest_update(msg_update(1, GUEST, successful_payment=sp(payload, "bot-ref"))) is True
    assert rt._ingest_update(_bot_refund(2, GUEST, payload, "bot-ref")) is True       # used to raise ValueError
    row = rt.store.db.execute("SELECT who, body FROM inbox WHERE id=2").fetchone()
    body = rt.store.open(row["body"])
    assert row["who"] == f"{GUEST}:{GUEST}" and body["_chat_id"] == GUEST and body["_user_id"] == GUEST
    assert rt.paid_docs.ledger.payment("bot-ref")["status"] == "refunded"
    assert rt.store.get("offset") == 3


def test_2_a_successful_payment_whose_sender_is_not_the_chat_is_booked_to_the_chat(tmp_path):
    rt = stars_runtime(tmp_path, BatchAPI())
    key = key_of(rt, GUEST)
    payload = rt.paid_docs.invoice(key, GUEST)["payload"]
    upd = msg_update(1, GUEST, successful_payment=sp(payload, "odd-from"))
    upd["message"]["from"] = {"id": GUEST + 1, "is_bot": False}
    assert rt._ingest_update(upd) is True                              # used to raise ValueError out of the poller
    assert rt.paid_docs.ledger.payment("odd-from")["status"] == "paid"
    assert rt.paid_docs.ledger.entitlement(key)["has_paid"] is True


def test_2_a_bad_update_is_acknowledged_journaled_and_cannot_stop_the_poller(tmp_path):
    api = BatchAPI()
    rt = stars_runtime(tmp_path, api)
    key = key_of(rt, GUEST)
    payload = rt.paid_docs.invoice(key, GUEST)["payload"]
    impossible = msg_update(10, GUEST, successful_payment=sp(payload, "tg-impossible"))
    impossible["message"]["chat"] = {"id": 2 ** 60, "type": "private"}     # not a Telegram identity: Person() raises
    impossible["message"]["from"] = {"id": 2 ** 60, "is_bot": False}
    api.queue = [impossible, msg_update(11, GUEST, successful_payment=sp(payload, "tg-good")),
                 msg_update(12, GUEST, successful_payment=sp(payload, "tg-good")),   # redelivered, new update id
                 msg_update(13, OTHER, text="привет")]
    results = asyncio.run(_poll_for(rt, 3.5))
    assert not any(isinstance(r, Exception) and not isinstance(r, asyncio.CancelledError) for r in results)
    assert rt.store.get("offset") == 14                                # the bad update did not hold anything back
    journal = rt.paid_docs.ledger.rejected_events()
    assert len(journal) == 1
    assert journal[0]["kind"] == "successful_payment" and journal[0]["charge_id"] == "tg-impossible"
    assert journal[0]["update_id"] == 10 and "ValueError" in journal[0]["reason"]
    assert "привет" not in json.dumps(journal, ensure_ascii=False)     # never message text
    ledger = rt.paid_docs.ledger
    # the money of the impossible identity was booked by the first pass (kept and flagged for the owner, never lost)
    assert ledger.payment("tg-impossible")["status"] == "unmatched"
    assert ledger.report()["stars_unmatched_needs_owner"] == DOCS30.stars
    assert ledger.payment("tg-good")["status"] == "paid"               # negative control: booked, and exactly once
    assert ledger._db.execute("SELECT COUNT(*) FROM grants").fetchone()[0] == 1
    assert ledger.report()["stars_gross_received"] == DOCS30.stars
    assert ledger.report()["rejected_events"] == 1


def test_2_a_malformed_pre_checkout_is_acknowledged_and_does_not_kill_the_poller(tmp_path):
    api = BatchAPI()
    rt = stars_runtime(tmp_path, api)
    api.queue = [{"update_id": 5, "pre_checkout_query": {"id": "q1", "from": {"id": "not-a-number"},
                                                         "currency": "XTR", "total_amount": "NaN",
                                                         "invoice_payload": "x"}},
                 msg_update(6, OTHER, text="привет")]
    results = asyncio.run(_poll_for(rt, 3.0))
    assert not any(isinstance(r, Exception) and not isinstance(r, asyncio.CancelledError) for r in results)
    assert rt.store.get("offset") == 7
    assert any(e["kind"] == "pre_checkout_query" for e in rt.paid_docs.ledger.rejected_events())


# ===================================================================================================== 3
def test_3_a_refund_that_arrives_before_its_payment_is_never_granted(tmp_path):
    led = StarsLedger(tmp_path / "l.sqlite3")
    payload = led.create_order(KA, 1, now=T0)
    assert led.record_refund("late-1", now=T0 + 1).status == "unknown"        # the refund event came first
    out = led.record_payment(person_key=KA, user_id=1, payload=payload, charge_id="late-1", currency="XTR",
                             total_amount=DOCS30.stars, now=T0 + 2)
    assert out.status == "refunded"                                           # was "granted" (free access)
    assert led.entitlement(KA, now=T0 + 3)["has_paid"] is False
    assert led.consume(KA, "ask", now=T0 + 3).pool == "trial"                 # only the free trial remains
    rep = led.report(now=T0 + 3)
    assert (rep["stars_gross_received"], rep["stars_refunded"], rep["stars_net_received"]) == \
        (DOCS30.stars, DOCS30.stars, 0)
    assert rep["refunds_before_payment"] == 1                                 # flagged for the owner
    assert led.payment("late-1")["status"] == "refunded"
    assert led.record_payment(person_key=KA, user_id=1, payload=payload, charge_id="late-1", currency="XTR",
                              total_amount=DOCS30.stars, now=T0 + 4).status == "duplicate"
    # negative control: an ordinary payment of the same person, no refund, is granted exactly once
    other = led.create_order(KA, 1, now=T0 + 5)
    args = dict(person_key=KA, user_id=1, payload=other, charge_id="fine-1", currency="XTR",
                total_amount=DOCS30.stars, now=T0 + 5)
    assert led.record_payment(**args).status == "granted"
    assert led.record_payment(**args).status == "duplicate"
    assert led.entitlement(KA, now=T0 + 6)["paid_requests_left"] == DOCS30.requests


def test_3_the_tombstone_survives_a_restart_and_is_reported_for_a_refund_without_any_payment(tmp_path):
    path = tmp_path / "l.sqlite3"
    first = StarsLedger(path)
    first.record_refund("ghost-1", now=T0)
    first.close()
    second = StarsLedger(path)
    payload = second.create_order(KA, 1, now=T0 + 1)
    assert second.record_payment(person_key=KA, user_id=1, payload=payload, charge_id="ghost-1", currency="XTR",
                                 total_amount=DOCS30.stars, now=T0 + 2).status == "refunded"
    second.record_refund("never-paid", now=T0 + 3)
    assert second.report(now=T0 + 4)["refunds_without_payment"] == 1


def test_3_through_the_runtime_the_early_refund_leaves_no_access_and_says_so(tmp_path):
    rt = stars_runtime(tmp_path, BatchAPI())
    key = key_of(rt, GUEST)
    payload = rt.paid_docs.invoice(key, GUEST)["payload"]
    refund = msg_update(1, GUEST, refunded_payment={"currency": "XTR", "total_amount": DOCS30.stars,
                                                    "invoice_payload": payload, "telegram_payment_charge_id": "early"})
    assert rt._ingest_update(refund) is True
    assert rt._ingest_update(msg_update(2, GUEST, successful_payment=sp(payload, "early"))) is True
    row = rt.store.db.execute("SELECT body FROM inbox WHERE id=2").fetchone()
    event = rt.store.open(row["body"])["_payment"]
    assert (event["kind"], event["status"]) == ("paid", "refunded")
    assert "уже вернул" in rt.paid_docs.payment_notice(event, key)
    assert "refund arrived before the payment" in str(rt.store.get("payment_error"))
    assert rt.paid_docs.ledger.entitlement(key)["has_paid"] is False


# ===================================================================================================== 4
def docx_paragraphs(n: int) -> bytes:
    body = "".join("<w:p><w:r><w:t>Пункт %d договора: срок %d дней.</w:t></w:r></w:p>" % (i, i)
                   for i in range(1, n + 1))
    return zipped({"word/document.xml": f"<w:document {W}><w:body>{body}</w:body></w:document>"})


def test_4_a_long_docx_is_stored_partly_and_every_reply_says_so(tmp_path):
    rt = guest_runtime(tmp_path, FakeAdapter("Ответ [1]."))
    reply = upload(rt, OWNER, "contract.docx", docx_paragraphs(1200))
    assert "сохранён частично: первые 400 из ~1200 абзацев" in reply          # not a silent "сохранён"
    rec = rt.paid_docs.library(key_of(rt, OWNER)).list()[0]
    assert rec.partial and (rec.kept, rec.total, rec.unit) == (400, 1200, "абзацев")
    assert "сохранён частично: первые 400 из ~1200 абзацев" in say(rt, OWNER, "/docs")
    index = json.loads((rt.paid_docs.library(key_of(rt, OWNER)).root / "index.json").read_text(encoding="utf-8"))
    assert index["docs"][0]["partial"] is True and index["docs"][0]["kept"] == 400
    summary = say(rt, OWNER, "/ask Пункт 5 договора срок")
    assert "только на сохранённую часть" in summary                          # the answer says what it rests on
    # the same bytes again: still said, never "saved as new"
    assert "первые 400 из ~1200" in upload(rt, OWNER, "contract-copy.docx", docx_paragraphs(1200))
    # negative control: a document under every cap carries no note and no flag
    short = upload(rt, OWNER, "short.docx", docx_paragraphs(50))
    assert "частично" not in short and "сохранён:" in short
    assert not rt.paid_docs.library(key_of(rt, OWNER)).list()[-1].partial
    assert "частично" not in say(rt, OWNER, "/docs").splitlines()[-1]


def test_4_a_csv_a_markdown_file_and_a_json_cut_by_the_parser_are_all_reported(tmp_path):
    rt = guest_runtime(tmp_path)
    csv_rows = "\n".join("item%d,%d" % (i, i) for i in range(20_000)).encode()
    assert "первые 5000 из ~20000 строк" in upload(rt, OWNER, "table.csv", csv_rows)
    md = "\n".join("# Heading %d\nbody of section %d" % (i, i) for i in range(600)).encode()
    assert "первые 400 из ~600 разделов" in upload(rt, OWNER, "notes.md", md)
    big = json.dumps([{"k": i, "v": "x" * 20} for i in range(8000)]).encode()
    reply = upload(rt, OWNER, "data.json", big)
    assert "первые 200000 символов, в файле их больше" in reply
    # negative control: a csv of 100 rows is complete
    assert "частично" not in upload(rt, OWNER, "small.csv", "\n".join("a%d,%d" % (i, i) for i in range(100)).encode())


def test_4_a_spreadsheet_with_more_rows_than_the_parser_keeps_is_reported_with_both_counts(tmp_path):
    def sheet(n: int) -> bytes:
        rows = "".join('<row r="%d"><c r="A%d" t="inlineStr"><is><t>v%d</t></is></c></row>' % (i, i, i)
                       for i in range(1, n + 1))
        xml = ('<worksheet xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main"><sheetData>'
               + rows + "</sheetData></worksheet>")
        return zipped({"xl/worksheets/sheet1.xml": xml})
    rt = guest_runtime(tmp_path)
    reply = upload(rt, OWNER, "ledger.xlsx", sheet(5100))
    assert "сохранён частично: первые 5000 из ~5100 строк" in reply
    assert "частично" not in upload(rt, OWNER, "small.xlsx", sheet(40))         # negative control


# ===================================================================================================== 5
def nested_tables_docx(levels: int, leaves: int = 10) -> bytes:
    opened = "<w:tbl><w:tr><w:tc>" * levels
    closed = "</w:tc></w:tr></w:tbl>" * levels
    leaf = "<w:p><w:r><w:t>клетка</w:t></w:r></w:p>" * leaves
    return zipped({"word/document.xml": f"<w:document {W}><w:body>{opened}{leaf}{closed}</w:body></w:document>"})


def test_5_nested_tables_past_the_cap_are_refused_early_and_ordinary_tables_still_parse():
    started = time.monotonic()
    with pytest.raises(dl.LibraryError) as err:
        dl.extract_sections("bomb.docx", nested_tables_docx(19, 230_000))      # the reviewer's 19-level, 230k leaf bomb
    assert err.value.code == "too_long" and time.monotonic() - started < 15.0
    with pytest.raises(dl.LibraryError):
        dl.extract_sections("six.docx", nested_tables_docx(dl.MAX_TABLE_DEPTH + 1))
    ok = dl.extract_sections("five.docx", nested_tables_docx(dl.MAX_TABLE_DEPTH))     # negative control: exactly the cap
    assert ok and any("клетка" in text for _ref, text in ok)


def test_5_hostile_uploads_queue_on_their_own_pool_and_do_not_delay_other_work(tmp_path, monkeypatch):
    rt = guest_runtime(tmp_path, FakeAdapter("Ответ [1]."))
    real = dl.extract_sections
    threads: set[str] = set()

    def slow(name, data):
        threads.add(threading.current_thread().name)
        time.sleep(1.0)
        return real(name, data)
    monkeypatch.setattr(dl, "extract_sections", slow)

    async def fetch(file_id, max_bytes=0):
        return file_id.encode() + b" unique text of this upload"
    rt.telegram.fetch_file = fetch
    owner = person_of(rt, OWNER)

    async def go():
        uploads = []
        for i in range(40):                                             # far more than the default executor has
            doc = {"file_id": f"doc-{i}", "file_name": f"d{i}.txt", "mime_type": "text/plain", "file_size": 30}
            uploads.append(asyncio.create_task(rt.handle(owner, message("", user_id=OWNER, message_id=i + 1,
                                                                        _document=doc))))
        await asyncio.sleep(0.4)
        t = time.perf_counter()
        await asyncio.to_thread(lambda: 1)                              # stands in for retrieval / /deldoc / speech
        waited = time.perf_counter() - t
        replies = await asyncio.gather(*uploads)
        return waited, replies
    waited, replies = asyncio.run(go())
    assert waited < 0.5                                                 # was: behind every queued parse
    busy = [r for r in replies if r == pd_mod.PARSE_BUSY_RU]
    stored = [r for r in replies if "сохранён" in r]
    assert busy and stored and len(busy) + len(stored) == 40            # honest "busy", nothing lost silently
    assert len(stored) <= pd_mod.MAX_PARSE_BACKLOG
    assert threads and all(name.startswith("jdoc-parse") for name in threads)     # the dedicated pool, 2 workers
    assert len(threads) <= pd_mod.PARSE_POOL_WORKERS
    assert rt.paid_docs._backlog == 0                                   # nothing left counted after the burst


# ===================================================================================================== 6
def test_6_an_old_ledger_opened_while_another_process_migrates_it_does_not_fail(tmp_path):
    if sqlite3.sqlite_version_info < (3, 35):
        pytest.skip("ALTER TABLE DROP COLUMN needs SQLite 3.35")
    path = tmp_path / "old.sqlite3"
    seed = sqlite3.connect(path, isolation_level=None)
    seed.execute("PRAGMA journal_mode=WAL")                             # as the ledger runs: opening changes nothing
    seed.executescript(stars_ledger._SCHEMA)
    seed.execute("ALTER TABLE usage DROP COLUMN final")                 # the previous build's schema, all tables there
    seed.close()
    holding, release = threading.Event(), threading.Event()

    def other_process():
        c = sqlite3.connect(path, isolation_level=None, timeout=30)
        c.execute("BEGIN IMMEDIATE")
        holding.set()
        release.wait(5)
        c.execute("ALTER TABLE usage ADD COLUMN final INTEGER NOT NULL DEFAULT 1")
        c.execute("COMMIT")
        c.close()
    t = threading.Thread(target=other_process)
    t.start()
    assert holding.wait(5)
    threading.Timer(0.6, release.set).start()
    ledger = StarsLedger(path)                                          # "duplicate column name: final" before
    t.join()
    assert "final" in {r["name"] for r in ledger._db.execute("PRAGMA table_info(usage)").fetchall()}
    assert ledger.consume(KA, "ask", now=T0).ok                         # negative control: the ledger works


def test_6_three_processes_open_an_old_schema_ledger_together(tmp_path):
    fails = 0
    for trial in range(3):
        path = tmp_path / ("m%d.sqlite3" % trial)
        db = sqlite3.connect(path)
        db.executescript("CREATE TABLE usage(id INTEGER PRIMARY KEY AUTOINCREMENT, person_key TEXT NOT NULL,"
                         " kind TEXT NOT NULL, pool TEXT NOT NULL, charge_id TEXT, ts REAL NOT NULL,"
                         " voided INTEGER NOT NULL DEFAULT 0);")
        db.commit()
        db.close()
        go = tmp_path / ("go%d" % trial)
        code = ("from pathlib import Path\n"
                "from bcc.pit.stars_ledger import StarsLedger\n"
                f"while not Path({str(go)!r}).exists():\n    pass\n"
                f"StarsLedger({str(path)!r})\nprint('ok')\n")
        procs = [subprocess.Popen([sys.executable, "-c", "import sys; sys.path[:0] = "
                                   + repr([PACKAGE_ROOT] + os.environ.get("PYTHONPATH", "").split(os.pathsep))
                                   + "\n" + code], stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
                 for _ in range(3)]
        time.sleep(1.5)
        go.write_text("x")
        for p in procs:
            _out, err = p.communicate(timeout=60)
            fails += p.returncode != 0
            if p.returncode:
                print(err.strip().splitlines()[-1])
    assert fails == 0


# ===================================================================================================== 7
def _closed_runtime(tmp_path):
    settings = dataclasses.replace(guest_settings(tmp_path), allowlist_open=False)
    rt = rtmod.ParticipantRuntime(settings)
    rt.telegram = Telegram(rtmod._transport_settings(settings), transport=BatchAPI())
    rt.adapter = FakeAdapter("ok")
    return rt


def test_7_a_payer_outside_a_closed_allowlist_is_booked_acknowledged_and_the_owner_is_told_once(tmp_path):
    rt = _closed_runtime(tmp_path)
    stranger = 909
    key = key_of(rt, stranger)
    payload = rt.paid_docs.invoice(key, stranger)["payload"]
    assert rt._ingest_update(msg_update(5, stranger, successful_payment=sp(payload, "gone-1"))) in (True, None)
    assert rt.paid_docs.ledger.payment("gone-1")["status"] == "paid"                  # the money is on the books
    assert rt.store.get("offset") == 6                                               # acknowledged
    stuck = rt.store.db.execute("SELECT COUNT(*) FROM inbox WHERE who=? AND phase='pending'",
                                (f"{stranger}:{stranger}",)).fetchone()[0]
    assert stuck == 0                                                                # nothing for the stranger's lane
    owner = next(p for p in rt.settings.people if p.role == "owner")
    rows = rt.store.db.execute("SELECT who, body FROM inbox").fetchall()
    assert [r["who"] for r in rows] == [owner.key]                                   # one alert, on the owner's lane
    event = rt.store.open(rows[0]["body"])["_payment"]
    assert event["kind"] == "unlisted_paid" and event["charge_id"] == "gone-1"
    assert "вне списка" in rt.paid_docs.payment_notice(event, key_of(rt, OWNER))
    # the same charge delivered again (new update id) alerts nobody a second time and books nothing twice
    assert rt._ingest_update(msg_update(6, stranger, successful_payment=sp(payload, "gone-1"))) in (True, None)
    assert rt.store.db.execute("SELECT COUNT(*) FROM inbox").fetchone()[0] == 1
    assert rt.paid_docs.ledger.report()["stars_gross_received"] == DOCS30.stars


def test_7_a_listed_guest_still_gets_the_ordinary_confirmation(tmp_path):
    rt = _closed_runtime(tmp_path)
    key = key_of(rt, GUEST)
    payload = rt.paid_docs.invoice(key, GUEST)["payload"]
    assert rt._ingest_update(msg_update(5, GUEST, successful_payment=sp(payload, "listed-1"))) is True
    row = rt.store.db.execute("SELECT who, body FROM inbox WHERE id=5").fetchone()
    assert row["who"] == f"{GUEST}:{GUEST}" and rt.store.open(row["body"])["_payment"]["status"] == "granted"


# ===================================================================================================== 8
def _say(runtime, person, text, mid=1):
    return asyncio.run(runtime.handle(person, message(text, user_id=person.user_id, message_id=mid)))


def test_8_forget_removes_the_words_from_why_memory_the_inbox_and_the_director_state(tmp_path):
    runtime = make_runtime(tmp_path, adapter=FakeAdapter("Хорошо, запомнил."))
    person = runtime.settings.people[0]
    key = runtime.vault.key_for_telegram(person.user_id)
    warm(runtime, key)
    _say(runtime, person, f"Я люблю разводить кактусы сорта {FORGOTTEN}", 1)
    _say(runtime, person, "Люблю шахматы и походы", 2)
    assert [r for r in runtime.vault.list_facts(key) if FORGOTTEN.lower() in str(r.get("value", "")).lower()]
    # the three leftover stores of the review: /why_memory context, the inbox body, the director topic
    runtime.store.put(f"last_context:{person.key}", [f"Я люблю разводить кактусы сорта {FORGOTTEN}", "люблю шахматы"])
    body = {"_user_id": person.user_id, "_chat_id": person.chat_id, "_message_id": 900,
            "text": f"Я люблю разводить кактусы сорта {FORGOTTEN}"}
    assert runtime.store.ingest(900, person.key, body)
    runtime.store.db.execute("UPDATE inbox SET phase='done' WHERE id=900")     # a finished turn: kept for a week
    other_body = {"_user_id": person.user_id, "_chat_id": person.chat_id, "_message_id": 901, "text": "про шахматы"}
    runtime.store.ingest(901, person.key, other_body)
    director = next(m for m in runtime.j2.modules if m.name == "director")
    state = director_mod.TopicState(turn=3, topic={"зебра7": {"w": FORGOTTEN.lower(), "s": 2.0},
                                                   "шахмат": {"w": "шахматы", "s": 1.0}})
    director._states[key] = state
    path = runtime.vault.person_dir(key) / "j2" / "director.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(state.as_json(time.time()), ensure_ascii=False), encoding="utf-8")
    assert FORGOTTEN.lower() in _say(runtime, person, "/why_memory", 3).lower()    # precondition: it shows up

    assert "Забыл: удалено 1" in _say(runtime, person, f"/forget {FORGOTTEN}", 4)

    why = _say(runtime, person, "/why_memory", 5)
    assert FORGOTTEN.lower() not in why.lower() and "шахматы" in why                # the unrelated item stays
    rows = runtime.store.db.execute("SELECT id, body FROM inbox").fetchall()
    held = [r["id"] for r in rows if FORGOTTEN.lower() in str(runtime.store.open(r["body"])).lower()]
    assert held == []
    kept = [runtime.store.open(r["body"]) for r in rows if r["id"] == 901]
    assert kept and kept[0]["text"] == "про шахматы"                               # an unrelated row is untouched
    saved = json.loads(path.read_text(encoding="utf-8"))
    assert FORGOTTEN.lower() not in json.dumps(saved, ensure_ascii=False).lower()
    assert "шахмат" in saved["topic"]                                              # negative control: other topic kept
    assert "зебра7" not in director._states[key].topic and "шахмат" in director._states[key].topic


def test_8_a_director_file_is_scrubbed_even_when_the_module_was_never_loaded(tmp_path):
    runtime = make_runtime(tmp_path, adapter=FakeAdapter("ок"))
    person = runtime.settings.people[0]
    key = runtime.vault.key_for_telegram(person.user_id)
    warm(runtime, key)
    _say(runtime, person, f"Я люблю разводить кактусы сорта {FORGOTTEN}", 1)
    path = runtime.vault.person_dir(key) / "j2" / "director.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    state = director_mod.TopicState(turn=1, topic={"зебра7": {"w": FORGOTTEN.lower(), "s": 1.0}})
    path.write_text(json.dumps(state.as_json(time.time()), ensure_ascii=False), encoding="utf-8")
    runtime.__dict__.pop("_j2_pipeline", None)                          # as after a restart: no module in memory
    _say(runtime, person, f"/forget {FORGOTTEN}", 2)
    assert FORGOTTEN.lower() not in path.read_text(encoding="utf-8").lower()


# ===================================================================================================== 9
def _provider_runtime(tmp_path, status, body):
    calls: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path.endswith("/models"):
            return httpx.Response(200, json={"data": [{"id": "free/model:free",
                                                       "pricing": {"prompt": "0", "completion": "0"}}]})
        calls.append(request.url.path)
        return httpx.Response(status, json=body)
    adapter = OpenAICompatAdapter(base_url="http://127.0.0.1:9/v1", api_key="test-key",
                                  transport=httpx.MockTransport(handler))
    runtime = make_runtime(tmp_path, adapter=adapter)
    person = runtime.settings.people[0]
    warm(runtime, runtime.vault.key_for_telegram(person.user_id))
    return runtime, person, calls


def test_9_a_500_whose_body_mentions_402_does_not_close_the_free_route(tmp_path):
    body = {"error": {"message": "upstream gateway failed (402) while contacting backend"}}
    runtime, person, calls = _provider_runtime(tmp_path, 500, body)
    _say(runtime, person, "Расскажи, как составить план недели", 1)
    first = len(calls)
    _say(runtime, person, "А как составить план на месяц?", 2)
    assert first >= 1 and len(calls) > first                           # the second turn asked the provider again
    assert "payment_required" not in str(runtime.model_route_status()["rejected"])


def test_9_a_real_402_still_closes_the_route_and_the_status_is_what_decides():
    err = ProviderError("сервер провайдера ответил 500: upstream failed (402)", kind="http", status_code=500)
    assert is_payment_required(err) is False
    assert is_payment_required(ProviderError("провайдер требует оплату (402): нет кредитов", status_code=402)) is True
    assert is_payment_required(ProviderError("any wording at all", status_code=402)) is True
    assert is_payment_required(ProviderError("провайдер отказал (402): payment required", kind="http")) is True
    assert is_payment_required(ProviderError("ошибка шлюза: body said (402) here", kind="http")) is False
    assert is_payment_required(RuntimeError("(402)")) is False


def test_9_the_http_status_is_carried_by_the_adapter(tmp_path):
    runtime, person, calls = _provider_runtime(tmp_path, 402, {"error": {"message": "Insufficient credits"}})
    _say(runtime, person, "Расскажи, как составить план недели", 1)
    n = len(calls)
    _say(runtime, person, "А как составить план на месяц?", 2)
    assert n == 1 and len(calls) == 1                                  # closed after the one real 402
    adapter = OpenAICompatAdapter(base_url="http://127.0.0.1:9/v1", api_key="k", transport=httpx.MockTransport(
        lambda request: httpx.Response(502, json={"error": {"message": "bad (402)"}})))
    with pytest.raises(ProviderError) as caught:
        asyncio.run(adapter.chat("m", [{"role": "user", "content": "hi"}]))
    assert caught.value.status_code == 502


# ===================================================================================================== 10
def test_10_delete_me_confirmation_does_not_hold_the_event_loop_while_a_commit_holds_the_lock(tmp_path, monkeypatch):
    rt = guest_runtime(tmp_path, FakeAdapter("ok"))
    key = key_of(rt, GUEST)
    upload(rt, GUEST, "a.txt", b"alpha document one two three")
    monkeypatch.setattr(pd_mod, "LOCK_WAIT_SECONDS", 1.5)
    lock = rt.paid_docs.person_lock(key)
    person = person_of(rt, GUEST)
    gaps: list[float] = []

    async def go():
        await rt.handle(person, message("/delete_me", user_id=GUEST, message_id=3))
        stop = False

        async def beat():
            last = time.perf_counter()
            while not stop:
                await asyncio.sleep(0.02)
                now = time.perf_counter()
                gaps.append(now - last)
                last = now
        hb = asyncio.create_task(beat())
        await asyncio.sleep(0.1)
        lock.acquire()
        threading.Timer(1.0, lock.release).start()                      # a slow commit holding the person's lock
        reply = await rt.handle(person, message("подтверждаю", user_id=GUEST, message_id=4))
        stop = True
        await hb
        return reply
    reply = asyncio.run(go())
    assert reply == rtmod.DELETED_RU and max(gaps) < 0.5               # was: the whole lock wait, loop frozen
    assert rt.paid_docs.library(key).count() == 0                       # and the person's documents are gone


def test_10_delete_me_erases_support_ticket_texts_but_keeps_the_numbered_row(tmp_path):
    rt = guest_runtime(tmp_path)
    key = key_of(rt, GUEST)
    assert "Обращение №1 принято" in say(rt, GUEST, "/paysupport мой телефон 555-0100, верните деньги")
    assert "Обращение №2 принято" in say(rt, OTHER, "/paysupport у меня другая жалоба")
    pay_ = rt.paid_docs.invoice(key, GUEST)["payload"]
    rt.paid_docs.on_successful_payment(key, GUEST, {"invoice_payload": pay_, "telegram_payment_charge_id": "ch-t",
                                                    "currency": "XTR", "total_amount": DOCS30.stars})
    say(rt, GUEST, "/delete_me")
    assert say(rt, GUEST, "подтверждаю") == rtmod.DELETED_RU
    rows = {r["id"]: r for r in rt.paid_docs.ledger.open_tickets()}
    assert "555-0100" not in json.dumps(rows[1], ensure_ascii=False) and rows[1]["person_key"] == key
    assert "другая жалоба" in rows[2]["text"]                          # negative control: someone else's text stays
    assert rt.paid_docs.ledger.payment("ch-t")["status"] == "paid"      # the payment record is never deleted


# ===================================================================================================== 11
def test_11_stale_plaintext_temp_copies_are_swept_at_start_and_nothing_else_is_touched(tmp_path, monkeypatch):
    temp = tmp_path / "tmp"
    temp.mkdir()
    old, young, foreign = temp / "jdoc-abcd1234.docx", temp / "jdoc-zzzz9999.txt", temp / "keep-me-old.txt"
    lookalike = temp / "jdoc-notours.txt.bak"
    folder = temp / "jdoc-folder1.txt"
    for f in (old, young, foreign, lookalike):
        f.write_text("plaintext of somebody's document", encoding="utf-8")
    folder.mkdir()
    long_ago = time.time() - 2 * 3600
    for f in (old, foreign, lookalike):
        os.utime(f, (long_ago, long_ago))
    monkeypatch.setattr("tempfile.gettempdir", lambda: str(temp))
    runtime = guest_runtime(tmp_path / "rt")
    pd_mod.PaidDocs(runtime.paid_docs.data_dir, runtime.vault)         # a start of the feature: the sweep runs
    assert not old.exists()                                            # older than an hour, our prefix: gone
    assert young.exists()                                              # a parse may be using it right now
    assert foreign.exists() and lookalike.exists() and folder.exists()
    assert dl.sweep_stale_temp(root=temp, max_age=0.0) == 1            # only the young matching file is left to go
    assert foreign.exists()


# ===================================================================================================== 12
def test_12_terms_say_where_document_fragments_may_go(tmp_path):
    rt = guest_runtime(tmp_path)
    terms = say(rt, GUEST, "/terms")
    flat = " ".join(terms.split())
    assert "если для вас включена удалённая обработка" in flat
    assert "удалённой бесплатной модели" in flat and "остаются на этом компьютере" in flat and "/privacy" in flat
    assert "Условия пакета" in terms and "/paysupport" in terms         # negative control: the rest is still there
    assert "Telegram Stars" in terms and "/delete_me" in terms


# ===================================================================================================== flag off
def test_without_the_flag_nothing_of_this_round_changes_behaviour(tmp_path, monkeypatch):
    monkeypatch.delenv(pd_mod.ENV_FLAG)
    runtime = make_runtime(tmp_path, adapter=FakeAdapter("Хорошо."))
    person = runtime.settings.people[0]
    warm(runtime, runtime.vault.key_for_telegram(person.user_id))
    assert runtime.paid_docs is None
    assert _say(runtime, person, "/delete_me", 1) == rtmod.DELETE_CONFIRM_RU
    assert _say(runtime, person, "подтверждаю", 2) in (rtmod.DELETED_RU, "Удалять нечего — профиль уже отсутствует.")
    assert runtime._ingest_update_checked({"update_id": 1, "message": {"message_id": 1, "text": "x",
                                           "from": {"id": 5, "is_bot": False}, "chat": {"id": 5, "type": "private"}}}
                                          ) in (True, None, False)
