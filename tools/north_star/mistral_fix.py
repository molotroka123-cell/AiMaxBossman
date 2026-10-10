"""Escalation fixer (owner/coordinator 10.10): Mistral Large 4 via OpenRouter writes the fix for a Bossman-found defect.

    python tools/north_star/mistral_fix.py --evidence <dir> [--case cli-tasks|goal-budget] [--feedback "<verifier reason>"]

One bounded model call (max_tokens 8000, reasoning disabled: `reasoning.enabled=false`, measured 10.10 — with it on,
Mistral Large 4 spent 8000-12000 completion tokens on reasoning and returned an empty reply). The reply is JSON of
exact text edits (the repo's own EDIT_SCHEMA idea). The edits are applied mechanically to the base text
(git show <base>:path), scope-checked (one source file + one NEW test file; existing tests untouched), turned into
a unified diff and recorded as a task-like record, so tools/north_star/verify_candidate.py (hidden holdout wrapped as
pytest, product RESULT_VERIFIER with negative control, cross-family reviewers) scores it like a sidecar worker's
patch. The harness writes no fix text: it only applies, scopes and records. The model sees the symptom and the code
excerpts, never the holdout. `--feedback` carries only the previous verifier reason (no solution text).
"""
from __future__ import annotations

import argparse
import difflib
import json
import sys
import time
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
sys.path.insert(0, str(HERE.parents[1] / "tools"))
import cycle as C  # noqa: E402
import providers  # noqa: E402
import tree_self_repair_cycle as tool  # noqa: E402

MODEL = "mistralai/mistral-large-4-0"
TEMPLATE = """You are a Bossman repair worker. Fix ONE defect with exact text edits; return ONLY JSON.

SYMPTOM (reported by Bossman's own pipeline):
{symptom}

CODE UNDER REPAIR ({source}):
```python
{code}
```
{context}
RULES: change only {source}; create exactly one NEW test file {newtest} (the test must FAIL on the old code and pass
with your fix, and must be self-contained and consistent with how the code really behaves); never touch any other file
or any existing test. Each edit's `old` must be an exact, unique substring of the current file; for the new test file
use "old": "" and put the whole file in "new".
Return JSON exactly: {{"summary": "<one sentence>", "edits": [{{"path": "...", "old": "...", "new": "..."}}]}}"""

CASES = {
    "cli-tasks": {
        "source": "command-center/bcc/terminal_cli/cli.py",
        "newtest": "command-center/tests/test_terminal_task_paging.py",
        "holdout": "tools/tree_holdout/cli_task_paging.py",
        "symptom": ("when more than 500 tasks exist, `bossman list tasks --json --limit 1000` returns exactly 500 "
                    "tasks although the API holds 837 (cli=500 api=837; found by the UX soak). An explicit --limit N "
                    "must return min(N, available) tasks, newest first, without duplicates; small limits and an empty "
                    "store behave as before. Only the `tasks` branch of list_items is wrong."),
    },
    "goal-budget": {
        "source": "command-center/bcc/autonomy/goals.py",
        "newtest": "command-center/tests/test_autonomy_goal_budget_ns.py",
        "holdout": "tools/tree_holdout/goal_budget_nonfinite.py",
        "symptom": tool.CASES["goal-budget"]["wish"].split("Сначала тест")[0].strip() + (
            " (Bossman's own audit text, 06.10.) Contract: the budget ceiling must always bind, whatever the "
            "reported cost is (NaN, +-inf, negative, non-number); valid charging behaves as before."),
    },
}


def base_text(sha: str, path: str) -> str:
    return C.gitc(C.REPO, "show", f"{sha}:{path}").replace("\r\n", "\n")


def excerpt(text: str, start_pat: str, end_pat: str) -> str:
    s = text.index(start_pat)
    return text[s:text.index(end_pat, s)]


def udiff(path: str, old: str | None, new: str) -> str:
    if old is None:
        body = "".join(difflib.unified_diff([], new.splitlines(True), "/dev/null", f"b/{path}"))
        return f"diff --git a/{path} b/{path}\nnew file mode 100644\n{body}"
    body = "".join(difflib.unified_diff(old.splitlines(True), new.splitlines(True), f"a/{path}", f"b/{path}"))
    return f"diff --git a/{path} b/{path}\n{body}"


