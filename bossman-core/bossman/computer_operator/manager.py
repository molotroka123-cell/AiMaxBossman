from __future__ import annotations
import asyncio,hashlib,json,re,threading,time
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
                 completion_evidence_required=None,observation_fingerprint=None):
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
        # AT-03: из чего считается подпись применимости наблюдения. Подменяемо для
        # хоста, чей UI-снимок содержит заведомо шумные поля.
        self.observation_fingerprint=observation_fingerprint
        self.observations_taken=0; self.observations_reused=0   # счётчики для замера, не для гейтов
        # Стоимость и срабатывания проверки свежести на границе эффекта (AT-03) и
        # отказы завершения без улики (AT-01). Это измерение, а не гейт.
        self.boundary_probes=0; self.stale_boundaries=0; self.completions_refused=0

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
                    before=await self.observer.observe(generation=t.generation); self.observations_taken+=1
                t.last_observation=before
                plan_generation=t.generation
                t.state=TaskState.PLANNING; self._save(t)
                try:
                    a=await self.planner.next_action(goal=t.goal,observation_summary=before.summary,
                      foreground=before.foreground,ui_tree=before.ui_tree,last_result=last,
                      remaining_steps=t.max_steps-t.steps_used)
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
                    verdict_obs=await self.observer.observe(generation=t.generation)
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
                            return self._fail(t,"completion evidence budget")
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
                    fresh=await self.observer.observe(generation=t.generation); self.observations_taken+=1
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
                try: backend=await self.action_router.execute(a,before)
                except Exception as e:
                    step.error=f"{type(e).__name__}:{e}"; step.finished_at=time.time(); t.pending_action=None
                    t.replans_used+=1; last=f"action failed:{step.error}"; self._save(t)
                    if t.replans_used>t.max_replans:return self._fail(t,"action replan budget")
                    continue
                t.steps_used+=1; t.state=TaskState.OBSERVING; self._save(t)
                after=await self.observer.observe(generation=t.generation); self.observations_taken+=1
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
            except OwnerStateChanged:
                # Владелец (или восстановление) записал строку, пока шёл шаг.
                # Перечитываем сверху цикла и подчиняемся тому, что записано.
                reusable=None
                continue

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
        for step in t.history:
            if (step.verified is True and step.finished_at is not None
                    and step.action.kind not in _NON_EFFECT_KINDS):
                return None
        return ("completion refused: goal asserts an external effect but no verified "
                "effect was performed; perform and verify the change before completing")

    def pause(self,i): return self._state(i,TaskState.PAUSED,"paused",invalidate=True)
    def take_control(self,i):
        self.loop_guards.pop(i,None)   # оператор вмешался -> прежние подписи не значат ничего
        t=self._state(i,TaskState.USER_CONTROL,"user_control",invalidate=True)
        self.control_lease.revoke()
        return t
    def stop(self,i): return self._state(i,TaskState.CANCELLED,"cancelled",invalidate=True)
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
