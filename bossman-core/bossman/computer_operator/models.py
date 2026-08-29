from __future__ import annotations
import time, uuid
from dataclasses import dataclass, field
from enum import Enum
from typing import Any

def new_id(prefix: str) -> str:
    return f"{prefix}_{uuid.uuid4().hex[:16]}"

class TaskState(str, Enum):
    QUEUED="QUEUED"; PLANNING="PLANNING"; OBSERVING="OBSERVING"; RUNNING="RUNNING"
    WAITING="WAITING"; WAITING_APPROVAL="WAITING_APPROVAL"; PAUSED="PAUSED"
    USER_CONTROL="USER_CONTROL"; RECOVERING="RECOVERING"; COMPLETED="COMPLETED"
    FAILED="FAILED"; CANCELLED="CANCELLED"; LOCKED="LOCKED"

class TaskMode(str, Enum):
    CONTROL="CONTROL"; OBSERVE_ONLY="OBSERVE_ONLY"

class ActionKind(str, Enum):
    NOOP="NOOP"; WAIT="WAIT"; FOCUS="FOCUS"; CLICK="CLICK"; DOUBLE_CLICK="DOUBLE_CLICK"
    TYPE="TYPE"; HOTKEY="HOTKEY"; SCROLL="SCROLL"; DRAG="DRAG"; APP_LAUNCH="APP_LAUNCH"
    APP_CLOSE="APP_CLOSE"; BROWSER="BROWSER"; UI_INVOKE="UI_INVOKE"
    TAKE_SCREENSHOT="TAKE_SCREENSHOT"; COMPLETE="COMPLETE"; FAIL="FAIL"

TERMINAL_STATES=frozenset({TaskState.COMPLETED,TaskState.FAILED,TaskState.CANCELLED,TaskState.LOCKED})

@dataclass(slots=True)
class Observation:
    id:str; created_at:float; foreground:dict[str,Any]; summary:str
    ui_tree:Any=None; screenshot_ref:str|None=None; sensitive:bool=False; generation:int=0

@dataclass(slots=True)
class ExpectedState:
    contains_text:str|None=None
    window_title_contains:str|None=None
    foreground_app_contains:str|None=None
    url_contains:str|None=None
    absent_text:str|None=None
    def is_empty(self)->bool:
        return not any((self.contains_text,self.window_title_contains,
                        self.foreground_app_contains,self.url_contains,self.absent_text))

@dataclass(slots=True)
class ComputerAction:
    id:str; kind:ActionKind; expected:ExpectedState
    target:str|None=None; text:str|None=None; args:dict[str,Any]=field(default_factory=dict)
    confidence:float=1.0; source:str="planner"; idempotency_key:str|None=None
    @classmethod
    def make(cls,kind:ActionKind,*,expected:ExpectedState|None=None,**kwargs):
        return cls(id=new_id("act"),kind=kind,expected=expected or ExpectedState(),**kwargs)

@dataclass(slots=True)
class StepRecord:
    action:ComputerAction; before_observation_id:str|None=None; after_observation_id:str|None=None
    verified:bool|None=None; error:str|None=None; approval_id:int|None=None
    started_at:float=field(default_factory=time.time); finished_at:float|None=None

@dataclass(slots=True)
class ComputerTask:
    id:str; goal:str; mode:TaskMode=TaskMode.CONTROL; state:TaskState=TaskState.QUEUED
    source:str="local"; owner_device_id:str|None=None
    created_at:float=field(default_factory=time.time); updated_at:float=field(default_factory=time.time)
    max_steps:int=80; max_replans:int=20; steps_used:int=0; replans_used:int=0; generation:int=0
    last_observation:Observation|None=None; last_error:str|None=None
    waiting_approval_id:int|None=None; pending_action:ComputerAction|None=None
    history:list[StepRecord]=field(default_factory=list)
    @classmethod
    def create(cls,goal:str,**kwargs):
        goal=(goal or "").strip()
        if not goal: raise ValueError("goal is required")
        return cls(id=new_id("ct"),goal=goal[:12000],**kwargs)
    def touch(self): self.updated_at=time.time()
    @property
    def terminal(self): return self.state in TERMINAL_STATES
