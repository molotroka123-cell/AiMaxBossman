from __future__ import annotations
import asyncio,hashlib,inspect,json,re,threading,time
from dataclasses import replace
from .models import ActionKind,ComputerAction,ComputerTask,StepRecord,TaskMode,TaskState
from ..obs import redact,redact_obj
from .obligations import FileEffect,UnknownEffect,extract_obligations,screen_text,snapshot,unsatisfied
from .policy import ComputerPolicy,authorize_computer_control
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

# --- AT-01: слово планировщика не является результатом -------------------------
# Действия, которые снаружи НИЧЕГО не меняют. Verifier штампует их "non-mutating"
# вообще без постусловия, поэтому их подтверждение не может быть уликой того, что
# эффектная цель достигнута: скриншот не создаёт файл.
_NON_EFFECT_KINDS=frozenset({ActionKind.NOOP,ActionKind.WAIT,ActionKind.TAKE_SCREENSHOT,
                             ActionKind.COMPLETE,ActionKind.FAIL})
# Цель, которая ОБЕЩАЕТ внешний результат: файл, отправку, запуск, правку, оплату.
# Владелец ставит задачи по-русски, поэтому русские основы здесь не «на всякий
# случай», а основной путь. Ошибка в сторону «нужна улика» безопасна: она стоит
# одного перепланирования, ошибка в другую сторону — ложный COMPLETED.
_EFFECT_GOAL=re.compile(
    r"\b(create|write|save|send|upload|download|delete|remove|rename|move|copy|install|"
    r"uninstall|publish|post|submit|click|type|press|enter|edit|change|update|open|close|"
    r"launch|start|run|book|buy|pay|transfer|export|import|render|generate|print|replace|"
    r"insert|append|configure|enable|disable|add|fill|attach|rebuild|deploy)(?:s|es|d|ed|ing)?\b"
    r"|(созда|напиш|запиш|сохран|отправ|загруз|скача|удал|переименов|перемест|копир|"
    r"установ|опубликов|нажм|введ|набер|измен|обнов|откр|закр|запуст|экспорт|импорт|"
    r"сгенерир|вставь|вставит|добав|настрой|включ|выключ|замен|печат|заполн|прикреп|"
    r"оплат|заплат|куп|перевед|перевес|подпиш|отпис|созвон|брониру)"
    r"|([A-Za-z]:\\|(?:^|\s)~?/\S)"
    r"|\.(txt|json|csv|md|py|js|html?|pdf|docx?|xlsx?|pptx?|png|jpe?g|webp|gif|mp4|mov|mkv"
    r"|wav|mp3|zip)\b",
    re.IGNORECASE)


def goal_requires_external_effect(goal:str)->bool:
    """Обещает ли формулировка цели внешний результат.

    Задача приходит строкой, поэтому текстовая эвристика — единственный доступный
    здесь признак. Она вынесена наружу и подменяема (`completion_evidence_required`),
    чтобы хост мог поставить точный классификатор, не форкая цикл оператора.
    """
    return bool(_EFFECT_GOAL.search(goal or ""))


class OwnerStateChanged(RuntimeError):
    """Кто-то ещё (владелец: пауза/стоп/перехват) записал строку задачи.

    Цикл обязан перечитать авторитетное состояние и подчиниться ему, а не
    продолжать со своим снимком.
    """

# A3-04: ни один шаг рабочего стола не ждёт бесконечно. Зависшее приложение,
# застрявшее UIA/COM-чтение, не отвечающий браузер или медленная модель держали
# `await` вечно: цикл не возвращался наверх, авторитетную строку никто не
# перечитывал, и «Стоп» владельца оставался записанным, но не исполненным.
# Пороги — потолок ожидания, а не таймер шага; типичный шаг укладывается на
# порядок быстрее.
OBSERVE_TIMEOUT_S=45.0
PLAN_TIMEOUT_S=120.0
ACT_TIMEOUT_S=60.0
# Как часто внутри ожидания перечитывается строка владельца. Отзывчивость «Стопа»
# определяется этим шагом, а НЕ таймаутом фазы.
OWNER_POLL_S=.25

