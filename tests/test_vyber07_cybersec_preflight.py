from __future__ import annotations

from tools.vyber07_cybersec_preflight import (
    check_gated_access,
    deduplicate_siblings,
    is_eligible_training_file,
    normalize_cybersec_record,
)


def test_deduplicate_siblings_filters_tar_gz_and_oid_duplicates() -> None:
    siblings = [
        {"rfilename": "vulnerability-intelligence/cybersec_master.jsonl", "size": 2555 * 1024 * 1024, "lfs": {"oid": "sha-master"}},
        {"rfilename": "vulnerability-intelligence__cybersec_master.jsonl", "size": 2555 * 1024 * 1024, "lfs": {"oid": "sha-master"}},
        {"rfilename": "vulnerability-intelligence__cybersec_master__dup1.jsonl", "size": 2420 * 1024 * 1024, "lfs": {"oid": "sha-dup1"}},
        {"rfilename": "vulnerability-intelligence__arvo__3305__repo-vul.tar.gz", "size": 477 * 1024 * 1024, "lfs": {"oid": "sha-tar"}},
        {"rfilename": ".openclaude__settings.local.json", "size": 1024, "lfs": {"oid": "sha-dot"}},
        {"rfilename": "vulnerability-intelligence/Cybersecurity-ShareGPT.jsonl", "size": 94 * 1024 * 1024, "lfs": {"oid": "sha-sharegpt"}},
    ]
    res = deduplicate_siblings(siblings)
    assert res["clean_unique_files_count"] == 2
    names = [f["rfilename"] for f in res["top_clean_files"]]
    assert names == [
        "vulnerability-intelligence/cybersec_master.jsonl",
        "vulnerability-intelligence/Cybersecurity-ShareGPT.jsonl",
    ]
    assert res["archive_bloat_mb"] == 477.0
    assert res["size_reduction_x"] > 3.0


def test_normalize_cybersec_record_handles_all_five_schemas() -> None:
    r1 = normalize_cybersec_record(
        {"conversations": [{"from": "human", "value": "How to prevent SQLi in Python?"}, {"from": "gpt", "value": "Always use parameterized queries with placeholders."}]}
    )
    assert r1 is not None
    assert r1["messages"][0]["role"] == "user"
    assert r1["messages"][1]["role"] == "assistant"

    r2 = normalize_cybersec_record(
        {"instruction": "Explain SSRF mitigation", "input": "Internal metadata IP", "output": "Block 169.254.169.254 at egress firewall and enforce IMDSv2."}
    )
    assert r2 is not None
    assert "169.254.169.254" in r2["messages"][1]["content"]

    r3 = normalize_cybersec_record(
        {"title": "CVE-2026-1001", "text": "Buffer overflow in custom parser allows remote code execution when header length exceeds 4096 bytes. Patch by validating bounds."}
    )
    assert r3 is not None
    assert len(r3["sha256"]) == 64


def test_is_eligible_and_missing_token_blocks_gpu_spend(monkeypatch) -> None:
    monkeypatch.delenv("HF_TOKEN", raising=False)
    monkeypatch.delenv("HUGGINGFACE_TOKEN", raising=False)
    assert is_eligible_training_file("vulnerability-intelligence__arvo.tar.gz") is False
    assert is_eligible_training_file("file__dup1.jsonl") is False
    assert is_eligible_training_file("vulnerability-intelligence/cybersec_master.jsonl") is True
    gate = check_gated_access("")
    assert gate["ok"] is False
    assert gate["status_code"] == 401
