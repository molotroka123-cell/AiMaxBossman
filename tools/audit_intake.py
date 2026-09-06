"""Development-audit catalogue, separate from Bossman runtime authority.

Indexes every reachable audit-file version in the declared window, PR discussion
and CI metadata. Contents are hashed, not executed or copied to the database.
Ingestion is not semantic review. A green workflow is not exact-checkout proof.
"""
from __future__ import annotations

import argparse
from contextlib import closing
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import re
import sqlite3
import subprocess
from typing import Any
import urllib.parse
import urllib.request

REPO = "molotroka123-cell/AiMaxBossman"
STATUSES = {"UNTRIAGED", "REPORTED_OPEN", "REPRODUCED", "FIX_PRESENT_NEEDS_RETEST",
            "ENVIRONMENT_BLOCKER", "STALE", "DUPLICATE", "VERIFIED_CLOSED"}
SCHEMA = """
CREATE TABLE IF NOT EXISTS sources (
 id TEXT PRIMARY KEY, kind TEXT NOT NULL, locator TEXT NOT NULL,
 digest TEXT NOT NULL, details TEXT NOT NULL, review_state TEXT NOT NULL DEFAULT 'UNREVIEWED');
CREATE TABLE IF NOT EXISTS sightings (
 source_id TEXT NOT NULL, scan_id TEXT NOT NULL, ref TEXT NOT NULL, code_sha TEXT NOT NULL,
 PRIMARY KEY(source_id,scan_id,ref,code_sha));
CREATE TABLE IF NOT EXISTS finding_versions (
 id TEXT NOT NULL, digest TEXT NOT NULL, root_cause_key TEXT NOT NULL,
 status TEXT NOT NULL, payload TEXT NOT NULL, PRIMARY KEY(id,digest));
CREATE TABLE IF NOT EXISTS scans (
 id TEXT PRIMARY KEY, scope TEXT NOT NULL, complete INTEGER NOT NULL, gaps TEXT NOT NULL);
"""


def canonical(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False)


def digest(value: str | bytes) -> str:
    return hashlib.sha256(value.encode() if isinstance(value, str) else value).hexdigest()


def full_sha(value: Any) -> bool:
    return isinstance(value, str) and re.fullmatch(r"[0-9a-f]{40}", value) is not None


class AuditDatabase:
    def __init__(self, path: Path):
        path.parent.mkdir(parents=True, exist_ok=True)
        self.path = path
        with closing(self.connect()) as db, db:
            db.executescript(SCHEMA)

    def connect(self):
        db = sqlite3.connect(self.path, timeout=30)
        db.execute("PRAGMA busy_timeout=30000")
        return db

    def sources(self, records):
        rows, sightings = [], []
        for kind, locator, content_digest, details, scan_id, ref, code_sha in records:
            if code_sha and not full_sha(code_sha):
                raise ValueError("full source commit SHA required, not inferred test SHA")
            sid = digest(canonical([kind, locator, content_digest]))
            rows.append((sid, kind, locator, content_digest, canonical(details)))
            sightings.append((sid, scan_id, ref, code_sha))
        with closing(self.connect()) as db, db:
            db.executemany("INSERT OR IGNORE INTO sources(id,kind,locator,digest,details) VALUES(?,?,?,?,?)", rows)
            db.executemany("INSERT OR IGNORE INTO sightings VALUES(?,?,?,?)", sightings)

    def source(self, kind, locator, content_digest, details, *, scan_id, ref="", code_sha=""):
        self.sources([(kind, locator, content_digest, details, scan_id, ref, code_sha)])
        return digest(canonical([kind, locator, content_digest]))

    def findings(self, records):
        # Validate the whole batch before any mutation. Source claims stay claims.
        rows = []
        for record in records:
            if (not isinstance(record, dict) or not record.get("id")
                    or not record.get("root_cause_key") or not record.get("source")
                    or record.get("status") not in STATUSES):
                raise ValueError("invalid finding")
            if record["status"] == "VERIFIED_CLOSED":
                if (not full_sha(record.get("verified_sha"))
                        or not record.get("test_run")
                        or record.get("reviewed_by") is None):
                    raise ValueError("closure needs explicitly reviewed, SHA-bound evidence")
            payload = canonical(record)
            rows.append((record["id"], digest(payload), record["root_cause_key"], record["status"], payload))
        with closing(self.connect()) as db, db:
            db.executemany("INSERT OR IGNORE INTO finding_versions VALUES(?,?,?,?,?)", rows)

    def finish(self, scan_id, scope, gaps):
        with closing(self.connect()) as db, db:
            db.execute("INSERT OR REPLACE INTO scans VALUES(?,?,?,?)",
                       (scan_id, canonical(scope), int(not gaps), canonical(gaps)))
            assert db.execute("PRAGMA quick_check").fetchone()[0] == "ok"

    def export(self, path):
        with closing(self.connect()) as db:
            data = {}
            for table in ("sources", "sightings", "finding_versions", "scans"):
                cursor = db.execute("SELECT * FROM " + table + " ORDER BY 1,2")
                names = [c[0] for c in cursor.description]
                data[table] = [dict(zip(names, row)) for row in cursor]
        path.write_text(canonical(data) + "\n", encoding="utf-8")
        return {key: len(value) for key, value in data.items()}


