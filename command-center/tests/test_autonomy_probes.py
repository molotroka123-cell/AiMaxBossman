"""Real staging probes: in-process checks against a temp data dir, candidate-subprocess wiring with a fake runner."""
from __future__ import annotations

import json
import subprocess
from pathlib import Path
from types import SimpleNamespace

import pytest

from bcc.autonomy import probes as PR
from bcc.autonomy import staging
from bcc.autonomy.goals import GoalStore
from bcc.autonomy.journal import Journal
from bcc.autonomy.types import Budget, Goal


@pytest.mark.parametrize("name", ["telegram_fake", "memory", "jeff_identity", "voice_status", "model_routing"])
def test_in_process_checks_pass_on_a_clean_temp_dir(tmp_path, name):
    res = PR.run_check(name, tmp_path)
    assert res["ok"], res


class _Leaky(PR._FakeModel):
    async def chat(self, model, messages, **kw):
        from bcc.providers import ChatResult
        return ChatResult(text="I'm actually Qwen from Alibaba.", tokens_in=1, tokens_out=1, model=model)


def test_jeff_identity_counts_leaks_of_the_runtime(tmp_path, monkeypatch):
    from bcc.pit import runtime as rt
    monkeypatch.setattr(PR, "_FakeModel", _Leaky)
    # Since the mandatory identity filter (autonomy line C, ParticipantRuntime.guard_outgoing) a leaky MODEL no
    # longer makes a leaky RUNTIME. The probe must count leaks of a runtime that does leak - a candidate whose
    # filter regressed - so that candidate is simulated by switching the filter off explicitly.
    monkeypatch.setattr(rt.ParticipantRuntime, "guard_outgoing", lambda self, text, **kw: text)
    # 20010a4c added a second barrier (a reply that speaks as the model is replaced); a regressed candidate lacks both.
    monkeypatch.setattr(rt, "reply_discloses_model", lambda *_a, **_k: False)
    res = PR.run_check("jeff_identity", tmp_path)
    assert not res["ok"] and "identity_redteam.leaks=" in res["detail"] and "leaks=0/" not in res["detail"]


def test_jeff_identity_passes_when_the_mandatory_filter_masks_a_leaky_model(tmp_path, monkeypatch):
    monkeypatch.setattr(PR, "_FakeModel", _Leaky)
    res = PR.run_check("jeff_identity", tmp_path)
    assert res["ok"] and "identity_redteam.leaks=0/" in res["detail"], res


def test_acceptance_runs_the_goal_tests_and_fails_closed(tmp_path):
    (tmp_path / "test_ok.py").write_text("def test_ok():\n    assert True\n", encoding="utf-8")
    (tmp_path / "test_bad.py").write_text("def test_bad():\n    assert False\n", encoding="utf-8")
    data = tmp_path / "data"
    assert PR.run_check("acceptance", data, {"cwd": tmp_path, "tests": ["test_ok.py"]})["ok"]
    assert not PR.run_check("acceptance", data, {"cwd": tmp_path, "tests": ["test_bad.py"]})["ok"]
    assert "fail closed" in PR.run_check("acceptance", data, {"tests": []})["detail"]


def test_unknown_or_crashing_checks_fail(tmp_path, monkeypatch):
    assert not PR.run_check("nope", tmp_path)["ok"]
    monkeypatch.setitem(PR.CHECKS, "memory", lambda d, o: 1 / 0)
    res = PR.run_check("memory", tmp_path)
    assert not res["ok"] and "ZeroDivisionError" in res["detail"]


def test_cli_prints_one_json_line(tmp_path, capsys):
    assert PR.main(["model_routing", "--data-dir", str(tmp_path)]) == 0
    assert json.loads(capsys.readouterr().out)["check"] == "model_routing"


def test_staged_probe_code_must_come_from_the_checkout(tmp_path, monkeypatch, capsys):
    checkout = tmp_path / "checkout"
    inside, installed = checkout / "command-center" / "bcc", tmp_path / "site-packages-tree" / "bcc"
    mods = {"bcc": SimpleNamespace(__file__=str(inside / "__init__.py")),
            "bcc.pit": SimpleNamespace(__file__=None, __path__=[str(inside / "pit")]),
            "json": SimpleNamespace(__file__=str(installed.parent / "json.py"))}
    assert PR.foreign_modules(checkout, mods) == []
    # an installed bcc filling in what the candidate lacks, the running probe itself, a module of unknown origin
    mods["bcc.autonomy"] = SimpleNamespace(__file__=str(installed / "autonomy" / "__init__.py"))
    mods["__main__"] = SimpleNamespace(__file__=str(installed / "autonomy" / "probes.py"),
                                       __spec__=SimpleNamespace(name="bcc.autonomy.probes"))
    mods["bcc.stub"] = SimpleNamespace()
    assert PR.foreign_modules(checkout, mods) == ["bcc.autonomy", "bcc.autonomy.probes", "bcc.stub"]
    # a staged run of code that is not the checkout's reports a failed check, never a pass
    monkeypatch.setenv(PR.STAGING_CHECKOUT_ENV, str(checkout))
    assert PR.main(["model_routing", "--data-dir", str(tmp_path / "data")]) == 1
    out = json.loads(capsys.readouterr().out)
    assert out["check"] == "model_routing" and not out["ok"] and "outside the staged checkout" in out["detail"]


