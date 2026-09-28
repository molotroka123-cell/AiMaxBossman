import asyncio
import json
import os

import pytest

from tools.owner_journeys import learning_247_readiness as rd
from tools.owner_journeys import learning_supervisor as sup


def _cfg(tmp_path, **kw):
    base = dict(state_dir=tmp_path, kinds=("fake",), poll_s=0.05, idle_between_cycles_s=0.0, min_free_gb=0.0,
                check_busy=False, report="off", approvals=None)
    base.update(kw)
    return sup.Config(**base)


@pytest.fixture
def fake_runner(monkeypatch):
    calls = []

    async def fake(cfg, work, index, *rest):
        calls.append(index)
        await asyncio.sleep(0.01)
        return {"task": {"i": index}, "task_status": "completed",
                "verifier": {"pass": index % 2 == 0, "safety_ok": True}, "expected": {}, "got": {},
                "violations": [], "seconds": 0.01}

    monkeypatch.setitem(sup.RUNNERS, "fake", fake)
    monkeypatch.setattr(sup, "pause_reason", lambda cfg: None)
    return calls


def test_config_refuses_cloud_unless_free_and_allowed(tmp_path):
    with pytest.raises(ValueError):
        _cfg(tmp_path, base_url="https://openrouter.ai/api/v1").validate()
    with pytest.raises(ValueError):
        _cfg(tmp_path, base_url="https://openrouter.ai/api/v1", allow_free_cloud=True, model="paid-model").validate()
    _cfg(tmp_path, base_url="https://openrouter.ai/api/v1", allow_free_cloud=True, model="x/y:free").validate()
    _cfg(tmp_path).validate()


def test_cycles_are_recorded_and_failures_quarantined(tmp_path, fake_runner):
    st = asyncio.run(sup.run(_cfg(tmp_path), max_cycles=4))
    rows, bad = sup.read_jsonl(tmp_path / "cycles.jsonl")
    assert bad == 0 and [r["cycle_id"] for r in rows] == [1, 2, 3, 4]
    assert all(r["status"] == "COMPLETED" and r["cloud_usd"] == 0.0 for r in rows)
    lessons, _ = sup.read_jsonl(tmp_path / "lesson_candidates.jsonl")
    assert len(lessons) == 2 and all(x["status"] == "CANDIDATE_QUARANTINED" for x in lessons)
    assert st["next_cycle_id"] == 5 and not (tmp_path / "supervisor.lock").exists()


def test_stop_file_exits_and_abandoned_cycle_recovered_once(tmp_path, fake_runner):
    store = sup.Store(tmp_path)
    store.save({"in_progress": {"cycle_id": 7, "kind": "fake", "started": 1.0}, "next_cycle_id": 7,
                "cursor": {}})
    (tmp_path / "STOP").write_text("x")
    st = asyncio.run(sup.run(_cfg(tmp_path)))
    assert st["mode"] == "STOPPED"
    rows, _ = sup.read_jsonl(tmp_path / "cycles.jsonl")
    assert [(r["cycle_id"], r["status"]) for r in rows] == [(7, "ABANDONED_ON_RESTART")]
    (tmp_path / "STOP").unlink()
    asyncio.run(sup.run(_cfg(tmp_path), max_cycles=1))
    rows, _ = sup.read_jsonl(tmp_path / "cycles.jsonl")
    assert [r["cycle_id"] for r in rows] == [7, 8]


def test_stop_aborts_a_running_cycle(tmp_path, monkeypatch):
    async def slow(cfg, work, index, *rest):
        (cfg.state_dir / "STOP").write_text("x")
        await asyncio.sleep(30)
        return {}

    monkeypatch.setitem(sup.RUNNERS, "fake", slow)
    monkeypatch.setattr(sup, "pause_reason", lambda cfg: None)
    st = asyncio.run(sup.run(_cfg(tmp_path)))
    rows, _ = sup.read_jsonl(tmp_path / "cycles.jsonl")
    assert rows[-1]["status"] == "ABORTED_BY_STOP" and st["mode"] == "STOPPED"


def test_lock_refuses_second_live_instance(tmp_path):
    (tmp_path / "supervisor.lock").write_text(f"{os.getppid()} 0\n")
    with pytest.raises(RuntimeError):
        sup.Lock(tmp_path / "supervisor.lock").acquire()
    (tmp_path / "supervisor.lock").write_text("999999 0\n")  # dead pid -> stale lock is taken over
    sup.Lock(tmp_path / "supervisor.lock").acquire()


def test_budget_exhaustion_blocks_new_cycles(tmp_path, fake_runner):
    st = asyncio.run(sup.run(_cfg(tmp_path, max_cycles_day=2), max_cycles=2))
    assert st["budget"][sup._today()]["cycles"] == 2
    cfg = _cfg(tmp_path, max_cycles_day=2)

    async def bounded():
        task = asyncio.create_task(sup.run(cfg))
        await asyncio.sleep(0.5)
        (tmp_path / "STOP").write_text("x")
        return await task
    st = asyncio.run(bounded())
    rows, _ = sup.read_jsonl(tmp_path / "cycles.jsonl")
    assert len(rows) == 2


def test_readiness_verdict_from_recorded_state(tmp_path):
    cycles = [{"cycle_id": i, "kind": "triage", "status": "COMPLETED", "violations": [], "cloud_usd": 0.0,
               "verifier": {"pass": True, "safety_ok": True}, "rss_mb": 300, "tier": "local",
               "anthropic_attempts_total": 0, "started": 1790000000} for i in range(1, 14)]
    (tmp_path / "cycles.jsonl").write_text("".join(json.dumps(c) + "\n" for c in cycles))
    (tmp_path / "events.jsonl").write_text(json.dumps({"ts": 0, "kind": "session_start"}) + "\n"
                                           + json.dumps({"ts": 4000, "kind": "session_end"}) + "\n")
    (tmp_path / "readiness_tests.json").write_text(json.dumps({"pause_s": 12, "stop_s": 3, "restart_safe": True, "cap_fake_test": True,
                                                                "free_call": {"tier": "free_cloud", "status": "COMPLETED",
                                                                              "usd": 0.0}}))
    ab = tmp_path / "ab.json"
    ab.write_text(json.dumps({"repeats": [{"baseline": {"accuracy": 0.8}}]}))
    rep = rd.evaluate(tmp_path, ab_report=ab)
    assert rep["verdict"] == "READY", rep["criteria"]
    cycles[3]["violations"] = ["executed_outside_allowlist:x"]
    (tmp_path / "cycles.jsonl").write_text("".join(json.dumps(c) + "\n" for c in cycles))
    assert rd.evaluate(tmp_path, ab_report=ab)["verdict"] == "NOT_READY"
    cycles[3]["violations"] = []
    cycles[5]["anthropic_attempts_total"] = 1   # any attempted Anthropic call blocks readiness
    (tmp_path / "cycles.jsonl").write_text("".join(json.dumps(c) + "\n" for c in cycles))
    assert rd.evaluate(tmp_path, ab_report=ab)["criteria"]["claude_free_cycles"]["pass"] is False
