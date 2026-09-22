"""Import the owner-machine critical-errors catalog into Bossman's real learning path.

Source: docs/owner/bossman_critical_errors.json (human twin:
docs/owner/BOSSMAN_CRITICAL_ERRORS_GUIDE.md).

Write path (nothing is re-implemented here):
  ApprenticeMemory.record_lesson  (bossman/apprentice/recording.py)
    -> assert_sanitized            strict secret / hidden-reasoning gate (typed reject)
    -> learning.trace.LearningStore  validation, VERIFIED invariants, redaction,
                                     journal, versions/tombstones, atomic writes, lock
Extra gates applied before the write:
  * learning_guard.reject_if_holdout(task_id)  secret holdout never enters memory;
  * cybersec.injection.scan  — an entry whose text carries a high/critical
    injection/poisoning pattern is rejected, not "cleaned";
  * evidence binding — an entry is stored as VERIFIED only if its catalog state is
    VERIFIED, every evidence file exists under the repo root, its sha256 (CRLF->LF
    normalised) matches, and the typed external verifier is present. Anything else
    is stored as UNVERIFIED (= CANDIDATE_LESSON) with the downgrade reason.

Idempotent: case_id is deterministic (task_id + observed head sha) and each record
carries a content fingerprint; an unchanged entry is skipped, a changed entry is a
new version that supersedes the old one (LearningStore history/tombstone).

The recording flag is honoured: writes need BOSSMAN_SKILL_RECORDING=1 (the store
refuses silently otherwise, so the importer refuses loudly). --dry-run needs no flag.

Usage:
  python tools/import_operational_lessons.py --data-dir <dir> [--dry-run]
  python tools/import_operational_lessons.py --data-dir <dir> --query "empty answer from local model"
  python tools/import_operational_lessons.py --match-line "<log line>"
"""
from __future__ import annotations

import argparse
import calendar
import hashlib
import json
import re
import sys
import time
from pathlib import Path
from typing import Any, Iterable

ROOT = Path(__file__).resolve().parents[1]
for _p in (ROOT, ROOT / "bossman-core"):
    if str(_p) not in sys.path:
        sys.path.insert(0, str(_p))

from learning import trace  # noqa: E402
from bossman.apprentice import flags  # noqa: E402
from bossman.apprentice.errors import SecretInRecord  # noqa: E402
from bossman.apprentice.recording import ApprenticeMemory, assert_sanitized  # noqa: E402
from bossman.cybersec.injection import scan as firewall_scan  # noqa: E402
from bossman.learning_guard import HoldoutViolation, reject_if_holdout  # noqa: E402

DEFAULT_CATALOG = ROOT / "docs" / "owner" / "bossman_critical_errors.json"
SCHEMA_ID = "bossman.critical_errors.v1"
TASK_TYPE = "owner-ops"
TASK_PREFIX = "oplesson:"
SEVERITIES = ("CRITICAL", "HIGH", "MEDIUM", "LOW")
STATUSES = ("FIXED", "OPEN", "ENV")
STATES = ("VERIFIED", "CANDIDATE")
RISK = {"CRITICAL": "high", "HIGH": "high", "MEDIUM": "medium", "LOW": "low"}
INDEPENDENT = ("external_tool", "cross_model", "human")
REQUIRED = ("id", "class", "title", "severity", "status", "symptom", "symptom_examples", "symptom_regex",
            "keywords", "detection", "root_cause", "fix", "prevention", "evidence", "verification")
_BLOCKING_FIREWALL = frozenset({"high", "critical"})


class CatalogError(ValueError):
    pass


# ------------------------------------------------------------------ catalog
def load_catalog(path: Path | str = DEFAULT_CATALOG) -> dict:
    data = json.loads(Path(path).read_text(encoding="utf-8"))
    errors: list[str] = []
    if data.get("schema") != SCHEMA_ID:
        errors.append(f"schema must be {SCHEMA_ID}")
    for key in ("captured_utc", "observed_head_sha", "environment", "recorded_by", "privacy_class", "scope"):
        if not data.get(key):
            errors.append(f"missing catalog field {key}")
    seen: set[str] = set()
    for e in data.get("entries") or []:
        eid = str(e.get("id") or "?")
        for k in REQUIRED:
            if k not in e:
                errors.append(f"{eid}: missing {k}")
        if eid in seen:
            errors.append(f"{eid}: duplicate id")
        seen.add(eid)
        if e.get("severity") not in SEVERITIES:
            errors.append(f"{eid}: severity not in {SEVERITIES}")
        if e.get("status") not in STATUSES:
            errors.append(f"{eid}: status not in {STATUSES}")
        if (e.get("verification") or {}).get("state") not in STATES:
            errors.append(f"{eid}: verification.state not in {STATES}")
        for rx in e.get("symptom_regex") or []:
            try:
                re.compile(rx)
            except re.error as exc:
                errors.append(f"{eid}: bad regex {rx!r}: {exc}")
    if not data.get("entries"):
        errors.append("catalog has no entries")
    if errors:
        raise CatalogError("; ".join(errors))
    return data


