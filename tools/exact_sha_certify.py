#!/usr/bin/env python3
"""Exact-SHA release certification: green means green ON THIS COMMIT.

The scorecard field `exact_sha_ci` used to be a hand-typed string with no
verification path. This tool makes the claim falsifiable. It reads GitHub
Actions workflow runs (a saved JSON page or a live fetch) and certifies a SHA
only when EVERY required workflow has a completed, successful run whose
`head_sha` is exactly that SHA, with a complete, nonempty list of successful
jobs bound to that run and attempt.

Saved runs must include `jobs` and `jobs_evidence` with `run_id`, `run_attempt`,
`head_sha`, `total_count`, and `complete: true`. --fetch builds this envelope
from every page of the official run-attempt jobs endpoint. Raw workflow-run
pages alone are insufficient. Saved JSON is caller-supplied evidence, not an
authenticated attestation. Run/job metadata does not prove the code actually
checked out, an artifact's source identity, or the contents of executed tests;
those remain separate release checks.

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
    # BL-089: целевая платформа владельца — Windows, и до этой правки НИ ОДНО
    # из двух заданий Windows не было обязательным. Сертификат выдавался SHA,
    # на котором продукт владельца не собирался вовсе: отсутствующий прогон не
    # окрашен никак и читался как «замечаний нет». Совпадение идёт по
    # ОТОБРАЖАЕМОМУ имени задания (поле `name` в ответе API), поэтому строки
    # ниже обязаны совпадать с `name:` в самих файлах заданий — это закреплено
    # тестом, иначе переименование задания тихо выключило бы требование.
    "One-download Windows application",
    "Windows owner run — light, medium and super-long task",
    "Windows 100 real checks",
    # Табло владельческих сценариев. Готовность продукта определяют ОНИ, а не
    # регрессионные наборы, — и до этой строки сертификат мог быть выдан SHA,
    # на котором табло не считалось вовсе. Отсутствующий прогон не окрашен
    # никак и читается как «замечаний нет»: ровно та ловушка, что и в BL-089.
    # Задание поднимается фильтром путей и `workflow_dispatch`, как оба
    # Windows-задания, поэтому порядок выпуска для кандидата тот же.
    "Owner scenarios (integrated, not unit tests)",
)

CERTIFIED = "CERTIFIED"
NOT_CERTIFIED = "NOT_CERTIFIED"
INSUFFICIENT = "INSUFFICIENT_EVIDENCE"
EXIT = {CERTIFIED: 0, NOT_CERTIFIED: 1, INSUFFICIENT: 2}
_SHA_RE = re.compile(r"^[0-9a-f]{40}$")
EVIDENCE_LIMITATIONS = [
    "Run/job metadata does not verify checkout contents, artifact source identity, or test contents.",
    "Saved JSON is caller-supplied evidence, not an authenticated GitHub attestation.",
]


class CertificationError(ValueError):
    pass


def _runs_from_payload(payload: Any) -> list[dict]:
    """Accept the raw `/actions/runs` page ({"workflow_runs": [...]}), a bare list,
    or a concatenation of pages ([{"workflow_runs": [...]}, ...])."""
    if isinstance(payload, dict):
        pages = [payload]
    elif isinstance(payload, list):
        if not all(isinstance(item, dict) for item in payload):
            raise CertificationError('malformed saved run inventory')
        if not any('workflow_runs' in item for item in payload):
            return payload  # caller-supplied inventory, without API completeness attestation
        pages = payload
    else:
        raise CertificationError("unrecognised runs payload")
    out: list[dict] = []
    declared = any('total_count' in page for page in pages)
    total = None
    for page in pages:
        chunk = page.get('workflow_runs')
        if not isinstance(chunk, list) or not all(isinstance(run, dict) for run in chunk):
            raise CertificationError('payload has no valid workflow_runs list')
        if declared:
            count = page.get('total_count')
            if type(count) is not int or count < 0:
                raise CertificationError('saved run pagination has no valid total_count')
            if total is not None and total != count:
                raise CertificationError('saved run pagination total_count changed')
            total = count
        out.extend(chunk)
    if declared:
        if len(out) != total:
            raise CertificationError('saved run pagination incomplete: total_count does not match inventory')
        ids = [run.get('id') for run in out]
        if not all(_positive_int(value) for value in ids) or len(set(ids)) != len(ids):
            raise CertificationError('saved run pagination contains invalid or duplicate ids')
    return out


def _order_key(run: dict) -> tuple[int, int, int]:
    def _int(v: Any) -> int:
        try:
            return int(v)
        except (TypeError, ValueError):
            return 0
    return (_int(run.get("run_number")), _int(run.get("run_attempt")), _int(run.get("id")))


def _positive_int(value: Any) -> bool:
    return type(value) is int and value > 0


def _validate_run_order(run: dict) -> None:
    for key in ('id', 'run_number', 'run_attempt'):
        if not _positive_int(run.get(key)):
            raise CertificationError(f'relevant workflow run requires a positive integer {key} for latest-run selection')


def _validate_jobs(run: dict, sha: str) -> list[dict]:
    """Check completeness and identity before interpreting any job's result."""
    run_id, attempt = run.get('id'), run.get('run_attempt')
    if not _positive_int(run_id) or not _positive_int(attempt):
        raise CertificationError('job evidence requires positive integer run id and run_attempt')
    jobs, evidence = run.get('jobs'), run.get('jobs_evidence')
    if not isinstance(jobs, list) or not jobs:
        raise CertificationError('job evidence missing or empty: zero jobs is not execution evidence')
    if not isinstance(evidence, dict) or evidence.get('complete') is not True:
        raise CertificationError('complete job pagination evidence is missing')
    for key, expected in (('run_id', run_id), ('run_attempt', attempt), ('head_sha', sha)):
        actual = evidence.get(key)
        if actual != expected or (key != 'head_sha' and not _positive_int(actual)):
            raise CertificationError(f'jobs_evidence.{key} does not match the selected run/attempt/SHA')
    total = evidence.get('total_count')
    if not _positive_int(total) or total != len(jobs):
        raise CertificationError('job pagination total_count does not match the complete nonempty job list')
    seen = set()
    for job in jobs:
        if not isinstance(job, dict) or not _positive_int(job.get('id')):
            raise CertificationError('job evidence has a malformed job or invalid job id')
        if job['id'] in seen:
            raise CertificationError('job evidence contains duplicate job ids')
        seen.add(job['id'])
        if not _positive_int(job.get('run_id')) or job['run_id'] != run_id:
            raise CertificationError(f"job {job['id']} run_id does not match the selected run")
        if job.get('head_sha') != sha:
            raise CertificationError(f"job {job['id']} head_sha does not match the requested SHA")
        # Some API responses omit run_attempt; the requested attempt endpoint
        # and its envelope bind it. If present, the job field must agree too.
        if 'run_attempt' in job and (not _positive_int(job['run_attempt']) or job['run_attempt'] != attempt):
            raise CertificationError(f"job {job['id']} run_attempt does not match the selected attempt")
    return jobs


