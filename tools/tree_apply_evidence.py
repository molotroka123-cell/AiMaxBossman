#!/usr/bin/env python3
"""Turn capability-tree leaves green ('reported') ONLY from deterministic receipts.

Receipts live in docs/architecture/bossman-tree-20261005/evidence/<lane>.json (JSON list).
A receipt is accepted only if every check in validate_receipt() passes; model claims,
owner notes and scanner output never reach this code path.

Receipt fields: node_id, sha, probe, command, exit_code, started_at, finished_at,
output_sha256, output_tail (<=1500 chars, scrubbed), verdict, model (optional),
kind in {import, pytest, live_call, ui}. The full output must be stored at
evidence/out/<node_id>.txt and its sha256 must equal output_sha256.

A 'recorded' leaf (an OSS/reference entry, NOT a claim of installation) may be promoted to
'reported' ONLY by a receipt of kind 'integration' (PASS, exit 0, hash ok, sha ancestor of HEAD)
that also names a non-empty 'integration_code' list of repo-relative paths that exist in the
repo: the Bossman code that uses the project. No other kind may touch a 'recorded' leaf, and
'integration' is not a kind for code/branch leaves.
"""
from __future__ import annotations

import argparse
import collections
import hashlib
import json
import re
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
EVID = ROOT / "docs" / "architecture" / "bossman-tree-20261005" / "evidence"
SEED = ROOT / "command-center" / "bcc" / "capability_tree_seed.json"
KINDS = {"import", "pytest", "live_call", "ui", "audit"}
INSTALLED_KINDS = {"installed_pytest", "installed_import"}
INSTALLED_PREFIX = "installed-"
BUILDS_FILE = "installed-builds.json"
ELIGIBLE = {"code", "branch"}
INTEGRATION_KIND = "integration"
RECORDED = "recorded"
REQUIRED = ("node_id", "sha", "probe", "command", "exit_code", "started_at",
            "finished_at", "output_sha256", "output_tail", "verdict", "kind")
_SECRET = re.compile(r"(sk-[A-Za-z0-9_-]{16,}|gh[pousr]_[A-Za-z0-9]{20,}|AIza[0-9A-Za-z_-]{30,}|"
                     r"xox[abp]-[A-Za-z0-9-]{10,}|(?i:bearer)\s+[A-Za-z0-9._-]{20,}|"
                     r"(?i:(api[_-]?key|token|secret|password)\s*[=:]\s*)\S{8,})")

STALE = {
    "Семантика и live работоспособность не сертифицированы.": "{p}; полная семантика и сквозная живая работа не сертифицированы.",
    "Авторизация/live эффект не проверены.": "{p}.",
    "Код в этой сборке; живая работа не сертифицирована.": "Код в этой сборке; {p}, живая работа не сертифицирована.",
}


def proof_phrase(rc: dict) -> str:
    if rc.get("kind") == "live_call":
        return "Проверено вживую на указанном коммите"
    return "Импорт и существующие тесты прошли на указанном коммите"


def refresh_detail(detail: str, rc: dict) -> str:
    """Replace boilerplate 'not verified' sentences that the receipt has just made untrue."""
    for old, new in STALE.items():
        detail = detail.replace(old, new.format(p=proof_phrase(rc)))
    return detail



def scrub(text: str) -> str:
    """Helper for lanes: redact obvious secrets before storing output_tail."""
    return _SECRET.sub("[REDACTED]", text)


