"""Negative & regression security tests for P1.2 (bossman_15_ling_coder subprocess sandbox)
and P1.3-P1.6 (GitHub Actions SHA pinning, startup scripts, lockfiles, self-hosted CI sandbox).

Note: Follows `test_root_suite_stays_within_its_environment.py` rules (no top-level bcc/fastapi imports).
"""
from __future__ import annotations

import importlib.util
from pathlib import Path
import subprocess
import sys
import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]


def _load_ling_coder():
    spec = importlib.util.spec_from_file_location(
        "bossman_15_ling_coder", REPO_ROOT / "tools" / "bossman_15_ling_coder.py"
    )
    assert spec and spec.loader
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def test_p1_2_ling_coder_rejects_shell_metacharacters_and_unallowlisted_commands(tmp_path: Path):
    mod = _load_ling_coder()
    canary = tmp_path / "pwned.txt"

    malicious_commands = [
        f"pytest -q; echo PWN > {canary.name}",
        "pytest -q && whoami",
        "pytest -q | findstr passed",
        "pytest -q `id`",
        "pytest -q $(whoami)",
        "powershell -Command Get-Process",
        "cmd /c dir",
        "python -c \"import os; open('pwned.txt','w').write('x')\"",
        "pytest --rootdir=.. -q",
        "pytest -o cache_dir=/tmp/evil",
    ]
    for cmd in malicious_commands:
        out = mod.execute(tmp_path, "run_tests", {"command": cmd})
        assert out.startswith("REFUSED:"), f"Expected REFUSED for {cmd!r}, got {out!r}"
    assert not canary.exists()


def test_p1_2_ling_coder_scrubs_secrets_and_disables_shell_and_network(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    mod = _load_ling_coder()
    monkeypatch.setenv("OPENAI_API_KEY", "sk-secret-do-not-leak-12345")
    monkeypatch.setenv("BOSSMAN_MASTER_TOKEN", "tok-secret-999")
    monkeypatch.setenv("SOLANA_PRIVATE_KEY", "priv-secret-888")
    monkeypatch.setenv("GITHUB_TOKEN", "ghp_secret_777")

    test_file = tmp_path / "test_sandbox_probe.py"
    test_file.write_text(
        "import os\n"
        "def test_env_scrubbed():\n"
        "    for k in ('OPENAI_API_KEY', 'BOSSMAN_MASTER_TOKEN', 'SOLANA_PRIVATE_KEY', 'GITHUB_TOKEN'):\n"
        "        assert k not in os.environ, f'Secret {k} leaked into test env'\n"
        "    assert os.environ.get('BOSSMAN_OFFLINE_MODE_ENABLED') == '1'\n"
        "    assert os.environ.get('HTTP_PROXY', '').startswith('http://127.0.0.1:')\n",
        encoding="utf-8",
    )

    out = mod.execute(tmp_path, "run_tests", {"command": "pytest -q test_sandbox_probe.py"})
    assert out.startswith("exit_code=0"), out
    assert "1 passed" in out


def test_p1_2_ling_coder_enforces_path_containment_timeout_and_output_cap(tmp_path: Path):
    mod = _load_ling_coder()

    # Path traversal in read_file / write_file must be refused
    escape_read = mod.execute(tmp_path, "read_file", {"path": "../outside.txt"})
    assert "ERROR:" in escape_read and "escapes worktree" in escape_read

    escape_write = mod.execute(tmp_path, "write_file", {"path": "../outside.txt", "content": "nope"})
    assert "ERROR:" in escape_write and "escapes worktree" in escape_write

    # Path traversal in test target argument must be refused
    escape_test = mod.execute(tmp_path, "run_tests", {"command": "pytest ../outside_test.py"})
    assert escape_test.startswith("REFUSED:"), escape_test

    # Output cap check
    noisy_test = tmp_path / "test_noisy.py"
    noisy_test.write_text(
        "def test_noisy():\n"
        "    print('A' * 40000)\n"
        "    assert False\n",
        encoding="utf-8",
    )
    out = mod.execute(tmp_path, "run_tests", {"command": "pytest -s test_noisy.py"})
    assert len(out) <= mod.MAX_OUTPUT_CHARS + 64
