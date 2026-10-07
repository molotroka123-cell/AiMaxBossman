"""Replay the 'reported' green leaves against the INSTALLED Bossman build.

For every leaf whose seed status is 'reported' and that has a PASS receipt in
docs/architecture/bossman-tree-20261005/evidence/<lane>.json, re-run the SAME import and the SAME
pytest files (taken from the clean clone of the installed commit) with the installed
site-packages FIRST on PYTHONPATH and no repo source dir on the path.  A leaf is proven only if
the module under test imports from inside the installed build dir.

    python tools/tree_proof/installed_rerun.py --zone plugins
    python tools/tree_proof/installed_rerun.py --lane ops --limit 5 --workers 3
    python tools/tree_proof/installed_rerun.py --only mod-jeff_settings,cap-1

Writes evidence/installed-<lane>.json (one receipt per leaf) and evidence/out/installed-<node>.txt.
Results of each (cwd, test files, -k) step are cached per run in %TEMP% so shared test files run once
per installed-code environment and the result is attributed to every leaf that used exactly them.
Uses a temp LOCALAPPDATA/APPDATA/BCC_DATA_DIR/HOME and strips KEY/TOKEN/SECRET env; never touches :8801
data and never calls external services.
"""
from __future__ import annotations

import argparse
import concurrent.futures as cf
import hashlib
import json
import os
import re
import shlex
import shutil
import subprocess
import sys
import tempfile
import time
import urllib.request
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
EVID = ROOT / "docs" / "architecture" / "bossman-tree-20261005" / "evidence"
OUT = EVID / "out"
SEED = ROOT / "command-center" / "bcc" / "capability_tree_seed.json"

PY = r"C:\Users\asd\AppData\Local\Programs\Python\Python312\python.exe"
HEALTH = "http://127.0.0.1:8801/health/live"
APP_ROOT = Path(r"C:\Users\asd\Bossman\app")
BUILD_ROOT = Path(r"C:\Users\asd\Bossman\tree-build")


def _installed_sha() -> str:
    """The build installed NOW: IG_INSTALLED_SHA, else the live backend build_sha (was hard-coded to an old build)."""
    env = os.environ.get("IG_INSTALLED_SHA", "").strip()
    if env:
        return env
    import urllib.request
    with urllib.request.urlopen(HEALTH, timeout=10) as r:
        return json.load(r)["build_sha"]


SHA = _installed_sha()
BUILD = "BOSSMAN-Windows-x64-" + SHA[:12]
INSTALLED_BUILD = APP_ROOT / BUILD
SP = INSTALLED_BUILD / "runtime" / "Lib" / "site-packages"
CLONE = BUILD_ROOT / SHA / "src"

LANES = ["plugins", "skills", "ops", "opsplug", "jeffa", "jeffb", "memapps", "mediaux", "agcloud"]
ZONES = ["plugins", "skills", "ops", "jeff", "memapps", "mediaux", "agcloud"]
STEP_TIMEOUT = 280
IMPORT_TIMEOUT = 200
NOT_PRODUCT = {"tests", "scripts", "tools", "conftest"}
SECRET_ENV = re.compile(r"(KEY|TOKEN|SECRET|PASSWORD|PASSWD|CREDENTIAL)", re.I)
SECRET_RE = re.compile(r"(sk-[A-Za-z0-9_\-]{10,}|ghp_[A-Za-z0-9]{10,}|Bearer\s+[A-Za-z0-9._\-]{10,}|AIza[0-9A-Za-z_\-]{20,})")

