"""goal-budget cycle through the repo's own tools/tree_self_repair_cycle.py with the lab-only worker routes allowed,
followed by an independent cross-family review of the worker's diff (protocol.review_prompt / validate_review).

    python tools/north_star/gb_cycle.py --worker gemini-flash --evidence <dir> [--reviewer mistral|gemini]

Nothing here edits product code or a holdout. The tool's stages (DEFECT_REPRODUCED, MODEL_PATCH_CREATED,
BOSSMAN_ZONE_CHECK_PASS, HOLDOUT_PASS_ON_PATCH, SCOPE_RESPECTED, INDEPENDENT_VERIFICATION_PASS) are the repo's own;
the review stage is added here and is required for this harness's PASS.
"""
from __future__ import annotations

import json
import sys
import tempfile
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
sys.path.insert(0, str(HERE.parents[1] / "tools"))
import tree_self_improve as tsi  # noqa: E402
import tree_self_repair_cycle as tool  # noqa: E402

LAB_WORKERS = {"gemini-flash": "gemini-3.5-flash", "gemini-flash-2": "gemini-3.5-flash",
               "mistral-large": "mistralai/mistral-large-4-0"}
tool.OWNER_APPROVED_PAID.update(LAB_WORKERS)
tsi.FREE_WORKERS = tuple(tsi.FREE_WORKERS) + ("gemini-flash", "gemini-flash-2")

tool.CASES["cli-tasks"] = {
    # found by Bossman's own UX soak (10.10): CLI `list tasks --limit 1000` silently returns 500
    "mode": "task", "target": "command-center/bcc/terminal_cli/cli.py",
    "holdout": "tools/tree_holdout/cli_task_paging.py",
    "verify_tests": ["command-center/tests/test_terminal_local_models.py"],
    "allowed": ["command-center/bcc/terminal_cli/cli.py", "command-center/tests/test_terminal_local_models.py"],
    "search": "limit",
    "cause": ("CLI `list tasks --limit N` передавал limit в GET /api/tasks, а маршрут режет limit до 500 "
              "(есть постраничный before_id); CLI молча возвращал 500 задач"),
    "keywords": ["list", "tasks", "limit", "before_id", "pagination", "page", "cap", "500", "cli"],
    "wish": ("UX-прогон Bossman (tools/ux_soak) зафиксировал 42 находки high: когда задач больше 500, команда "
             "`bossman list tasks --json --limit 1000` возвращает ровно 500 задач, хотя API держит 837 "
             "(cli=500 api=837). Найди причину в command-center/bcc/terminal_cli/cli.py (list_items, ветка tasks; "
             "маршрут GET /api/tasks в command-center/bcc/api.py читай, но не меняй) и исправь так, чтобы явный "
             "--limit N возвращал min(N, всего) задач, новые первыми, без повторов; малые лимиты и пустое "
             "хранилище работают как раньше. Сначала тест, который падает на старом коде. ОБЪЁМ: меняй только "
             "command-center/bcc/terminal_cli/cli.py и добавь тест-функции в "
             "command-center/tests/test_terminal_local_models.py; новых файлов не создавай."),
}
tool.CASES["cli-tasks2"] = dict(tool.CASES["cli-tasks"], **{
    # second wording (10.10): the first forced edits into an existing test file and the product RESULT_VERIFIER
    # rejects any change of existing test lines (INVALID_TEST). Same holdout, same defect.
    "allowed": ["command-center/bcc/terminal_cli/cli.py", "command-center/tests/test_terminal_task_paging.py"],
    "wish": ("UX-прогон Bossman (tools/ux_soak) зафиксировал 42 находки high: когда задач больше 500, команда "
             "`bossman list tasks --json --limit 1000` возвращает ровно 500 задач, хотя API держит 837 "
             "(cli=500 api=837). Найди причину в command-center/bcc/terminal_cli/cli.py (list_items, ветка tasks; "
             "маршрут GET /api/tasks в command-center/bcc/api.py читай, но не меняй) и исправь так, чтобы явный "
             "--limit N возвращал min(N, всего) задач, новые первыми, без повторов; малые лимиты и пустое "
             "хранилище работают как раньше. Сначала напиши тест, который падает на старом коде, в НОВОМ файле "
             "command-center/tests/test_terminal_task_paging.py со своим маленьким поддельным клиентом (у "
             "поддельного клиента метод get(path, params=None)). ОБЪЁМ (жёстко): меняй только "
             "command-center/bcc/terminal_cli/cli.py и создай только этот один новый тест-файл; существующие "
             "тесты и файлы не редактируй."),
})
_arg = sys.argv[sys.argv.index("--case") + 1] if "--case" in sys.argv else "goal-budget"
CASE = _arg if _arg in tool.CASES else "goal-budget"


def _strip(argv, flag):
    out, skip = [], False
    for a in argv:
        if skip:
            skip = False
        elif a == flag:
            skip = True
        else:
            out.append(a)
    return out