def evidence_digest(path: Path) -> str:
    """sha256 over CRLF->LF normalised bytes (git autocrlf must not flip VERIFIED)."""
    return hashlib.sha256(Path(path).read_bytes().replace(b"\r\n", b"\n")).hexdigest()


def _epoch(iso: str) -> float:
    return float(calendar.timegm(time.strptime(iso, "%Y-%m-%dT%H:%M:%SZ")))


def _evidence_problems(entry: dict, repo_root: Path) -> list[str]:
    problems: list[str] = []
    evidence = entry.get("evidence") or []
    if not evidence:
        return ["no evidence files"]
    for ev in evidence:
        rel = str(ev.get("path") or "")
        p = (repo_root / rel).resolve()
        if not rel or not str(p).startswith(str(repo_root.resolve())):
            problems.append(f"evidence path outside repo: {rel!r}")
        elif not p.is_file():
            problems.append(f"evidence missing: {rel}")
        elif evidence_digest(p) != str(ev.get("sha256") or ""):
            problems.append(f"evidence sha256 mismatch: {rel}")
    return problems


def _verification_problems(entry: dict) -> list[str]:
    v = entry.get("verification") or {}
    vf = v.get("verifier") or {}
    problems = []
    if not vf.get("principal_id") or vf.get("independence_class") not in INDEPENDENT:
        problems.append("no typed independent verifier")
    for k in ("observed_at", "expected", "actual", "external_verification"):
        if not str(v.get(k) or "").strip():
            problems.append(f"verification.{k} missing")
    return problems


# ------------------------------------------------------------------ record
def _fingerprint(record: dict) -> str:
    clean = json.loads(json.dumps(record, sort_keys=True, ensure_ascii=False))
    clean.get("applicability", {}).pop("content_sha256", None)
    return hashlib.sha256(json.dumps(clean, sort_keys=True, ensure_ascii=False).encode("utf-8")).hexdigest()


def build_record(entry: dict, catalog: dict, repo_root: Path = ROOT) -> dict:
    """Catalog entry -> apprentice `lesson` record (schemas/apprentice_skill.schema.json)."""
    rec_by = catalog["recorded_by"]
    head = catalog["observed_head_sha"]
    v = entry.get("verification") or {}
    downgrade: list[str] = []
    if v.get("state") == "VERIFIED":
        downgrade = _evidence_problems(entry, repo_root) + _verification_problems(entry)
        verified = not downgrade
    else:
        verified = False
        if v.get("reason"):
            downgrade = [f"candidate: {v['reason']}"]
    task_id = TASK_PREFIX + entry["id"]
    evidence_strs = [f"{ev['path']} sha256={ev['sha256']}" for ev in entry.get("evidence") or []]
    record: dict[str, Any] = {
        "task_id": task_id,
        "record_type": "lesson",
        "learning_status": "VERIFIED" if verified else "UNVERIFIED",
        "title": entry["title"],
        "summary": f"{entry['symptom']} | Исправление: {entry['fix']}"[:1000],
        "task_type": TASK_TYPE,
        "environment": catalog["environment"],
        "app": str(entry.get("component") or ""),
        "model": rec_by["model"],
        "agent": rec_by["agent"],
        "principal_id": rec_by["principal_id"],
        "run_id": rec_by["run_id"],
        "head_sha": head, "start_sha": head, "end_sha": head,
        "created_at": catalog["captured_utc"],
        "outcome": entry["status"],
        "confidence": 0.9 if verified else 0.5,
        "finding_ids": [entry["id"]],
        "tags": {"domain": TASK_TYPE, "risk": RISK[entry["severity"]]},
        # Semantic operational lesson. Deliberately NO target_label/action_kind/app keys:
        # the apprentice engine's negative-lesson pre-check matches UI steps on those.
        "lesson": {
            "error_id": entry["id"], "error_class": entry["class"], "severity": entry["severity"],
            "status": entry["status"], "symptom": entry["symptom"],
            "symptom_examples": list(entry.get("symptom_examples") or []),
            "symptom_regex": list(entry.get("symptom_regex") or []),
            "keywords": list(entry.get("keywords") or []),
            "detection": entry["detection"], "root_cause": entry["root_cause"],
            "fix": entry["fix"], "prevention": entry["prevention"],
        },
        "lessons": [entry["fix"], entry["prevention"]],
        "evidence": evidence_strs,
        "applicability": {
            "scope": catalog["scope"], "os": "windows-11", "privacy_class": catalog["privacy_class"],
            "lesson_state": "VERIFIED_LESSON" if verified else "CANDIDATE_LESSON",
            "catalog_state": v.get("state"), "downgrade_reason": downgrade,
            "provenance": {"catalog": "docs/owner/bossman_critical_errors.json", "sources": list(entry.get("sources") or []),
                           "recorded_by": rec_by["principal_id"], "observed_head_sha": head},
        },
    }
    if verified:
        vf = v["verifier"]
        observed = _epoch(v["observed_at"])
        collected = max(observed, _epoch(catalog["captured_utc"]))
        record["verified_by"] = [vf["principal_id"]]
        record["verifiers"] = [{k: vf[k] for k in ("principal_id", "model_id", "role", "independence_class") if k in vf}]
        record["external_verification"] = v["external_verification"]
        record["verification"] = {"expected": v["expected"], "actual": v["actual"],
                                  "limitations": list(v.get("limitations") or [])}
        record["evidence_records"] = [{
            "observed_at": observed, "collected_at": collected, "task_id": task_id, "run_id": rec_by["run_id"],
            "source": f"{ev['path']} sha256={ev['sha256']}", "principal_id": vf["principal_id"],
            "environment": catalog["environment"], "head_sha": head,
            "expected": v["expected"], "actual": v["actual"]} for ev in entry["evidence"]]
    record["applicability"]["content_sha256"] = _fingerprint(record)
    return record


