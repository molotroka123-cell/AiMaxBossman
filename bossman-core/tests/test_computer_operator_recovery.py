from bossman.computer_operator.manager import ComputerOperatorManager
from bossman.computer_operator.store import JsonTaskStore
from bossman.computer_operator.models import TaskState
class D:pass
async def ac(*a,**k):return 1
async def aw(*a,**k):return {"status":"rejected"}
def ev(*a,**k):pass
def m(p):return ComputerOperatorManager(store=JsonTaskStore(p),planner=D(),observer=D(),action_router=D(),approval_create=ac,approval_wait=aw,event_emit=ev)
def test_restart_forces_recovery(tmp_path):
    x=m(tmp_path/"t.json");t=x.create_task("x");t.state=TaskState.RUNNING;x.store.save(t)
    y=m(tmp_path/"t.json");y.recover_all();z=y.store.get(t.id)
    assert z.state is TaskState.RECOVERING and z.pending_action is None and z.waiting_approval_id is None
def test_take_control_invalidates_generation(tmp_path):
    x=m(tmp_path/"t.json");t=x.create_task("x");g=t.generation;z=x.take_control(t.id)
    assert z.state is TaskState.USER_CONTROL and z.generation==g+1
