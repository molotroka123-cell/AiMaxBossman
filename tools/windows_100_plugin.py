"""pytest plugin for tools/windows_100_real.py: one JSON line per test phase.

Loaded with `-p windows_100_plugin`; writes to $BOSSMAN_W100_REPORT. The gate
decides from these records, not from pytest's exit code alone, so a skipped,
missing or extra nodeid can never pass as one of the 100.
"""
from __future__ import annotations

import json
import os


def pytest_runtest_logreport(report):
    path = os.environ.get("BOSSMAN_W100_REPORT")
    if not path:
        return
    with open(path, "a", encoding="utf-8") as fh:
        fh.write(json.dumps({"nodeid": report.nodeid, "when": report.when,
                             "outcome": report.outcome}, ensure_ascii=False) + "\n")


def pytest_collection_modifyitems(config, items):
    """At collection: name the tests this platform/env will skip by marker, so
    the gate never selects a test that cannot run here (a skip at run time is
    still a FAIL)."""
    path = os.environ.get("BOSSMAN_W100_SKIPS")
    if not path:
        return
    from _pytest.skipping import evaluate_skip_marks
    with open(path, "a", encoding="utf-8") as fh:
        for item in items:
            skip = evaluate_skip_marks(item)
            if skip is not None:
                fh.write(json.dumps({"nodeid": item.nodeid, "reason": skip.reason},
                                    ensure_ascii=False) + "\n")