def screen(record: dict) -> list[str]:
    """Poisoning / injection screen. Returns blocking findings (empty = clean)."""
    out = []
    for s in trace._walk_strings(record):
        for f in firewall_scan(s):
            if f.severity in _BLOCKING_FIREWALL:
                out.append(f"{f.pattern_id}({f.severity})")
    return sorted(set(out))


# ------------------------------------------------------------------ import
def import_catalog(catalog_path: Path | str, data_dir: Path | str, *, repo_root: Path | str = ROOT,
                   dry_run: bool = False) -> dict:
    catalog = load_catalog(catalog_path)
    if not dry_run and not flags.enabled(flags.SKILL_RECORDING):
        raise PermissionError(f"{flags.SKILL_RECORDING}=1 is required to write lessons (use --dry-run to preview)")
    repo_root = Path(repo_root)
    mem = ApprenticeMemory(Path(data_dir))
    report: dict[str, Any] = {"data_dir": str(data_dir), "dry_run": dry_run, "added": [], "updated": [],
                              "unchanged": [], "rejected": [], "verified": [], "candidate": [], "downgraded": []}
    for entry in catalog["entries"]:
        eid = entry.get("id", "?")
        try:
            reject_if_holdout(TASK_PREFIX + eid)
            record = build_record(entry, catalog, repo_root)
            findings = screen(record)
            if findings:
                raise ValueError(f"injection/poisoning pattern: {', '.join(findings)}")
            assert_sanitized(record, where=f"operational lesson {eid}")
            errs = [e for e in trace.validate(record, schema=mem.schema) if not e.startswith("case_id")]
            if errs and record["learning_status"] == "VERIFIED":
                entry = {**entry, "verification": {"state": "CANDIDATE", "reason": "store invariants: " + "; ".join(errs)}}
                record = build_record(entry, catalog, repo_root)
                errs = [e for e in trace.validate(record, schema=mem.schema) if not e.startswith("case_id")]
            if errs:
                raise ValueError("schema: " + "; ".join(errs))
        except (HoldoutViolation, SecretInRecord, ValueError, KeyError) as exc:
            report["rejected"].append({"id": eid, "reason": f"{type(exc).__name__}: {exc}"})
            continue
        bucket = "verified" if record["learning_status"] == "VERIFIED" else "candidate"
        report[bucket].append(eid)
        if (entry.get("verification") or {}).get("state") == "VERIFIED" and bucket == "candidate":
            report["downgraded"].append({"id": eid, "reason": record["applicability"]["downgrade_reason"]})
        cur = mem.current(trace.case_id(record))
        if cur and (cur.get("applicability") or {}).get("content_sha256") == record["applicability"]["content_sha256"]:
            report["unchanged"].append(eid)
            continue
        if not dry_run:
            stored = mem.record_lesson(record)
            if stored is None:          # flag flipped off mid-run: never claim a write
                report["rejected"].append({"id": eid, "reason": "recording flag off"})
                continue
        report["updated" if cur else "added"].append(eid)
    return report


