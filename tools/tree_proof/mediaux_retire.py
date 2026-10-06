"""RETIRE audit receipts for lane mediaux (deterministic grep + AST; never edits product code).

Re-runnable: python tools/tree_proof/mediaux_retire.py
A leaf is retired only when: no importer outside the module itself, no route/CLI/manifest registration,
no docs/config reference as a required feature, and it is not security/safety/budget/backup related.
"""
from __future__ import annotations

import ast
import hashlib
import json
import re
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
EVID = ROOT / "docs/architecture/bossman-tree-20261005/evidence"
OUT = EVID / "out"
EXCLUDE = [":!docs/architecture/bossman-tree-20261005", ":!command-center/bcc/capability_tree_seed.json",
           ":!docs/architecture/tree-registry.json", ":!docs/architecture/tree-leaf-manifest.json",
           ":!docs/audits", ":!tools/tree_proof"]

CANDIDATES = {
    "reg-promo_video": {
        "path": "tools/promo_video/render.py",
        "needles": [r"promo_video", r"promo\.html"],
        "reason": ("Одноразовый скрипт рендера промо '32 дня' (даты 2026-08-27..09-27 зашиты в promo.html): ни одного "
                   "импортёра, нет маршрута/CLI/манифеста, в документации не заявлен как функция, требует Chromium и "
                   "Kokoro; не безопасность/бюджет/бэкап. Выпадает из дерева, файл остаётся в git."),
    },
}


def now():
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def sh(args):
    p = subprocess.run(args, cwd=ROOT, capture_output=True)
    return p.returncode, p.stdout.decode("utf-8", "replace") + p.stderr.decode("utf-8", "replace")


def audit(nid, spec):
    started = now()
    sha = subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=ROOT, text=True).strip()
    path = spec["path"]
    own_dir = str(Path(path).parent).replace("\\", "/")
    lines = [f"node_id: {nid}", f"path: {path}", f"sha: {sha}", ""]
    cmds = []
    foreign_total = 0
    for needle in spec["needles"]:
        cmd = ["git", "grep", "-nIE", needle, "--", ".", *EXCLUDE]
        cmds.append(" ".join(cmd))
        rc, out = sh(cmd)
        hits = [ln for ln in out.splitlines() if ln.strip()]
        foreign = [ln for ln in hits if not ln.replace("\\", "/").startswith(own_dir + "/")]
        foreign_total += len(foreign)
        lines += [f"$ {' '.join(cmd)}", f"[exit {rc}] hits={len(hits)} outside_own_dir={len(foreign)}"] + hits[:40] + [""]
    tree = ast.parse((ROOT / path).read_text(encoding="utf-8"))
    defs = [n.name for n in tree.body if isinstance(n, (ast.FunctionDef, ast.ClassDef))]
    has_main_guard = any(isinstance(n, ast.If) and "__main__" in ast.dump(n.test) for n in tree.body)
    lines += [f"AST: top-level defs={defs} main_guard={has_main_guard}",
              f"risk-words in module (security/approval/budget/backup/secret): "
              f"{bool(re.search(r'approval|permission|budget|backup|restore|secret|credential', (ROOT / path).read_text(encoding='utf-8'), re.I))}"]
    # other files of the same tool dir importing this module by file name
    mod = Path(path).stem
    imp = re.compile(r"^\s*(from|import)\s+.*\b" + re.escape(mod) + r"\b", re.M)
    importers = [str(p.relative_to(ROOT)).replace("\\", "/") for p in (ROOT / own_dir).rglob("*.py")
                 if p.name != Path(path).name and imp.search(p.read_text(encoding="utf-8", errors="replace"))]
    lines.append(f"importers inside own dir: {importers}")
    verdict = "RETIRE" if foreign_total == 0 and not importers and has_main_guard else "KEEP"
    lines.append(f"\nVERDICT: {verdict}")
    text = "\n".join(lines) + "\n"
    OUT.mkdir(parents=True, exist_ok=True)
    data = text.encode("utf-8")
    (OUT / f"{nid}.txt").write_bytes(data)
    rec = {"node_id": nid, "sha": sha, "probe": "grep+ast dead-code audit", "command": "python tools/tree_proof/mediaux_retire.py [" + " ; ".join(cmds) + "]",
           "exit_code": 0, "started_at": started, "finished_at": now(), "output_sha256": hashlib.sha256(data).hexdigest(),
           "output_tail": text[-1500:], "verdict": verdict if verdict == "RETIRE" else "FAIL", "kind": "audit",
           "reason": spec["reason"]}
    return rec


def main():
    recs = [audit(n, s) for n, s in CANDIDATES.items()]
    (EVID / "mediaux-retire.json").write_text(json.dumps(recs, ensure_ascii=False, indent=1), encoding="utf-8")
    print([(r["node_id"], r["verdict"]) for r in recs])


if __name__ == "__main__":
    sys.exit(main())
