#!/usr/bin/env python3
"""Owner CLI for bounded Bossman evolution experiments (Python 3.11+)."""
from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT), str(ROOT / "bossman-core")]
from bossman_v3.self_improvement.runner import (ClaudeProposer, LocalProposer,
    LearningStore, export_verified, git, load_suite, run)


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("action", choices=("assess", "run", "report", "export"))
    parser.add_argument("--suite", type=Path, default=ROOT / "config/evolution/owner-v1.1.json")
    parser.add_argument("--work", type=Path)
    parser.add_argument("--backend", choices=("local", "claude"), default="local")
    parser.add_argument("--model")
    parser.add_argument("--local-url", default="http://127.0.0.1:8080/v1")
    parser.add_argument("--allow-cloud", action="store_true")
    parser.add_argument("--iterations", type=int, default=3)
    parser.add_argument("--max-usd", type=float, default=2.0)
    parser.add_argument("--proposal-usd", type=float, default=0.5)
    parser.add_argument("--timeout", type=int, default=180)
    parser.add_argument("--executor", choices=("host", "docker"), default="docker")
    parser.add_argument("--image", default="bossman-evolution:1.1")
    parser.add_argument("--max-seconds", type=int, default=1800)
    args = parser.parse_args(argv)
    try:
        work = args.work or Path.home() / ".bossman/evolution" / git(ROOT, "rev-parse", "--short=12", "HEAD")
        if args.action == "report":
            print((work / "report.json").read_text(encoding="utf-8"))
            return 0
        if args.action == "export":
            suite = load_suite(ROOT, args.suite)
            count = export_verified(LearningStore(work / "learning"), work / "verified-training.jsonl",
                                    {c["id"] for c in suite["cases"] if c["role"] == "train"})
            print(json.dumps({"examples": count, "path": str(work / "verified-training.jsonl")}))
            return 0
        proposer = None
        if args.action == "run":
            if args.executor != "docker":
                raise ValueError("Model-written code requires the Docker executor; host is assessment-only")
            if args.backend == "claude":
                if not args.allow_cloud or os.environ.get("LOCAL_ONLY", "").lower() in {"1", "true", "yes"}:
                    raise ValueError("Cloud requires --allow-cloud and LOCAL_ONLY must not be active")
                proposer = ClaudeProposer(args.model)
            else:
                proposer = LocalProposer(args.local_url, args.model or "")
        result = run(ROOT, load_suite(ROOT, args.suite), work, proposer=proposer,
                     iterations=args.iterations, max_usd=args.max_usd, proposal_usd=args.proposal_usd,
                     timeout=args.timeout, local=args.backend == "local", model=args.model or args.backend,
                     executor=args.executor, image=args.image, max_seconds=args.max_seconds)
        print(json.dumps({"status": result["status"], "base_sha": result["base_sha"],
                          "candidate_sha": result["champion_sha"], "reserved_usd": result["reserved_usd"],
                          "attempts": len(result["attempts"]), "report": str(work / "report.json")}, ensure_ascii=False))
        return 2 if result["status"].startswith("BLOCKED") else 0
    except (OSError, ValueError, RuntimeError) as exc:
        print("Evolution blocked: " + str(exc), file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
