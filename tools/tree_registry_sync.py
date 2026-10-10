"""Sync the capability tree with the repository: add leaves for implemented code from a manifest, and write the per-leaf evidence registry.

    python tools/tree_registry_sync.py add --manifest docs/architecture/tree-leaf-manifest.json
    python tools/tree_registry_sync.py registry --audit docs/audits/blue-leaf-audit-20261006.json --out docs/architecture/tree-registry.json \
        [--ci-evidence ci.json] [--owner-evidence owner.json]

Rules (never relaxed):
* a leaf is added only if its source file exists; its detail comes from the module docstring, not from a hand-written claim;
* every leaf row carries a stable id, source file, the commit that last touched it, the integration status of the tree, and FOUR separate
  evidence levels — code present, tests passed in a recorded run, CI green on a SHA, verified on the owner's PC. A level without evidence is
  NOT_RUN; nothing here ever marks a leaf green by itself, and `proven_through` is the highest level reached in order (a passing test does not imply CI);
* no network, no models, no secrets.
"""
from __future__ import annotations

import argparse
import ast
import json
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SEED = ROOT / "command-center" / "bcc" / "capability_tree_seed.json"
LEVELS = ("code", "tests", "ci", "owner_pc")
#: A manifest may only add a leaf BELOW green: green ('reported'/'working') comes from receipts via tree_apply_evidence.
ENTRY_STATUSES = {"code", "idea", "prepared", "blocked"}


def git(*a: str) -> str:
    return subprocess.run(["git", "-C", str(ROOT), *a], capture_output=True, text=True, check=False).stdout.strip()


def last_sha(path: str) -> str:
    return git("log", "-1", "--format=%H", "--", path)


def docstring_line(path: Path) -> str:
    if path.suffix != ".py":
        return ""
    try:
        doc = ast.get_docstring(ast.parse(path.read_text(encoding="utf-8", errors="replace"))) or ""
    except SyntaxError:
        return ""
    return " ".join(doc.split())[:260]


def load_seed() -> tuple[dict, str]:
    raw = SEED.read_text(encoding="utf-8")
    return json.loads(raw), raw


def write_seed(doc: dict, raw: str) -> None:
    indent = 2 if '\n  "' in raw else 1
    SEED.write_text(json.dumps(doc, ensure_ascii=False, indent=indent) + ("\n" if raw.endswith("\n") else ""), encoding="utf-8",
                    newline="\n")                                   # LF on Windows too (CRLF rewrote every line)


def cmd_add(manifest: Path, update: bool = False) -> int:
    """Add manifest leaves; with update=True also refresh label/detail/status of EXISTING reg-* leaves that are still
    below green (code/idea/prepared/blocked). A green leaf (reported/working/...) is never touched: it moves only by
    receipts (tree_apply_evidence)."""
    doc, raw = load_seed()
    nodes = doc["nodes"]
    byid = {n["id"]: n for n in nodes}
    zones = {n["id"] for n in nodes if n.get("parent") == "bossman"}
    added, skipped, updated = [], [], []
    for e in json.loads(manifest.read_text(encoding="utf-8")):
        src = e["source"]
        if not (ROOT / src).is_file():
            print(f"REFUSED: {e['slug']}: source file {src} does not exist")
            return 3
        missing = [t for t in e.get("tests", []) if not (ROOT / t).is_file()]
        if missing:
            print(f"REFUSED: {e['slug']}: test file(s) missing: {missing}")
            return 3
        if e["parent"] not in zones:
            print(f"REFUSED: {e['slug']}: parent {e['parent']!r} is not a zone")
            return 3
        status = e.get("status", "code")
        if status not in ENTRY_STATUSES:
            print(f"REFUSED: {e['slug']}: status {status!r} cannot be set by a manifest (only {sorted(ENTRY_STATUSES)}; green needs receipts)")
            return 3
        if status != "code" and len(str(e.get("detail", "")).strip()) < 20:
            print(f"REFUSED: {e['slug']}: status {status} needs a 'detail' saying why (>= 20 chars)")
            return 3
        nid = f"reg-{e['slug']}"
        if nid in byid:
            cur = byid[nid]
            if update and cur.get("status") in ENTRY_STATUSES:
                if status == "code":
                    lead = docstring_line(ROOT / src)
                    cur["detail"] = (lead or "Описания в коде нет.") + " Код есть; польза и живая работа не доказаны."
                else:
                    cur["detail"] = str(e["detail"]).strip()
                cur.update(label=e["label"], status=status, sources=[{"path": src, "sha": last_sha(src)}],
                           reference_paths=[src, *e.get("tests", [])])
                updated.append(nid)
            else:
                skipped.append(nid)
            continue
        lead = docstring_line(ROOT / src)
        detail = ((lead or "Описания в коде нет.") + " Код есть; польза и живая работа не доказаны." if status == "code"
                  else str(e["detail"]).strip())
        node = {"id": nid, "label": e["label"], "parent": e["parent"], "status": status,
                "detail": detail,
                "sources": [{"path": src, "sha": last_sha(src)}],
                "next_action": "Проверить через UX/CMD на ПК владельца и измерить пользу до/после на одинаковых задачах.",
                "reference_paths": [src, *e.get("tests", [])]}
        nodes.append(node)
        byid[nid] = node
        added.append(nid)
    write_seed(doc, raw)
    print(json.dumps({"added": len(added), "updated": len(updated), "already_present": len(skipped), "nodes": len(nodes)},
                     ensure_ascii=False))
    return 0


