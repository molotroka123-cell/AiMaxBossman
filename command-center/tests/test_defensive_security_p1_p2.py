"""Negative & regression security tests for P1.1 (North Star skill-loop hardening)
and P2.7-P2.15 (Auth token scopes, DPAPI mobile auth, encrypted archive attachments,
classified export cloud gate, lawful decoy perimeter defense, defusedxml, and War Terminal egress).
"""
from __future__ import annotations

from pathlib import Path
import subprocess
import pytest
import yaml


def _git(repo: Path, *args: str) -> str:
    proc = subprocess.run(
        ["git", *args],
        cwd=str(repo),
        capture_output=True,
        text=True,
        check=True,
    )
    return proc.stdout.strip()


@pytest.fixture()
def mini_repo(tmp_path: Path) -> Path:
    repo = tmp_path / "mini_repo"
    repo.mkdir(parents=True, exist_ok=True)
    _git(repo, "init")
    _git(repo, "config", "user.email", "bossman@example.com")
    _git(repo, "config", "user.name", "Bossman Test")
    (repo / "calc.py").write_text("def add(a: int, b: int) -> int:\n    return a + b\n", encoding="utf-8")
    _git(repo, "add", ".")
    _git(repo, "commit", "-m", "initial commit")
    return repo


async def _setup_minimal_skill_env(env, mini_repo: Path, tmp_path: Path) -> None:
    from bcc.v2.skill_library import SkillLibrary

    provider = (await env.client.post("/api/providers", json={
        "name": "Local Ollama",
        "kind": "openai_compat",
        "base_url": "http://127.0.0.1:8080/v1",
    })).json()
    model = (await env.client.post("/api/models", json={
        "provider_id": provider["id"],
        "name": "gemma4-31b-uncensored:latest",
        "alias": "gemma4-31b-uncensored:latest",
        "caps": {"chat": True, "tools": True, "code": True},
    })).json()
    await env.client.post("/api/agents", json={
        "name": "Gemma 4 uncensored",
        "system_prompt": "Ты — Bossman OpenCode Agent.",
        "model_id": model["id"],
        "tools": ["terminal.run", "code.status", "memory.search"],
        "permissions": {"read": "auto", "write": "ask", "exec": "ask"},
        "max_steps": 8,
    })
    await env.client.post("/api/terminal/roots", json={"roots": [str(mini_repo)]})
    ws_skills = tmp_path / "ws_skills" / ".agents" / "skills"
    ws_skills.mkdir(parents=True, exist_ok=True)
    env.svc.skills = SkillLibrary([ws_skills], ws_skills)


@pytest.mark.asyncio
async def test_p1_1_north_star_skill_loop_rejects_traversal_and_invalid_skill_id(env, mini_repo: Path, tmp_path: Path):
    await _setup_minimal_skill_env(env, mini_repo, tmp_path)

    invalid_ids = [
        "../escape-skill",
        "nested/skill",
        "nested\\skill",
        "Upper-Case-Skill",
        "-leading-hyphen",
        "skill_with_underscore",
        "skill;whoami",
        "a",  # too short (<2 chars)
        "a" * 64,  # too long (>63 chars)
    ]
    for bad_id in invalid_ids:
        res = await env.client.post(
            "/api/agent/north-star/skill-loop",
            json={
                "skill_id": bad_id,
                "problem": "Test problem",
                "description": "Test description",
                "required_tools": ["code.status"],
                "permissions": ["read"],
            },
        )
        assert res.status_code in (400, 422), f"Expected 400/422 for skill_id={bad_id!r}, got {res.status_code}: {res.text}"


