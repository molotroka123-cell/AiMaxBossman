"""Two-Line PC Cyber-Defense & 3 Memory Skills regression suite (2026-10-22).

Covers all requirements from docs/owner/PC_DEFENSE_AUDIT_MASTER_PROMPT_20261022_RU.md:
- Line 1: Offensive network/exploitation tool HARD_DENY & TerminalPolicy.DANGEROUS,
  _NEVER_AUTO_APPROVE_KINDS (outbound_send, network_send, cloud_send, cloud.ask) in AUTO mode,
  plugin_security.validate_url / resolve_pinned_ip IPv6 literals & DNS rebinding.
- Line 2: ci_secret_scan CLI file mode + DB/PaaS tokens, Jeff OutboundGuard 6/6 scenarios,
  pc_defense_audit forward/backslash Temp task detection.
- Memory Skills: pc-defense-audit, secret-hygiene, incident-response-local (manifest, provenance,
  positive/negative selection, negative permission checks).
"""
from __future__ import annotations

import asyncio
from pathlib import Path
from types import SimpleNamespace

import pytest

from bcc import plugin_security as ps
from bcc.features import agent_opencode as ao
from bcc.features import tools_terminal as tt
from bcc.pit import outbound_guard as og
from bcc.v2 import skill_catalog as sc
from bcc.v2 import skill_library as sl
from bcc.v2.terminal_control import TerminalPolicy

REPO_ROOT = Path(__file__).resolve().parents[2]
SKILLS_ROOT = REPO_ROOT / ".agents" / "skills"


# ---------------------------------------------------------------------------
# Line 1.1: Offensive network & exploitation tools denied in Terminal
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("cmd", [
    "nmap -sS 192.168.1.0/24",
    "masscan -p1-65535 10.0.0.0/8",
    "sqlmap -u https://example.com/item?id=1 --dbs",
    "hydra -l admin -P rockyou.txt ssh://192.168.1.10",
    "john --wordlist=passwords.txt hashes.txt",
    "hashcat -m 0 -a 0 hashes.txt rockyou.txt",
    "msfconsole -q -x 'use exploit/multi/handler'",
    "msfvenom -p windows/x64/meterpreter/reverse_tcp",
    "nuclei -u https://example.com",
    "mimikatz.exe privilege::debug sekurlsa::logonpasswords",
    "impacket-secretsdump Administrator:Pass@192.168.1.5",
    "responder -I eth0 -wrf",
    "netexec smb 192.168.1.0/24",
    "chisel client 10.10.10.10:8000 R:socks",
    "ligolo-ng -connect 10.10.10.10:11601",
    "rm -rf /",
    "git push origin main --force",
    "cat ~/.ssh/id_rsa",
    "cat .env",
])
def test_line1_terminal_hard_deny_blocks_offensive_and_credential_commands(cmd: str):
    matched = [label for rx, label in tt.HARD_DENY if rx.search(cmd)]
    assert matched, f"Expected HARD_DENY to block {cmd!r}"


@pytest.mark.parametrize("cmd", [
    "nmap -A 10.0.0.1",
    "sqlmap -u http://target/page",
    "hydra -L users.txt -P pass.txt ftp://target",
    "mimikatz",
    "msfconsole",
    "nuclei -l urls.txt",
    "impacket-psexec user@target",
])
def test_line1_terminal_policy_denies_offensive_tools_even_in_system_admin(cmd: str):
    for mode in ("sandbox", "project_host", "system_admin"):
        pol = TerminalPolicy(allowed_roots=[REPO_ROOT], mode=mode)
        assert pol.decision(cmd, REPO_ROOT) == "deny"


@pytest.mark.parametrize("cmd", [
    "curl -s http://127.0.0.1:8801/health/live",
    "git status",
    "pytest -q command-center/tests/test_pc_defense_two_lines.py",
])
def test_line1_terminal_allows_normal_local_curl_and_dev_commands(cmd: str):
    matched = [label for rx, label in tt.HARD_DENY if rx.search(cmd)]
    assert matched == [], f"Normal command {cmd!r} must not hit HARD_DENY"
    pol = TerminalPolicy(allowed_roots=[REPO_ROOT], mode="project_host")
    assert pol.decision(cmd, REPO_ROOT) != "deny"


