"""Tests for the Claude ops tools in tools/: gen_watch, oss_check, git_velocity, llm_task_bench, pc_defense_audit.

Pure logic only - no network, no Bossman services, no PowerShell: every external call is injected or monkeypatched.
"""
from __future__ import annotations

import datetime as dt
import json
from types import SimpleNamespace

from tools import gen_watch, git_velocity, llm_task_bench, oss_check, pc_defense_audit as pda


# ------------------------------------------------------------------------------------------------------- gen_watch

def _job(jid, status, kind="photo", progress=None, error=None):
    return {"job_id": jid, "status": status, "kind": kind, "progress": progress, "error": error}


def test_gen_watch_reports_only_transitions_and_edges():
    w = gen_watch.Watcher(stall_s=600, model_match="gemma4-31b")
    first = w.step([_job("abc123", "generating", progress={"step": 1})], ["gemma4-31b:latest"], True, 1000.0)
    assert first == []                                               # first poll is a baseline, never an event
    same = w.step([_job("abc123", "generating", progress={"step": 2})], ["gemma4-31b:latest"], True, 1060.0)
    assert same == []
    done = w.step([_job("abc123", "completed")], None, True, 1120.0)
    assert done == ["JOB abc123 photo -> completed  | live left 0", "QUEUE EMPTY: all generations finished"]
    again = w.step([_job("abc123", "completed")], None, True, 1180.0)
    assert again == []                                               # completed job reported once


def test_gen_watch_stall_jeff_and_model_edges():
    w = gen_watch.Watcher(stall_s=600, model_match="gemma4-31b")
    w.step([_job("s1", "loading", progress={"pct": 10})], ["gemma4-31b:latest"], True, 0.0)
    assert w.step([_job("s1", "loading", progress={"pct": 10})], ["gemma4-31b:latest"], True, 300.0) == []
    stalled = w.step([_job("s1", "loading", progress={"pct": 10})], ["other"], False, 700.0)
    assert stalled[0].startswith("STALL s1 loading no progress 11 min")
    assert "JEFF DOWN (process not running)" in stalled
    assert any(s.startswith("JEFF MODEL UNLOADED") for s in stalled)
    assert w.step([_job("s1", "loading", progress={"pct": 10})], ["other"], False, 800.0) == []    # edges fire once
    back = w.step([_job("s1", "loading", progress={"pct": 11})], ["gemma4-31b:latest"], True, 900.0)
    assert back == ["JEFF back up", "JEFF MODEL loaded again"]
    failed = w.step([_job("s1", "failed", error={"message": "OOM" * 60})], ["gemma4-31b:latest"], True, 1000.0)
    assert failed[0].startswith("JOB s1 photo -> failed OOM") and len(failed[0]) < 160


# ------------------------------------------------------------------------------------------------------- oss_check

def test_oss_flags_copyleft_archived_stale_and_missing_license():
    today = dt.date(2026, 10, 22)
    assert oss_check.flags_for({"license": "MIT", "pushed": "2026-10-01T00:00:00Z"}, today) == []
    assert oss_check.flags_for({"license": "GPL-3.0", "archived": True, "pushed": "2024-01-01"}, today) == ["COPYLEFT", "ARCHIVED", "STALE"]
    assert oss_check.flags_for({"license": "NOASSERTION"}, today) == ["NO-LICENSE"]
    assert oss_check.flags_for({"license": "Apache-2.0", "pushed": "garbage"}, today) == []


def test_oss_check_uses_injected_registry_and_reports_errors():
    def fake_get(url):
        if "pypi.org" in url:
            return {"info": {"version": "1.2.3", "license_expression": "MIT"}}
        if "npmjs.org" in url:
            return {"version": "9.0.0", "license": "AGPL-3.0"}
        if "api.github.com/repos/good" in url:
            return {"stargazers_count": 42, "license": {"spdx_id": "MIT"}, "pushed_at": dt.date.today().isoformat() + "T00:00:00Z", "archived": False}
        raise RuntimeError("404")
    assert oss_check.check("pypi", "x", get=fake_get)["flags"] == []
    assert oss_check.check("npm", "y", get=fake_get)["flags"] == ["COPYLEFT"]
    gh = oss_check.check("github", "good/repo", get=fake_get)
    assert gh["stars"] == 42 and gh["flags"] == []
    bad = oss_check.check("github", "missing/repo", get=fake_get)
    assert bad["flags"] == ["ERR"] and "ERR" in oss_check.line(bad)
    assert "!! COPYLEFT" in oss_check.line(oss_check.check("npm", "y", get=fake_get))


