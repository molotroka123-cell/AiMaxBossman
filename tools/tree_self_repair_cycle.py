"""One self-repair cycle through the RUNNING Bossman, scored stage by stage (owner, 06.10.2026).

    python tools/tree_self_repair_cycle.py --case discovery   --worker openrouter-free --evidence <dir>
    python tools/tree_self_repair_cycle.py --case goal-budget --worker openrouter-free --evidence <dir> --transfer

Stages, each reported on its own and never upgraded by a later one:
  DEFECT_REPRODUCED              the hidden holdout FAILS on the task's base commit
  MODEL_PATCH_CREATED            the free worker (not Claude) changed the target file in the isolated copy
  INDEPENDENT_VERIFICATION_PASS  Bossman's own zone check passed AND the hidden holdout PASSES on base+patch
  EXPERIENCE_AUTO_SAVED          a VERIFIED coding recipe was written from the task's own artifacts (no hand-written text)
  RECIPE_RECALLED                (transfer) the new task's context carried a saved recipe id
  TRANSFER_PASS                  (transfer) recipe recalled AND independent verification passed on the NEW defect
APPLIED_RUNTIME_PASS is not decided here: applying needs the owner's Apply and a rebuilt, installed bundle.

The holdouts live in tools/tree_holdout/, outside the worker's editable paths; they check the RESULT of the code,
and run with plain Python (no pytest), so they work with the installed bundle runtime. A partial fix stays PARTIAL.
"""
from __future__ import annotations

import argparse
import json
import platform
import subprocess
import sys
import tempfile
import time
from pathlib import Path
from typing import Any

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
from tree_self_improve import NODE_ID, refuse_worker  # noqa: E402

PROJECT = "capability-tree"            # the project id tree zone tasks run under; recipes are recalled per project
CASES: dict[str, dict[str, Any]] = {
    "discovery": {
        "mode": "tree", "node": NODE_ID, "target": "command-center/bcc/pit/discovery.py",
        "holdout": "tools/tree_holdout/discovery_nonfinite.py",
        "wish": ("В bcc/pit/discovery.py выбор вопроса (choose_discovery_question / score) ломается на некорректных числах. "
                 "Прошлая попытка (задача 904e7a3c02be) закрыла только NaN в relevance, uncertainty и future_utility; "
                 "остались annoyance_cost и sensitivity_risk, а также +inf/-inf и None в любом числовом поле. Контракт: "
                 "кандидат, у которого любое числовое поле не является конечным числом, НИКОГДА не выбирается и не роняет "
                 "выбор; неизвестный риск нельзя превращать в безопасный ноль; результат не зависит от порядка кандидатов; "
                 "корректные кандидаты выбираются как раньше. Сначала напиши тест, который падает на старом коде. "
                 "ОБЪЁМ (жёстко): меняй ТОЛЬКО command-center/bcc/pit/discovery.py и добавь тест-функции в уже "
                 "существующий command-center/tests/test_pit_foundation.py; новых файлов не создавай."),
    },
    "goal-budget": {
        "mode": "task", "target": "command-center/bcc/autonomy/goals.py",
        "holdout": "tools/tree_holdout/goal_budget_nonfinite.py",
        "verify_tests": ["command-center/tests/test_autonomy_goals.py"],
        "allowed": ["command-center/bcc/autonomy/goals.py", "command-center/tests/test_autonomy_goals.py"],
        # a symptom, not a patch: the fix has to come from the worker (and the recipe it recalls)
        "wish": ("Аудит бюджета целей автономии (bcc/autonomy/goals.py, GoalStore.charge и budget_exceeded): стоимость "
                 "приходит из внешних отчётов, и в одном прогоне цель с потолком max_cost_usd=1.0 продолжила тратить после "
                 "отчёта о стоимости 5.0 и не была заблокирована. Найди дефект и исправь так, чтобы потолок бюджета "
                 "всегда срабатывал. Сначала тест, который падает на старом коде. ОБЪЁМ: меняй только "
                 "command-center/bcc/autonomy/goals.py и добавь тест-функции в command-center/tests/test_autonomy_goals.py."),
    },
}


