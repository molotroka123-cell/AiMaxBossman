"""Branch 9 (agents): the owner's STOP / cancel is honoured BETWEEN every step of an operator task, not only at
the start of an attempt. Offline: the planner, the coder and the cloud are the fakes of test_qwen_operator.py."""
from __future__ import annotations

import json
from pathlib import Path

from bcc.features import qwen_operator as qo

from .test_qwen_operator import PLAN, Fakes, git, repo, stack  # noqa: F401  (fixtures)


def _saved(svc, rid):
    return json.loads((Path(svc.settings.data_dir) / "qwen-operator" / f"{rid}.json").read_text("utf-8"))


def _stop_file(svc) -> Path:
    stop = Path(svc.settings.data_dir) / "computer" / "STOP"
    stop.parent.mkdir(parents=True, exist_ok=True)
    return stop


async def _run(svc, repo, f, **kw):
    rec = await qo.start_task(svc, "улучши докстринг", source_repo=str(repo), decision_wait_s=0,
                              local_chat=kw.get("local_chat", f.local_chat), free_cloud=f.free_cloud,
                              coder=kw.get("coder", f.coder))
    await qo._JOBS[rec["id"]]
    return _saved(svc, rec["id"])


def _lab_untouched(saved):
    lab = Path(saved["lab"]["path"])
    assert git(lab, "rev-parse", "HEAD") == saved["lab"]["base_commit"]
    assert git(lab, "status", "--porcelain") == ""


async def test_stop_pressed_while_the_coder_runs_applies_and_commits_nothing(stack, repo):  # noqa: F811
    svc, _c, _a = stack
    f = Fakes(repo, PLAN)
    stop = _stop_file(svc)

    async def coder(run, instruction, paths, tests, attempt):
        out = await f.coder(run, instruction, paths, tests, attempt)
        stop.write_text("{}", encoding="utf-8")          # the owner pressed STOP during the (long) coder call
        return out
    try:
        saved = await _run(svc, repo, f, coder=coder)
    finally:
        stop.unlink(missing_ok=True)
    assert saved["status"] == "stopped", saved.get("error")
    assert not saved.get("commit_sha") and not saved.get("diff")
    assert not any(c.get("why") == "mandatory verification" for c in saved["commands"])   # no lab test run
    assert not saved["decisions"]                                                        # no release asked
    _lab_untouched(saved)


async def test_stop_pressed_during_the_lab_tests_leaves_no_commit(stack, repo, monkeypatch):  # noqa: F811
    svc, _c, _a = stack
    f = Fakes(repo, PLAN)
    stop = _stop_file(svc)
    real = qo.run_tests_in_lab

    def tests_then_stop(scope, tests):
        out = real(scope, tests)
        stop.write_text("{}", encoding="utf-8")
        return out
    monkeypatch.setattr(qo, "run_tests_in_lab", tests_then_stop)
    try:
        saved = await _run(svc, repo, f)
    finally:
        stop.unlink(missing_ok=True)
    assert saved["status"] == "stopped", saved.get("error")
    assert not saved.get("commit_sha")
    _lab_untouched(saved)


async def test_stop_pressed_while_the_planner_runs_starts_no_further_step(stack, repo):  # noqa: F811
    svc, _c, _a = stack
    f = Fakes(repo, PLAN)
    stop = _stop_file(svc)

    async def planner_then_stop(model, messages, **kw):
        out = await f.local_chat(model, messages, **kw)
        if kw.get("json_mode"):
            stop.write_text("{}", encoding="utf-8")
        return out
    try:
        saved = await _run(svc, repo, f, local_chat=planner_then_stop)
    finally:
        stop.unlink(missing_ok=True)
    assert saved["status"] == "stopped"
    assert f.coder_calls == 0 and not saved.get("worker_draft")
