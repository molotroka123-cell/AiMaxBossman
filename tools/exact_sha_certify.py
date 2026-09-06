#!/usr/bin/env python3
"""Exact-SHA release certification: green means green ON THIS COMMIT.

The scorecard field `exact_sha_ci` used to be a hand-typed string with no
verification path. This tool makes the claim falsifiable. It reads GitHub
Actions workflow runs (a saved JSON page or a live fetch) and certifies a SHA
only when EVERY required workflow has a completed, successful run whose
`head_sha` is exactly that SHA.

Rules (release certification, handoff pack 07):
  * OLD_SHA_PASS != CURRENT_SHA_PASS — runs on any other commit are ignored,
    however green they were.
  * SKIPPED != PASS, CANCELLED != PASS — only conclusion == "success" passes;
    skipped, cancelled, neutral, timed_out, stale, action_required,
    startup_failure and failure are all NOT PASS, each reported by name.
  * IN_PROGRESS != PASS — a run that has not completed makes the SHA NOT_FINAL.
  * a required workflow with no run on the SHA is MISSING, never assumed.
  * no data at all (empty page, fetch failure) is INSUFFICIENT_EVIDENCE, not PASS.
  * a re-run supersedes earlier attempts of the same workflow on the same SHA
    (latest run_number / run_attempt wins); it never inherits another SHA.

Exit codes: 0 CERTIFIED, 1 NOT_CERTIFIED (or a scorecard claim contradicted),
2 INSUFFICIENT_EVIDENCE.

Usage:
  python tools/exact_sha_certify.py --sha <40hex> --runs-json runs.json [--output report.json]
  GITHUB_TOKEN=… python tools/exact_sha_certify.py --sha <40hex> --fetch --repo owner/name
  python tools/exact_sha_certify.py --sha <40hex> --runs-json runs.json \\
      --scorecard docs/benchmark/current-scorecard.json   # PASS claim must be certified
"""
from __future__ import annotations

import argparse
import json
import os
import re
import sys
import urllib.error
import urllib.parse
import urllib.request
from typing import Any

# Workflows whose green run on the exact SHA constitutes release CI evidence —
# the six the scorecard's `exact_sha_ci` row is about. Names must match `name:`
# in .github/workflows/*.yml. The Intelligence Preservation gate is a separate,
# SHA-bound axis (tools/intelligence_preservation_gate.py --expect-sha) and is
# not folded into CI certification; pass it via --required to include it.
DEFAULT_REQUIRED = (
    "root-ci (shared contracts, learning layer, tools)",
    "Bossman Core CI",
    "Command Center CI",
    "Bossman V2 Auto-Repair",
    "ASTRA acceptance",
    "Solana safety gates",
    # The only gate that installs real FFmpeg and actually executes the
    # renderer, the Fleet TLS RPC and the execution-truth regressions. A
    # release SHA whose media path was never executed is not certified.
    "Fable media and Fleet acceptance",
)

CERTIFIED = "CERTIFIED"
NOT_CERTIFIED = "NOT_CERTIFIED"
INSUFFICIENT = "INSUFFICIENT_EVIDENCE"
EXIT = {CERTIFIED: 0, NOT_CERTIFIED: 1, INSUFFICIENT: 2}
_SHA_RE = re.compile(r"^[0-9a-f]{40}$")


class CertificationError(ValueError):
    pass


def _runs_from_payload(payload: Any) -> list[dict]:
    """Accept the raw `/actions/runs` page ({"workflow_runs": [...]}), a bare list,
    or a concatenation of pages ([{"workflow_runs": [...]}, ...])."""
    if isinstance(payload, dict):
        runs = payload.get("workflow_runs")
        if not isinstance(runs, list):
            raise CertificationError("payload has no workflow_runs list")
        return [r for r in runs if isinstance(r, dict)]
    if isinstance(payload, list):
        out: list[dict] = []
        for item in payload:
            if isinstance(item, dict) and "workflow_runs" in item:
                out.extend(_runs_from_payload(item))
            elif isinstance(item, dict):
                out.append(item)
        return out
    raise CertificationError("unrecognised runs payload")


def _order_key(run: dict) -> tuple[int, int, int]:
    def _int(v: Any) -> int:
        try:
            return int(v)
        except (TypeError, ValueError):
            return 0
    return (_int(run.get("run_number")), _int(run.get("run_attempt")), _int(run.get("id")))


