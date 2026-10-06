"""SwapMe / Fresh Vibes admin journeys: create -> save -> reopen -> restart.

Pure domain + journal (no bcc stack, no model, no network). A "restart" is a new
Journal object on the same file, including a crash that tore the last line.
"""
from __future__ import annotations

import json

from tools.owner_journeys import admin_domain as dom


def _seed(path):
    j = dom.Journal(path)
    lead = dom.capture_lead(j, "beauty", {"name": "Test Person", "contact": "t@example.invalid",
                                          "interest": "lash lift", "consent": True})
    bk = dom.request_booking(j, "beauty", lead["lead_id"], "lash lift", "2030-01-15T11:00:00")
    return j, lead, bk


def test_records_survive_reopen_and_chain_continues(tmp_path):
    p = tmp_path / "j.jsonl"
    _, lead, bk = _seed(p)
    j2 = dom.Journal(p)                                   # restart
    assert j2.verify_chain()
    assert dom.find_lead(j2, "beauty", lead["lead_id"]) is not None
    rem = dom.queue_reminder_draft(j2, bk["booking_id"])  # continue the journey after restart
    assert rem["ok"] and rem["status"] == "QUEUED_NOT_SENT"
    lead2 = dom.capture_lead(j2, "dental", {"name": "B", "contact": "+420 000 111 222",
                                            "interest": "dental hygiene", "consent": True})
    assert lead2["lead_id"] == "dental-lead-002" or lead2["lead_id"].startswith("dental-lead-")
    assert len({r["data"].get("lead_id") for r in j2.rows() if r["kind"] == "lead"}) == 2
    assert j2.verify_chain()


def test_crash_torn_last_line_does_not_brick_the_journal(tmp_path):
    p = tmp_path / "j.jsonl"
    _, lead, bk = _seed(p)
    good_rows = len(dom.Journal(p).rows())
    with p.open("a", encoding="utf-8") as fh:            # crash mid-append: no newline, cut JSON
        fh.write('{"seq": 99, "ts": "2030-01-01T00:00:00+00:00", "kind": "boo')
    j = dom.Journal(p)
    assert len(j.rows()) == good_rows                     # torn tail ignored, earlier records intact
    assert j.verify_chain()
    rec = j.append("note", {"after": "restart"})          # next append heals the tail
    assert rec["seq"] == good_rows + 1
    j3 = dom.Journal(p)
    assert len(j3.rows()) == good_rows + 1 and j3.verify_chain()
    assert all(json.loads(x) for x in p.read_text(encoding="utf-8").splitlines())


def test_mid_file_corruption_is_still_detected(tmp_path):
    p = tmp_path / "j.jsonl"
    _seed(p)
    lines = p.read_text(encoding="utf-8").splitlines()
    lines[0] = lines[0][:20]                              # damage a NON-final line
    p.write_text("\n".join(lines) + "\n", encoding="utf-8")
    assert dom.Journal(p).verify_chain() is False        # tamper/corruption is never silently repaired