def is_audit_path(path: str) -> bool:
    p = path.lower()
    return (not p.startswith((".git/", ".github/"))
            and any(word in p for word in ("audit", "acceptance", "evidence", "scorecard",
                                           "testing/", "testing_period", "report", "junit", "qa/")))


def git(root, *args):
    return subprocess.run(["git", *args], cwd=root, check=True, capture_output=True,
                          timeout=90).stdout


def collect_git(root, db, scan_id, since, gaps, max_commits=2000):
    refs = git(root, "for-each-ref", "--format=%(refname) %(objectname)", "refs/remotes/origin").decode().splitlines()
    refs = [tuple(row.rsplit(" ", 1)) for row in refs if not row.startswith("refs/remotes/origin/HEAD ")]
    if not refs:
        refs = [("HEAD", git(root, "rev-parse", "HEAD").decode().strip())]
        gaps.append("no_remote_refs: only local HEAD indexed")
    for ref, sha in refs:
        db.source("BRANCH_HEAD", ref, sha, {"captured_sha": sha},
                  scan_id=scan_id, ref=ref, code_sha=sha)
    commits = git(root, "rev-list", "--all", "--since=" + since,
                  "--max-count=" + str(max_commits + 1)).decode().splitlines()
    if len(commits) > max_commits:
        gaps.append("commit_limit: historical audit versions remain unindexed")
    # All commit/path metadata remains visible even when a report has no audit-like name.
    for sha in commits[:max_commits]:
        try:
            names = git(root, "diff-tree", "--root", "--no-commit-id", "--name-only", "-r", "-m", "-z", sha)
            paths = sorted(set(x.decode("utf-8", "surrogateescape") for x in names.split(b"\0") if x))
            db.source("GIT_COMMIT", sha, sha, {"changed_paths": paths, "content_reviewed": False},
                      scan_id=scan_id, ref="history", code_sha=sha)
        except (OSError, UnicodeError, subprocess.SubprocessError) as exc:
            gaps.append("commit:" + sha + ":" + type(exc).__name__)
    # Tips plus intermediate commits retain files added/deleted between tips.
    targets = refs + [("history", sha) for sha in commits[:max_commits]]
    cache = {}
    batch = []
    for ref, sha in targets:
        try:
            if sha not in cache:
                entries = []
                for raw in git(root, "ls-tree", "-rz", "--full-tree", sha).split(b"\0"):
                    if not raw:
                        continue
                    header, name = raw.split(b"\t", 1)
                    mode, kind, blob = header.decode("ascii").split()
                    path = name.decode("utf-8", "surrogateescape")
                    if is_audit_path(path):
                        entries.append((path, blob, mode, kind))
                cache[sha] = entries
            for path, blob, mode, kind in cache[sha]:
                batch.append(("REPO_AUDIT_FILE", path, "git-blob:" + blob,
                              {"mode": mode, "object_kind": kind, "audited_sha": None,
                               "content_reviewed": False, "symlink_followed": False},
                              scan_id, ref, sha))
                if len(batch) >= 250:
                    db.sources(batch)
                    batch.clear()
        except (ValueError, UnicodeError, OSError, subprocess.SubprocessError) as exc:
            gaps.append("git:" + sha + ":" + type(exc).__name__)
    db.sources(batch)
    return {"branch_tips": len(refs), "history_commits": min(len(commits), max_commits)}


class NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        raise ValueError("GitHub API redirect refused; credentials never forwarded")


class GitHubRead:
    def __init__(self, token="", max_calls=800):
        self.token, self.remaining = token, max_calls
        self.opener = urllib.request.build_opener(NoRedirect())

    def get(self, suffix):
        if self.remaining <= 0:
            raise ValueError("api_call_budget_exhausted")
        self.remaining -= 1
        url = "https://api.github.com/repos/" + REPO + "/" + suffix
        headers = {"Accept": "application/vnd.github+json", "User-Agent": "Bossman-Audit-Intake"}
        if self.token:
            headers["Authorization"] = "Bearer " + self.token
        with self.opener.open(urllib.request.Request(url, headers=headers), timeout=30) as response:
            raw = response.read(8 * 1024 * 1024 + 1)
        if len(raw) > 8 * 1024 * 1024:
            raise ValueError("api_response_size_limit")
        return json.loads(raw)

    def pages(self, suffix, key=None):
        count, advertised = 0, None
        seen_ids = set()
        for page in range(1, 101):
            value = self.get(suffix + ("&" if "?" in suffix else "?") + f"per_page=100&page={page}")
            rows = value[key] if key else value
            if not isinstance(rows, list):
                raise ValueError("api_list_shape")
            if key and advertised is None:
                advertised = value.get("total_count")
            for row in rows:
                if isinstance(row, dict) and "id" in row:
                    if row["id"] in seen_ids:
                        raise ValueError("unstable_pagination_duplicate")
                    seen_ids.add(row["id"])
                count += 1
                yield row
            if len(rows) < 100:
                if isinstance(advertised, int) and count < advertised:
                    raise ValueError("api_incomplete_count")
                return
        raise ValueError("api_pagination_limit")