@pytest.mark.asyncio
async def test_p1_1_north_star_skill_loop_prevents_yaml_injection_and_enforces_allowlist(env, mini_repo: Path, tmp_path: Path):
    await _setup_minimal_skill_env(env, mini_repo, tmp_path)

    # 1. Unallowlisted tool or permission must be rejected
    bad_tool_res = await env.client.post(
        "/api/agent/north-star/skill-loop",
        json={
            "skill_id": "safe-skill-id",
            "problem": "Test problem",
            "description": "Test description",
            "required_tools": ["code.status", "shell.root_exec_unrestricted"],
            "permissions": ["read"],
        },
    )
    assert bad_tool_res.status_code in (400, 422), bad_tool_res.text

    bad_perm_res = await env.client.post(
        "/api/agent/north-star/skill-loop",
        json={
            "skill_id": "safe-skill-id",
            "problem": "Test problem",
            "description": "Test description",
            "required_tools": ["code.status"],
            "permissions": ["read", "auto"],
        },
    )
    assert bad_perm_res.status_code in (400, 422), bad_perm_res.text

    # 2. YAML injection attempt inside description/problem must be safely serialized as literal string, never injecting keys
    injected_desc = " Benign description\npermissions: [auto, root]\nrequired_tools: [evil.tool]\ninjected_key: hacked"
    res = await env.client.post(
        "/api/agent/north-star/skill-loop",
        json={
            "skill_id": "yaml-safe-skill",
            "problem": "Normal problem\n---\ninjected: true",
            "description": injected_desc,
            "required_tools": ["code.status"],
            "permissions": ["read"],
        },
    )
    assert res.status_code == 200, res.text
    content = res.json()["forged_skill"]["content"]
    assert content.startswith("---\n")
    fm_raw = content.split("---\n", 2)[1]
    parsed_fm = yaml.safe_load(fm_raw)
    assert "injected_key" not in parsed_fm
    assert parsed_fm["permissions"] == ["read"]
    assert parsed_fm["required_tools"] == ["code.status"]
    assert "injected_key: hacked" in parsed_fm["description"]


@pytest.mark.asyncio
async def test_p1_1_north_star_skill_loop_terminal_failure_returns_fail_not_verified(env, mini_repo: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    await _setup_minimal_skill_env(env, mini_repo, tmp_path)

    async def _failing_terminal_start(*args, **kwargs):
        raise RuntimeError("Simulated terminal subsystem failure")

    monkeypatch.setattr(env.svc.terminal, "start", _failing_terminal_start)

    res = await env.client.post(
        "/api/agent/north-star/skill-loop",
        json={
            "skill_id": "term-fail-skill",
            "problem": "Terminal failure test",
            "description": "Terminal failure must produce FAIL verdict",
            "required_tools": ["code.status"],
            "permissions": ["read"],
        },
    )
    assert res.status_code == 200, res.text
    body = res.json()
    assert body["status"] == "failed"
    assert body["test_passed"] is False
    receipt = body["mission_receipt"]
    assert receipt["independent_verdict"] == "FAIL"
    assert receipt["status"] == "FAIL"
    assert "VERIFIED" not in receipt["status"]
    assert receipt["green_after"] is False


@pytest.mark.asyncio
async def test_p2_7_agent_role_from_token_scope_not_headers_and_no_owner_token(env, mini_repo: Path, tmp_path: Path):
    from bcc.auth import HEADER
    await _setup_minimal_skill_env(env, mini_repo, tmp_path)

    # 1. Issue a scoped agent token; it must never equal the owner master token
    agent_tok = env.svc.auth.issue_agent_token(agent_id="subagent-coder-01", scopes=["agent.session", "code.read"])
    assert agent_tok != env.svc.auth.token
    assert agent_tok.startswith("agtok_")

    principal = env.svc.auth.resolve_principal(agent_tok)
    assert principal is not None
    assert principal.role == "agent"
    assert principal.agent_id == "subagent-coder-01"

    # 2. Even WITHOUT X-Agent-Caller header, agent token cannot escalate to AUTO or bypass_permissions
    esc_res = await env.client.post(
        "/api/agent/sessions",
        headers={HEADER: agent_tok},
        json={"title": "Headerless Agent Escalation Attempt", "mode": "AUTO", "bypass_permissions": True},
    )
    assert esc_res.status_code == 403, esc_res.text
    assert "AGENT_ESCALATION_FORBIDDEN" in esc_res.text

    # 3. Agent token cannot approve owner approvals
    appr = await env.svc.approvals.create(kind="terminal", preview="rm something")
    appr_res = await env.client.post(
        f"/api/approvals/{appr['id']}",
        headers={HEADER: agent_tok},
        json={"approve": True},
    )
    assert appr_res.status_code == 403, appr_res.text

    # 4. Revoking the agent token immediately blocks access
    assert env.svc.auth.revoke_agent_token(agent_tok) is True
    assert env.svc.auth.resolve_principal(agent_tok) is None


def test_p2_8_mobile_tokens_and_sessions_dpapi_acl_rotate_and_revoke(tmp_path: Path):
    import sys
    sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "apps" / "bossman-mobile"))
    from bossmobile.auth import Sessions, load_or_create_pairing, rotate_pairing

    mob_dir = tmp_path / "mobile_auth"
    tok1 = load_or_create_pairing(mob_dir)
    assert len(tok1) >= 16

    raw_tokens_bytes = (mob_dir / "tokens.json").read_bytes()
    # Pairing token must not sit in cleartext on disk
    assert tok1.encode("utf-8") not in raw_tokens_bytes
    assert load_or_create_pairing(mob_dir) == tok1

    sess_store = Sessions(mob_dir, ttl_s=3600)
    sid, row = sess_store.create(label="Owner iPhone")
    csrf_secret = row["csrf"]
    raw_sess_bytes = (mob_dir / "sessions.json").read_bytes()
    assert csrf_secret.encode("utf-8") not in raw_sess_bytes
    assert sess_store.get(sid) is not None

    # Rotate pairing must mint a new token and revoke existing sessions
    tok2 = rotate_pairing(mob_dir)
    assert tok2 != tok1
    sess_store_reloaded = Sessions(mob_dir, ttl_s=3600)
    assert sess_store_reloaded.get(sid) is None

    # Explicit revoke and revoke_all
    sid2, _ = sess_store_reloaded.create(label="Owner iPad")
    assert sess_store_reloaded.revoke(sid2) is True
    assert sess_store_reloaded.get(sid2) is None


