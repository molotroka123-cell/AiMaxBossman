"""Deterministic evidence receipts for lane jeffb (zone jeff, second half) -- derived from ops_probe.py. Original doc: 'ops' zone module leaves (mod-* / module-*).

Probe 1: import the module in a clean subprocess (checkout paths first, asserts __file__ is inside this checkout).
Probe 2: run ONLY the existing test files that reference the module (pytest, this SHA).
PASS = import ok AND >=1 test passed AND nothing failed. No tests => FAIL reason no_tests (never faked).
Never edits product code or tests. Re-runnable: python tools/tree_proof/ops_probe.py [--limit N] [--only id[,id]]
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
from collections import Counter
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
SEED = ROOT / "command-center/bcc/capability_tree_seed.json"
EVID = ROOT / "docs/architecture/bossman-tree-20261005/evidence"
OUT = EVID / "out"
PKGS = ("command-center", "bossman-core")
SRC_PREFIX = {"command-center/bcc/": "command-center", "bossman-core/bossman/": "bossman-core"}
TEST_DIRS = [("command-center", "command-center/tests"), ("bossman-core", "bossman-core/tests"), (".", "tests")]
MAX_FILES = 8
PYTEST_CAP = 120
IMPORT_CAP = 60
PY = sys.executable
SECRET_RE = re.compile(r"(KEY|TOKEN|SECRET|PASSWORD|PROXY|CREDENTIAL)", re.I)


def now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def sha_head() -> str:
    return subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=ROOT, text=True).strip()


def clean_env(tmp: str) -> dict:
    env = {k: v for k, v in os.environ.items() if not SECRET_RE.search(k)}
    pp = os.pathsep.join(str(ROOT / p) for p in ("command-center", "bossman-core", "."))
    env.update(PYTHONPATH=pp, PYTHONIOENCODING="utf-8", PYTHONUTF8="1", LOCALAPPDATA=tmp, APPDATA=tmp,
               BCC_DATA_DIR=tmp, BOSSMAN_DATA_DIR=tmp, PYTHONDONTWRITEBYTECODE="1", NO_PROXY="*")
    return env


def modname(path: str):
    for pref, pdir in SRC_PREFIX.items():
        if path.startswith(pref) and path.endswith(".py"):
            rel = path[len(pdir) + 1:-3]
            parts = rel.split("/")
            if parts[-1] == "__init__":
                parts = parts[:-1]
            return ".".join(parts), pdir
    return None, None


def find_tests(dotted: str):
    parts = dotted.split(".")
    name, parent = parts[-1], ".".join(parts[:-1])
    parts_root = ".".join(parts[1:2]) if parts[0] == "bcc" else parts[0]
    pats = [re.compile(r"(?<![\w.])" + re.escape(dotted) + r"(?![\w])")]
    if parent:
        pats.append(re.compile(r"from\s+" + re.escape(parent) + r"\s+import\s+(?:\(\s*)?[\w,\s#]{0,400}?\b"
                               + re.escape(name) + r"\b"))
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
            by_name = (f.stem == "test_" + name or f.stem.startswith("test_" + name + "_")) and (
                "bcc." + parts_root in txt or "bossman." + parts_root in txt)
            if by_name or any(p.search(txt) for p in pats):
                found[f] = (cwd, 0 if by_name else 1)
    ordered = sorted(found.items(), key=lambda kv: (kv[1][1], len(kv[0].parts), str(kv[0])))
    return [(f, c) for f, (c, _) in ordered]


def run(cmd, cwd, env, cap):
    t = time.time()
    try:
        p = subprocess.run(cmd, cwd=cwd, env=env, capture_output=True, timeout=cap)
        out = p.stdout.decode("utf-8", "replace") + p.stderr.decode("utf-8", "replace")
        return p.returncode, out, round(time.time() - t, 1)
    except subprocess.TimeoutExpired as e:
        out = (e.stdout or b"").decode("utf-8", "replace") + (e.stderr or b"").decode("utf-8", "replace")
        return 124, out + f"\n[TIMEOUT after {cap}s]\n", cap


def probe(node, sha):
    nid = node["id"]
    path = node["sources"][0]["path"]
    dotted, _ = modname(path)
    started = now()
    tmp = tempfile.mkdtemp(prefix="jeffbprobe_")
    env = clean_env(tmp)
    log, cmds = [], []
    reason, verdict = "", "FAIL"
    rootp = str(ROOT).replace("\\", "/")
    code = ("import importlib,sys;m=importlib.import_module(%r);f=(getattr(m,'__file__','') or '').replace(chr(92),'/');"
            "print('IMPORTED',m.__name__,f);root=%r;"
            "sys.exit(0 if f.lower().startswith(root.lower()) else 3)") % (dotted, rootp)
    cmds.append(f"python -c \"importlib.import_module('{dotted}')\" [PYTHONPATH=command-center;bossman-core;.]")
    rc, out, dt = run([PY, "-c", code], str(ROOT), env, IMPORT_CAP)
    log.append(f"$ {cmds[-1]}\n{out}\n[exit {rc} in {dt}s]\n")
    exit_code = rc
    tests = []
    if rc != 0:
        reason = "import_outside_checkout" if rc == 3 else ("import_timeout" if rc == 124 else "import_failed")
    else:
        tests = find_tests(dotted)
        if not tests:
            reason = "no_tests"
        else:
            groups = {}
            for f, cwd in tests[:MAX_FILES]:
                groups.setdefault(cwd, []).append(f)
            total_pass = 0
            for cwd, files in groups.items():
                rel = [os.path.relpath(f, ROOT / cwd).replace("\\", "/") for f in files]
                cmd = [PY, "-m", "pytest", "-x", "-q", "--timeout=60", "-p", "no:cacheprovider", *rel]
                shown = f"(cd {cwd} && python -m pytest -x -q --timeout=60 -p no:cacheprovider {' '.join(rel)})"
                cmds.append(shown)
                rc2, out2, dt2 = run(cmd, str(ROOT / cwd), env, PYTEST_CAP)
                log.append(f"$ {shown}\n{out2}\n[exit {rc2} in {dt2}s]\n")
                exit_code = rc2
                m = re.search(r"(\d+) passed", out2)
                total_pass += int(m.group(1)) if m else 0
                if rc2 != 0:
                    reason = ("pytest_timeout" if rc2 == 124 else
                              "env_missing_dependency" if re.search(r"ModuleNotFoundError|ImportError", out2) else
                              "network_or_secrets" if re.search(r"ConnectError|ConnectionError|getaddrinfo|api[_ ]key", out2, re.I) else
                              "tests_failed")
                    break
            if not reason:
                if total_pass >= 1:
                    verdict, exit_code = "PASS", 0
                else:
                    reason = "all_skipped_or_zero_collected"
    text = (f"node_id: {nid}\nmodule: {dotted}\npath: {path}\nsha: {sha}\n"
            f"test_files: {[os.path.relpath(f, ROOT).replace(chr(92), '/') for f, _ in tests[:MAX_FILES]]} "
            f"(candidates={len(tests)})\n\n" + "\n".join(log) + f"\nVERDICT: {verdict} {reason}\n")
    OUT.mkdir(parents=True, exist_ok=True)
    data = text.encode("utf-8")
    (OUT / f"{nid}.txt").write_bytes(data)
    return {
        "node_id": nid, "sha": sha, "probe": "import+pytest", "command": " && ".join(cmds),
        "exit_code": exit_code, "started_at": started, "finished_at": now(),
        "output_sha256": hashlib.sha256(data).hexdigest(), "output_tail": text[-1500:],
        "verdict": verdict, "kind": "pytest", "reason": reason if verdict == "FAIL" else "",
        "module": dotted, "test_files_run": len(tests[:MAX_FILES]), "test_candidates": len(tests),
    }


def main():
    global MAX_FILES
    ap = argparse.ArgumentParser()
    ap.add_argument("--limit", type=int)
    ap.add_argument("--only", help="comma-separated node ids")
    ap.add_argument("--workers", type=int, default=6)
    ap.add_argument("--max-files", type=int, default=MAX_FILES)
    a = ap.parse_args()
    sys.path.insert(0, str(Path(__file__).parent))
    from jeffb_scan import L
    nodes = [n for n in L if n.get("sources") and isinstance(n["sources"][0], dict)
             and (ROOT / n["sources"][0]["path"]).is_file() and modname(n["sources"][0]["path"])[0]]
    if a.only:
        want = set(a.only.split(","))
        nodes = [n for n in nodes if n["id"] in want]
    if a.limit:
        nodes = nodes[: a.limit]
    MAX_FILES = a.max_files
    sha = sha_head()
    with ThreadPoolExecutor(min(a.workers, 6)) as ex:
        rec = list(ex.map(lambda n: probe(n, sha), nodes))
    full = EVID / "jeffb.json"
    if a.only or a.limit:
        old = json.loads(full.read_text(encoding="utf-8")) if full.exists() else []
        ids = {r["node_id"] for r in rec}
        rec = [r for r in old if r["node_id"] not in ids] + rec
    full.write_text(json.dumps(rec, ensure_ascii=False, indent=1), encoding="utf-8")
    print(len(rec), Counter(r["verdict"] for r in rec), Counter(r["reason"] for r in rec if r["verdict"] == "FAIL"))


if __name__ == "__main__":
    main()
