"""Unit tests for Bossman Legit Growth Network (10 Channels + 10 Campaigns + Bubble Map) & LoRA Corpus."""

from __future__ import annotations

import json
from pathlib import Path

from learning.bossman_legit_growth_and_lora import (
    OWNER_VERIFIED_WALLET,
    build_10_marketing_campaigns,
    build_10_telegram_channels,
    build_gems_and_pumpfun_compliance_manifest,
    build_network_graph_data,
    build_source_grounded_lora_corpus,
    compute_file_sha256,
    generate_all_deliverables,
    run_lora_security_and_quality_gates,
)


REPO_ROOT = Path(__file__).resolve().parents[1]


def test_10_telegram_channels_structure_and_honest_launch_status() -> None:
    channels = build_10_telegram_channels()
    assert len(channels) == 10
    handles = {c.proposed_handle for c in channels}
    assert len(handles) == 10
    for ch in channels:
        assert ch.proposed_handle.startswith("@bossman_")
        assert "[BOT]" in ch.bot_disclosure_label
        assert len(ch.content_plan_30d) == 4
        assert ch.launch_status == "PLANNED_AWAITING_OWNER_APPROVAL"
        assert ch.live_channel_id_or_link is None


def test_10_marketing_campaigns_zero_budget_and_honest_verdict() -> None:
    campaigns = build_10_marketing_campaigns()
    assert len(campaigns) == 10
    gems_ids = {c.gems_campaign_id for c in campaigns}
    assert len(gems_ids) == 10
    for camp in campaigns:
        assert camp.budget_usd == 0.0
        assert camp.realized_spend_usd == 0.0
        assert camp.realized_revenue_usd == 0.0
        assert "utm_source=" in camp.utm_template
        assert camp.gems_campaign_id in camp.utm_template
        assert len(camp.stop_criteria) >= 2
        assert "PASS (PAPER_PILOT)" in camp.honest_verdict
        assert "UNKNOWN" in camp.honest_verdict


def test_gems_single_owner_and_pumpfun_compliance() -> None:
    campaigns = build_10_marketing_campaigns()
    compliance = build_gems_and_pumpfun_compliance_manifest(campaigns)
    gems_policy = compliance["gems_and_referrals_policy"]["single_owner_profile_enforcement"]
    assert gems_policy["owner_wallet"] == OWNER_VERIFIED_WALLET
    assert gems_policy["total_accounts_used"] == 1
    assert gems_policy["sybil_accounts_created"] == 0
    assert gems_policy["self_referral_loops_created"] == 0
    assert gems_policy["wash_trades_executed"] == 0
    assert len(gems_policy["trackable_campaign_ids"]) == 10

    pump_policy = compliance["pumpfun_and_tokens_policy"]
    assert pump_policy["executed_live_token_trades_count"] == 0
    assert pump_policy["executed_wash_volume_usd"] == 0.0

    tg2 = compliance["second_telegram_account_gate"]
    assert tg2["sms_or_2fa_entered_by_agent"] is False
    assert tg2["session_persisted_in_reports"] is False
    assert tg2["private_chats_read_count"] == 0


def test_bubble_map_and_network_graph_distinguish_real_vs_paper() -> None:
    channels = build_10_telegram_channels()
    campaigns = build_10_marketing_campaigns()
    graph = build_network_graph_data(channels, campaigns)
    counts = graph["summary_counts"]
    assert counts["real_verified_nodes"] == 7
    assert counts["paper_only_planned_nodes"] == 30
    assert counts["fake_trades_depicted"] == 0
    assert counts["sybil_accounts_depicted"] == 0


def test_lora_corpus_holdout_isolation_and_5_security_gates() -> None:
    records, sources = build_source_grounded_lora_corpus(REPO_ROOT)
    assert len(sources) == 20
    assert len(records) == 60

    required_fields = {
        "record_id",
        "split",
        "source_path",
        "source_sha256",
        "date",
        "capability",
        "instruction",
        "expected_response_or_tool_call",
        "permission_class",
        "expected_postcondition",
        "limitations",
        "provenance",
    }
    for r in records:
        rec_dict = r.__dict__
        assert required_fields.issubset(rec_dict.keys())

    gate_report = run_lora_security_and_quality_gates(records, REPO_ROOT)
    assert gate_report["overall_status"] == "PASS"
    assert gate_report["counts"]["holdout_ratio_of_positive"] >= 0.15
    assert gate_report["counts"]["train_holdout_overlap"] == 0
    for gate_name, gate_res in gate_report["gates"].items():
        assert gate_res["status"] == "PASS", f"Gate {gate_name} failed: {gate_res}"


def test_end_to_end_deliverable_files_and_manifest_hashes() -> None:
    summary = generate_all_deliverables(REPO_ROOT)
    assert summary["financial_journal"]["total_spend_usd"] == 0.0
    assert summary["lora_bundle"]["training_config"]["weights_trained_in_this_run"] is False

    for name, meta in summary["artifacts"].items():
        p = Path(meta["path"])
        assert p.exists(), f"Missing artifact {name}"
        assert p.stat().st_size > 0
        assert compute_file_sha256(p) == meta["sha256"]

    manifest_path = Path(summary["lora_bundle"]["manifest_path"])
    assert manifest_path.exists()
    manifest_data = json.loads(manifest_path.read_text(encoding="utf-8"))
    corpus_dir = manifest_path.parent
    for fname, fmeta in manifest_data["files"].items():
        fpath = corpus_dir / fname
        assert fpath.exists()
        assert compute_file_sha256(fpath) == fmeta["sha256"]
