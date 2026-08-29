"""Test wiring for the computer operator: scripted planner/observer fakes plus a
real ActionRouter dispatching to a fake desktop adapter. Production defaults in
subsystem.py stay untouched."""
from __future__ import annotations
import asyncio,time
from .adapters.router import ActionRouter
from .manager import ComputerOperatorManager
from .models import ActionKind,ComputerAction,Observation,new_id
from .store import JsonTaskStore

class FakePlanner:
    def __init__(self,actions=None,gate:asyncio.Event|None=None):
        self.actions=actions if actions is not None else []
        self.gate=gate; self.calls=[]
    def _queue(self,goal):
        if isinstance(self.actions,dict):
            for k,v in self.actions.items():
                if k in goal:return v
            return []
        return self.actions
    async def next_action(self,*,goal,observation_summary,foreground,ui_tree,last_result,remaining_steps):
        self.calls.append({"goal":goal,"observation_summary":observation_summary,"foreground":foreground,
                           "ui_tree":ui_tree,"last_result":last_result,"remaining_steps":remaining_steps})
        if self.gate is not None:await self.gate.wait()
        q=self._queue(goal)
        if not q:return ComputerAction.make(ActionKind.FAIL,text="fake planner exhausted")
        item=q.pop(0)
        if callable(item):item=item(goal=goal,observation_summary=observation_summary,foreground=foreground,
                                    ui_tree=ui_tree,last_result=last_result,remaining_steps=remaining_steps)
        if isinstance(item,Exception):raise item
        return item

class FakeObserver:
    def __init__(self,observations=None,summary="fake screen",foreground=None,ui_tree=None,sensitive=False):
        self.observations=list(observations) if observations is not None else None
        self.summary=summary; self.foreground=dict(foreground or {})
        self.ui_tree=ui_tree; self.sensitive=sensitive; self.generations=[]
    async def observe(self,*,generation):
        self.generations.append(generation)
        await asyncio.sleep(0)
        if self.observations:
            item=self.observations.pop(0)
            if isinstance(item,Exception):raise item
            if isinstance(item,Observation):return item
            if isinstance(item,dict):
                return Observation(new_id("obs"),time.time(),item.get("foreground",self.foreground),
                    item.get("summary",self.summary),item.get("ui_tree",self.ui_tree),
                    item.get("screenshot_ref"),bool(item.get("sensitive",self.sensitive)),generation)
        return Observation(new_id("obs"),time.time(),dict(self.foreground),self.summary,self.ui_tree,None,self.sensitive,generation)

class FakeAdapter:
    name="fake"
    def __init__(self,*,supported=None,gate:asyncio.Event|None=None,raise_on=None):
        self.supported=frozenset(supported) if supported is not None else None
        self.gate=gate; self.entered=asyncio.Event() if gate is not None else None
        self.raise_on=dict(raise_on or {}); self.executed=[]
    async def supports(self,a,o):return self.supported is None or a.kind in self.supported
    async def execute(self,a,o):
        await asyncio.sleep(0)
        self.executed.append(a)
        if self.entered is not None:self.entered.set()
        if self.gate is not None:await self.gate.wait()
        if a.kind in self.raise_on:raise self.raise_on[a.kind]
        return self.name

async def _auto_approve_create(kind,preview,tool=None,payload=None):return 1
async def _auto_approve_wait(approval_id,timeout_s=None):return {"status":"approved","id":approval_id}

def make_manager(store_path,planner,observer,*,adapter=None,approval_create=None,approval_wait=None,
                 event_emit=None,control_lease=None,policy=None,verifier=None):
    return ComputerOperatorManager(store=JsonTaskStore(store_path),planner=planner,observer=observer,
        action_router=ActionRouter([adapter if adapter is not None else FakeAdapter()]),
        approval_create=approval_create or _auto_approve_create,
        approval_wait=approval_wait or _auto_approve_wait,
        event_emit=event_emit or (lambda *a,**k:None),
        policy=policy,verifier=verifier,control_lease=control_lease)
