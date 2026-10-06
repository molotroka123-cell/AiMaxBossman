"""Hidden holdout for the tree leaf `pit/discovery.py`: a non-finite or non-numeric field never wins the choice.

    python tools/tree_holdout/discovery_nonfinite.py --repo <checkout> [--out result.json]

Independent of the worker: it lives outside the zone's editable paths and checks the RESULT of
`choose_discovery_question` (who is chosen, in either order, without crashing), not the presence of an
`isfinite` call. Policy (owner, 06.10): an unknown value is never turned into a safe one - a candidate with
NaN/±inf/None in any numeric field is not selectable. Valid behaviour must stay as it was.
Exit 0 = every case passed, 1 = at least one failed, 2 = the repo could not be loaded.
"""
from __future__ import annotations

import argparse
import itertools
import json
import sys
from pathlib import Path

FIELDS = ("relevance", "uncertainty", "future_utility", "annoyance_cost", "sensitivity_risk")
BAD_VALUES = (("nan", float("nan")), ("+inf", float("inf")), ("-inf", float("-inf")), ("None", None))


def cases(mod) -> list[dict]:
    C, choose = mod.DiscoveryCandidate, mod.choose_discovery_question
    out: list[dict] = []

    def record(name: str, fn) -> None:
        try:
            ok, detail = fn()
        except Exception as exc:  # noqa: BLE001 - a crash is a failed case, reported as such
            ok, detail = False, f"{type(exc).__name__}: {exc}"
        out.append({"case": name, "ok": bool(ok), "detail": str(detail)[:300]})

    good = C("good", "good?", 0.9, 0.9, 0.9)
    for field, (label, value) in itertools.product(FIELDS, BAD_VALUES):
        kw = dict(key="bad", question="bad?", relevance=0.9, uncertainty=0.9, future_utility=0.9)
        kw[field] = value
        bad = C(**kw)
        for order in ("bad_first", "good_first"):
            rows = [bad, good] if order == "bad_first" else [good, bad]
            record(f"{field}={label}/{order}",
                   lambda rows=rows: ((r := choose(rows, enabled=True)) is not None and r.key == "good",
                                      f"chosen={getattr(r, 'key', None)}"))
        record(f"{field}={label}/alone_is_not_chosen",
               lambda bad=bad: ((r := choose([bad], enabled=True)) is None, f"chosen={getattr(r, 'key', None)}"))
    # valid behaviour preserved
    a, b, c = C("a", "a?", 0.9, 0.9, 0.9), C("b", "b?", 0.6, 0.6, 0.9), C("c", "c?", 0.5, 0.5, 0.5)
    for perm in itertools.permutations([a, b, c]):
        record("valid/best_wins/" + "".join(x.key for x in perm),
               lambda perm=perm: ((r := choose(list(perm), enabled=True)) is not None and r.key == "a",
                                  f"chosen={getattr(r, 'key', None)}"))
    record("valid/high_sensitivity_excluded",
           lambda: ((r := choose([C("s", "s?", 0.9, 0.9, 0.9, sensitivity_risk=0.5)], enabled=True)) is None, r))
    record("valid/previously_skipped_excluded",
           lambda: ((r := choose([C("p", "p?", 0.9, 0.9, 0.9, previously_skipped=True)], enabled=True)) is None, r))
    record("valid/disabled_returns_none", lambda: ((r := choose([a], enabled=False)) is None, r))
    record("valid/empty_returns_none", lambda: ((r := choose([], enabled=True)) is None, r))
    record("valid/below_threshold_none",
           lambda: ((r := choose([C("low", "low?", 0.1, 0.1, 0.1)], enabled=True)) is None, r))
    record("valid/score_of_good_is_finite_number",
           lambda: (isinstance(s := mod.score(good), float) and s == s and abs(s) != float("inf"), s))
    return out


def run(repo: Path) -> dict:
    for sub in ("command-center", "bossman-core", "."):
        sys.path.insert(0, str((repo / sub).resolve()))
    import importlib
    mod = importlib.import_module("bcc.pit.discovery")
    if not str(Path(mod.__file__).resolve()).startswith(str(repo.resolve())):
        raise RuntimeError(f"imported {mod.__file__}, not the checkout {repo}")
    rows = cases(mod)
    failed = [r for r in rows if not r["ok"]]
    return {"holdout": "discovery_nonfinite/1", "module": str(mod.__file__), "total": len(rows),
            "failed": len(failed), "passed": not failed, "failures": failed[:40], "cases": rows}


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--repo", required=True, type=Path)
    ap.add_argument("--out", type=Path)
    args = ap.parse_args(argv)
    try:
        result = run(args.repo)
    except Exception as exc:  # noqa: BLE001
        print(json.dumps({"holdout": "discovery_nonfinite/1", "error": f"{type(exc).__name__}: {exc}"}))
        return 2
    text = json.dumps(result, ensure_ascii=False, indent=1)
    if args.out:
        args.out.write_text(text, encoding="utf-8")
    print(f"HOLDOUT discovery_nonfinite: {result['total'] - result['failed']}/{result['total']} passed")
    for r in result["failures"][:12]:
        print(f"  FAIL {r['case']}: {r['detail']}")
    return 0 if result["passed"] else 1


if __name__ == "__main__":
    sys.exit(main())
