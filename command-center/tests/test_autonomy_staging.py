"""Staging runner: separate port + temp data, never the live Bossman or its data, always torn down."""
from __future__ import annotations

from pathlib import Path

import pytest

from bcc.autonomy.journal import Journal
from bcc.autonomy.staging import STANDARD_CHECKS, StagingRunner

SHA = "c" * 40


class FakeLauncher:
    def __init__(self, fail_start=False, fail_stop=False):
        self.started, self.stopped = [], []
        self.fail_start, self.fail_stop = fail_start, fail_stop

    def start(self, sha, port, data_dir, env):
        if self.fail_start:
            raise RuntimeError("port in use")
        assert Path(data_dir).is_dir()
        (Path(data_dir) / "bcc.db").write_text("candidate data")
        self.started.append({"sha": sha, "port": port, "data_dir": Path(data_dir), "env": dict(env)})
        return {"port": port}

    def stop(self, handle):
        self.stopped.append(handle)
        if self.fail_stop:
            raise RuntimeError("zombie")


def probes(fail=(), boom=()):
    def make(name):
        def probe(handle, ctx):
            if name in boom:
                raise ValueError("probe crashed")
            return (name not in fail, f"{name} checked on {ctx['base_url']}")
        return probe
    return {n: make(n) for n in STANDARD_CHECKS}


@pytest.fixture
def owner(tmp_path):
    d = tmp_path / "owner-data"
    d.mkdir()
    (d / "bcc.db").write_text("OWNER")
    return d


def runner(tmp_path, owner, launcher=None, **kw):
    kw.setdefault("tmp_root", tmp_path / "staging")
    return StagingRunner(launcher or FakeLauncher(), kw.pop("probes", probes()), owner_data_dir=owner,
                         journal=Journal(tmp_path / "j"), **kw)


def test_all_checks_pass_on_separate_port_and_temp_dir(tmp_path, owner):
    launcher = FakeLauncher()
    rep = runner(tmp_path, owner, launcher, port_finder=lambda: 45123).run(SHA)
    assert rep.passed and rep.ok and rep.torn_down and rep.port == 45123
    assert list(rep.checks) == list(STANDARD_CHECKS) and all(c["ok"] for c in rep.checks.values())
    s = launcher.started[0]
    assert s["env"]["BCC_DATA_DIR"] == str(s["data_dir"]) and s["env"]["BCC_PORT"] == "45123"
    assert not s["data_dir"].exists()                                  # temp dir removed
    assert launcher.stopped == [{"port": 45123}]
    assert (owner / "bcc.db").read_text() == "OWNER"                  # owner data untouched
    kinds = [e["kind"] for e in Journal(tmp_path / "j").entries()]
    assert kinds == ["staging.started", "staging.finished"]


def test_live_ports_are_never_used(tmp_path, owner):
    ports = iter([8801, 8800, 45000])
    rep = runner(tmp_path, owner, port_finder=lambda: next(ports)).run(SHA)
    assert rep.passed and rep.port == 45000
    rep = runner(tmp_path, owner, port_finder=lambda: 8801).run(SHA)
    assert not rep.passed and "live Bossman port" in rep.reason


@pytest.mark.parametrize("where", ["inside", "same", "above"])
def test_owner_data_path_is_refused(tmp_path, owner, where):
    launcher = FakeLauncher()
    root = {"inside": owner / "staging", "same": owner, "above": owner.parent}[where]
    rep = runner(tmp_path, owner, launcher, tmp_root=root).run(SHA)
    assert not rep.passed and "owner data dir" in rep.reason
    assert launcher.started == []
    assert sorted(p.name for p in owner.iterdir()) == ["bcc.db"] and (owner / "bcc.db").read_text() == "OWNER"


def test_failing_or_crashing_probe_fails_but_tears_down(tmp_path, owner):
    launcher = FakeLauncher()
    rep = runner(tmp_path, owner, launcher, probes=probes(fail=("memory",), boom=("voice_status",))).run(SHA)
    assert not rep.passed and rep.torn_down and "memory" in rep.reason and "voice_status" in rep.reason
    assert "probe raised ValueError" in rep.checks["voice_status"]["detail"] and not rep.ok
    assert launcher.stopped and not launcher.started[0]["data_dir"].exists()


def test_unknown_check_is_refused_before_launch(tmp_path, owner):
    launcher = FakeLauncher()
    rep = runner(tmp_path, owner, launcher).run(SHA, ["memory", "vibes"])
    assert not rep.passed and "vibes" in rep.reason and launcher.started == []


def test_start_failure_and_teardown_failure(tmp_path, owner):
    rep = runner(tmp_path, owner, FakeLauncher(fail_start=True)).run(SHA)
    assert not rep.passed and "did not start" in rep.reason and rep.checks == {}
    rep = runner(tmp_path, owner, FakeLauncher(fail_stop=True)).run(SHA)
    assert not rep.passed and not rep.torn_down and "teardown failed" in rep.reason


def test_short_sha_is_refused(tmp_path, owner):
    rep = runner(tmp_path, owner).run("abc123")
    assert not rep.passed and "full immutable commit SHA" in rep.reason


def test_report_dict_is_json_ready(tmp_path, owner):
    import json
    d = runner(tmp_path, owner).run(SHA).as_dict()
    assert json.loads(json.dumps(d))["checks"]["memory"]["ok"] is True and d["ok"] is True
