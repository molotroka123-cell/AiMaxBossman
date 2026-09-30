"""Bounded supervisor (`bossman autonomy run`): hard limits, preflight, heartbeat, owner gate, bounded backoff, STOP.

Fake cycle objects stand in for the workers; one test drives the REAL cycle over the real control plane with fake CLIs."""
from __future__ import annotations

import json
import subprocess
import sys
from types import SimpleNamespace

import pytest

from .autonomy_fakes import OK_CONSTITUTION, kinds, make_goal
from .test_autonomy_bounded_loop import build, world  # noqa: F401  (the fixture and the real-cycle builder)
from bcc.autonomy import stop as stopmod
from bcc.autonomy.cycle import CycleOutcome
from bcc.autonomy.service import AutonomyService
from bcc.autonomy.supervisor import EXIT_BY_STATUS, HARD_MAX_CYCLES, Supervisor, SupervisorConfig


class Clock:
    def __init__(self):
        self.t = 1_000_000.0

    def __call__(self):
        return self.t


class FakeCycle:
    """Plays the AutonomyCycle: `script[goal_id]` is a list of CycleOutcome states or exceptions, consumed in order."""

    def __init__(self, svc: AutonomyService, script: dict | None = None):
        self.svc, self.script = svc, script or {}
        self.calls: list[tuple[str, str]] = []

    def _next(self, action: str, gid: str):
        self.calls.append((action, gid))
        steps = self.script.get(gid) or ["COMPLETE"]
        step = steps.pop(0) if len(steps) > 1 else steps[0]
        if isinstance(step, Exception):
            raise step
        state, _, reason = str(step).partition("|")
        extra = {"blocked_reason": reason, "blocked_from": "BUILDING"} if state == "BLOCKED" else (
            {"outcome": "accepted"} if state == "COMPLETE" else {})
        set_state(self.svc, gid, state, **extra)              # leave the goal where the real cycle would leave it
        return CycleOutcome(gid, state, reason)

    async def run_goal(self, goal):
        return self._next("run", goal.goal_id)

    async def resume(self, gid):
        return self._next("resume", gid)

    async def resume_after_stop(self, gid):
        return self._next("resume_after_stop", gid)


def set_state(svc: AutonomyService, gid: str, state: str, **over) -> None:
    path = svc.root / "goals" / f"{gid}.json"
    rec = json.loads(path.read_text(encoding="utf-8"))
    rec["state"] = state
    rec.update(over)
    path.write_text(json.dumps(rec), encoding="utf-8")


@pytest.fixture
def svc(tmp_path):
    return AutonomyService(tmp_path / "data", constitution_status=lambda: OK_CONSTITUTION)


def sup(svc, cycle=None, *, clock=None, sleeps=None, **cfg):
    clock = clock or Clock()
    sleeps = sleeps if sleeps is not None else []

    async def sleep(seconds):
        sleeps.append(seconds)
        clock.t += seconds

    made = []

    def make_cycle():
        made.append(1)
        return cycle

    s = Supervisor(svc, make_cycle, SupervisorConfig(poll_s=1.0, heartbeat_s=3600, **cfg), clock=clock, sleep=sleep)
    s.made = made
    return s


def heartbeat(svc) -> dict:
    return json.loads((svc.root / "heartbeat.json").read_text(encoding="utf-8"))


async def test_defaults_are_one_cycle_two_hours_and_limits_are_mandatory_and_capped(svc):
    c = SupervisorConfig()
    assert (c.max_cycles, c.max_hours, c.interval_s, c.dry) == (1, 2.0, 0.0, False)
    for bad in ({"max_cycles": 0}, {"max_cycles": HARD_MAX_CYCLES + 1}, {"max_hours": 0}, {"max_hours": 48},
                {"interval_s": -1}):
        report = await sup(svc, FakeCycle(svc), **bad).run()
        assert report.status == "REFUSED" and "--max-" in report.reason or "--interval" in report.reason
    assert EXIT_BY_STATUS["REFUSED"] == 5 and EXIT_BY_STATUS["STOPPED"] == 6


