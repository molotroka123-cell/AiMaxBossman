#!/usr/bin/env python3
"""One registry of the installed-Windows acceptance profile (OA-02).

Three places used to hold their own copy of "what must pass": the workflow's
pytest command listed thirteen files, ``require_acceptance_results.py`` was
told ``--minimum-tests 40`` on its command line, and ``astra6_freeze.py``
still accepted thirteen JUnit cases. They drifted, and the audit of
17 September 2026 proved the aggregator would freeze on a third of the real
profile. Now all three read ``acceptance_registry.json`` beside this file.

    python tools/acceptance_registry.py --paths          # pytest arguments
    python tools/acceptance_registry.py --minimum-tests  # the total floor
    python tools/acceptance_registry.py --json           # the validated registry
"""
from __future__ import annotations

import argparse
import hashlib
import json
import re
from pathlib import Path

REGISTRY = Path(__file__).with_name("acceptance_registry.json")
MODULE = re.compile(r"[A-Za-z_][A-Za-z0-9_]*\Z")


def load(path: Path = REGISTRY) -> dict:
    """The registry, refused when it does not describe a profile."""
    registry = json.loads(path.read_text(encoding="utf-8"))
    if registry.get("schema_version") != 1:
        raise ValueError("acceptance registry: unsupported schema_version")
    junit = registry.get("junit")
    if not isinstance(junit, dict):
        raise ValueError("acceptance registry: junit section missing")
    modules = junit.get("modules")
    if not isinstance(modules, dict) or not modules:
        raise ValueError("acceptance registry: junit.modules must name at least one module")
    for module, minimum in modules.items():
        if not MODULE.fullmatch(module) or not module.startswith("test_"):
            raise ValueError(f"acceptance registry: {module!r} is not a test module name")
        if not isinstance(minimum, int) or isinstance(minimum, bool) or minimum < 1:
            raise ValueError(f"acceptance registry: {module} needs a positive minimum, got {minimum!r}")
    total = sum(modules.values())
    if junit.get("minimum_tests") != total:
        raise ValueError(f"acceptance registry: minimum_tests must equal the per-module sum {total}")
    live = registry.get("live_model")
    if not isinstance(live, dict) or not isinstance(live.get("models"), int) or live["models"] < 1:
        raise ValueError("acceptance registry: live_model.models must be a positive count")
    cases = live.get("cases")
    if not isinstance(cases, list) or not cases or len(set(cases)) != len(cases) \
            or not all(isinstance(case, str) and case for case in cases):
        raise ValueError("acceptance registry: live_model.cases must be distinct non-empty names")
    return registry


def digest(path: Path = REGISTRY) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def pytest_paths(registry: dict) -> list[str]:
    root = registry["junit"]["root"].rstrip("/")
    return [f"{root}/{module}.py" for module in registry["junit"]["modules"]]


def module_of(classname: str) -> str | None:
    """``tests.test_x`` and ``tests.test_x.TestY`` both belong to ``test_x``."""
    for part in (classname or "").split("."):
        if part.startswith("test_"):
            return part
    return None


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--registry", type=Path, default=REGISTRY)
    group = parser.add_mutually_exclusive_group(required=True)
    group.add_argument("--paths", action="store_true", help="one pytest path per line")
    group.add_argument("--minimum-tests", action="store_true")
    group.add_argument("--json", action="store_true")
    args = parser.parse_args(argv)
    registry = load(args.registry)
    if args.paths:
        print("\n".join(pytest_paths(registry)))
    elif args.minimum_tests:
        print(registry["junit"]["minimum_tests"])
    else:
        print(json.dumps(registry, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