def certify(sha: str, runs: list[dict], required: tuple[str, ...] | list[str] = DEFAULT_REQUIRED) -> dict:
    sha = str(sha or "").strip().lower()
    if not _SHA_RE.match(sha):
        raise CertificationError("sha must be a full 40-hex commit id (abbreviations cannot be certified)")
    if not required:
        raise CertificationError("no required workflows: an empty requirement set certifies nothing")
    if not runs:
        return {"sha": sha, "verdict": INSUFFICIENT, "final": False, "required": list(required),
                "workflows": {}, "ignored_other_sha": 0,
                "reason": "no workflow runs available for this SHA (empty page or fetch failure)",
                "limitations": list(EVIDENCE_LIMITATIONS)}

    on_sha: dict[str, dict] = {}
    malformed: dict[str, str] = {}
    identities: set[tuple[str, int, int]] = set()
    ignored = 0
    for run in runs:
        name = str(run.get("name") or "")
        if str(run.get("head_sha") or "").lower() != sha:
            ignored += 1                      # OLD_SHA_PASS != CURRENT_SHA_PASS
            continue
        if name in required:
            try:
                _validate_run_order(run)
            except CertificationError as exc:
                malformed[name] = str(exc)
                continue
            identity = (name, run['id'], run['run_attempt'])
            if identity in identities:
                malformed[name] = 'duplicate run/attempt identity: saved inventory is ambiguous'
                continue
            identities.add(identity)
        if name not in on_sha or _order_key(run) > _order_key(on_sha[name]):
            on_sha[name] = run                # a re-run supersedes earlier attempts

    workflows: dict[str, dict] = {}
    final = True
    all_pass = True
    for name in required:
        if name in malformed:
            workflows[name] = {'result': 'INSUFFICIENT_RUN_EVIDENCE', 'reason': malformed[name]}
            all_pass = False
            continue
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
            try:
                jobs = _validate_jobs(run, sha)
            except CertificationError as exc:
                entry.update(result="INSUFFICIENT_JOB_EVIDENCE", reason=str(exc))
                all_pass = False
            else:
                entry['job_count'] = len(jobs)
                entry['jobs'] = [{key: job.get(key) for key in
                                  ('id', 'name', 'run_id', 'head_sha', 'run_attempt', 'status', 'conclusion')}
                                 for job in jobs]
                unfinished = [j for j in jobs if j.get('status') != 'completed']
                failed_jobs = [j for j in jobs if j.get('conclusion') != 'success']
                if unfinished:
                    entry.update(result="NOT_FINAL", reason="unfinished jobs: " + ', '.join(
                        f"{j['id']}={j.get('status') or 'unknown'}" for j in unfinished))
                    final = False
                    all_pass = False
                elif failed_jobs:
                    entry.update(result="NOT_PASS", reason="non-success jobs: " + ', '.join(
                        f"{j['id']}={j.get('conclusion') or 'none'}" for j in failed_jobs))
                    all_pass = False
                else:
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
            "limitations": list(EVIDENCE_LIMITATIONS),
            "reason": "all required workflows and their complete nonempty job lists completed successfully on this exact SHA" if all_pass
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