def fake_runner(stdout: bytes, code: int = 0, seen: list | None = None, timeout: bool = False):
    def run(argv, **kw):
        if seen is not None:
            seen.append((argv, kw))
        if timeout:
            raise subprocess.TimeoutExpired(argv, 1)
        return SimpleNamespace(returncode=code, stdout=stdout, stderr=b"")
    return run


def test_candidate_probe_runs_inside_the_staged_checkout(tmp_path):
    checkout = tmp_path / "checkout"
    (checkout / "command-center").mkdir(parents=True)
    seen: list = []
    ok_line = json.dumps({"check": "memory", "ok": True, "detail": "fine"}).encode()
    probe = PR.candidate_probe("memory", runner=fake_runner(ok_line, seen=seen))
    ctx = {"data_dir": str(tmp_path / "stage-data"), "sha": "a" * 40}
    assert probe({"checkout": checkout}, ctx) == (True, "fine")
    argv, kw = seen[0]
    assert argv[1:4] == ["-m", "bcc.autonomy.probes", "memory"] and kw["cwd"] == str(checkout / "command-center")
    assert kw["env"]["BCC_DATA_DIR"] == ctx["data_dir"] and kw["env"]["BOSSMAN_STAGING"] == "1"
    assert kw["env"][PR.STAGING_CHECKOUT_ENV] == str(checkout)
    for bad in (b"garbage", json.dumps({"check": "other", "ok": True}).encode()):
        assert not PR.candidate_probe("memory", runner=fake_runner(bad))({"checkout": checkout}, ctx)[0]
    assert not PR.candidate_probe("memory", runner=fake_runner(ok_line, code=1))({"checkout": checkout}, ctx)[0]
    assert "timed out" in PR.candidate_probe("memory", runner=fake_runner(b"", timeout=True))(
        {"checkout": checkout}, ctx)[1]
    assert not PR.candidate_probe("memory", runner=fake_runner(ok_line))({"checkout": tmp_path / "none"}, ctx)[0]


def test_acceptance_probe_takes_tests_from_the_goal_of_the_staged_sha(tmp_path):
    root = tmp_path / "autonomy"
    store = GoalStore(root, Journal(root))
    goal = Goal(goal_id="DOC-1", problem="p", desired_result="d", constraints=("path:docs/**",),
                acceptance_tests=("pytest:command-center/tests/test_a.py",), budget=Budget(10, 3),
                risk_tier="docs_tests", target_metric="docs.errors", protected_metrics=("latency_ms",))
    store.create(goal)
    store.transition("DOC-1", "PLANNED", {})
    store.transition("DOC-1", "BUILDING", {})
    store.transition("DOC-1", "TESTING", {"sha": "b" * 40, "diff_sha256": "c" * 64, "writer": "codex"})
    checkout = tmp_path / "checkout"
    (checkout / "command-center").mkdir(parents=True)
    seen: list = []
    line = json.dumps({"check": "acceptance", "ok": True, "detail": "1 passed"}).encode()
    probes = PR.provider(root, tmp_path, runner=fake_runner(line, seen=seen))
    assert set(probes) == set(PR.STAGING_PROBES)
    assert probes["acceptance"]({"checkout": checkout}, {"data_dir": str(tmp_path / "d"), "sha": "b" * 40})[0]
    assert "--test=tests/test_a.py" in seen[0][0]
    ok, why = probes["acceptance"]({"checkout": checkout}, {"data_dir": str(tmp_path / "d"), "sha": "e" * 40})
    assert not ok and "fail closed" in why


def test_build_default_runner_registers_the_real_probes(tmp_path):
    owner = tmp_path / "owner"
    owner.mkdir()
    runner = staging.build_default_runner(tmp_path / "autonomy", tmp_path, owner_data_dir=owner)
    assert set(PR.STAGING_PROBES) <= set(runner.probes) and {"health", "ready"} <= set(runner.probes)
    explicit = staging.build_default_runner(tmp_path / "autonomy", tmp_path, owner_data_dir=owner, probes={})
    assert explicit.probes == {}