def sha256_file(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def is_ancestor(repo: Path, sha: str) -> bool:
    if not isinstance(sha, str) or not re.fullmatch(r"[0-9a-f]{7,40}", sha):
        return False
    r = subprocess.run(["git", "-C", str(repo), "merge-base", "--is-ancestor", sha, "HEAD"],
                       capture_output=True, text=True)
    return r.returncode == 0


def zone_of(nodes: dict, nid: str) -> str:
    """Top-level zone = the ancestor directly below the root."""
    chain, cur = [], nid
    while cur in nodes and cur not in chain:
        chain.append(cur)
        cur = nodes[cur].get("parent")
    return chain[-2] if len(chain) >= 2 else nid


def load_builds(evid: Path) -> set:
    """Full shas of installed builds that receipts may claim (installed-builds.json)."""
    try:
        data = json.loads((evid / BUILDS_FILE).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return set()
    if not isinstance(data, list):
        return set()
    return {e["sha"].lower() for e in data if isinstance(e, dict) and isinstance(e.get("sha"), str)
            and re.fullmatch(r"[0-9a-fA-F]{40}", e["sha"])}


def validate_installed(rc, nodes: dict, parents: set, evid: Path, repo: Path, lane: str, builds: set) -> str | None:
    """'working' = leaf was green AND its tests passed against the installed build's code."""
    if not isinstance(rc, dict):
        return "not an object"
    if not lane.startswith(INSTALLED_PREFIX) or lane == BUILDS_FILE:
        return "installed receipt outside installed-*.json"
    for k in REQUIRED:
        if k not in rc:
            return f"missing {k}"
    if not isinstance(rc["kind"], str) or rc["kind"] not in INSTALLED_KINDS:
        return "bad installed kind"
    nid = rc["node_id"]
    if not isinstance(nid, str) or nid not in nodes:
        return "unknown node"
    if not re.fullmatch(r"[A-Za-z0-9_.:/-]+", nid) or ".." in nid:
        return "bad node id"
    if nid in parents:
        return "not a leaf"
    if nodes[nid].get("status") != "reported":
        return f"status {nodes[nid].get('status')} is not reported (green first)"
    if rc["verdict"] != "PASS":
        return "verdict not PASS"
    if isinstance(rc["exit_code"], bool) or rc["exit_code"] != 0:
        return "exit_code != 0"
    if not isinstance(rc["command"], str) or not rc["command"].strip():
        return "empty command"
    if not isinstance(rc["probe"], str) or not rc["probe"].strip():
        return "empty probe"
    if not isinstance(rc["output_tail"], str) or len(rc["output_tail"]) > 1500:
        return "output_tail too long"
    if not isinstance(rc["sha"], str) or rc["sha"].lower() not in builds:
        return "sha is not a registered installed build"
    out = evid / "out" / f"installed-{nid.replace('/', '_')}.txt"
    if not out.is_file():
        return "output file missing"
    if sha256_file(out) != rc["output_sha256"]:
        return "output hash mismatch"
    if not is_ancestor(repo, rc["sha"]):
        return "sha not ancestor of HEAD"
    return None


def check_integration_code(paths, repo: Path) -> str | None:
    """Non-empty list of repo-relative paths, each existing inside the repo."""
    if not isinstance(paths, list) or not paths:
        return "integration_code must be a non-empty list of repo paths"
    root = repo.resolve()
    for p in paths:
        if not isinstance(p, str) or not p.strip():
            return "integration_code entry is not a path"
        norm = p.replace("\\", "/")
        if norm.startswith("/") or re.match(r"^[A-Za-z]:", norm) or ".." in norm.split("/"):
            return f"integration_code path not repo-relative: {p}"
        full = (root / norm).resolve()
        if root not in full.parents or not full.exists():
            return f"integration_code path does not exist in the repo: {p}"
    return None


def validate_receipt(rc, nodes: dict, parents: set, evid: Path, repo: Path) -> str | None:
    """Return None if valid, else a rejection reason."""
    if not isinstance(rc, dict):
        return "not an object"
    for k in REQUIRED:
        if k not in rc:
            return f"missing {k}"
    nid = rc["node_id"]
    if not isinstance(nid, str) or nid not in nodes:
        return "unknown node"
    if not re.fullmatch(r"[A-Za-z0-9_.:/-]+", nid) or ".." in nid:
        return "bad node id"
    if nid in parents:
        return "not a leaf"
    status = nodes[nid].get("status")
    if status == RECORDED:
        if rc["verdict"] == "RETIRE" and rc["kind"] == "audit":
            pass  # a reference that provably no longer exists (e.g. repo 404) may fall off; never turns green
        elif rc["kind"] != INTEGRATION_KIND:
            return "status recorded: only an integration receipt may promote a reference leaf"
        elif rc["verdict"] != "PASS":
            return "status recorded: integration receipt must be PASS"
    elif status not in ELIGIBLE:
        return f"status {status} not eligible"
    elif rc["kind"] == INTEGRATION_KIND:
        return "integration receipts are only for recorded leaves"
    if rc["verdict"] not in ("PASS", "RETIRE"):
        return "verdict not PASS"
    if rc["verdict"] == "RETIRE" and (rc["kind"] != "audit" or len(str(rc.get("reason", "")).strip()) < 20):
        return "retire needs kind=audit and a reason"
    if rc["verdict"] == "PASS" and rc["kind"] == "audit":
        return "audit receipts cannot turn green"
    if isinstance(rc["exit_code"], bool) or rc["exit_code"] != 0:
        return "exit_code != 0"
    if not isinstance(rc["command"], str) or not rc["command"].strip():
        return "empty command"
    if not isinstance(rc["kind"], str) or rc["kind"] not in KINDS | {INTEGRATION_KIND}:
        return "bad kind"
    if not isinstance(rc["probe"], str) or not rc["probe"].strip():
        return "empty probe"
    if not isinstance(rc["output_tail"], str) or len(rc["output_tail"]) > 1500:
        return "output_tail too long"
    if rc.get("model") is not None and not isinstance(rc.get("model"), str):
        return "bad model"
    if rc["kind"] == INTEGRATION_KIND:
        bad = check_integration_code(rc.get("integration_code"), repo)
        if bad:
            return bad
    out = evid / "out" / f"{nid.replace('/', '_')}.txt"
    if not out.is_file():
        return "output file missing"
    if sha256_file(out) != rc["output_sha256"]:
        return "output hash mismatch"
    if not is_ancestor(repo, rc["sha"]):
        return "sha not ancestor of HEAD"
    return None


def load_receipts(evid: Path):
    # installed-*.json last: a leaf must be green (possibly in this very run) before it can be 'working'.
    files = sorted(f for f in evid.glob("*.json") if f.name not in ("tree.export.json", BUILDS_FILE))
    files = [f for f in files if not f.name.startswith(INSTALLED_PREFIX)] +             [f for f in files if f.name.startswith(INSTALLED_PREFIX)]
    for f in files:
        try:
            data = json.loads(f.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            data = None
        if not isinstance(data, list):
            yield f.name, None
            continue
        for rc in data:
            yield f.name, rc


def apply(seed_path: Path = SEED, evid: Path = EVID, repo: Path = ROOT, write: bool = True) -> dict:
    seed = json.loads(seed_path.read_text(encoding="utf-8"))
    nodes = {n["id"]: n for n in seed["nodes"]}
    parents = {n["parent"] for n in seed["nodes"] if n.get("parent")}
    accepted, rejected = [], []
    builds = load_builds(evid)
    for lane, rc in load_receipts(evid):
        if rc is None:
            rejected.append((lane, None, "unreadable lane file"))
            continue
        installed = lane.startswith(INSTALLED_PREFIX) or (isinstance(rc, dict) and isinstance(rc.get("kind"), str) and rc["kind"] in INSTALLED_KINDS)
        if installed:
            reason = validate_installed(rc, nodes, parents, evid, repo, lane, builds)
        else:
            reason = validate_receipt(rc, nodes, parents, evid, repo)
        if reason:
            rejected.append((lane, rc.get("node_id") if isinstance(rc, dict) else None, reason))
            continue
        node = nodes[rc["node_id"]]
        date = str(rc["finished_at"])[:10]
        if installed:
            node["status"] = "working"
            node["detail"] = (refresh_detail(node.get("detail", ""), rc)
                              + f" Работает в установленном Bossman @ {rc['sha'][:8]}: {rc['probe']}").strip()
        elif rc["verdict"] == "RETIRE":
            node["status"] = "retired"
            node["detail"] = (node.get("detail", "") + f" Выбыл {date} @ {rc['sha'][:8]}: {rc['reason']}").strip()
        else:
            node["status"] = "reported"
            node["detail"] = (refresh_detail(node.get("detail", ""), rc) + f" Прогон {date} @ {rc['sha'][:8]}: {rc['probe']}").strip()
        ref = f"docs/architecture/bossman-tree-20261005/evidence/{lane}#{rc['node_id']}"
        node.setdefault("sources", []).append(
            {"path": ref, "branch": "green/tree-leaves-20261006", "sha": rc["sha"], "kind": "receipt"})
        accepted.append((lane, rc["node_id"], zone_of(nodes, rc["node_id"])))
    if write:
        seed_path.write_text(json.dumps(seed, ensure_ascii=False, indent=2) + "\n", encoding="utf-8", newline="\n")
        export(seed, evid)
        summary(seed, evid, accepted, rejected)
    return {"seed": seed, "accepted": accepted, "rejected": rejected,
            "zones": collections.Counter(z for _, _, z in accepted)}


def export(seed: dict, evid: Path) -> None:
    nodes = [{"id": n["id"], "label": n["label"], "parent": n.get("parent"), "status": n["status"],
              "short": (n.get("detail") or "")[:160]} for n in seed["nodes"] if n["status"] != "retired"]
    evid.mkdir(parents=True, exist_ok=True)
    (evid / "tree.export.json").write_text(
        json.dumps({"schema": 1, "as_of": seed.get("as_of"), "nodes": nodes}, ensure_ascii=False, indent=1) + "\n",
        encoding="utf-8", newline="\n")


def summary(seed: dict, evid: Path, accepted, rejected) -> None:
    st = collections.Counter(n["status"] for n in seed["nodes"])
    zc = collections.Counter(z for _, _, z in accepted)
    lines = ["# Evidence summary", "", "Статусы дерева: " + ", ".join(f"{k}={v}" for k, v in sorted(st.items())), "",
             f"Принято расписок: {len(accepted)}; отклонено: {len(rejected)}", "", "## По зонам", ""]
    lines += [f"- {z}: {c}" for z, c in sorted(zc.items())] or ["- нет"]
    lines += ["", "## Принятые", ""] + [f"- `{n}` ({lane})" for lane, n, _ in accepted]
    lines += ["", "## Отклонённые", ""] + [f"- `{n}` ({lane}): {r}" for lane, n, r in rejected]
    (evid / "SUMMARY.md").write_text("\n".join(lines) + "\n", encoding="utf-8", newline="\n")


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="Apply deterministic evidence receipts to the capability tree seed")
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--seed", type=Path, default=SEED)
    ap.add_argument("--evidence", type=Path, default=EVID)
    ap.add_argument("--repo", type=Path, default=ROOT)
    a = ap.parse_args(argv)
    r = apply(a.seed, a.evidence, a.repo, write=not a.dry_run)
    print(f"{'DRY-RUN ' if a.dry_run else ''}accepted={len(r['accepted'])} rejected={len(r['rejected'])}")
    for z, c in sorted(r["zones"].items()):
        print(f"  zone {z}: +{c}")
    for lane, nid, why in r["rejected"]:
        print(f"  REJECT {lane} {nid}: {why}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
