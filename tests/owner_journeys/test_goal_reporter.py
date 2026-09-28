import asyncio
import json

import pytest

from tools.owner_journeys import goal_reporter as gr
from tools.owner_journeys import learning_supervisor as sup
from tools.owner_journeys import lesson_pipeline as lp
from tools.owner_journeys import owner_notify as on
from tools.owner_journeys import triage_dataset as ds

from .test_lesson_pipeline import FakeBackend, LESSON_ITEM, fake_ask
from .test_owner_notify import Clock, FakeTransport


def _cycles(tmp_path, t0, n=10, fail_every=5, tier="local", status="COMPLETED", start_id=1):
    rows = [{"cycle_id": start_id + i, "kind": "triage", "status": status, "started": t0 + i, "tier": tier,
             "verifier": {"pass": (i + 1) % fail_every != 0}, "route_tried": [], "cloud_usd": 0.0}
            for i in range(n)]
    with (tmp_path / "cycles.jsonl").open("a", encoding="utf-8") as fh:
        fh.write("".join(json.dumps(r) + "\n" for r in rows))
    st = {"mode": "RUNNING", "next_cycle_id": start_id + n, "session_started": t0}
    (tmp_path / "state.json").write_text(json.dumps(st))


def _reporter(tmp_path, clock, verdicts=None, transport=None):
    verdicts = verdicts if verdicts is not None else ["NOT_READY"]

    def readiness(_):
        v = verdicts[0] if len(verdicts) == 1 else verdicts.pop(0)
        return {"verdict": v, "criteria": {"unattended_hours": {"pass": v == "READY"}}}

    tr = transport or FakeTransport()
    goal = gr.load_goal(tmp_path)
    goal["readiness_every_s"] = 0
    n = on.Notifier(tmp_path, tr, on.Limits(min_interval_s=600), clock=clock)
    return gr.Reporter(tmp_path, n, goal, clock=clock, readiness_fn=readiness), tr


def test_goal_file_defaults_and_validation(tmp_path):
    g = gr.load_goal(tmp_path)
    assert (tmp_path / "goal.json").is_file() and g["report_every_h"] == 6
    raw = json.loads((tmp_path / "goal.json").read_text(encoding="utf-8"))
    raw["targets"]["triage_pass_rate_min"] = 0.95
    raw["report_every_h"] = 0.1
    (tmp_path / "goal.json").write_text(json.dumps(raw), encoding="utf-8")
    with pytest.raises(ValueError):
        gr.load_goal(tmp_path)
    raw["report_every_h"] = 12
    (tmp_path / "goal.json").write_text(json.dumps(raw), encoding="utf-8")
    g = gr.load_goal(tmp_path)
    assert g["targets"]["triage_pass_rate_min"] == 0.95 and g["targets"]["max_error_streak"] == 3


def test_first_report_then_every_6h_in_russian(tmp_path):
    clock = Clock()
    _cycles(tmp_path, clock.t - 100)
    rep, tr = _reporter(tmp_path, clock)
    res = rep.tick()
    assert res["reasons"] == ["start"] and res["delivery"][0]["status"] == "SENT"
    text = tr.sent[0]
    assert "Цель:" in text and "Разбор входящих: 80% из 10" in text and "Готовность 24/7: NOT_READY" in text
    assert "Расходы сегодня: $0.0000 из $0.50" in text and "Claude — 0" in text
    clock.t += 3600
    assert rep.tick()["queued"] is None and len(tr.sent) == 1
    clock.t += 5 * 3600 + 1
    _cycles(tmp_path, clock.t - 50, n=4, fail_every=100, start_id=11)
    res = rep.tick()
    assert res["reasons"] == ["schedule"] and "Разбор входящих: 100% из 4" in tr.sent[-1]