GUARD = r'''
import importlib.abc, importlib.machinery as _M, json, os, sys
_SP = os.path.normcase(os.path.abspath(os.environ["IG_SP"]))
_PIN = set(filter(None, os.environ.get("IG_PIN", "").split(",")))
_PROD = set(filter(None, os.environ.get("IG_PROD", "").split(",")))
_CLONE = os.path.normcase(os.path.abspath(os.environ["IG_CLONE"]))
_OUT = os.environ["IG_OUT"]


class _Pin(importlib.abc.MetaPathFinder):
    """Top-level product packages that exist in the installed build always resolve there."""
    def find_spec(self, name, path=None, target=None):
        if path is None and name in _PIN:
            return _M.PathFinder.find_spec(name, [os.environ["IG_SP"]])
        return None


sys.meta_path.insert(0, _Pin())
_cwd = os.getcwd()
if _cwd not in sys.path:
    sys.path.append(_cwd)  # LAST: only for non-product helpers (tests.*, tools.*)


def pytest_sessionfinish(session, exitstatus):
    inst, src, aux = [], [], []
    for name, mod in list(sys.modules.items()):
        f = getattr(mod, "__file__", None)
        if not f:
            continue
        top = name.split(".")[0]
        nf = os.path.normcase(os.path.abspath(f))
        if nf.startswith(_SP):
            if top in _PROD:
                inst.append(name)
        elif top in _PROD:
            src.append([name, nf])
        elif nf.startswith(_CLONE) and top not in ("tests", "conftest"):
            aux.append([name, nf])
    with open(_OUT, "w", encoding="utf-8") as fh:
        json.dump({"installed": sorted(inst), "from_source": sorted(src), "aux_source": sorted(aux)[:60]}, fh)
'''

IMPORT_SCRIPT = r'''
import importlib, importlib.util, json, os, sys
sp = os.path.normcase(os.path.abspath(os.environ["IG_SP"]))
res = {}
for m in json.loads(sys.argv[1]):
    try:
        spec = None
        try:
            spec = importlib.util.find_spec(m)
        except (ModuleNotFoundError, ImportError, ValueError):
            spec = None
        if spec is None:
            res[m] = {"status": "not_in_installed_build", "detail": "find_spec none"}
            continue
        mod = importlib.import_module(m)
        f = os.path.normcase(os.path.abspath(getattr(mod, "__file__", "") or ""))
        res[m] = {"status": "ok" if f.startswith(sp) else "resolved_from_source", "file": f}
    except ModuleNotFoundError as e:
        n = e.name or ""
        if n and (m == n or m.startswith(n + ".")):
            res[m] = {"status": "not_in_installed_build", "detail": str(e)}
        else:
            res[m] = {"status": "import_error", "detail": "missing dependency: " + str(e)}
    except BaseException as e:
        res[m] = {"status": "import_error", "detail": type(e).__name__ + ": " + str(e)[:300]}
print("@@RESULT@@" + json.dumps(res))
'''


def now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def scrub(s: str) -> str:
    for k, v in os.environ.items():
        if SECRET_ENV.search(k) and v and len(v) > 7:
            s = s.replace(v, "[REDACTED]")
    return SECRET_RE.sub("[REDACTED]", s)


# ------------------------------------------------------------------ environment

class Env:
    def __init__(self) -> None:
        self.base = Path(tempfile.mkdtemp(prefix="installed_rerun_"))
        self.guard_dir = self.base / "guard"
        self.guard_dir.mkdir()
        (self.guard_dir / "installed_guard.py").write_text(GUARD, "utf-8")
        inst = {p.name for p in SP.iterdir() if (p.is_dir() and (p / "__init__.py").exists()) or p.suffix == ".py"}
        inst = {n[:-3] if n.endswith(".py") else n for n in inst}
        clone_pkgs: set[str] = set()
        roots = [CLONE / "command-center", CLONE / "bossman-core", CLONE]
        roots += list((CLONE / "apps").glob("*/src")) + list((CLONE / "apps").glob("*"))
        for r in roots:
            if r.is_dir():
                for d in r.iterdir():
                    if d.is_dir() and (d / "__init__.py").exists():
                        clone_pkgs.add(d.name)
        clone_pkgs -= NOT_PRODUCT
        self.prod = sorted(clone_pkgs | (inst & {n for n in inst if n.startswith(("bcc", "bossman"))}))
        self.pin = sorted(set(self.prod) & inst)

    def child_env(self, tag: str, out_json: str | None = None) -> dict:
        d = self.base / tag
        for sub in ("local", "roaming", "data", "home", "tmp"):
            (d / sub).mkdir(parents=True, exist_ok=True)
        env = {k: v for k, v in os.environ.items() if not SECRET_ENV.search(k)}
        env.update({
            "PYTHONPATH": f"{SP};{self.guard_dir}",
            "PYTHONSAFEPATH": "1", "PYTHONUTF8": "1", "PYTHONIOENCODING": "utf-8",
            "PYTHONDONTWRITEBYTECODE": "1",
            "LOCALAPPDATA": str(d / "local"), "APPDATA": str(d / "roaming"), "BCC_DATA_DIR": str(d / "data"),
            "HOME": str(d / "home"), "USERPROFILE": str(d / "home"), "TEMP": str(d / "tmp"), "TMP": str(d / "tmp"),
            "IG_SP": str(SP), "IG_CLONE": str(CLONE), "IG_PIN": ",".join(self.pin), "IG_PROD": ",".join(self.prod),
            "IG_OUT": out_json or str(d / "guard.json"),
        })
        return env


