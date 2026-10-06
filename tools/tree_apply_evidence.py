#!/usr/bin/env python3
"""Turn capability-tree leaves green ('reported') ONLY from deterministic receipts.

Receipts live in docs/architecture/bossman-tree-20261005/evidence/<lane>.json (JSON list).
A receipt is accepted only if every check in validate_receipt() passes; model claims,
owner notes and scanner output never reach this code path.

Receipt fields: node_id, sha, probe, command, exit_code, started_at, finished_at,
output_sha256, output_tail (<=1500 chars, scrubbed), verdict, model (optional),
kind in {import, pytest, live_call, ui}. The full output must be stored at
evidence/out/<node_id>.txt and its sha256 must equal output_sha256.
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
KINDS = {"import", "pytest", "live_call", "ui"}
ELIGIBLE = {"code", "branch"}
REQUIRED = ("node_id", "sha", "probe", "command", "exit_code", "started_at",
            "finished_at", "output_sha256", "output_tail", "verdict", "kind")
_SECRET = re.compile(r"(sk-[A-Za-z0-9_-]{16,}|gh[pousr]_[A-Za-z0-9]{20,}|AIza[0-9A-Za-z_-]{30,}|"
                     r"xox[abp]-[A-Za-z0-9-]{10,}|(?i:bearer)\s+[A-Za-z0-9._-]{20,}|"
                     r"(?i:(api[_-]?key|token|secret|password)\s*[=:]\s*)\S{8,})")


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
    if nodes[nid].get("status") not in ELIGIBLE:
        return f"status {nodes[nid].get('status')} not eligible"
    if rc["verdict"] != "PASS":
        return "verdict not PASS"
    if isinstance(rc["exit_code"], bool) or rc["exit_code"] != 0:
        return "exit_code != 0"
    if not isinstance(rc["command"], str) or not rc["command"].strip():
        return "empty command"
    if rc["kind"] not in KINDS:
        return "bad kind"
    if not isinstance(rc["probe"], str) or not rc["probe"].strip():
        return "empty probe"
    if not isinstance(rc["output_tail"], str) or len(rc["output_tail"]) > 1500:
        return "output_tail too long"
    if rc.get("model") is not None and not isinstance(rc.get("model"), str):
        return "bad model"
    out = evid / "out" / f"{nid.replace('/', '_')}.txt"
    if not out.is_file():
        return "output file missing"
    if sha256_file(out) != rc["output_sha256"]:
        return "output hash mismatch"
    if not is_ancestor(repo, rc["sha"]):
        return "sha not ancestor of HEAD"
    return None


def load_receipts(evid: Path):
    for f in sorted(evid.glob("*.json")):
        if f.name == "tree.export.json":
            continue
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
    for lane, rc in load_receipts(evid):
        if rc is None:
            rejected.append((lane, None, "unreadable lane file"))
            continue
        reason = validate_receipt(rc, nodes, parents, evid, repo)
        if reason:
            rejected.append((lane, rc.get("node_id") if isinstance(rc, dict) else None, reason))
            continue
        node = nodes[rc["node_id"]]
        date = str(rc["finished_at"])[:10]
        node["status"] = "reported"
        node["detail"] = (node.get("detail", "") + f" Прогон {date} @ {rc['sha'][:8]}: {rc['probe']}").strip()
        ref = f"docs/architecture/bossman-tree-20261005/evidence/{lane}#{rc['node_id']}"
        node.setdefault("sources", []).append(ref)
        accepted.append((lane, rc["node_id"], zone_of(nodes, rc["node_id"])))
    if write:
        seed_path.write_text(json.dumps(seed, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        export(seed, evid)
        summary(seed, evid, accepted, rejected)
    return {"seed": seed, "accepted": accepted, "rejected": rejected,
            "zones": collections.Counter(z for _, _, z in accepted)}


def export(seed: dict, evid: Path) -> None:
    nodes = [{"id": n["id"], "label": n["label"], "parent": n.get("parent"), "status": n["status"],
              "short": (n.get("detail") or "")[:160]} for n in seed["nodes"]]
    evid.mkdir(parents=True, exist_ok=True)
    (evid / "tree.export.json").write_text(
        json.dumps({"schema": 1, "as_of": seed.get("as_of"), "nodes": nodes}, ensure_ascii=False, indent=1) + "\n",
        encoding="utf-8")


def summary(seed: dict, evid: Path, accepted, rejected) -> None:
    st = collections.Counter(n["status"] for n in seed["nodes"])
    zc = collections.Counter(z for _, _, z in accepted)
    lines = ["# Evidence summary", "", "Статусы дерева: " + ", ".join(f"{k}={v}" for k, v in sorted(st.items())), "",
             f"Принято расписок: {len(accepted)}; отклонено: {len(rejected)}", "", "## По зонам", ""]
    lines += [f"- {z}: {c}" for z, c in sorted(zc.items())] or ["- нет"]
    lines += ["", "## Принятые", ""] + [f"- `{n}` ({lane})" for lane, n, _ in accepted]
    lines += ["", "## Отклонённые", ""] + [f"- `{n}` ({lane}): {r}" for lane, n, r in rejected]
    (evid / "SUMMARY.md").write_text("\n".join(lines) + "\n", encoding="utf-8")


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
