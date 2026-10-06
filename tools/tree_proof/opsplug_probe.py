"""Lane opsplug: deterministic GREEN receipts for ops + plugins leaves still blue.

Per leaf:
  1. import the module in a clean subprocess with THIS worktree first on PYTHONPATH
     (the package that owns the module goes first, so `tests.*` helpers resolve to the
     right project) and assert __file__ is inside the worktree;
  2. run every selected existing test file separately (cwd = its project, <=105s each)
     plus the test_leaf_* file authored by this lane when present;
  3. PASS only if the import worked, >=1 test passed and nothing failed.
Never edits product code. Re-runnable:
    python tools/tree_proof/opsplug_probe.py --only mod-a,mod-b [--workers 3] [--extra-tests id=path,..]
Writes evidence/opsplug.json (merged by node id) and evidence/out/<node_id>.txt.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import subprocess
import sys
import tempfile
import time
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
SEED = ROOT / "command-center/bcc/capability_tree_seed.json"
EVID = ROOT / "docs/architecture/bossman-tree-20261005/evidence"
OUT = EVID / "out"
PY = sys.executable
PKG_DIRS = {"command-center": "command-center", "bossman-core": "bossman-core", ".": "."}
TEST_DIRS = [("command-center", "command-center/tests"), ("bossman-core", "bossman-core/tests"), (".", "tests")]
PLAN_FILE = ROOT / "tools/tree_proof/opsplug_plan.json"
PER_FILE_CAP = 105
MAX_FILES = 8
SECRET_RE = re.compile(r"(KEY|TOKEN|SECRET|PASSWORD|PROXY|CREDENTIAL)", re.I)


def now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def head() -> str:
    return subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=ROOT, text=True).strip()


def env_for(tmp: str, first: str, exclusive: bool = False) -> dict:
    """bossman-core tests import helpers as `tests.<name>`; command-center/tests is a regular package and
    would shadow bossman-core's namespace `tests`, so those tests run with command-center OFF the path."""
    env = {k: v for k, v in os.environ.items() if not SECRET_RE.search(k)}
    order = [first] + [d for d in ("command-center", "bossman-core", ".") if d != first]
    if exclusive and first == "bossman-core":
        order = ["bossman-core", "."]
    env.update(PYTHONPATH=os.pathsep.join(str(ROOT / d) for d in order), PYTHONIOENCODING="utf-8",
               PYTHONUTF8="1", LOCALAPPDATA=tmp, APPDATA=tmp, BCC_DATA_DIR=tmp, BOSSMAN_DATA_DIR=tmp,
               PYTHONDONTWRITEBYTECODE="1", NO_PROXY="*", WORKSPACE_DIR=tmp,
               BOSSMAN_COST_DB=str(Path(tmp) / "cost.db"), BOSSMAN_NOTIFICATION_DB=str(Path(tmp) / "notif.db"))
    return env


def modinfo(path: str):
    """(dotted module, owning package dir) for a source path."""
    for pref, pdir in (("command-center/bcc/", "command-center"), ("bossman-core/bossman/", "bossman-core"),
                       ("tools/", ".")):
        if path.startswith(pref) and path.endswith(".py"):
            rel = path[len(pdir) + 1:-3] if pdir != "." else path[:-3]
            parts = rel.split("/")
            if parts[-1] == "__init__":
                parts = parts[:-1]
            return ".".join(parts), pdir
    return None, None


def find_tests(dotted: str, path: str):
    parts = dotted.split(".")
    name, parent = parts[-1], ".".join(parts[:-1])
    pats = [re.compile(r"(?<![\w.])" + re.escape(dotted) + r"(?![\w])")]
    if parent:
        pats.append(re.compile(r"from\s+" + re.escape(parent) + r"\s+import\s+(?:\(\s*)?[\w,\s#]{0,400}?\b"
                               + re.escape(name) + r"\b"))
    if path.startswith("tools/"):       # tools/x.py is also imported by file location in tests
        pats.append(re.compile(r"tools[\"'/\\ ,)]+\s*" + re.escape(name) + r"\.py|import\s+" + re.escape(name) + r"\b"))
    GENERIC = {"routes", "models", "subsystem", "service", "runtime", "engine", "brain", "bridge", "resources",
               "storage", "tasks", "verify", "reasoning", "workflow", "dataset", "toolbox", "office", "strategy"}
    if len(name) >= 8 and name not in GENERIC and not path.startswith("tools/"):
        # a distinctive module name used as a whole word (route prefix, import, endpoint) is a reference too
        pats.append(re.compile(r"(?<![\w])" + re.escape(name) + r"(?![\w])"))
    found = {}
    for cwd, d in TEST_DIRS:
        base = ROOT / d
        if not base.is_dir():
            continue
        for f in base.rglob("test_*.py"):
            try:
                txt = f.read_text(encoding="utf-8", errors="replace")
            except OSError:
                continue
            by_name = f.stem == "test_" + name or f.stem.startswith("test_" + name + "_") or \
                f.stem == "test_leaf_" + name or f.stem.startswith("test_leaf_" + name + "_")
            if by_name or any(p.search(txt) for p in pats):
                found[f] = (cwd, 0 if by_name else 1)
    ordered = sorted(found.items(), key=lambda kv: (kv[1][1], len(kv[0].parts), str(kv[0])))
    return [(f, c) for f, (c, _) in ordered]