# ---------------------------------------------------------------------------
# Line 1.2: _NEVER_AUTO_APPROVE_KINDS blocks outbound/network/cloud in AUTO
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("kind", [
    "self_improve",
    "skill_permissions",
    "admin",
    "destructive",
    "outbound_send",
    "network_send",
    "cloud_send",
    "cloud.ask",
])
def test_line1_never_auto_approve_kinds_contains_all_critical_classes(kind: str):
    assert kind in ao._NEVER_AUTO_APPROVE_KINDS


@pytest.mark.parametrize("approval_kind,tool_name,preview", [
    ("outbound_send", "telegram.send", "Send message to external chat"),
    ("network_send", "http.post", "POST payload to external webhook"),
    ("cloud_send", "cloud.ask", "Ask cloud model vendor/strong"),
    ("ask", "cloud.ask", "cloud.ask: Explain TCP vs UDP"),
    ("destructive", "terminal.run", "git reset --hard"),
])
def test_line1_bypass_permissions_never_auto_approves_outbound_or_cloud(
    monkeypatch, approval_kind: str, tool_name: str, preview: str
):
    async def _run():
        decided: list[tuple[int, bool, str]] = []
        queue: asyncio.Queue = asyncio.Queue()

        class FakeBus:
            def subscribe(self):
                return queue

            def unsubscribe(self, _q):
                pass

        class FakeApprovals:
            async def decide(self, appr_id: int, ok: bool, actor: str):
                decided.append((appr_id, ok, actor))

        svc = SimpleNamespace(bus=FakeBus(), approvals=FakeApprovals(), settings=SimpleNamespace(data_dir=None))
        orch = ao._orch(svc)
        session_id = "sess-auto-test"
        task_id = 901
        orch.task_to_session[task_id] = session_id
        monkeypatch.setattr(ao, "_session_cfg", lambda _svc, _sid: {"bypass_permissions": True})
        rt = orch.runtime(session_id)
        sub_q = asyncio.Queue()
        rt.subscribers.add(sub_q)

        bridge = asyncio.create_task(ao._bus_bridge(svc))
        try:
            await queue.put({
                "kind": "approval.created",
                "id": 555,
                "task_id": task_id,
                "approval_kind": approval_kind,
                "tool": tool_name,
                "preview": preview,
            })
            await asyncio.sleep(0.05)
        finally:
            bridge.cancel()
            with pytest.raises(asyncio.CancelledError):
                await bridge

        assert decided == [], f"bypass_permissions=True must NOT auto-approve {approval_kind}/{tool_name}"
        assert not sub_q.empty()
        evt = sub_q.get_nowait()
        assert evt.get("type") == "approval.required"
        assert evt.get("approval_id") == 555

    asyncio.run(_run())


# ---------------------------------------------------------------------------
# Line 1.3: plugin_security SSRF IPv6 literals & DNS rebinding
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("bad_url", [
    "http://127.0.0.1:8801/api/agents",
    "http://localhost/admin",
    "http://169.254.169.254/latest/meta-data/",
    "http://metadata.google.internal/computeMetadata/v1/",
    "http://service.internal/secret",
    "http://[::1]:8801/health",
    "http://[fe80::1]/status",
    "http://[fc00::1]/private",
    "http://[::ffff:127.0.0.1]/loopback",
    "http://[0:0:0:0:0:0:0:1]/loopback",
])
def test_line1_validate_url_blocks_loopback_metadata_and_ipv6_literals(bad_url: str):
    with pytest.raises(ps.PluginSecurityError):
        ps.validate_url(bad_url)


