"""MP21-D: the Solana volume suite stays quarantined for agents (manifest + skill)."""
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[1]


def test_manifest_is_quarantined_and_denies_network_and_signing():
    m = yaml.safe_load((ROOT / "apps/solana-volume-suite/app.manifest.yaml").read_text(encoding="utf-8"))
    assert m["status"] == "quarantined"
    perms = m["permissions"]
    for key in ("network.rpc", "network.jito", "wallet.signing", "wallet.export"):
        assert perms[key] == "deny", key


def test_skill_does_not_advertise_volume_generation_triggers():
    text = (ROOT / ".agents/skills/solana-volume-suite/SKILL.md").read_text(encoding="utf-8")
    head = text.split("---", 2)[1]
    assert "QUARANTINED" in head
    assert "Invoke this skill when" not in text.split("QUARANTINED", 2)[1][:400]
