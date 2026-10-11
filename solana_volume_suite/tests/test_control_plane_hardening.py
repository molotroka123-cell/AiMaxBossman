"""MP21-D H1/H2/H3 regression tests: vault destruction, password policy,
plaintext password, unauthenticated control plane, non-strict SAFETY_ONLY gate."""
import os
import re
import subprocess
import sys

import pytest
from fastapi.testclient import TestClient

TEST_DIR = os.path.dirname(os.path.abspath(__file__))
SUITE = os.path.dirname(TEST_DIR)
if SUITE not in sys.path:
    sys.path.insert(0, SUITE)

from solana_volume_suite.dashboard import safety_app  # noqa: E402
from solana_volume_suite.dashboard.safety_app import app  # noqa: E402

from core.key_vault.vault import MIN_PASSWORD_LENGTH, SecurityKeyVault  # noqa: E402

GOOD = {"X-Control-Token": safety_app.CONTROL_TOKEN}
CONTROL_POSTS = ["/api/bot/start", "/api/bot/sweep", "/api/orchestrator/start",
                 "/api/sweep", "/api/vault/generate"]


@pytest.fixture
def client():
    with TestClient(app, base_url="http://127.0.0.1:8000") as c:
        yield c
    safety_app.orchestrator.stop()


# ---------------------------------------------------------------- H3: auth
@pytest.mark.parametrize("path", CONTROL_POSTS)
def test_control_posts_require_token(client, path):
    r = client.post(path, json={"paper_only": True})
    assert r.status_code == 401
    r = client.post(path, json={"paper_only": True}, headers={"X-Control-Token": "wrong"})
    assert r.status_code == 401
    assert safety_app.orchestrator.is_running is False


def test_token_is_not_leaked_by_read_endpoints(client):
    for path in ("/api/status", "/api/telemetry", "/api/trading/telemetry"):
        assert safety_app.CONTROL_TOKEN not in client.get(path).text


def test_foreign_origin_is_rejected_even_with_token(client):
    r = client.post("/api/bot/start", json={"paper_only": True},
                    headers={**GOOD, "Origin": "http://evil.example"})
    assert r.status_code == 403
    assert safety_app.orchestrator.is_running is False


@pytest.mark.parametrize("body", [{"x": 1}, {"paper_only": False}, {"paper_only": "yes"},
                                  {"paper_only": True, "password": "hunter2hunter2"}, []])
def test_safety_only_gate_is_strict(client, body):
    r = client.post("/api/bot/start", json=body, headers=GOOD)
    assert r.status_code in (403, 422)
    assert safety_app.orchestrator.is_running is False


def test_authenticated_strict_start_runs_paper_only_then_kill_switch_needs_no_token(client):
    r = client.post("/api/bot/start", json={"paper_only": True}, headers=GOOD)
    assert r.status_code == 200
    assert r.json()["live_execution_enabled"] is False
    # stopping is always allowed: a kill switch must not depend on a secret
    s = client.post("/api/bot/stop")
    assert s.status_code == 200 and s.json()["bot_status"] == "STOPPED"


def test_authenticated_dangerous_endpoints_stay_blocked(client):
    assert client.post("/api/sweep", json={"destination": "x"}, headers=GOOD).status_code == 409
    assert client.post("/api/bot/sweep", json={"paper_only": True}, headers=GOOD).status_code == 409
    g = client.post("/api/vault/generate", json={"count": 3, "password": "a-long-enough-password"},
                    headers=GOOD)
    assert g.status_code == 403
    assert g.json()["reason"] == "PAPER_ONLY_REAL_KEY_GENERATION_DISABLED"


def test_env_token_too_short_falls_back_to_random(monkeypatch):
    monkeypatch.setenv("SVS_CONTROL_TOKEN", "short")
    t = safety_app._load_control_token()
    assert t != "short" and len(t) >= 32
    monkeypatch.setenv("SVS_CONTROL_TOKEN", "x" * 24)
    assert safety_app._load_control_token() == "x" * 24


# ------------------------------------------------------ H1: vault safety
def test_min_password_length_is_twelve_everywhere():
    assert MIN_PASSWORD_LENGTH == 12
    src = open(os.path.join(SUITE, "scripts", "generate_vault.py"), encoding="utf-8").read()
    assert not re.search(r"len\(password\)\s*<\s*6", src)


