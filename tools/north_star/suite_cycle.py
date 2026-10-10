"""North Star ladder, SUITE mode (10.10 economy run): the defect is found by Bossman's OWN test pipeline.

    python tools/north_star/suite_cycle.py candidates --evidence <dir> --junit <junit.xml>
    python tools/north_star/suite_cycle.py run --evidence <dir> --cycle s01 --cand 0 \
        --judge gemini:gemini-3.5-flash --fixer glm-flash --reviewer gemini:gemini-3.5-flash

Why this replaces the "audit a module" finder (first 8 attempts, kept under docs/evidence): free and cheap workers
either hit the sidecar's 40-step cap without writing a test (5/8) or wrote a test for a speculative contract that the
independent judge rejected (3/8). A failing test on the base commit is a defect Bossman's pipeline itself reports
(the same signal the 1.5 self-repair inbox is built on); the harness author picks none of it by hand: candidates are
ordered mechanically (failing test file, then test id) and the lowest index not yet attempted is taken.

Stages (each recorded on its own, never upgraded by a later one):
  OBSERVE    pytest of command-center/tests on the lab clone's base commit (suite_scan.ps1) -> junit.xml
  CONFIRM    the failing test file is re-run on a clean checkout of the base by the product's guarded runner
             (must fail again: not flaky) and a judge model of another vendor classifies the failure as a real code
             defect (not environment / obsolete test / flaky).
  FIX        a Bossman coding task (product path, isolated worktree, cloud worker) gets the failing test id and its
             own failure output; scope = the non-test source files named in that traceback + one NEW test file.
  VERIFY     bossman_v3.self_improvement.verifier.verify: base checkout, static review (existing tests may not be
             touched), the failing test must pass after and nothing that passed may fail, inventory kept.
  REVIEW     protocol.review_prompt/validate_review by a reviewer of a different model family than the fixer.
  LEARN      only after VERIFY PASS and REVIEW accept: POST /api/coding-recipes (VERIFIED).
"""
from __future__ import annotations

import argparse
import json
import re
import sys
import tempfile
import xml.etree.ElementTree as ET
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
import cycle as C  # noqa: E402
import providers  # noqa: E402

FIX_WISH = (
    "Owner request: Improve Bossman. Bossman's own test run on the base commit reports a failing test:\n"
    "  {test_id}\nFailure output:\n{output}\n\n"
    "Find the root cause in the source code (the traceback names {sources}) and fix the SOURCE so the test passes "
    "without changing what it checks. Do NOT edit, delete, skip or loosen any existing test. If you add a regression "
    "test, put it in the single new file {newtest}. Run {test_file} with run_tests before you finish. SCOPE (strict): "
    "change only {sources}; the only new file allowed is {newtest}.")

JUDGE_PROMPT = (
    "You are an independent auditor of an automated repair pipeline. A test in a Python repository FAILS on the "
    "current base commit. Classify the failure. Return ONLY JSON: {{\"kind\": \"code_defect\" | \"environment\" | "
    "\"obsolete_test\" | \"flaky\" | \"unclear\", \"fixable_in_source\": true|false, \"reason\": \"<one short "
    "paragraph>\"}}. code_defect = the source code is wrong relative to what the test (and the code's own docs) require "
    "and a source change fixes it. environment = depends on OS, missing tool/key/network, timing, or the machine. "
    "obsolete_test = the test asserts behaviour that other code/docs show has intentionally changed.\n\n"
    "FAILING TEST: {test_id}\n\nFAILURE OUTPUT:\n```\n{output}\n```\n\nTEST FILE ({test_file}):\n```python\n{test}\n```\n\n"
    "SOURCE FILES NAMED IN THE TRACEBACK:\n{sources}\n")

SRC_RX = re.compile(r"([\w./\\-]+\.py):\d+")


def parse_junit(path: Path) -> list[dict]:
    root = ET.parse(path).getroot()
    out = []
    for case in root.iter("testcase"):
        bad = case.find("failure")
        if bad is None:
            bad = case.find("error")
        if bad is None:
            continue
        parts = (case.get("classname") or "").split(".")
        idx = next((i for i, p in enumerate(parts) if p.startswith("test_")), None)
        if idx is None:
            continue
        file = "/".join(parts[: idx + 1]) + ".py"
        text = (bad.get("message") or "") + "\n" + (bad.text or "")
        srcs = []
        for m in SRC_RX.findall(text):
            p = m.replace("\\", "/")
            p = p[p.find("command-center/"):] if "command-center/" in p else p
            name = Path(p).name
            if name.startswith("test_") or "/tests/" in p or p in srcs:
                continue
            if (C.REPO / p).is_file() and (p.startswith("command-center/bcc/") or p.startswith("bossman-core/")
                                           or p.startswith("learning/")):
                srcs.append(p)
        out.append({"test_file": file, "test_id": f"{file}::{case.get('name')}", "classname": case.get("classname"),
                    "name": case.get("name"), "kind": "error" if case.find("error") is not None else "failure",
                    "message": (bad.get("message") or "")[:400], "output": text[-2500:], "sources": srcs[:3]})
    out.sort(key=lambda r: (r["test_file"], r["test_id"]))
    return out


