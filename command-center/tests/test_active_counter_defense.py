"""Tests for Active Perimeter Counter-Defense (Canary traps, Tarpit, Attacker Dossier, Firewall Quarantine, and Skill)."""
from __future__ import annotations

import asyncio
from pathlib import Path

import pytest

from bcc.features import active_counter_defense as acd
from bcc.v2 import skill_catalog as sc
from bcc.v2 import skill_library as sl

REPO_ROOT = Path(__file__).resolve().parents[2]
SKILLS_ROOT = REPO_ROOT / ".agents" / "skills"


@pytest.mark.parametrize("protected_ip", [
    "127.0.0.1",
    "::1",
    "0.0.0.0",
    "169.254.10.20",
    "fe80::1",
    "100.64.0.1",
    "100.101.102.103",
    "not-an-ip",
])
def test_counter_defense_refuses_loopback_linklocal_and_tailscale_self_lockout(protected_ip: str):
    with pytest.raises(ValueError):
        acd.validate_quarantine_target_ip(protected_ip)


def test_counter_defense_builds_reversible_firewall_plan_and_dossier():
    state = acd.CounterDefenseState()
    ev1 = state.record(
        kind="canary_hit",
        remote_ip="198.51.100.42",
        path="/.env",
        user_agent="BadScanner/1.0",
        detail="Probed decoy .env",
        tarpit_applied_ms=400,
    )
    ev2 = state.record(
        kind="brute_force",
        remote_ip="198.51.100.42",
        path="/api/internal/master-keys",
        user_agent="BadScanner/1.0",
        detail="Repeated 401",
        tarpit_applied_ms=600,
    )
    assert ev1.event_id != ev2.event_id

    dossier = acd.build_attacker_dossier("198.51.100.42", state=state)
    assert dossier["attacker_ip"] == "198.51.100.42"
    assert dossier["hits_count"] == 2
    assert dossier["total_tarpit_ms"] == 1000
    assert "/.env" in dossier["paths_probed"]
    plan = dossier["quarantine_plan"]
    assert plan["requires_owner_yes"] is True
    assert 'New-NetFirewallRule -DisplayName "Bossman-CounterDefense-Block-198_51_100_42"' in plan["block_command"]
    assert 'Remove-NetFirewallRule -DisplayName "Bossman-CounterDefense-Block-198_51_100_42"' in plan["rollback_command"]
    assert "198.51.100.42" in dossier["abuse_report_text"]


def test_counter_defense_tarpit_stream_yields_watermarked_decoy():
    async def _collect():
        chunks = []
        async for part in acd.tarpit_byte_stream(chunks=3, delay_s=0.01, token_id="trap-99"):
            chunks.append(part)
        return b"".join(chunks).decode("utf-8")

    text = asyncio.run(_collect())
    assert "CANARY_TRAP_ID=trap-99" in text
    assert "ACTIVE_COUNTER_DEFENSE" in text


def test_active_counter_defense_skill_manifest_provenance_and_selection():
    path = SKILLS_ROOT / "active-counter-defense" / "SKILL.md"
    assert path.is_file()
    sk = sl.parse_skill(path, SKILLS_ROOT)
    val = sl.validate_manifest(sk)
    assert val["errors"] == []
    assert val["warnings"] == []
    assert sc.scan_policy(sk.body) == []

    cat = sc.SkillCatalog(REPO_ROOT / ".agents")
    entries = {e.name: e for e in cat.entries()}
    assert "active-counter-defense" in entries
    assert entries["active-counter-defense"].status == sc.VERIFIED

    sel = cat.select("Включи встречный отпор и ловушку для хакера, чтобы заблокировать IP атакующего")
    assert any(item["title"] == "active-counter-defense" for item in sel)