def _fetch_collection(repo: str, endpoint: str, key: str, token: str | None, *,
                      filters: dict, per_page: int, max_pages: int) -> list[dict]:
    """Follow numeric pages on the fixed API host; partial pages never become proof."""
    items: list[dict] = []
    seen: set[int] = set()
    total = None
    for page in range(1, max_pages + 1):
        query = urllib.parse.urlencode({**filters, "per_page": per_page, "page": page})
        req = urllib.request.Request(f"https://api.github.com/repos/{repo}/{endpoint}?{query}",
                                     headers={"Accept": "application/vnd.github+json",
                                              "X-GitHub-Api-Version": "2022-11-28",
                                              **({"Authorization": f"Bearer {token}"} if token else {})})
        try:
            with urllib.request.urlopen(req, timeout=30) as resp:  # noqa: S310 — fixed https host
                payload = json.load(resp)
        except (urllib.error.URLError, urllib.error.HTTPError, TimeoutError, ValueError) as exc:
            raise CertificationError(f"{key} fetch failed on page {page}: {exc}") from exc
        if not isinstance(payload, dict) or type(payload.get('total_count')) is not int or payload['total_count'] < 0:
            raise CertificationError(f'{key} page {page} has no valid total_count')
        if total is None:
            total = payload['total_count']
        elif payload['total_count'] != total:
            raise CertificationError(f'{key} pagination total_count changed while fetching')
        chunk = payload.get(key)
        if not isinstance(chunk, list) or len(chunk) > per_page:
            raise CertificationError(f'{key} page {page} has no valid {key} list')
        for item in chunk:
            if not isinstance(item, dict) or not _positive_int(item.get('id')):
                raise CertificationError(f'{key} page {page} has a malformed item or invalid id')
            if item['id'] in seen:
                raise CertificationError(f'{key} pagination contains duplicate ids')
            seen.add(item['id'])
        items.extend(chunk)
        if len(items) > total:
            raise CertificationError(f'{key} pagination exceeded total_count')
        if len(items) == total:
            return items
        if len(chunk) < per_page:
            raise CertificationError(f'{key} pagination ended before total_count was reached')
    raise CertificationError(f'{key} pagination incomplete after {max_pages} pages')