def preflight() -> None:
    try:
        h = json.loads(urllib.request.urlopen(HEALTH, timeout=5).read())
    except Exception as exc:  # noqa: BLE001
        sys.exit(f"ABORT: cannot read {HEALTH}: {exc}")
    if h.get("build_sha") != SHA:
        sys.exit(f"ABORT: running build_sha {h.get('build_sha')} != {SHA}")
    head = subprocess.check_output(["git", "-C", str(CLONE), "rev-parse", "HEAD"], text=True).strip()
    if head != SHA:
        sys.exit(f"ABORT: clone HEAD {head} != {SHA}")
    if subprocess.check_output(["git", "-C", str(CLONE), "status", "--porcelain"], text=True).strip():
        print("WARNING: clean clone has local changes:", file=sys.stderr)
    if not SP.is_dir():
        sys.exit(f"ABORT: {SP} missing")


# ------------------------------------------------------------------ leaves

def zone_of(lane: str, node: str) -> str:
    if lane == "plugins" or node.startswith(("plugin-", "plugins-")):
        return "plugins"
    if lane == "skills":
        return "skills"
    if lane == "ops":
        return "ops"
    if lane in ("jeffa", "jeffb"):
        return "jeff"
    return {"memapps": "memapps", "mediaux": "mediaux"}.get(lane, "agcloud")


def out_lane(lane: str, node: str) -> str:
    return "plugins" if node.startswith(("plugin-", "plugins-")) else lane


def parse_command(cmd: str) -> dict:
    mods: list[str] = re.findall(r"import_module\('([\w\.]+)'\)", cmd)
    for m in re.findall(r"python -c \"?import ([\w\., ]+?)\"?(?: \[|\s*&&|$)", cmd):
        mods += [x.strip() for x in m.split(",") if x.strip()]
    steps: list[tuple] = []
    found = re.findall(r"\(cd (\S+) && python -m pytest ([^)]*)\)", cmd)
    if not found:
        m = re.search(r"python(?:\.exe)? -m pytest (.*)$", cmd)
        if m:
            found = [(".", m.group(1))]
    for cwd, args in found:
        toks = shlex.split(args, posix=True)
        files, k, i = [], None, 0
        while i < len(toks):
            t = toks[i]
            if t == "-k":
                k = toks[i + 1]
                i += 2
                continue
            if t == "-p":
                i += 2
                continue
            if not t.startswith("-"):
                files.append(t)
            i += 1
        steps.append((cwd, tuple(files), k))
    return {"modules": list(dict.fromkeys(mods)), "steps": steps,
            "live_only_skipped": ("agcloud_live_probe.py" in cmd) or ("live call" in cmd)}


def collect(args) -> list[dict]:
    seed = json.loads(SEED.read_text("utf-8"))
    reported = {n["id"] for n in seed["nodes"] if n["status"] == "reported"}
    leaves: dict[str, dict] = {}
    for lane in LANES:
        p = EVID / f"{lane}.json"
        if not p.exists():
            continue
        for r in json.loads(p.read_text("utf-8")):
            if r.get("verdict") != "PASS" or r["node_id"] not in reported:
                continue
            nid = r["node_id"]
            if nid in leaves and leaves[nid]["receipt"]["finished_at"] > r["finished_at"]:
                continue
            pc = parse_command(r["command"])
            leaves[nid] = {"id": nid, "lane": out_lane(lane, nid), "src_lane": lane, "zone": zone_of(lane, nid),
                           "receipt": r, **pc, "special": "plugins_probe" if "plugins_probe.py" in r["command"] else None}
    sel = list(leaves.values())
    if args.only:
        want = set(args.only.split(","))
        sel = [x for x in sel if x["id"] in want]
    if args.lane:
        sel = [x for x in sel if x["lane"] == args.lane or x["src_lane"] == args.lane]
    if args.zone:
        sel = [x for x in sel if x["zone"] == args.zone]
    sel.sort(key=lambda x: (ZONES.index(x["zone"]), x["id"]))
    return sel[: args.limit] if args.limit else sel