def cmd_candidates(a) -> int:
    ev = Path(a.evidence).resolve()
    cands = parse_junit(Path(a.junit))
    root = ET.parse(a.junit).getroot()
    suite = root.find("testsuite") if root.tag == "testsuites" else root
    meta = {k: suite.get(k) for k in ("tests", "failures", "errors", "skipped", "time")} if suite is not None else {}
    base = C.gitc(C.REPO, "rev-parse", "HEAD").strip()
    usable = [c for c in cands if c["sources"]]
    by_file: dict[str, int] = {}
    for c in usable:
        by_file[c["test_file"]] = by_file.get(c["test_file"], 0) + 1
    C.jwrite(ev / "candidates.json", {"recorded": C.now(), "base": base, "junit_totals": meta,
                                     "failing_total": len(cands), "with_source_in_traceback": len(usable),
                                     "failures_per_test_file": by_file, "candidates": usable})
    print(f"base {base[:12]} totals {meta} failing={len(cands)} usable={len(usable)} files={len(by_file)}")
    for i, c in enumerate(usable[:25]):
        print(i, c["test_id"], "->", c["sources"])
    return 0


def cmd_run(a) -> int:
    ev = Path(a.evidence).resolve()
    cand = json.loads((ev / "candidates.json").read_text(encoding="utf-8"))["candidates"][a.cand]
    cdir = ev / a.cycle
    cdir.mkdir(parents=True, exist_ok=True)
    state = C.jread(cdir / "cycle.json") or {"cycle": a.cycle, "mode": "suite", "candidate": cand,
                                             "started": C.now(), "roles": {}}
    state["roles"].update(judge=a.judge, fixer=a.fixer, reviewer=a.reviewer)
    st = state.setdefault("stages", {})
    base = C.gitc(C.REPO, "rev-parse", "HEAD").strip()
    zone = {"target": cand["sources"][0], "targets": cand["sources"], "test": cand["test_file"]}
    state["zone"] = zone
    st["OBSERVED_BY_BOSSMAN_TEST_RUN"] = {"test": cand["test_id"], "kind": cand["kind"], "base": base}

    # CONFIRM ------------------------------------------------------------------------------------------
    conf = C.jread(cdir / "confirm.json")
    if not conf:
        tmp = Path(tempfile.mkdtemp(prefix="ns-suite-confirm-"))
        wt = C.worktree(base, tmp)
        try:
            run = C.runner().run(wt, [cand["test_file"]], cdir / "confirm-run")
            source_text = "\n\n".join(f"# {p}\n" + (wt / p).read_text(encoding="utf-8", errors="replace")[:14000]
                                      for p in cand["sources"])
            test_text = (wt / cand["test_file"]).read_text(encoding="utf-8", errors="replace")[:14000]
        finally:
            C.drop_worktree(wt)
        ids = {k: v for k, v in run["tests"].items() if k.endswith("::" + cand["name"]) or k.endswith("." + cand["name"])}
        reproduced = bool(ids) and all(v in ("FAIL", "ERROR") for v in ids.values())
        conf = {"repro": cand["test_file"], "verify_base": base, "base": base, "run_status": run["status"],
                "results": ids, "DEFECT_REPRODUCED": reproduced}
        if reproduced:
            prov, model = a.judge.split(":", 1)
            output = (cdir / "confirm-run" / "output.txt").read_text(encoding="utf-8", errors="replace")[-3000:]
            try:
                resp = providers.chat(prov, model, JUDGE_PROMPT.format(test_id=cand["test_id"], output=output,
                                                                       test_file=cand["test_file"], test=test_text,
                                                                       sources=source_text),
                                      ledger=ev / "spend.jsonl", purpose=f"{a.cycle}:judge")
                verdict = providers.extract_json(resp["text"])
            except Exception as exc:  # noqa: BLE001
                verdict = {"kind": "unclear", "error": str(exc)[:300]}
            conf["judge"] = {"model": a.judge, **verdict}
            conf["confirmed"] = verdict.get("kind") == "code_defect" and verdict.get("fixable_in_source") is True
        else:
            conf["confirmed"] = False
            conf["why"] = "failing test does not fail again on a clean checkout (flaky or order dependent)"
        C.jwrite(cdir / "confirm.json", conf)
    st["DEFECT_REPRODUCED"] = bool(conf.get("DEFECT_REPRODUCED"))
    st["DEFECT_CONFIRMED_BY_JUDGE"] = bool(conf.get("confirmed"))
    # the finding text used later by review / learn
    C.jwrite(cdir / "finding.json", {"id": cand["test_id"], "summary": f"{cand['test_id']} fails on base {base[:12]}: "
                                     + cand["message"][:600], "source": "bossman-test-run"})
    if not conf.get("confirmed"):
        state.update(result="NO_CONFIRMED_DEFECT", why=conf.get("why") or (conf.get("judge") or {}).get("reason"),
                     finished=C.now())
        C.jwrite(cdir / "cycle.json", state)
        print(json.dumps(state, ensure_ascii=False, indent=1)[:2500])
        return 1

    # FIX ----------------------------------------------------------------------------------------------
    newtest = f"command-center/tests/test_nsfix_{a.cycle}.py"
    fix = C.jread(cdir / "fix-task.json")
    if not (fix and fix.get("status") in C.TERMINAL):
        wish = FIX_WISH.format(test_id=cand["test_id"], output=cand["output"][-1800:], sources=", ".join(cand["sources"]),
                               newtest=newtest, test_file=cand["test_file"])
        tid = C.start_task(wish, [*cand["sources"], newtest], [cand["test_file"]], a.fixer, use_memory=True)
        print(f"[{C.now()}] FIX task {tid} ({a.fixer})", flush=True)
        fix = C.wait_task(tid)
        C.jwrite(cdir / "fix-task.json", fix)
    changed = fix.get("changed_files") or []
    st["FIX_TASK"] = {"task": fix.get("id"), "status": fix.get("status"), "model": fix.get("model"),
                      "steps": (fix.get("sidecar") or {}).get("steps"), "changed": changed,
                      "stop": (fix.get("sidecar") or {}).get("stop_reason"),
                      "zone_check": {k: (fix.get("verification") or {}).get(k) for k in ("ran", "runner", "passed")},
                      "recipes_recalled": (fix.get("memory") or {}).get("recipe_ids")}
    st["MODEL_PATCH_CREATED"] = fix.get("status") == "completed" and any(p in cand["sources"] for p in changed)

    # VERIFY -------------------------------------------------------------------------------------------
    from bossman_v3.self_improvement import verifier as v
    verdict = C.jread(cdir / "verify" / "verdict.json")
    if not verdict:
        task = {"id": a.cycle, "goal": "make the failing test pass", "tests": [cand["test_file"]],
                "editable": list(cand["sources"])}
        ev_rec = {"model": fix.get("model"), "worker": fix.get("worker"), "backend": "bossman_coding",
                  "model_kind": "REAL_MODEL", "task_id": fix.get("id")}
        verdict = v.verify(task=task, source=C.REPO, base_sha=base, diff=C.lf(fix.get("diff")),
                           tests=[cand["test_file"]], evidence=ev_rec, out=cdir / "verify", runner=C.runner(),
                           holdout=(), require_new_regression=False)
    st["RESULT_VERIFIER"] = {"verdict": verdict.get("verdict"), "reasons": verdict.get("reasons"),
                             "counts_as_student_success": verdict.get("counts_as_student_success")}

    # REVIEW -------------------------------------------------------------------------------------------
    review = C.jread(cdir / "review.json")
    if verdict.get("verdict") != "PASS":
        review = {"accepted": False, "why": "not reviewed: verifier did not PASS"}
    elif not review:
        from bossman_v3.self_improvement import protocol as proto
        diff = C.lf(fix.get("diff"))
        tmp = Path(tempfile.mkdtemp(prefix="ns-suite-review-"))
        wt = C.worktree(base, tmp)
        try:
            C.gitc(wt, "apply", "--whitespace=nowarn", "-", inp=(diff if diff.endswith("\n") else diff + "\n").encode())
            sources = {p: (wt / p).read_text(encoding="utf-8", errors="replace") for p in changed if (wt / p).is_file()}
        finally:
            C.drop_worktree(wt)
        ctx = proto.review_input(base, diff, sources, f"Make failing test {cand['test_id']} pass by fixing the source, "
                                                       "without weakening tests.")
        prov, model = a.reviewer.split(":", 1)
        review = {"reviewer": a.reviewer, "fixer_model": fix.get("model")}
        if proto.model_identity(model) == proto.model_identity(str(fix.get("model") or "")):
            review.update(accepted=False, why="reviewer model equals fixer model: not independent")
        else:
            try:
                resp = providers.chat(prov, model, proto.review_prompt(ctx), ledger=ev / "spend.jsonl",
                                      purpose=f"{a.cycle}:review", max_tokens=6000)
                parsed = providers.extract_json(resp["text"])
                review["response"] = parsed
                proto.validate_review(parsed, ctx)
                review.update(accepted=True, why="accept with no findings")
            except Exception as exc:  # noqa: BLE001
                review.update(accepted=False, why=str(exc)[:400])
        C.jwrite(cdir / "review.json", review)
    st["REVIEW"] = {k: review.get(k) for k in ("reviewer", "accepted", "why")}
    passed = bool(st["MODEL_PATCH_CREATED"] and verdict.get("verdict") == "PASS" and review.get("accepted"))
    st["INDEPENDENT_VERIFICATION_PASS"] = passed
    if passed and not a.no_learn:
        added = [l[1:].rstrip() for l in C.lf(fix.get("diff")).splitlines()
                 if l.startswith("+") and not l.startswith("+++")][:18]
        summary = str((fix.get("sidecar") or {}).get("summary") or "")[:700]
        recipe = {
            "id": f"ns-selfrepair-{a.cycle}-{fix['id']}", "title": f"Bossman self-repair: {cand['sources'][0]}",
            "symptom": f"{cand['test_id']} fails: {cand['message'][:600]}",
            "cause": f"Found by Bossman's own test run on base {base[:12]}; failure reproduced on a clean checkout "
                     "and classified code_defect by an independent judge model.",
            "diagnosis": "A failing test of the product's suite points at the source named in its traceback.",
            "action": (f"Bossman worker {fix.get('worker')} ({fix.get('model')}, task {fix['id']}): {summary} | changed: "
                       + " ; ".join(added))[:1900],
            "counterexample": "Do not edit or loosen the failing test; keep other tests of the file green.",
            "required_check": {"tool": "run_tests", "args": {"paths": [cand["test_file"]]}},
            "applies_when": {"project_id": C.PROJECT, "language": "python",
                             "keywords": sorted({w for w in re.findall(r"[a-z_]{4,}", cand["message"].lower())})[:12]
                             or ["failing", "test"]},
            "steps": [{"tool": "read_file", "args": {"path": cand["sources"][0]}},
                      {"tool": "run_tests", "args": {"paths": [cand["test_file"]]}}],
            "provenance": {"who": f"bossman-worker:{fix.get('worker')}/{fix.get('model')}", "source": "student",
                           "assistance_level": "none", "what": "verified self-repair recipe (north star 10.10)",
                           "code_refs": cand["sources"], "test_refs": [cand["test_file"]],
                           "evidence_refs": [f"coding-task:{fix['id']}"]},
            "status": "VERIFIED", "project_id": C.PROJECT, "scope": "project"}
        evidence = {"source": f"{cand['test_file']}@{base}+task:{fix['id']}", "expected": "PASS", "actual": "PASS",
                    "head_sha": base, "environment": "owner-pc Windows python 3.12"}
        ver = {"principal_id": "tool:bossman-evolution-result-verifier", "independence_class": "external_tool"}
        try:
            with C.client() as c:
                saved = c.post("/api/coding-recipes", {"recipe": recipe, "evidence": evidence, "verifier": ver,
                                                       "project_id": C.PROJECT, "scope": "project"})
        except Exception as exc:  # noqa: BLE001
            saved = {"error": str(exc)[:600]}
        C.jwrite(cdir / "recipe.json", {"recipe": recipe, "evidence": evidence, "verifier": ver, "saved": saved})
        st["EXPERIENCE_SAVED"] = {"recipe_id": (saved or {}).get("recipe_id"), "lesson_id": (saved or {}).get("lesson_id"),
                                  "error": (saved or {}).get("error")}
    state.update(result="SELF_REPAIR_CYCLE_PASS" if passed else "FIX_NOT_VERIFIED", finished=C.now())
    C.jwrite(cdir / "cycle.json", state)
    print(json.dumps(state, ensure_ascii=False, indent=1)[:3500])
    return 0 if passed else 1


def main(argv=None) -> int:
    ap = argparse.ArgumentParser()
    sub = ap.add_subparsers(dest="cmd", required=True)
    c = sub.add_parser("candidates")
    c.add_argument("--evidence", required=True)
    c.add_argument("--junit", required=True)
    r = sub.add_parser("run")
    r.add_argument("--evidence", required=True)
    r.add_argument("--cycle", required=True)
    r.add_argument("--cand", type=int, required=True)
    r.add_argument("--judge", default="gemini:gemini-3.5-flash")
    r.add_argument("--fixer", default="glm-flash")
    r.add_argument("--reviewer", default="gemini:gemini-3.5-flash")
    r.add_argument("--no-learn", action="store_true")
    a = ap.parse_args(argv)
    return {"candidates": cmd_candidates, "run": cmd_run}[a.cmd](a)


if __name__ == "__main__":
    sys.exit(main())
