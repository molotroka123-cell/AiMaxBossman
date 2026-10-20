"""Regression tests for Claude Audit P2 items (Items 19..25)."""
from __future__ import annotations

import hashlib
import json
import sqlite3
from datetime import datetime
from pathlib import Path
from types import SimpleNamespace

import pytest

from tools import (
    benchmark_bossman_speedup,
    import_operational_lessons,
    limits_out_orchestra,
    night_autonomy,
    owner_one_bossman,
    verify_tier5_argon_e2e,
)

ROOT = Path(__file__).resolve().parents[1]


def test_item19_telegram_stop_no_hardcoded_literals_and_not_run_on_empty(tmp_path: Path):
    res_empty = verify_tier5_argon_e2e.inspect_historical_live_telegram_stop(
        comp_db=tmp_path / "no_comp.sqlite3",
        audit_path=tmp_path / "no_audit.jsonl",
    )
    assert res_empty["status"] == "NOT_RUN"

    db_path = tmp_path / "companion.sqlite3"
    conn = sqlite3.connect(str(db_path))
    conn.execute("CREATE TABLE inbox (id INTEGER PRIMARY KEY, who TEXT, lane TEXT, phase TEXT, created REAL)")
    conn.execute(
        "INSERT INTO inbox (id, who, lane, phase, created) VALUES (?, ?, ?, ?, ?)",
        (2299, "owner", "stop", "done", 1791990000.5),
    )
    conn.commit()
    conn.close()

    audit_path = tmp_path / "audit.jsonl"
    record = {
        "seq": 1,
        "ts": "2026-10-19T12:00:00Z",
        "task_id": "t-stop-1",
        "run_id": "r-1",
        "actor": "owner",
        "tool": "pc.stop",
        "tier": "T0",
        "decision": "allow",
        "approval_id": None,
        "verified": True,
        "args": {"reason": "owner_stop"},
        "result": {"stopped": True},
        "prev_hash": "0" * 64,
    }
    record["entry_hash"] = hashlib.sha256(
        json.dumps(record, sort_keys=True, ensure_ascii=False).encode("utf-8")
    ).hexdigest()
    audit_path.write_text(json.dumps(record, ensure_ascii=False) + "\n", encoding="utf-8")

    res_live = verify_tier5_argon_e2e.inspect_historical_live_telegram_stop(
        comp_db=db_path,
        audit_path=audit_path,
    )
    assert res_live["status"] == "PASS"
    assert res_live["verified_from_disk_files"] is True
    assert res_live["inbox_record"]["update_id"] == 2299
    assert len(res_live["companion_db_sha256"]) == 64
    assert len(res_live["audit_jsonl_sha256"]) == 64


def test_item20_g4_memory_label_and_transfer30_not_run_without_artifact(tmp_path: Path):
    res = verify_tier5_argon_e2e.inspect_g4_memory_and_transfer_30(artifact_path=tmp_path / "missing.json")
    assert res["kind_label"] == "память, не обучение (memory, not weight training)"
    assert res["is_weight_training"] is False
    assert res["transfer_30_tasks_status"] == "NOT_RUN"


def test_item21_policy_ssrf_equivalence_and_real_session_quote_bench():
    eq = benchmark_bossman_speedup.verify_policy_ssrf_equivalence()
    assert eq["equivalent"] is True
    assert eq["corpus_size"] >= 22
    assert eq["mismatches"] == []

    bench = benchmark_bossman_speedup.bench_real_session_quote_response(iterations=5)
    assert bench["iterations"] == 5
    assert bench["latency_ms"]["p50_ms"] > 0.0


def test_item22_executable_checks_fail_on_missing_files_and_ast_bypass(tmp_path: Path):
    empty_res = import_operational_lessons.run_executable_checks(tmp_path)
    assert empty_res["failed"] == 10
    assert empty_res["passed"] == 0

    fake_service = tmp_path / "command-center" / "bcc" / "telegram_companion" / "service.py"
    fake_service.parent.mkdir(parents=True)
    # Only a comment containing the words — no AST function definition
    fake_service.write_text('# delivery_allowed authorize_delivery "owner"\nx = 1\n', encoding="utf-8")
    ok, _ = import_operational_lessons._check_pult_owner_only(tmp_path)
    assert ok is False

    real_res = import_operational_lessons.run_executable_checks(ROOT)
    assert real_res["failed"] == 0, f"Failed checks on ROOT: {real_res['failed_ids']}"
    assert real_res["passed"] == 10