# ------------------------------------------------------------------ jobs

def run_proc(cmd: list[str], cwd: Path, env: dict, timeout: int) -> dict:
    t0 = now()
    t = time.time()
    try:
        p = subprocess.run(cmd, cwd=str(cwd), env=env, capture_output=True, timeout=timeout)
        out = (p.stdout or b"").decode("utf-8", "replace") + (("\n[stderr]\n" + p.stderr.decode("utf-8", "replace")) if p.stderr else "")
        rc = p.returncode
    except subprocess.TimeoutExpired as e:
        out = ((e.stdout or b"").decode("utf-8", "replace") if isinstance(e.stdout, bytes) else str(e.stdout or "")) + f"\n[TIMEOUT after {timeout}s]"
        rc = -9
    return {"exit": rc, "out": scrub(out), "started": t0, "finished": now(), "secs": round(time.time() - t, 1)}


def step_key(step: tuple) -> str:
    return json.dumps(step)


def run_step(env: Env, step: tuple, idx: int) -> dict:
    cwd_rel, files, k = step
    cwd = CLONE / cwd_rel
    tmpcopy = None
    fl = []
    for f in files:
        if (cwd / f).exists():
            fl.append(f)
            continue
        wt = ROOT / cwd_rel / f
        if wt.exists():  # authored leaf test not in the clean clone: copy into a temp dir, installed code stays first
            tmpcopy = tmpcopy or Path(tempfile.mkdtemp(prefix="leaf_tests_", dir=str(env.base)))
            dst = tmpcopy / f
            dst.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(wt, dst)
            fl.append(str(dst))
        else:
            return {"exit": 4, "out": f"test file missing in clean clone and worktree: {cwd_rel}/{f}", "started": now(),
                    "finished": now(), "secs": 0, "guard": None, "cmd": "", "reason": "test_file_missing"}
    tag = f"step{idx}"
    outj = str(env.base / f"{tag}.guard.json")
    cmd = [PY, "-P", "-m", "pytest", "-q", "-rf", "--tb=short", "--maxfail=5", "-p", "no:cacheprovider", "--timeout=100",
           "-p", "installed_guard", *fl]
    if k:
        cmd += ["-k", k]
    r = run_proc(cmd, cwd, env.child_env(tag, outj), STEP_TIMEOUT)
    g = None
    try:
        g = json.loads(Path(outj).read_text("utf-8"))
    except Exception:  # noqa: BLE001
        pass
    shown = f"(cd {cwd_rel} && python -P -m pytest -q -rf --tb=short --maxfail=5 -p no:cacheprovider --timeout=100 -p installed_guard {' '.join(files)}" + (f' -k "{k}"' if k else "") + ")"
    if r["exit"] == -9:
        reason = "timeout"
    elif g and g["from_source"]:
        reason = "resolved_from_source"
    elif r["exit"] == 0:
        reason = None
    elif r["exit"] == 5:
        reason = "no_tests_collected"
    elif r["exit"] == 2:
        reason = "collection_or_interrupted"
    else:
        reason = "tests_failed"
    return {**r, "guard": g, "cmd": shown, "reason": reason, "copied": bool(tmpcopy)}


