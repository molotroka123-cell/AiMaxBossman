"""Autonomy journal: hash chain, append-only, tamper detection, redaction, multi-process appends."""
from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

from bcc.autonomy.journal import GENESIS, Journal


def clock():
    n = iter(range(10_000))
    return lambda: f"2026-09-29T00:00:{next(n):02d}Z"


def rewrite(path: Path, lines: list[dict]) -> None:
    path.write_bytes(b"".join(json.dumps(x, sort_keys=True, separators=(",", ":")).encode() + b"\n" for x in lines))


def read(path: Path) -> list[dict]:
    return [json.loads(x) for x in path.read_text(encoding="utf-8").splitlines() if x.strip()]


def test_empty_journal_verifies(tmp_path):
    v = Journal(tmp_path).verify()
    assert v.ok and v.entries == 0 and v.head == GENESIS


def test_chain_links_and_head(tmp_path):
    j = Journal(tmp_path, clock=clock())
    h1 = j.append("goal.created", {"goal_id": "JEFF-1"})
    h2 = j.append("goal.transition", {"goal_id": "JEFF-1", "to": "PLANNED"})
    rows = read(j.path)
    assert rows[0]["prev"] == GENESIS and rows[1]["prev"] == h1 and rows[1]["hash"] == h2
    assert j.head() == h2
    v = j.verify()
    assert v.ok and v.entries == 2 and v.head == h2


def test_edit_in_the_middle_is_detected(tmp_path):
    j = Journal(tmp_path, clock=clock())
    for i in range(3):
        j.append("x", {"i": i})
    rows = read(j.path)
    rows[1]["payload"]["i"] = 99
    rewrite(j.path, rows)
    v = j.verify()
    assert not v.ok and v.bad_seq == 2 and "modified" in v.reason


def test_recomputed_hash_breaks_the_next_link(tmp_path):
    from bcc.autonomy.journal import _entry_hash
    j = Journal(tmp_path, clock=clock())
    for i in range(3):
        j.append("x", {"i": i})
    rows = read(j.path)
    rows[0]["payload"]["i"] = 7
    rows[0]["hash"] = _entry_hash(rows[0])       # a careful forger re-hashes the entry
    rewrite(j.path, rows)
    v = j.verify()
    assert not v.ok and v.bad_seq == 2 and "chain" in v.reason


def test_deleted_line_and_truncated_tail_are_detected(tmp_path):
    j = Journal(tmp_path, clock=clock())
    for i in range(4):
        j.append("x", {"i": i})
    rows = read(j.path)
    rewrite(j.path, rows[:1] + rows[2:])
    assert not j.verify().ok
    rewrite(j.path, rows[:3])                    # tail cut: chain intact, head disagrees
    v = j.verify()
    assert not v.ok and "head mismatch" in v.reason


def test_unparseable_line_is_detected(tmp_path):
    j = Journal(tmp_path, clock=clock())
    j.append("x", {})
    with open(j.path, "ab") as fh:
        fh.write(b"{not json\n")
    assert not j.verify().ok


def test_secrets_are_redacted_before_hashing(tmp_path):
    j = Journal(tmp_path, clock=clock())
    token = "sk-" + "Q" * 32
    bot = "123456789:" + "A" * 35
    j.append("hand.result", {"api_key": "plain-value-123", "stdout": f"used {token} and {bot}",
                             "note": "password=" + "hunter2x"})
    text = j.path.read_text(encoding="utf-8")
    for secret in (token, bot, "plain-value-123", "hunter2x"):
        assert secret not in text
    assert "REDACTED" in text and j.verify().ok


def test_filters(tmp_path):
    j = Journal(tmp_path, clock=clock())
    j.append("goal.created", {"goal_id": "A-1"})
    j.append("goal.transition", {"goal_id": "B-1"})
    j.append("hand.request", {"goal_id": "A-1"})
    assert [e["kind"] for e in j.entries(goal_id="A-1")] == ["goal.created", "hand.request"]
    assert [e["kind"] for e in j.entries(kind="goal")] == ["goal.created", "goal.transition"]
    assert len(j.entries(limit=1)) == 1


def test_concurrent_processes_keep_one_chain(tmp_path):
    code = ("import sys; from bcc.autonomy.journal import Journal\n"
            "j = Journal(sys.argv[1])\n"
            "for i in range(25): j.append('p', {'w': sys.argv[2], 'i': i})\n")
    cwd = Path(__file__).resolve().parents[1]
    procs = [subprocess.Popen([sys.executable, "-c", code, str(tmp_path), str(w)], cwd=cwd) for w in range(3)]
    assert all(p.wait(timeout=60) == 0 for p in procs)
    v = Journal(tmp_path).verify()
    assert v.ok and v.entries == 75, v
