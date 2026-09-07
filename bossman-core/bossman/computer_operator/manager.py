from __future__ import annotations
import asyncio,contextlib,threading,time
from dataclasses import replace
from .models import ActionKind,ComputerAction,ComputerTask,StepRecord,TaskMode,TaskState
from ..obs import redact,redact_obj
from .policy import ComputerPolicy
from .store import StaleTaskWrite
from .verifier import Verifier
from .loop_guard import LoopGuard

# Наблюдение, подтверждённое верификатором, — это и есть текущее состояние экрана.
# Повторный полный снимок сразу после него стоит ещё один UIA-обход + PNG и НИЧЕГО
# нового не приносит, если состояние не успело измениться. Переиспользуем его как
# `before` следующего шага только внутри этого окна и только после успешной
# верификации; всё остальное (смена generation, отказ верификации, approval,
# loop guard, ошибка действия) заставляет наблюдать заново.
OBSERVATION_REUSE_MAX_AGE_S=0.75
# Ограниченное число повторов compare-and-set: команда владельца записывается один
# раз, бесконечно бороться за строку недопустимо.
_CAS_RETRIES=5
# Состояния, которые ставит владелец: пауза, перехват управления, стоп.
_OWNER_SET_STATES=frozenset({TaskState.PAUSED,TaskState.USER_CONTROL,TaskState.CANCELLED})


class OwnerStateChanged(RuntimeError):
    """Кто-то ещё (владелец: пауза/стоп/перехват) записал строку задачи.

    Цикл обязан перечитать авторитетное состояние и подчиниться ему, а не
    продолжать со своим снимком.
    """