def run_import_chunk(env: Env, mods: list[str], idx: int) -> dict:
    cmd = [PY, "-P", "-c", IMPORT_SCRIPT, json.dumps(mods)]
    work = env.base / "importcwd"
    work.mkdir(exist_ok=True)
    r = run_proc(cmd, work, env.child_env(f"imp{idx}"), IMPORT_TIMEOUT)
    m = re.search(r"@@RESULT@@(\{.*\})", r["out"])
    if m:
        try:
            return {m_: {**v, "stdout_exit": r["exit"]} for m_, v in json.loads(m.group(1)).items()}
        except Exception:  # noqa: BLE001
            pass
    if len(mods) == 1:
        return {mods[0]: {"status": "import_error", "detail": f"import process died exit={r['exit']}: {r['out'][-300:]}"}}
    res = {}
    for i, m_ in enumerate(mods):
        res.update(run_import_chunk(env, [m_], idx * 1000 + i))
    return res


# ------------------------------------------------------------------ main

def fmt_step_text(step: tuple, res: dict, shared: int) -> str:
    s = f"$ {res['cmd']}\n[step shared by {shared} leaf(s); exit {res['exit']} in {res['secs']}s; reason={res['reason']}]\n{res['out']}\n"
    g = res.get("guard")
    if g is not None:
        s += (f"[guard] product modules loaded from installed build: {len(g['installed'])}; from repo source: {g['from_source']}; "
              f"aux repo-source helper modules: {len(g['aux_source'])}\n")
    return s