def git(repo: Path, *args: str, check: bool = True) -> subprocess.CompletedProcess:
    return subprocess.run(["git", "-C", str(repo), *args], capture_output=True, text=True, encoding="utf-8",
                          errors="replace", check=check)


def run_holdout(holdout: Path, repo: Path, out: Path, python: str) -> dict:
    proc = subprocess.run([python, "-X", "utf8", str(holdout), "--repo", str(repo), "--out", str(out)],
                          capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=600)
    result = json.loads(out.read_text(encoding="utf-8")) if out.is_file() else {"error": proc.stdout[-800:]}
    result.update(exit_code=proc.returncode, command=f"{Path(python).name} {holdout.name} --repo <checkout>",
                  stdout_tail=proc.stdout[-1200:])
    return result


def independent_check(rec: dict, case: dict, evidence: Path, python: str) -> dict:
    """Hidden holdout on the task's exact base commit, then on base + the worker's diff."""
    repo, base, diff = Path(rec["source_repo"]), rec.get("base_commit"), rec.get("diff") or ""
    holdout = HERE.parent / case["holdout"]
    if not base:
        return {"error": "the task recorded no base commit"}
    tmp = Path(tempfile.mkdtemp(prefix="selfrepair-"))
    wt = tmp / "wt"
    git(repo, "worktree", "add", "--detach", str(wt), base)
    try:
        before = run_holdout(holdout, wt, evidence / f"holdout-{rec['id']}-base.json", python)
        after: dict = {"skipped": "no diff"}
        if diff.strip():
            patch = tmp / "candidate.diff"
            patch.write_text(diff if diff.endswith("\n") else diff + "\n", encoding="utf-8", newline="\n")
            applied = git(wt, "apply", "--whitespace=nowarn", str(patch), check=False)
            after = (run_holdout(holdout, wt, evidence / f"holdout-{rec['id']}-patched.json", python)
                     if applied.returncode == 0 else {"error": f"git apply failed: {applied.stderr[-400:]}"})
        return {"base_commit": base, "before": before, "after": after}
    finally:
        git(repo, "worktree", "remove", "--force", str(wt), check=False)


def _added_lines(diff: str, target: str, limit: int = 18) -> list[str]:
    lines, inside = [], False
    for row in diff.splitlines():
        if row.startswith("diff --git"):
            inside = row.endswith(" b/" + target)
        elif inside and row.startswith("+") and not row.startswith("+++"):
            lines.append(row[1:].rstrip())
    return [x for x in lines if x.strip()][:limit]


def build_recipe(rec: dict, case: dict, check: dict) -> tuple[dict, dict, dict]:
    """A recipe from the task's OWN artifacts: the wish (symptom), the holdout's failures (cause/diagnosis),
    the worker's summary and diff (action), the holdout's valid cases (counterexample)."""
    before, after = check["before"], check["after"]
    fails = sorted({f["case"].split("/")[0] for f in before.get("failures", [])})
    valid = [c["case"] for c in after.get("cases", []) if c["case"].startswith("valid")][:6]
    summary = str(((rec.get("sidecar") or {}).get("summary")) or "")[:700]
    added = _added_lines(rec.get("diff") or "", case["target"])
    worker = f"{rec.get('worker')}/{rec.get('model')}"
    paths = list(rec.get("verify_tests") or [])[:20] or [case["target"]]
    recipe = {
        "id": f"tree-selfrepair-{rec['id']}",
        "title": f"Bossman self-repair: {case['target']}",
        "symptom": case["wish"][:1200],
        "cause": ("Некорректное число (NaN/±inf/None/отрицательное) проходило проверку как допустимое: сравнения с NaN "
                  f"ложны. До исправления holdout {before.get('total', 0) - before.get('failed', 0)}/{before.get('total')}; "
                  f"падали: {', '.join(fails[:12])}"),
        "diagnosis": f"Скрытый holdout {Path(case['holdout']).name} на базе {check['base_commit'][:12]} воспроизводит дефект "
                     "по результату, а не по наличию проверки.",
        "action": (f"Исполнитель Bossman ({worker}, задача {rec['id']}): {summary} | Изменённые строки: "
                   + " ; ".join(added))[:1900],
        "counterexample": "Не трогать корректные значения: " + ", ".join(valid or ["valid cases of the holdout"]),
        "required_check": {"tool": "run_tests", "args": {"paths": paths}},
        "applies_when": {"project_id": PROJECT, "language": "python",
                         "keywords": ["nan", "inf", "non-finite", "float", "budget", "score", "max", "comparison",
                                      "isfinite", "cost", "threshold"]},
        "steps": [{"tool": "read_file", "args": {"path": case["target"]}},
                  {"tool": "search", "args": {"pattern": "float(", "path": case["target"]}},
                  {"tool": "run_tests", "args": {"paths": paths}}],
        "provenance": {"who": f"bossman-worker:{worker}", "source": "student", "assistance_level": "hint",
                       "what": "verified self-repair recipe", "code_refs": [case["target"]],
                       "test_refs": [case["holdout"]], "evidence_refs": [f"coding-task:{rec['id']}"]},
        "status": "VERIFIED", "project_id": PROJECT, "scope": "project",
    }
    evidence = {"source": f"{case['holdout']}@{check['base_commit']}+task:{rec['id']}", "expected": "PASS",
                "actual": "PASS" if after.get("passed") else "FAIL", "head_sha": check["base_commit"],
                "environment": f"owner-pc {platform.system()} {platform.release()} python {platform.python_version()}"}
    verifier = {"principal_id": f"tree-holdout:{Path(case['holdout']).stem}", "independence_class": "external_tool"}
    return recipe, evidence, verifier


