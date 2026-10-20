"""tools/agent_watchdog.py: report-only hung-agent watchdog. Fake process tables only; nothing real is touched,
nothing is sent (fake sender), state lives in tmp_path."""
from __future__ import annotations

import json

import pytest

from tools import agent_watchdog as aw

NOW = 1_800_000_000.0
H = 3600.0
WIN = 60.0
HOME = r"C:\Users\owner"


def proc(pid, name, cmd, age_h, cpu, ppid=1):
    return {"pid": pid, "ppid": ppid, "name": name, "cmd": cmd, "ct": NOW - age_h * H, "cpu": cpu}


def grow(table, extra=None):
    """Second sample: same processes, CPU grown by extra[pid] seconds."""
    extra = extra or {}
    return [{**p, "cpu": (p["cpu"] + extra.get(p["pid"], 0.0)) if p["cpu"] is not None else None} for p in table]


def analyze(first, second=None, **kw):
    return aw.analyze(first, grow(first) if second is None else second, WIN, NOW, me=99999, home=HOME, **kw)


CODEX = r'"C:\Users\owner\AppData\Roaming\npm\node_modules\@openai\codex\bin\codex.exe" exec --json "fix the tests"'


def test_idle_old_codex_flagged_with_manual_close_command():
    t = [proc(10, "codex.exe", CODEX, age_h=9, cpu=40.0)]
    rep = analyze(t, grow(t, {10: 0.3}))
    assert [r["pid"] for r in rep["hung"]] == [10]
    r = rep["hung"][0]
    assert r["kind"] == "codex" and r["role"] == "exec"
    assert r["close_cmd"] == "taskkill /PID 10 /T"
    assert r["cpu_delta_s"] == pytest.approx(0.3)
    assert "9 ч" in r["age"]


def test_busy_old_codex_not_flagged():
    t = [proc(10, "codex.exe", CODEX, age_h=9, cpu=40.0)]
    rep = analyze(t, grow(t, {10: 5.0}))
    assert rep["hung"] == [] and rep["counts"]["agents"] == 1


def test_busy_child_counts_for_the_agent_tree():
    t = [proc(10, "codex.exe", CODEX, age_h=9, cpu=40.0),
         proc(11, "python.exe", "python -m pytest tests", age_h=3, cpu=100.0, ppid=10)]
    rep = analyze(t, grow(t, {11: 30.0}))
    assert rep["hung"] == []


def test_young_idle_codex_not_flagged():
    t = [proc(10, "codex.exe", CODEX, age_h=5.5, cpu=1.0)]
    assert analyze(t)["hung"] == []


def test_recent_child_process_means_not_hung():
    t = [proc(10, "codex.exe", CODEX, age_h=9, cpu=40.0),
         proc(12, "git.exe", "git status", age_h=0.2, cpu=0.1, ppid=10)]
    rep = analyze(t)
    assert rep["hung"] == []
    assert rep["agents"][0]["recent_child"] is True


def test_old_child_does_not_rescue_a_hung_agent():
    t = [proc(10, "codex.exe", CODEX, age_h=9, cpu=40.0),
         proc(12, "bash.exe", "bash -l", age_h=4, cpu=0.1, ppid=10)]
    assert [r["pid"] for r in analyze(t)["hung"]] == [10]


def test_unknown_cpu_is_never_a_hang_verdict():
    t = [proc(10, "codex.exe", CODEX, age_h=9, cpu=None)]
    rep = analyze(t)
    assert rep["hung"] == [] and rep["agents"][0]["cpu_delta_s"] is None


@pytest.mark.parametrize("name,cmd,kind", [
    ("node.exe", r"node C:\Users\owner\AppData\Roaming\npm\node_modules\@anthropic-ai\claude-code\cli.js", "claude"),
    ("claude.exe", r"C:\Users\owner\.local\bin\claude.exe", "claude"),
    ("codex.exe", r"codex app-server", "codex"),
    ("language_server_windows_x64.exe",
     r"C:\Users\owner\AppData\Local\Programs\Antigravity\resources\app\extensions\antigravity\bin\language_server_windows_x64.exe --x",
     "antigravity"),
    ("opencode.exe", "opencode run", "opencode"),
    ("node.exe", r"node C:\npm\node_modules\@qwen-code\qwen-code\cli.js", "qwen"),
])
def test_agent_kinds_recognised(name, cmd, kind):
    assert aw.agent_kind(name, cmd) == kind


@pytest.mark.parametrize("name,cmd", [
    ("python.exe", r"python -m pytest tests\test_codex_bridge.py"),
    ("ollama.exe", "ollama run qwen3:8b"),
    ("node.exe", r"node C:\proj\server.js"),
    ("language_server_windows_x64.exe", r"C:\Windsurf\bin\language_server_windows_x64.exe"),
])
def test_non_agents_ignored(name, cmd):
    assert aw.agent_kind(name, cmd) is None


