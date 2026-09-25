#!/usr/bin/env python3
"""Compile independently verified Bossman 1.5 learning into reusable assets.

Raw worker or YouTube output is refused. Input must carry a passed unseen
transfer test and an independent verifier. Outputs are a VERIFIED lesson in the
existing LearningStore, a workflow recipe, and a local skill proposal that is
not auto-activated.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import pathlib
import re
import sys
from typing import Any

ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from learning.lessons import CoachingEpisode, LessonBook, Provenance  # noqa: E402

def utf8_console() -> None:
    # Shipped runners start with `-I`, which ignores PYTHONUTF8/PYTHONIOENCODING:
    # without this the first Cyrillic line dies with cp1252 on Windows.
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(encoding="utf-8", errors="replace")
        except (AttributeError, ValueError):
            pass


utf8_console()



SHA_RE = re.compile(r"^[0-9a-f]{40}$")


def require(record: dict[str, Any]) -> None:
    transfer = record.get("transfer") if isinstance(record.get("transfer"), dict) else {}
    if transfer.get("passed") is not True:
        raise ValueError("unseen transfer did not pass")
    verifier = str(transfer.get("verifier_principal") or "").strip()
    producer = str((record.get("provenance") or {}).get("who") or "").strip()
    if not verifier or verifier == producer:
        raise ValueError("independent verifier is required")
    if not list((record.get("provenance") or {}).get("evidence_refs") or []):
        raise ValueError("evidence_refs are required")
    if not SHA_RE.fullmatch(str(transfer.get("head_sha") or "")):
        raise ValueError("transfer head_sha must be full 40-hex")
    for key in ("attempt_id", "task_id", "project_id", "failure_observation", "correction"):
        if not str(record.get(key) or "").strip():
            raise ValueError(key + " is required")
    task_class = str(record.get("task_class") or "general").strip().lower()
    if task_class.startswith(("trading", "market", "crypto", "orderflow", "order-flow")):
        raise ValueError(
            "trading lessons must use bossman.trading_learning TradingMemory promotion gates, "
            "not the generic LearningBook compiler"
        )
    recipe = record.get("recipe")
    if not isinstance(recipe, list) or not [x for x in recipe if str(x).strip()]:
        raise ValueError("verified workflow recipe is required")
    if not str(record.get("check") or "").strip():
        raise ValueError("verified check is required")


def slug(text: str) -> str:
    base = re.sub(r"[^a-z0-9]+", "-", text.lower()).strip("-")[:48] or "lesson"
    return base + "-" + hashlib.sha256(text.encode("utf-8")).hexdigest()[:8]


def compile_record(book: LessonBook, record: dict[str, Any], out: pathlib.Path) -> dict[str, Any]:
    require(record)
    prov_raw = record["provenance"]
    transfer = record["transfer"]
    ep = CoachingEpisode(
        attempt_id=record["attempt_id"],
        task_id=record["task_id"],
        project_id=record["project_id"],
        task_class=record.get("task_class") or "general",
        failure_observation=record["failure_observation"],
        correction=record["correction"],
        source=record.get("source") or "student",
        kind=record.get("kind") or "student_fix",
        status="candidate",
        scope=record.get("scope") or "project",
        environment=transfer.get("environment") or "owner-windows",
        agent=record.get("agent") or "bossman-student",
        model=record.get("model") or "",
        root_cause=record.get("root_cause") or "",
        recipe=[str(x) for x in record["recipe"]],
        check=record["check"],
        counterexample=record.get("counterexample") or "",
        symptoms=[str(x) for x in record.get("symptoms") or []],
        provenance=Provenance(
            who=str(prov_raw["who"]),
            what=str(prov_raw.get("what") or "Bossman 1.5 verified learning"),
            evidence_refs=[str(x) for x in prov_raw.get("evidence_refs") or []],
            run_id=str(prov_raw.get("run_id") or ""),
            model=str(record.get("model") or ""),
        ),
    )
    saved = book.save(ep)
    verified = book.verify(
        ep.lesson_id,
        verifier={
            "principal_id": str(transfer["verifier_principal"]),
            "independence_class": str(transfer.get("independence_class") or "independent"),
            "model_id": str(transfer.get("verifier_model") or ""),
            "run_id": str(transfer.get("verifier_run_id") or ""),
        },
        evidence={
            "source": str(transfer.get("source") or "unseen-transfer"),
            "expected": str(transfer.get("expected") or "PASS"),
            "actual": str(transfer.get("actual") or "PASS"),
            "head_sha": str(transfer["head_sha"]),
            "environment": str(transfer.get("environment") or "owner-windows"),
        },
        statement="unseen transfer passed with independent evidence",
    )
    out.mkdir(parents=True, exist_ok=True)
    name = slug(record["correction"])
    workflow = {
        "schema": "bossman.15.verified-workflow.v1",
        "lesson_id": ep.lesson_id,
        "task_class": ep.task_class,
        "recipe": ep.recipe,
        "check": ep.check,
        "counterexample": ep.counterexample,
        "source_head_sha": transfer["head_sha"],
        "status": "VERIFIED",
        "activation": "lesson_recall_only",
    }
    with (out / "verified-workflows.jsonl").open("a", encoding="utf-8") as fh:
        fh.write(json.dumps(workflow, ensure_ascii=False) + "\n")
    skill = (
        "# Skill proposal: " + name + "\n\n"
        "Status: PROPOSED_FROM_VERIFIED_LESSON / NOT AUTO-ACTIVATED\n\n"
        "Task class: " + ep.task_class + "\n\n"
        "## Lesson\n\n" + ep.correction + "\n\n"
        "## Workflow\n\n" + "\n".join("- " + x for x in ep.recipe) + "\n\n"
        "## Verification\n\n" + ep.check + "\n\n"
        "Lesson id: " + ep.lesson_id + "\n"
        "Evidence SHA: " + str(transfer["head_sha"]) + "\n"
    )
    skill_path = out / (name + ".skill-proposal.md")
    skill_path.write_text(skill, encoding="utf-8")
    return {
        "lesson_id": ep.lesson_id,
        "learning_status": verified.get("learning_status"),
        "workflow": str(out / "verified-workflows.jsonl"),
        "skill_proposal": str(skill_path),
        "activated": False,
        "saved_version": saved.get("version"),
        "verified_version": verified.get("version"),
    }


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--input", required=True)
    ap.add_argument("--data-dir", required=True)
    ap.add_argument("--out", required=True)
    ns = ap.parse_args()
    payload = json.loads(pathlib.Path(ns.input).read_text(encoding="utf-8"))
    records = payload.get("records") if isinstance(payload, dict) else None
    if not isinstance(records, list):
        raise SystemExit("input must contain records[]")
    book = LessonBook(pathlib.Path(ns.data_dir))
    results = []
    for i, record in enumerate(records):
        try:
            results.append({"index": i, "status": "VERIFIED",
                            **compile_record(book, record, pathlib.Path(ns.out))})
        except Exception as exc:
            results.append({"index": i, "status": "REJECTED",
                            "error": type(exc).__name__ + ": " + str(exc)})
    report = {"schema": "bossman.15.learning-compile.v1", "results": results}
    pathlib.Path(ns.out).mkdir(parents=True, exist_ok=True)
    (pathlib.Path(ns.out) / "learning-compile-report.json").write_text(
        json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    print(json.dumps(report, ensure_ascii=False))
    return 0 if all(r["status"] == "VERIFIED" for r in results) else 1


if __name__ == "__main__":
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(encoding="utf-8", errors="replace")
        except (AttributeError, OSError, ValueError):
            pass
    raise SystemExit(main())
