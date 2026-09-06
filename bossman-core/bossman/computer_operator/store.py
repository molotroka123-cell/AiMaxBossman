from __future__ import annotations
import json,os
from dataclasses import asdict
from pathlib import Path
from threading import RLock
from .models import ActionKind,ComputerAction,ComputerTask,ExpectedState,Observation,StepRecord,TaskMode,TaskState

class StaleTaskWrite(RuntimeError):
    """A write carried an older revision than the stored row.

    Raised instead of silently overwriting: the owner's pause/stop/take-control
    is written through this same store, and the operator loop used to hold a
    decoded copy for the whole step. Losing that write meant the desktop kept
    acting after the owner had stopped it.
    """
    def __init__(self,task_id:str,expected:int,stored:int):
        super().__init__(f"stale write for {task_id}: held revision {expected}, stored revision {stored}")
        self.task_id=task_id; self.expected=expected; self.stored=stored


class JsonTaskStore:
    """Restart journal only; not a second auth/approval/database architecture.

    Single writer process (the RLock is thread-scoped, as before). ``save`` is
    now compare-and-set on ``ComputerTask.revision``; a caller holding an older
    snapshot must reload rather than clobber the newer row.
    """
    def __init__(self,path):
        self.path=Path(path); self.path.parent.mkdir(parents=True,exist_ok=True); self.lock=RLock()
    def _rows(self):
        if not self.path.exists(): return {}
        try: return json.loads(self.path.read_text(encoding="utf-8"))
        except Exception: return {}
    def save(self,t):
        with self.lock:
            r=self._rows(); prev=r.get(t.id)
            if prev is not None:
                stored=int(prev.get("revision") or 0)
                if stored>int(t.revision or 0): raise StaleTaskWrite(t.id,int(t.revision or 0),stored)
            t.revision=int(t.revision or 0)+1
            r[t.id]=self._enc(t)
            q=self.path.with_suffix(".tmp"); q.write_text(json.dumps(r,ensure_ascii=False,indent=1),encoding="utf-8")
            os.replace(q,self.path)
    def get(self,i):
        with self.lock: x=self._rows().get(i)
        return self._dec(x) if x else None
    def list(self):
        with self.lock: r=self._rows()
        return [self._dec(x) for x in r.values()]
    def _enc(self,t):
        d=asdict(t); d["state"]=t.state.value; d["mode"]=t.mode.value
        if d.get("pending_action"): d["pending_action"]["kind"]=t.pending_action.kind.value
        for i,h in enumerate(t.history): d["history"][i]["action"]["kind"]=h.action.kind.value
        return d
    def _action(self,x):
        if not x:return None
        return ComputerAction(id=x["id"],kind=ActionKind(x["kind"]),expected=ExpectedState(**(x.get("expected") or {})),
          target=x.get("target"),text=x.get("text"),args=x.get("args") or {},confidence=float(x.get("confidence",1)),
          source=x.get("source","planner"),idempotency_key=x.get("idempotency_key"))
    def _dec(self,x):
        o=Observation(**x["last_observation"]) if x.get("last_observation") else None
        t=ComputerTask(id=x["id"],goal=x["goal"],mode=TaskMode(x["mode"]),state=TaskState(x["state"]),
          source=x.get("source","local"),owner_device_id=x.get("owner_device_id"),created_at=x.get("created_at",0),
          updated_at=x.get("updated_at",0),max_steps=x.get("max_steps",80),max_replans=x.get("max_replans",20),
          steps_used=x.get("steps_used",0),replans_used=x.get("replans_used",0),generation=x.get("generation",0),
          last_observation=o,last_error=x.get("last_error"),waiting_approval_id=x.get("waiting_approval_id"),
          pending_action=self._action(x.get("pending_action")),revision=int(x.get("revision") or 0))
        for h in x.get("history",[]):
            t.history.append(StepRecord(action=self._action(h["action"]),before_observation_id=h.get("before_observation_id"),
              after_observation_id=h.get("after_observation_id"),verified=h.get("verified"),error=h.get("error"),
              approval_id=h.get("approval_id"),started_at=h.get("started_at",0),finished_at=h.get("finished_at")))
        return t