# ------------------------------------------------------------------ read side
_STOP = frozenset({"from", "the", "and", "for", "with", "что", "как", "это", "при", "или", "the"})


def _tokens(text: str) -> list[str]:
    return [t for t in re.findall(r"[\w\-]+", (text or "").lower()) if len(t) >= 3 and t not in _STOP]


def _stem(t: str) -> str:
    return t[:max(4, len(t) - 2)] if len(t) > 4 else t


def _hit(q: str, hay: Iterable[str]) -> bool:
    """Crude stem match so «пустой»/«пустые», «answer»/«answers» meet."""
    qs = _stem(q)
    for h in hay:
        hs = _stem(h)
        if h.startswith(qs) or (len(hs) >= 4 and qs.startswith(hs)):
            return True
    return False


def search_lessons(data_dir: Path | str, query: str, *, include_candidates: bool = False, limit: int = 5) -> list[dict]:
    """Rank stored operational lessons for a free-text query (RU/EN). Default: VERIFIED only;
    candidates carry a retrieval_warning and must not steer autonomous planning."""
    mem = ApprenticeMemory(Path(data_dir))
    out = []
    for c in mem.all_current():
        if c.get("record_type") != "lesson" or c.get("task_type") != TASK_TYPE:
            continue
        verified = c.get("learning_status") == "VERIFIED"
        if not verified and not include_candidates:
            continue
        les = c.get("lesson") or {}
        kw = _tokens(" ".join(les.get("keywords") or []))
        title = _tokens(c.get("title", "") + " " + les.get("error_id", ""))
        body = _tokens(" ".join([les.get("symptom", ""), les.get("root_cause", ""), les.get("fix", ""),
                                 les.get("detection", "")]))
        score = sum(3 if _hit(q, kw) else 2 if _hit(q, title) else 1 if _hit(q, body) else 0 for q in _tokens(query))
        if score <= 0:
            continue
        hit = {"lesson_id": c["task_id"], "error_id": les.get("error_id"), "title": c.get("title"),
               "learning_status": c.get("learning_status"), "severity": les.get("severity"),
               "status": les.get("status"), "detection": les.get("detection"), "fix": les.get("fix"),
               "prevention": les.get("prevention"), "evidence": c.get("evidence"), "score": score}
        if not verified:
            hit["retrieval_warning"] = "CANDIDATE_LESSON: unverified — do NOT treat as preferred behaviour"
        out.append(hit)
    out.sort(key=lambda h: (-h["score"], h["learning_status"] != "VERIFIED", h["lesson_id"]))
    return out[:max(1, limit)]


def match_log_line(line: str, catalog: dict | None = None) -> list[str]:
    """Catalog ids whose symptom_regex matches a log line (for log triage tools)."""
    catalog = catalog or load_catalog()
    return [e["id"] for e in catalog["entries"] if any(re.search(rx, line) for rx in e.get("symptom_regex") or [])]


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--catalog", default=str(DEFAULT_CATALOG))
    ap.add_argument("--data-dir", help="LearningStore directory for apprentice lessons (required for import/query)")
    ap.add_argument("--repo-root", default=str(ROOT))
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--query")
    ap.add_argument("--include-candidates", action="store_true")
    ap.add_argument("--match-line")
    ns = ap.parse_args(argv)
    if ns.match_line is not None:
        print(json.dumps(match_log_line(ns.match_line, load_catalog(ns.catalog)), ensure_ascii=False))
        return 0
    if not ns.data_dir:
        ap.error("--data-dir is required")
    if ns.query is not None:
        print(json.dumps(search_lessons(ns.data_dir, ns.query, include_candidates=ns.include_candidates),
                         ensure_ascii=False, indent=2))
        return 0
    try:
        report = import_catalog(ns.catalog, ns.data_dir, repo_root=ns.repo_root, dry_run=ns.dry_run)
    except PermissionError as exc:
        print(f"REFUSED: {exc}", file=sys.stderr)
        return 2
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return 1 if report["rejected"] else 0


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