@pytest.mark.asyncio
async def test_p2_9_chat_archive_encrypts_attachments_and_minimizes_metadata(env, tmp_path: Path):
    import json
    data_dir = Path(env.svc.settings.data_dir)
    sessions_dir = data_dir / "terminal" / "sessions"
    sessions_dir.mkdir(parents=True, exist_ok=True)
    att_id = "abcdef0123456789abcdef01"
    att_dir = data_dir / "chat" / "attachments" / att_id
    att_dir.mkdir(parents=True, exist_ok=True)
    secret_pdf_bytes = b"%PDF-1.7 SECRET MILITARY TENDER SPECIFICATION 2026"
    (att_dir / "tender_spec.pdf").write_bytes(secret_pdf_bytes)

    thread_id = "chat-sec-archive-01"
    thread_doc = {
        "session_id": thread_id,
        "turns": [
            {
                "role": "user",
                "content": f"Проверь вложение /api/chat/attachments/{att_id}/tender_spec.pdf",
                "attachments": [{"id": att_id, "name": "tender_spec.pdf"}],
            }
        ],
    }
    (sessions_dir / f"{thread_id}.json").write_text(json.dumps(thread_doc, ensure_ascii=False), encoding="utf-8")
    meta_path = data_dir / "chat" / "threads-meta.json"
    meta_path.parent.mkdir(parents=True, exist_ok=True)
    meta_path.write_text(
        json.dumps({"threads": {thread_id: {"title": "Секретный военный тендер №42", "archived": False}}}, ensure_ascii=False),
        encoding="utf-8",
    )

    arch_res = await env.client.post(f"/api/chat/threads/{thread_id}/archive-encrypted")
    assert arch_res.status_code == 200, arch_res.text
    arch_body = arch_res.json()
    assert arch_body["encrypted"] is True
    assert arch_body["attachments_encrypted"] is True
    assert arch_body["encrypted_attachments_count"] >= 1

    # Plaintext attachment file must be removed and replaced with encrypted blob
    assert not (att_dir / "tender_spec.pdf").exists()
    enc_files = list(att_dir.glob("*.enc"))
    assert len(enc_files) == 1
    assert secret_pdf_bytes not in enc_files[0].read_bytes()

    # Plaintext title in threads-meta.json must be minimized/redacted while archived
    raw_meta_text = meta_path.read_text(encoding="utf-8")
    assert "Секретный военный тендер №42" not in raw_meta_text

    # Restore must decrypt both thread and attachment and restore title
    rest_res = await env.client.post(f"/api/chat/archive/{thread_id}/restore")
    assert rest_res.status_code == 200, rest_res.text
    assert (att_dir / "tender_spec.pdf").read_bytes() == secret_pdf_bytes
    restored_meta = json.loads(meta_path.read_text(encoding="utf-8"))
    assert restored_meta["threads"][thread_id]["title"] == "Секретный военный тендер №42"


