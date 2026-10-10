"""North Star ladder harness (owner 10.10.2026): FIND -> CONFIRM -> FIX -> VERIFY -> REVIEW -> LEARN, on the lab Bossman.

    python tools/north_star/cycle.py zones  --evidence <dir>
    python tools/north_star/cycle.py run    --evidence <dir> --cycle c01 --zone 0 --finder nvidia-nim --fixer glm-flash \
                                            --judge nvidia:moonshotai/kimi-k3 --reviewer gemini:gemini-3.5-flash
    python tools/north_star/cycle.py restart --evidence <dir>      # restart/resume proof of the lab backend

Who does what (nothing here writes product code, a test or a holdout):
  FIND     a Bossman coding task (product path POST /api/coding-tasks, isolated worktree, cloud worker) is told only
           "Improve Bossman: audit <module>, find ONE real defect, reproduce it with ONE failing test, do not fix it".
           The module comes from a seeded, pre-recorded list (`zones`), the defect is the worker's.
  CONFIRM  the finder's test runs on a clean checkout of the base: it must FAIL (Bossman's own guarded runner); a judge
           model from another vendor must agree the expectation follows from the module's contract. The finder's
           version of the test file becomes a hidden repro file in a verification-base commit (refs/ns/verify-<cycle>
           of the lab clone). The fixer never sees it.
  FIX      a second Bossman coding task gets only the finder's defect report (its own words) and fixes the module in
           its own isolated worktree on the ORIGINAL base.
  VERIFY   bossman_v3.self_improvement.verifier.verify (the product's RESULT_VERIFIER): clean checkout of the
           verification base, static review, task tests = the hidden repro (must fail before, pass after), no
           regression in the zone's test file, test inventory kept.
  REVIEW   protocol.review_prompt/validate_review: a reviewer model that is not the fixer must accept with no findings.
  LEARN    only after VERIFY PASS and REVIEW accept: POST /api/coding-recipes (VERIFIED, verifier = external tool).
Every stage is recorded on its own in <evidence>/<cycle>/cycle.json and never upgraded by a later stage.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import platform
import random
import re
import subprocess
import sys
import tempfile
import time
from pathlib import Path

LAB = Path(os.environ.get("NS_LAB", r"C:\Users\asd\Bossman\ns-lab-20261010"))
REPO = LAB / "repo"
DATA = LAB / "data"
URL = os.environ.get("NS_URL", "http://127.0.0.1:8835")
PY = os.environ.get("NS_PY", r"C:\Users\asd\AppData\Local\Programs\Python\Python312\python.exe")
PROJECT = "north-star-20261010"
SEED = "north-star-20261010"
HERE = Path(__file__).resolve().parent
sys.path[:0] = [str(HERE), str(REPO / "command-center"), str(REPO / "bossman-core"), str(REPO)]

import providers  # noqa: E402

TERMINAL = ("completed", "failed", "blocked", "cancelled")


def now() -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())


def sh(*args: str, cwd: Path | None = None, check: bool = True, inp: bytes | None = None) -> str:
    res = subprocess.run(list(args), cwd=str(cwd) if cwd else None, input=inp, capture_output=True)
    out = (res.stdout or b"").decode("utf-8", "replace")
    if check and res.returncode:
        raise RuntimeError(f"{args[:3]} rc={res.returncode}: {(res.stderr or b'').decode('utf-8', 'replace')[-600:]}")
    return out


def gitc(repo: Path, *args: str, check: bool = True, inp: bytes | None = None) -> str:
    return sh("git", "-c", "core.autocrlf=false", "-c", "user.name=Bossman North Star Harness",
              "-c", "user.email=ns-harness@localhost", "-C", str(repo), *args, check=check, inp=inp)


def client():
    from bcc.terminal_cli.api_client import Client, discover
    return Client(discover(URL, str(DATA)), timeout=120.0)


def lf(diff: str | None) -> str:
    """Task diffs come from a CRLF sandbox (global core.autocrlf=true); the repo stores LF. Mechanical EOL fix only."""
    return (diff or "").replace("\r\n", "\n")


def jwrite(path: Path, value) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, ensure_ascii=False, indent=1), encoding="utf-8")


def jread(path: Path):
    return json.loads(path.read_text(encoding="utf-8")) if path.is_file() else None


# ------------------------------------------------------------------------------------------------ zones
def zones() -> list[dict]:
    """bcc modules of 60..320 lines with a dedicated command-center/tests/test_<stem>.py, seeded order."""
    root = REPO / "command-center"
    out = []
    for mod in sorted((root / "bcc").rglob("*.py")):
        if mod.name.startswith("__"):
            continue
        test = root / "tests" / f"test_{mod.stem}.py"
        if not test.is_file():
            continue
        n = len(mod.read_text(encoding="utf-8", errors="replace").splitlines())
        if 60 <= n <= 320:
            out.append({"target": mod.relative_to(REPO).as_posix(), "test": test.relative_to(REPO).as_posix(),
                        "lines": n})
    random.Random(SEED).shuffle(out)
    return out


# ------------------------------------------------------------------------------------------------ tasks
def start_task(instruction: str, allowed: list[str], verify: list[str], worker: str, use_memory: bool = True) -> str:
    body = {"instruction": instruction, "source_repo": str(REPO), "allowed_paths": allowed, "verify_tests": verify,
            "project_id": PROJECT, "timeout_seconds": 1800, "worker": worker, "use_memory": use_memory}
    with client() as c:
        return c.post("/api/coding-tasks", body)["id"]


def wait_task(task_id: str, timeout: float = 2400) -> dict:
    deadline = time.monotonic() + timeout
    with client() as c:
        rec = c.get(f"/api/coding-tasks/{task_id}")
        while rec.get("status") not in TERMINAL and time.monotonic() < deadline:
            time.sleep(15)
            rec = c.get(f"/api/coding-tasks/{task_id}")
    return rec


FIND_WISH = (
    "Owner request: Improve Bossman. Audit the module {target} (read it fully first). Find ONE real, bounded defect in "
    "it: an input on which a public function returns a wrong result, raises an unexpected exception, or violates its "
    "own docstring/contract. You may NOT change {target}; do NOT fix anything. Reproduce the defect by adding ONE new "
    "test function at the end of {test} that FAILS on the current code because of the defect and would pass once it is "
    "fixed. The test must call the module's public API, must not use network, wall-clock time, randomness or machine "
    "specifics, and its expected value must follow from the module's docstring, names or established behaviour, not "
    "from taste. Run {test} with run_tests: your new test must fail for the reason you describe, every other test in "
    "the file must still pass. If you find no real defect, change nothing and answer NO_DEFECT. Final answer in this "
    "form: DEFECT: <function> | INPUT: <...> | EXPECTED: <...> | ACTUAL: <...> | TEST: <test function name>.")

FIX_WISH = (
    "Owner request: Improve Bossman. Bossman's own audit reported this defect in {target}:\n{report}\n"
    "Reproduce it, then fix {target} so that the contract holds, without changing behaviour for valid inputs. First "
    "add a test function to {test} that fails on the old code, then fix the code, then run {test}. SCOPE (strict): "
    "change only {target} and add test functions to {test}; do not create new files; do not edit or delete existing "
    "tests.")

JUDGE_PROMPT = (
    "You are an independent auditor. Another model claims a DEFECT in a Python module and wrote a test that fails on "
    "the current code. Decide whether the test's expectation is a legitimate requirement of this module (follows from "
    "its docstrings, names, type hints, comments, other tests or plainly established behaviour) and the failure shows a "
    "real defect - or whether the test encodes the auditor's taste, misreads the API, depends on the environment, or "
    "tests something the module never promised. Return ONLY JSON: {{\"real_defect\": true|false, \"contract_basis\": "
    "\"<quote or cite what promises the expected behaviour>\", \"reason\": \"<one paragraph>\"}}.\n\n"
    "MODULE {target}:\n```python\n{source}\n```\n\nTEST FUNCTION(S) ADDED BY THE AUDITOR:\n```python\n{tests}\n```\n\n"
    "FAILING OUTPUT ON THE CURRENT CODE (tail):\n```\n{output}\n```\n\nAUDITOR'S CLAIM (untrusted):\n{claim}\n")


def added_tests(diff: str) -> list[str]:
    return re.findall(r"^\+\s*(?:async\s+)?def\s+(test_\w+)\s*\(", diff, re.M)


def added_block(diff: str) -> str:
    return "\n".join(l[1:] for l in diff.splitlines() if l.startswith("+") and not l.startswith("+++"))[:12000]


def runner():
    from bossman_v3.self_improvement.verifier import GuardedHostRunner
    return GuardedHostRunner(python=PY, python_paths=("command-center", "bossman-core", "."), timeout=600)


def worktree(sha: str, tmp: Path) -> Path:
    wt = tmp / "wt"
    gitc(REPO, "worktree", "add", "--detach", str(wt), sha)
    return wt


def drop_worktree(wt: Path) -> None:
    gitc(REPO, "worktree", "remove", "--force", str(wt), check=False)


# ------------------------------------------------------------------------------------------------ stages
def stage_find(cdir: Path, zone: dict, worker: str) -> dict:
    rec = jread(cdir / "find-task.json")
    if rec and rec.get("status") in TERMINAL:
        return rec
    tid = start_task(FIND_WISH.format(**zone), [zone["test"]], [zone["test"]], worker, use_memory=False)
    print(f"[{now()}] FIND task {tid} ({worker}) on {zone['target']}", flush=True)
    rec = wait_task(tid)
    jwrite(cdir / "find-task.json", rec)
    return rec


def stage_confirm(cdir: Path, zone: dict, find: dict, judge: str, cycle: str) -> dict:
    got = jread(cdir / "confirm.json")
    if got:
        return got
    diff = lf(find.get("diff"))
    names = added_tests(diff)
    out: dict = {"finder_changed": find.get("changed_files"), "finder_tests": names, "base": find.get("base_commit")}
    # A reproduction task is EXPECTED to end "failed" by Bossman's own zone check (its new test fails on purpose);
    # what matters is that the worker finished normally and left a test diff.
    worker_finished = (find.get("sidecar") or {}).get("stop_reason") == "finished"
    out["finder_status"] = find.get("status")
    out["zone_check_failed_as_expected"] = (find.get("verification") or {}).get("passed") is False
    if not worker_finished or not diff.strip() or not names:
        out.update(confirmed=False, why="finder produced no test (status %s, stop %s)"
                   % (find.get("status"), (find.get("sidecar") or {}).get("stop_reason")))
        jwrite(cdir / "confirm.json", out)
        return out
    if set(find.get("changed_files") or []) != {zone["test"]}:
        out.update(confirmed=False, why="finder edited files outside the test file")
        jwrite(cdir / "confirm.json", out)
        return out
    repro = f"command-center/tests/test_nsrepro_{cycle}.py"
    tmp = Path(tempfile.mkdtemp(prefix="ns-confirm-"))
    wt = worktree(find["base_commit"], tmp)
    try:
        gitc(wt, "apply", "--whitespace=nowarn", "-", inp=(diff if diff.endswith("\n") else diff + "\n").encode())
        text = (wt / zone["test"]).read_text(encoding="utf-8")
        gitc(wt, "checkout", "--", zone["test"])
        (wt / repro).write_text(text, encoding="utf-8", newline="\n")
        gitc(wt, "add", repro)
        gitc(wt, "commit", "-q", "-m", f"north-star {cycle}: hidden repro written by the Bossman finder task {find['id']}")
        vbase = gitc(wt, "rev-parse", "HEAD").strip()
        gitc(REPO, "update-ref", f"refs/ns/verify-{cycle}", vbase)
        run = runner().run(wt, [repro], cdir / "confirm-run")
    finally:
        drop_worktree(wt)
    finder_ids = {k: v for k, v in run["tests"].items() if any(k.endswith("." + n) or k.endswith("::" + n) for n in names)}
    others_bad = {k: v for k, v in run["tests"].items() if k not in finder_ids and v != "PASS"}
    out.update(repro=repro, verify_base=vbase, run_status=run["status"], finder_results=finder_ids,
               other_failures=others_bad)
    output = (cdir / "confirm-run" / "output.txt").read_text(encoding="utf-8", errors="replace")[-3500:]
    fails = bool(finder_ids) and all(v in ("FAIL", "ERROR") for v in finder_ids.values())
    out["DEFECT_REPRODUCED"] = fails and not others_bad
    if not out["DEFECT_REPRODUCED"]:
        out.update(confirmed=False, why="finder test does not fail cleanly on the base")
        jwrite(cdir / "confirm.json", out)
        return out
    prov, model = judge.split(":", 1)
    source = (REPO / zone["target"]).read_text(encoding="utf-8")[:60000]
    claim = str((find.get("sidecar") or {}).get("summary") or "")[:1500]
    try:
        resp = providers.chat(prov, model, JUDGE_PROMPT.format(target=zone["target"], source=source,
                                                               tests=added_block(diff), output=output, claim=claim),
                              ledger=cdir.parent / "spend.jsonl", purpose=f"{cycle}:judge")
        verdict = providers.extract_json(resp["text"])
    except Exception as exc:  # noqa: BLE001
        verdict = {"real_defect": None, "error": str(exc)[:300]}
    out["judge"] = {"model": judge, **verdict}
    out["confirmed"] = verdict.get("real_defect") is True
    out["why"] = "judge agrees" if out["confirmed"] else "judge does not confirm a real defect"
    jwrite(cdir / "confirm.json", out)
    return out


def stage_fix(cdir: Path, zone: dict, find: dict, worker: str, tag: str = "") -> dict:
    name = f"fix-task{tag}.json"
    rec = jread(cdir / name)
    if rec and rec.get("status") in TERMINAL:
        return rec
    report = str((find.get("sidecar") or {}).get("summary") or "")[:1500]
    tid = start_task(FIX_WISH.format(target=zone["target"], test=zone["test"], report=report),
                     [zone["target"], zone["test"]], [zone["test"]], worker, use_memory=True)
    print(f"[{now()}] FIX task {tid} ({worker})", flush=True)
    rec = wait_task(tid)
    jwrite(cdir / name, rec)
    return rec


def stage_verify(cdir: Path, zone: dict, conf: dict, fix: dict, cycle: str, tag: str = "") -> dict:
    from bossman_v3.self_improvement import verifier as v
    got = jread(cdir / f"verify{tag}" / "verdict.json")
    if got:
        return got
    task = {"id": f"{cycle}{tag}", "goal": "fix the defect reproduced by the hidden repro", "tests": [conf["repro"]],
            "editable": [zone["target"], zone["test"]]}
    ev = {"model": fix.get("model"), "worker": fix.get("worker"), "backend": "bossman_coding",
          "model_kind": "REAL_MODEL", "task_id": fix.get("id")}
    return v.verify(task=task, source=REPO, base_sha=conf["verify_base"], diff=lf(fix.get("diff")),
                    tests=[conf["repro"], zone["test"]], evidence=ev, out=cdir / f"verify{tag}", runner=runner(),
                    holdout=(conf["repro"],), require_new_regression=False)


def stage_review(cdir: Path, conf: dict, fix: dict, reviewer: str, cycle: str, tag: str = "") -> dict:
    from bossman_v3.self_improvement import protocol as proto
    got = jread(cdir / f"review{tag}.json")
    if got:
        return got
    diff = lf(fix.get("diff"))
    tmp = Path(tempfile.mkdtemp(prefix="ns-review-"))
    wt = worktree(conf["verify_base"], tmp)
    try:
        gitc(wt, "apply", "--whitespace=nowarn", "-", inp=(diff if diff.endswith("\n") else diff + "\n").encode())
        sources = {p: (wt / p).read_text(encoding="utf-8", errors="replace") for p in (fix.get("changed_files") or [])
                   if (wt / p).is_file()}
    finally:
        drop_worktree(wt)
    goal = "Fix this defect reported by Bossman's audit: " + str((jread(cdir / "find-task.json").get("sidecar") or {})
                                                                 .get("summary") or "")[:1200]
    ctx = proto.review_input(conf["verify_base"], diff, sources, goal)
    prov, model = reviewer.split(":", 1)
    out: dict = {"reviewer": reviewer, "fixer_model": fix.get("model")}
    if proto.model_identity(model) == proto.model_identity(str(fix.get("model") or "")):
        out.update(accepted=False, why="reviewer model equals fixer model: not independent")
        jwrite(cdir / f"review{tag}.json", out)
        return out
    try:
        resp = providers.chat(prov, model, proto.review_prompt(ctx), ledger=cdir.parent / "spend.jsonl",
                              purpose=f"{cycle}{tag}:review", max_tokens=6000)
        parsed = providers.extract_json(resp["text"])
        out["response"] = parsed
        proto.validate_review(parsed, ctx)
        out.update(accepted=True, why="accept with no findings")
    except Exception as exc:  # noqa: BLE001
        out.update(accepted=False, why=str(exc)[:400])
    jwrite(cdir / f"review{tag}.json", out)
    return out


def stage_learn(cdir: Path, zone: dict, conf: dict, fix: dict, verdict: dict, cycle: str) -> dict:
    got = jread(cdir / "recipe.json")
    if got:
        return got
    added = [l[1:].rstrip() for l in (fix.get("diff") or "").splitlines()
             if l.startswith("+") and not l.startswith("+++")][:18]
    summary = str((fix.get("sidecar") or {}).get("summary") or "")[:700]
    report = str((jread(cdir / "find-task.json").get("sidecar") or {}).get("summary") or "")[:1100]
    who = f"bossman-worker:{fix.get('worker')}/{fix.get('model')}"
    recipe = {
        "id": f"ns-selfrepair-{cycle}-{fix['id']}", "title": f"Bossman self-repair: {zone['target']}",
        "symptom": report or "defect reported by Bossman's audit task",
        "cause": f"Found by Bossman audit task {jread(cdir / 'find-task.json')['id']}; "
                 f"hidden repro {conf['repro']} failed on base {conf['verify_base'][:12]}",
        "diagnosis": "Reproduced by the finder's own failing test; confirmed by an independent judge model.",
        "action": (f"Bossman worker {fix.get('worker')} ({fix.get('model')}, task {fix['id']}): {summary} | changed: "
                   + " ; ".join(added))[:1900],
        "counterexample": "Keep valid inputs unchanged: the zone's existing tests pass before and after.",
        "required_check": {"tool": "run_tests", "args": {"paths": [zone["test"]]}},
        "applies_when": {"project_id": PROJECT, "language": "python",
                         "keywords": sorted({w for w in re.findall(r"[a-z_]{4,}", report.lower())})[:12]
                         or ["defect", "contract"]},
        "steps": [{"tool": "read_file", "args": {"path": zone["target"]}},
                  {"tool": "run_tests", "args": {"paths": [zone["test"]]}}],
        "provenance": {"who": who, "source": "student", "assistance_level": "none",
                       "what": "verified self-repair recipe (north star 10.10)", "code_refs": [zone["target"]],
                       "test_refs": [conf["repro"]], "evidence_refs": [f"coding-task:{fix['id']}"]},
        "status": "VERIFIED", "project_id": PROJECT, "scope": "project",
    }
    evidence = {"source": f"{conf['repro']}@{conf['verify_base']}+task:{fix['id']}", "expected": "PASS",
                "actual": "PASS" if verdict.get("verdict") == "PASS" else verdict.get("verdict"),
                "head_sha": conf["verify_base"],
                "environment": f"owner-pc {platform.system()} {platform.release()} python {platform.python_version()}"}
    ver = {"principal_id": "tool:bossman-evolution-result-verifier", "independence_class": "external_tool"}
    try:
        with client() as c:
            saved = c.post("/api/coding-recipes", {"recipe": recipe, "evidence": evidence, "verifier": ver,
                                                   "project_id": PROJECT, "scope": "project"})
    except Exception as exc:  # noqa: BLE001
        saved = {"error": str(exc)[:600]}
    out = {"recipe": recipe, "evidence": evidence, "verifier": ver, "saved": saved}
    jwrite(cdir / "recipe.json", out)
    return out


def cmd_run(a) -> int:
    ev = Path(a.evidence).resolve()
    zl = jread(ev / "zones.json") or {}
    zone = zl["zones"][a.zone]
    cdir = ev / a.cycle
    cdir.mkdir(parents=True, exist_ok=True)
    state = jread(cdir / "cycle.json") or {"cycle": a.cycle, "zone": zone, "started": now(), "roles": {}}
    state["roles"].update(finder=a.finder, judge=a.judge)
    st = state.setdefault("stages", {})
    find = stage_find(cdir, zone, a.finder)
    st["FIND_TASK"] = {"task": find.get("id"), "status": find.get("status"), "steps": (find.get("sidecar") or {}).get("steps"),
                       "changed": find.get("changed_files"), "base": find.get("base_commit")}
    conf = stage_confirm(cdir, zone, find, a.judge, a.cycle)
    st["DEFECT_REPRODUCED"] = bool(conf.get("DEFECT_REPRODUCED"))
    st["DEFECT_CONFIRMED_BY_JUDGE"] = bool(conf.get("confirmed"))
    jwrite(cdir / "cycle.json", state)
    if not conf.get("confirmed"):
        state.update(result="NO_CONFIRMED_DEFECT", why=conf.get("why"), finished=now())
        jwrite(cdir / "cycle.json", state)
        print(json.dumps(state, ensure_ascii=False, indent=1))
        return 1
    if not a.fixer:
        state.update(result="DEFECT_CONFIRMED_FIX_PENDING", finished=now())
        jwrite(cdir / "cycle.json", state)
        print(json.dumps(state, ensure_ascii=False, indent=1))
        return 0
    tag = f"-{a.fix_tag}" if a.fix_tag else ""
    state["roles"][f"fixer{tag}"] = a.fixer
    state["roles"][f"reviewer{tag}"] = a.reviewer
    fix = stage_fix(cdir, zone, find, a.fixer, tag)
    mem = fix.get("memory") or {}
    st[f"FIX_TASK{tag}"] = {"task": fix.get("id"), "status": fix.get("status"), "model": fix.get("model"),
                            "steps": (fix.get("sidecar") or {}).get("steps"), "changed": fix.get("changed_files"),
                            "zone_check": {k: (fix.get("verification") or {}).get(k) for k in ("ran", "runner", "passed")},
                            "recipes_recalled": mem.get("recipe_ids"), "error": (fix.get("error") or "")[:300]}
    st[f"MODEL_PATCH_CREATED{tag}"] = fix.get("status") == "completed" and zone["target"] in (fix.get("changed_files") or [])
    verdict = stage_verify(cdir, zone, conf, fix, a.cycle, tag)
    st[f"RESULT_VERIFIER{tag}"] = {"verdict": verdict.get("verdict"), "reasons": verdict.get("reasons"),
                                   "counts_as_student_success": verdict.get("counts_as_student_success")}
    review = stage_review(cdir, conf, fix, a.reviewer, a.cycle, tag) if verdict.get("verdict") == "PASS" else \
        {"accepted": False, "why": "not reviewed: verifier did not PASS"}
    st[f"REVIEW{tag}"] = {k: review.get(k) for k in ("reviewer", "accepted", "why")}
    passed = bool(st[f"MODEL_PATCH_CREATED{tag}"] and verdict.get("verdict") == "PASS" and review.get("accepted"))
    st[f"INDEPENDENT_VERIFICATION_PASS{tag}"] = passed
    if passed and not a.no_learn:
        saved = stage_learn(cdir, zone, conf, fix, verdict, a.cycle)
        st["EXPERIENCE_SAVED"] = {"recipe_id": (saved.get("saved") or {}).get("recipe_id"),
                                  "lesson_id": (saved.get("saved") or {}).get("lesson_id"),
                                  "error": (saved.get("saved") or {}).get("error")}
    state.update(result="SELF_REPAIR_CYCLE_PASS" if passed else "FIX_NOT_VERIFIED", finished=now())
    jwrite(cdir / "cycle.json", state)
    print(json.dumps(state, ensure_ascii=False, indent=1))
    return 0 if passed else 1


def cmd_zones(a) -> int:
    ev = Path(a.evidence).resolve()
    head = gitc(REPO, "rev-parse", "HEAD").strip()
    zl = {"seed": SEED, "repo_head": head, "rule": "command-center/bcc/**/*.py, 60..320 lines, with "
          "command-center/tests/test_<stem>.py; random.Random(seed).shuffle", "recorded": now(), "zones": zones()}
    if (ev / "zones.json").is_file():
        print("zones.json exists; not overwritten")
        return 0
    jwrite(ev / "zones.json", zl)
    print(len(zl["zones"]), "zones;", [z["target"] for z in zl["zones"][:12]])
    return 0


def cmd_restart(a) -> int:
    """Kill the lab backend's process tree and start it again; then read the recipes back (restart/resume proof)."""
    import psutil
    ev = Path(a.evidence).resolve()
    pid = int((LAB / "backend.pid").read_text().strip())
    rec: dict = {"at": now(), "old_pid": pid}
    with client() as c:
        rec["recipes_before"] = [i.get("id") or i.get("recipe_id") for i in c.get(f"/api/coding-recipes?project_id={PROJECT}")["items"]]
    try:
        p = psutil.Process(pid)
        for ch in p.children(recursive=True):
            ch.kill()
        p.kill()
        p.wait(20)
    except psutil.Error as exc:
        rec["kill_error"] = str(exc)
    time.sleep(3)
    rec["start"] = sh("powershell", "-NoProfile", "-File", str(HERE / "start_lab.ps1")).strip()
    t0 = time.monotonic()
    while time.monotonic() - t0 < 120:
        try:
            with client() as c:
                rec["health"] = c.get("/health/live")
                rec["recipes_after"] = [i.get("id") or i.get("recipe_id")
                                        for i in c.get(f"/api/coding-recipes?project_id={PROJECT}")["items"]]
                break
        except Exception:  # noqa: BLE001
            time.sleep(3)
    rec["seconds_to_ready"] = round(time.monotonic() - t0, 1)
    rec["recipes_survived"] = sorted(rec.get("recipes_before") or []) == sorted(rec.get("recipes_after") or [])
    path = ev / "restarts.jsonl"
    with path.open("a", encoding="utf-8") as fh:
        fh.write(json.dumps(rec, ensure_ascii=False) + "\n")
    print(json.dumps(rec, ensure_ascii=False, indent=1))
    return 0 if rec["recipes_survived"] and rec.get("health") else 1


