"""Deterministic evidence receipts for lane 'memapps' (zones memory + apps), eligible leaves status code|branch.

For every leaf: import the unit in a clean subprocess with THIS worktree first on PYTHONPATH (asserting __file__ is
inside the worktree) AND run the existing tests that reference it (pytest, this SHA). PASS only if import works,
>=1 test passed and nothing failed. Failures are recorded as FAIL receipts with a reason; nothing is faked.
Leaves whose source file is absent in the worktree get no receipt (classified KEEP by the classification table).
Re-runnable: python tools/tree_proof/memapps_probe.py [--only id,id] [--workers 3]
Never edits product code or tests.
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
LANE = "memapps"
PY = sys.executable
PYTEST_CAP = 110
IMPORT_CAP = 60
SECRET_RE = re.compile(r"(KEY|TOKEN|SECRET|PASSWORD|PROXY|CREDENTIAL)", re.I)
ZONES = ("memory", "apps")
# explicit extra test files for leaves whose name does not appear in test text
EXTRA_TESTS = {}


def now():
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def sha_head():
    return subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=ROOT, text=True).strip()


def env_for(tmp, pypath):
    env = {k: v for k, v in os.environ.items() if not SECRET_RE.search(k)}
    env.update(PYTHONPATH=os.pathsep.join(str(ROOT / p) if p != "." else str(ROOT) for p in pypath),
               PYTHONIOENCODING="utf-8", PYTHONUTF8="1", LOCALAPPDATA=tmp, APPDATA=tmp, BCC_DATA_DIR=tmp,
               BOSSMAN_DATA_DIR=tmp, PYTHONDONTWRITEBYTECODE="1", NO_PROXY="*")
    return env


def run(cmd, cwd, env, cap):
    t = time.time()
    try:
        p = subprocess.run(cmd, cwd=cwd, env=env, capture_output=True, timeout=cap)
        return p.returncode, p.stdout.decode("utf-8", "replace") + p.stderr.decode("utf-8", "replace"), round(time.time() - t, 1)
    except subprocess.TimeoutExpired as e:
        o = (e.stdout or b"").decode("utf-8", "replace") + (e.stderr or b"").decode("utf-8", "replace")
        return 124, o + f"\n[TIMEOUT after {cap}s]\n", cap


def spec(node):
    """-> dict(dotted, pypath, cwd, tests(list of paths relative to cwd) or None, kind) or None if source absent."""
    path = node["sources"][0]["path"]
    nid = node["id"]
    if not (ROOT / path).exists():
        return None
    if nid == "app-solana-volume-suite":  # manifest lives in apps/, the code at repo root solana_volume_suite/
        return {"dotted": "solana_volume_suite.core", "pypath": ["."], "cwd": ".", "kind": "app",
                "tests": ["tests/test_solana_safety.py", "tests/test_mainnet_setup_safety.py", "solana_volume_suite/tests"]}
    if nid.startswith("app-"):
        app = path.split("/")[1]
        pkg = None
        src = ROOT / "apps" / app / "src"
        if src.is_dir():
            pkg = next((p.name for p in src.iterdir() if p.is_dir() and (p / "__init__.py").exists()), None)
        if not pkg or not (ROOT / "apps" / app / "tests").is_dir():
            return {"dotted": pkg, "pypath": [f"apps/{app}/src"], "cwd": f"apps/{app}", "tests": [], "kind": "app"}
        return {"dotted": pkg, "pypath": [f"apps/{app}/src", f"apps/{app}"], "cwd": f"apps/{app}",
                "tests": ["tests"], "kind": "app"}
    p = path
    if p.startswith("apps/poker-vision/") and p.endswith(".py"):
        rel = p[len("apps/poker-vision/"):-3].split("/")
        return {"dotted": ".".join(rel[:-1] if rel[-1] == "__init__" else rel), "pypath": ["apps/poker-vision"],
                "cwd": "apps/poker-vision", "tests": None, "testdir": "apps/poker-vision/tests", "kind": "mod"}
    for pref, pdir in (("command-center/bcc/", "command-center"), ("bossman-core/bossman/", "bossman-core")):
        if p.startswith(pref) and p.endswith(".py"):
            rel = p[len(pdir) + 1:-3].split("/")
            return {"dotted": ".".join(rel[:-1] if rel[-1] == "__init__" else rel),
                    "pypath": ["command-center", "bossman-core", "."], "cwd": pdir, "tests": None,
                    "testdir": f"{pdir}/tests", "kind": "mod"}
    if p.startswith("tools/") and p.endswith(".py"):
        rel = p[:-3].split("/")
        return {"dotted": ".".join(rel), "pypath": ["command-center", "bossman-core", "."], "cwd": ".",
                "tests": None, "testdir": "command-center/tests", "kind": "mod"}
    return {"dotted": None, "pypath": [], "cwd": ".", "tests": [], "kind": "nonpy", "path": p}


def find_tests(dotted, dirs, own_dir=None):
    parts = dotted.split(".")
    name, parent = parts[-1], ".".join(parts[:-1])
    pats = [re.compile(r"(?<![\w.])" + re.escape(dotted) + r"(?![\w])")]
    if parent:
        pats.append(re.compile(r"from\s+" + re.escape(parent) + r"\s+import\s+(?:\(\s*)?[\w,\s#]{0,400}?\b" + re.escape(name) + r"\b"))
    found = {}
    for d in dirs:
        base = ROOT / d
        if not base.is_dir():
            continue
        for f in base.rglob("test_*.py"):
            try:
                txt = f.read_text(encoding="utf-8", errors="replace")
            except OSError:
                continue
            if own_dir is None:
                same = True
            else:
                same = str(f).replace("\\", "/").startswith(str(ROOT / own_dir).replace("\\", "/") + "/")
            by_name = same and (f.stem == "test_" + name or f.stem.startswith("test_" + name + "_") or f.stem.startswith("test_leaf_" + name))
            if by_name and parts[0] == "bossman" and len(parts) > 2 and not re.search(r"" + re.escape(parts[1]) + r"", txt):
                by_name = False  # generic file name (test_benchmark_*, test_memory_*) of an unrelated package
            if by_name or any(p.search(txt) for p in pats):
                found[f] = 0 if by_name else 1
    return [f for f, _ in sorted(found.items(), key=lambda kv: (kv[1], len(kv[0].parts), str(kv[0])))]


def probe(node, sha):
    nid = node["id"]
    sp = spec(node)
    if sp is None:
        return None
    started = now()
    tmp = tempfile.mkdtemp(prefix="memapps_")
    env = env_for(tmp, sp["pypath"] or ["."])
    log, cmds, reason, verdict, exit_code = [], [], "", "FAIL", 1
    rootp = str(ROOT).replace("\\", "/")
    tests = []
    if not sp["dotted"]:
        reason = "no_python_unit" if sp["kind"] != "nonpy" else "non_python_source"
    else:
        code = ("import importlib,sys;m=importlib.import_module(%r);f=(getattr(m,'__file__','') or '').replace(chr(92),'/');"
                "print('IMPORTED',m.__name__,f);root=%r;sys.exit(0 if f.lower().startswith(root.lower()) else 3)") % (sp["dotted"], rootp)
        cmds.append(f"python -c \"importlib.import_module('{sp['dotted']}')\" [PYTHONPATH={';'.join(sp['pypath'])}]")
        rc, out, dt = run([PY, "-c", code], str(ROOT / sp["cwd"]), env, IMPORT_CAP)
        log.append(f"$ {cmds[-1]}\n{out}\n[exit {rc} in {dt}s]\n")
        exit_code = rc
        if rc != 0:
            reason = "import_outside_checkout" if rc == 3 else ("import_timeout" if rc == 124 else "import_failed")
        else:
            if sp["tests"] is not None:
                tests = sp["tests"]
                rel = tests
                n_cand = len(tests)
            else:
                dirs = [sp["testdir"]] + (["command-center/tests", "bossman-core/tests", "tests"] if sp["kind"] == "mod" else [])
                files = find_tests(sp["dotted"], dirs, sp["testdir"])
                n_cand = len(files)
                files = files[:8]
                # all selected files must live in one pytest cwd group; group by their top test dir
                tests = files
                rel = [os.path.relpath(f, ROOT / sp["cwd"]).replace("\\", "/") for f in files]
            if not rel:
                reason = "no_tests"
            else:
                groups = {}
                if sp["tests"] is None:
                    for f in tests:
                        for c in ("command-center", "bossman-core", "apps/poker-vision", "."):
                            if str(f).replace("\\", "/").startswith(str(ROOT / c).replace("\\", "/") + "/") and c != ".":
                                groups.setdefault(c, []).append(f)
                                break
                        else:
                            groups.setdefault(".", []).append(f)
                else:
                    groups[sp["cwd"]] = [None]
                total = 0
                for cwd, files in groups.items():
                    if sp["tests"] is None:
                        r2 = [os.path.relpath(f, ROOT / cwd).replace("\\", "/") for f in files]
                    else:
                        r2 = sp["tests"]
                    cmd = [PY, "-m", "pytest", "-x", "-q", "--timeout=60", "-p", "no:cacheprovider", *r2]
                    shown = f"(cd {cwd} && python -m pytest -x -q --timeout=60 -p no:cacheprovider {' '.join(r2)})"
                    cmds.append(shown)
                    rc2, out2, dt2 = run(cmd, str(ROOT / cwd), env, PYTEST_CAP)
                    log.append(f"$ {shown}\n{out2}\n[exit {rc2} in {dt2}s]\n")
                    exit_code = rc2
                    m = re.search(r"(\d+) passed", out2)
                    if m:
                        total += int(m.group(1))
                    else:  # ini addopts may silence the summary line: count progress dots
                        total += sum(l.split("[")[0].count(".") for l in out2.splitlines() if re.fullmatch(r"[.sFExX]+\s*(\[\s*\d+%\])?", l.strip()))
                    if rc2 != 0:
                        reason = ("pytest_timeout" if rc2 == 124 else
                                  "all_skipped_env_dependency" if rc2 == 5 else
                                  "env_missing_dependency" if re.search(r"ModuleNotFoundError|ImportError", out2) else
                                  "network_or_secrets" if re.search(r"ConnectError|ConnectionError|getaddrinfo|api[_ ]key", out2, re.I) else
                                  "tests_failed")
                        break
                if not reason:
                    if total >= 1:
                        verdict, exit_code = "PASS", 0
                    else:
                        reason = "all_skipped_or_zero_collected"
    tf = [os.path.relpath(f, ROOT).replace(chr(92), "/") for f in tests] if isinstance(tests, list) and tests and not isinstance(tests[0], str) else tests
    text = (f"node_id: {nid}\nunit: {sp['dotted']}\npath: {node['sources'][0]['path']}\nsha: {sha}\n"
            f"test_files: {tf}\n\n" + "\n".join(log) + f"\nVERDICT: {verdict} {reason}\n")
    OUT.mkdir(parents=True, exist_ok=True)
    data = text.encode("utf-8")
    (OUT / f"{nid}.txt").write_bytes(data)
    return {"node_id": nid, "sha": sha, "probe": "import+pytest", "command": " && ".join(cmds) or "n/a",
            "exit_code": exit_code, "started_at": started, "finished_at": now(),
            "output_sha256": hashlib.sha256(data).hexdigest(), "output_tail": text[-1500:],
            "verdict": verdict, "kind": "pytest", "reason": reason if verdict == "FAIL" else "", "unit": sp["dotted"]}


def leaves():
    seed = json.loads(SEED.read_text(encoding="utf-8"))
    ids = {n["id"]: n for n in seed["nodes"]}
    par = {n["parent"] for n in seed["nodes"]}

    def zone(i):
        ch, c = [], i
        while c in ids and c not in ch:
            ch.append(c)
            c = ids[c]["parent"]
        return ch[-2] if len(ch) >= 2 else i
    return [n for n in seed["nodes"] if n["id"] not in par and zone(n["id"]) in ZONES and n["status"] in ("code", "branch")]


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--only")
    ap.add_argument("--workers", type=int, default=3)
    a = ap.parse_args()
    ns = leaves()
    if a.only:
        want = set(a.only.split(","))
        ns = [n for n in ns if n["id"] in want]
    sha = sha_head()
    with ThreadPoolExecutor(min(a.workers, 3)) as ex:
        rec = [r for r in ex.map(lambda n: probe(n, sha), ns) if r]
    full = EVID / f"{LANE}.json"
    old = json.loads(full.read_text(encoding="utf-8")) if full.exists() else []
    ids = {r["node_id"] for r in rec}
    rec = [r for r in old if r["node_id"] not in ids] + rec
    full.write_text(json.dumps(rec, ensure_ascii=False, indent=1), encoding="utf-8")
    print(len(rec), Counter(r["verdict"] for r in rec), Counter(r["reason"] for r in rec if r["verdict"] == "FAIL"))


if __name__ == "__main__":
    main()
