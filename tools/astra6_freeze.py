"""Bind Windows acceptance evidence to an exact release candidate; never sign it.

Writing the report and permitting a release are two different acts:

* by default this command always emits a manifest, on incomplete acceptance
  too, and exits 0 when the manifest was written — ``status`` carries the
  release state (``FROZEN`` / ``OWNER_REQUIRED`` / ``BLOCKED``);
* with ``--require-frozen`` it is the publish gate: exit 0 only for FROZEN.

The contract it enforces is the one the installed acceptance really runs
(``tools/acceptance_registry.json``, OA-02 of the 17 September 2026 audit):

* ``results.xml`` holds every registry module with at least its declared
  number of clean cases and reaches the registry floor — thirteen cases,
  forty repetitions of one case, or a suite missing one module are refused;
* ``live-model.json`` holds exactly ``models × cases`` from the registry: two
  distinct models, each with every registry case once, every row PASS with
  restart persistence PASS. Six copies of one row, a missing case, a foreign
  model or an unknown case are refused. A ``task_id`` may repeat across models
  (two isolated databases number from 1) but never within one model;
* every report — the JUnit ``<properties>`` included — names the same
  ``source_sha``, ``archive_sha256``, ``run_id`` and ``harness_sha``; the
  archive digest is the one ``bundle-acceptance.json`` measured on the ZIP.
  An expected SHA typed into any old file is not a binding;
* a report's ``status`` must agree with its content: PASS beside a failed
  row, a review-class click count, or an evening verdict that is not PASS
  is a contradiction, not evidence;
* the archive was built from locked inputs (OA-03): ``bundle-acceptance.json``
  carries ``details.build_inputs_locked`` read from the archive's own
  MANIFEST, and an unlocked build is blocked from FROZEN.
"""
from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
import re
import sys
import xml.etree.ElementTree as ET

sys.path.insert(0, str(Path(__file__).resolve().parent))
import acceptance_registry  # noqa: E402

SHA = re.compile(r"[0-9a-f]{40}\Z")
DIGEST = re.compile(r"[0-9a-f]{64}\Z")
RUN_ID = re.compile(r"[A-Za-z0-9_.:-]{1,64}\Z")
REPORTS = ("bundle-acceptance.json", "ui-sweep.json", "live-model.json", "results.xml")
STATUSES = ("PASS", "FAIL", "OWNER_REQUIRED", "REVIEW_REQUIRED", "NOT_RUN", "SKIPPED", "PARTIAL")
CONTRACT_VERSION = 2