async def test_refuses_without_a_pinned_constitution_and_starts_no_cycle(tmp_path):
    blocked = AutonomyService(tmp_path / "d", constitution_status=lambda: SimpleNamespace(ok=False, reason="not pinned"))
    blocked.goals.create(make_goal("SU-1"))
    s = sup(blocked, FakeCycle(blocked))
    report = await s.run()
    assert report.status == "BLOCKED" and "constitution" in report.reason and s.made == []
    assert heartbeat(blocked)["status"] == "BLOCKED"


async def test_stop_before_start_and_instant_stop_while_waiting(svc):
    svc.goals.create(make_goal("SU-2"))
    stopmod.request_stop(svc.root, by="t", reason="halt")
    s = sup(svc, FakeCycle(svc))
    assert (await s.run()).status == "STOPPED" and s.made == []
    stopmod.clear_stop(svc.root)
    # idle + waiting: the STOP appears while the supervisor naps and ends it within one poll
    napped = []
    idle = AutonomyService(svc.data_dir.parent / "idle", constitution_status=lambda: OK_CONSTITUTION)

    async def sleep(seconds):
        napped.append(seconds)
        if len(napped) == 2:
            stopmod.request_stop(idle.root, by="t", reason="stop now")

    s2 = Supervisor(idle, lambda: None, SupervisorConfig(interval_s=600, poll_s=1.0, max_hours=3), sleep=sleep)
    report = await s2.run()
    assert report.status == "STOPPED" and len(napped) == 2 and max(napped) <= 1.0     # slices, never one long sleep
    assert heartbeat(idle)["status"] == "STOPPED"


async def test_one_cycle_runs_heartbeats_journals_and_stops_at_the_owner_gate(svc):
    svc.goals.create(make_goal("SU-3"))
    cycle = FakeCycle(svc, {"SU-3": ["USER_APPROVAL"]})
    report = await sup(svc, cycle).run()
    assert report.status == "WAITING_OWNER" and report.cycles == 1 and cycle.calls == [("run", "SU-3")]
    hb = heartbeat(svc)
    assert hb["status"] == "WAITING_OWNER" and hb["cycles"] == 1 and hb["max_cycles"] == 1 and hb["pid"]
    assert hb["weights"] == "WEIGHTS_UNCHANGED" and hb["goal_id"] == "SU-3" and "finished_at" in hb
    seen = kinds(svc.journal)
    assert [k for k in seen if k.startswith("supervisor.")] == ["supervisor.started", "supervisor.pass",
                                                                "supervisor.pass_finished", "supervisor.finished"]
    assert svc.journal.verify().ok and svc.status()["heartbeat"]["status"] == "WAITING_OWNER"
    # the next supervisor run starts nothing while the owner has not decided
    assert svc.goals.get("SU-3")["state"] == "USER_APPROVAL"
    again = await sup(svc, cycle).run()
    assert again.status == "WAITING_OWNER" and cycle.calls == [("run", "SU-3")]


async def test_dry_run_shows_what_would_run_and_starts_nothing(svc):
    svc.goals.create(make_goal("SU-4"))
    s = sup(svc, FakeCycle(svc), dry=True)
    report = await s.run()
    assert report.status == "DRY" and "SU-4" in report.reason and s.made == []
    assert kinds(svc.journal).count("supervisor.dry") == 1 and svc.goals.get("SU-4")["state"] == "PROPOSED"


async def test_max_cycles_bounds_the_run_and_leaves_the_rest_untouched(svc):
    for i in (5, 6, 7):
        svc.goals.create(make_goal(f"SU-{i}"))
    cycle = FakeCycle(svc)
    report = await sup(svc, cycle, max_cycles=2).run()
    assert report.status == "FINISHED" and "max cycles" in report.reason and report.cycles == 2
    assert [g for _, g in cycle.calls] == ["SU-5", "SU-6"] and svc.goals.get("SU-7")["state"] == "PROPOSED"


