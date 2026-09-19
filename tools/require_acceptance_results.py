#!/usr/bin/env python3
"""Reject a required acceptance suite that failed, errored, skipped, or is not the profile.

Two modes, chosen by the caller:

* ``--profile windows-installed`` — the profile is ``tools/acceptance_registry.json``:
  every module it names must be present with at least its declared number of
  clean cases, the total must reach the registry floor, no (classname, name)
  may repeat, and with ``--source-sha`` the suite's own ``<properties>`` must
  name that source SHA. Thirteen real cases, forty repetitions of one case, or
  a results file produced for another commit are all refused here, before the
  aggregator (OA-02).
* ``--minimum-tests N`` without a profile — the contract every other suite uses
  (local bundle, shipped apps): at least N cases, none failed, errored or
  skipped, no repeated identity. Those suites are not the Windows profile and
  must not be judged by it.
"""
from __future__ import annotations

import argparse
from collections import Counter
from pathlib import Path
import sys
import xml.etree.ElementTree as ET

sys.path.insert(0, str(Path(__file__).resolve().parent))
import acceptance_registry  # noqa: E402

PROFILES = ("windows-installed",)


def properties(root: ET.Element) -> dict[str, str]:
    found: dict[str, str] = {}
    for prop in root.iter("property"):
        name, value = prop.get("name"), prop.get("value")
        if isinstance(name, str) and isinstance(value, str) and name not in found:
            found[name] = value
    return found


def verify(path: Path, minimum_tests: int | None = None, source_sha: str | None = None,
           registry: dict | None = None) -> dict:
    """``registry`` given → the profile contract; otherwise the plain floor contract."""
    if registry is None and not minimum_tests:
        raise ValueError("either a registry profile or a positive --minimum-tests is required")
    root = ET.parse(path).getroot()
    cases = list(root.iter("testcase"))
    counts = {"tests": len(cases), **{name: sum(case.find(name) is not None for case in cases)
              for name in ("failure", "error", "skipped")}}
    problems: list[str] = []
    for key in ("failure", "error", "skipped"):
        if counts[key]:
            problems.append(f"{counts[key]} {key}")
    identities = [(case.get("classname", ""), case.get("name")) for case in cases]
    if any(not name for _, name in identities):
        problems.append("a testcase has no name")
    if len(set(identities)) != len(identities):
        problems.append("repeated testcase identities")
    clean = [case for case in cases
             if all(case.find(tag) is None for tag in ("failure", "error", "skipped"))]
    result: dict = dict(counts)
    if registry is not None:
        per_module = Counter(acceptance_registry.module_of(case.get("classname", "")) for case in clean)
        modules = registry["junit"]["modules"]
        for module, minimum in modules.items():
            if per_module.get(module, 0) < minimum:
                problems.append(f"{module}: {per_module.get(module, 0)} < {minimum}")
        counted = sum(per_module.get(module, 0) for module in modules)
        floor = max(registry["junit"]["minimum_tests"], minimum_tests or 0)
        if counted < floor:
            problems.append(f"{counted} profile cases < {floor}")
        result["per_module"] = {module: per_module.get(module, 0) for module in modules}
        result["profile"] = registry.get("profile")
    elif len(clean) < (minimum_tests or 0):
        problems.append(f"{len(clean)} clean cases < {minimum_tests}")
    if source_sha:
        found = properties(root).get("source_sha")
        if found != source_sha:
            problems.append(f"results.xml names source_sha {found!r}, expected {source_sha}")
    if problems:
        raise SystemExit(f"Required acceptance did not pass: {'; '.join(problems)}; counts={counts}")
    return result


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("results", type=Path)
    parser.add_argument("--profile", choices=PROFILES, default=None,
                        help="judge by the acceptance registry profile instead of a plain floor")
    parser.add_argument("--minimum-tests", type=int, default=None,
                        help="plain floor without --profile; an extra floor on top of the profile with it")
    parser.add_argument("--source-sha", default=None,
                        help="the results must carry this source_sha in their <properties>")
    parser.add_argument("--registry", type=Path, default=acceptance_registry.REGISTRY)
    args = parser.parse_args()
    if args.minimum_tests is not None and args.minimum_tests < 1:
        parser.error("minimum tests must be positive")
    if args.profile is None and args.minimum_tests is None:
        parser.error("--minimum-tests is required without --profile")
    registry = None
    if args.profile:
        registry = acceptance_registry.load(args.registry)
        if registry.get("profile") != args.profile:
            parser.error(f"registry describes profile {registry.get('profile')!r}, not {args.profile!r}")
    print(verify(args.results, args.minimum_tests, args.source_sha, registry))
