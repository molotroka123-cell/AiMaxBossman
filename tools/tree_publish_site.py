#!/usr/bin/env python3
"""One command: regenerate the public capability-tree site data from origin/main -> test -> (optionally, owner-approved) push.

  python tools/tree_publish_site.py                 # DRY (default): builds in a temp copy, prints a diff summary, writes nothing else
  BOSSMAN_OWNER_APPROVED_PUBLISH=1 python tools/tree_publish_site.py --push   # commit + push the site (owner approval required)

Why this exists (18.10): a scheduled task published from an old worktree (HEAD 10.10, a stale branch), so the public site showed
a stale source SHA, a non-existent branch and 13 zones while main's seed had 15. Hard rules, all enforced here:

* the source checkout must be CLEAN and its HEAD must equal `git ls-remote origin refs/heads/main` exactly, otherwise REFUSED;
* meta is stamped with the real SHA, the branch `main` (verified), the zone count computed from the seed, and CI / live-check
  status `not_run` unless a receipt file named <full-sha>.json whose `sha` equals that exact HEAD exists (never claims green);
* seed <-> registry <-> meta must agree (ids, statuses, zones, node counts) or the run FAILS loudly;
* dry-run is the default and writes only to a temp directory; `--push` additionally needs BOSSMAN_OWNER_APPROVED_PUBLISH=1;
* a push is appended to a JSONL publish log.

The Bossman repo itself is never written, committed, checked out or pushed. No evidence is applied to the seed (the committed
seed at the verified SHA is the truth; `tree_apply_evidence --dry-run` is only run to report rejected receipts).
"""
from __future__ import annotations

import argparse
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
DEFAULT_SITE = Path(r"C:\Users\asd\Bossman\site-tree-publisher")
DEFAULT_RECEIPTS = Path(r"C:\Users\asd\Bossman\tree-publish-receipts")
GENERATED = ("data/", "ai/", "llms.txt")
SEED_REL = "command-center/bcc/capability_tree_seed.json"
EVID_REL = "docs/architecture/bossman-tree-20261005/evidence"
APPROVAL_ENV = "BOSSMAN_OWNER_APPROVED_PUBLISH"
FULL_SHA = re.compile(r"[0-9a-f]{40}")
TRAILERS = ("Co-Authored-By: Claude Sonnet 5.5 <noreply@anthropic.com>\n"
            "Claude-Session: https://claude.ai/code/session_01YJiEovhsj8qVPoqyNwAbTd")


class Refused(Exception):
    pass


def _run(cmd, cwd=None, check=False):
    env = dict(os.environ, PYTHONIOENCODING="utf-8", PYTHONDONTWRITEBYTECODE="1")
    r = subprocess.run(cmd, cwd=cwd, capture_output=True, text=True, encoding="utf-8", errors="replace", env=env)
    if check and r.returncode:
        raise Refused(f"{' '.join(map(str, cmd[:4]))} failed ({r.returncode}): {(r.stderr or r.stdout)[-400:]}")
    return r


def _git(repo: Path, *a):
    return _run(["git", "-C", str(repo), *a])


def unrelated_changes(site: Path) -> list[str]:
    """Uncommitted paths in the site clone that are not generated data (bytecode caches ignored)."""
    r = _git(site, "status", "--porcelain", "--untracked-files=all")
    if r.returncode:
        raise Refused(f"not a git repo: {site}")
    out = []
    for line in r.stdout.splitlines():
        path = line[3:].strip().strip('"')
        if " -> " in path:
            path = path.split(" -> ")[-1]
        if "__pycache__" in path or path.endswith(".pyc"):
            continue
        if not path.startswith(GENERATED):
            out.append(path)
    return out


def run_apply_check(source: Path, python: str) -> subprocess.CompletedProcess:
    """tree_apply_evidence in --dry-run: validates receipts and reports rejects; writes nothing."""
    return _run([python, str(Path(__file__).with_name("tree_apply_evidence.py")), "--dry-run",
                 "--seed", str(source / SEED_REL), "--evidence", str(source / EVID_REL), "--repo", str(source)])