if __name__ == "__main__":
    argv = sys.argv[1:]
    worker = argv[argv.index("--worker") + 1]
    ev = Path(argv[argv.index("--evidence") + 1]).resolve()
    extra = []
    if worker in LAB_WORKERS:
        extra = ["--allow-paid-worker", worker]
    rc = tool.main(["--case", CASE, "--source-repo", r"C:\Users\asd\Bossman\ns-lab-20261010\repo",
                    "--url", "http://127.0.0.1:8835", "--data-dir", r"C:\Users\asd\Bossman\ns-lab-20261010\data",
                    "--python", r"C:\Users\asd\Bossman\ns-lab-20261010\venv\Scripts\python.exe",
                    "--timeout", "1500", "--no-save", *extra, *_strip(_strip(argv, "--reviewer"), "--case")])
    # independent review (only when the tool reports a patched, holdout-passing candidate)
    cycles = sorted(ev.glob("cycle-*.json"), key=lambda p: p.stat().st_mtime)
    if cycles:
        rep = json.loads(cycles[-1].read_text(encoding="utf-8"))
        rec = json.loads((ev / f"task-{rep['task_id']}.json").read_text(encoding="utf-8"))
        review = {"accepted": False, "why": "not reviewed: tool did not report INDEPENDENT_VERIFICATION_PASS"}
        if rep["stages"].get("INDEPENDENT_VERIFICATION_PASS"):
            import providers
            from bossman_v3.self_improvement import protocol as proto
            diff = (rec.get("diff") or "").replace("\r\n", "\n")
            base = rec["base_commit"]
            import subprocess
            tmp = Path(tempfile.mkdtemp(prefix="gb-review-"))
            repo = Path(rec["source_repo"])
            wt = tmp / "wt"
            subprocess.run(["git", "-C", str(repo), "worktree", "add", "--detach", str(wt), base], check=True,
                           capture_output=True)
            try:
                subprocess.run(["git", "-C", str(wt), "apply", "--whitespace=nowarn", "-"], input=diff.encode(),
                               check=True, capture_output=True)
                sources = {p: (wt / p).read_text(encoding="utf-8", errors="replace")
                           for p in rec.get("changed_files") or [] if (wt / p).is_file()}
            finally:
                subprocess.run(["git", "-C", str(repo), "worktree", "remove", "--force", str(wt)], capture_output=True)
            goal = tool.CASES[CASE]["wish"]
            ctx = proto.review_input(base, diff, sources, goal)
            # free reviewers of a different family than the fixer: Gemini for NVIDIA/Nemotron fixers, Kimi for Gemini
            prov, model = (("nvidia", "moonshotai/kimi-k3") if worker.startswith("gemini")
                           else ("gemini", "gemini-3.5-flash"))
            if "--reviewer" in argv:
                prov, model = argv[argv.index("--reviewer") + 1].split(":", 1)
            review = {"reviewer": f"{prov}:{model}", "fixer_model": rec.get("model")}
            if proto.model_identity(model) == proto.model_identity(str(rec.get("model") or "")):
                review.update(accepted=False, why="reviewer equals fixer model")
            else:
                try:
                    resp = providers.chat(prov, model, proto.review_prompt(ctx), ledger=ev.parent / "spend.jsonl",
                                          purpose="gb:review", max_tokens=6000)
                    parsed = providers.extract_json(resp["text"])
                    review["response"] = parsed
                    proto.validate_review(parsed, ctx)
                    review.update(accepted=True, why="accept with no findings")
                except Exception as exc:  # noqa: BLE001
                    review.update(accepted=False, why=str(exc)[:500])
        (ev / "review.json").write_text(json.dumps(review, ensure_ascii=False, indent=1), encoding="utf-8")
        if review.get("accepted"):
            before = json.loads(next(ev.glob("holdout-*-base.json")).read_text(encoding="utf-8"))
            after = json.loads(next(ev.glob("holdout-*-patched.json")).read_text(encoding="utf-8"))
            recipe, evd, ver = tool.build_recipe(rec, tool.CASES[CASE],
                                                 {"before": before, "after": after, "base_commit": rec["base_commit"]})
            recipe["provenance"]["assistance_level"] = "none"
            from bcc.terminal_cli.api_client import Client, discover
            lab_data = r"C:\Users\asd\Bossman\ns-lab-20261010\data"
            with Client(discover("http://127.0.0.1:8835", lab_data), timeout=60) as c:
                try:
                    saved = c.post("/api/coding-recipes", {"recipe": recipe, "evidence": evd, "verifier": ver,
                                                           "project_id": tool.PROJECT, "scope": "project"})
                except Exception as exc:  # noqa: BLE001
                    saved = {"error": str(exc)[:500]}
            (ev / "recipe.json").write_text(json.dumps({"recipe": recipe, "saved": saved}, ensure_ascii=False, indent=1),
                                            encoding="utf-8")
            print("RECIPE", json.dumps(saved, ensure_ascii=False)[:300])
        print("REVIEW", json.dumps({k: review.get(k) for k in ("reviewer", "accepted", "why")}, ensure_ascii=False))
    sys.exit(rc)
