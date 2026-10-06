"""Writes evidence/agcloud-classification.md from the seed + lane receipts (agcloud).

Every leaf of zones agents/cloud/computer still in status code/branch (and the idea/blocked ones, for completeness)
gets exactly one verdict: GREEN (PASS receipt), RETIRE (audit receipt) or KEEP (honest one-line reason).
"""
from __future__ import annotations

import json
from collections import Counter
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
EVID = ROOT / "docs/architecture/bossman-tree-20261005/evidence"
TOP = {"cap-21", "cap-22", "mod-router", "cap-40", "mod-economy_swarm", "mod-nl_permissions"}
ZONES = ("agents", "cloud", "computer")

KEEP_REASON = {
    "cap-24": "idea: Telegram/phone remote operator is a design doc only (no code), needs real work",
    "cap-27": "blocked: computer-use act path needs owner WAIT_APPROVAL on the real desktop; not run by this lane",
    "cap-28": "idea: OpenDots sidecar is a design doc only (no code), needs real work",
}


def lane(name):
    f = EVID / name
    return {r["node_id"]: r for r in json.loads(f.read_text(encoding="utf-8"))} if f.exists() else {}


def main():
    seed = json.loads((ROOT / "command-center/bcc/capability_tree_seed.json").read_text(encoding="utf-8"))
    by = {n["id"]: n for n in seed["nodes"]}
    parents = {n["parent"] for n in seed["nodes"] if n.get("parent")}
    green, retire = lane("agcloud.json"), lane("agcloud-retire.json")

    def zone(n):
        while n.get("parent") and by[n["parent"]].get("parent"):
            n = by[n["parent"]]
        return n["id"]

    rows, cnt = [], Counter()
    for n in seed["nodes"]:
        if zone(n) not in ZONES or n["id"] in ZONES or n["id"] in parents:
            continue
        # status in the committed seed may already be reported/retired if the apply step ran; classify by receipts
        nid = n["id"]
        g, r = green.get(nid), retire.get(nid)
        label = n["label"]
        if r and r["verdict"] == "RETIRE":
            v, why, val = "RETIRE", r["reason"], "LOW"
        elif g and g["verdict"] == "PASS":
            v = "GREEN"
            why = ("live free-OpenRouter call (model " + str(g.get("model")) + ") + import + tests") if g["kind"] == "live_call" \
                else "import in clean subprocess + tests passed"
            if "authored_by_lane" in g["probe"]:
                why += "; focused behavior test authored by lane"
            val = "TOP" if nid in TOP else "OK"
        elif g and g["verdict"] == "FAIL":
            v, why, val = "KEEP", f"receipt FAIL ({g['reason']}): see out/{nid}.txt", "OK"
        else:
            v, why, val = "KEEP", KEEP_REASON.get(nid, "no receipt"), "OK"
        cnt[v] += 1
        rows.append((nid, label, v, why, val))
    out = ["# agcloud lane classification (agents, cloud, computer)", "",
           f"Counts: {dict(cnt)}; TOP: {sorted(i for i, _, _, _, val in rows if val == 'TOP')}", "",
           "| id | label | verdict | reason | value |", "|---|---|---|---|---|"]
    out += [f"| {i} | {l} | {v} | {w.replace('|', '/')} | {val} |" for i, l, v, w, val in rows]
    (EVID / "agcloud-classification.md").write_text("\n".join(out) + "\n", encoding="utf-8")
    print(dict(cnt), len(rows))


if __name__ == "__main__":
    main()