async def test_nothing_to_run_is_idle_not_an_error(svc):
    report = await sup(svc, FakeCycle(svc)).run()
    assert report.status == "IDLE" and "no goal" in report.reason and EXIT_BY_STATUS["IDLE"] == 0


async def test_max_hours_bounds_a_waiting_supervisor(svc):
    clock, sleeps = Clock(), []
    report = await sup(svc, FakeCycle(svc), clock=clock, sleeps=sleeps, interval_s=60.0, max_hours=0.05).run()
    assert report.status == "FINISHED" and "max hours" in report.reason
    assert sum(sleeps) >= 0.05 * 3600 and max(sleeps) <= 1.0 and heartbeat(svc)["status"] == "FINISHED"


async def test_a_failing_pass_backs_off_exponentially_and_stops_after_three_errors(svc):
    svc.goals.create(make_goal("SU-8"))
    cycle = FakeCycle(svc, {"SU-8": [RuntimeError("boom-1"), RuntimeError("boom-2"), RuntimeError("boom-3")]})
    clock, sleeps = Clock(), []
    report = await sup(svc, cycle, clock=clock, sleeps=sleeps, max_cycles=5, backoff_base_s=5.0).run()
    assert report.status == "ERRORS" and "3 consecutive errors" in report.reason and "boom-3" in report.reason
    assert len(cycle.calls) == 3                                   # not an endless loop of errors
    assert sum(sleeps) == pytest.approx(5.0 + 10.0) and sleeps[0] == 1.0          # 5 s then 10 s, in 1 s slices
    assert kinds(svc.journal).count("supervisor.error") == 3
    assert EXIT_BY_STATUS["ERRORS"] == 1


async def test_a_success_resets_the_error_streak(svc):
    svc.goals.create(make_goal("SU-9"))
    cycle = FakeCycle(svc, {"SU-9": [RuntimeError("once"), "COMPLETE"]})
    report = await sup(svc, cycle, max_cycles=3).run()
    assert report.cycles == 1 and report.status in ("FINISHED", "IDLE") and len(cycle.calls) == 2


async def test_blocked_goal_stops_the_supervisor_and_an_owner_stop_is_reported_as_stopped(svc):
    svc.goals.create(make_goal("SU-10"))
    report = await sup(svc, FakeCycle(svc, {"SU-10": ["BLOCKED|protected tests fail: acceptance"]})).run()
    assert report.status == "BLOCKED" and "protected tests fail" in report.reason
    svc.goals.create(make_goal("SU-11"))
    report = await sup(svc, FakeCycle(svc, {"SU-11": ["BLOCKED|owner STOP: autonomy STOP"]})).run()
    assert report.status == "STOPPED" and EXIT_BY_STATUS[report.status] == 6


async def test_lease_busy_daily_cap_and_a_second_supervisor_block_the_start(svc):
    svc.goals.create(make_goal("SU-12"))
    token = svc.lease.acquire("OTHER-1", "someone-else", 600)
    report = await sup(svc, FakeCycle(svc)).run()
    assert report.status == "BLOCKED" and "engineering lease" in report.reason
    svc.lease.release(token)
    (svc.root / "budget.json").write_text(json.dumps({"cycles_per_day": 0}), encoding="utf-8")
    report = await sup(svc, FakeCycle(svc)).run()
    assert report.status == "BLOCKED" and "cycles/day cap" in report.reason
    (svc.root / "budget.json").unlink()
    other = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(120)"])
    try:
        clock = Clock()
        (svc.root / "heartbeat.json").write_text(json.dumps({"pid": other.pid, "status": "RUNNING", "at": clock.t}),
                                                 encoding="utf-8")
        report = await sup(svc, FakeCycle(svc), clock=clock).run()
        assert report.status == "REFUSED" and "another supervisor" in report.reason
    finally:
        other.kill()


