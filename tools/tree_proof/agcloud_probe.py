"""Receipts for lane agcloud (zones agents, cloud, computer): import + existing/authored pytest per module leaf.

Reuses ops_probe.probe (clean subprocess, worktree-first PYTHONPATH, asserts __file__ inside checkout).
A leaf with several source modules (agents-graph) is PASS only if every module passes.
Re-run: python tools/tree_proof/agcloud_probe.py [--only id,id] [--workers 3]
"""
from __future__ import annotations

import argparse
import hashlib
import json
import sys
from collections import Counter
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import ops_probe as o  # noqa: E402

ROOT, EVID, OUT = o.ROOT, o.EVID, o.OUT
ZONES = ("agents", "cloud", "computer")
LANE = EVID / "agcloud.json"
LIVE_NODES = {"cap-22", "mod-openrouter"}          # leaves that also carry the live free-OpenRouter run
LIVE_OUT = OUT / "_live_openrouter.txt"
LIVE_SUM = OUT / "_live_openrouter.summary.json"


def zone(nodes, n):
    while n.get("parent") and nodes[n["parent"]].get("parent"):
        n = nodes[n["parent"]]
    return n["id"]


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--only")
    ap.add_argument("--workers", type=int, default=3)
    ap.add_argument("--pytest-cap", type=int, default=120, help="seconds per pytest group (slow suites)")
    a = ap.parse_args()
    o.PYTEST_CAP = a.pytest_cap
    seed = json.loads((ROOT / "command-center/bcc/capability_tree_seed.json").read_text(encoding="utf-8"))
    by = {n["id"]: n for n in seed["nodes"]}
    parents = {n["parent"] for n in seed["nodes"] if n.get("parent")}
    leaves = [n for n in seed["nodes"] if zone(by, n) in ZONES and n["id"] not in ZONES and n["id"] not in parents
              and n["status"] in ("code", "branch")]
    if a.only:
        want = set(a.only.split(","))
        leaves = [n for n in leaves if n["id"] in want]
    sha = o.sha_head()
    # unit of work = (leaf id, module path); cache by module path
    paths = {}
    for n in leaves:
        for s in n["sources"]:
            p = s["path"] if isinstance(s, dict) else s
            if p.endswith(".py") and (ROOT / p).is_file() and o.modname(p)[0]:
                paths.setdefault(p, None)

    def run_mod(p):
        node = {"id": "_m_" + o.modname(p)[0].replace(".", "_"), "sources": [{"path": p}]}
        return p, o.probe(node, sha)

    with ThreadPoolExecutor(min(a.workers, 3)) as ex:
        for p, r in ex.map(run_mod, list(paths)):
            paths[p] = r
    recs = []
    for n in leaves:
        mods = [s["path"] if isinstance(s, dict) else s for s in n["sources"]]
        mods = [m for m in mods if paths.get(m)]
        if not mods:
            continue
        rs = [paths[m] for m in mods]
        ok = all(r["verdict"] == "PASS" for r in rs)
        text = f"node_id: {n['id']}\nmodules: {mods}\nsha: {sha}\n\n" + "\n=====\n".join(
            (o.OUT / f"{r['node_id']}.txt").read_text(encoding="utf-8") for r in rs)
        live = None
        if ok and n["id"] in LIVE_NODES and LIVE_OUT.is_file() and LIVE_SUM.is_file():
            live = json.loads(LIVE_SUM.read_text(encoding="utf-8"))
            if live.get("passed"):
                text += "\n===== LIVE (free OpenRouter, $0) =====\n" + LIVE_OUT.read_text(encoding="utf-8")
            else:
                live = None
        data = text.encode("utf-8")
        (OUT / f"{n['id']}.txt").write_bytes(data)
        bad = [r for r in rs if r["verdict"] != "PASS"]
        authored = any("test_leaf_" in r["command"] for r in rs)
        recs.append({
            "node_id": n["id"], "sha": sha,
            "probe": "import+pytest" + (" + live free OpenRouter connect/catalog/pin/probe" if live else "") + (" (incl. test_leaf_* authored_by_lane)" if authored else ""),
            "command": (" && ".join(r["command"] for r in rs)
                        + (" && python tools/tree_proof/agcloud_live_probe.py" if live else ""))[:3500],
            "exit_code": 0 if ok else (bad[0]["exit_code"] or 1),
            "started_at": min(r["started_at"] for r in rs), "finished_at": o.now(),
            "output_sha256": hashlib.sha256(data).hexdigest(), "output_tail": text[-1500:],
            "verdict": "PASS" if ok else "FAIL", "kind": "live_call" if live else "pytest",
            **({"model": live["model"]} if live else {}),
            "reason": "" if ok else ",".join(r["reason"] for r in bad),
            "modules": mods,
        })
    if a.only and LANE.exists():
        old = json.loads(LANE.read_text(encoding="utf-8"))
        ids = {r["node_id"] for r in recs}
        recs = [r for r in old if r["node_id"] not in ids] + recs
    LANE.write_text(json.dumps(recs, ensure_ascii=False, indent=1), encoding="utf-8")
    print(len(recs), Counter(r["verdict"] for r in recs), Counter(r["reason"] for r in recs if r["verdict"] == "FAIL"))


if __name__ == "__main__":
    main()
