"""Gem Search sidecar (owner 31.10): research-only; token launches, wallets and launch credentials can never pass."""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "tools"))
import gem_search_research as g  # noqa: E402


def test_clean_folder_without_credentials_may_start(tmp_path):
    (tmp_path / "data").mkdir()
    assert g.refusal(tmp_path, {"PATH": "x"}) is None


def test_wallet_env_file_or_launch_credentials_refuse(tmp_path):
    (tmp_path / "data" / "wallets").mkdir(parents=True)
    (tmp_path / "data" / "wallets" / "treasury.json").write_text("[]")
    assert "wallet" in g.refusal(tmp_path, {})
    clean = tmp_path / "clean"; (clean / "data").mkdir(parents=True)
    (clean / ".env").write_text("XAI_API_KEY=x")
    assert ".env" in g.refusal(clean, {})
    other = tmp_path / "other"; other.mkdir()
    assert "PINATA_JWT" in g.refusal(other, {"PINATA_JWT": "jwt"})
    assert "SPIDER_ALLOW_LAUNCH" in g.refusal(other, {"SPIDER_ALLOW_LAUNCH": "1"})
    assert "LAUNCH_BRIDGE_URL" in g.refusal(other, {"LAUNCH_BRIDGE_URL": "https://bridge"})


def test_engine_env_forces_research_only_and_drops_keys():
    env = g.research_env({"PINATA_JWT": "secret", "XAI_API_KEY": "k", "SPIDER_ALLOW_LAUNCH": "1",
                          "LAUNCH_MODE": "live", "ENABLE_PAID_X": "1", "PATH": "p"})
    assert env["LAUNCH_MODE"] == "dry_run" and env["SPIDER_ALLOW_LAUNCH"] == "0" and env["ENABLE_PAID_X"] == "0"
    assert env["PINATA_JWT"] == "" and env["XAI_API_KEY"] == "" and env["PATH"] == "p"
    assert env["SOLANA_KEYPAIR_PATH"].endswith("none.json")


def test_harden_adds_windows_lock_shim_and_pause_switch(tmp_path):
    (tmp_path / "automation.py").write_text("import os\nimport fcntl\nx = 1\n", encoding="utf-8")
    g.harden(tmp_path)
    text = (tmp_path / "automation.py").read_text(encoding="utf-8")
    assert "_FcntlShim" in text and text.count("import fcntl") == 1
    assert (tmp_path / "data" / "launch-paused").exists()
    g.harden(tmp_path)                                   # idempotent
    assert (tmp_path / "automation.py").read_text(encoding="utf-8").count("_FcntlShim()") == 1