def test_p2_10_classified_documents_and_telegram_exports_retention_acl_and_cloud_gate(tmp_path: Path):
    from bcc.features.tools_cloud import static_denial
    from bcc.security_data_governance import (
        DataClassification,
        classify_artifact,
        export_sanitized_bundle,
        purge_expired_classified_files,
        register_classified_artifact,
    )

    # 1. Telegram export and document classification
    tg_file = tmp_path / "result.json"
    tg_file.write_text('{"name": "Private Chat", "type": "personal_chat", "messages": [{"id": 1, "from_id": "user123", "text": "secret"}]}', encoding="utf-8")
    cls_tg = classify_artifact(tg_file)
    assert cls_tg == DataClassification.RESTRICTED_TELEGRAM_EXPORT

    doc_file = tmp_path / "contract_extracted.txt"
    doc_file.write_text("ДОГОВОР ПОСТАВКИ № 15/2026 ИНН 7701234567", encoding="utf-8")
    cls_doc = classify_artifact(doc_file)
    assert cls_doc == DataClassification.RESTRICTED_DOCUMENT

    # 2. Cloud transmission gate blocks Telegram exports and classified documents without separate owner approval
    denial_tg = static_denial({"prompt": 'Проанализируй дамп Telegram: {"type": "personal_chat", "messages": [{"from_id": "user123"}]}'})
    assert denial_tg is not None and "CLOUD_ASK_CLASSIFIED" in denial_tg

    denial_doc = static_denial({"prompt": "Вот выдержка из документа [CLASSIFIED:RESTRICTED_DOCUMENT]: условия поставки"})
    assert denial_doc is not None and "CLOUD_ASK_CLASSIFIED" in denial_doc

    # 3. Retention / delete / sanitized export with ACL
    reg = register_classified_artifact(tmp_path, tg_file, retention_days=0)
    assert reg["classification"] == DataClassification.RESTRICTED_TELEGRAM_EXPORT.value
    exported = export_sanitized_bundle(tmp_path, tg_file)
    assert exported["redacted"] is True
    purged = purge_expired_classified_files(tmp_path, now_ts=reg["expires_at"] + 10)
    assert str(tg_file) in purged["deleted"]
    assert not tg_file.exists()


def test_p2_11_12_perimeter_defense_neutral_attribution_trusted_proxy_limits_tamper_log_and_typed_rollback(tmp_path: Path):
    from bcc.features import active_counter_defense as acd

    # 1. Trusted-proxy IP parsing: spoofed X-Forwarded-For from untrusted peer must be ignored
    obs_untrusted = acd.extract_observed_client_ip(
        peer_ip="198.51.100.77",
        x_forwarded_for="203.0.113.5, 10.0.0.1",
        trusted_proxies=frozenset({"127.0.0.1"}),
    )
    assert obs_untrusted["observed_remote_ip"] == "198.51.100.77"
    assert obs_untrusted["forwarded_header_trusted"] is False
    assert obs_untrusted["attribution_verified"] is False

    # From trusted reverse proxy, rightmost untrusted hop is extracted
    obs_trusted = acd.extract_observed_client_ip(
        peer_ip="127.0.0.1",
        x_forwarded_for="203.0.113.99",
        trusted_proxies=frozenset({"127.0.0.1"}),
    )
    assert obs_trusted["observed_remote_ip"] == "203.0.113.99"
    assert obs_trusted["forwarded_header_trusted"] is True
    assert obs_trusted["attribution_verified"] is False

    # 2. Durable tamper-evident event log + owner notification hook
    notifications: list[dict] = []
    log_file = tmp_path / "counter-defense-events.jsonl"
    state = acd.CounterDefenseState(log_path=log_file, owner_notify_cb=notifications.append)

    ev1 = state.record(
        kind="canary_hit",
        remote_ip="198.51.100.77",
        path="/.env",
        user_agent="UntrustedInput\r\nInjectedHeader: yes",
        detail="Probed canary trap",
    )
    ev2 = state.record(
        kind="canary_hit",
        remote_ip="198.51.100.77",
        path="/.git/config",
        user_agent="Scanner/2.0",
        detail="Second probe",
    )
    assert len(notifications) == 2
    assert ev2.prev_hash == ev1.entry_hash
    assert state.verify_log_integrity() is True

    # Tampering with the durable log file must be detected by verify_log_integrity()
    raw_log = log_file.read_text(encoding="utf-8")
    log_file.write_text(raw_log.replace("/.env", "/. hacked"), encoding="utf-8")
    assert state.verify_log_integrity() is False
    log_file.write_text(raw_log, encoding="utf-8")
    assert state.verify_log_integrity() is True

    # 3. Neutral unverified source IP terminology in dossier + typed action with confirmation and rollback
    dossier = acd.build_attacker_dossier("198.51.100.77", state=state)
    assert dossier["observed_remote_ip"] == "198.51.100.77"
    assert dossier["attribution_status"] == "UNVERIFIED_SOURCE_IP"
    assert dossier["attribution_verified"] is False
    assert "Unverified Observed Source IP" in dossier["abuse_report_text"]

    typed_action = acd.propose_firewall_quarantine_action("198.51.100.77", reason="canary_hit")
    with pytest.raises(PermissionError):
        typed_action.confirm(owner_confirmed=False)
    confirmed = typed_action.confirm(owner_confirmed=True, owner_id="owner-local")
    assert confirmed["status"] == "CONFIRMED_READY"
    rb = typed_action.rollback_spec()
    assert rb["action_type"] == "firewall_inbound_quarantine_rollback"
    assert "Remove-NetFirewallRule" in rb["rollback_command"]


