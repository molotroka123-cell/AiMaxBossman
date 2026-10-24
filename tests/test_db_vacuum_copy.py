"""tools/db_vacuum_copy.py: compaction on a copy keeps every row; the live DB is untouched; a lost row is detected."""
from __future__ import annotations

import sqlite3
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "tools"))
import db_vacuum_copy as dv  # noqa: E402


def _db(path: Path) -> None:
    con = sqlite3.connect(path)
    con.execute("CREATE TABLE a(id INTEGER PRIMARY KEY, t TEXT)")
    con.execute("CREATE TABLE b(k TEXT, v BLOB)")
    con.executemany("INSERT INTO a(t) VALUES (?)", [("x" * 500,)] * 400)
    con.executemany("INSERT INTO b VALUES (?,?)", [(str(i), b"z" * 100) for i in range(50)])
    con.execute("DELETE FROM a WHERE id % 2 = 0")        # leaves free pages to reclaim
    con.commit()
    con.close()


def test_vacuum_copy_shrinks_keeps_all_rows_and_leaves_the_source_alone(tmp_path):
    db = tmp_path / "m.db"
    _db(db)
    size = db.stat().st_size
    r = dv.vacuum_copy(db, tmp_path / "work")
    assert r["verdict"] == "PASS" and r["tables_changed"] == [] and r["before"]["rows"] == r["after"]["rows"] == 250
    assert r["saved_bytes"] > 0 and r["after"]["freelist_pages"] == 0
    assert db.stat().st_size == size and r["live_db_modified"] is False


def test_fingerprint_detects_a_changed_or_missing_row(tmp_path):
    db = tmp_path / "m.db"
    _db(db)
    con = sqlite3.connect(db)
    f1 = dv.fingerprint(con, "b")
    con.execute("DELETE FROM b WHERE k='3'")
    f2 = dv.fingerprint(con, "b")
    con.execute("UPDATE b SET v=x'00' WHERE k='4'")
    f3 = dv.fingerprint(con, "b")
    assert f1 != f2 != f3 and f1[0] == 50 and f2[0] == 49