def certify(sha: str, runs: list[dict], required: tuple[str, ...] | list[str] = DEFAULT_REQUIRED) -> dict:
    sha = str(sha or "").strip().lower()
    if not _SHA_RE.match(sha):
        raise CertificationError("sha must be a full 40-hex commit id (abbreviations cannot be certified)")
    if not required:
        raise CertificationError("no required workflows: an empty requirement set certifies nothing")
    if not runs:
        return {"sha": sha, "verdict": INSUFFICIENT, "final": False, "required": list(required),
                "workflows": {}, "ignored_other_sha": 0,
                "reason": "no workflow runs available for this SHA (empty page or fetch failure)"}

    on_sha: dict[str, dict] = {}
    ignored = 0
    for run in runs:
        name = str(run.get("name") or "")
        if str(run.get("head_sha") or "").lower() != sha:
            ignored += 1                      # OLD_SHA_PASS != CURRENT_SHA_PASS
            continue
        if name not in on_sha or _order_key(run) > _order_key(on_sha[name]):
            on_sha[name] = run                # a re-run supersedes earlier attempts

    workflows: dict[str, dict] = {}
    final = True
    all_pass = True
    for name in required:
        run = on_sha.get(name)
        if run is None:
            workflows[name] = {"result": "MISSING", "reason": "no run on this SHA"}
            all_pass = False
            continue
        status = str(run.get("status") or "")
        conclusion = str(run.get("conclusion") or "")
        entry = {"run_id": run.get("id"), "run_number": run.get("run_number"),
                 "run_attempt": run.get("run_attempt"), "status": status, "conclusion": conclusion,
                 "html_url": run.get("html_url")}
        if status != "completed":
            entry.update(result="NOT_FINAL", reason=f"status={status or 'unknown'}: an unfinished run is not evidence")
            final = False
            all_pass = False
        elif conclusion == "success":
            entry.update(result="PASS")
        else:
            entry.update(result="NOT_PASS",
                         reason=f"conclusion={conclusion or 'none'}: only success passes "
                                "(skipped/cancelled/neutral/timed_out/stale/failure do not)")
            all_pass = False
        workflows[name] = entry

    verdict = CERTIFIED if all_pass else NOT_CERTIFIED
    failed = [n for n, w in workflows.items() if w["result"] != "PASS"]
    return {"sha": sha, "verdict": verdict, "final": final, "required": list(required),
            "workflows": workflows, "ignored_other_sha": ignored,
            "reason": "all required workflows completed successfully on this exact SHA" if all_pass
            else "not certified: " + ", ".join(f"{n}={workflows[n]['result']}" for n in failed)}


def check_scorecard(report: dict, scorecard: dict) -> str:
    """A scorecard may claim exact_sha_ci=PASS only for a SHA this tool certified.
    Returns "" when consistent, else the contradiction."""
    claim = str(scorecard.get("exact_sha_ci") or "UNPROVEN")
    evidence_sha = str(scorecard.get("last_evidence_sha") or "").lower()
    if claim != "PASS":
        return ""
    if evidence_sha != report["sha"]:
        return (f"scorecard claims exact_sha_ci=PASS for last_evidence_sha={evidence_sha or 'none'} "
                f"but certification was run for {report['sha']}: OLD_SHA_PASS != CURRENT_SHA_PASS")
    if report["verdict"] != CERTIFIED:
        return f"scorecard claims exact_sha_ci=PASS but {report['sha']} is {report['verdict']}: {report['reason']}"
    return ""


def fetch_runs(repo: str, sha: str, token: str | None, *, per_page: int = 100, max_pages: int = 10) -> list[dict]:
    if not re.match(r"^[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+$", repo or ""):
        raise CertificationError("--repo must be owner/name")
    runs: list[dict] = []
    for page in range(1, max_pages + 1):
        query = urllib.parse.urlencode({"head_sha": sha, "per_page": per_page, "page": page})
        req = urllib.request.Request(f"https://api.github.com/repos/{repo}/actions/runs?{query}",
                                     headers={"Accept": "application/vnd.github+json",
                                              "X-GitHub-Api-Version": "2022-11-28",
                                              **({"Authorization": f"Bearer {token}"} if token else {})})
        try:
            with urllib.request.urlopen(req, timeout=30) as resp:  # noqa: S310 — fixed https host
                payload = json.load(resp)
        except (urllib.error.URLError, urllib.error.HTTPError, TimeoutError, ValueError) as exc:
            raise CertificationError(f"fetch failed on page {page}: {exc}") from exc
        chunk = _runs_from_payload(payload)
        runs.extend(chunk)
        if len(chunk) < per_page:
            break
    return runs


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--sha", required=True)
    src = ap.add_mutually_exclusive_group(required=True)
    src.add_argument("--runs-json", help="saved /repos/{o}/{r}/actions/runs?head_sha=… page(s)")
    src.add_argument("--fetch", action="store_true", help="fetch runs from api.github.com (GITHUB_TOKEN)")
    ap.add_argument("--repo", default=os.environ.get("GITHUB_REPOSITORY", ""))
    ap.add_argument("--required", nargs="*", default=None, help="override the required workflow names")
    ap.add_argument("--scorecard", help="cross-check a scorecard's exact_sha_ci claim")
    ap.add_argument("--output", help="write the JSON report here")
    args = ap.parse_args(argv)
    required = tuple(args.required) if args.required else DEFAULT_REQUIRED
    try:
        if args.runs_json:
            with open(args.runs_json, encoding="utf-8") as fh:
                runs = _runs_from_payload(json.load(fh))
        else:
            runs = fetch_runs(args.repo, args.sha.strip().lower(), os.environ.get("GITHUB_TOKEN"))
        report = certify(args.sha, runs, required)
        contradiction = ""
        if args.scorecard:
            with open(args.scorecard, encoding="utf-8") as fh:
                contradiction = check_scorecard(report, json.load(fh))
            report["scorecard_contradiction"] = contradiction
    except (CertificationError, OSError, json.JSONDecodeError) as exc:
        report = {"sha": args.sha, "verdict": INSUFFICIENT, "final": False, "reason": str(exc)}
        contradiction = ""
    if args.output:
        with open(args.output, "w", encoding="utf-8") as fh:
            json.dump(report, fh, ensure_ascii=False, indent=2, sort_keys=True)
    print(f"EXACT_SHA_CI={report['verdict']} sha={report['sha']} final={report.get('final')}")
    for name, entry in (report.get("workflows") or {}).items():
        print(f"  {entry['result']:<10} {name}: {entry.get('reason') or entry.get('conclusion')}")
    if report.get("ignored_other_sha"):
        print(f"  ignored {report['ignored_other_sha']} run(s) on other commits (old green does not transfer)")
    if contradiction:
        print("SCORECARD_CONTRADICTION: " + contradiction)
        return 1
    return EXIT[report["verdict"]]


if __name__ == "__main__":
    sys.exit(main())
