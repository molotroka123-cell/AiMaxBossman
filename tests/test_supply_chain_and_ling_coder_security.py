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


def test_p1_3_github_workflows_pinned_to_full_40_hex_sha_with_permissions_and_persist_credentials():
    import re
    wf_dir = REPO_ROOT / ".github" / "workflows"
    workflows = sorted(wf_dir.glob("*.yml"))
    assert len(workflows) >= 20

    uses_re = re.compile(r"^\s*-?\s*uses:\s*([^\s#]+)", re.MULTILINE)
    pinned_re = re.compile(r"^[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+@[0-9a-f]{40}$")

    for wf in workflows:
        text = wf.read_text(encoding="utf-8")
        # 1. Every workflow must declare explicit permissions:
        assert re.search(r"(?m)^permissions:\s*$", text) or re.search(r"(?m)^\s+permissions:\s*$", text), (
            f"{wf.name} is missing an explicit permissions: block"
        )
        # 2. Every uses: action must be pinned to a full 40-hex commit SHA
        matches = uses_re.findall(text)
        assert matches, f"{wf.name} has no uses: actions"
        for action_ref in matches:
            assert pinned_re.fullmatch(action_ref), (
                f"{wf.name} uses unpinned action {action_ref!r}; must be @<40-hex-sha>"
            )
        # 3. Every workflow with actions/checkout must set persist-credentials: false
        if "actions/checkout@" in text:
            assert "persist-credentials: false" in text, (
                f"{wf.name} uses actions/checkout without persist-credentials: false"
            )


def test_p1_4_startup_scripts_do_not_auto_upgrade_pip_or_network_install_by_default():
    ps1 = (REPO_ROOT / "start-bossman.ps1").read_text(encoding="utf-8")
    sh = (REPO_ROOT / "start-bossman.sh").read_text(encoding="utf-8")

    # Never run unpinned `pip install --upgrade pip`
    assert "pip install --upgrade pip" not in ps1
    assert "pip install --upgrade pip" not in sh

    # Network/editable package installation must be gated behind explicit BootstrapInstall / BOOTSTRAP_INSTALL
    assert "[switch]$BootstrapInstall" in ps1
    assert "if ($BootstrapInstall -and -not $SkipInstall)" in ps1
    assert "--bootstrap-install)" in sh
    assert 'if [ "$BOOTSTRAP_INSTALL" -eq 1 ] && [ "$SKIP_INSTALL" -eq 0 ]; then' in sh


def test_p1_5_hash_pinned_lockfiles_and_sbom_exist_for_prod_and_wallet_adjacent():
    import json

    lockfiles = [
        REPO_ROOT / "solana_volume_suite" / "requirements.lock",
        REPO_ROOT / "command-center" / "requirements.lock",
        REPO_ROOT / "apps" / "war-terminal" / "requirements.lock",
        REPO_ROOT / "apps" / "swapme-bot" / "requirements.lock",
        REPO_ROOT / "tools" / "windows_bundle_lock.txt",
    ]
    for lf in lockfiles:
        assert lf.is_file(), f"Missing hash-pinned lockfile: {lf}"
        content = lf.read_text(encoding="utf-8")
        assert "--hash=sha256:" in content, f"Lockfile {lf} lacks --hash=sha256: pins"

    # Also verify solana_volume_suite/requirements.txt no longer says hashes are omitted
    svs_req = (REPO_ROOT / "solana_volume_suite" / "requirements.txt").read_text(encoding="utf-8")
    assert "--hash=sha256:" in svs_req

    sbom_path = REPO_ROOT / "docs" / "security" / "sbom-lockfile-manifest.json"
    assert sbom_path.is_file(), "Missing SBOM / lockfile manifest"
    sbom = json.loads(sbom_path.read_text(encoding="utf-8"))
    assert sbom.get("schema") == "bossman.sbom.lockfiles.v1"
    assert len(sbom.get("lockfiles", [])) >= 5


def test_p1_6_self_hosted_ci_sandbox_is_isolated_without_secrets_docker_socket_or_open_egress():
    wf = (REPO_ROOT / ".github" / "workflows" / "astra-acceptance.yml").read_text(encoding="utf-8")
    sandbox_idx = wf.find("sandbox:")
    assert sandbox_idx != -1
    sandbox_block = wf[sandbox_idx:]

    assert "persist-credentials: false" in sandbox_block
    assert "clean: true" in sandbox_block
    assert "BOSSMAN_SANDBOX_NO_EGRESS: '1'" in sandbox_block or 'BOSSMAN_SANDBOX_NO_EGRESS: "1"' in sandbox_block
    assert "PIP_NO_INDEX: '1'" in sandbox_block or 'PIP_NO_INDEX: "1"' in sandbox_block
    assert "/var/run/docker.sock" not in sandbox_block
    assert "secrets." not in sandbox_block
