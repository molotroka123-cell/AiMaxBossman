"""Append-only JSONL ledger (source of truth) + SQLite WAL index + CSV export.

Layout under <root> (outside Git):
    raw/YYYY-MM-DD/observations.jsonl
    crops/YYYY-MM-DD/...
    market-observations.sqlite
    exports/YYYY-MM-DD.csv
    reports/collector-status.json
"""
from __future__ import annotations

import csv
import json
import os
import sqlite3
from pathlib import Path
from typing import Any

from . import extract, schema

COLUMNS = ("captured_at_utc", "status", "stream_state", "symbol", "exchange", "timeframe", "price",
           "oi_value", "oi_unit", "cvd_value", "cvd_unit", "cvd_type", "confidence", "fresh_frame",
           "frame_sha256", "extractor", "video_time", "stream_clock")


def default_root(channel: str = "k1m6a") -> Path:
    base = os.environ.get("BCC_DATA_DIR") or os.path.join(os.environ.get("LOCALAPPDATA") or str(Path.home()),
                                                          "Bossman", "CommandCenter")
    return Path(base) / "market-data" / "twitch" / channel


class Ledger:
    def __init__(self, root: Path):
        self.root = Path(root)
        self.root.mkdir(parents=True, exist_ok=True)
        self.db_path = self.root / "market-observations.sqlite"
        self.db = sqlite3.connect(self.db_path, timeout=30)
        self.db.execute("PRAGMA journal_mode=WAL")
        self.db.execute("PRAGMA synchronous=NORMAL")
        self.db.execute(
            "CREATE TABLE IF NOT EXISTS observations (id INTEGER PRIMARY KEY, dedupe TEXT UNIQUE NOT NULL, "
            + ", ".join(f"{c} TEXT" for c in COLUMNS) + ", raw TEXT NOT NULL)")
        self.db.commit()

    def close(self) -> None:
        self.db.close()

    def raw_path(self, day: str) -> Path:
        return self.root / "raw" / day / "observations.jsonl"

    def crops_dir(self, day: str) -> Path:
        d = self.root / "crops" / day
        d.mkdir(parents=True, exist_ok=True)
        return d

    def record(self, rec: dict[str, Any]) -> bool:
        """Validate, append to JSONL (fsync), index in SQLite. False = duplicate (not re-written).
        An invalid record raises — the collector never writes a record the contract rejects."""
        errs = schema.validate(rec)
        if errs:
            raise ValueError(f"invalid observation: {errs}")
        key = schema.dedupe_key(rec)
        if self.db.execute("SELECT 1 FROM observations WHERE dedupe=?", (key,)).fetchone():
            return False
        day = rec["captured_at_utc"][:10]
        path = self.raw_path(day)
        path.parent.mkdir(parents=True, exist_ok=True)
        line = json.dumps(rec, ensure_ascii=False, sort_keys=True)
        with open(path, "a", encoding="utf-8", newline="\n") as fh:
            fh.write(line + "\n")
            fh.flush()
            os.fsync(fh.fileno())
        row = self._row(rec)
        with self.db:
            self.db.execute(f"INSERT OR IGNORE INTO observations (dedupe, {', '.join(COLUMNS)}, raw) VALUES "
                            f"(?, {', '.join('?' for _ in COLUMNS)}, ?)", (key, *row, line))
        return True

    @staticmethod
    def _row(rec: dict[str, Any]) -> tuple:
        m, q, ev, ins = rec["metrics"], rec["quality"], rec["evidence"], rec["instrument"]
        vals = {"captured_at_utc": rec["captured_at_utc"], "status": q["status"],
                "stream_state": rec["source"]["stream_state"], "symbol": ins.get("symbol"),
                "exchange": ins.get("exchange"), "timeframe": ins.get("timeframe"),
                "confidence": q.get("confidence"), "fresh_frame": q.get("fresh_frame"),
                "frame_sha256": ev.get("frame_sha256"), "extractor": ev.get("extractor"),
                "video_time": ev.get("video_time"), "stream_clock": ev.get("stream_clock"), **m}
        return tuple(None if vals.get(c) is None else str(vals.get(c)) for c in COLUMNS)

    def reindex(self) -> int:
        """Rebuild the SQLite index from the JSONL ledger (the ledger is the truth)."""
        n = 0
        for path in sorted((self.root / "raw").glob("*/observations.jsonl")):
            for line in path.read_text(encoding="utf-8").splitlines():
                if not line.strip():
                    continue
                rec = json.loads(line)
                with self.db:
                    cur = self.db.execute(
                        f"INSERT OR IGNORE INTO observations (dedupe, {', '.join(COLUMNS)}, raw) VALUES "
                        f"(?, {', '.join('?' for _ in COLUMNS)}, ?)", (schema.dedupe_key(rec), *self._row(rec), line))
                n += cur.rowcount
        return n

    def export_csv(self, day: str | None = None) -> Path:
        out_dir = self.root / "exports"
        out_dir.mkdir(parents=True, exist_ok=True)
        name = day or "all"
        path = out_dir / f"{name}.csv"
        q = f"SELECT {', '.join(COLUMNS)} FROM observations"
        args: tuple = ()
        if day:
            q += " WHERE substr(captured_at_utc,1,10)=?"
            args = (day,)
        q += " ORDER BY captured_at_utc, id"
        with open(path, "w", encoding="utf-8", newline="") as fh:
            w = csv.writer(fh, lineterminator="\n")
            w.writerow(COLUMNS)
            w.writerows(self.db.execute(q, args))
        return path

    def counts(self) -> dict[str, Any]:
        rows = self.db.execute("SELECT status, COUNT(*) FROM observations GROUP BY status").fetchall()
        by = {s: c for s, c in rows}
        total = sum(by.values())
        live = self.db.execute("SELECT COUNT(*) FROM observations WHERE stream_state='LIVE'").fetchone()[0]
        oi = self.db.execute("SELECT COUNT(*) FROM observations WHERE oi_value IS NOT NULL AND status='VERIFIED'").fetchone()[0]
        cvd = self.db.execute("SELECT COUNT(*) FROM observations WHERE cvd_value IS NOT NULL AND status='VERIFIED'").fetchone()[0]
        first_last = self.db.execute("SELECT MIN(captured_at_utc), MAX(captured_at_utc) FROM observations").fetchone()
        return {"attempted": total, "by_status": by, "live_attempts": live,
                "verified_rate": round(by.get("VERIFIED", 0) / total, 4) if total else None,
                "oi_coverage_live": round(oi / live, 4) if live else None,
                "cvd_coverage_live": round(cvd / live, 4) if live else None,
                "first": first_last[0], "last": first_last[1]}

    def resume_state(self) -> tuple[str | None, dict[str, float]]:
        """Recover freshness and jump baselines from durable observations.

        JSONL is authoritative after a crash between append and SQLite insert.
        Only a fresh frame can become the previous frame; only a calibrated,
        individually verified metric can become a jump baseline.
        """
        self.reindex()
        frame = None
        values: dict[str, float] = {}
        for (raw,) in self.db.execute("SELECT raw FROM observations ORDER BY id DESC"):
            rec = json.loads(raw)
            if frame is None and rec["quality"].get("fresh_frame"):
                frame = rec["evidence"].get("frame_sha256")
            if (rec["quality"].get("status") == schema.VERIFIED
                    and extract.calibrated_extractor(rec["evidence"].get("extractor"))):
                for metric in ("cvd", "oi"):
                    m = rec["metrics"]
                    if (metric not in values and m.get(f"{metric}_value") is not None
                            and rec["quality"].get("per_metric", {}).get(metric, {}).get("status") == schema.VERIFIED):
                        values[metric] = m[f"{metric}_value"] * schema.UNITS[m[f"{metric}_unit"]]
            if frame is not None and len(values) == 2:
                break
        return frame, values