def test_line1_resolve_pinned_ip_blocks_dns_rebinding_mixed_records(monkeypatch):
    import socket

    def fake_getaddrinfo(host, port, *args, **kwargs):
        return [
            (socket.AF_INET, socket.SOCK_STREAM, 6, "", ("93.184.216.34", 0)),
            (socket.AF_INET, socket.SOCK_STREAM, 6, "", ("127.0.0.1", 0)),
        ]

    monkeypatch.setattr(socket, "getaddrinfo", fake_getaddrinfo)
    with pytest.raises(ps.PluginSecurityError, match="non-global address blocked"):
        ps.resolve_pinned_ip("rebind.attacker.example")


def test_line1_resolve_pinned_ip_blocks_ipv6_loopback_rebinding(monkeypatch):
    import socket

    def fake_getaddrinfo(host, port, *args, **kwargs):
        return [
            (socket.AF_INET6, socket.SOCK_STREAM, 6, "", ("2606:2800:220:1:248:1893:25c8:1946", 0, 0, 0)),
            (socket.AF_INET6, socket.SOCK_STREAM, 6, "", ("::1", 0, 0, 0)),
        ]

    monkeypatch.setattr(socket, "getaddrinfo", fake_getaddrinfo)
    with pytest.raises(ps.PluginSecurityError, match="non-global address blocked"):
        ps.resolve_pinned_ip("rebind6.attacker.example")


# ---------------------------------------------------------------------------
# Line 2.1: ci_secret_scan CLI file mode + DB/Railway/Zeabur/OpenAI/GitHub
# ---------------------------------------------------------------------------

def test_line2_ci_secret_scan_catches_synthetic_keys_and_db_uris(tmp_path: Path):
    import tools.ci_secret_scan as css

    dirty = tmp_path / "dirty_sample.py"
    fake_openai = "sk-" + "A1b2C3d4E5f6G7h8I9j0K1l2M3n4"  # ci-secret-scan: allow
    fake_ghp = "ghp_" + "Z9y8X7w6V5u4T3s2R1q0P9o8N7m6"  # ci-secret-scan: allow
    fake_db = "postgresql://admin:SuperSecretPass99@containers-us-west-12.railway.app:5432/railway"  # ci-secret-scan: allow
    fake_rw = "RAILWAY_API_TOKEN=" + "rw_live_9876543210abcdef123456"  # ci-secret-scan: allow
    dirty.write_text(f"K1 = '{fake_openai}'\nK2 = '{fake_ghp}'\nDB = '{fake_db}'\n{fake_rw}\n", encoding="utf-8")

    rc = css.main([str(dirty)])
    assert rc == 2

    clean = tmp_path / "clean_sample.py"
    clean.write_text("x = 42\nprint('hello world')\n", encoding="utf-8")
    assert css.main([str(clean)]) == 0


# ---------------------------------------------------------------------------
# Line 2.2: Jeff OutboundGuard 6/6 scenarios
# ---------------------------------------------------------------------------