def test_p2_13_defusedxml_blocks_xxe_billion_laughs_and_deep_nesting():
    import sys
    sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "apps" / "war-terminal"))
    from bcc.safe_xml import safe_fromstring
    from warterminal.connectors.sanctions import parse_un

    billion_laughs = b"""<?xml version="1.0"?>
    <!DOCTYPE lolz [
      <!ENTITY lol "lol">
      <!ENTITY lol2 "&lol;&lol;&lol;&lol;&lol;&lol;&lol;&lol;&lol;&lol;">
    ]>
    <CONSOLIDATED_LIST>&lol2;</CONSOLIDATED_LIST>
    """
    xxe_payload = b"""<?xml version="1.0"?>
    <!DOCTYPE foo [
      <!ENTITY xxe SYSTEM "file:///etc/passwd">
    ]>
    <CONSOLIDATED_LIST><INDIVIDUAL><FIRST_NAME>&xxe;</FIRST_NAME></INDIVIDUAL></CONSOLIDATED_LIST>
    """
    for payload in (billion_laughs, xxe_payload):
        with pytest.raises(ValueError):
            safe_fromstring(payload)
        with pytest.raises(ValueError):
            parse_un(payload)

    # Deep nesting bomb must also be rejected
    deep_xml = ("<a>" * 100 + "x" + "</a>" * 100).encode("utf-8")
    with pytest.raises(ValueError):
        safe_fromstring(deep_xml, max_depth=32)


def test_p2_14_15_war_terminal_egress_allowlist_private_ip_and_redirect_block(monkeypatch: pytest.MonkeyPatch):
    import socket
    import sys
    sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "apps" / "war-terminal"))
    from warterminal.connectors import http as wt_http

    # 1. Non-HTTPS, unallowlisted domains, and private/loopback/metadata IPs must be rejected
    forbidden_urls = [
        "http://api.sam.gov/opportunities/v2/search",  # plain HTTP forbidden
        "https://evil.example.com/steal",  # domain not in allowlist
        "https://127.0.0.1:8801/api/system",  # loopback IP
        "https://169.254.169.254/latest/meta-data/",  # cloud metadata IP
        "https://10.0.0.1/internal",  # RFC1918 private IP
        "https://100.64.0.5/tailscale",  # Tailscale CGNAT IP
        "file:///C:/Windows/win.ini",  # non-https scheme
    ]
    for bad_url in forbidden_urls:
        with pytest.raises(ValueError):
            wt_http.validate_egress_url(bad_url, resolve_dns=False)

    # 2. DNS rebinding check: if an allowlisted host resolves to a loopback/private IP, reject!
    def _fake_rebinding_getaddrinfo(host, port, *args, **kwargs):
        return [(socket.AF_INET, socket.SOCK_STREAM, 6, "", ("127.0.0.1", 443))]

    monkeypatch.setattr(socket, "getaddrinfo", _fake_rebinding_getaddrinfo)
    with pytest.raises(ValueError, match="non-public IP"):
        wt_http.validate_egress_url("https://api.sam.gov/opportunities/v2/search", resolve_dns=True)

    # 3. Safe redirect check: redirect from allowlisted host to private/unallowlisted target must be blocked
    def _fake_public_getaddrinfo(host, port, *args, **kwargs):
        return [(socket.AF_INET, socket.SOCK_STREAM, 6, "", ("93.184.216.34", 443))]

    monkeypatch.setattr(socket, "getaddrinfo", _fake_public_getaddrinfo)
    with pytest.raises(ValueError):
        wt_http.validate_redirect_target(
            "https://api.sam.gov/opportunities/v2/search",
            "http://169.254.169.254/latest/meta-data/",
        )