def run_sync(work: Path, source: Path, registry: Path, python: str) -> subprocess.CompletedProcess:
    return _run([python, str(work / "scripts" / "sync_bossman.py"), "--source", str(source),
                 "--registry", str(registry), "--out", str(work)], cwd=work)


def run_site_tests(site: Path, python: str) -> subprocess.CompletedProcess:
    return _run([python, "-m", "unittest", "discover", "-s", "tests", "-p", "test_*.py"], cwd=site)


def data_summary(site: Path) -> dict:
    try:
        meta = json.loads((site / "data" / "meta.json").read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {"by_status": {}, "head": None, "zones": None}
    return {"by_status": meta.get("counts", {}).get("by_status", {}), "head": (meta.get("source") or {}).get("head"),
            "zones": meta.get("counts", {}).get("zones")}


# ---------------------------------------------------------------- freshness gate (never publish from a stale snapshot)
def remote_main_sha(source: Path) -> str:
    """Exact SHA of refs/heads/main on origin (ls-remote; read-only)."""
    r = _git(source, "ls-remote", "origin", "refs/heads/main")
    line = r.stdout.strip().splitlines()[0] if r.returncode == 0 and r.stdout.strip() else ""
    sha = line.split()[0] if line else ""
    if not FULL_SHA.fullmatch(sha):
        raise Refused(f"cannot resolve origin refs/heads/main (rc={r.returncode}): {(r.stderr or r.stdout).strip()[:200]}")
    return sha


def assert_fresh_clean(source: Path) -> str:
    """Refuse unless `source` is a clean checkout whose HEAD is exactly origin/main. Returns that full SHA."""
    if _git(source, "rev-parse", "--show-toplevel").returncode:
        raise Refused(f"source is not a git checkout: {source}")
    head = _git(source, "rev-parse", "HEAD").stdout.strip()
    if not FULL_SHA.fullmatch(head):
        raise Refused("source has no HEAD commit")
    dirty = [ln for ln in _git(source, "status", "--porcelain", "--untracked-files=all").stdout.splitlines()
             if "__pycache__" not in ln and not ln.endswith(".pyc")]
    if dirty:
        raise Refused(f"source tree is not clean ({len(dirty)} paths, e.g. " + "; ".join(x.strip() for x in dirty[:5]) + ")")
    remote = remote_main_sha(source)
    if head != remote:
        raise Refused(f"source HEAD {head[:12]} is not origin/main {remote[:12]}: refusing to publish a stale or foreign snapshot")
    return head


# ---------------------------------------------------------------- seed / registry / meta consistency
def seed_facts(source: Path) -> dict:
    doc = json.loads((source / SEED_REL).read_text(encoding="utf-8"))
    retired = {n["id"] for n in doc["nodes"] if n.get("status") == "retired"}
    nodes = [n for n in doc["nodes"] if n.get("status") != "retired"]        # the site drops retired nodes the same way
    parents = {n.get("parent") for n in nodes if n.get("parent")}
    zones = sorted(n["id"] for n in nodes if n.get("parent") == "bossman")
    leaf_ids = {n["id"] for n in nodes if n["id"] not in parents}
    by_status: dict[str, int] = {}
    for n in nodes:
        by_status[n["status"]] = by_status.get(n["status"], 0) + 1
    return {"nodes": len(nodes), "zones": zones, "leaves": len(leaf_ids), "leaf_ids": leaf_ids,
            "status": {n["id"]: n["status"] for n in nodes}, "by_status": by_status,
            "retired": retired, "source_sha": doc.get("source_sha"), "selected_branch": doc.get("selected_branch")}


def check_registry(source: Path, registry: Path, seed: dict, head: str) -> tuple[list[str], list[str]]:
    """(errors, warnings). Errors block publication. The registry cannot carry the SHA of the commit that contains it,
    so its `head` must be an ancestor of (or equal to) the source HEAD; a lag is only a warning."""
    errors: list[str] = []
    warnings: list[str] = []
    try:
        reg = json.loads(registry.read_text(encoding="utf-8"))
        rows = {r["id"]: r for r in reg["leaves"]}
    except (OSError, ValueError, KeyError, TypeError) as exc:
        return [f"registry unreadable: {registry.name}: {type(exc).__name__}"], []
    unknown = sorted(set(rows) - set(seed["status"]) - seed["retired"])
    if unknown:
        errors.append(f"registry has {len(unknown)} rows that are not seed nodes, e.g. {unknown[:5]}")
    dead = sorted(set(rows) & seed["retired"])
    if dead:
        warnings.append(f"registry still carries {len(dead)} rows of retired seed nodes (ignored by the site)")
    stale = sorted(i for i, r in rows.items() if i in seed["status"] and r.get("integration_status") != seed["status"][i])
    if stale:
        errors.append(f"registry status differs from seed for {len(stale)} rows, e.g. {stale[:5]}")
    missing = sorted(seed["leaf_ids"] - set(rows))
    if missing:
        errors.append(f"{len(missing)} live seed leaves have no registry row, e.g. {missing[:5]}")
    reg_zones = sorted(i for i, r in rows.items() if r.get("zone_parent") == "bossman")
    if reg_zones != seed["zones"]:
        errors.append(f"zone mismatch: seed has {len(seed['zones'])} zones, registry {len(reg_zones)} "
                      f"(only in seed: {sorted(set(seed['zones']) - set(reg_zones))}, only in registry: {sorted(set(reg_zones) - set(seed['zones']))})")
    rhead = str(reg.get("head") or "")
    if rhead != head:
        if FULL_SHA.fullmatch(rhead) and _git(source, "merge-base", "--is-ancestor", rhead, head).returncode == 0:
            warnings.append(f"registry head {rhead[:12]} lags source HEAD {head[:12]} (ancestor; regenerate the registry)")
        else:
            errors.append(f"registry head {rhead[:12] or '(none)'} is not an ancestor of source HEAD {head[:12]}")
    return errors, warnings


def read_receipt(receipts_dir: Path | None, head: str) -> dict:
    """CI / live-check status. Never green without <full-sha>.json whose `sha` equals the full source HEAD."""
    out = {"ci": {"state": "not_run", "sha": None, "run_url": None}, "live": {"state": "not_run", "sha": None}, "receipt": None}
    if receipts_dir is None:
        return out
    f = Path(receipts_dir) / f"{head}.json"
    try:
        rc = json.loads(f.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return out
    if not isinstance(rc, dict) or str(rc.get("sha") or "") != head:
        return out
    for key in ("ci", "live"):
        item = rc.get(key)
        if isinstance(item, dict) and item.get("state") in ("green", "red", "not_run", "unknown"):
            out[key] = {"state": item["state"], "sha": head, **({"run_url": item.get("run_url")} if key == "ci" else {})}
    out["receipt"] = f.name
    return out


def stamp_meta(work: Path, head: str, seed: dict, checks: dict) -> dict:
    """Overwrite what the site generator cannot know: real SHA, real branch (verified == origin/main), zones computed from the seed,
    CI/live status from receipts only."""
    mp = work / "data" / "meta.json"
    meta = json.loads(mp.read_text(encoding="utf-8"))
    src = meta.setdefault("source", {})
    src["head"], src["head_full"], src["branch"] = head[:12], head, "main"
    src["verified_origin_main"] = True
    src["map_selected_branch"] = seed.get("selected_branch")
    meta.setdefault("counts", {})["zones"] = len(seed["zones"])
    meta["checks"] = {"ci": checks["ci"], "live": checks["live"], "receipt": checks["receipt"]}
    mp.write_text(json.dumps(meta, ensure_ascii=False, separators=(",", ":")), encoding="utf-8", newline="\n")
    return meta


def check_meta(work: Path, head: str, seed: dict) -> list[str]:
    errors: list[str] = []
    meta = json.loads((work / "data" / "meta.json").read_text(encoding="utf-8"))
    c = meta.get("counts", {})
    if (meta.get("source") or {}).get("head_full") != head:
        errors.append("meta source head does not equal the verified origin/main SHA")
    if c.get("zones") != len(seed["zones"]):
        errors.append(f"meta zones {c.get('zones')} != seed zones {len(seed['zones'])}")
    if c.get("nodes") != seed["nodes"]:
        errors.append(f"meta nodes {c.get('nodes')} != seed nodes {seed['nodes']}")
    if c.get("by_status") != seed["by_status"]:
        errors.append("meta by_status differs from the seed")
    try:
        tree = json.loads((work / "data" / "tree.json").read_text(encoding="utf-8"))["nodes"]
        tz = sorted(n["id"] for n in tree if n.get("parent") == "bossman")
        if tz != seed["zones"]:
            errors.append(f"tree.json zones {len(tz)} != seed zones {len(seed['zones'])}")
    except (OSError, ValueError, KeyError):
        errors.append("tree.json unreadable")
    return errors


def diff_summary(work: Path, site: Path) -> dict:
    added, changed, same = [], [], 0
    for g in GENERATED:
        base = work / g.rstrip("/")
        files = [base] if base.is_file() else sorted(x for x in base.rglob("*") if x.is_file()) if base.is_dir() else []
        for f in files:
            rel = f.relative_to(work).as_posix()
            old = site / rel
            if not old.is_file():
                added.append(rel)
            elif old.read_bytes() != f.read_bytes():
                changed.append(rel)
            else:
                same += 1
    return {"added": added, "changed": changed, "unchanged": same}


def _append_publish_log(path: Path, result: dict) -> None:
    row = {"at": datetime.now(timezone.utc).replace(microsecond=0).isoformat(), "source_sha": result["source_sha"],
           "branch": result["branch"], "zones": result["seed_zones"], "nodes": result["seed_nodes"], "by_status": result["by_status"],
           "checks": result["checks"], "site_commit": result["committed"], "files_changed": len(result["diff"]["changed"]),
           "files_added": len(result["diff"]["added"])}
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8", newline="\n") as fh:
        fh.write(json.dumps(row, ensure_ascii=False) + "\n")


def publish(site: Path, source: Path = ROOT, registry: Path | None = None, push: bool = False,
            python: str = sys.executable, log=print, receipts_dir: Path | None = None, env=None,
            publish_log: Path | None = None) -> dict:
    """Dry-run by default: everything is built in a temp copy of the site; the site clone and the source are never written.
    push=True additionally needs BOSSMAN_OWNER_APPROVED_PUBLISH=1."""
    env = os.environ if env is None else env
    site, source = Path(site).resolve(), Path(source).resolve()
    registry = Path(registry) if registry else source / "docs" / "architecture" / "tree-registry.json"
    if not (site / ".git").exists():
        raise Refused(f"site clone not found: {site}")
    if push and env.get(APPROVAL_ENV) != "1":
        raise Refused(f"--push requires {APPROVAL_ENV}=1 (owner approval); nothing done")
    head = assert_fresh_clean(source)
    seed = seed_facts(source)
    errors, warnings = check_registry(source, registry, seed, head)
    for w in warnings:
        log("WARNING: " + w)
    if errors:
        raise Refused("seed/registry inconsistent: " + " | ".join(errors))
    if push:
        dirty = unrelated_changes(site)
        if dirty:
            raise Refused("site clone has unrelated uncommitted changes: " + ", ".join(dirty[:8]))
        branch = _git(site, "rev-parse", "--abbrev-ref", "HEAD").stdout.strip()
        if branch != "main":
            raise Refused(f"site clone is on '{branch}', not main")
        # another session also writes to the site: fast-forward to its commits, refuse on divergence
        pull = _git(site, "pull", "--ff-only", "origin", "main")
        if pull.returncode:
            raise Refused("site clone cannot fast-forward to origin/main (diverged?): " + (pull.stderr or pull.stdout).strip()[:200])
    r = run_apply_check(source, python)
    log("evidence check (informational, nothing applied): " + ((r.stdout.strip().splitlines() or [r.stderr.strip()])[0]))
    with tempfile.TemporaryDirectory(prefix="tree-publish-", ignore_cleanup_errors=True) as tmp:
        work = Path(tmp) / "site"
        shutil.copytree(site, work, ignore=shutil.ignore_patterns(".git", "__pycache__", "node_modules"))
        r = run_sync(work, source, registry, python)
        log(r.stdout.strip() or r.stderr.strip())
        if r.returncode or "SYNC=OK" not in r.stdout:
            raise Refused("site sync failed")
        checks = read_receipt(receipts_dir, head)
        stamp_meta(work, head, seed, checks)
        merrors = check_meta(work, head, seed)
        if merrors:
            raise Refused("meta inconsistent with seed: " + " | ".join(merrors))
        t = run_site_tests(work, python)
        tests_ok = t.returncode == 0
        log("site tests: PASS" if tests_ok else "site tests: FAIL\n" + (t.stderr or t.stdout)[-1500:])
        summary = data_summary(work)
        diff = diff_summary(work, site)
        result = {"tests_ok": tests_ok, "pushed": False, "committed": None, "source_sha": head, "branch": "main",
                  "seed_zones": len(seed["zones"]), "seed_nodes": seed["nodes"], "seed_leaves": seed["leaves"],
                  "checks": {k: checks[k]["state"] for k in ("ci", "live")}, "diff": diff, "warnings": warnings, **summary}
        if push:
            if not tests_ok:
                raise Refused("site tests failed; nothing committed or pushed")
            dirty = unrelated_changes(site)
            if dirty:
                raise Refused("site clone has unrelated uncommitted changes: " + ", ".join(dirty[:8]))
            for rel in diff["added"] + diff["changed"]:
                dst = site / rel
                dst.parent.mkdir(parents=True, exist_ok=True)
                shutil.copyfile(work / rel, dst)
            paths = [g.rstrip("/") for g in GENERATED if (site / g.rstrip("/")).exists()]
            if paths:
                _run(["git", "-C", str(site), "add", "--", *paths], check=True)
            if _git(site, "diff", "--cached", "--quiet").returncode:
                msg = f"data: tree snapshot @ {summary['head']}, " + ", ".join(
                    f"{k}={v}" for k, v in sorted(summary["by_status"].items())) + "\n\n" + TRAILERS
                _run(["git", "-C", str(site), "commit", "-q", "-m", msg], check=True)
            result["committed"] = _git(site, "rev-parse", "HEAD").stdout.strip()
            _run(["git", "-C", str(site), "push", "origin", "HEAD:main"], check=True)
            result["pushed"] = True
            _append_publish_log(publish_log or site.parent / "tree-publish.log.jsonl", result)
    return result


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="Regenerate the tree site data from origin/main (dry-run by default)")
    ap.add_argument("--site", type=Path, default=DEFAULT_SITE)
    ap.add_argument("--source", type=Path, default=ROOT)
    ap.add_argument("--registry", type=Path, default=None)
    ap.add_argument("--receipts", type=Path, default=DEFAULT_RECEIPTS, help="dir with <full-sha>.json CI/live receipts")
    ap.add_argument("--push", action="store_true", help=f"commit and push the site (needs {APPROVAL_ENV}=1)")
    a = ap.parse_args(argv)
    try:
        res = publish(a.site, a.source, a.registry, a.push, receipts_dir=a.receipts)
    except Refused as e:
        print(f"REFUSED: {e}")
        return 2
    d = res["diff"]
    print(f"{'PUSHED' if res['pushed'] else 'DRY (nothing written outside a temp dir, no commit, no push)'} "
          f"source={res['source_sha']} branch={res['branch']} zones={res['seed_zones']} nodes={res['seed_nodes']} "
          f"leaves={res['seed_leaves']} ci={res['checks']['ci']} live={res['checks']['live']} site_commit={res['committed']}")
    print("by_status: " + ", ".join(f"{k}={v}" for k, v in sorted(res["by_status"].items())))
    print(f"would change site: added={len(d['added'])} changed={len(d['changed'])} unchanged={d['unchanged']}")
    return 0 if res["tests_ok"] else 1


if __name__ == "__main__":
    sys.exit(main())
