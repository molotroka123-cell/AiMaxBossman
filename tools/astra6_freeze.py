"""Bind Windows acceptance evidence to an exact release candidate; never sign it.

This command always emits a report on incomplete acceptance. Its exit code only
reports whether manifest creation succeeded: inspect ``status`` for release state.
"""
from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
import re
import xml.etree.ElementTree as ET

SHA = re.compile(r"[0-9a-f]{40}\Z")
DIGEST = re.compile(r"[0-9a-f]{64}\Z")
REPORTS = ("bundle-acceptance.json", "ui-sweep.json", "live-model.json", "results.xml")


def _unique_object(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValueError("duplicate JSON field")
        result[key] = value
    return result


def build_manifest(evidence_dir: Path, source_sha: str, job_statuses: list[str]) -> dict:
    blocked: list[str] = []
    owner_required: list[str] = []
    evidence = {}
    archive_sha256 = None
    if not SHA.fullmatch(source_sha):
        blocked.append("source_sha_invalid")
    if len(job_statuses) < 2 or any(status != "success" for status in job_statuses):
        blocked.append("required_jobs_not_successful")
    for name in REPORTS:
        matches = list(evidence_dir.rglob(name)) if evidence_dir.is_dir() else []
        if len(matches) != 1:
            reason = "missing" if not matches else "duplicate"
            (owner_required if name == "live-model.json" and not matches else blocked).append(f"{name}:{reason}")
            continue
        path = matches[0]
        try:
            if path.is_symlink() or not path.is_file():
                raise ValueError("not a regular report")
            raw = path.read_bytes()
            # Only hashes and validated enums escape this function: no logs,
            # provider payloads, local paths, prompts, tokens or arbitrary strings.
            record = {"sha256": hashlib.sha256(raw).hexdigest()}
            evidence[name] = record
            if name.endswith(".xml"):
                root = ET.fromstring(raw)
                cases = root.findall(".//testcase")
                if root.tag == "testcase":
                    cases = [root]
                skipped = sum(case.find("skipped") is not None for case in cases)
                failed = sum(case.find("failure") is not None or case.find("error") is not None for case in cases)
                unique_cases = {(case.get("classname", ""), case.get("name")) for case in cases}
                passed = len(unique_cases) == len(cases) and all(case.get("name") for case in cases) and len(cases) >= 13 and not skipped and not failed and not root.findall(".//error") and not root.findall(".//failure")
                record.update(status="PASS" if passed else "FAIL", tests=len(cases), skipped=skipped, failed=failed)
                if not passed:
                    blocked.append(f"{name}:tests_not_all_passed")
                continue
            report = json.loads(raw, object_pairs_hook=_unique_object)
            if not isinstance(report, dict):
                raise ValueError("not an object")
            sha_key = "expected_sha" if name == "bundle-acceptance.json" else "source_sha"
            if report.get(sha_key) != source_sha:
                blocked.append(f"{name}:source_sha_mismatch")
            else:
                record["source_sha"] = source_sha
            status = report.get("status")
            record["status"] = status if status in ("PASS", "FAIL", "OWNER_REQUIRED", "REVIEW_REQUIRED", "NOT_RUN", "SKIPPED") else "INVALID"
            if status != "PASS":
                (owner_required if status == "OWNER_REQUIRED" else blocked).append(f"{name}:not_passed")
            if name == "live-model.json" and status == "PASS":
                identity = report.get("identity")
                tasks = report.get("tasks")
                models = report.get("models")
                valid_models = isinstance(models, list) and len(models) == 2 and all(isinstance(model, str) for model in models) and len(set(models)) == 2
                valid_tasks = (isinstance(tasks, list) and len(tasks) == 6
                               and all(isinstance(task, dict) and task.get("status") == "PASS"
                                       and task.get("restart_persistence") == "PASS"
                                       and task.get("source_sha") == source_sha
                                       and task.get("model") in models
                                       for task in tasks)) if valid_models else False
                if not isinstance(identity, dict) or identity.get("source_sha") != source_sha or not valid_tasks:
                    blocked.append(f"{name}:installed_model_and_restart_proof_incomplete")
            if name == "bundle-acceptance.json":
                details = report.get("details")
                digest = details.get("archive_sha256") if isinstance(details, dict) else None
                if not isinstance(digest, str) or not DIGEST.fullmatch(digest):
                    blocked.append(f"{name}:archive_sha256_invalid")
                else:
                    archive_sha256 = digest
                if report.get("problems"):
                    blocked.append(f"{name}:reported_problems")
        except (OSError, ValueError, TypeError, ET.ParseError):
            blocked.append(f"{name}:malformed")
    return {
        "schema_version": 1,
        "release_label": "Астра 6",
        "label_is_cryptographic_signature": False,
        "source_sha": source_sha if SHA.fullmatch(source_sha) else None,
        "status": "BLOCKED" if blocked else "OWNER_REQUIRED" if owner_required else "FROZEN",
        "scope": "WINDOWS_RELEASE_CANDIDATE",
        "intelligence_preservation": "NOT_VERIFIED_BY_THIS_MANIFEST",
        "target_hardware_acceptance": "OWNER_REQUIRED",
        "model_weights_trained": False,
        "archive_sha256": archive_sha256,
        "archive_digest_origin": "bundle-acceptance.json; archive bytes are not rehashed by this aggregator",
        "jobs": [status if status in ("success", "failure", "cancelled", "skipped") else "unknown" for status in job_statuses],
        "evidence": evidence,
        "blockers": blocked,
        "owner_required": owner_required,
    }


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--evidence-dir", type=Path, required=True)
    parser.add_argument("--source-sha", required=True)
    parser.add_argument("--out", type=Path, required=True)
    parser.add_argument("--job-status", action="append", default=[])
    args = parser.parse_args(argv)
    report = build_manifest(args.evidence_dir, args.source_sha, args.job_status)
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(f"BOSSMAN_ASTRA6_FREEZE={report['status']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