def stages(rec: dict, check: dict, case: dict, saved: dict | None, transfer: bool) -> dict:
    before, after = check.get("before") or {}, check.get("after") or {}
    changed = list(rec.get("changed_files") or [])
    ver = rec.get("verification") or {}
    out = {
        "DEFECT_REPRODUCED": before.get("passed") is False and before.get("failed", 0) > 0,
        "MODEL_PATCH_CREATED": rec.get("status") == "completed" and case["target"] in changed,
        "BOSSMAN_ZONE_CHECK_PASS": bool(ver.get("ran") and ver.get("passed")),
        "HOLDOUT_PASS_ON_PATCH": after.get("passed") is True,
        "SCOPE_RESPECTED": all(p == case["target"] or p.startswith("command-center/tests/test_") for p in changed),
    }
    out["INDEPENDENT_VERIFICATION_PASS"] = bool(out["DEFECT_REPRODUCED"] and out["MODEL_PATCH_CREATED"]
                                                and out["BOSSMAN_ZONE_CHECK_PASS"] and out["HOLDOUT_PASS_ON_PATCH"])
    out["EXPERIENCE_AUTO_SAVED"] = bool(saved and saved.get("lesson_id"))
    if transfer:
        ids = list(((rec.get("memory") or {}).get("recipe_ids")) or [])
        out["RECIPE_RECALLED"] = any(str(i).startswith("tree-selfrepair-") for i in ids)
        out["recalled_recipe_ids"] = ids
        out["TRANSFER_PASS"] = bool(out["RECIPE_RECALLED"] and out["INDEPENDENT_VERIFICATION_PASS"])
    return out


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--case", choices=sorted(CASES), default="discovery")
    ap.add_argument("--worker", default="openrouter-free")
    ap.add_argument("--source-repo", help="checkout the tree works on (default: the backend's own choice)")
    ap.add_argument("--task", help="score an already finished coding task instead of starting a new one")
    ap.add_argument("--transfer", action="store_true", help="also require a recalled recipe (TRANSFER_PASS)")
    ap.add_argument("--no-save", action="store_true", help="do not write the recipe even if verified")
    ap.add_argument("--evidence", type=Path, required=True)
    ap.add_argument("--timeout", type=int, default=1900)
    ap.add_argument("--python", default=sys.executable, help="interpreter for the holdout (no pytest needed)")
    ap.add_argument("--url")
    ap.add_argument("--data-dir")
    args = ap.parse_args(argv)
    refusal = refuse_worker(args.worker)
    if refusal:
        print("SELF_REPAIR=REFUSED\n" + refusal)
        return 2
    case = CASES[args.case]
    args.evidence.mkdir(parents=True, exist_ok=True)
    from bcc.terminal_cli.api_client import BossmanError, Client, discover
    try:
        with Client(discover(args.url, args.data_dir), timeout=120.0) as client:
            task_id = args.task
            if not task_id:
                if case["mode"] == "tree":
                    body = {"node_id": case["node"], "instruction": case["wish"], "worker": args.worker}
                    if args.source_repo:
                        body["source_repo"] = args.source_repo
                    task_id = client.post("/api/capability-tree/work", body)["job"]["task_id"]
                else:
                    task_id = client.post("/api/coding-tasks", {
                        "instruction": case["wish"], "source_repo": args.source_repo, "allowed_paths": case["allowed"],
                        "verify_tests": case["verify_tests"], "project_id": PROJECT, "timeout_seconds": 1800,
                        "worker": args.worker})["id"]
                print(f"task {task_id} started ({args.case}, worker {args.worker})", flush=True)
            deadline = time.monotonic() + args.timeout
            rec = client.get(f"/api/coding-tasks/{task_id}")
            while rec.get("status") not in ("completed", "failed", "blocked", "cancelled") and time.monotonic() < deadline:
                time.sleep(10)
                rec = client.get(f"/api/coding-tasks/{task_id}")
            if rec.get("status") not in ("completed", "failed", "blocked", "cancelled"):
                print(f"SELF_REPAIR=TIMEOUT task={task_id}")
                return 4
            (args.evidence / f"task-{task_id}.json").write_text(json.dumps(rec, ensure_ascii=False, indent=1), encoding="utf-8")
            check = independent_check(rec, case, args.evidence, args.python) if rec.get("base_commit") else {"error": "no base"}
            st = stages(rec, check, case, None, args.transfer)
            saved = None
            if st["INDEPENDENT_VERIFICATION_PASS"] and not args.no_save:
                recipe, ev, verifier = build_recipe(rec, case, check)
                try:
                    saved = client.post("/api/coding-recipes", {"recipe": recipe, "evidence": ev, "verifier": verifier,
                                                                "project_id": PROJECT, "scope": "project"})
                except BossmanError as exc:
                    saved = {"error": str(exc)[:600]}
                (args.evidence / f"recipe-{task_id}.json").write_text(
                    json.dumps({"recipe": recipe, "evidence": ev, "verifier": verifier, "saved": saved},
                               ensure_ascii=False, indent=1), encoding="utf-8")
            st = stages(rec, check, case, saved, args.transfer)
    except BossmanError as exc:
        print(f"SELF_REPAIR=NOT_RUN\n{exc}")
        return 3
    report = {"task_id": task_id, "case": args.case, "worker": rec.get("worker"), "model": rec.get("model"),
              "status": rec.get("status"), "error": rec.get("error"), "base_commit": rec.get("base_commit"),
              "changed_files": rec.get("changed_files"),
              "verification": {k: (rec.get("verification") or {}).get(k) for k in ("ran", "runner", "exit_code", "passed")},
              "holdout_base": {k: (check.get("before") or {}).get(k) for k in ("total", "failed", "passed", "exit_code")},
              "holdout_patched": {k: (check.get("after") or {}).get(k) for k in ("total", "failed", "passed", "exit_code")},
              "recipe_saved": saved, "stages": st}
    (args.evidence / f"cycle-{task_id}.json").write_text(json.dumps(report, ensure_ascii=False, indent=1), encoding="utf-8")
    verdict = ("TRANSFER_PASS" if st.get("TRANSFER_PASS") else
               "INDEPENDENT_VERIFICATION_PASS" if st["INDEPENDENT_VERIFICATION_PASS"] else
               "PARTIAL" if st["MODEL_PATCH_CREATED"] else "FAILED")
    print(f"SELF_REPAIR={verdict}")
    print(json.dumps(report, ensure_ascii=False, indent=1))
    return 0 if verdict in ("TRANSFER_PASS", "INDEPENDENT_VERIFICATION_PASS") else 1


if __name__ == "__main__":
    sys.exit(main())
