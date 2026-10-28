"""Topbar health badge (master prompt 28.10, task 2): core vs optional adapters.

Live finding: the badge said «сбой» only because 16 external providers (no key / offline) and the models/providers
aggregates were in `error` while db, queue, scheduler and every tick were `ok`. The split logic is a pure module
(ui/health_split.js) executed in node; the shell must use it instead of the old flat any-error rule.
"""
from __future__ import annotations

import shutil
import subprocess
from pathlib import Path

import pytest

UI = Path(__file__).resolve().parents[1] / "ui"


def test_shell_uses_the_split_and_no_longer_flattens_every_component():
    app = (UI / "app.js").read_text(encoding="utf-8")
    assert "import { splitHealth } from './health_split.js'" in app
    assert "splitHealth(rawHealth)" in app
    assert "values.some((v) => ['down', 'error'" not in app, "the flat any-component-error rule is back"
    assert "ядро: сбой" in app and "адаптеры:" in app


def test_split_logic_runs_in_node():
    node = shutil.which("node")
    if not node:
        pytest.skip("Node unavailable: ui/tests/health_split.test.mjs was not executed")
    r = subprocess.run([node, "--test", str(UI / "tests" / "health_split.test.mjs")], capture_output=True, text=True,
                       encoding="utf-8", errors="replace", timeout=120)
    assert r.returncode == 0, r.stdout[-1500:] + r.stderr[-500:]
    assert "# pass 5" in r.stdout and "# fail 0" in r.stdout
