"""Strict verification + cross-family review of a cli-tasks candidate produced by a Bossman worker.

    python tools/north_star/verify_candidate.py --evidence <dir with task-*.json> --reviewers nvidia:moonshotai/kimi-k3,nvidia:z-ai/glm-5.3 [--learn]

1. The hidden holdout is wrapped into a pytest file committed on top of the base in the lab clone
   (refs/ns/verify-<tag>); the worker never sees it. bossman_v3 RESULT_VERIFIER runs it: fails on the base, must pass
   on base+diff, nothing that passed may fail, existing tests may not be weakened (removed lines are flagged).
2. Each reviewer (a different model family than the fixer) gets only goal + diff + sources via protocol.review_prompt.
3. With --learn and PASS + all reviewers accept: POST /api/coding-recipes (VERIFIED, verifier = external tool).
Nothing here edits product code or the candidate.
"""
from __future__ import annotations

import argparse
import json
import sys
import tempfile
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
sys.path.insert(0, str(HERE.parents[1] / "tools"))
import cycle as C  # noqa: E402
import providers  # noqa: E402
import tree_self_repair_cycle as tool  # noqa: E402

CASES = {
    "cli-tasks": {"holdout": "cli_task_paging.py", "editable": ["command-center/bcc/terminal_cli/cli.py"],
                  "zone_tests": ["command-center/tests/test_terminal_local_models.py"]},
    "goal-budget": {"holdout": "goal_budget_nonfinite.py", "editable": ["command-center/bcc/autonomy/goals.py"],
                    "zone_tests": ["command-center/tests/test_autonomy_goals.py"]},
}
GOAL = ("`bossman list tasks --limit N` must return min(N, available) tasks, newest first, without duplicates, even "
        "when N exceeds the API's 500 per-request cap (GET /api/tasks offers before_id paging). Small limits and an "
        "empty store behave as before. Existing tests must not be weakened.")


