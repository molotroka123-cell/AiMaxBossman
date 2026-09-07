"""AT-02: durable owner state survives recovery, including fresh interpreters."""
import asyncio
import json
import subprocess
import sys

import pytest
from bossman.computer_operator.models import ActionKind, ComputerAction, ExpectedState, TaskState
from bossman.computer_operator.wiring import FakeAdapter, FakeObserver, FakePlanner, make_manager


def manager(path):
    return make_manager(path, FakePlanner([ComputerAction.make(ActionKind.CLICK,
                expected=ExpectedState(contains_text='ok'))]), FakeObserver(summary='ok'), adapter=FakeAdapter())


@pytest.mark.parametrize('owner_state', [TaskState.PAUSED, TaskState.USER_CONTROL, TaskState.CANCELLED, TaskState.LOCKED])
def test_owner_state_survives_recovery_in_fresh_process(tmp_path, owner_state):
    path = tmp_path / 'tasks.json'
    mgr = manager(path)
    task = mgr.create_task('do not resume without owner')
    task.state = owner_state
    mgr.store.save(task)
    script = '''import asyncio,json,sys
from bossman.computer_operator.wiring import make_manager,FakePlanner,FakeObserver,FakeAdapter
from bossman.computer_operator.models import ComputerAction,ActionKind,ExpectedState
adapter=FakeAdapter()
m=make_manager(sys.argv[1],FakePlanner([ComputerAction.make(ActionKind.CLICK,expected=ExpectedState(contains_text="ok"))]),FakeObserver(summary="ok"),adapter=adapter)
m.recover_all()
result=asyncio.run(m.run(sys.argv[2]))
print(json.dumps({"state":result.value,"effects":len(adapter.executed)}))
'''
    process = subprocess.run([sys.executable, '-c', script, str(path), task.id],
                             capture_output=True, text=True, timeout=20)
    assert process.returncode == 0, process.stderr
    assert json.loads(process.stdout) == {'state': owner_state.value, 'effects': 0}


def test_waiting_approval_is_parked_for_explicit_resume_and_new_approval(tmp_path):
    mgr = manager(tmp_path / 'tasks.json')
    task = mgr.create_task('payment fixture')
    task.state = TaskState.WAITING_APPROVAL
    task.waiting_approval_id = 7
    task.pending_action = ComputerAction.make(ActionKind.CLICK, args={'semantic': 'pay'})
    mgr.store.save(task)
    mgr.recover_all()
    restored = mgr.store.get(task.id)
    assert restored.state == TaskState.PAUSED
    assert restored.waiting_approval_id is None and restored.pending_action is None
    assert restored.generation > task.generation
    assert 'approval' in restored.last_error.lower()
    assert asyncio.run(mgr.run(task.id)) == TaskState.PAUSED
    assert mgr.resume(task.id).state == TaskState.RECOVERING


def test_recover_all_cannot_erase_a_concurrent_owner_pause(tmp_path, monkeypatch):
    mgr = manager(tmp_path / 'tasks.json')
    task = mgr.create_task('concurrent fixture')
    task.state = TaskState.RUNNING
    mgr.store.save(task)
    original = mgr._save
    inserted = False
    def race(row):
        nonlocal inserted
        if not inserted:
            inserted = True
            fresh = mgr.store.get(row.id)
            fresh.state = TaskState.PAUSED
            mgr.store.save(fresh)
        return original(row)
    monkeypatch.setattr(mgr, '_save', race)
    mgr.recover_all()
    assert mgr.store.get(task.id).state == TaskState.PAUSED


def test_active_without_pending_effect_still_enters_recovery(tmp_path):
    mgr = manager(tmp_path / 'tasks.json')
    task = mgr.create_task('active fixture')
    task.state = TaskState.RUNNING
    mgr.store.save(task)
    assert [x.id for x in mgr.recover_all()] == [task.id]
    assert mgr.store.get(task.id).state == TaskState.RECOVERING
