from pathlib import Path
from .. import approvals,events
from ..config import settings
from .manager import ComputerOperatorManager
from .store import JsonTaskStore

class Unwired:
    async def next_action(self,**kw):raise RuntimeError("wire Stage3 Gateway planner")
    async def observe(self,**kw):raise RuntimeError("wire desktop/browser observer")
    async def execute(self,*a,**kw):raise RuntimeError("wire action router")

MANAGER=ComputerOperatorManager(store=JsonTaskStore(Path(getattr(settings,"data_dir","."))/"computer_operator/tasks.json"),
    planner=Unwired(),observer=Unwired(),action_router=Unwired(),
    approval_create=approvals.create,approval_wait=approvals.wait,event_emit=events.emit)

class ComputerOperatorSubsystem:
    name="computer_operator";critical=False
    async def validate(self):return None
    async def start(self):MANAGER.recover_all()
    async def stop(self):return None
def build_subsystem():return ComputerOperatorSubsystem()