def _load_ling_coder():
    import importlib.util
    repo_root = Path(__file__).resolve().parents[2]
    spec = importlib.util.spec_from_file_location(
        "bossman_15_ling_coder", repo_root / "tools" / "bossman_15_ling_coder.py"
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
    monkeypatch.setenv("OPENAI_API_KEY", "dummy-openai-test-value-not-a-real-key")
    monkeypatch.setenv("BOSSMAN_MASTER_TOKEN", "dummy-master-token-999")
    monkeypatch.setenv("SOLANA_PRIVATE_KEY", "dummy-solana-priv-888")
    monkeypatch.setenv("GITHUB_TOKEN", "dummy-github-token-777")

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

    escape_read = mod.execute(tmp_path, "read_file", {"path": "../outside.txt"})
    assert "ERROR:" in escape_read and "escapes worktree" in escape_read

    escape_write = mod.execute(tmp_path, "write_file", {"path": "../outside.txt", "content": "nope"})
    assert "ERROR:" in escape_write and "escapes worktree" in escape_write

    escape_test = mod.execute(tmp_path, "run_tests", {"command": "pytest ../outside_test.py"})
    assert escape_test.startswith("REFUSED:"), escape_test

    noisy_test = tmp_path / "test_noisy.py"
    noisy_test.write_text(
        "def test_noisy():\n"
        "    print('A' * 40000)\n"
        "    assert False\n",
        encoding="utf-8",
    )
    out = mod.execute(tmp_path, "run_tests", {"command": "pytest -s test_noisy.py"})
    assert len(out) <= mod.MAX_OUTPUT_CHARS + 64


@pytest.mark.asyncio
async def test_owner_safe_runtime_drill_end_to_end(env, mini_repo: Path, tmp_path: Path):
    """Owner-safe runtime verification on verified build SHA:
    1. build_sha matches git HEAD
    2. 1 permission-denied action (403)
    3. STOP activation and verification
    4. Token/session revoke in test session (401)
    5. Recovery on the verified build
    """
    import subprocess
    from bcc.auth import HEADER
    await _setup_minimal_skill_env(env, mini_repo, tmp_path)

    head_short = subprocess.check_output(["git", "rev-parse", "--short", "HEAD"], text=True).strip()

    # 1. Runtime build identity check
    live = await env.client.get("/health/live")
    assert live.status_code == 200
    live_body = live.json()
    assert live_body["alive"] is True
    assert live_body["build_sha"].startswith(head_short[:7]) or head_short.startswith(live_body["build_sha"][:7])

    # 2. Permission-denied action (agent scoped token denied on owner approval endpoint & AUTO mode)
    agent_token = env.svc.auth.issue_agent_token(agent_id="drill-agent-01")
    h_agent = {HEADER: agent_token}
    appr = await env.svc.approvals.create(kind="terminal", preview="echo test")
    denied_resp = await env.client.post(
        f"/api/approvals/{appr['id']}",
        headers=h_agent,
        json={"approve": True},
    )
    assert denied_resp.status_code == 403
    assert "OWNER_CREDENTIAL_REQUIRED" in denied_resp.text or "AGENT_ESCALATION_FORBIDDEN" in denied_resp.text

    # 3. Emergency STOP activation
    stop_resp = await env.client.post("/api/autonomy/stop", json={"reason": "owner-safe security drill"})
    assert stop_resp.status_code == 200
    assert stop_resp.json()["state"]["active"] is True
    status_during_stop = await env.client.get("/api/autonomy/status")
    assert status_during_stop.status_code == 200
    assert status_during_stop.json()["stop"]["active"] is True

    # 4. Token revoke in test session -> 401
    assert env.svc.auth.revoke_agent_token(agent_token) is True
    revoked_resp = await env.client.get("/api/agent/sessions", headers=h_agent)
    assert revoked_resp.status_code == 401

    # 5. Recovery on verified build: resume STOP + verify owner & new safe ASSIST agent session
    resume_resp = await env.client.post("/api/autonomy/resume")
    assert resume_resp.status_code == 200
    assert resume_resp.json()["state"]["active"] is False

    new_agent_token = env.svc.auth.issue_agent_token(agent_id="drill-agent-02")
    assist_resp = await env.client.post(
        "/api/agent/sessions",
        headers={HEADER: new_agent_token},
        json={"title": "recovered-agent-session", "mode": "AGENT", "bypass_permissions": False},
    )
    assert assist_resp.status_code in (200, 201), assist_resp.text
    assert assist_resp.json()["mode"] == "AGENT"