@pytest.mark.parametrize("cmd", [
    r"python -m bcc --host 127.0.0.1 --port 8801",
    r"python -m bcc.telegram_companion",
    r"python -m bcc.pit --codex-exec",
    r"python -m bossmobile --claude",
    r"python C:\Bossman\tools\resource_governor\governor.py run --codex",
])
def test_bossman_services_and_their_agent_children_never_flagged(cmd):
    svc = proc(20, "python.exe", cmd, age_h=30, cpu=500.0)
    child = proc(21, "codex.exe", CODEX + " --dangerously-bypass-approvals-and-sandbox", age_h=20, cpu=1.0, ppid=20)
    rep = analyze([svc, child])
    assert rep["hung"] == [] and rep["policy"] == [] and rep["agents"] == []
    assert rep["counts"]["skipped_service"] == 1


def test_service_whose_cmdline_mentions_an_agent_is_still_a_service():
    svc = proc(20, "python.exe", r"python -m bcc --host 0.0.0.0 --agent claude", age_h=30, cpu=1.0)
    assert analyze([svc])["agents"] == []


def test_watchdog_never_reports_itself():
    me = proc(99999, "claude.exe", "claude --dangerously-skip-permissions", age_h=9, cpu=0.0)
    assert analyze([me])["agents"] == []


@pytest.mark.parametrize("flag", ["--dangerously-skip-permissions", "--permission-mode bypassPermissions",
                                  "--dangerously-bypass-approvals-and-sandbox"])
def test_bypass_flag_goes_to_policy_not_hung(flag):
    t = [proc(30, "claude.exe", f"claude {flag} -p hello", age_h=1, cpu=10.0)]
    rep = analyze(t, grow(t, {30: 20.0}))
    assert rep["hung"] == []
    assert [r["pid"] for r in rep["policy"]] == [30]
    assert rep["policy"][0]["flag"].lower() in flag.lower()


def test_wrapper_and_child_with_same_flag_reported_once_and_nested_judged_by_parent():
    wrap = proc(40, "node.exe", r"node C:\npm\node_modules\@openai\codex\bin\codex.js exec --dangerously-bypass-approvals-and-sandbox x",
                age_h=10, cpu=1.0)
    child = proc(41, "codex.exe", r"C:\npm\node_modules\@openai\codex\vendor\codex.exe exec --dangerously-bypass-approvals-and-sandbox x",
                 age_h=10, cpu=50.0, ppid=40)
    rep = analyze([wrap, child])
    assert [r["pid"] for r in rep["policy"]] == [40]
    assert [r["pid"] for r in rep["hung"]] == [40]               # tree CPU did not grow: the root only
    busy = analyze([wrap, child], grow([wrap, child], {41: 10.0}))
    assert busy["hung"] == []


def test_redaction_of_tokens_keys_and_private_paths():
    raw = (r'codex exec --api-key sk-proj-ABCDEFGH12345678 OPENAI_API_KEY=sk-live-zzz --token=ghp_abcdefghijklmnop1234 '
           r'-i C:\Users\owner\.ssh\id_ed25519 --cfg C:\Users\owner\Bossman\vault\keys.enc '
           r'Authorization: Bearer abcdefghijklmnopqrstuvwx 123456789:SYNTHETIC_token_for_watchdog_tests0 '
           r'C:\Users\owner\project\main.py')
    out = aw.redact(raw, limit=1000, home=HOME)
    for secret in ("sk-proj-ABCDEFGH12345678", "sk-live-zzz", "ghp_abcdefghijklmnop1234", "id_ed25519", "keys.enc",
                   "abcdefghijklmnopqrstuvwx", "SYNTHETIC_token_for_watchdog_tests0", r"C:\Users\owner"):
        assert secret not in out, secret
    assert r"~\project\main.py" in out
    assert "<redacted>" in out and "<path-redacted>" in out


def test_redaction_truncates_and_keeps_ordinary_paths():
    out = aw.redact(r"node C:\npm\node_modules\@openai\codex\bin\codex.js exec " + "x" * 400, home=HOME)
    assert len(out) <= 160 and out.endswith("…")
    assert r"@openai\codex\bin\codex.js" in out


def test_report_carries_redacted_cmd_only():
    t = [proc(10, "codex.exe", CODEX + " --api-key sk-ant-SECRETSECRET123", age_h=9, cpu=1.0)]
    rep = analyze(t)
    blob = json.dumps(rep, ensure_ascii=False)
    assert "sk-ant-SECRETSECRET123" not in blob


def _report(pids):
    return {"hung": [{"pid": p, "create_time": NOW - 9 * H} for p in pids], "policy": []}


def test_dedup_once_per_6h_and_pid_reuse_is_new():
    rep = _report([10, 11])
    state = {"reported": {}}
    new = aw.select_new(rep, state, NOW)
    assert [r["pid"] for r in new["hung"]] == [10, 11]
    state = aw.mark_reported(state, new, NOW)
    assert aw.select_new(rep, state, NOW + 5 * H)["hung"] == []
    assert [r["pid"] for r in aw.select_new(rep, state, NOW + 6 * H + 1)["hung"]] == [10, 11]
    reused = {"hung": [{"pid": 10, "create_time": NOW - 1 * H}], "policy": []}
    assert [r["pid"] for r in aw.select_new(reused, state, NOW + H)["hung"]] == [10]