def cmd_negctl(a) -> int:
    """Deliberately bad patches (harness-authored NEGATIVE CONTROLS, never counted as learning) through the same
    RESULT_VERIFIER: (1) a no-op edit of the target, (2) the no-op plus a weakened existing test. Both must be
    rejected (FAIL / INVALID_TEST); a PASS here would mean the gate is broken."""
    from bossman_v3.self_improvement import verifier as v
    ev = Path(a.evidence).resolve()
    cdir = ev / a.cycle
    state = jread(cdir / "cycle.json")
    conf = jread(cdir / "confirm.json")
    zone = state["zone"]
    tmp = Path(tempfile.mkdtemp(prefix="ns-neg-"))
    wt = worktree(conf["base"], tmp)
    diffs = {}
    try:
        tgt = wt / zone["target"]
        tgt.write_text(tgt.read_text(encoding="utf-8") + "\n# reviewed\n", encoding="utf-8", newline="\n")
        diffs["noop_target"] = gitc(wt, "diff")
        test = wt / zone["test"]
        lines = test.read_text(encoding="utf-8").splitlines()
        idx = next((i for i, l in enumerate(lines) if l.strip().startswith("assert ")), None)
        if idx is not None:
            lines[idx] = lines[idx][: len(lines[idx]) - len(lines[idx].lstrip())] + "assert True"
            test.write_text("\n".join(lines) + "\n", encoding="utf-8", newline="\n")
            diffs["weakened_test"] = gitc(wt, "diff")
    finally:
        drop_worktree(wt)
    out = {"cycle": a.cycle, "author": "north-star harness (deliberately bad patch; negative control only)"}
    for name, diff in diffs.items():
        task = {"id": f"{a.cycle}-neg-{name}", "goal": "negative control", "tests": [conf["repro"]],
                "editable": [zone["target"], zone["test"]]}
        verdict = v.verify(task=task, source=REPO, base_sha=conf["verify_base"], diff=diff,
                           tests=[conf["repro"], zone["test"]], evidence={"model": "harness-bad-patch",
                                                                          "model_kind": "REAL_MODEL"},
                           out=cdir / f"negctl-{name}", runner=runner(), holdout=(conf["repro"],),
                           require_new_regression=False)
        out[name] = {"verdict": verdict["verdict"], "reasons": verdict["reasons"],
                     "rejected": verdict["verdict"] != "PASS"}
    out["BAD_PATCH_REJECTED"] = all(x["rejected"] for k, x in out.items() if isinstance(x, dict))
    jwrite(cdir / "negctl.json", out)
    print(json.dumps(out, ensure_ascii=False, indent=1))
    return 0 if out["BAD_PATCH_REJECTED"] else 1


def main(argv=None) -> int:
    ap = argparse.ArgumentParser()
    sub = ap.add_subparsers(dest="cmd", required=True)
    z = sub.add_parser("zones")
    z.add_argument("--evidence", required=True)
    r = sub.add_parser("run")
    r.add_argument("--evidence", required=True)
    r.add_argument("--cycle", required=True)
    r.add_argument("--zone", type=int, required=True)
    r.add_argument("--finder", required=True)
    r.add_argument("--judge", required=True)
    r.add_argument("--fixer")
    r.add_argument("--fix-tag", default="")
    r.add_argument("--reviewer", default="gemini:gemini-3.5-flash")
    r.add_argument("--no-learn", action="store_true")
    s = sub.add_parser("restart")
    s.add_argument("--evidence", required=True)
    n = sub.add_parser("negctl")
    n.add_argument("--evidence", required=True)
    n.add_argument("--cycle", required=True)
    a = ap.parse_args(argv)
    return {"zones": cmd_zones, "run": cmd_run, "restart": cmd_restart, "negctl": cmd_negctl}[a.cmd](a)


if __name__ == "__main__":
    sys.exit(main())
