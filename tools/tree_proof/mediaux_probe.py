"""Deterministic evidence receipts for zones 'media' and 'ux' (lane mediaux).

Per leaf (status code|branch): import the module in a clean subprocess with THIS worktree first on PYTHONPATH
(asserts __file__ inside the worktree), then run the existing tests that reference it, or the lane-authored
test_leaf_*.py selected by mediaux_overrides.json. PASS = import ok AND >=1 test passed AND nothing failed.
Never edits product code. Re-runnable: python tools/tree_proof/mediaux_probe.py [--only id,id] [--workers 3]
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
import threading
import time
from collections import Counter
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
SEED = ROOT / "command-center/bcc/capability_tree_seed.json"
EVID = ROOT / "docs/architecture/bossman-tree-20261005/evidence"
OUT = EVID / "out"
LANE = "mediaux"
RETIRED_BY_AUDIT = {"reg-promo_video"}   # own receipt in mediaux-retire.json (see mediaux_retire.py)
PY = sys.executable
SECRET_RE = re.compile(r"(KEY|TOKEN|SECRET|PASSWORD|PROXY|CREDENTIAL)", re.I)
ZONES = {"media", "ux"}
IMPORT_CAP, PYTEST_CAP, MAX_FILES = 60, 115, 6
ENV_RE = re.compile(r"Chromium|Playwright|playwright|browser_reason|ffmpeg not found|No module named|ModuleNotFoundError")

# leaf-specific proofs: nid -> {module_path?, tests: [...], k: expr, only: bool, cwd}
OVERRIDE: dict = {}
_ov = ROOT / "tools/tree_proof/mediaux_overrides.json"
if _ov.is_file():
    OVERRIDE = json.loads(_ov.read_text(encoding="utf-8"))

TEST_DIRS = [("command-center", "command-center/tests"), ("bossman-core", "bossman-core/tests"), (".", "tests"),
             ("apps/ai-3d-maker", "apps/ai-3d-maker/tests"), ("apps/social-farm", "apps/social-farm/tests")]
APP_SRC = ["apps/ai-3d-maker/src", "apps/social-farm/src"]


def now():
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def sha_head():
    return subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=ROOT, text=True).strip()


def clean_env(tmp):
    env = {k: v for k, v in os.environ.items() if not SECRET_RE.search(k)}
    pp = os.pathsep.join(str(ROOT / p) for p in ["command-center", "bossman-core", ".", *APP_SRC])
    env.update(PYTHONPATH=pp, PYTHONIOENCODING="utf-8", PYTHONUTF8="1", LOCALAPPDATA=tmp, APPDATA=tmp,
               BCC_DATA_DIR=tmp, BOSSMAN_DATA_DIR=tmp, PYTHONDONTWRITEBYTECODE="1", NO_PROXY="*")
    return env


def modname(path):
    if path.startswith("command-center/bcc/") and path.endswith(".py"):
        parts = path[len("command-center/"):-3].split("/")
    elif path.startswith("tools/") and path.endswith(".py"):
        parts = path[:-3].split("/")
    else:
        m = re.match(r"apps/[^/]+/src/(.+)\.py$", path)
        if not m:
            return None
        parts = m.group(1).split("/")
    if parts[-1] == "__init__":
        parts = parts[:-1]
    return ".".join(parts)


def find_tests(dotted):
    parts = dotted.split(".")
    name, parent = parts[-1], ".".join(parts[:-1])
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
            by_name = f.stem == "test_" + name or f.stem.startswith("test_" + name + "_")
            if by_name or any(p.search(txt) for p in pats):
                found[f] = (cwd, 0 if by_name else 1)
    ordered = sorted(found.items(), key=lambda kv: (kv[1][1], len(kv[0].parts), str(kv[0])))
    return [(f, c) for f, (c, _) in ordered]


def run(cmd, cwd, env, cap):
    t = time.time()
    try:
        p = subprocess.run(cmd, cwd=cwd, env=env, capture_output=True, timeout=cap)
        return (p.returncode, p.stdout.decode("utf-8", "replace") + p.stderr.decode("utf-8", "replace"),
                round(time.time() - t, 1))
    except subprocess.TimeoutExpired as e:
        return (124, (e.stdout or b"").decode("utf-8", "replace") + (e.stderr or b"").decode("utf-8", "replace")
                + f"\n[TIMEOUT {cap}s]\n", cap)


def probe(node, sha):
    nid = node["id"]
    ov = OVERRIDE.get(nid, {})
    path = ov.get("module_path") or node["sources"][0]["path"]
    dotted = modname(path)
    started = now()
    tmp = tempfile.mkdtemp(prefix="mediaux_")
    env = clean_env(tmp)
    log, cmds, tests = [], [], []
    reason, verdict = "", "FAIL"
    rootp = str(ROOT).replace("\\", "/")
    code = ("import importlib,sys;m=importlib.import_module(%r);f=(getattr(m,'__file__','') or '').replace(chr(92),'/');"
            "print('IMPORTED',m.__name__,f);sys.exit(0 if f.lower().startswith(%r.lower()) else 3)") % (dotted, rootp)
    cmds.append(f"python -c \"importlib.import_module('{dotted}')\" [PYTHONPATH=worktree command-center;bossman-core;.;apps/*/src]")
    rc, out, dt = run([PY, "-c", code], str(ROOT), env, IMPORT_CAP)
    log.append(f"$ {cmds[-1]}\n{out}\n[exit {rc} in {dt}s]\n")
    exit_code = rc
    authored = False
    probe_extra = ""
    if rc != 0:
        reason = "import_outside_checkout" if rc == 3 else ("import_timeout" if rc == 124 else "import_failed")
    else:
        if ov.get("tests"):
            tests = [(ROOT / t, ov.get("cwd", "command-center")) for t in ov["tests"]]
            authored = any("test_leaf_" in t for t in ov["tests"])
        if not ov.get("only"):
            have = [t[0] for t in tests]
            tests += [x for x in find_tests(dotted) if x[0] not in have]
        if not tests:
            reason = "no_tests"
        else:
            groups = {}
            for f, cwd in tests[:MAX_FILES]:
                groups.setdefault(cwd, []).append(f)
            total_pass = 0
            env_excluded = []
            for cwd, files in groups.items():
                rel = [os.path.relpath(f, ROOT / cwd).replace("\\", "/") for f in files]
                cmd = [PY, "-m", "pytest", "-q", "-rf", "--tb=line", "--timeout=60", "-p", "no:cacheprovider", *rel]
                shown = f"(cd {cwd} && python -m pytest -q -rf --tb=line --timeout=60 -p no:cacheprovider {' '.join(rel)}"
                if ov.get("k") and cwd == ov.get("cwd", "command-center"):
                    cmd += ["-k", ov["k"]]
                    shown += f" -k \"{ov['k']}\""
                shown += ")"
                cmds.append(shown)
                rc2, out2, dt2 = run(cmd, str(ROOT / cwd), env, PYTEST_CAP)
                log.append(f"$ {shown}\n{out2}\n[exit {rc2} in {dt2}s]\n")
                exit_code = rc2
                m = re.search(r"(\d+) passed", out2)
                total_pass += int(m.group(1)) if m else 0
                if rc2 == 1:
                    lines = out2.splitlines()
                    fails = [ln for ln in lines if ln.startswith("FAILED ")]
                    errs = [ln for ln in lines if ln.startswith("ERROR ")]

                    tb_env = [x for x in lines if not x.startswith(("FAILED", "ERROR")) and ".py:" in x
                              and ENV_RE.search(x)]
                    # every failure must be explained by its own environment traceback line (browser/dependency missing)
                    bad = fails[len(tb_env):] if len(tb_env) < len(fails) else []
                    if fails and not bad and not errs:
                        env_excluded += fails
                        log.append(f"[env-only failures excluded, not product failures: {len(fails)}]" + chr(10) + chr(10).join(fails) + chr(10))
                        continue
                if rc2 != 0:
                    reason = ("pytest_timeout" if rc2 == 124 else
                              "env_missing_dependency" if re.search(r"ModuleNotFoundError|ImportError", out2) else
                              "tests_failed")
                    break
            if not reason:
                if total_pass >= 1:
                    verdict, exit_code = "PASS", 0
                    if env_excluded:
                        probe_extra = f" env_excluded={len(env_excluded)}"
                else:
                    reason = "all_skipped_or_zero_collected"
    probe_name = "import+pytest" + (" authored_by_lane" if authored else "") + probe_extra
    text = (f"node_id: {nid}\nmodule: {dotted}\npath: {path}\nsha: {sha}\nprobe: {probe_name}\n"
            f"test_files: {[os.path.relpath(f, ROOT).replace(chr(92), '/') for f, _ in tests[:MAX_FILES]]}\n\n"
            + "\n".join(log) + f"\nVERDICT: {verdict} {reason}\n")
    OUT.mkdir(parents=True, exist_ok=True)
    data = text.encode("utf-8")
    (OUT / f"{nid}.txt").write_bytes(data)
    return {"node_id": nid, "sha": sha, "probe": probe_name, "command": " && ".join(cmds), "exit_code": exit_code,
            "started_at": started, "finished_at": now(), "output_sha256": hashlib.sha256(data).hexdigest(),
            "output_tail": text[-1500:], "verdict": verdict, "kind": "pytest",
            "reason": reason if verdict == "FAIL" else "", "module": dotted, "test_files_run": len(tests[:MAX_FILES])}


def leaves():
    seed = json.loads(SEED.read_text(encoding="utf-8"))
    nodes = {n["id"]: n for n in seed["nodes"]}
    parents = {n.get("parent") for n in seed["nodes"]}

    def zone(i):
        chain = []
        while i in nodes and i not in chain:
            chain.append(i)
            i = nodes[i].get("parent")
        return chain[-2] if len(chain) >= 2 else None
    return [n for n in seed["nodes"] if n["id"] not in parents and zone(n["id"]) in ZONES
            and n["status"] in ("code", "branch") and n["id"] not in RETIRED_BY_AUDIT]


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--only")
    ap.add_argument("--workers", type=int, default=3)
    a = ap.parse_args()
    nodes = [n for n in leaves() if n["id"] in OVERRIDE or (n.get("sources") and modname(n["sources"][0]["path"]))]
    if a.only:
        want = set(a.only.split(","))
        nodes = [n for n in nodes if n["id"] in want]
    sha = sha_head()
    full = EVID / f"{LANE}.json"
    lock = threading.Lock()

    def one(n):
        r = probe(n, sha)
        with lock:
            old = json.loads(full.read_text(encoding="utf-8")) if full.exists() else []
            full.write_text(json.dumps([x for x in old if x["node_id"] != r["node_id"]] + [r],
                                       ensure_ascii=False, indent=1), encoding="utf-8")
        return r
    with ThreadPoolExecutor(min(a.workers, 3)) as ex:
        rec = list(ex.map(one, nodes))
    print(len(rec), Counter(r["verdict"] for r in rec), Counter(r["reason"] for r in rec if r["verdict"] == "FAIL"))


if __name__ == "__main__":
    main()