def run(cmd, cwd, env, cap):
    t = time.time()
    try:
        p = subprocess.run(cmd, cwd=cwd, env=env, capture_output=True, timeout=cap)
        return p.returncode, p.stdout.decode("utf-8", "replace") + p.stderr.decode("utf-8", "replace"), round(time.time() - t, 1)
    except subprocess.TimeoutExpired as e:
        out = (e.stdout or b"").decode("utf-8", "replace") + (e.stderr or b"").decode("utf-8", "replace")
        return 124, out + f"\n[TIMEOUT after {cap}s]\n", cap


def load_plan() -> dict:
    """node id -> {"tests": [explicit 'cwd:path'], "add": [extra 'cwd:path'], "note": why}. Explicit 'tests' replace
    auto-discovery (used only to skip files that cannot run inside the 105s/command cap, with the reason in 'note')."""
    try:
        return json.loads(PLAN_FILE.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}


def probe(node, sha, extra, deselect):
    nid = node["id"]
    path = node["sources"][0]["path"] if isinstance(node["sources"][0], dict) else node["sources"][0]
    dotted, pdir = modinfo(path)
    started = now()
    tmp = tempfile.mkdtemp(prefix="opsplug_")
    log, cmds, reason, verdict = [], [], "", "FAIL"
    rootp = str(ROOT).replace("\\", "/")
    if dotted.endswith(".__main__"):
        parent = dotted.rsplit(".", 1)[0]
        code = ("import importlib,sys,subprocess;m=importlib.import_module(%r);f=m.__file__.replace(chr(92),'/');"
                "print('IMPORTED',%r,f);ok=f.lower().startswith(%r.lower());"
                "r=subprocess.run([sys.executable,'-m',%r,'--help'],capture_output=True,text=True);"
                "print('python -m',%r,'--help exit',r.returncode);sys.exit(0 if ok and r.returncode==0 else 3)"
                ) % (parent, dotted, rootp, parent, parent)
    elif dotted.startswith("tools."):
        code = ("import importlib.util,sys;sp=importlib.util.spec_from_file_location(%r,%r);m=importlib.util.module_from_spec(sp);"
                "sys.modules[%r]=m;sp.loader.exec_module(m);f=m.__file__.replace(chr(92),'/');print('IMPORTED',%r,f);"
                "sys.exit(0 if f.lower().startswith(%r.lower()) else 3)") % (dotted, str(ROOT / path), dotted, dotted, rootp)
    else:
        code = ("import importlib,sys;m=importlib.import_module(%r);f=(getattr(m,'__file__','') or '').replace(chr(92),'/');"
                "print('IMPORTED',m.__name__,f);root=%r;sys.exit(0 if f.lower().startswith(root.lower()) else 3)") % (dotted, rootp)
    cmds.append(f"python -c \"import {dotted}\" [PYTHONPATH={pdir} first, then command-center;bossman-core;.]")
    rc, out, dt = run([PY, "-c", code], str(ROOT), env_for(tmp, pdir), 60)
    log.append(f"$ {cmds[-1]}\n{out}\n[exit {rc} in {dt}s]\n")
    exit_code, tests, passed, failed_any = rc, [], 0, False
    plan = load_plan().get(nid, {})
    if rc != 0:
        reason = "import_outside_checkout" if rc == 3 else ("import_timeout" if rc == 124 else "import_failed")
    else:
        if plan.get("tests"):
            tests = [(ROOT / x.split(":", 1)[1], x.split(":", 1)[0]) for x in plan["tests"]]
        else:
            tests = [(ROOT / p, c) for p, c in extra.get(nid, [])] or find_tests(dotted, path)[:MAX_FILES]
            tests += [(ROOT / x.split(":", 1)[1], x.split(":", 1)[0]) for x in plan.get("add", [])
                      if (ROOT / x.split(":", 1)[1]) not in {t for t, _ in tests}]
        if not tests:
            reason = "no_tests"
        for f, cwd in tests:
            rel = os.path.relpath(f, ROOT / cwd).replace("\\", "/")
            cmd = [PY, "-m", "pytest", "-q", "--timeout=60", "-p", "no:cacheprovider", rel]
            for d in deselect.get(nid, []):
                cmd += ["--deselect", d]
            shown = f"(cd {cwd} && python -m pytest -q --timeout=60 -p no:cacheprovider {rel}" + \
                "".join(f" --deselect {d}" for d in deselect.get(nid, [])) + ")"
            cmds.append(shown)
            rc2, out2, dt2 = run(cmd, str(ROOT / cwd), env_for(tmp, PKG_DIRS[cwd], exclusive=True), PER_FILE_CAP)
            log.append(f"$ {shown}\n{out2}\n[exit {rc2} in {dt2}s]\n")
            exit_code = rc2
            m = re.search(r"(\d+) passed", out2)
            passed += int(m.group(1)) if m else 0
            if rc2 not in (0, 5):
                failed_any = True
                reason = ("pytest_timeout" if rc2 == 124 else
                          "env_missing_dependency" if re.search(r"ModuleNotFoundError|ImportError", out2) else
                          "tests_failed")
                break
        if tests and not failed_any:
            if passed >= 1:
                verdict, exit_code = "PASS", 0
            else:
                reason = "all_skipped_or_zero_collected"
    text = (f"node_id: {nid}\nmodule: {dotted}\npath: {path}\nsha: {sha}\n"
            f"test_files: {[os.path.relpath(f, ROOT).replace(chr(92), '/') for f, _ in tests]}\n"
            + (f"plan_note: {plan['note']}\n" if plan.get("note") else "") + "\n"
            + "\n".join(log) + f"\nVERDICT: {verdict} {reason}\n")
    OUT.mkdir(parents=True, exist_ok=True)
    data = text.encode("utf-8")
    (OUT / f"{nid}.txt").write_bytes(data)
    tail = text[-1500:]
    return {"node_id": nid, "sha": sha, "probe": "import+pytest" + ("+authored_by_lane" if any(
        "test_leaf_" in str(f) for f, _ in tests) else ""),
        "command": " && ".join(cmds), "exit_code": exit_code, "started_at": started, "finished_at": now(),
        "output_sha256": hashlib.sha256(data).hexdigest(), "output_tail": tail, "verdict": verdict, "kind": "pytest",
        "reason": reason if verdict == "FAIL" else "", "module": dotted,
        "test_files_run": len(tests), "tests_passed": passed}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--only", required=True)
    ap.add_argument("--workers", type=int, default=3)
    ap.add_argument("--extra", default="", help="node=cwd:path;node=cwd:path (explicit test files, ';' sep)")
    ap.add_argument("--deselect", default="", help="node=testid;node=testid")
    a = ap.parse_args()
    extra, deselect = {}, {}
    for item in filter(None, a.extra.split(";")):
        n, v = item.split("=", 1)
        c, p = v.split(":", 1)
        extra.setdefault(n, []).append((p, c))
    for item in filter(None, a.deselect.split(";")):
        n, v = item.split("=", 1)
        deselect.setdefault(n, []).append(v)
    seed = json.loads(SEED.read_text(encoding="utf-8"))
    want = a.only.split(",")
    byid = {n["id"]: n for n in seed["nodes"]}
    nodes = [byid[i] for i in want]
    sha = head()
    with ThreadPoolExecutor(min(a.workers, 3)) as ex:
        rec = list(ex.map(lambda n: probe(n, sha, extra, deselect), nodes))
    full = EVID / "opsplug.json"
    old = json.loads(full.read_text(encoding="utf-8")) if full.exists() else []
    ids = {r["node_id"] for r in rec}
    full.write_text(json.dumps([r for r in old if r["node_id"] not in ids] + rec, ensure_ascii=False, indent=1),
                    encoding="utf-8")
    for r in rec:
        print(r["node_id"], r["verdict"], r["reason"], f"passed={r['tests_passed']}")


if __name__ == "__main__":
    main()
