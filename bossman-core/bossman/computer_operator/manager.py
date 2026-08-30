from __future__ import annotations
import asyncio,threading,time
from dataclasses import replace
from .models import ActionKind,ComputerAction,ComputerTask,StepRecord,TaskMode,TaskState
from ..obs import redact,redact_obj
from .policy import ComputerPolicy
from .verifier import Verifier

class ControlLease:
    """Exclusive desktop control lease: single live holder, TTL + heartbeat, revocable."""
    def __init__(self,ttl_s:float=30.0):
        self.ttl_s=float(ttl_s); self._lock=threading.RLock(); self._holder=None
    def acquire(self,task_id:str,ttl_s:float|None=None)->bool:
        with self._lock:
            now=time.monotonic()
            if self._holder and self._holder[0]!=task_id and self._holder[1]>now:return False
            self._holder=(task_id,now+float(ttl_s if ttl_s is not None else self.ttl_s)); return True
    def heartbeat(self,task_id:str)->bool:
        with self._lock:
            if self._holder and self._holder[0]==task_id:
                self._holder=(task_id,time.monotonic()+self.ttl_s); return True
            return False
    def release(self,task_id:str)->bool:
        with self._lock:
            if self._holder and self._holder[0]==task_id:
                self._holder=None; return True
            return False
    def revoke(self)->None:
        with self._lock: self._holder=None
    def holder(self)->str|None:
        with self._lock:
            if self._holder and self._holder[1]>time.monotonic():return self._holder[0]
            return None