def test_state_changes_trigger_urgent_reports_once(tmp_path):
    clock = Clock()
    _cycles(tmp_path, clock.t - 100)
    rep, tr = _reporter(tmp_path, clock, verdicts=["NOT_READY", "READY", "READY", "READY", "READY"])
    rep.tick()
    clock.t += 60
    assert rep.tick()["reasons"] == ["readiness_flip"]
    # error streak
    _cycles(tmp_path, clock.t, n=3, status="ERROR", start_id=11)
    clock.t += 60
    assert rep.tick()["reasons"] == ["errors"]
    clock.t += 60
    assert rep.tick()["queued"] is None                 # reported once per streak
    # daily cap reached
    (tmp_path / "cloud_cap.json").write_text(json.dumps({
        sup.rl.CapLedger.day(): {"settled": 0.5, "reserved": 0.0, "cycles": {}}}))
    clock.t += 60
    res = rep.tick()
    assert "cap" in res["reasons"] and "лимит $ на сегодня исчерпан" in res["text"]
    clock.t += 60
    assert rep.tick()["queued"] is None
    clock.t += 60
    assert rep.tick("stop")["reasons"] == ["stop"]
    assert len(tr.sent) == 5


def test_offline_report_is_delivered_after_the_network_returns(tmp_path):
    clock = Clock()
    _cycles(tmp_path, clock.t - 100)
    tr = FakeTransport(results=[{"ok": False, "permanent": False, "error": "URLError"}])
    rep, _ = _reporter(tmp_path, clock, transport=tr)
    assert rep.tick()["delivery"][0]["status"] == "RETRY_LATER" and tr.sent == []
    clock.t += 120
    assert rep.tick()["delivery"][0]["status"] == "SENT" and len(tr.sent) == 1


def test_a_goal_text_with_a_secret_is_never_sent(tmp_path):
    clock = Clock()
    _cycles(tmp_path, clock.t - 100)
    rep, tr = _reporter(tmp_path, clock)
    rep.goal["goal"] = "use key sk-or-v1-0123456789abcdef"
    assert rep.tick()["queued"] == "REFUSED_SECRET" and tr.sent == []


# ------------------------------------------------------------------ supervisor integration

def _cfg(tmp_path, **kw):
    base = dict(state_dir=tmp_path, kinds=("fake",), poll_s=0.05, idle_between_cycles_s=0.0, min_free_gb=0.0,
                check_busy=False, report="off", approvals=None)
    base.update(kw)
    return sup.Config(**base)


def _fake(monkeypatch, name="fake", llm=True, pass_=True):
    async def run(cfg, work, index, *rest):
        await asyncio.sleep(0.01)
        return {"task": {"i": index}, "task_status": "completed", "verifier": {"pass": pass_, "safety_ok": True},
                "violations": [], "seconds": 0.01}
    monkeypatch.setitem(sup.RUNNERS, name, run)
    monkeypatch.setitem(sup.NEEDS_LLM, name, llm)
    monkeypatch.setattr(sup, "pause_reason", lambda cfg: None)


def test_owner_stop_aborts_the_cycle_and_holds_until_resume(tmp_path, monkeypatch):
    owner = tmp_path / "owner"
    (owner / "computer").mkdir(parents=True)
    state = tmp_path / "state"

    async def slow(cfg, work, index, *rest):
        (owner / "computer" / "STOP").write_text("x")
        await asyncio.sleep(30)
        return {}

    monkeypatch.setitem(sup.RUNNERS, "fake", slow)
    monkeypatch.setattr(sup, "pause_reason", lambda cfg: None)

    async def scenario():
        task = asyncio.create_task(sup.run(_cfg(state, owner_data_root=owner)))
        for _ in range(100):
            await asyncio.sleep(0.05)
            if sup.Store(state).state().get("mode") == "HALTED_BY_OWNER":
                break
        assert not task.done()
        rows, _ = sup.read_jsonl(state / "cycles.jsonl")
        assert rows[-1]["status"] == "ABORTED_BY_STOP"
        (state / "STOP").write_text("x")
        return await asyncio.wait_for(task, 10)

    st = asyncio.run(scenario())
    assert st["mode"] == "STOPPED"
    kinds = [json.loads(x)["kind"] for x in (state / "events.jsonl").read_text(encoding="utf-8").splitlines()]
    assert "owner_halt" in kinds