# ---------------------------------------------------------------------------------------------------- git_velocity

LOG = """@@aaa|2026-10-20T10:00:00+00:00|Argon||feat: x
12\t3\tcommand-center/bcc/x.py
100\t0\tdocs/owner/X.md
@@bbb|2026-10-20T10:30:00+00:00|Argon||docs: y
5\t5\tREADME.md
@@ccc|2026-10-20T13:00:00+00:00|molotroka|Claude Sonnet <noreply@anthropic.com>|fix: z
7\t1\ttools/t.py
-\t-\tdocs/assets/a.png
"""


def test_git_velocity_attributes_claude_trailer_and_counts_code_only():
    commits = git_velocity.parse_log(LOG)
    assert [c["code_add"] for c in commits] == [12, 0, 7]            # docs/ and .md are not code; binary numstat skipped
    res = git_velocity.summarize(commits, gap_s=3600)
    assert set(res) == {"Argon", "Claude"}
    assert res["Argon"] == {"commits": 2, "added": 117, "deleted": 8, "code_added": 12, "active_hours": 0.5,
                            "code_per_active_hour": 24, "biggest_commit_code": 12, "per_day": {"10-20": 2}}
    assert res["Claude"]["commits"] == 1 and res["Claude"]["active_hours"] == 0.0


# -------------------------------------------------------------------------------------------------- llm_task_bench

def test_extract_code_prefers_longest_python_block():
    text = "intro\n```python\nx = 1\n```\nmore\n```py\ndef f():\n    return 2\n```"
    assert llm_task_bench.extract_code(text) == "def f():\n    return 2\n"
    assert llm_task_bench.extract_code("no fences") == "no fences"


def test_run_check_executes_real_unit_test_in_sandbox():
    ok, _ = llm_task_bench.run_check("def add(a, b):\n    return a + b\n", "from sol import add\nassert add(2, 2) == 4\n")
    assert ok
    bad, note = llm_task_bench.run_check("def add(a, b):\n    return a - b\n", "from sol import add\nassert add(2, 2) == 4\n")
    assert not bad and "AssertionError" in note


def test_run_bench_counts_pass_latency_and_errors():
    answers = {"t1": "```python\ndef f():\n    return 1\n```", "t2": "```python\ndef f():\n    return 0\n```"}
    calls = []

    def chat(messages):
        calls.append(messages)
        task = next(k for k in answers if k in messages[1]["content"])
        if task == "t2":
            raise RuntimeError("HTTP 429")
        return answers[task], {"prompt_tokens": 10, "completion_tokens": 5}, "stop"
    ticks = iter([0.0, 1.5, 2.0, 2.25])
    tasks = [("t1", "task t1", "from sol import f\nassert f() == 1\n"), ("t2", "task t2", "from sol import f\nassert f() == 0\n")]
    res = llm_task_bench.run_bench(tasks, chat, samples=1, clock=lambda: next(ticks))
    assert res["n"] == 2 and res["passed"] == 1 and res["pass_rate"] == 0.5
    assert res["rows"][0]["latency_s"] == 1.5 and res["rows"][1]["error"] == "HTTP 429"
    assert calls[0][0]["role"] == "system" and "task t1" in calls[0][1]["content"]


def test_resolve_key_never_reads_outside_env_or_keys_file(tmp_path, monkeypatch):
    monkeypatch.delenv("BENCH_TEST_KEY", raising=False)
    monkeypatch.setenv("BCC_DATA_DIR", str(tmp_path / "CommandCenter"))
    (tmp_path / "keys").mkdir()
    (tmp_path / "keys" / "provider-keys.env").write_text('OTHER=1\nBENCH_TEST_KEY="abc"\n', encoding="utf-8")
    assert llm_task_bench.resolve_key("BENCH_TEST_KEY") == "abc"
    assert llm_task_bench.resolve_key("MISSING_KEY") is None


# ------------------------------------------------------------------------------------------------ pc_defense_audit

def _ps(responses):
    def fake(script, timeout=60.0):
        for key, value in responses.items():
            if key in script:
                return value
        return None
    return fake


