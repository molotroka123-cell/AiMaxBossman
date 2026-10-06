"""Hidden holdout for the tree leaf `autonomy/goals.py`: an unknown cost never slips under the goal's budget cap.

    python tools/tree_holdout/goal_budget_nonfinite.py --repo <checkout> [--out result.json]

Found on the owner PC 06.10 (c79eaec2): `GoalStore.charge(cost_usd=nan)` makes usage NaN for good, and
`NaN > max_cost_usd` is always False - a goal with a $1 cap was charged $5 and stayed un-blocked. Policy: an
unknown or impossible cost (NaN, ±inf, negative, non-number) is never treated as "within budget"; the store may
refuse such a charge (an exception) or block the goal, but afterwards the budget must still bind. Valid charging
must behave as before. Exit 0 = all passed, 1 = some failed, 2 = the repo could not be loaded.
"""
from __future__ import annotations

import argparse
import dataclasses
import json
import sys
import tempfile
from pathlib import Path

BAD_COSTS = (("nan", float("nan")), ("+inf", float("inf")), ("-inf", float("-inf")), ("negative", -5.0),
             ("str-nan", "nan"))


def cases(goals_mod, journal_mod, make_goal) -> list[dict]:
    out: list[dict] = []

    def record(name: str, fn) -> None:
        try:
            ok, detail = fn()
        except Exception as exc:  # noqa: BLE001
            ok, detail = False, f"{type(exc).__name__}: {exc}"
        out.append({"case": name, "ok": bool(ok), "detail": str(detail)[:300]})

    def fresh(cap: float = 1.0):
        root = Path(tempfile.mkdtemp(prefix="holdout-goal-"))
        store = goals_mod.GoalStore(root, journal_mod.Journal(root / "journal.jsonl"))
        g = make_goal("H-1")
        store.create(dataclasses.replace(g, budget=dataclasses.replace(g.budget, max_cost_usd=cap)))
        return store

    def bound_after_bad(value):
        store = fresh()
        try:
            store.charge("H-1", cost_usd=value)
        except Exception:  # noqa: BLE001 - refusing the charge is an allowed policy
            pass
        rec = store.get("H-1")
        if rec["state"] == "BLOCKED" or store.budget_exceeded(rec):
            return True, f"blocked right after the bad charge (state={rec['state']})"
        try:
            store.charge("H-1", cost_usd=5.0)
        except Exception:  # noqa: BLE001
            pass
        rec = store.get("H-1")
        return (rec["state"] == "BLOCKED" or bool(store.budget_exceeded(rec)),
                f"usage={rec.get('usage')} state={rec['state']} exceeded={store.budget_exceeded(rec)!r}")

    for label, value in BAD_COSTS:
        record(f"charge {label} then $5 over a $1 cap -> budget binds", lambda value=value: bound_after_bad(value))

    def stored_nan():
        store = fresh()
        rec = store.get("H-1")
        rec = dict(rec, usage={"agent_turns": 0, "cost_usd": float("nan")})
        return bool(store.budget_exceeded(rec)), f"exceeded={store.budget_exceeded(rec)!r}"
    record("usage already NaN on disk (older build) counts as exceeded", stored_nan)

    def valid_under():
        store = fresh()
        store.charge("H-1", cost_usd=0.4)
        rec = store.charge("H-1", cost_usd=0.4)
        return (not store.budget_exceeded(rec) and rec["state"] != "BLOCKED" and abs(rec["usage"]["cost_usd"] - 0.8) < 1e-9,
                f"usage={rec['usage']} state={rec['state']}")
    record("valid: $0.8 under a $1 cap stays open and is summed", valid_under)

    def valid_over():
        store = fresh()
        for _ in range(3):
            store.charge("H-1", cost_usd=0.4)
        rec = store.get("H-1")
        return store.budget_exceeded(rec) == "cost", f"exceeded={store.budget_exceeded(rec)!r}"
    record("valid: $1.2 over a $1 cap is 'cost'", valid_over)

    def valid_zero():
        store = fresh()
        rec = store.charge("H-1", cost_usd=0.0, agent_turns=1)
        return not store.budget_exceeded(rec), f"exceeded={store.budget_exceeded(rec)!r}"
    record("valid: a $0 charge is fine", valid_zero)
    return out


def run(repo: Path) -> dict:
    cc = (repo / "command-center").resolve()
    for p in (cc / "tests", cc, (repo / "bossman-core").resolve(), repo.resolve()):
        sys.path.insert(0, str(p))
    import importlib
    goals_mod = importlib.import_module("bcc.autonomy.goals")
    if not str(Path(goals_mod.__file__).resolve()).startswith(str(repo.resolve())):
        raise RuntimeError(f"imported {goals_mod.__file__}, not the checkout {repo}")
    journal_mod = importlib.import_module("bcc.autonomy.journal")
    make_goal = importlib.import_module("autonomy_fakes").make_goal
    rows = cases(goals_mod, journal_mod, make_goal)
    failed = [r for r in rows if not r["ok"]]
    return {"holdout": "goal_budget_nonfinite/1", "module": str(goals_mod.__file__), "total": len(rows),
            "failed": len(failed), "passed": not failed, "failures": failed, "cases": rows}


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--repo", required=True, type=Path)
    ap.add_argument("--out", type=Path)
    args = ap.parse_args(argv)
    try:
        result = run(args.repo)
    except Exception as exc:  # noqa: BLE001
        print(json.dumps({"holdout": "goal_budget_nonfinite/1", "error": f"{type(exc).__name__}: {exc}"}))
        return 2
    if args.out:
        args.out.write_text(json.dumps(result, ensure_ascii=False, indent=1), encoding="utf-8")
    print(f"HOLDOUT goal_budget_nonfinite: {result['total'] - result['failed']}/{result['total']} passed")
    for r in result["failures"][:12]:
        print(f"  FAIL {r['case']}: {r['detail']}")
    return 0 if result["passed"] else 1


if __name__ == "__main__":
    sys.exit(main())