def _unique_object(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValueError("duplicate JSON field")
        result[key] = value
    return result


def _properties(root: ET.Element) -> dict:
    found: dict = {}
    for prop in root.iter("property"):
        name = prop.get("name")
        if isinstance(name, str) and name not in found:
            found[name] = prop.get("value")
    return found


def _junit(root: ET.Element, registry: dict) -> tuple[dict, list[str]]:
    """Counts and the reasons the suite is not the profile."""
    cases = [root] if root.tag == "testcase" else root.findall(".//testcase")
    skipped = sum(case.find("skipped") is not None for case in cases)
    failed = sum(case.find("failure") is not None or case.find("error") is not None for case in cases)
    identities = [(case.get("classname", ""), case.get("name")) for case in cases]
    problems: list[str] = []
    if not cases or any(not name for _, name in identities):
        problems.append("tests_not_all_passed")
    elif skipped or failed or root.findall(".//error") or root.findall(".//failure"):
        problems.append("tests_not_all_passed")
    if len(set(identities)) != len(identities):
        problems.append("repeated_testcases")
    clean = [case for case in cases
             if all(case.find(tag) is None for tag in ("failure", "error", "skipped"))]
    per_module: dict = {}
    for case in clean:
        module = acceptance_registry.module_of(case.get("classname", ""))
        per_module[module] = per_module.get(module, 0) + 1
    modules = registry["junit"]["modules"]
    if any(per_module.get(module, 0) < minimum for module, minimum in modules.items()):
        problems.append("profile_modules_incomplete")
    counted = sum(per_module.get(module, 0) for module in modules)
    if counted < registry["junit"]["minimum_tests"]:
        problems.append("below_profile_minimum")
    record = {"tests": len(cases), "skipped": skipped, "failed": failed,
              "profile_cases": counted, "status": "PASS" if not problems else "FAIL"}
    return record, problems


def _live_tasks(report: dict, models, source_sha: str, registry: dict) -> list[str]:
    """Why the live rows are not the exact ``models × cases`` set."""
    live = registry["live_model"]
    cases = list(live["cases"])
    problems: list[str] = []
    if not (isinstance(models, list) and len(models) == live["models"]
            and all(isinstance(model, str) and model for model in models)
            and len(set(models)) == len(models)):
        return ["installed_model_and_restart_proof_incomplete"]
    tasks = report.get("tasks")
    if not isinstance(tasks, list):
        return ["installed_model_and_restart_proof_incomplete"]
    seen: dict = {}
    ids: set = set()
    rows_ok = True
    for task in tasks:
        if not isinstance(task, dict):
            rows_ok = False
            continue
        if task.get("status") != "PASS" or task.get("restart_persistence") != "PASS":
            problems.append("status_inconsistent")
            rows_ok = False
        if task.get("source_sha") != source_sha or task.get("model") not in models \
                or task.get("case") not in cases:
            rows_ok = False
            continue
        key = (task["model"], task["case"])
        seen[key] = seen.get(key, 0) + 1
        task_id = task.get("task_id")
        if task_id is not None:
            if (task["model"], task_id) in ids:
                rows_ok = False
            ids.add((task["model"], task_id))
    expected = {(model, case) for model in models for case in cases}
    if not rows_ok or set(seen) != expected or any(count != 1 for count in seen.values()) \
            or len(tasks) != len(expected):
        problems.append("installed_model_and_restart_proof_incomplete")
    return sorted(set(problems))


def _binding_of(payload: dict | None) -> dict:
    binding = payload if isinstance(payload, dict) else {}
    return {field: binding.get(field) for field in ("source_sha", "archive_sha256", "run_id", "harness_sha")}


def build_manifest(evidence_dir: Path, source_sha: str, job_statuses: list[str],
                   registry: dict | None = None) -> dict:
    registry = registry or acceptance_registry.load()
    blocked: list[str] = []
    owner_required: list[str] = []
    evidence: dict = {}
    bindings: dict = {}
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
                counts, problems = _junit(root, registry)
                record.update(counts)
                blocked.extend(f"{name}:{problem}" for problem in problems)
                bindings[name] = _binding_of(_properties(root))
                continue
            report = json.loads(raw, object_pairs_hook=_unique_object)
            if not isinstance(report, dict):
                raise ValueError("not an object")
            sha_key = "expected_sha" if name == "bundle-acceptance.json" else "source_sha"
            if report.get(sha_key) != source_sha:
                blocked.append(f"{name}:source_sha_mismatch")
            else:
                record["source_sha"] = source_sha
            bindings[name] = _binding_of(report.get("binding"))
            status = report.get("status")
            record["status"] = status if status in STATUSES else "INVALID"
            if status != "PASS":
                (owner_required if status == "OWNER_REQUIRED" else blocked).append(f"{name}:not_passed")
            if name == "live-model.json" and status == "PASS":
                identity = report.get("identity")
                if not isinstance(identity, dict) or identity.get("source_sha") != source_sha:
                    blocked.append(f"{name}:installed_model_and_restart_proof_incomplete")
                blocked.extend(f"{name}:{problem}"
                               for problem in _live_tasks(report, report.get("models"), source_sha, registry))
            if name == "ui-sweep.json" and status == "PASS":
                counts = report.get("counts")
                review = registry.get("ui_sweep", {}).get("review_verdicts", [])
                if not isinstance(counts, dict) or any(counts.get(verdict) for verdict in review):
                    blocked.append(f"{name}:status_inconsistent")
            if name == "bundle-acceptance.json":
                details = report.get("details")
                details = details if isinstance(details, dict) else {}
                digest = details.get("archive_sha256")
                if not isinstance(digest, str) or not DIGEST.fullmatch(digest):
                    blocked.append(f"{name}:archive_sha256_invalid")
                else:
                    archive_sha256 = digest
                if report.get("problems"):
                    blocked.append(f"{name}:reported_problems")
                verdict = details.get("evening_verdict")
                expected_code = {"PASS": 0, "OWNER_REQUIRED": 2}.get(status)
                if status in ("PASS", "OWNER_REQUIRED") and (
                        verdict != status or details.get("evening_returncode") != expected_code):
                    blocked.append(f"{name}:status_inconsistent")
                if status == "PASS" and details.get("evening_negative_control") != "PASS":
                    blocked.append(f"{name}:negative_control_not_passed")
                if status in ("PASS", "OWNER_REQUIRED") and details.get("build_inputs_locked") is not True:
                    blocked.append(f"{name}:build_inputs_not_locked")
        except (OSError, ValueError, TypeError, ET.ParseError):
            blocked.append(f"{name}:malformed")
    payload_binding = _bind(bindings, source_sha, archive_sha256, blocked)
    status = "BLOCKED" if blocked else "OWNER_REQUIRED" if owner_required else "FROZEN"
    return {
        "schema_version": 1,
        "contract_version": CONTRACT_VERSION,
        "release_label": "Астра 6",
        "label_is_cryptographic_signature": False,
        "source_sha": source_sha if SHA.fullmatch(source_sha) else None,
        "status": status,
        "scope": "WINDOWS_RELEASE_CANDIDATE",
        "acceptance_registry": {"profile": registry.get("profile"), "sha256": acceptance_registry.digest(),
                                "minimum_tests": registry["junit"]["minimum_tests"],
                                "modules": len(registry["junit"]["modules"]),
                                "live_rows": registry["live_model"]["models"] * len(registry["live_model"]["cases"])},
        "intelligence_preservation": "NOT_VERIFIED_BY_THIS_MANIFEST",
        "target_hardware_acceptance": "OWNER_REQUIRED",
        "model_weights_trained": False,
        "archive_sha256": archive_sha256,
        "archive_digest_origin": "bundle-acceptance.json; archive bytes are not rehashed by this aggregator",
        "payload_binding": payload_binding,
        "jobs": [status if status in ("success", "failure", "cancelled", "skipped") else "unknown" for status in job_statuses],
        "evidence": evidence,
        "blockers": blocked,
        "owner_required": owner_required,
        "publish_gate": {
            "release_ready": status == "FROZEN",
            "rule": "FROZEN only; a written manifest is a report, never a release permission",
        },
    }


def _bind(bindings: dict, source_sha: str, archive_sha256, blocked: list[str]) -> dict:
    """Every present report must name the same payload, run and harness."""
    run_ids = set()
    for name, binding in bindings.items():
        problems = []
        if binding["source_sha"] != source_sha or binding["harness_sha"] != source_sha:
            problems.append("not_bound_to_payload")
        digest = binding["archive_sha256"]
        if not isinstance(digest, str) or not DIGEST.fullmatch(digest) or digest != archive_sha256:
            problems.append("not_bound_to_payload")
        run_id = binding["run_id"]
        if not isinstance(run_id, str) or not RUN_ID.fullmatch(run_id):
            problems.append("not_bound_to_payload")
        else:
            run_ids.add(run_id)
        for problem in sorted(set(problems)):
            blocked.append(f"{name}:{problem}")
    if len(run_ids) > 1:
        blocked.append("run_id_mismatch")
    run_id = next(iter(run_ids)) if len(run_ids) == 1 else None
    return {"archive_sha256": archive_sha256, "run_id": run_id,
            "harness_sha": source_sha if bindings and all(
                b["harness_sha"] == source_sha for b in bindings.values()) else None,
            "reports_bound": sorted(name for name, b in bindings.items()
                                    if f"{name}:not_bound_to_payload" not in blocked)}


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--evidence-dir", type=Path, required=True)
    parser.add_argument("--source-sha", required=True)
    parser.add_argument("--out", type=Path, required=True)
    parser.add_argument("--job-status", action="append", default=[])
    parser.add_argument("--require-frozen", action="store_true",
                        help="publish gate: exit 0 only when the status is FROZEN")
    args = parser.parse_args(argv)
    report = build_manifest(args.evidence_dir, args.source_sha, args.job_status)
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(f"BOSSMAN_ASTRA6_FREEZE={report['status']}")
    ready = report["publish_gate"]["release_ready"]
    print(f"BOSSMAN_ASTRA6_RELEASE_READY={'YES' if ready else 'NO'}")
    if args.require_frozen and not ready:
        for blocker in report["blockers"]:
            print(f"  blocker: {blocker}")
        for item in report["owner_required"]:
            print(f"  owner_required: {item}")
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