class StepTimeout(RuntimeError):
    """Фаза шага не уложилась в свой потолок ожидания (A3-04)."""
    def __init__(self,phase,seconds):
        super().__init__(f"{phase} exceeded {seconds:g}s")
        self.phase=phase; self.seconds=seconds

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
                 observation_reuse_max_age_s=OBSERVATION_REUSE_MAX_AGE_S,
                 completion_evidence_required=None,observation_fingerprint=None,
                 observe_timeout_s=OBSERVE_TIMEOUT_S,plan_timeout_s=PLAN_TIMEOUT_S,
                 act_timeout_s=ACT_TIMEOUT_S,obligations_of=None,obligation_probe=None):
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
        # AT-01: чем «эффектная» цель отличается от наблюдательной. Подменяемо —
        # хост с точным классификатором ставит свой, не форкая цикл.
        self.completion_evidence_required=completion_evidence_required or goal_requires_external_effect
        # AT-01: ИМЕННЫЕ обязательства цели и независимое чтение их исхода.
        # `obligation_probe=None` оставляет прежнее (более слабое) правило «хотя
        # бы один подтверждённый изменяющий шаг»: без порта читать мир нечем, и
        # выдавать его отсутствие за выполнение обязательств нельзя.
        self.obligations_of=obligations_of or extract_obligations
        self.obligation_probe=obligation_probe
        self._prestate={}; self._prescreen={}
        # AT-03: из чего считается подпись применимости наблюдения. Подменяемо для
        # хоста, чей UI-снимок содержит заведомо шумные поля.
        self.observation_fingerprint=observation_fingerprint
        self.observations_taken=0; self.observations_reused=0   # счётчики для замера, не для гейтов
        # Стоимость и срабатывания проверки свежести на границе эффекта (AT-03) и
        # отказы завершения без улики (AT-01). Это измерение, а не гейт.
        self.boundary_probes=0; self.stale_boundaries=0; self.completions_refused=0
        # A3-02: кооперативная отмена ввода. `asyncio.to_thread` неотменяем —
        # отмена корутины роняет ожидание, а поток pyautogui продолжает жать
        # клавиши. Флаг ставится ДО освобождения аренды, иначе оператор печатает
        # уже поверх владельца, который аренду только что забрал.
        self._interrupts={}
        # A3-04: потолки ожидания по фазам. 0/None означает «не ограничивать» и
        # существует только для замеров — в проде это возврат к зависанию.
        self.observe_timeout_s=self._timeout(observe_timeout_s)
        self.plan_timeout_s=self._timeout(plan_timeout_s)
        self.act_timeout_s=self._timeout(act_timeout_s)
        self.step_timeouts=0; self.unknown_effects_parked=0   # замер, не гейт

    @staticmethod
    def _timeout(v):
        v=float(v or 0.0)
        return v if v>0 else None

    def create_task(self,goal,*,mode=TaskMode.CONTROL,source="local",owner_device_id=None):
        if self.access_check is not None:
            # Источник передаётся в gate: не-локальный источник без профиля
            # получает fail-CLOSED (Security Hardening V1.1). Совместимо со старыми
            # одно-аргументными колбэками — но совместимость больше не покупается
            # понижением источника (A2-04).
            self._gate(owner_device_id,source)
        t=ComputerTask.create(goal,mode=mode,source=source,owner_device_id=owner_device_id)
        self._save(t); self._emit(t,"created"); return t

    def _gate(self,owner_device_id,source):
        """Спросить профильный гейт, разрешено ли управление компьютером.

        Одна реализация на весь оператор — `policy.authorize_computer_control`:
        та же форма вызова и тот же fail-CLOSED, что и на границе эффекта. Две
        копии этой логики уже разошлись однажды (A2-04), и разойтись второй раз
        им нечего.
        """
        authorize_computer_control(self.access_check,owner_device_id,source)

    def _interrupt_event(self,task_id):
        return self._interrupts.setdefault(task_id,threading.Event())

    def _signal_interrupt(self,task_id)->None:
        """Сказать исполняющемуся вводу остановиться (A3-02)."""
        self._interrupt_event(task_id).set()

    def _clear_interrupt(self,task_id)->None:
        self._interrupt_event(task_id).clear()

    def interrupted(self,task_id)->bool:
        return self._interrupt_event(task_id).is_set()

    async def run(self,task_id):
        lock=self.locks.setdefault(task_id,asyncio.Lock())
        async with lock:
            try:
                t=self._req(task_id)
                if t.terminal:return t.state
                if t.state in {TaskState.PAUSED,TaskState.USER_CONTROL,TaskState.WAITING_APPROVAL}:return t.state
                if not self.control_lease.acquire(task_id):
                    return self._fail(t,f"desktop busy: control lease held by {self.control_lease.holder()}")
                self._clear_interrupt(task_id)
                self._bind_attempt(t)
                setter=getattr(self.action_router,"set_interrupt",None)
                if setter is not None:setter(self._interrupt_event(task_id))
                return await self._run_loop(t)
            except asyncio.CancelledError:
                raise
            except Exception as e:
                try:return self._fail(self._req(task_id),f"operator crash:{type(e).__name__}:{e}")
                except Exception:return TaskState.FAILED
            finally:
                # Порядок обязателен: сначала сказать вводу остановиться, потом
                # отпускать аренду. Наоборот — окно, в котором владелец уже
                # получил рабочий стол, а поток всё ещё в него печатает.
                self._signal_interrupt(task_id)
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
                    before=await self._bounded(t,self.observer.observe(generation=t.generation),
                                               self.observe_timeout_s,"observe",abandon_on_owner=True)
                    self.observations_taken+=1
                t.last_observation=before
                self._bind_attempt(t)
                plan_generation=t.generation
                t.state=TaskState.PLANNING; self._save(t)
                try:
                    a=await self._bounded(t,self.planner.next_action(goal=t.goal,
                        observation_summary=before.summary,foreground=before.foreground,
                        ui_tree=before.ui_tree,last_result=last,
                        remaining_steps=t.max_steps-t.steps_used),
                      self.plan_timeout_s,"plan",abandon_on_owner=True)
                except OwnerStateChanged:
                    # Команда владельца — не ошибка планировщика: её нельзя
                    # списывать в replan-бюджет. Перечитываем строку сверху.
                    reusable=None; continue
                except Exception as e:
                    # OPERATOR-OBSERVABILITY-001 (живой прогон 20260906): причина
                    # (404 без /v1, 401 по истёкшему ключу, ошибка разбора ответа)
                    # терялась, и владелец видел только «planner replan budget».
                    # Настоящая ошибка остаётся в строке задачи и в событии.
                    t.replans_used+=1; last=f"planner:{type(e).__name__}:{e}"
                    t.last_error=last[:3000]; self._save(t)
                    self._emit(t,"planner_error",reason=last[:500])
                    if t.replans_used>t.max_replans:
                        return self._fail(t,f"planner replan budget; last planner error: {last}")
                    continue
                if a.kind is ActionKind.COMPLETE:
                    # AT-01: слово планировщика — не результат. COMPLETE обязан
                    # нести проверяемое постусловие, и верификатор подтверждает
                    # его на СВЕЖЕМ наблюдении, прежде чем задача станет COMPLETED.
                    # Голый COMPLETE — replan; после исчерпания бюджета — честный
                    # FAILED, а не фиктивный успех.
                    if a.expected.is_empty():
                        self.completions_refused+=1; t.replans_used+=1
                        last="COMPLETE without verifiable postcondition; provide expected postcondition"
                        t.last_error=last; self._save(t)
                        self._emit(t,"completion_refused",reason=last)
                        if t.replans_used>t.max_replans:
                            return self._fail(t,"unverifiable COMPLETE: no postcondition")
                        continue
                    # Завершение — тоже граница эффекта: оно печатает необратимый
                    # вердикт. Проверять постусловие по наблюдению, снятому ДО
                    # вызова модели (а то и переиспользованному), значит верить
                    # снимку, который планировщик уже прочитал. Смотрим сейчас.
                    self.observations_taken+=1
                    verdict_obs=await self._bounded(t,self.observer.observe(generation=t.generation),
                                                    self.observe_timeout_s,"observe",abandon_on_owner=True)
                    t.last_observation=verdict_obs
                    cv=self.verifier.verify(a,verdict_obs)
                    if not cv.ok:
                        self.completions_refused+=1
                        t.replans_used+=1; last=f"COMPLETE postcondition failed:{cv.reason}"
                        t.last_error=last; self._save(t)
                        self._emit(t,"completion_refused",reason=last)
                        if t.replans_used>t.max_replans:
                            return self._fail(t,f"false COMPLETE: {cv.reason}")
                        continue
                    # Постусловие пишет сам планировщик и сверяет его с экраном,
                    # который сам же прочитал: «создай файл» закрывается фразой
                    # «desktop», и она правдива. Поэтому для цели, ОБЕЩАЮЩЕЙ
                    # внешний результат, дополнительно требуется хотя бы один
                    # подтверждённый ИЗМЕНЯЮЩИЙ шаг: скриншот не создаёт файл.
                    refusal=self._completion_blocked(t)
                    if refusal:
                        self.completions_refused+=1
                        t.replans_used+=1; last=refusal; t.last_error=refusal; self._save(t)
                        self._emit(t,"completion_refused",reason=refusal)
                        if t.replans_used>t.max_replans:
                            # Причина отказа обязана дожить до владельца: раньше
                            # исчерпание бюджета затирало её словами про бюджет,
                            # и «почему не закрылось» приходилось угадывать.
                            return self._fail(t,f"completion evidence budget; last refusal: {refusal}")
                        continue
                    t.state=TaskState.COMPLETED; t.pending_action=None; self._save(t)
                    self.loop_guards.pop(t.id,None)
                    self._emit(t,"completed"); return t.state
                if a.kind is ActionKind.FAIL:return self._fail(t,a.text or "planner failed")
                # `before` — наблюдение, сделанное НАБЛЮДАТЕЛЕМ, а не моделью:
                # приложение/заголовок переднего плана политика использует как
                # улику последствия, чтобы решение «спросить владельца» не
                # держалось на одном поле планировщика (см. policy).
                d=self.policy.classify(a,mode=t.mode,locked=self.global_locked,observation=before)
                if not d.allow:
                    t.replans_used+=1; last=f"policy denied:{d.reason}"
                    t.last_error=last; self._save(t)   # OPERATOR-OBSERVABILITY-001
                    if t.replans_used>t.max_replans:return self._fail(t,"policy/replan budget")
                    continue
                cur=self._req(t.id)
                if cur.generation!=plan_generation or cur.state in {TaskState.PAUSED,TaskState.USER_CONTROL,TaskState.CANCELLED,TaskState.LOCKED}:
                    return self._fail(cur,"stale observation: generation changed" if cur.generation!=plan_generation else "input state changed before action")
                # AT-03: между наблюдением и намерением прошёл ВЫЗОВ МОДЕЛИ. Окно
                # переиспользования и generation — это про нашу собственную задачу,
                # они ничего не говорят про экран. Спрашиваем сам экран.
                if not await self._fresh_boundary(t,before,"pre_intent"):
                    t.replans_used+=1; last="stale observation: UI changed during planning"
                    reusable=None; self._save(t)
                    if t.replans_used>t.max_replans:return self._fail(t,"freshness replan budget")
                    continue
                t.pending_action=self._sanitize_action(a); step=StepRecord(action=t.pending_action,before_observation_id=before.id); t.history.append(step); self._save(t)
                approval_used=False
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
                    approval_used=True
                if t.state in {TaskState.PAUSED,TaskState.USER_CONTROL,TaskState.CANCELLED,TaskState.LOCKED}:
                    return self._fail(t,"input state changed before action")
                if approval_used:
                    # AT-03: наблюдение, по которому действие планировалось и
                    # одобрялось, устарело за время ожидания владельца: попап,
                    # смена вкладки/фокуса или значения поля не обновляют его.
                    # Перед эффектом — обязательная свежая проверка состояния,
                    # и policy пересматривается по ней.
                    t.state=TaskState.OBSERVING; self._save(t)
                    fresh=await self._bounded(t,self.observer.observe(generation=t.generation),
                                              self.observe_timeout_s,"observe",abandon_on_owner=True)
                    self.observations_taken+=1
                    # Владелец одобрил ЭТО действие ПРОТИВ ЭТОГО экрана. Если экран
                    # с тех пор изменился, одобрение на него не переносится: клик
                    # «оплатить» нацелен в конкретное окно, а не в координату. Это
                    # и есть требование AT-03 «TTL и generation не заменяют
                    # актуальность цели на границе эффекта» — перепланировать,
                    # а не исполнять по устаревшему разрешению.
                    if self._signature(fresh)!=self._signature(before):
                        self.stale_boundaries+=1
                        last="approval stale: UI changed while waiting for approval"
                        self._emit(t,"stale_observation",phase="post_approval")
                        step.error="not dispatched: "+last; step.finished_at=time.time()
                        t.last_observation=fresh; t.pending_action=None; t.waiting_approval_id=None
                        t.replans_used+=1; reusable=None; t.last_error=last; self._save(t)
                        self._emit(t,"approval_invalidated",reason=last)
                        if t.replans_used>t.max_replans:return self._fail(t,"freshness replan budget")
                        continue
                    before=fresh
                    t.last_observation=before; step.before_observation_id=before.id
                    d2=self.policy.classify(a,mode=t.mode,locked=self.global_locked,observation=before)
                    if not d2.allow:
                        t.replans_used+=1; last=f"policy denied after re-observe:{d2.reason}"; self._save(t)
                        if t.replans_used>t.max_replans:
                            return self._fail(t,"policy/replan budget after re-observe")
                        continue
                guard=self.loop_guards.setdefault(t.id,LoopGuard())
                gv=guard.check(a,before)
                if gv.tripped:
                    # Не повторяем одно и то же вслепую: тратим replan, а не действие.
                    t.replans_used+=1; last=f"loop guard [{gv.kind}]: {gv.reason}"; self._save(t)
                    self._emit(t,"loop_guard",kind=gv.kind,reason=gv.reason)
                    if t.replans_used>t.max_replans:return self._fail(t,f"loop guard: {gv.reason}")
                    continue
                t.state=TaskState.RUNNING; self._save(t)
                try: backend=await self._bounded(t,self.action_router.execute(a,before),
                                                 self.act_timeout_s,"act",abandon_on_owner=False)
                except StepTimeout as exc:
                    # Ввод отправлен, ответа нет. Повтор здесь удвоил бы уже
                    # ушедший необратимый эффект, поэтому повтора не будет.
                    if a.kind in _NON_EFFECT_KINDS:
                        step.error=str(exc); step.finished_at=time.time(); t.pending_action=None
                        t.replans_used+=1; last=f"action timed out:{exc}"; self._save(t)
                        self._emit(t,"step_timeout",phase="act",action=a.kind.value)
                        if t.replans_used>t.max_replans:return self._fail(t,"action replan budget")
                        continue
                    step.error=str(exc); step.finished_at=time.time()
                    self._emit(t,"step_timeout",phase="act",action=a.kind.value)
                    return self._park_unknown_effect(
                        t,f"action {a.kind.value} timed out with unknown outcome: {exc}")
                except Exception as e:
                    step.error=f"{type(e).__name__}:{e}"; step.finished_at=time.time(); t.pending_action=None
                    t.replans_used+=1; last=f"action failed:{step.error}"; self._save(t)
                    if t.replans_used>t.max_replans:return self._fail(t,"action replan budget")
                    continue
                t.steps_used+=1; t.state=TaskState.OBSERVING; self._save(t)
                try:
                    after=await self._bounded(t,self.observer.observe(generation=t.generation),
                                              self.observe_timeout_s,"observe",abandon_on_owner=False)
                except StepTimeout as exc:
                    # Действие ИСПОЛНЕНО, а прочитать результат нечем: подтвердить
                    # или опровергнуть эффект невозможно. Это тот же неизвестный
                    # исход, что и таймаут самого действия.
                    self._emit(t,"step_timeout",phase="post_observe",action=a.kind.value)
                    if a.kind in _NON_EFFECT_KINDS:
                        t.replans_used+=1; last=f"post-action observe timed out:{exc}"; self._save(t)
                        if t.replans_used>t.max_replans:return self._fail(t,"verification budget")
                        continue
                    return self._park_unknown_effect(
                        t,f"{a.kind.value} executed but the screen could not be re-read: {exc}")
                self.observations_taken+=1
                t.last_observation=after
                step.after_observation_id=after.id
                v=self.verifier.verify(a,after); step.verified=v.ok; step.finished_at=time.time()
                guard.record(a,before,after,v.ok)
                t.pending_action=None; t.waiting_approval_id=None; self._save(t)
                if v.ok:
                    last=f"verified via {backend}:{v.reason}"; self._emit(t,"step_verified",action=a.kind.value)
                    # Проверенное наблюдение = текущее состояние. Следующий шаг может
                    # планировать по нему, пока оно не устарело (см. _reuse).
                    reusable=(after,t.generation,time.monotonic())
                else:
                    t.replans_used+=1; last=f"verify failed:{v.reason}"; self._save(t)
                    if t.replans_used>t.max_replans:return self._fail(t,"verification budget")
            except StepTimeout as exc:
                # Дошли сюда только фазы ЧТЕНИЯ (наблюдение до действия, зонд
                # свежести): ввод не отправлялся, мир не тронут — поэтому это
                # трата replan-бюджета, а не парковка и не «крах оператора».
                # Зависание, которое не проходит, упирается в тот же бюджет и
                # заканчивается честным FAILED с названной фазой.
                reusable=None
                t=self._req(t.id)
                if t.terminal:return t.state
                t.replans_used+=1; last=f"timeout:{exc}"; t.last_error=last[:3000]
                try:self._save(t)
                except OwnerStateChanged:continue
                self._emit(t,"step_timeout",phase=exc.phase)
                if t.replans_used>t.max_replans:
                    return self._fail(t,f"desktop step timeout budget; last: {last}")
                continue
            except OwnerStateChanged:
                # Владелец (или восстановление) записал строку, пока шёл шаг.
                # Перечитываем сверху цикла и подчиняемся тому, что записано.
                reusable=None
                continue

    def _owner_intervened(self,t):
        """Записал ли владелец (или восстановление) что-то, отменяющее этот шаг.

        Читается АВТОРИТЕТНАЯ строка, а не снимок: в этом весь смысл — во время
        длинного ожидания только строка и меняется.
        """
        if self.global_locked:return "operator globally locked"
        try:cur=self._req(t.id)
        except KeyError:return "task row disappeared"
        if cur.generation!=t.generation:return "owner invalidated the step"
        if cur.terminal:return f"owner ended the task: {cur.state.value}"
        if cur.state in {TaskState.PAUSED,TaskState.USER_CONTROL}:return f"owner took over: {cur.state.value}"
        return None

    async def _bounded(self,t,coro,seconds,phase,*,abandon_on_owner):
        """Ждать фазу шага с потолком и с опросом команды владельца (A3-04).

        Два разных «хватит» намеренно разведены:

        `abandon_on_owner=True` — фаза ЧИТАЕТ экран (наблюдение, зонд, план).
        Брошенное чтение не оставляет следа в мире, поэтому «Стоп» исполняется в
        пределах `OWNER_POLL_S`, а не в пределах таймаута.

        `abandon_on_owner=False` — фаза УЖЕ ОТПРАВИЛА ввод. Бросить её досрочно
        значило бы объявить исход, которого никто не наблюдал, поэтому владельцу
        отдаётся кооперативный флаг отмены (его читает адаптер), а ожидание
        продолжается до потолка. Дальше исход всё равно неизвестен — и
        обрабатывается как неизвестный, а не как повод повторить.

        Честно про предел: `asyncio.to_thread` неотменяем. Снятие обёртки
        освобождает ЦИКЛ, а не поток. Поэтому на любом исходе, кроме штатного,
        взводится флаг прерывания: поток может дожить до своего конца, но
        ВВОДИТЬ он больше ничего не будет.
        """
        task=asyncio.ensure_future(coro)
        deadline=None if seconds is None else time.monotonic()+seconds
        try:
            while True:
                budget=OWNER_POLL_S if deadline is None else min(OWNER_POLL_S,max(0.0,deadline-time.monotonic()))
                done,_=await asyncio.wait({task},timeout=budget)
                if done:return task.result()
                stop=self._owner_intervened(t)
                if stop is not None:
                    self._signal_interrupt(t.id)
                    if abandon_on_owner:raise OwnerStateChanged(f"{t.id}: {stop}")
                if deadline is not None and time.monotonic()>=deadline:
                    self.step_timeouts+=1
                    self._signal_interrupt(t.id)
                    raise StepTimeout(phase,seconds)
        finally:
            if not task.done():
                task.cancel()
                # Ждём саму обёртку, а не работу: без этого «Task was destroyed
                # but it is pending» и незамеченные исключения потока.
                try:await asyncio.wait({task},timeout=OWNER_POLL_S)
                except asyncio.CancelledError:raise
            elif not task.cancelled():
                task.exception()   # исход прочитан — иначе предупреждение в лог

    def _park_unknown_effect(self,t,reason):
        """Отправить задачу на сверку: ввод ушёл, исход неизвестен (A3-04/H04).

        НЕ повтор и НЕ FAILED. Повтор дублировал бы уже отправленный необратимый
        эффект, а FAILED утверждал бы, что эффекта не было. PAUSED с диагнозом
        оставляет решение владельцу — ровно как парковка необратимого эффекта
        неизвестного исхода после перезапуска.
        """
        self._signal_interrupt(t.id)
        self.unknown_effects_parked+=1
        for _ in range(_CAS_RETRIES):
            t=self._req(t.id)
            if t.terminal or t.state in {TaskState.PAUSED,TaskState.USER_CONTROL}:
                return t.state
            t.state=TaskState.PAUSED; t.last_error=str(reason)[:3000]; t.pending_action=None
            try:self._save(t)
            except OwnerStateChanged:continue
            self._emit(t,"unknown_effect_parked",reason=t.last_error)
            return t.state
        return self._req(t.id).state

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

    def _signature(self,obs):
        """Подпись ПРИМЕНИМОСТИ наблюдения: то, по чему действие было нацелено.

        Входят generation, foreground (окно/приложение/url) и ui_tree целиком —
        включая значения полей, потому что AT-03 требует ловить именно «popup,
        смену вкладки/фокуса, layout, значение поля или внешнюю модификацию».

        НЕ входят: `summary` (пересказ тех же двух источников; у продового
        наблюдателя его пишет summarizer — недетерминированный внешний вызов, и
        его дрожь означала бы ложные перепланирования), а также `id`,
        `created_at` и `screenshot_ref` — они новые при каждом снимке по
        построению, и подпись по ним не сравнивалась бы никогда.
        """
        if self.observation_fingerprint is not None:return self.observation_fingerprint(obs)
        payload={"generation":getattr(obs,"generation",None),
                 "foreground":getattr(obs,"foreground",None),
                 "ui_tree":getattr(obs,"ui_tree",None)}
        raw=json.dumps(payload,sort_keys=True,ensure_ascii=True,default=repr)
        return hashlib.sha256(raw.encode("utf-8")).hexdigest()

    async def _probe(self,generation):
        """Дешёвое повторное чтение экрана для проверки применимости.

        Наблюдатель может отдать `probe()` — структура без скриншота и без вызова
        summarizer. Иначе честно платим за полное наблюдение и считаем его как
        полное, чтобы счётчик стоимости не врал.
        """
        self.boundary_probes+=1
        probe=getattr(self.observer,"probe",None)
        if probe is None:
            self.observations_taken+=1
            return await self.observer.observe(generation=generation)
        return await probe(generation=generation)

    async def _fresh_boundary(self,t,planned,phase):
        """Тот ли ещё экран, по которому спланировано действие (AT-03).

        True только если авторитетная строка задачи не перехвачена И экран сейчас
        даёт ту же подпись применимости. При совпадении подписи `planned` остаётся
        корректным `before` — по определению совпадения.
        """
        cur=self._req(t.id)
        if cur.generation!=t.generation or cur.state in {TaskState.PAUSED,TaskState.USER_CONTROL,
                                                         TaskState.CANCELLED,TaskState.LOCKED}:
            return False
        fresh=await self._probe(cur.generation)
        if self._signature(fresh)==self._signature(planned):return True
        self.stale_boundaries+=1
        self._emit(t,"stale_observation",phase=phase)
        return False

    def _completion_blocked(self,t):
        """Причина отказа принять COMPLETE как результат, или None (AT-01).

        Постусловие уже проверено на свежем наблюдении, но пишет его сам
        планировщик по экрану, который сам же прочитал, — «создай файл» честно
        закрывается фразой «desktop». Поэтому цель, обещающая внешний результат,
        дополнительно требует хотя бы один ПОДТВЕРЖДЁННЫЙ ИЗМЕНЯЮЩИЙ шаг.
        Наблюдательная цель («опиши экран») ничего снаружи не обещала и
        закрывается одним проверенным постусловием.
        """
        if not self.completion_evidence_required(t.goal):return None
        # Именные обязательства проверяются ПЕРВЫМИ и по существу: «какая-то
        # мутация произошла» не закрывает «создай ЭТОТ файл с ЭТИМ текстом».
        obligations=self._obligations(t)
        # UnknownEffect — честно признанный предел, а не проверка: цель обещает
        # результат и не называет его, значит отличить относящуюся мутацию от
        # посторонней НЕЧЕМ. Блокировать всё подряд здесь означало бы, что
        # оператор не может закрыть ни «оплати счёт», ни «нажми кнопку», то есть
        # почти ничего. Вместо этого правило УСИЛЕНО против прежнего: мало того,
        # что нужен подтверждённый изменяющий шаг, — экран обязан отличаться от
        # того, что был до попытки. AT-01 для таких целей остаётся PARTIAL, и это
        # записано в отчёте, а не спрятано.
        if obligations and all(isinstance(e,UnknownEffect) for e in obligations):
            # Требовать здесь изменения экрана нельзя: законный эффект бывает
            # невидимым (запись в фоне, вызов API), и такое требование ломало бы
            # рабочие цели ради видимости строгости. Остаётся прежнее слабое
            # правило, и AT-01 для таких целей честно остаётся PARTIAL.
            obligations=()
        # Файловые обязательства без порта проверять нечем: возвращаемся к
        # прежнему (слабому) правилу, а не притворяемся, что проверили.
        if self.obligation_probe is None:
            obligations=tuple(e for e in obligations if not isinstance(e,FileEffect))
        if obligations:
            missing=unsatisfied(obligations,self.obligation_probe or (lambda e:None),
                                self._prestate.get(t.id),
                                (self._prescreen.get(t.id,""),screen_text(t.last_observation)))
            if missing:
                detail="; ".join(f"{getattr(e,'path',None) or getattr(e,'text',None) or 'результат'}: {why}"
                                 for e,why in missing)
                return ("completion refused: the goal's stated results are not confirmed "
                        f"by an independent post-state read [{detail}]")
            return None
        for step in t.history:
            if (step.verified is True and step.finished_at is not None
                    and step.action.kind not in _NON_EFFECT_KINDS):
                return None
        return ("completion refused: goal asserts an external effect but no verified "
                "effect was performed; perform and verify the change before completing")

    def _obligations(self,t):
        try:return tuple(self.obligations_of(t.goal) or ())
        except Exception:
            # Извлечение — эвристика над текстом владельца. Её поломка не имеет
            # права ни закрыть задачу, ни уронить цикл: возвращаем «именных
            # обязательств нет» и решает прежнее правило.
            return ()

    def _bind_attempt(self,t):
        """Снять состояние обещанных результатов ДО попытки (привязка улики).

        Файл, лежавший там до начала и не изменившийся, доказывает прошлое.
        Снимок делается один раз на попытку и переживает перезапуск процесса
        не больше, чем сама попытка: после restart он снимается заново, и это
        строже, а не мягче — уже созданный в прошлой попытке файл станет
        «существовал до начала», и завершение потребует свежего подтверждения.
        """
        # Экран «до» снимается по ПЕРВОМУ наблюдению попытки: на входе в run()
        # его ещё нет, и пустая строка означала бы «до было пусто», то есть
        # любое непустое «после» считалось бы изменением.
        if t.last_observation is None or t.id in self._prescreen:return
        self._prescreen[t.id]=screen_text(t.last_observation)
        if self.obligation_probe is None:return
        obligations=self._obligations(t)
        if not obligations:return
        try:self._prestate[t.id]=snapshot(obligations,self.obligation_probe)
        except Exception:self._prestate[t.id]={}

    def pause(self,i):
        self._signal_interrupt(i)      # A3-02: до записи состояния, а не после
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
                    # Действие было отправлено, а исход так и не записан: повторять
                    # его вслепую нельзя, и «просто продолжить» тоже нельзя. Называем
                    # неопределённость в строке, чтобы владелец видел ПОЧЕМУ задача
                    # стоит, а не молчаливое RECOVERING без причины.
                    unknown=(not waiting and (t.pending_action is not None
                             or (t.history and t.history[-1].finished_at is None)))
                    t.state=TaskState.PAUSED if waiting else TaskState.RECOVERING
                    t.generation+=1; t.pending_action=None; t.waiting_approval_id=None
                    if waiting:
                        t.last_error="restart invalidated approval; explicit resume and fresh approval required"
                    elif unknown:
                        t.last_error="reconciliation required: prior effect outcome is unknown"
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
        for task_id in list(self._interrupts)+[t.id for t in self.store.list()]:
            self._signal_interrupt(task_id)
        self.control_lease.revoke()
        for t in self.store.list():
            if not t.terminal:self._fail(t,"emergency lock",TaskState.LOCKED)
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
        # Владелец уже завершил задачу («Стоп») — его решение не переписывается
        # системным FAILED с техническим текстом вроде "approved action stale".
        # Но САМА причина не теряется: состояние остаётся владельческим, а
        # системный диагноз пишется рядом. Иначе оператор видит «отменено» и
        # никогда не узнаёт, что акция вдобавок была протухшей.
        if t.state is TaskState.CANCELLED:
            for _ in range(_CAS_RETRIES):
                t.last_error=error;t.pending_action=None
                try:self._save(t)
                except OwnerStateChanged:
                    t=self._req(t.id)
                    if t.state is not TaskState.CANCELLED:break
                    continue
                break
            self.loop_guards.pop(t.id,None);return t.state
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
    def _req(self,i):
        t=self.store.get(i)
        if not t:raise KeyError(i)
        return t
    def _save(self,t):
        t.touch()
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
