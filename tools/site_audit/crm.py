"""Minimal local CRM (SQLite) for scenario D — subset of the owner plan §9.

Tables: leads, website_audits, messages. ``messages`` only ever holds owner-gated drafts/notes; there is
no delivery status other than the owner's own decision fields.
"""
from __future__ import annotations

import datetime as _dt
import json
import sqlite3
from pathlib import Path

SCHEMA = """
CREATE TABLE IF NOT EXISTS leads (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  company_name TEXT NOT NULL,
  website TEXT NOT NULL UNIQUE,
  category TEXT,
  city TEXT,
  source TEXT NOT NULL,
  score INTEGER NOT NULL DEFAULT 0,
  detected_problems TEXT NOT NULL DEFAULT '[]',
  status TEXT NOT NULL DEFAULT 'new'
    CHECK (status IN ('new','audited','offer_card_ready','owner_approved','rejected','simulated')),
  notes TEXT,
  created_at TEXT NOT NULL,
  updated_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS website_audits (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  lead_id INTEGER NOT NULL REFERENCES leads(id),
  url TEXT NOT NULL,
  audited_at TEXT NOT NULL,
  status TEXT NOT NULL,
  findings TEXT NOT NULL,
  signals TEXT NOT NULL,
  tier TEXT
);
CREATE TABLE IF NOT EXISTS messages (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  lead_id INTEGER NOT NULL REFERENCES leads(id),
  kind TEXT NOT NULL CHECK (kind IN ('offer_card','owner_note')),
  path TEXT,
  status TEXT NOT NULL DEFAULT 'draft' CHECK (status IN ('draft','owner_approved','owner_rejected')),
  created_at TEXT NOT NULL
);
"""
SEVERITY_POINTS = {"high": 30, "medium": 15, "low": 5}


def _now() -> str:
    return _dt.datetime.now(_dt.timezone.utc).isoformat(timespec="seconds")


def lead_score(findings: list[dict]) -> int:
    """0..100: more and more severe measured problems = better reason to offer help."""
    return min(100, sum(SEVERITY_POINTS.get(f.get("severity"), 0) for f in findings))


def connect(data_dir: str | Path) -> sqlite3.Connection:
    d = Path(data_dir)
    d.mkdir(parents=True, exist_ok=True)
    con = sqlite3.connect(d / "site_audit_crm.sqlite3")
    con.row_factory = sqlite3.Row
    con.execute("PRAGMA foreign_keys = ON")
    con.executescript(SCHEMA)
    return con


def upsert_lead(con: sqlite3.Connection, *, company_name: str, website: str, source: str,
                category: str | None = None, city: str | None = None, notes: str | None = None,
                status: str = "new") -> int:
    now = _now()
    con.execute(
        "INSERT INTO leads(company_name, website, category, city, source, status, notes, created_at, updated_at) "
        "VALUES (?,?,?,?,?,?,?,?,?) ON CONFLICT(website) DO UPDATE SET company_name=excluded.company_name, "
        "category=excluded.category, city=excluded.city, notes=excluded.notes, updated_at=excluded.updated_at",
        (company_name, website, category, city, source, status, notes, now, now))
    con.commit()
    return int(con.execute("SELECT id FROM leads WHERE website=?", (website,)).fetchone()["id"])


def record_audit(con: sqlite3.Connection, lead_id: int, audit: dict, tier: str | None = None,
                 lead_status: str = "audited") -> int:
    findings = audit.get("findings") or []
    cur = con.execute(
        "INSERT INTO website_audits(lead_id, url, audited_at, status, findings, signals, tier) VALUES (?,?,?,?,?,?,?)",
        (lead_id, audit.get("final_url") or audit.get("url"), audit.get("audited_at") or _now(), audit.get("status"),
         json.dumps(findings, ensure_ascii=False), json.dumps(audit.get("signals") or {}, ensure_ascii=False), tier))
    con.execute("UPDATE leads SET score=?, detected_problems=?, status=?, updated_at=? WHERE id=?",
                (lead_score(findings), json.dumps([f["id"] for f in findings]), lead_status, _now(), lead_id))
    con.commit()
    return int(cur.lastrowid)


def add_offer_card(con: sqlite3.Connection, lead_id: int, path: str | Path) -> int:
    cur = con.execute("INSERT INTO messages(lead_id, kind, path, created_at) VALUES (?,?,?,?)",
                      (lead_id, "offer_card", str(path), _now()))
    con.commit()
    return int(cur.lastrowid)