async def test_mid_cycle_goals_resume_and_a_stop_blocked_goal_resumes_after_the_stop_is_cleared(svc):
    svc.goals.create(make_goal("SU-13"))
    set_state(svc, "SU-13", "CODEX_REVIEW")
    cycle = FakeCycle(svc, {"SU-13": ["COMPLETE"]})
    await sup(svc, cycle).run()
    assert cycle.calls == [("resume", "SU-13")]
    svc.goals.create(make_goal("SU-14"))
    set_state(svc, "SU-14", "BLOCKED", blocked_reason="owner STOP: autonomy STOP (halt)", blocked_from="BUILDING")
    cycle = FakeCycle(svc, {"SU-14": ["COMPLETE"]})
    await sup(svc, cycle).run()
    assert cycle.calls == [("resume_after_stop", "SU-14")]
    svc.goals.create(make_goal("SU-15"))
    set_state(svc, "SU-15", "BLOCKED", blocked_reason="staged evaluation rejected the candidate", blocked_from="STAGING")
    cycle = FakeCycle(svc)
    assert (await sup(svc, cycle).run()).status == "IDLE" and cycle.calls == []     # other blocks need the owner


async def test_the_real_cycle_runs_under_the_supervisor_and_stops_at_user_approval(world):  # noqa: F811
    svc = AutonomyService(world["data"], constitution_status=lambda: OK_CONSTITUTION)
    svc.goals.create(make_goal("SU-16"))
    report = await Supervisor(svc, lambda: build(world, daily=True, experience=True, skills=True),
                              SupervisorConfig(max_cycles=1)).run()
    assert report.status == "WAITING_OWNER" and report.passes[0]["state"] == "USER_APPROVAL"
    assert svc.goals.get("SU-16")["state"] == "USER_APPROVAL" and svc.lease.peek() is None
    assert heartbeat(svc)["status"] == "WAITING_OWNER" and svc.journal.verify().ok
    assert svc.budget.usage().cycles == 1 and svc.promotion_eligibility()["clean_cycles"] == 0
    # STOP during the next run: nothing new starts, the supervisor reports STOPPED
    svc.goals.create(make_goal("SU-17"))
    set_state(svc, "SU-16", "COMPLETE", outcome="accepted")
    stopmod.request_stop(svc.root, by="t", reason="halt")
    stopped = await Supervisor(svc, lambda: build(world), SupervisorConfig()).run()
    assert stopped.status == "STOPPED" and svc.goals.get("SU-17")["state"] == "PROPOSED"


def cli(capsys, *argv):
    from bcc.terminal_cli.cli import main
    code = main(["autonomy", *argv])
    return code, capsys.readouterr().out


def test_cli_run_refuses_unpinned_and_dry_runs_pinned(tmp_path, capsys):
    import hashlib
    doc = tmp_path / "repo" / "C.md"
    doc.parent.mkdir()
    doc.write_text("# rules\n", encoding="utf-8")
    pin = tmp_path / "local" / "constitution.sha256"
    data = tmp_path / "data"
    base = ["--data-dir", str(data), "--constitution", str(doc), "--pin-path", str(pin), "--json"]
    AutonomyService(data).goals.create(make_goal("SU-18"))
    code, out = cli(capsys, "run", "--dry", *base)
    assert code == 5 and json.loads(out)["status"] == "BLOCKED"                      # no owner pin: nothing runs
    pin.parent.mkdir(parents=True)
    pin.write_text(hashlib.sha256(doc.read_bytes()).hexdigest() + "\n")             # a TEST pin in a temp dir
    code, out = cli(capsys, "run", "--dry", "--max-cycles", "1", "--max-hours", "1", *base)
    body = json.loads(out)
    assert code == 0 and body["status"] == "DRY" and "SU-18" in body["reason"] and body["weights"] == "WEIGHTS_UNCHANGED"
    assert (data / "autonomy" / "heartbeat.json").is_file()
    code, out = cli(capsys, "run", "--max-cycles", "99", *base)
    assert code == 5 and json.loads(out)["status"] == "REFUSED"
