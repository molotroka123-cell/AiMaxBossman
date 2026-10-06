"""Lane jeffa: zone 'jeff', first 67 code/branch leaves by sorted node id.
Reuses ops_probe.probe (clean-subprocess import with this checkout first + existing tests that reference the module).
Re-runnable: python tools/tree_proof/jeffa_probe.py [--only id,id]
"""
from __future__ import annotations
import argparse, importlib.util, json, sys
from collections import Counter
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

HERE = Path(__file__).resolve().parent
spec = importlib.util.spec_from_file_location("ops_probe", HERE / "ops_probe.py")
op = importlib.util.module_from_spec(spec); spec.loader.exec_module(op)
ROOT, EVID = op.ROOT, op.EVID


def lane_nodes():
    seed = json.loads(op.SEED.read_text(encoding="utf-8"))
    nodes = seed["nodes"]; ids = {n["id"]: n for n in nodes}
    def zone(n):
        c, ch = n, []
        while c and c["id"] not in ch:
            ch.append(c["id"]); c = ids.get(c.get("parent"))
        return ch[-2] if len(ch) >= 2 else n["id"]
    parents = {n["parent"] for n in nodes if n.get("parent")}
    L = sorted([n for n in nodes if zone(n) == "jeff" and n["id"] not in parents and n["status"] in ("code", "branch")],
               key=lambda n: n["id"])
    return L[:67]


def module_path(n):
    for s in n.get("sources") or []:
        if isinstance(s, dict) and s.get("path", "").endswith(".py") and (ROOT / s["path"]).is_file():
            return s["path"]
    return None


def main():
    ap = argparse.ArgumentParser(); ap.add_argument("--only"); ap.add_argument("--workers", type=int, default=3); ap.add_argument("--cap", type=int, default=120, help="pytest wall cap seconds (slow shared machine)")
    a = ap.parse_args()
    op.PYTEST_CAP = a.cap
    nodes = [n for n in lane_nodes() if module_path(n) and op.modname(module_path(n))[0]]
    if a.only:
        w = set(a.only.split(",")); nodes = [n for n in nodes if n["id"] in w]
    sha = op.sha_head()
    def go(n):
        n = dict(n); n["sources"] = [{"path": module_path(n)}]
        return op.probe(n, sha)
    with ThreadPoolExecutor(min(a.workers, 3)) as ex:
        rec = list(ex.map(go, nodes))
    full = EVID / "raw" / "jeffa-raw.json"
    old = json.loads(full.read_text(encoding="utf-8")) if (a.only and full.exists()) else []
    ids = {r["node_id"] for r in rec}
    rec = [r for r in old if r["node_id"] not in ids] + rec
    full.write_text(json.dumps(rec, ensure_ascii=False, indent=1), encoding="utf-8")
    print(len(rec), Counter(r["verdict"] for r in rec), Counter(r["reason"] for r in rec if r["verdict"] == "FAIL"))

if __name__ == "__main__":
    main()
