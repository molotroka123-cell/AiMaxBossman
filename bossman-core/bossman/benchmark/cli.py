from __future__ import annotations

import argparse
import json
from pathlib import Path

import sys

from .engine import BenchmarkRunner, ShaMismatch, compare_reports, load_latest, render_markdown, run_isolated


def main() -> None:
    parser = argparse.ArgumentParser(prog="python -m bossman.benchmark")
    subs = parser.add_subparsers(dest="command", required=True)
    run = subs.add_parser("run", help="run runtime-bound benchmark fixtures")
    run.add_argument("--tier", choices=("smoke", "pr", "nightly", "release"), required=True)
    run.add_argument("--sha", help="commit SHA that MUST equal the executing checkout (refused otherwise)")
    run.add_argument("--output", type=Path, help="result/history directory")
    run.add_argument("--allow-live", action="store_true", help="requires owner and budget environment attestations too")
    iso = subs.add_parser("run-isolated", help="benchmark a commit by executing ITS code in a detached git worktree")
    iso.add_argument("--sha", required=True)
    iso.add_argument("--tier", choices=("smoke", "pr", "nightly", "release"), required=True)
    iso.add_argument("--output", type=Path, help="result/history directory")
    iso.add_argument("--allow-live", action="store_true")
    ciso = subs.add_parser("compare-isolated", help="run base and candidate each in their own worktree, then compare")
    ciso.add_argument("--base", required=True)
    ciso.add_argument("--candidate", required=True)
    ciso.add_argument("--tier", choices=("smoke", "pr", "nightly", "release"), default="pr")
    ciso.add_argument("--output", type=Path, help="result/history directory")
    compare = subs.add_parser("compare", help="compare newest reports for two SHAs")
    compare.add_argument("--base", required=True)
    compare.add_argument("--candidate", required=True)
    compare.add_argument("--output", type=Path, help="result/history directory")
    report = subs.add_parser("report", help="render a saved report")
    report.add_argument("--latest", action="store_true", required=True)
    report.add_argument("--sha", help="default: current HEAD")
    report.add_argument("--output", type=Path, help="result/history directory")
    args = parser.parse_args()
    runner = BenchmarkRunner(output_root=getattr(args, "output", None))
    if args.command == "run":
        try:
            data, json_path, markdown_path = runner.run(args.tier, sha=args.sha, allow_live=args.allow_live)
        except ShaMismatch as exc:
            print(json.dumps({"status": "REFUSED", "error": "ShaMismatch", "reason": str(exc)}, sort_keys=True), file=sys.stderr)
            raise SystemExit(3)
        print(json.dumps({"status": data["release_gate"]["status"], "json": str(json_path), "markdown": str(markdown_path), "commit_sha": data["commit_sha"], "scores": data["scores"]}, sort_keys=True))
        if not data["release_gate"]["ready"]:
            # P0: a NO-GO gate must fail the process — exit code is part of the contract.
            raise SystemExit(1)
    elif args.command == "run-isolated":
        try:
            env = run_isolated(args.sha, args.tier, runner.output_root, allow_live=args.allow_live)
        except ShaMismatch as exc:
            print(json.dumps({"status": "REFUSED", "error": "ShaMismatch", "reason": str(exc)}, sort_keys=True), file=sys.stderr)
            raise SystemExit(3)
        print(json.dumps({"resolved_sha": env["resolved_sha"], "worktree_head": env["worktree_head"], "child_returncode": env["child_returncode"], "scores": env["scores"]}, sort_keys=True))
    elif args.command == "compare-isolated":
        base = run_isolated(args.base, args.tier, runner.output_root)
        cand = run_isolated(args.candidate, args.tier, runner.output_root)
        comparison = compare_reports(load_latest(runner.output_root, base["resolved_sha"]), load_latest(runner.output_root, cand["resolved_sha"]))
        comparison["isolation"] = {"base_worktree_head": base["worktree_head"], "candidate_worktree_head": cand["worktree_head"],
                                   "base_engine_hash": base["engine_hash_in_worktree"], "candidate_engine_hash": cand["engine_hash_in_worktree"]}
        print(json.dumps(comparison, indent=2, sort_keys=True))
    elif args.command == "compare":
        comparison = compare_reports(load_latest(runner.output_root, args.base), load_latest(runner.output_root, args.candidate))
        print(json.dumps(comparison, indent=2, sort_keys=True))
    else:
        data = load_latest(runner.output_root, args.sha or __import__("subprocess").check_output(["git", "rev-parse", "HEAD"], text=True).strip())
        print(render_markdown(data))


if __name__ == "__main__":
    main()
