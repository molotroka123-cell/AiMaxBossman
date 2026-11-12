"""Seed custom tools of the PC operator (tools/pc_operator_seed_tools): facts are computed, policy accepts the code."""
from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
TOOLS = ROOT / "tools" / "pc_operator_seed_tools" / "pc_tools.py"
pytestmark = pytest.mark.skipif(sys.platform != "win32", reason="Windows PC tools")


def run(*args: str) -> str:
    r = subprocess.run([sys.executable, str(TOOLS), *args], capture_output=True, text=True, encoding="utf-8",
                       env={**__import__("os").environ, "PYTHONIOENCODING": "utf-8"}, timeout=60)
    assert r.returncode == 0, r.stderr
    return r.stdout.strip().splitlines()[-1]


def test_find_files_counts_by_extension_recursively(tmp_path):
    for i, sub in enumerate(["a", "a/b", "c"]):
        (tmp_path / sub).mkdir(parents=True, exist_ok=True)
        (tmp_path / sub / f"x{i}.csv").write_text("1" * (i + 1))
        (tmp_path / sub / f"y{i}.log").write_text("1")
    assert run("find_files", "--root", str(tmp_path), "--ext", "csv").startswith("ИТОГ: файлов 3")


def test_json_get_nested_value_array_length_and_ini(tmp_path):
    p = tmp_path / "c.json"
    p.write_text(json.dumps({"app": {"server": {"port": 4321}}, "nodes": [1, 2, 3]}))
    assert run("json_get", "--path", str(p), "--key", "app.server.port") == "ИТОГ: app.server.port = 4321"
    assert "из 3 элементов" in run("json_get", "--path", str(p), "--key", "nodes")
    ini = tmp_path / "s.ini"
    ini.write_text("[service]\nkey_a=1\ntimeout_ms=777\n")
    assert run("json_get", "--path", str(ini), "--key", "timeout_ms") == "ИТОГ: timeout_ms = 777"


def test_count_lines_variants(tmp_path):
    p = tmp_path / "l.log"
    p.write_text("h\nERROR a\nok\nERROR b\n")
    assert run("count_lines", "--path", str(p)) == "ИТОГ: строк всего: 4"
    assert run("count_lines", "--path", str(p), "--contains", "ERROR").endswith(": 2")
    assert run("count_lines", "--path", str(p), "--skip_header", "1").endswith(": 3")


def test_disk_free_reports_missing_drive_without_a_number():
    import os
    import string
    missing = next(d for d in reversed(string.ascii_uppercase) if not os.path.exists(f"{d}:\\"))
    line = run("disk_free", "--drive", missing)
    assert f"диска {missing}: на этом ПК нет" in line


def test_close_window_refuses_short_title():
    assert "отказ" in run("close_window", "--title", "ab")


def test_seed_tools_pass_the_operator_code_policy():
    r = subprocess.run([sys.executable, str(ROOT / "tools" / "pc_operator_seed_tools" / "install.py"), "--check"],
                       capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=120)
    assert r.returncode == 0, r.stdout + r.stderr
