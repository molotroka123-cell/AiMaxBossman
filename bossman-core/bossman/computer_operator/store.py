from __future__ import annotations
import json,os
from dataclasses import asdict
from pathlib import Path
from threading import RLock
from .models import ActionKind,ComputerAction,ComputerTask,ExpectedState,Observation,StepRecord,TaskMode,TaskState

class JsonTaskStore:
    """Restart journal only; not a second auth/approval/database architecture."""
    def __init__(self,path):
        self.path=Path(path); self.path.parent.mkdir(parents=True,exist_ok=True); self.lock=RLock()
    def _rows(self):
        if not self.path.exists(): return {}
        try: return json.loads(self.path.read_text(encoding="utf-8"))
        except Exception: return {}
    def save(self,t):
        with self.lock:
            r=self._rows(); r[t.id]=self._enc(t)
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
          pending_action=self._action(x.get("pending_action")))
        for h in x.get("history",[]):
            t.history.append(StepRecord(action=self._action(h["action"]),before_observation_id=h.get("before_observation_id"),
              after_observation_id=h.get("after_observation_id"),verified=h.get("verified"),error=h.get("error"),
              approval_id=h.get("approval_id"),started_at=h.get("started_at",0),finished_at=h.get("finished_at")))
        return t