def test_item23_soak_sample_requires_density_gap_and_scheduler(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setattr(
        owner_one_bossman,
        "status",
        lambda data_dir, companion_config, mobile_port=8840: {
            "backend": {"pid": 1234, "port": 8801},
            "backend_pid_alive": True,
            "health": {"alive": True, "source_identity": "PASS"},
            "mobile": {"ok": True},
        },
    )
    # Two samples 24h apart -> huge gap and low sample count -> must FAIL, never PASS
    state_dir = tmp_path / owner_one_bossman.STATE_DIR
    state_dir.mkdir(parents=True)
    log_path = state_dir / "soak-24h.jsonl"
    t0 = 1792000000.0
    log_path.write_text(
        json.dumps({"ts": t0, "backend_pid": 1234, "all_ok": True}) + "\n",
        encoding="utf-8",
    )
    sparse = owner_one_bossman.soak_sample(
        tmp_path,
        interval_s=300,
        window_hours=24.0,
        now_ts=t0 + 24.0 * 3600.0,
        scheduler_running=True,
    )
    assert sparse["verdict"] == "FAIL"
    assert sparse["density_ok"] is False

    # Dense samples across a 1-hour window with interval_s=300 (target=12, min_required=10)
    dense_dir = tmp_path / "dense"
    dense_state = dense_dir / owner_one_bossman.STATE_DIR
    dense_state.mkdir(parents=True)
    dense_log = dense_state / "soak-24h.jsonl"
    rows = [
        json.dumps({"ts": t0 + i * 300, "backend_pid": 1234, "all_ok": True})
        for i in range(12)
    ]
    dense_log.write_text("\n".join(rows) + "\n", encoding="utf-8")

    no_sched = owner_one_bossman.soak_sample(
        dense_dir,
        interval_s=300,
        window_hours=1.0,
        now_ts=t0 + 12 * 300,
        scheduler_running=False,
    )
    assert no_sched["density_ok"] is True
    assert no_sched["scheduler_ok"] is False
    assert no_sched["verdict"] == "FAIL"

    dense_dir2 = tmp_path / "dense2"
    dense_state2 = dense_dir2 / owner_one_bossman.STATE_DIR
    dense_state2.mkdir(parents=True)
    (dense_state2 / "soak-24h.jsonl").write_text("\n".join(rows) + "\n", encoding="utf-8")

    with_sched = owner_one_bossman.soak_sample(
        dense_dir2,
        interval_s=300,
        window_hours=1.0,
        now_ts=t0 + 12 * 300,
        scheduler_running=True,
    )
    assert with_sched["density_ok"] is True
    assert with_sched["scheduler_ok"] is True
    assert with_sched["verdict"] == "PASS"


def test_item24_cross_family_gemma_gemini_llama_nemotron_and_missing_verifier(tmp_path: Path):
    assert limits_out_orchestra.model_family("gemma4:26b") == "google"
    assert limits_out_orchestra.model_family("gemini-2.5-pro") == "google"
    assert limits_out_orchestra.model_family("llama3.1:8b") == "meta_llama"
    assert limits_out_orchestra.model_family("nemotron-mini:4b") == "meta_llama"

    ev = tmp_path / "ev"
    ev.mkdir()
    (ev / "task-01.json").write_text(json.dumps({"model": "gemma4:e4b"}), encoding="utf-8")
    (ev / "recipe-01.json").write_text(json.dumps({"stages": {}}), encoding="utf-8")

    missing = limits_out_orchestra.cross_family_check(ev)
    assert missing["ok"] is False
    assert missing["reason"] == "missing verifier block"

    (ev / "recipe-01.json").write_text(
        json.dumps({"verifier": {"model_id": "gemini-2.5-flash", "independence_class": "cross_model"}}),
        encoding="utf-8",
    )
    same_google = limits_out_orchestra.cross_family_check(ev)
    assert same_google["ok"] is False
    assert "google == google" in same_google["reason"]

    (ev / "recipe-01.json").write_text(
        json.dumps({"verifier": {"model_id": "qwen2.5-coder:14b", "independence_class": "cross_model"}}),
        encoding="utf-8",
    )
    diff_fam = limits_out_orchestra.cross_family_check(ev)
    assert diff_fam["ok"] is True


def test_item25_night_autonomy_default_permission_mode_and_bypass_coercion(tmp_path: Path):
    parsed = night_autonomy.parse_args(["--archive", "bundle.zip"])
    assert parsed.permission_mode == "default"

    seen_cmds: list[list[str]] = []

    def fake_runner(cmd, *, cwd, timeout, env=None, stdin=None):
        seen_cmds.append([str(c) for c in cmd])
        if cmd[:3] == ["git", "rev-parse", "HEAD"]:
            return 0, "a" * 40 + "\n"
        return 0, json.dumps({"result": '{"to":"bossman","done":[]}', "is_error": False})

    args = SimpleNamespace(
        archive=str(tmp_path / "bundle.zip"),
        until="23:00",
        every=45,
        permission_mode="bypassPermissions",
        build_python="py",
        once=True,
    )
    night = night_autonomy.Night(
        args,
        runner=fake_runner,
        which=lambda _: "claude.exe",
        clock=lambda: datetime(2026, 10, 20, 12, 0, 0),
        sleep=lambda _: None,
        evidence_root=tmp_path / "ev",
    )
    night.step_dialogue({"baseline": {"ux_errors": 0}})
    claude_calls = [c for c in seen_cmds if "claude.exe" in c[0]]
    assert len(claude_calls) == 1
    idx = claude_calls[0].index("--permission-mode")
    assert claude_calls[0][idx + 1] == "default"
