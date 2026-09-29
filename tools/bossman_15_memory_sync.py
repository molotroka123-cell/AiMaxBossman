#!/usr/bin/env python3
"""Import verified Bossman-1.5 workflow lessons into the canonical LessonBook.

Trading hypotheses from YouTube are deliberately NOT imported here. They remain
in the trading quarantine until the trading-learning promotion gates have
independent episodes, lookahead-clean outcomes and OOS evidence.

This script teaches only operational workflow lessons (free-first routing,
independent deterministic verification) and verifies them only when the run
report contains the corresponding deterministic evidence.
"""
from __future__ import annotations

import argparse
import json
import os
import pathlib
import subprocess
import sys
import time

ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from learning.lessons import CoachingEpisode, LessonBook, Provenance  # noqa: E402


def default_dir() -> pathlib.Path:
    explicit = os.environ.get("BOSSMAN_LEARNING_DIR", "").strip()
    if explicit:
        return pathlib.Path(explicit)
    data = os.environ.get("BCC_DATA_DIR", "").strip()
    if data:
        return pathlib.Path(data) / "learning"
    local = os.environ.get("LOCALAPPDATA", "").strip()
    if local:
        return pathlib.Path(local) / "Bossman" / "CommandCenter" / "learning"
    return ROOT / "data" / "learning"


def head_sha() -> str:
    p = subprocess.run(["git", "rev-parse", "HEAD"], cwd=ROOT, capture_output=True,
                       text=True, encoding="utf-8", errors="replace", check=False)
    value = p.stdout.strip()
    return value if p.returncode == 0 and len(value) == 40 else "unknown"


def save_and_verify(book: LessonBook, *, attempt: str, correction: str, failure: str,
                    task_class: str, evidence_source: str, expected: str, actual: str,
                    verified: bool) -> dict:
    ep = CoachingEpisode(
        attempt_id=attempt,
        task_id=attempt,
        project_id="bossman-1.5",
        task_class=task_class,
        failure_observation=failure,
        correction=correction,
        source="student",
        kind="student_fix",
        status="candidate",
        scope="global",
        environment="bossman-owner-run",
        agent="bossman-1.5-economy",
        model="workflow",
        check=expected,
        provenance=Provenance(
            who="bossman-1.5-economy",
            what="measured owner workflow",
            evidence_refs=[evidence_source],
            run_id=attempt,
            model="workflow",
        ),
    )
    rec = book.save(ep)
    if not verified:
        return rec
    return book.verify(
        rec["task_id"],
        verifier={
            "principal_id": "deterministic-owner-verifier",
            "independence_class": "pytest-and-cost-ledger",
            "model_id": "none",
            "run_id": attempt + "-verify",
        },
        evidence={
            "source": evidence_source,
            "expected": expected,
            "actual": actual,
            "head_sha": head_sha(),
            "environment": "owner-machine",
            "observed_at": time.time(),
            "collected_at": time.time(),
        },
        statement="Deterministic owner evidence verified this workflow lesson.",
    )


def main(argv=None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("economy_report")
    ap.add_argument("--learning-dir", default="")
    ns = ap.parse_args(argv)
    report_path = pathlib.Path(ns.economy_report)
    report = json.loads(report_path.read_text(encoding="utf-8"))
    book = LessonBook(pathlib.Path(ns.learning_dir) if ns.learning_dir else default_dir())

    tests_pass = bool((report.get("repo_tests_final") or {}).get("pass"))
    paid = report.get("paid") or {}
    free_clean = all(
        (meta.get("usage") or {}).get("cost_usd", 0) == 0
        for video in report.get("videos") or []
        for meta in (video.get("nemotron") or {}).values()
    ) and all(
        ((video.get("ling") or {}).get("usage") or {}).get("cost_usd", 0) == 0
        for video in report.get("videos") or []
        if video.get("ling")
    )
    paid_calls = int(paid.get("glm_calls") or 0)

    saved = []
    saved.append(save_and_verify(
        book,
        attempt="v15-free-first-routing",
        task_class="economy-routing",
        failure="Cloud work can consume paid finalizer capacity before cheaper evidence lanes are exhausted.",
        correction=(
            "For Bossman 1.5 batch work, run the three free evidence workers and the free verifier first; "
            "offer the paid finalizer only after a failed free verification and an explicit remaining budget."
        ),
        evidence_source=str(report_path),
        expected="all bulk/verifier calls cost zero and paid finalizer calls <= 1",
        actual=f"free_clean={free_clean}; paid_calls={paid_calls}",
        verified=free_clean and paid_calls <= 1,
    ))
    saved.append(save_and_verify(
        book,
        attempt="v15-proof-before-done",
        task_class="verification-workflow",
        failure="A model can report DONE while repository behavior still fails.",
        correction=(
            "After any model-authored repair, run the deterministic targeted test suite and use that result, "
            "not the model's completion text, as the release decision."
        ),
        evidence_source=str(report_path),
        expected="repo_tests_final.pass=true",
        actual=f"repo_tests_final.pass={tests_pass}",
        verified=tests_pass,
    ))

    out = {
        "ok": True,
        "learning_dir": str(book.data_dir),
        "saved": [{"task_id": x.get("task_id"), "learning_status": x.get("learning_status"),
                   "version": x.get("version")} for x in saved],
        "trading_candidates_imported": 0,
        "note": "YouTube strategy candidates remain in trading quarantine; weights unchanged.",
    }
    print(json.dumps(out, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