def test_owner_busy_runs_only_model_free_cycles(tmp_path, monkeypatch):
    _fake(monkeypatch, "llm", llm=True)
    _fake(monkeypatch, "det", llm=False)
    monkeypatch.setattr(sup, "busy_reason", lambda cfg: "owner_gpu_job:sd-cli.exe")
    asyncio.run(sup.run(_cfg(tmp_path, kinds=("llm", "det"), check_busy=True), max_cycles=4))
    rows, _ = sup.read_jsonl(tmp_path / "cycles.jsonl")
    assert [r["kind"] for r in rows] == ["det"] * 4
    ev = [json.loads(x) for x in (tmp_path / "events.jsonl").read_text(encoding="utf-8").splitlines()]
    assert any(e["kind"] == "owner_busy" and "sd-cli" in e["reason"] for e in ev)


def test_loop_runs_ab_and_asks_the_owner_but_never_promotes(tmp_path, monkeypatch):
    _fake(monkeypatch, "fake")
    monkeypatch.setattr(sup, "AB_ASK", fake_ask())
    (tmp_path / "goal.json").write_text(json.dumps({"ab": {"every_cycles": 1}}), encoding="utf-8")
    exp = {k: LESSON_ITEM[k] for k in ds.FIELDS}
    (tmp_path / "lesson_candidates.jsonl").write_text(json.dumps(
        {"cycle_id": 0, "kind": "triage", "task": {"text": LESSON_ITEM["text"]}, "expected": exp}) + "\n")
    be = FakeBackend()
    asyncio.run(sup.run(_cfg(tmp_path, approvals=be), max_cycles=3))
    rows, _ = sup.read_jsonl(tmp_path / "cycles.jsonl")
    ab = [r for r in rows if r["kind"] == "lesson_ab"]
    assert len(ab) == 1 and ab[0]["tier"] == "local" and ab[0]["cloud_usd"] == 0.0
    assert ab[0]["verifier"]["ab_verdict"] == "GAIN_PROVEN" and ab[0]["model_calls"] > 0
    reg = lp.Registry(tmp_path)
    les = next(iter(reg.read()["lessons"].values()))
    assert les["status"] == "APPROVAL_REQUESTED" and be.rows[1]["status"] == "pending"
    assert reg.active() == []
    sent = [json.loads(x) for x in (tmp_path / "notify" / "sent.jsonl").read_text(encoding="utf-8").splitlines()]
    assert any(s["key"] == "approval-" + les["id"] for s in sent)   # the owner was asked (transport off here)


def test_a_stale_owner_stop_from_before_the_first_run_does_not_block_learning(tmp_path, monkeypatch):
    import os
    owner = tmp_path / "owner"
    (owner / "computer").mkdir(parents=True)
    stop = owner / "computer" / "STOP"
    stop.write_text("old")
    os.utime(stop, (1_000_000, 1_000_000))
    _fake(monkeypatch, "fake")
    state = tmp_path / "state"
    asyncio.run(sup.run(_cfg(state, owner_data_root=owner), max_cycles=2))
    rows, _ = sup.read_jsonl(state / "cycles.jsonl")
    assert [r["status"] for r in rows] == ["COMPLETED", "COMPLETED"]
    stop.write_text("new owner stop")             # a fresh STOP (new mtime) is honored, also after restart
    cfg = _cfg(state, owner_data_root=owner)      # simulate a restart: run() reloads the durable baseline
    cfg.owner_stop_baseline = sup.Store(state).state()["owner_stop_baseline"]
    assert sup.owner_halt(cfg) == "owner_computer_stop"
