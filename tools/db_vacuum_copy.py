"""Safe-copy VACUUM of a SQLite memory DB with before/after proof. The live DB is only READ (online backup API).

    python tools/db_vacuum_copy.py --db <bcc.db> --work <dir> [--receipt <file.json>]

Per table: row count before/after (must be equal), plus an order-independent content fingerprint computed inside SQLite
(xor of per-row hashes is not available, so rows are hashed in Python and never printed or stored). Size, page counts,
integrity_check and free-list pages are recorded. The live DB is not replaced: the compacted copy is a candidate and
swapping it in is a separate owner/maintenance step (backend stopped, copy kept as the reserve).
"""
from __future__ import annotations

import argparse
import hashlib
import json
import sqlite3
import time
from pathlib import Path


def _tables(con: sqlite3.Connection) -> list[str]:
    return [r[0] for r in con.execute("SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%' ORDER BY name")]


def fingerprint(con: sqlite3.Connection, table: str) -> tuple[int, str]:
    """(row count, order-independent digest). Row contents are hashed in memory only."""
    acc = 0
    n = 0
    for row in con.execute(f'SELECT * FROM "{table}"'):
        acc ^= int.from_bytes(hashlib.sha256(repr(row).encode("utf-8", "replace")).digest()[:16], "big")
        n += 1
    return n, f"{acc:032x}"


def snapshot(path: Path) -> dict:
    con = sqlite3.connect(f"file:{path}?mode=ro", uri=True)
    try:
        tabs = {t: dict(zip(("rows", "fingerprint"), fingerprint(con, t))) for t in _tables(con)}
        return {"size_bytes": path.stat().st_size, "page_count": con.execute("PRAGMA page_count").fetchone()[0],
                "freelist_pages": con.execute("PRAGMA freelist_count").fetchone()[0],
                "integrity": con.execute("PRAGMA integrity_check").fetchone()[0], "tables": tabs}
    finally:
        con.close()


def vacuum_copy(db: Path, work: Path) -> dict:
    work.mkdir(parents=True, exist_ok=True)
    copy = work / (db.stem + "-copy.db")
    for p in (copy, Path(str(copy) + "-wal"), Path(str(copy) + "-shm")):
        p.unlink(missing_ok=True)
    t0 = time.time()
    src = sqlite3.connect(f"file:{db}?mode=ro", uri=True, timeout=30)
    dst = sqlite3.connect(copy)
    try:
        src.backup(dst)
    finally:
        dst.close()
        src.close()
    before = snapshot(copy)
    con = sqlite3.connect(copy)
    try:
        con.execute("PRAGMA wal_checkpoint(TRUNCATE)")
        con.execute("VACUUM")
    finally:
        con.close()
    after = snapshot(copy)
    lost = sorted(t for t in before["tables"] if before["tables"][t] != after["tables"].get(t))
    return {"at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()), "source": str(db), "copy": str(copy), "seconds": round(time.time() - t0, 1),
            "before": {k: v for k, v in before.items() if k != "tables"} | {"tables": len(before["tables"]), "rows": sum(t["rows"] for t in before["tables"].values())},
            "after": {k: v for k, v in after.items() if k != "tables"} | {"tables": len(after["tables"]), "rows": sum(t["rows"] for t in after["tables"].values())},
            "saved_bytes": before["size_bytes"] - after["size_bytes"], "tables_changed": lost,
            "verdict": "PASS" if not lost and after["integrity"] == "ok" else "FAIL",
            "live_db_modified": False}


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--db", required=True)
    ap.add_argument("--work", required=True)
    ap.add_argument("--receipt")
    a = ap.parse_args(argv)
    r = vacuum_copy(Path(a.db), Path(a.work))
    if a.receipt:
        Path(a.receipt).parent.mkdir(parents=True, exist_ok=True)
        Path(a.receipt).write_text(json.dumps(r, indent=1), encoding="utf-8", newline="\n")
    print(json.dumps(r))
    return 0 if r["verdict"] == "PASS" else 1


if __name__ == "__main__":
    raise SystemExit(main())