def fetch_runs(repo: str, sha: str, token: str | None, *, per_page: int = 100, max_pages: int = 10,
               required: tuple[str, ...] | list[str] = DEFAULT_REQUIRED) -> list[dict]:
    if not re.fullmatch(r"[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+", repo or ""):
        raise CertificationError("--repo must be owner/name")
    if not _SHA_RE.fullmatch(sha):
        raise CertificationError('fetch requires a full lowercase 40-hex SHA')
    if not _positive_int(per_page) or per_page > 100 or not _positive_int(max_pages):
        raise CertificationError('pagination requires per_page in 1..100 and positive max_pages')
    runs = _fetch_collection(repo, 'actions/runs', 'workflow_runs', token,
                             filters={'head_sha': sha}, per_page=per_page, max_pages=max_pages)
    latest: dict[str, dict] = {}
    for run in runs:
        if run.get('head_sha') != sha:
            raise CertificationError('fetched workflow run head_sha does not match the requested SHA')
        name = run.get('name')
        if not isinstance(name, str) or not name:
            raise CertificationError('fetched workflow run has no name')
        if name in required:
            _validate_run_order(run)
        if name not in latest or _order_key(run) > _order_key(latest[name]):
            latest[name] = run
    for run in latest.values():
        # Non-required workflows (including this certification workflow) may
        # still be queued. Only a selected required success needs job proof;
        # certify() already rejects unfinished and non-success required runs.
        if (run['name'] not in required or run.get('status') != 'completed'
                or run.get('conclusion') != 'success'):
            continue
        run_id, attempt = run.get('id'), run.get('run_attempt')
        if not _positive_int(run_id) or not _positive_int(attempt):
            raise CertificationError('fetched workflow run requires positive id and run_attempt')
        jobs = _fetch_collection(repo, f'actions/runs/{run_id}/attempts/{attempt}/jobs', 'jobs', token,
                                filters={}, per_page=per_page, max_pages=max_pages)
        run['jobs'] = jobs
        run['jobs_evidence'] = {'run_id': run_id, 'run_attempt': attempt, 'head_sha': sha,
                                'total_count': len(jobs), 'complete': True}
        _validate_jobs(run, sha)
    return runs


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--sha", required=True)
    src = ap.add_mutually_exclusive_group(required=True)
    src.add_argument("--runs-json", help="saved run page(s) enriched with complete jobs and jobs_evidence")
    src.add_argument("--fetch", action="store_true", help="fetch runs and complete attempt jobs from api.github.com (GITHUB_TOKEN)")
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
            runs = fetch_runs(args.repo, args.sha.strip().lower(), os.environ.get("GITHUB_TOKEN"),
                              required=required)
        report = certify(args.sha, runs, required)
        contradiction = ""
        if args.scorecard:
            with open(args.scorecard, encoding="utf-8") as fh:
                contradiction = check_scorecard(report, json.load(fh))
            report["scorecard_contradiction"] = contradiction
    except (CertificationError, OSError, json.JSONDecodeError) as exc:
        report = {"sha": args.sha, "verdict": INSUFFICIENT, "final": False, "reason": str(exc),
                  "limitations": list(EVIDENCE_LIMITATIONS)}
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