class ComputerOperatorManager:
    def __init__(self,*,store,planner,observer,action_router,approval_create,approval_wait,event_emit,
                 policy=None,verifier=None,control_lease=None,access_check=None):
        self.store=store; self.planner=planner; self.observer=observer; self.action_router=action_router
        self.approval_create=approval_create; self.approval_wait=approval_wait; self.event_emit=event_emit
        self.policy=policy or ComputerPolicy(); self.verifier=verifier or Verifier()
        self.control_lease=control_lease or ControlLease()
        # Профильный чек доступа к управлению компом (см. profiles.gate). None → no-op:
        # существующие локальные потоки не режем; профиль с выключенным тумблером
        # computer_control ЗАПРЕЩАЕТ создание задачи (бросает PermissionError ДО _save).
        self.access_check=access_check
        self.locks={}; self.global_locked=False

    def create_task(self,goal,*,mode=TaskMode.CONTROL,source="local",owner_device_id=None):
        if self.access_check is not None:
            self.access_check(owner_device_id)   # бросает PermissionError, если запрещено
        t=ComputerTask.create(goal,mode=mode,source=source,owner_device_id=owner_device_id)
        self._save(t); self._emit(t,"created"); return t

    async def run(self,task_id):
        lock=self.locks.setdefault(task_id,asyncio.Lock())
        async with lock:
            try:
                t=self._req(task_id)
                if t.terminal:return t.state
                if t.state in {TaskState.PAUSED,TaskState.USER_CONTROL,TaskState.WAITING_APPROVAL}:return t.state
                if not self.control_lease.acquire(task_id):
                    return self._fail(t,f"desktop busy: control lease held by {self.control_lease.holder()}")
                return await self._run_loop(t)
            except asyncio.CancelledError:
                raise
            except Exception as e:
                try:return self._fail(self._req(task_id),f"operator crash:{type(e).__name__}:{e}")
                except Exception:return TaskState.FAILED
            finally:
                self.control_lease.release(task_id)

    async def _run_loop(self,t):
        last=""
        while not t.terminal:
            if self.global_locked:return self._fail(t,"operator globally locked",TaskState.LOCKED)
            if t.state in {TaskState.PAUSED,TaskState.USER_CONTROL}:return t.state
            self.control_lease.heartbeat(t.id)
            if self.control_lease.holder()!=t.id:return self._fail(t,"desktop control lease lost")
            if t.steps_used>=t.max_steps:return self._fail(t,"max steps exceeded")
            t.state=TaskState.OBSERVING; self._save(t)
            before=await self.observer.observe(generation=t.generation); t.last_observation=before
            plan_generation=t.generation
            t.state=TaskState.PLANNING; self._save(t)
            try:
                a=await self.planner.next_action(goal=t.goal,observation_summary=before.summary,
                  foreground=before.foreground,ui_tree=before.ui_tree,last_result=last,
                  remaining_steps=t.max_steps-t.steps_used)
            except Exception as e:
                t.replans_used+=1; last=f"planner:{type(e).__name__}:{e}"; self._save(t)
                if t.replans_used>t.max_replans:return self._fail(t,"planner replan budget")
                continue
            if a.kind is ActionKind.COMPLETE:
                t.state=TaskState.COMPLETED; t.pending_action=None; self._save(t); self._emit(t,"completed"); return t.state
            if a.kind is ActionKind.FAIL:return self._fail(t,a.text or "planner failed")
            d=self.policy.classify(a,mode=t.mode,locked=self.global_locked)
            if not d.allow:
                t.replans_used+=1; last=f"policy denied:{d.reason}"; self._save(t)
                if t.replans_used>t.max_replans:return self._fail(t,"policy/replan budget")
                continue
            cur=self._req(t.id)
            if cur.generation!=plan_generation or cur.state in {TaskState.PAUSED,TaskState.USER_CONTROL,TaskState.CANCELLED,TaskState.LOCKED}:
                return self._fail(cur,"stale observation: generation changed" if cur.generation!=plan_generation else "input state changed before action")
            t.pending_action=self._sanitize_action(a); step=StepRecord(action=t.pending_action,before_observation_id=before.id); t.history.append(step); self._save(t)
            if d.requires_approval:
                t.state=TaskState.WAITING_APPROVAL
                aid=await self.approval_create(d.approval_kind or "computer_action",self._preview(t,a,d.reason),
                  tool="computer_operator",payload={"computer_task_id":t.id,"action_id":a.id,
                  "idempotency_key":a.idempotency_key,"kind":a.kind.value})
                t.waiting_approval_id=aid; step.approval_id=aid; self._save(t); self._emit(t,"waiting_approval",approval_id=aid)
                self.control_lease.release(t.id)
                result=await self.approval_wait(aid)
                if result.get("status")!="approved":return self._fail(t,f"approval {result.get('status','unknown')}")
                if not self.control_lease.acquire(t.id):
                    return self._fail(self._req(t.id),f"desktop busy: control lease held by {self.control_lease.holder()}")
                cur=self._req(t.id)
                if cur.generation!=plan_generation or not cur.pending_action or cur.pending_action.id!=a.id:
                    return self._fail(cur,"approved action stale")
                t=cur
            if t.state in {TaskState.PAUSED,TaskState.USER_CONTROL,TaskState.CANCELLED,TaskState.LOCKED}:
                return self._fail(t,"input state changed before action")
            t.state=TaskState.RUNNING; self._save(t)
            try: backend=await self.action_router.execute(a,before)
            except Exception as e:
                step.error=f"{type(e).__name__}:{e}"; step.finished_at=time.time(); t.pending_action=None
                t.replans_used+=1; last=f"action failed:{step.error}"; self._save(t)
                if t.replans_used>t.max_replans:return self._fail(t,"action replan budget")
                continue
            t.steps_used+=1; t.state=TaskState.OBSERVING; self._save(t)
            after=await self.observer.observe(generation=t.generation); t.last_observation=after
            step.after_observation_id=after.id
            v=self.verifier.verify(a,after); step.verified=v.ok; step.finished_at=time.time()
            t.pending_action=None; t.waiting_approval_id=None; self._save(t)
            if v.ok:
                last=f"verified via {backend}:{v.reason}"; self._emit(t,"step_verified",action=a.kind.value)
            else:
                t.replans_used+=1; last=f"verify failed:{v.reason}"; self._save(t)
                if t.replans_used>t.max_replans:return self._fail(t,"verification budget")
        return t.state

    def pause(self,i): return self._state(i,TaskState.PAUSED,"paused",invalidate=True)
    def take_control(self,i):
        t=self._state(i,TaskState.USER_CONTROL,"user_control",invalidate=True)
        self.control_lease.revoke()
        return t
    def stop(self,i): return self._state(i,TaskState.CANCELLED,"cancelled",invalidate=True)
    def resume(self,i):
        t=self._req(i)
        if t.state not in {TaskState.PAUSED,TaskState.USER_CONTROL,TaskState.RECOVERING}:raise RuntimeError("invalid resume")
        t.state=TaskState.RECOVERING; t.generation+=1; t.pending_action=None; t.waiting_approval_id=None
        self._save(t); self._emit(t,"recovering"); return t
    def recover_all(self):
        out=[]
        for t in self.store.list():
            if t.terminal:continue
            t.state=TaskState.RECOVERING; t.generation+=1; t.pending_action=None; t.waiting_approval_id=None
            self._save(t); out.append(t)
        self.control_lease.revoke()
        return out
    def emergency_lock(self):
        self.global_locked=True
        self.control_lease.revoke()
        for t in self.store.list():
            if not t.terminal:self._fail(t,"emergency lock",TaskState.LOCKED)
    def _state(self,i,state,event,invalidate=False):
        t=self._req(i)
        if not t.terminal:
            t.state=state
            if invalidate:t.generation+=1;t.pending_action=None
            self._save(t);self._emit(t,event)
        return t
    def _fail(self,t,reason,state=TaskState.FAILED):
        t.state=state;t.last_error=str(reason)[:3000];t.pending_action=None;self._save(t);self._emit(t,"failed",error=t.last_error);return t.state
    def _req(self,i):
        t=self.store.get(i)
        if not t:raise KeyError(i)
        return t
    def _save(self,t):t.touch();self.store.save(t)
    def _emit(self,t,event,**kw):self.event_emit("computer_operator.task",computer_task_id=t.id,state=t.state.value,event=event,**kw)
    @staticmethod
    def _sanitize_action(a:ComputerAction)->ComputerAction:
        if a.kind is ActionKind.TYPE:
            return replace(a,text=redact(a.text) if a.text else a.text,args=redact_obj(a.args))
        return a
    @staticmethod
    def _preview(t,a,reason):return redact(f"Computer task: {t.goal[:500]}\nAction: {a.kind.value} {a.target or ''}\nReason: {reason}")
