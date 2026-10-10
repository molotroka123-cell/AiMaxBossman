"""Hidden holdout: `bossman list tasks --limit N` returns N tasks, not a silent 500.

    python tools/tree_holdout/cli_task_paging.py --repo <checkout> [--out result.json]

Found by Bossman's own UX soak (tools/ux_soak/soak.py cli_check, 10.10.2026): after more than 500 tasks existed
`list tasks --json --limit 1000` returned exactly 500 while the API held 837 -> 42 `high` findings
"CLI vs API tasks differ". `GET /api/tasks` clamps `limit` to 500 by design and offers `before_id` paging; the CLI
passed `limit` through and returned the clamped page without saying so. Policy: an explicit `--limit N` returns
min(N, available) tasks, newest first, no duplicates; small limits and small stores behave as before.
The holdout drives `bcc.terminal_cli.cli.list_items` with a fake client that mimics the real route (500 cap,
`before_id`, id DESC). The case set was written by the harness author; the fix must come from a Bossman worker.
Exit 0 = all passed, 1 = some failed, 2 = the repo could not be loaded.
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path


class FakeApi:
    """Mimics command-center/bcc/api.py list_tasks: limit clamped to 1..500, `before_id`, newest first."""

    def __init__(self, total: int):
        self.total = total
        self.calls = 0

    def get(self, path, params=None, **_kw):
        if path != "/api/tasks":
            raise AssertionError(f"unexpected GET {path}")
        self.calls += 1
        params = dict(params or {})
        limit = max(1, min(int(params.get("limit", 100)), 500))
        before = params.get("before_id")
        ids = [i for i in range(self.total, 0, -1) if before is None or i < int(before)]
        return [{"id": i, "status": "completed", "title": f"task {i}", "last_run": None} for i in ids[:limit]]

    def post(self, path, body=None, **_kw):
        raise AssertionError(f"unexpected POST {path}")


def run(repo: Path) -> dict:
    for p in ((repo / "command-center").resolve(), (repo / "bossman-core").resolve(), repo.resolve()):
        sys.path.insert(0, str(p))
    import importlib
    cli = importlib.import_module("bcc.terminal_cli.cli")
    if not str(Path(cli.__file__).resolve()).startswith(str(repo.resolve())):
        raise RuntimeError(f"imported {cli.__file__}, not the checkout {repo}")
    rows: list[dict] = []

    def record(name, total, limit, expect):
        api = FakeApi(total)
        try:
            got = cli.list_items(api, "tasks", limit=limit)
            ids = [int(t["id"]) for t in got]
            ok = len(ids) == expect and len(set(ids)) == len(ids) and ids == sorted(ids, reverse=True)
            detail = f"returned {len(ids)} (expected {expect}), unique={len(set(ids))}, first={ids[:1]}, last={ids[-1:]}, api_calls={api.calls}"
        except Exception as exc:  # noqa: BLE001
            ok, detail = False, f"{type(exc).__name__}: {exc}"
        rows.append({"case": name, "ok": bool(ok), "detail": detail[:300]})

    record("defect: --limit 1000 with 1200 tasks returns 1000", 1200, 1000, 1000)
    record("defect: --limit 600 with 700 tasks returns 600", 700, 600, 600)
    record("defect: --limit 1500 with 837 tasks returns all 837", 837, 1500, 837)
    record("valid: --limit 20 with 700 tasks returns the newest 20", 700, 20, 20)
    record("valid: --limit 500 with 800 tasks returns 500", 800, 500, 500)
    record("valid: --limit 1000 with 300 tasks returns 300 once", 300, 1000, 300)
    record("valid: empty store returns nothing", 0, 100, 0)
    failed = [r for r in rows if not r["ok"]]
    return {"holdout": "cli_task_paging/1", "module": str(cli.__file__), "total": len(rows), "failed": len(failed),
            "passed": not failed, "failures": failed, "cases": rows}


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--repo", required=True, type=Path)
    ap.add_argument("--out", type=Path)
    args = ap.parse_args(argv)
    try:
        result = run(args.repo)
    except Exception as exc:  # noqa: BLE001
        print(json.dumps({"holdout": "cli_task_paging/1", "error": f"{type(exc).__name__}: {exc}"}))
        return 2
    if args.out:
        args.out.write_text(json.dumps(result, ensure_ascii=False, indent=1), encoding="utf-8")
    print(f"HOLDOUT cli_task_paging: {result['total'] - result['failed']}/{result['total']} passed")
    for r in result["failures"]:
        print(f"  FAIL {r['case']}: {r['detail']}")
    return 0 if result["passed"] else 1


if __name__ == "__main__":
    sys.exit(main())
