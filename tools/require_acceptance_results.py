#!/usr/bin/env python3
"""Reject a required acceptance suite that failed, errored, or silently skipped."""
from __future__ import annotations

import argparse
from pathlib import Path
import xml.etree.ElementTree as ET


def verify(path: Path, minimum_tests: int) -> dict[str, int]:
    cases = list(ET.parse(path).getroot().iter("testcase"))
    counts = {"tests": len(cases), **{name: sum(case.find(name) is not None for case in cases)
              for name in ("failure", "error", "skipped")}}
    if counts["tests"] < minimum_tests or any(counts[key] for key in ("failure", "error", "skipped")):
        raise SystemExit(f"Required acceptance did not pass: {counts}; minimum tests={minimum_tests}")
    return counts


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("results", type=Path)
    parser.add_argument("--minimum-tests", type=int, required=True)
    args = parser.parse_args()
    if args.minimum_tests < 1:
        parser.error("minimum tests must be positive")
    print(verify(args.results, args.minimum_tests))