def build_prompt(case_name: str, base: str) -> str:
    case = CASES[case_name]
    src = base_text(base, case["source"])
    if case_name == "cli-tasks":
        code = excerpt(src, "def list_items(", "# ----------------------------------------------------------------- code / evolution")
        api = base_text(base, "command-center/bcc/api.py")
        context = ("THE API ROUTE IT CALLS (command-center/bcc/api.py, read-only, do not change it):\n```python\n"
                   + excerpt(api, '    @router.get("/tasks")', '    @router.post("/tasks/preflight")') + "```\n"
                   "In the test use your own tiny fake client whose get is `get(self, path, params=None)`; it must hold "
                   "MORE than 500 tasks and clamp each request's limit to 500 like the real route (newest first, "
                   "`before_id` means id < before_id).\n")
    else:
        code = src
        helper = base_text(base, "command-center/tests/autonomy_fakes.py")
        sample = excerpt(base_text(base, "command-center/tests/test_autonomy_goals.py"), "def mk(**kw)", "class Clock")
        context = ("HOW EXISTING TESTS BUILD A GOAL AND STORE (command-center/tests/test_autonomy_goals.py, read-only):\n"
                   "```python\nfrom bcc.autonomy.types import Budget, Goal\n" + sample +
                   "```\nA GoalStore is built like `GoalStore(root_path, Journal(root_path / 'journal.jsonl'))` "
                   "(see bcc.autonomy.journal.Journal). Test helper available in tests: `from autonomy_fakes import "
                   "make_goal` (exists in command-center/tests/autonomy_fakes.py).\n")
        del helper
    return TEMPLATE.format(symptom=case["symptom"], source=case["source"], code=code, context=context,
                           newtest=case["newtest"])


def main(argv=None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--evidence", required=True, type=Path)
    ap.add_argument("--case", default="cli-tasks", choices=sorted(CASES))
    ap.add_argument("--feedback", default="", help="verifier reason of the previous attempt (no solution text)")
    a = ap.parse_args(argv)
    case = CASES[a.case]
    ev = a.evidence.resolve()
    ev.mkdir(parents=True, exist_ok=True)
    base = C.gitc(C.REPO, "rev-parse", "HEAD").strip()
    prompt = build_prompt(a.case, base)
    if a.feedback:
        prompt += ("\n\nVERIFIER FEEDBACK ON YOUR PREVIOUS ATTEMPT (independent verifier): " + a.feedback)
    t0 = time.time()
    resp = providers.chat("openrouter", MODEL, prompt, ledger=ev.parent / "spend.jsonl", purpose=f"mistral-fix:{a.case}",
                          max_tokens=8000, timeout=420, extra={"reasoning": {"enabled": False}})
    (ev / "model-reply.txt").write_text(resp["text"], encoding="utf-8")
    rec: dict = {"id": "mistral" + format(int(t0) % 10**8, "08d"), "worker": "mistral-large", "model": MODEL,
                 "case": a.case, "base_commit": base, "source_repo": str(C.REPO), "status": "failed",
                 "changed_files": [], "diff": "", "cost_usd": resp.get("cost_usd"), "prompt_chars": len(prompt),
                 "sidecar": {"summary": "", "steps": 1, "stop_reason": "single_call"}, "memory": {}}
    src_path, new_path = case["source"], case["newtest"]
    try:
        data = providers.extract_json(resp["text"])
        edits = data["edits"]
        rec["sidecar"]["summary"] = str(data.get("summary") or "")[:500]
        assert isinstance(edits, list) and edits, "no edits"
        src_old = base_text(base, src_path)
        src_new, newtest_text = src_old, None
        for e in edits:
            p, old, new = e["path"], e.get("old", ""), e["new"]
            if p == src_path:
                old_n = old.replace("\r\n", "\n")
                assert old_n and src_new.count(old_n) == 1, "source edit: old not unique/exact"
                src_new = src_new.replace(old_n, new.replace("\r\n", "\n"), 1)
            elif p == new_path:
                assert old == "" and newtest_text is None, "bad new-test edit"
                newtest_text = new.replace("\r\n", "\n")
            else:
                raise AssertionError(f"out-of-scope path {p}")
        assert src_new != src_old, "source unchanged"
        compile(src_new, src_path, "exec")
        parts, changed = [udiff(src_path, src_old, src_new)], [src_path]
        if newtest_text is not None:
            compile(newtest_text, new_path, "exec")
            parts.append(udiff(new_path, None, newtest_text))
            changed.append(new_path)
        rec.update(diff="".join(parts), changed_files=changed, status="completed")
    except Exception as exc:  # noqa: BLE001
        rec["error"] = f"{type(exc).__name__}: {str(exc)[:400]}"
    (ev / f"task-{rec['id']}.json").write_text(json.dumps(rec, ensure_ascii=False, indent=1), encoding="utf-8")
    print("status", rec["status"], rec.get("error", ""), "changed", rec["changed_files"], "cost", rec["cost_usd"])
    if rec["status"] != "completed":
        return 1
    tcase = dict(tool.CASES.get(a.case) or {"target": src_path}, target=src_path, holdout=case["holdout"],
                 allowed=[src_path, new_path])
    check = tool.independent_check(rec, tcase, ev, r"C:\Users\asd\Bossman\ns-lab-20261010\venv\Scripts\python.exe")
    out = {"holdout_base": {k: (check.get("before") or {}).get(k) for k in ("total", "failed", "passed")},
           "holdout_patched": {k: (check.get("after") or {}).get(k) for k in ("total", "failed", "passed", "error")}}
    (ev / "holdout-summary.json").write_text(json.dumps(out, indent=1), encoding="utf-8")
    print(json.dumps(out))
    return 0


if __name__ == "__main__":
    sys.exit(main())