def level_states(row: dict | None, ci: dict, owner: dict, leaf_id: str, exists: bool) -> dict:
    tests_state = "NOT_RUN"
    if row:
        if row.get("failed"):
            tests_state = "FAILED"
        elif row.get("verdict") == "covered" and row.get("passed"):
            tests_state = "PASSED_RECORDED_RUN"
        elif row.get("verdict") == "untested":
            tests_state = "NO_TEST"
    files = (row or {}).get("tests", [])
    ci_state = "NOT_RUN"
    if ci and files and all(f in set(ci.get("green_tests", [])) for f in files):
        ci_state = "GREEN_ON_SHA"
    owner_state = "VERIFIED" if leaf_id in owner else "NOT_RUN"
    return {
        "code": {"state": "PRESENT" if exists else "MISSING"},
        "tests": {"state": tests_state, "files": files, "basis": (row or {}).get("basis"),
                  "passed": (row or {}).get("passed", 0), "failed": (row or {}).get("failed", 0), "skipped": (row or {}).get("skipped", 0)},
        "ci": {"state": ci_state, "sha": ci.get("sha") if ci_state == "GREEN_ON_SHA" else None, "run_url": ci.get("run_url") if ci_state == "GREEN_ON_SHA" else None},
        "owner_pc": {"state": owner_state, "evidence": owner.get(leaf_id)},
    }


def proven_through(levels: dict) -> str:
    """Highest level reached IN ORDER; a later level never counts without the earlier ones."""
    ok = {"code": levels["code"]["state"] == "PRESENT", "tests": levels["tests"]["state"] == "PASSED_RECORDED_RUN",
          "ci": levels["ci"]["state"] == "GREEN_ON_SHA", "owner_pc": levels["owner_pc"]["state"] == "VERIFIED"}
    reached = "none"
    for lv in LEVELS:
        if not ok[lv]:
            break
        reached = lv
    return reached


def cmd_registry(audit: Path, out: Path, ci_path: Path | None, owner_path: Path | None) -> int:
    doc, _ = load_seed()
    rows = {r["id"]: r for r in json.loads(audit.read_text(encoding="utf-8")).get("rows", [])}
    ci = json.loads(ci_path.read_text(encoding="utf-8")) if ci_path and ci_path.is_file() else {}
    owner = {e["id"]: e.get("evidence") for e in json.loads(owner_path.read_text(encoding="utf-8"))} if owner_path and owner_path.is_file() else {}
    leaves = []
    for n in doc["nodes"]:
        if n.get("parent") in ("", None) or n["id"] == "bossman":
            continue
        src = next((s["path"] for s in n.get("sources", []) if s.get("path")), "")
        exists = bool(src) and (ROOT / src).is_file()
        levels = level_states(rows.get(n["id"]), ci, owner, n["id"], exists)
        leaves.append({"id": n["id"], "label": n["label"], "zone_parent": n["parent"], "source": src,
                       "sha": (n["sources"][0].get("sha") or last_sha(src)) if n.get("sources") and src else "",
                       "integration_status": n.get("status"), "levels": levels, "proven_through": proven_through(levels),
                       "evidence": {"audit": audit.name, "ci": ci.get("run_url"), "owner_pc": owner.get(n["id"])}})
    reached = {}
    for lf in leaves:
        reached[lf["proven_through"]] = reached.get(lf["proven_through"], 0) + 1
    result = {"schema": "bossman-tree-registry-1", "head": git("rev-parse", "HEAD"), "leaves": leaves, "proven_through_counts": reached,
              "note": "tests = a recorded run in the cloud; ci = green on the stated SHA only when a CI evidence file is supplied; owner_pc only from an owner evidence file."}
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(result, ensure_ascii=False, indent=1) + "\n", encoding="utf-8")
    print(json.dumps({"leaves": len(leaves), "proven_through": reached}, ensure_ascii=False))
    return 0


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser()
    sub = ap.add_subparsers(dest="cmd", required=True)
    a = sub.add_parser("add")
    a.add_argument("--manifest", type=Path, required=True)
    a.add_argument("--update", action="store_true", help="refresh existing leaves that are still below green")
    r = sub.add_parser("registry")
    r.add_argument("--audit", type=Path, required=True)
    r.add_argument("--out", type=Path, required=True)
    r.add_argument("--ci-evidence", type=Path)
    r.add_argument("--owner-evidence", type=Path)
    args = ap.parse_args(argv)
    if args.cmd == "add":
        return cmd_add(args.manifest, update=args.update)
    return cmd_registry(args.audit, args.out, args.ci_evidence, args.owner_evidence)


if __name__ == "__main__":
    sys.exit(main())
