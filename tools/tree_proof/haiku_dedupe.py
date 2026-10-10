#!/usr/bin/env python3
"""Find duplicate leaves in the capability tree: the judge (Haiku 5.5) proposes, a deterministic check decides.

Owner 10.10: «удали ненужное». Only leaves a retire receipt may touch (code / branch / recorded — see
tree_apply_evidence.validate_receipt) are candidates. The judge sees them per zone and answers
{"duplicates": [{"id": ..., "keep": ..., "reason": ...}]}. A proposal becomes a RETIRE receipt (kind=audit) ONLY when
  - `keep` is a different, non-retired node, and
  - both carry the same original name (label_en or label, case-insensitive) or the same source file;
everything else is written to the report as a suggestion for the owner, never applied. The receipt's output file
records the judge answer and the deterministic match; tree_apply_evidence validates it like any other receipt.

  python tools/tree_proof/haiku_dedupe.py [--lane dedupe-20261010] [--cap-usd 0.3] [--dry]
"""
from __future__ import annotations

import argparse
import hashlib
import json
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import haiku_translate as tr  # noqa: E402
import leaf_pytest_probe as probe  # noqa: E402

ROOT, SEED, EVID = probe.ROOT, probe.SEED, probe.EVID
RETIRABLE = {"code", "branch", "recorded"}

PROMPT = """You audit a capability tree of a local AI assistant. Below are leaves of ONE zone that may be retired, plus
the other living leaves of that zone for reference. Flag ONLY clear duplicates: a candidate that describes the same
thing as another living leaf (same skill copied to a second folder, same module listed twice, same repo twice).
Do not flag things that are merely related. Answer ONLY JSON:
{{"duplicates": [{{"id": "<candidate id>", "keep": "<id of the leaf it duplicates>", "reason": "<one sentence>"}}]}}

Candidates:
{cands}

Other living leaves of the zone:
{others}"""


def original(node: dict) -> str:
    return (node.get("label_en") or node.get("label") or "").strip().lower()


def deterministic_match(a: dict, b: dict) -> str | None:
    if a["id"] == b["id"]:
        return None
    if original(a) and original(a) == original(b):
        return f"same original name «{original(a)}»"
    sa, sb = probe.source_of(a), probe.source_of(b)
    if sa and sa == sb:                     # one python module listed twice; a shared page/doc file is NOT enough
        return f"same source module {sa}"   # (10.10: a page sub-feature is not a duplicate of the page)
    return None


CONFIRM = """Two leaves of a capability tree carry the same original name. Are they the SAME capability listed twice
(e.g. one skill copied into two folders), or two different things that only share a name (e.g. a module and a skill)?
A: {a}
B: {b}
Answer ONLY JSON: {{"same": true|false, "keep": "<id to keep if same>", "reason": "<one sentence>"}}"""


def same_name_pairs(leaves: list[dict]) -> list[tuple[dict, dict]]:
    """Retirable leaves whose original name equals another retirable leaf's — the judge then confirms or rejects."""
    seen: dict[str, dict] = {}
    pairs = []
    for n in leaves:
        if n["status"] not in RETIRABLE or not original(n):
            continue
        if original(n) in seen:
            pairs.append((seen[original(n)], n))
        else:
            seen[original(n)] = n
    return pairs


def row(n: dict) -> str:
    return json.dumps({"id": n["id"], "name": n.get("label_en") or n["label"], "label": n["label"], "status": n["status"],
                       "source": (n.get("sources") or [{}])[0].get("path", ""), "detail": (n.get("detail") or "")[:100]},
                      ensure_ascii=False)