def receipt_for(leaf: dict, imports: dict, step_res: dict, shared: dict) -> tuple[dict, str]:
    r0 = leaf["receipt"]
    lines = [f"node_id: {leaf['id']}", f"installed_build: {BUILD} (source_sha {SHA})", f"site-packages: {SP}",
             f"original receipt: {leaf['src_lane']}.json sha={r0['sha']} command={r0['command']}", ""]
    reasons: list[str] = []
    t0s, t1s, codes = [], [], []
    cmds = []
    if leaf["special"]:
        res = step_res[("special", leaf["id"])]
        lines.append(f"$ {res['cmd']}")
        lines.append(res["out"])
        t0s.append(res["started"]); t1s.append(res["finished"])
        cmds.append(res["cmd"])
        if res["exit"] != 0:
            codes.append(res["exit"])
            reasons.append("timeout" if res["exit"] == -9 else "probe_failed")
        kind = "installed_import"
        probe = "installed import + offline plugin calls (http/monitor: SSRF-block leg only, public network leg not re-run)"
    else:
        kind = "installed_pytest" if leaf["steps"] else "installed_import"
        for m in leaf["modules"]:
            im = imports.get(m, {"status": "import_error", "detail": "no import result"})
            lines.append(f"import {m}: {im['status']} {im.get('file') or im.get('detail', '')}")
            cmds.append(f"python -P -c \"importlib.import_module('{m}')\" [PYTHONPATH=<installed site-packages>]")
            if im["status"] != "ok":
                reasons.append(im["status"])
        lines.append("")
        for st in leaf["steps"]:
            res = step_res[st]
            lines.append(fmt_step_text(st, res, shared[st]))
            t0s.append(res["started"]); t1s.append(res["finished"])
            cmds.append(res["cmd"])
            if res["reason"]:
                reasons.append(res["reason"])
                codes.append(res["exit"] if res["exit"] != 0 else 1)
            g = res.get("guard") or {}
            for m in leaf["modules"]:
                lines.append(f"module under test {m} loaded by this test process from installed build: {m in g.get('installed', [])}")
        if leaf["steps"]:
            probe = (f"installed import+pytest (replay of the same import + {len(leaf['steps'])} pytest step(s); each (cwd, files, -k) "
                     "step is run once per installed-code environment and attributed to every leaf whose receipt used exactly those "
                     "files; leaves sharing each step: " + ", ".join(str(shared[s]) for s in leaf["steps"]) + ")")
        else:
            probe = "installed import"
        if leaf["live_only_skipped"]:
            probe += "; original live/network probe not re-run (no external calls)"
        if any(step_res[s].get("copied") for s in leaf["steps"]):
            probe += "; authored test file copied from worktree into a temp dir (not in clean clone)"
    reasons = list(dict.fromkeys(reasons))
    verdict = "PASS" if not reasons else "FAIL"
    if reasons:
        probe += f" [FAIL: {', '.join(reasons)}]"
    lines.append("")
    lines.append(f"VERDICT {verdict}" + (f" reason={','.join(reasons)}" if reasons else ""))
    data = ("\n".join(lines) + "\n").encode("utf-8")
    fn = OUT / f"installed-{leaf['id']}.txt"
    fn.write_bytes(data)
    rc = {
        "node_id": leaf["id"], "sha": SHA, "probe": probe, "command": " && ".join(cmds) if cmds else "(none)",
        "exit_code": 0 if verdict == "PASS" else (codes[0] if codes else 1),
        "started_at": min(t0s) if t0s else now(), "finished_at": max(t1s) if t1s else now(),
        "output_sha256": hashlib.sha256(data).hexdigest(),
        "output_tail": scrub(data.decode("utf-8", "replace"))[-1500:], "verdict": verdict, "kind": kind,
        "installed_build": BUILD,
    }
    return rc, verdict


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--only"); ap.add_argument("--lane"); ap.add_argument("--zone", choices=ZONES)
    ap.add_argument("--limit", type=int); ap.add_argument("--workers", type=int, default=3)
    ap.add_argument("--no-write", action="store_true")
    args = ap.parse_args()
    args.workers = max(1, min(3, args.workers))
    preflight()
    leaves = collect(args)
    print(f"leaves: {len(leaves)}  workers={args.workers}", flush=True)
    if not leaves:
        return 0
    OUT.mkdir(parents=True, exist_ok=True)
    env = Env()
    print(f"pinned product packages: {env.pin}", flush=True)

    mods = sorted({m for lf in leaves if not lf["special"] for m in lf["modules"]})
    steps = sorted({s for lf in leaves if not lf["special"] for s in lf["steps"]}, key=step_key)
    shared = {s: sum(1 for lf in leaves if s in lf["steps"]) for s in steps}
    imports: dict[str, dict] = {}
    step_res: dict = {}
    chunks = [mods[i:i + 25] for i in range(0, len(mods), 25)]
    with cf.ThreadPoolExecutor(args.workers) as ex:
        futs: dict = {}
        for i, ch in enumerate(chunks):
            futs[ex.submit(run_import_chunk, env, ch, i)] = ("imp", i)
        for i, s in enumerate(steps):
            futs[ex.submit(run_step, env, s, i)] = ("step", s)
        for lf in leaves:
            if lf["special"]:
                def sp(lf=lf):
                    node = lf["id"]
                    cmd = [PY, "-P", str(ROOT / "tools" / "tree_proof" / "installed_plugins_probe.py"), node]
                    res = run_proc(cmd, env.base, env.child_env("sp_" + node), 120)
                    res["cmd"] = "python -P tools/tree_proof/installed_plugins_probe.py " + node + " [PYTHONPATH=<installed site-packages>]"
                    return res
                futs[ex.submit(sp)] = ("special", lf["id"])
        done = 0
        for f in cf.as_completed(futs):
            kind, key = futs[f]
            res = f.result()
            if kind == "imp":
                imports.update(res)
            elif kind == "step":
                step_res[key] = res
            else:
                step_res[("special", key)] = res
            done += 1
            if done % 10 == 0 or done == len(futs):
                print(f"jobs {done}/{len(futs)}", flush=True)

    by_lane: dict[str, list] = {}
    summary: dict = {}
    for lf in leaves:
        rc, v = receipt_for(lf, imports, step_res, shared)
        by_lane.setdefault(lf["lane"], []).append(rc)
        summary.setdefault(lf["lane"], {}).setdefault(v, 0)
        summary[lf["lane"]][v] += 1
        if v == "FAIL":
            m = re.search(r"\[FAIL: ([^\]]*)\]", rc["probe"])
            print(f"FAIL {lf['id']}: {m.group(1) if m else ''}")
    if not args.no_write:
        for lane, rcs in by_lane.items():
            p = EVID / f"installed-{lane}.json"
            old = []
            if p.exists():
                keep = {r["node_id"] for r in rcs}
                old = [r for r in json.loads(p.read_text("utf-8")) if r["node_id"] not in keep]
            p.write_text(json.dumps(sorted(old + rcs, key=lambda r: r["node_id"]), ensure_ascii=False, indent=2) + "\n", "utf-8")
    print(json.dumps(summary, indent=1))
    return 0


if __name__ == "__main__":
    sys.exit(main())