def test_dedup_hung_and_policy_are_separate_keys():
    rec = {"pid": 10, "create_time": NOW - 9 * H}
    state = aw.mark_reported({"reported": {}}, {"hung": [rec], "policy": []}, NOW)
    assert aw.select_new({"hung": [rec], "policy": [rec]}, state, NOW + 60)["policy"] == [rec]


def test_state_prunes_old_entries_and_survives_corrupt_file(tmp_path):
    state = {"reported": {"hung:1:1": NOW - 100 * H}}
    state = aw.mark_reported(state, {"hung": [{"pid": 2, "create_time": 5}], "policy": []}, NOW)
    assert list(state["reported"]) == ["hung:2:5"]
    bad = tmp_path / "s.json"
    bad.write_text("{not json", encoding="utf-8")
    assert aw.load_state(bad) == {"reported": {}}


def _run(tmp_path, tables, send, sender=None, now=NOW):
    seq = iter(tables)
    clock = iter([now - WIN, now])
    return aw.run(sample_s=WIN, min_age_h=6, cpu_min_s=2, child_window_h=1, dedup_h=6, send=send,
                  state_file=tmp_path / "state.json", collector=lambda: (next(seq), "fake"),
                  sleeper=lambda s: None, clock=lambda: next(clock), sender=sender)


def test_default_run_prints_only_and_writes_no_state(tmp_path):
    t = [proc(10, "codex.exe", CODEX, age_h=9, cpu=1.0)]
    res = _run(tmp_path, [t, grow(t)], send=False,
               sender=lambda text: pytest.fail("must not send without --send"))
    assert res["sent"] is None and res["counts"]["hung"] == 1
    assert "pid 10" in res["pult_text"] and "taskkill /PID 10 /T" in res["pult_text"]
    assert "только отчёт" in res["pult_text"]
    assert not (tmp_path / "state.json").exists()


def test_send_goes_through_sender_once_then_deduped(tmp_path):
    t = [proc(10, "codex.exe", CODEX, age_h=9, cpu=1.0)]
    sent = []
    res = _run(tmp_path, [t, grow(t)], send=True, sender=lambda text: sent.append(text) or {"message_id": 1})
    assert len(sent) == 1 and res["sent"] == {"message_id": 1}
    assert (tmp_path / "state.json").exists()
    res2 = _run(tmp_path, [t, grow(t)], send=True, sender=lambda text: sent.append(text), now=NOW + H)
    assert len(sent) == 1 and res2["sent"] == {"skipped": "nothing new"}
    assert res2["counts"]["hung"] == 1 and res2["new"]["hung"] == []


def test_failed_send_does_not_mark_state(tmp_path):
    t = [proc(10, "codex.exe", CODEX, age_h=9, cpu=1.0)]

    def boom(text):
        raise RuntimeError("telegram down")

    with pytest.raises(RuntimeError):
        _run(tmp_path, [t, grow(t)], send=True, sender=boom)
    assert not (tmp_path / "state.json").exists()


def test_pult_text_warns_that_app_server_and_interactive_may_be_legit():
    rec = {"pid": 1, "kind": "codex", "role": "app-server", "age": "7 ч", "cpu_delta_s": 0.0, "window_s": 60,
           "cmd": "codex app-server", "close_cmd": "taskkill /PID 1 /T"}
    text = aw.pult_text({"hung": [rec, {**rec, "pid": 2, "kind": "claude", "role": "interactive"}], "policy": []},
                        {"agents": 2}, NOW, min_age_h=6, cpu_min_s=2, child_window_h=1)
    assert "открытый редактор" in text and "ждать ввода" in text


def test_pult_text_without_findings():
    text = aw.pult_text({"hung": [], "policy": []}, {"agents": 3, "skipped_service": 1}, NOW,
                        min_age_h=6, cpu_min_s=2, child_window_h=1)
    assert "Новых подозрений нет" in text and "Всего агентов: 3" in text


def test_cim_fallback_parser():
    raw = json.dumps([{"pid": 5, "ppid": 1, "name": "codex.exe", "cmd": "codex exec x", "ct": NOW - 7 * H,
                       "k": 10_000_000, "u": 20_000_000},
                      {"pid": 6, "ppid": 1, "name": "System", "cmd": None, "ct": 0, "k": None, "u": None}])
    rows = aw.parse_cim(raw)
    assert rows[0]["cpu"] == pytest.approx(3.0) and rows[0]["cmd"] == "codex exec x"
    assert rows[1]["cpu"] is None and rows[1]["cmd"] == ""
    assert aw.parse_cim(json.dumps({"pid": 7, "name": "x", "ct": 1}))[0]["pid"] == 7


def test_service_patterns_come_from_the_governor():
    assert aw._GOV is not None
    assert aw.SERVICE_CMD == tuple(aw._GOV.SERVICE_CMD)