def main(argv=None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--evidence", required=True, type=Path)
    ap.add_argument("--reviewers", required=True)
    ap.add_argument("--learn", action="store_true")
    ap.add_argument("--max-tokens", type=int, default=6000)
    ap.add_argument("--case", default="cli-tasks", choices=sorted(CASES))
    a = ap.parse_args(argv)
    case = CASES[a.case]
    goal = GOAL if a.case == "cli-tasks" else tool.CASES["goal-budget"]["wish"]
    ev = a.evidence.resolve()
    task_file = next(ev.glob("task-*.json"))
    rec = json.loads(task_file.read_text(encoding="utf-8"))
    tag = f"ct-{rec['id']}"
    diff = C.lf(rec.get("diff"))
    base = rec["base_commit"]
    out: dict = {"task": rec["id"], "worker": rec.get("worker"), "model": rec.get("model"), "base": base,
                 "changed": rec.get("changed_files")}
    # 1. verification base with the hidden repro as a pytest file
    repro = f"command-center/tests/test_nsrepro_{rec['id']}.py"
    holdout_src = HERE.parents[1] / "tools" / "tree_holdout" / case["holdout"]
    body = holdout_src.read_text(encoding="utf-8") + (
        "\n\ndef test_hidden_holdout():\n"
        "    res = run(Path(__file__).resolve().parents[2])\n"
        "    assert res['passed'], res['failures']\n")
    tmp = Path(tempfile.mkdtemp(prefix="ns-ver-"))
    wt = C.worktree(base, tmp)
    try:
        (wt / repro).write_text(body, encoding="utf-8", newline="\n")
        C.gitc(wt, "add", repro)
        C.gitc(wt, "commit", "-q", "-m", f"north-star {tag}: hidden repro (harness-authored holdout wrapped as pytest)")
        vbase = C.gitc(wt, "rev-parse", "HEAD").strip()
        C.gitc(C.REPO, "update-ref", f"refs/ns/verify-{tag}", vbase)
    finally:
        C.drop_worktree(wt)
    from bossman_v3.self_improvement import verifier as v
    task = {"id": tag, "goal": goal, "tests": [repro], "editable": list(case["editable"])}
    verdict = v.verify(task=task, source=C.REPO, base_sha=vbase, diff=diff,
                       tests=[repro, *case["zone_tests"]],
                       evidence={"model": rec.get("model"), "worker": rec.get("worker"), "backend": "bossman_coding",
                                 "model_kind": "REAL_MODEL", "task_id": rec.get("id")},
                       out=ev / "verify-strict", runner=C.runner(), holdout=(repro,), require_new_regression=False)
    out["RESULT_VERIFIER"] = {"verdict": verdict["verdict"], "reasons": verdict["reasons"],
                              "counts_as_student_success": verdict.get("counts_as_student_success")}
    # 2. reviewers
    from bossman_v3.self_improvement import protocol as proto
    tmp = Path(tempfile.mkdtemp(prefix="ns-rev-"))
    wt = C.worktree(base, tmp)
    try:
        C.gitc(wt, "apply", "--whitespace=nowarn", "-", inp=(diff if diff.endswith("\n") else diff + "\n").encode())
        sources = {p: (wt / p).read_text(encoding="utf-8", errors="replace") for p in rec.get("changed_files") or []}
    finally:
        C.drop_worktree(wt)
    reviews = []
    for spec in [s for s in a.reviewers.split(",") if s]:
        prov, model = spec.split(":", 1)
        ctx = proto.review_input(base, diff, sources, goal)
        rv: dict = {"reviewer": spec}
        if proto.model_identity(model) == proto.model_identity(str(rec.get("model") or "")):
            rv.update(accepted=False, state="no_verdict", why="reviewer equals fixer model")
        else:
            try:
                resp = providers.chat(prov, model, proto.review_prompt(ctx), ledger=ev.parent / "spend.jsonl",
                                      purpose=f"{tag}:review", max_tokens=a.max_tokens, timeout=600,
                                      extra=({"reasoning": {"enabled": False}}
                                             if prov == "openrouter" and "glm" not in model else None))
                parsed = providers.extract_json(resp["text"])
                rv["response"] = parsed
            except Exception as exc:  # noqa: BLE001 - transport error / empty or non-JSON reply: no verdict at all
                rv.update(accepted=False, state="no_verdict", why=str(exc)[:600])
            else:
                try:
                    proto.validate_review(parsed, ctx)
                    rv.update(accepted=True, state="accept", why="accept with no findings")
                except Exception as exc:  # noqa: BLE001 - a well-formed reply that rejects / has findings / wrong binding
                    rv.update(accepted=False, state="reject", why=str(exc)[:600])
        reviews.append(rv)
    out["reviews"] = [{k: r.get(k) for k in ("reviewer", "state", "accepted", "why")} for r in reviews]
    (ev / "reviews-strict.json").write_text(json.dumps(reviews, ensure_ascii=False, indent=1), encoding="utf-8")
    accepts = sum(1 for r in reviews if r.get("state") == "accept")
    rejects = sum(1 for r in reviews if r.get("state") == "reject")
    # PASS = product verifier PASS, at least one valid cross-family accept, no valid rejection.
    # "no_verdict" (transport error, empty reasoning reply) is neither: it is recorded and not counted.
    out["review_summary"] = {"accept": accepts, "reject": rejects,
                             "no_verdict": sum(1 for r in reviews if r.get("state") == "no_verdict")}
    out["PASS"] = bool(verdict["verdict"] == "PASS" and accepts >= 1 and rejects == 0)
    if out["PASS"] and a.learn:
        before = json.loads(next(ev.glob("holdout-*-base.json")).read_text(encoding="utf-8"))
        after = json.loads(next(ev.glob("holdout-*-patched.json")).read_text(encoding="utf-8"))
        recipe, evd, ver = tool.build_recipe(rec, tool.CASES.get(a.case) or CASE_FALLBACK,
                                             {"before": before, "after": after, "base_commit": base})
        recipe["provenance"]["assistance_level"] = "none"
        from bcc.terminal_cli.api_client import Client, discover
        with Client(discover("http://127.0.0.1:8835", r"C:\Users\asd\Bossman\ns-lab-20261010\data"), timeout=60) as c:
            try:
                out["recipe_saved"] = c.post("/api/coding-recipes", {"recipe": recipe, "evidence": evd, "verifier": ver,
                                                                     "project_id": tool.PROJECT, "scope": "project"})
            except Exception as exc:  # noqa: BLE001
                out["recipe_saved"] = {"error": str(exc)[:500]}
        (ev / "recipe-strict.json").write_text(json.dumps({"recipe": recipe, "saved": out["recipe_saved"]},
                                                          ensure_ascii=False, indent=1), encoding="utf-8")
    (ev / "strict.json").write_text(json.dumps(out, ensure_ascii=False, indent=1), encoding="utf-8")
    print(json.dumps(out, ensure_ascii=False, indent=1)[:3000])
    return 0 if out["PASS"] else 1


CASE_FALLBACK = {"target": "command-center/bcc/terminal_cli/cli.py", "holdout": "tools/tree_holdout/cli_task_paging.py",
                 "wish": GOAL, "verify_tests": ["command-center/tests/test_terminal_local_models.py"],
                 "keywords": ["list", "tasks", "limit", "before_id", "pagination", "cap", "500", "cli"], "search": "limit",
                 "cause": "CLI list tasks passed limit through; the API clamps to 500 (before_id paging exists)"}

if __name__ == "__main__":
    sys.exit(main())