def collect_github(api, db, scan_id, since, gaps):
    def add(kind, locator, body, details, sha=""):
        db.source(kind, locator, digest(canonical({"content": body, "metadata": details})), details, scan_id=scan_id, code_sha=sha)

    try:
        for pr in api.pages("pulls?state=all&sort=updated&direction=desc"):
            if pr["updated_at"] < since:
                break
            number, sha = pr["number"], pr["head"]["sha"]
            add("PR", pr["html_url"], pr.get("body") or "",
                {"number": number, "state": pr["state"], "draft": pr.get("draft"),
                 "head_sha": sha, "updated_at": pr["updated_at"], "content_reviewed": False}, sha)
            for endpoint, kind in ((f"issues/{number}/comments", "PR_COMMENT"),
                                   (f"pulls/{number}/reviews", "PR_REVIEW"),
                                   (f"pulls/{number}/comments", "INLINE_REVIEW")):
                try:
                    for row in api.pages(endpoint):
                        add(kind, row.get("html_url") or f"{endpoint}/{row['id']}",
                            row.get("body") or "", {"id": row["id"], "pr": number,
                            "state": row.get("state"), "updated_at": row.get("updated_at"),
                            "content_reviewed": False}, row.get("commit_id") or "")
                except (ValueError, KeyError, OSError) as exc:
                    gaps.append(endpoint + ":" + type(exc).__name__)
    except (ValueError, KeyError, OSError) as exc:
        gaps.append("pulls:" + type(exc).__name__)
    try:
        suffix = "actions/runs?created=" + urllib.parse.quote(">=" + since, safe="")
        for run in api.pages(suffix, "workflow_runs"):
            sha = run["head_sha"]
            details = {k: run.get(k) for k in ("id", "name", "head_sha", "head_branch", "event",
                                               "status", "conclusion", "run_attempt", "updated_at")}
            details["actual_checkout_sha"] = None
            details["log_reviewed"] = False
            add("CI_RUN", run["html_url"], details, details, sha)
            if run["status"] != "completed":
                continue
            try:
                for artifact in api.pages(f"actions/runs/{run['id']}/artifacts", "artifacts"):
                    meta = {k: artifact.get(k) for k in ("id", "name", "size_in_bytes", "digest", "expired")}
                    meta["downloaded_or_reviewed"] = False
                    add("CI_ARTIFACT", f"{run['html_url']}/artifacts/{artifact['id']}", meta, meta, sha)
            except (ValueError, KeyError, OSError) as exc:
                gaps.append(f"artifacts:{run['id']}:" + type(exc).__name__)
    except (ValueError, KeyError, OSError) as exc:
        gaps.append("actions:" + type(exc).__name__)


def main():
    import os
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--repo", type=Path, default=Path("."))
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--seed", type=Path)
    parser.add_argument("--since", default="2026-09-06T00:00:00Z")
    parser.add_argument("--github", action="store_true")
    args = parser.parse_args()
    datetime.strptime(args.since, "%Y-%m-%dT%H:%M:%SZ")
    args.output.mkdir(parents=True, exist_ok=True)
    scan_id = datetime.now(timezone.utc).isoformat()
    db = AuditDatabase(args.output / "Audit.sqlite")
    gaps = []
    if args.seed:
        db.findings(json.loads(args.seed.read_text(encoding="utf-8"))["findings"])
    scope = {"since": args.since, "repository": REPO, "semantic_review_complete": False}
    try:
        scope.update(collect_git(args.repo, db, scan_id, args.since, gaps))
    except (OSError, subprocess.SubprocessError, ValueError) as exc:
        gaps.append("git_collection:" + type(exc).__name__)
    if args.github:
        collect_github(GitHubRead(os.getenv("GITHUB_TOKEN", "")), db, scan_id, args.since, gaps)
    else:
        gaps.append("github_not_requested")
    db.finish(scan_id, scope, gaps)
    counts = db.export(args.output / "Audit.json")
    report = {"scan_id": scan_id, "scope": scope, "counts": counts, "gaps": gaps,
              "status": "PARTIAL" if gaps else "INDEXED_NOT_REVIEWED",
              "runtime_authority": False}
    (args.output / "summary.json").write_text(canonical(report) + "\n", encoding="utf-8")
    print(json.dumps(report, indent=2))
    return 2 if gaps else 0


if __name__ == "__main__":
    raise SystemExit(main())