def test_wrong_password_never_modifies_or_deletes_vault(tmp_path):
    path = tmp_path / "v.json"
    vault = SecurityKeyVault(storage_path=str(path))
    vault.create_and_store_pool(count=2, password="correct horse battery staple")  # ci-secret-scan: allow -- synthetic test password literal
    before = path.read_bytes()
    with pytest.raises(PermissionError):
        vault.load_keypairs("wrong password wrong password")
    assert path.read_bytes() == before


def test_create_refuses_to_overwrite_existing_vault(tmp_path):
    path = tmp_path / "v.json"
    vault = SecurityKeyVault(storage_path=str(path))
    vault.create_and_store_pool(count=1, password="correct horse battery staple")  # ci-secret-scan: allow -- synthetic test password literal
    before = path.read_bytes()
    with pytest.raises(FileExistsError):
        vault.create_and_store_pool(count=1, password="another long password here")  # ci-secret-scan: allow -- synthetic test password literal
    assert path.read_bytes() == before


def _run_generate(tmp_path, *args, stdin=""):
    script = os.path.join(SUITE, "scripts", "generate_vault.py")
    return subprocess.run([sys.executable, script, *args], input=stdin, text=True,
                          capture_output=True, cwd=tmp_path, timeout=60)


def test_generate_script_short_password_creates_nothing_and_keeps_existing(tmp_path):
    out = tmp_path / "keep.json"
    out.write_bytes(b"existing vault bytes")
    r = _run_generate(tmp_path, "--count", "1", "--path", str(out),
                      stdin="elevenchars\n")  # 11 chars, getpass falls back to stdin
    assert r.returncode != 0
    assert out.read_bytes() == b"existing vault bytes"


def test_generate_script_refuses_existing_vault_and_backs_it_up(tmp_path):
    out = tmp_path / "keep.json"
    out.write_bytes(b"existing vault bytes")
    r = _run_generate(tmp_path, "--count", "1", "--path", str(out),
                      stdin="twelve-chars-ok-password\n")
    assert r.returncode == 3
    assert out.read_bytes() == b"existing vault bytes"        # never deleted/overwritten
    backups = [p for p in tmp_path.iterdir() if ".bak-" in p.name]
    assert len(backups) == 1 and backups[0].read_bytes() == b"existing vault bytes"


def test_generate_script_has_no_password_cli_flag(tmp_path):
    r = _run_generate(tmp_path, "--password", "pass-on-argv-is-visible-12")
    assert r.returncode == 2   # argparse: unrecognised argument


# ------------------------------------- H2: no plaintext master password
def test_no_source_writes_vault_password_to_env_file():
    offenders = []
    for root, _dirs, files in os.walk(SUITE):
        if "tests" in root.split(os.sep) or ".git" in root:
            continue
        for f in files:
            if f.endswith((".py", ".sh", ".ps1", ".md", ".yaml", ".yml")):
                text = open(os.path.join(root, f), encoding="utf-8", errors="ignore").read()
                for ln, line in enumerate(text.splitlines(), 1):
                    if re.search(r"VAULT_MASTER_PASSWORD\s*=\s*(\{|\"|')", line):
                        offenders.append(f"{f}:{ln}")
    assert offenders == []


def test_setup_mainnet_writes_no_files(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    import setup_mainnet
    with pytest.raises(RuntimeError):
        setup_mainnet.run_setup_wizard()
    assert list(tmp_path.iterdir()) == []


# ------------------------- keep: role-separated keys + per-wallet limits
def test_wallet_policy_roles_and_limits():
    from core.wallet_policy import PolicyViolation, WalletPolicy, WalletRole
    p = WalletPolicy(per_order_limit_usd=25.0, per_day_limit_usd=100.0)
    p.assign("hot-1", WalletRole.TRADE)
    p.assign("cold-1", WalletRole.TREASURY)
    assert p.check("hot-1", "swap", 20.0) is True
    with pytest.raises(PolicyViolation):
        p.check("hot-1", "withdraw", 1.0)            # trade key cannot withdraw
    with pytest.raises(PolicyViolation):
        p.check("cold-1", "swap", 1.0)               # treasury key never trades
    with pytest.raises(PolicyViolation):
        p.check("hot-1", "swap", 26.0)               # per-order cap
    p.record("hot-1", 90.0)
    with pytest.raises(PolicyViolation):
        p.check("hot-1", "swap", 20.0)               # per-day cap
    with pytest.raises(PolicyViolation):
        p.check("unknown", "swap", 1.0)              # unassigned wallet denied
    with pytest.raises(PolicyViolation):
        p.assign("hot-1", WalletRole.TREASURY)       # roles are immutable once set
