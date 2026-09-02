from __future__ import annotations

import argparse
import json
from pathlib import Path

from .engine import BenchmarkRunner, compare_reports, load_latest, render_markdown


def main() -> None:
    parser = argparse.ArgumentParser(prog="python -m bossman.benchmark")
    subs = parser.add_subparsers(dest="command", required=True)
    run = subs.add_parser("run", help="run runtime-bound benchmark fixtures")
    run.add_argument("--tier", choices=("smoke", "pr", "nightly", "release"), required=True)
    run.add_argument("--sha", help="commit SHA to bind into the report")
    run.add_argument("--output", type=Path, help="result/history directory")
    run.add_argument("--allow-live", action="store_true", help="requires owner and budget environment attestations too")
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
        data, json_path, markdown_path = runner.run(args.tier, sha=args.sha, allow_live=args.allow_live)
        print(json.dumps({"status": data["release_gate"]["status"], "json": str(json_path), "markdown": str(markdown_path), "commit_sha": data["commit_sha"]}, sort_keys=True))
    elif args.command == "compare":
        comparison = compare_reports(load_latest(runner.output_root, args.base), load_latest(runner.output_root, args.candidate))
        print(json.dumps(comparison, indent=2, sort_keys=True))
    else:
        data = load_latest(runner.output_root, args.sha or __import__("subprocess").check_output(["git", "rev-parse", "HEAD"], text=True).strip())
        print(render_markdown(data))


if __name__ == "__main__":
    main()