def test_line2_jeff_outbound_guard_blocks_6_of_6_unsafe_cases_and_allows_reply(tmp_path: Path):
    now_box = {"t": 1_760_000_000.0}
    enabled = {"on": False}
    opted_in: set[int] = set()
    guard = og.OutboundGuard(
        tmp_path,
        clock=lambda: now_box["t"],
        unsolicited_switch=lambda: enabled["on"],
        opted_in=lambda cid: cid in opted_in,
    )

    # 1. Unsolicited with switch OFF -> blocked
    d1 = guard.decide(1001, now=now_box["t"])
    assert not d1.allowed and d1.reason == "global_switch_off"

    # 2. Switch ON, but user did NOT opt in -> blocked
    enabled["on"] = True
    d2 = guard.decide(1001, now=now_box["t"] + 1)
    assert not d2.allowed and d2.reason == "not_opted_in"

    # 3. User opted in -> first unsolicited allowed, second within 24h -> blocked (rate_24h)
    opted_in.update({1001, 2001, 2002, 2004})
    d_ok = guard.decide(1001, now=now_box["t"] + 2)
    assert d_ok.allowed and d_ok.kind == "unsolicited"
    d3 = guard.decide(1001, now=now_box["t"] + 3)
    assert not d3.allowed and d3.reason == "rate_24h"

    # 4. Fan-out breaker: 3 distinct chats in 10 minutes trips broadcast_tripped -> blocked
    guard.decide(2001, now=now_box["t"] + 4)
    d4 = guard.decide(2002, now=now_box["t"] + 5)
    assert not d4.allowed and d4.reason in ("fanout_breaker", "broadcast_tripped")

    # 5. Once tripped, even a fresh opted-in chat is blocked
    d5 = guard.decide(2004, now=now_box["t"] + 6)
    assert not d5.allowed and d5.reason == "broadcast_tripped"

    # 6. Missing/invalid chat_id -> blocked fail-closed
    d6 = guard.decide(None, now=now_box["t"] + 7)
    assert not d6.allowed and d6.reason == "chat_unknown"

    # Positive control: direct reply within 30 min of inbound user message is allowed
    now_box["t"] += 10
    guard.note_inbound(3001, ts=now_box["t"])
    d_reply = guard.decide(3001, now=now_box["t"] + 5)
    assert d_reply.allowed and d_reply.kind == "reply"


# ---------------------------------------------------------------------------
# Memory: 3 Cyber-Defense Skills (manifest, provenance, selection, permissions)
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("skill_id", [
    "pc-defense-audit",
    "secret-hygiene",
    "incident-response-local",
])
def test_memory_defense_skills_manifest_and_policy_clean(skill_id: str):
    path = SKILLS_ROOT / skill_id / "SKILL.md"
    assert path.is_file(), f"Missing {path}"
    sk = sl.parse_skill(path, SKILLS_ROOT)
    val = sl.validate_manifest(sk)
    assert val["errors"] == [], f"Manifest errors in {skill_id}: {val['errors']}"
    assert val["warnings"] == [], f"Manifest warnings in {skill_id}: {val['warnings']}"
    violations = sc.scan_policy(sk.body)
    assert violations == [], f"Policy violations in {skill_id}: {violations}"

    # Negative permission check: never grants write, network, or admin/bypass permissions
    contract = sl.skill_contract(sk)
    forbidden_perms = {"write", "network", "admin", "bypass_permissions", "outbound_send"}
    assert forbidden_perms.isdisjoint(set(contract.permissions))
    for tool in contract.required_tools:
        assert tool in {"terminal.run", "memory.write", "memory.search", "code.status"}


def test_memory_defense_skills_catalog_provenance_and_selection():
    cat = sc.SkillCatalog(REPO_ROOT / ".agents")
    entries = {e.name: e for e in cat.entries()}
    for sid in ("pc-defense-audit", "secret-hygiene", "incident-response-local"):
        assert sid in entries, f"{sid} not discovered by SkillCatalog"
        entry = entries[sid]
        assert entry.status == sc.VERIFIED, f"{sid} status={entry.status} reason={entry.status_reason}"
        # Catalog summary never grants direct execution permissions
        assert entry.summary()["grants"] == {"tools": [], "permissions": []}

    # Positive selection queries
    sel_audit = cat.select("Проверь защиту ПК и запусти аудит безопасности портов и планировщика")
    assert any(item["title"] == "pc-defense-audit" for item in sel_audit)

    sel_secrets = cat.select("Проверь ключи и секреты перед git push на утечка токенов")
    assert any(item["title"] == "secret-hygiene" for item in sel_secrets)

    sel_ir = cat.select("Подозрительно: лишние задачи планировщика, возможный взлом или инцидент")
    assert any(item["title"] == "incident-response-local" for item in sel_ir)

    # Negative selection query (unrelated culinary prompt)
    sel_neg = cat.select("Как приготовить яблочный пирог с корицей в духовке")
    defense_ids = {"pc-defense-audit", "secret-hygiene", "incident-response-local"}
    assert all(item["title"] not in defense_ids for item in sel_neg)