def retire(receipts: list, node: dict, keep: dict, match: str, reason: str, zone: str, sha: str) -> list:
    """Write the evidence file and the RETIRE audit receipt that tree_apply_evidence validates."""
    nid = node["id"]
    started = datetime.now(timezone.utc).isoformat()
    text = (f"$ haiku_dedupe zone={zone} judge={tr.judge.MODEL}\nsha: {sha}\n\njudge: {reason}\n"
            f"deterministic: {nid} vs {keep['id']}: {match}\nkeep: {keep['id']} ({keep['status']}) «{keep['label']}»\n"
            f"retire: {nid} ({node['status']}) «{node['label']}»\nexit_code: 0\n")
    out = EVID / "out" / f"{nid.replace('/', '_')}.txt"
    out.write_text(text, encoding="utf-8", newline="\n")
    receipts = [r for r in receipts if r.get("node_id") != nid]
    receipts.append({"node_id": nid, "sha": sha, "probe": f"audit: duplicate of {keep['id']} ({match}), judge {tr.judge.MODEL}",
                     "command": "python tools/tree_proof/haiku_dedupe.py", "exit_code": 0, "started_at": started,
                     "finished_at": datetime.now(timezone.utc).isoformat(),
                     "output_sha256": hashlib.sha256(out.read_bytes()).hexdigest(), "output_tail": text[-1400:],
                     "verdict": "RETIRE", "kind": "audit",
                     "reason": f"Дубликат листа {keep['id']} ({match}); судья {tr.judge.MODEL}: {reason}"})
    return receipts


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--lane", default="dedupe-20261010")
    ap.add_argument("--cap-usd", type=float, default=0.3)
    ap.add_argument("--dry", action="store_true")
    a = ap.parse_args(argv)
    seed = json.loads(SEED.read_text(encoding="utf-8"))
    nodes = [n for n in seed["nodes"] if n["status"] != "retired"]
    by = {n["id"]: n for n in nodes}
    parents = {n.get("parent") for n in seed["nodes"] if n.get("parent")}
    zones = tr.zone_labels(seed["nodes"])
    leaves = [n for n in nodes if n["id"] not in parents]
    sha = subprocess.run(["git", "-C", str(ROOT), "rev-parse", "HEAD"], capture_output=True, text=True).stdout.strip()
    spent, applied, suggestions = 0.0, [], []
    lane_file = EVID / f"{a.lane}.json"
    receipts = json.loads(lane_file.read_text(encoding="utf-8")) if lane_file.is_file() else []
    for zone in sorted({zones[n["id"]] for n in leaves}):
        members = [n for n in leaves if zones[n["id"]] == zone]
        cands = [n for n in members if n["status"] in RETIRABLE]
        if not cands or len(members) < 2:
            continue
        if a.dry:
            print(f"{zone}: {len(cands)} candidates / {len(members)} leaves")
            continue
        if spent >= a.cap_usd:
            print(f"cap ${a.cap_usd} reached at zone {zone}")
            break
        others = [n for n in members if n["status"] not in RETIRABLE]
        answer, cost = tr.ask_json(PROMPT.format(cands="\n".join(map(row, cands)), others="\n".join(map(row, others[:120]))))
        spent += cost
        for d in answer.get("duplicates", []) if isinstance(answer.get("duplicates"), list) else []:
            nid, keep, reason = d.get("id"), d.get("keep"), str(d.get("reason", "")).strip()
            if nid not in by or keep not in by or by[nid]["status"] not in RETIRABLE or nid in parents:
                suggestions.append({**d, "why_not_applied": "unknown id or not retirable"})
                continue
            match = deterministic_match(by[nid], by[keep])
            if not match:
                suggestions.append({**d, "why_not_applied": "no deterministic match (name/source differ)"})
                continue
            receipts = retire(receipts, by[nid], by[keep], match, reason, zone, sha)
            applied.append({"id": nid, "keep": keep, "match": match, "reason": reason})
        print(f"{zone}: {len(cands)} candidates, spent ${spent:.4f}")
    if not a.dry:
        done = {r["id"] for r in applied}
        for x, y in same_name_pairs(leaves):
            if spent >= a.cap_usd or x["id"] in done or y["id"] in done:
                continue
            answer, cost = tr.ask_json(CONFIRM.format(a=row(x), b=row(y)))
            spent += cost
            keep = answer.get("keep")
            if answer.get("same") is True and keep in (x["id"], y["id"]):
                gone, kept = (y, x) if keep == x["id"] else (x, y)
                reason = str(answer.get("reason", "")).strip()
                receipts = retire(receipts, gone, kept, deterministic_match(gone, kept), reason, zones[gone["id"]], sha)
                applied.append({"id": gone["id"], "keep": kept["id"], "match": "same original name", "reason": reason})
                done.add(gone["id"])
            else:
                suggestions.append({"id": y["id"], "keep": x["id"], "reason": answer.get("reason"),
                                    "why_not_applied": "same name, judge says different things"})
    if a.dry:
        return 0
    if receipts:
        lane_file.write_text(json.dumps(receipts, ensure_ascii=False, indent=2) + "\n", encoding="utf-8", newline="\n")
    (EVID / f"{a.lane}-report.json").write_text(json.dumps({"sha": sha, "judge": tr.judge.MODEL, "spent_usd": round(spent, 5),
                                                             "retire_receipts": applied, "suggestions": suggestions},
                                                            ensure_ascii=False, indent=2) + "\n", encoding="utf-8", newline="\n")
    print(json.dumps({"retire_receipts": len(applied), "suggestions": len(suggestions), "spent_usd": round(spent, 5)}))
    return 0


if __name__ == "__main__":
    sys.exit(main())