def test_defense_grades_by_worst_finding_and_isolates_a_broken_check(monkeypatch):
    monkeypatch.setattr(pda, "ps_json", _ps({
        "Get-MpComputerStatus": {"RealTimeProtectionEnabled": False, "AntivirusEnabled": True, "AntivirusSignatureAge": 9,
                                 "QuickScanAge": 1, "IsTamperProtected": True},
        "Get-NetFirewallProfile": [{"Name": "Public", "Enabled": False}, {"Name": "Private", "Enabled": True}],
        "Get-NetTCPConnection": [{"LocalAddress": "0.0.0.0", "LocalPort": 3389, "OwningProcess": 4},
                                 {"LocalAddress": "0.0.0.0", "LocalPort": 11434, "OwningProcess": 5},
                                 {"LocalAddress": "127.0.0.1", "LocalPort": 8801, "OwningProcess": 6}],
        "Get-Process": [{"Id": 4, "ProcessName": "svchost"}, {"Id": 5, "ProcessName": "ollama"}],
        "$keys=": [{"Key": "HKCU", "Name": "Updater", "Exe": "C:\\x\\u.exe", "Sig": "NotSigned"},
                   {"Key": "HKLM", "Name": "Ok", "Exe": "C:\\ok.exe", "Sig": "Valid"}],
        "Get-ScheduledTask": [{"Name": "T", "Path": "\\", "Exec": "powershell -ExecutionPolicy bypass -file c:\\temp\\x.ps1"},
                              {"Name": "Fine", "Path": "\\", "Exec": "C:\\Program Files\\app\\app.exe"}],
        "Get-WinEvent": {"Failed": 25, "TopIp": "10.0.0.9 x25", "NewAdmins": 0, "Cleared": 1},
        "EnableLUA": {"UAC": 0, "SmartScreen": "Off"},
    }))
    monkeypatch.setattr(pda.subprocess, "run", lambda *a, **k: (_ for _ in ()).throw(OSError("no tailscale")))
    rep = pda.run_audit()
    kinds = {(f["severity"], f["check"]) for f in rep["findings"]}
    assert rep["grade"] == "RED"
    assert ("HIGH", "defender") in kinds and ("HIGH", "firewall") in kinds and ("HIGH", "ports") in kinds
    assert ("MEDIUM", "ports") in kinds and ("MEDIUM", "autorun") in kinds and ("MEDIUM", "tasks") in kinds
    assert ("HIGH", "seclog") in kinds and ("HIGH", "hardening") in kinds and ("MEDIUM", "hardening") in kinds
    assert not any("Ok" in f["detail"] or "Fine" in f["detail"] or ":8801" in f["detail"] for f in rep["findings"])


def test_defense_green_when_clean_and_html_escapes(monkeypatch):
    monkeypatch.setattr(pda, "ps_json", _ps({
        "Get-MpComputerStatus": {"RealTimeProtectionEnabled": True, "AntivirusEnabled": True, "AntivirusSignatureAge": 0,
                                 "QuickScanAge": 1, "IsTamperProtected": True},
        "Get-NetFirewallProfile": [{"Name": "Public", "Enabled": True}],
        "Get-NetTCPConnection": [], "Get-Process": [], "$keys=": [], "Get-ScheduledTask": [],
        "Get-WinEvent": {"Failed": 0, "TopIp": None, "NewAdmins": 0, "Cleared": 0},
        "EnableLUA": {"UAC": 1, "SmartScreen": "RequireAdmin"},
    }))
    monkeypatch.setattr(pda.subprocess, "run", lambda *a, **k: SimpleNamespace(stdout="No funnel configured\n"))
    rep = pda.run_audit()
    assert rep["grade"] == "GREEN" and rep["findings"] == []
    html = pda.to_html({"ts": "2026-10-22T10:00:00", "grade": "YELLOW",
                        "findings": [{"severity": "MEDIUM", "check": "tasks", "detail": "a <b> & c"}]})
    assert "<code>a &lt;b> &amp; c</code>" in html and html.startswith("<b>🟡")


def test_defense_broken_check_becomes_low_finding(monkeypatch):
    def boom(script, timeout=60.0):
        raise KeyError("x")
    monkeypatch.setattr(pda, "ps_json", boom)
    monkeypatch.setattr(pda.subprocess, "run", lambda *a, **k: SimpleNamespace(stdout=""))
    rep = pda.run_audit()
    assert rep["findings"] and all(f["severity"] == "LOW" for f in rep["findings"])
    assert rep["grade"] == "GREEN" and json.dumps(rep)          # LOW alone never raises the grade
