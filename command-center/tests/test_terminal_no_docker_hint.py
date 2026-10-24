"""terminal.run tells the model up front when docker is absent (function probe 24.10).

Without docker every terminal task first called mode=sandbox - an owner approval for a call that could only fail -
and only then retried with project_host. The hint changes the description only: SPECS, the effect hook (sandbox
read/build AUTO, project_host ASK) and approvals stay exactly as they were.
"""
from __future__ import annotations

from bcc.features import tools_terminal as tt


def _run_spec():
    return next(s for s in tt.SPECS if s.name == "terminal.run")


def test_no_docker_adds_the_hint_once_and_keeps_the_policy(monkeypatch):
    monkeypatch.setattr(tt.shutil, "which", lambda name: None)
    spec = tt._with_docker_hint(_run_spec())
    assert tt.NO_DOCKER_HINT in spec.description and spec.description.count("docker не установлен") == 1
    assert tt._with_docker_hint(spec).description == spec.description, "idempotent"
    assert spec.effect_hook is _run_spec().effect_hook and spec.default_effect == "ask"
    assert tt.NO_DOCKER_HINT not in _run_spec().description, "the module-level SPECS are not mutated"
    assert tt._run_effect({"command": "git status", "mode": "project_host"})[0] == "ask"


def test_with_docker_nothing_changes(monkeypatch):
    monkeypatch.setattr(tt.shutil, "which", lambda name: r"C:\docker\docker.exe")
    assert tt._with_docker_hint(_run_spec()) is _run_spec()
    other = next(s for s in tt.SPECS if s.name != "terminal.run")
    monkeypatch.setattr(tt.shutil, "which", lambda name: None)
    assert tt._with_docker_hint(other) is other