class OwnerInterrupted(RuntimeError):
    """Владелец нажал «Пауза»/«Стоп»/«Перехват», пока шло ЧТЕНИЕ.

    Раньше цикл узнавал об этом только на своих контрольных точках, поэтому
    подтверждение стопа стоило столько, сколько длилось незавершённое чтение:
    обход UIA-дерева тяжёлого окна или ход модели — это секунды, а не
    миллисекунды. Защёлка ставится синхронно в pause/stop/take_control и
    обрывает НЕэффектные ожидания. Само диспатчирование эффекта не отменяется —
    отменить его на полпути значит получить неизвестный результат; вместо этого
    новый диспатч просто не начинается (см. _dispatch_blocked).
    """

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
                 policy=None,verifier=None,control_lease=None,access_check=None,
                 observation_reuse_max_age_s=OBSERVATION_REUSE_MAX_AGE_S):
        self.store=store; self.planner=planner; self.observer=observer; self.action_router=action_router
        self.approval_create=approval_create; self.approval_wait=approval_wait; self.event_emit=event_emit
        self.policy=policy or ComputerPolicy(); self.verifier=verifier or Verifier()
        self.control_lease=control_lease or ControlLease()
        # Профильный чек доступа к управлению компом (см. profiles.gate). None → no-op:
        # существующие локальные потоки не режем; профиль с выключенным тумблером
        # computer_control ЗАПРЕЩАЕТ создание задачи (бросает PermissionError ДО _save).
        self.access_check=access_check
        self.locks={}; self.global_locked=False
        self.loop_guards={}   # task_id -> LoopGuard (защита от слепого повтора)
        # 0 (или отрицательное) полностью выключает переиспользование наблюдений.
        self.observation_reuse_max_age_s=max(0.0,float(observation_reuse_max_age_s or 0.0))
        self.observations_taken=0; self.observations_reused=0   # счётчики для замера, не для гейтов
        # Защёлка «владелец вмешался», по одной на задачу. Ставится СИНХРОННО в
        # команде владельца, до и независимо от записи в журнал, поэтому
        # подтверждение стопа не ждёт ни наблюдения, ни модели, ни диска.
        self._interrupts={}
        self.interrupts_observed=0
        self.dispatches_prevented=0
        # Монотонные суммы по фазам шага. Это ИЗМЕРЕНИЕ, а не гейт: по ним видно,
        # что именно стоит времени — наблюдение, модель, допуск, диспатч,
        # верификация или запись журнала.
        self.phase_seconds={k:0.0 for k in ("observe","plan","admit","dispatch","verify","persist")}
        self.phase_calls={k:0 for k in self.phase_seconds}

    def create_task(self,goal,*,mode=TaskMode.CONTROL,source="local",owner_device_id=None):
        if self.access_check is not None:
            # Источник передаётся в gate: не-локальный источник без профиляса
            # получает fail-CLOSED (Security Hardening V1.1). Совместимо со старыми
            # одно-аргументными колбэками.
            try:
                self.access_check(owner_device_id,source)
            except TypeError:
                self.access_check(owner_device_id)   # бросает PermissionError, если запрещено
        t=ComputerTask.create(goal,mode=mode,source=source,owner_device_id=owner_device_id)
        self._clear_interrupt(t.id)
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
        last=""; reusable=None
        while True:
            try:
                # Авторитетное состояние строки, а не снимок, сделанный шаг назад.
                # Без этого «Пауза»/«Стоп» владельца, пришедшие во время наблюдения
                # или действия, затирались следующим _save устаревшей копии.
                t=self._req(t.id)
                if t.terminal:return t.state
                if self.global_locked:return self._fail(t,"operator globally locked",TaskState.LOCKED)
                if t.state in {TaskState.PAUSED,TaskState.USER_CONTROL}:return t.state
                self.control_lease.heartbeat(t.id)
                if self.control_lease.holder()!=t.id:return self._fail(t,"desktop control lease lost")
                if t.steps_used>=t.max_steps:return self._fail(t,"max steps exceeded")
                before=self._reuse(reusable,t); reusable=None
                if before is None:
                    t.state=TaskState.OBSERVING; self._save(t)
                    with self._phase("observe"):
                        before=await self._read_or_interrupt(t.id,self.observer.observe(generation=t.generation))
                    self.observations_taken+=1
                t.last_observation=before
                plan_generation=t.generation
                t.state=TaskState.PLANNING; self._save(t)
                try:
                    with self._phase("plan"):
                        a=await self._read_or_interrupt(t.id,self.planner.next_action(
                          goal=t.goal,observation_summary=before.summary,
                          foreground=before.foreground,ui_tree=before.ui_tree,last_result=last,
                          remaining_steps=t.max_steps-t.steps_used))
                except OwnerInterrupted:
                    raise
                except Exception as e:
                    t.replans_used+=1; last=f"planner:{type(e).__name__}:{e}"; self._save(t)
                    if t.replans_used>t.max_replans:return self._fail(t,"planner replan budget")
                    continue
                if a.kind is ActionKind.COMPLETE:
                    t.state=TaskState.COMPLETED; t.pending_action=None; self._save(t)
                    self.loop_guards.pop(t.id,None)
                    self._emit(t,"completed"); return t.state
                if a.kind is ActionKind.FAIL:return self._fail(t,a.text or "planner failed")
                # `before` — наблюдение, сделанное НАБЛЮДАТЕЛЕМ, а не моделью:
                # приложение/заголовок переднего плана политика использует как
                # улику последствия, чтобы решение «спросить владельца» не
                # держалось на одном поле планировщика (см. policy).
                with self._phase("admit"):
                    d=self.policy.classify(a,mode=t.mode,locked=self.global_locked,observation=before)
                if not d.allow:
                    t.replans_used+=1; last=f"policy denied:{d.reason}"; self._save(t)
                    if t.replans_used>t.max_replans:return self._fail(t,"policy/replan budget")
                    continue
                # Loop guard проверяется ДО записи намерения: намерение, которое мы
                # сейчас же и выбросим, незачем journal'ить (это была лишняя запись
                # всего журнала на каждом сработавшем guard).
                guard=self.loop_guards.setdefault(t.id,LoopGuard())
                gv=guard.check(a,before)
                if gv.tripped:
                    # Не повторяем одно и то же вслепую: тратим replan, а не действие.
                    t.replans_used+=1; last=f"loop guard [{gv.kind}]: {gv.reason}"; self._save(t)
                    self._emit(t,"loop_guard",kind=gv.kind,reason=gv.reason)
                    if t.replans_used>t.max_replans:return self._fail(t,f"loop guard: {gv.reason}")
                    continue
                cur=self._req(t.id)
                if cur.generation!=plan_generation or cur.state in {TaskState.PAUSED,TaskState.USER_CONTROL,TaskState.CANCELLED,TaskState.LOCKED}:
                    return self._fail(cur,"stale observation: generation changed" if cur.generation!=plan_generation else "input state changed before action")
                # Write-ahead намерения и рабочее состояние — ОДНА запись журнала.
                # Раньше это были две записи подряд (pending_action, затем RUNNING).
                t.pending_action=self._sanitize_action(a); step=StepRecord(action=t.pending_action,before_observation_id=before.id); t.history.append(step)
                t.state=TaskState.WAITING_APPROVAL if d.requires_approval else TaskState.RUNNING
                self._save(t)
                if d.requires_approval:
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
                        if cur.state in {TaskState.PAUSED,TaskState.USER_CONTROL}:
                            # Parking is the owner's result, not a task failure;
                            # retain why the old approval was unusable as well.
                            for _ in range(_CAS_RETRIES):
                                if cur.state not in {TaskState.PAUSED,TaskState.USER_CONTROL}:
                                    break
                                cur.last_error="approved action stale; owner control preserved"
                                try:self._save(cur)
                                except OwnerStateChanged:
                                    cur=self._req(t.id); continue
                                break
                            return self._req(t.id).state
                        return self._fail(cur,"approved action stale")
                    t=cur
                    step=t.history[-1] if t.history else step
                    if t.state in {TaskState.PAUSED,TaskState.USER_CONTROL,TaskState.CANCELLED,TaskState.LOCKED}:
                        return self._fail(t,"input state changed before action")
                    t.state=TaskState.RUNNING; self._save(t)
                index=len(t.history)-1
                # Последний барьер перед ЭФФЕКТОМ: после наблюдённой отмены новый
                # ввод не отправляется. Ниже этой строки отменять уже нельзя —
                # эффект был бы с неизвестным результатом.
                if self._interrupt_pending(t.id):
                    self.dispatches_prevented+=1
                    return self._fail(self._req(t.id),"owner interrupt: dispatch prevented")
                try:
                    with self._phase("dispatch"):
                        backend=await self.action_router.execute(a,before)
                except Exception as e:
                    error=f"{type(e).__name__}:{e}"
                    t=self._persist_effect_outcome(t.id,index,{"error":error,"finished_at":time.time()},
                                                   replans_used=t.replans_used+1)
                    last=f"action failed:{error}"
                    if t.replans_used>t.max_replans:return self._fail(t,"action replan budget")
                    continue
                # Ниже эффект УЖЕ произошёл. Его исход обязан попасть в журнал, даже
                # если ровно в этот момент владелец нажал «Стоп»: шаг без записи
                # выглядит неисполненным, а восстановление могло бы повторить
                # необратимое действие. Состояние владельца при этом сохраняется.
                t=self._persist_effect_outcome(t.id,index,{},steps_used=t.steps_used+1,
                                               state=TaskState.OBSERVING)
                # Наблюдение ПОСЛЕ эффекта не прерывается: результат уже случившегося
                # действия надо увидеть. Прерывание отменяет только следующий шаг.
                with self._phase("observe"):
                    after=await self.observer.observe(generation=t.generation)
                self.observations_taken+=1
                with self._phase("verify"):
                    v=self.verifier.verify(a,after)
                guard.record(a,before,after,v.ok)
                t=self._persist_effect_outcome(t.id,index,
                    {"after_observation_id":after.id,"verified":v.ok,"finished_at":time.time()},
                    observation=after,replans_used=None if v.ok else t.replans_used+1)
                if v.ok:
                    last=f"verified via {backend}:{v.reason}"; self._emit(t,"step_verified",action=a.kind.value)
                    # Проверенное наблюдение = текущее состояние. Следующий шаг может
                    # планировать по нему, пока оно не устарело (см. _reuse).
                    reusable=(after,t.generation,time.monotonic())
                else:
                    last=f"verify failed:{v.reason}"
                    if t.replans_used>t.max_replans:return self._fail(t,"verification budget")
            except OwnerInterrupted:
                # Защёлка сработала во время чтения. Ничего в мире не изменилось;
                # перечитываем строку сверху цикла и подчиняемся владельцу.
                reusable=None
                fresh=self._req(t.id)
                if not (fresh.terminal or self.global_locked
                        or fresh.state in {TaskState.PAUSED,TaskState.USER_CONTROL}):
                    # Защёлка без команды за ней (например, гонка с resume):
                    # не крутимся в пустую, снимаем её и продолжаем работу.
                    self._clear_interrupt(t.id)
                continue
            except OwnerStateChanged:
                # Владелец (или восстановление) записал строку, пока шёл шаг.
                # Перечитываем сверху цикла и подчиняемся тому, что записано.
                reusable=None
                continue

    @contextlib.contextmanager
    def _phase(self,name):
        started=time.perf_counter()
        try:
            yield
        finally:
            self.phase_seconds[name]+=time.perf_counter()-started
            self.phase_calls[name]+=1

    def phase_report(self)->dict:
        """Разбивка по фазам шага в миллисекундах; счётчики вызовов рядом."""
        return {"total_ms":{k:round(v*1000,3) for k,v in self.phase_seconds.items()},
                "calls":dict(self.phase_calls),
                "mean_ms":{k:(round(self.phase_seconds[k]*1000/self.phase_calls[k],3)
                              if self.phase_calls[k] else None) for k in self.phase_seconds}}

    def _interrupt_event(self,task_id):
        event=self._interrupts.get(task_id)
        if event is None:
            event=self._interrupts[task_id]=asyncio.Event()
        return event

    def _interrupt_pending(self,task_id)->bool:
        event=self._interrupts.get(task_id)
        return bool(event is not None and event.is_set())

    def _signal_interrupt(self,task_id)->None:
        """Синхронно защёлкнуть вмешательство владельца. Ошибка не глушится тихо
        только там, где события вообще нет (вне event loop): тогда цикл всё равно
        увидит команду на своей контрольной точке."""
        with contextlib.suppress(RuntimeError):
            self._interrupt_event(task_id).set()

    def _clear_interrupt(self,task_id)->None:
        self._interrupts.pop(task_id,None)

    async def _read_or_interrupt(self,task_id,coro):
        """Дождаться ЧТЕНИЯ или вмешательства владельца — что придёт раньше.

        Гоняются только неэффектные операции (наблюдение, планирование): их
        отмена ничего не меняет в мире. Диспатч эффекта не отменяется никогда.
        """
        event=self._interrupts.get(task_id)
        if event is not None and event.is_set():
            coro.close(); self.interrupts_observed+=1
            raise OwnerInterrupted(task_id)
        event=self._interrupt_event(task_id)
        work=asyncio.ensure_future(coro); waiter=asyncio.ensure_future(event.wait())
        try:
            done,_=await asyncio.wait({work,waiter},return_when=asyncio.FIRST_COMPLETED)
            if work in done:
                return work.result()
            work.cancel()
            with contextlib.suppress(asyncio.CancelledError,Exception):
                await work
            self.interrupts_observed+=1
            raise OwnerInterrupted(task_id)
        finally:
            waiter.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await waiter

    def _reuse(self,reusable,t):
        """Переиспользовать проверенное наблюдение как `before` следующего шага.

        Возвращает None (значит: наблюдать заново), если окно выключено,
        наблюдение старше окна, сменилась generation задачи или наблюдение
        принадлежит другой generation. Никакого «доверия по умолчанию»:
        отказ верификации, approval, loop guard и ошибка действия вообще не
        кладут наблюдение в `reusable`.
        """
        if not reusable or self.observation_reuse_max_age_s<=0:return None
        obs,generation,taken_at=reusable
        if generation!=t.generation or getattr(obs,"generation",generation)!=t.generation:return None
        if (time.monotonic()-taken_at)>self.observation_reuse_max_age_s:return None
        self.observations_reused+=1
        return obs

    def pause(self,i):
        self._signal_interrupt(i)      # защёлка ДО записи: подтверждение не ждёт диска
        return self._state(i,TaskState.PAUSED,"paused",invalidate=True)
    def take_control(self,i):
        self._signal_interrupt(i)
        self.loop_guards.pop(i,None)   # оператор вмешался -> прежние подписи не значат ничего
        t=self._state(i,TaskState.USER_CONTROL,"user_control",invalidate=True)
        self.control_lease.revoke()
        return t
    def stop(self,i):
        self._signal_interrupt(i)
        return self._state(i,TaskState.CANCELLED,"cancelled",invalidate=True)
    def resume(self,i):
        t=self._req(i)
        if t.state not in {TaskState.PAUSED,TaskState.USER_CONTROL,TaskState.RECOVERING}:raise RuntimeError("invalid resume")
        self.loop_guards.pop(i,None)   # внешнее вмешательство -> история неактуальна
        self._clear_interrupt(i)       # владелец сам возвращает управление
        t.state=TaskState.RECOVERING; t.generation+=1; t.pending_action=None; t.waiting_approval_id=None
        self._save(t); self._emit(t,"recovering"); return t
    def recover_all(self):
        """Recover interrupted work without converting owner stops into authority.

        Paused/taken-over tasks remain untouched. A pending approval from the
        previous process is invalidated and parked; explicit Resume must request
        a new approval. Reload on CAS conflict so concurrent owner commands win.
        This does not resolve an ambiguous external effect or certify its result.
        """
        out=[]
        try:
            for candidate in self.store.list():
                for _ in range(_CAS_RETRIES):
                    t=self._req(candidate.id)
                    if t.terminal or t.state in {TaskState.PAUSED,TaskState.USER_CONTROL}:
                        break
                    waiting=t.state is TaskState.WAITING_APPROVAL
                    t.state=TaskState.PAUSED if waiting else TaskState.RECOVERING
                    t.generation+=1; t.pending_action=None; t.waiting_approval_id=None
                    if waiting:
                        t.last_error="restart invalidated approval; explicit resume and fresh approval required"
                    try:self._save(t)
                    except OwnerStateChanged:continue
                    # Older manager variants have no latch; generation still
                    # invalidates their in-flight action/approval snapshot.
                    update=getattr(self,"_signal_interrupt" if waiting else "_clear_interrupt",None)
                    if update is not None:update(t.id)
                    out.append(t)
                    break
                else:
                    raise OwnerStateChanged(f"{candidate.id}: recovery deferred after concurrent updates")
        finally:
            self.control_lease.revoke()
        return out
    def emergency_lock(self):
        self.global_locked=True
        self.control_lease.revoke()
        for task_id in list(self._interrupts):self._signal_interrupt(task_id)
        for t in self.store.list():
            if not t.terminal:
                self._signal_interrupt(t.id); self._fail(t,"emergency lock",TaskState.LOCKED)
    def _state(self,i,state,event,invalidate=False):
        for _ in range(_CAS_RETRIES):
            t=self._req(i)
            if t.terminal:return t
            t.state=state
            if invalidate:t.generation+=1;t.pending_action=None
            try:self._save(t)
            except OwnerStateChanged:continue
            self._emit(t,event);return t
        raise OwnerStateChanged(f"{i}: task row kept changing under the owner command")
    def _fail(self,t,reason,state=TaskState.FAILED):
        """Записать терминальный вердикт, не затирая более свежую запись владельца.

        Если строка успела стать терминальной (владелец нажал «Стоп»), его решение
        побеждает: возвращаем сохранённое состояние, а не переписываем его на FAILED.
        """
        error=str(reason)[:3000]
        # Состояние, которое поставил ВЛАДЕЛЕЦ, не переписывается системным FAILED
        # с техническим текстом вроде "approved action stale": «я остановил» — не
        # «оно сломалось», а пауза/перехват обязаны остаться возобновляемыми.
        # Причина всё равно записывается — владельцу видно, чем кончился шаг.
        # Экстренная блокировка (LOCKED) — тоже команда владельца и доминирует.
        if state is TaskState.FAILED and t.state in _OWNER_SET_STATES:
            return self._record_owner_stop(t,error)
        for _ in range(_CAS_RETRIES):
            t.state=state;t.last_error=error;t.pending_action=None
            try:self._save(t)
            except OwnerStateChanged:
                t=self._req(t.id)
                if t.terminal:
                    self.loop_guards.pop(t.id,None);return t.state
                continue
            self.loop_guards.pop(t.id,None)   # задача терминальна -> история не нужна
            self._emit(t,"failed",error=t.last_error);return t.state
        return self._req(t.id).state
    def _persist_effect_outcome(self,task_id,index,fields,*,steps_used=None,replans_used=None,
                                state=None,observation=None):
        """Записать исход УЖЕ произошедшего эффекта на авторитетную строку.

        Не перетирает состояние, которое поставил владелец, и не откатывает
        generation: обновляются только поля шага и счётчики. Возвращает строку,
        которая реально сохранена.
        """
        for _ in range(_CAS_RETRIES):
            t=self._req(task_id)
            if 0<=index<len(t.history):
                for key,value in fields.items():setattr(t.history[index],key,value)
            if steps_used is not None:t.steps_used=max(t.steps_used,steps_used)
            if replans_used is not None:t.replans_used=max(t.replans_used,replans_used)
            if observation is not None:t.last_observation=observation
            if state is not None and not t.terminal and t.state not in _OWNER_SET_STATES:
                t.state=state
            t.pending_action=None; t.waiting_approval_id=None
            try:
                self._save(t); return t
            except OwnerStateChanged:
                continue
        return self._req(task_id)

    def _record_owner_stop(self,t,error):
        """Сохранить причину остановки шага, НЕ трогая состояние владельца."""
        for _ in range(_CAS_RETRIES):
            t.last_error=error; t.pending_action=None
            try:self._save(t)
            except OwnerStateChanged:
                t=self._req(t.id)
                if t.state not in _OWNER_SET_STATES:break
                continue
            self.loop_guards.pop(t.id,None)
            self._emit(t,"owner_stopped",error=error);return t.state
        return self._req(t.id).state
    def _req(self,i):
        t=self.store.get(i)
        if not t:raise KeyError(i)
        return t
    def _save(self,t):
        t.touch()
        with self._phase("persist"):
            try:self.store.save(t)
            except StaleTaskWrite as exc:raise OwnerStateChanged(str(exc)) from exc
    def _emit(self,t,event,**kw):self.event_emit("computer_operator.task",computer_task_id=t.id,state=t.state.value,event=event,**kw)
    @staticmethod
    def _sanitize_action(a:ComputerAction)->ComputerAction:
        if a.kind is ActionKind.TYPE:
            return replace(a,text=redact(a.text) if a.text else a.text,args=redact_obj(a.args))
        return a
    @staticmethod
    def _preview(t,a,reason):return redact(f"Computer task: {t.goal[:500]}\nAction: {a.kind.value} {a.target or ''}\nReason: {reason}")
