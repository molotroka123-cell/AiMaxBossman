"""Адаптеры V3-портов к ЗАМОРОЖЕННОМУ V2 Command Center (пакет `bcc`).

Правило этого файла одно: V2 не меняется. Всё здесь — вызовы уже существующего
публичного кода `bcc` (реестр инструментов, decide_effect, очередь approvals,
свежая верификация). Ни одной правки в command-center этот модуль не требует и
не подразумевает; `bcc` импортируется лениво, чтобы bossman_v3 оставался
импортируемым и там, где Command Center не установлен.

Почему адаптер исполнения ОДИН на все семейства (terminal, browser, memory,
apps, openclaw, opencode, plugins, mcp): в V2 они все — записи одного реестра
`bcc.tools.REGISTRY`, с одной политикой `decide_effect` и одним контрактом
`ToolResult`. Делать по адаптеру на семейство означало бы переписать реестр
второй раз. Семейство узнаётся по `ToolSpec.source` — ровно так же, как это
делает V2-контракт действий.

Что переносится из V2 без ослабления:
  * AUTO/ASK/DENY решает `decide_effect` V2, не этот файл;
  * ASK идёт через каноническую очередь `svc.approvals` и одобряется
    ВЛАДЕЛЬЦЕМ; адаптер никогда не одобряет сам — он лишь находит уже
    одобренную запись и потребляет её один раз;
  * инструмент с `ToolResult.error=True` — НЕ исполнение (та же граница, что
    у action_contract: status="executed" против "error");
  * шаг подтверждён только свежей верификацией `bcc/v2/verification`, а не
    тем, что инструмент отработал. Нет объявленного ожидания — нет
    подтверждения (fail-closed, как UNVERIFIED в review_gate).

Async/sync: V2 асинхронен и его движок БД привязан к циклу событий; V3-порты
синхронны. Поэтому все корутины V2 выполняются на ОДНОМ долгоживущем цикле в
фоновом потоке (CommandCenterRuntime) — иначе aiosqlite/SQLAlchemy получают
соединения «из другого цикла».
"""
from __future__ import annotations

import asyncio
import json
import threading
import uuid
from datetime import datetime, timezone
from typing import Any, Mapping

from ..computer_agent.agent import UniversalComputerAgent
from ..contracts import (ApprovalDecision, ExecutionReceipt, Observation, PolicyDecision,
                         TypedAction, VerificationResult)

APPROVAL_KIND = "tool"


def _now() -> datetime:
    return datetime.now(timezone.utc)


# ------------------------------------------------------------------ runtime

class CommandCenterRuntime:
    """Один цикл событий в фоновом потоке для всех вызовов в V2."""

    def __init__(self) -> None:
        self.loop = asyncio.new_event_loop()
        self._thread = threading.Thread(target=self._serve, name="v3-cc-runtime", daemon=True)
        self._thread.start()

    def _serve(self) -> None:
        asyncio.set_event_loop(self.loop)
        self.loop.run_forever()

    def call(self, coro, timeout: float | None = 120.0):
        return asyncio.run_coroutine_threadsafe(coro, self.loop).result(timeout)

    def close(self) -> None:
        """Сначала отменить фоновые задачи V2 (event-pump'ы фич), потом
        остановить цикл — иначе asyncio ругается «Task was destroyed but it
        is pending»: задачи жили на нашем цикле, и завершать их — наша забота."""
        async def _drain() -> None:
            current = asyncio.current_task()
            pending = [t for t in asyncio.all_tasks() if t is not current and not t.done()]
            for t in pending:
                t.cancel()
            if pending:
                await asyncio.gather(*pending, return_exceptions=True)
        try:
            self.call(_drain(), timeout=10)
        finally:
            self.loop.call_soon_threadsafe(self.loop.stop)
            self._thread.join(timeout=5)


def _preview(action: TypedAction, task_id: int | None = None) -> str:
    """Стабильное описание действия: по нему одобренная запись находится при
    повторном прогоне после того, как владелец нажал «одобрить». Идентификатор
    задачи входит в preview намеренно: одобрение привязано к задаче и не может
    быть «подобрано» другой задачей с тем же текстом действия (аудит P0-3)."""
    args = {k: v for k, v in dict(action.args).items() if k != "expect"}
    body = json.dumps(args, ensure_ascii=False, sort_keys=True)
    scope = f"task#{task_id} " if task_id is not None else ""
    return f"v3 {scope}{action.action_type}: {body}"[:500]


# ------------------------------------------------------------------- policy

class CommandCenterPolicy:
    """AUTO/ASK/DENY — решение V2 (`bcc.tools.decide_effect`), не наше."""

    def __init__(self, agent: Mapping[str, Any]):
        self.agent = dict(agent)

    def authorize(self, action: TypedAction, context: Mapping[str, Any]) -> PolicyDecision:
        from bcc.tools import REGISTRY, agent_policy_rules, decide_effect
        spec = REGISTRY.get(action.action_type)
        if spec is None:
            return PolicyDecision(False, reason=f"инструмент {action.action_type!r} не зарегистрирован")
        args = {k: v for k, v in dict(action.args).items() if k != "expect"}
        effect, reason = decide_effect(spec, args, self.agent, agent_policy_rules(self.agent))
        if effect == "deny":
            return PolicyDecision(False, reason=reason)
        return PolicyDecision(True, requires_approval=(effect == "ask"), reason=reason)


# ----------------------------------------------------------------- approval

class CommandCenterApproval:
    """ASK через каноническую очередь V2. Никогда не одобряет сам."""

    def __init__(self, rt: CommandCenterRuntime, svc: Any, *, task_id: int | None = None):
        self.rt, self.svc, self.task_id = rt, svc, task_id

    def request(self, action: TypedAction, policy: PolicyDecision,
                context: Mapping[str, Any]) -> ApprovalDecision:
        preview = _preview(action, self.task_id)
        approved = self.rt.call(self.svc.approvals.list(status="approved", limit=500))
        for row in approved:
            if row.get("kind") == APPROVAL_KIND and row.get("preview") == preview \
                    and (row.get("task_id") in (None, self.task_id)):
                ok = self.rt.call(self.svc.approvals.consume(row["id"], kind=APPROVAL_KIND,
                                                             preview=preview))
                if ok:
                    return ApprovalDecision(True, approval_id=str(row["id"]))
        pending = self.rt.call(self.svc.approvals.list(status="pending", limit=500))
        for row in pending:
            if row.get("kind") == APPROVAL_KIND and row.get("preview") == preview:
                return ApprovalDecision(False, approval_id=str(row["id"]),
                                        reason=f"ожидает решения владельца: approval {row['id']}")
        created = self.rt.call(self.svc.approvals.create(
            kind=APPROVAL_KIND, preview=preview, task_id=self.task_id))
        aid = (created or {}).get("id")
        return ApprovalDecision(False, approval_id=str(aid) if aid is not None else None,
                                reason=f"создан запрос на подтверждение: approval {aid}")


# ----------------------------------------------------------------- executor

class CommandCenterExecutor:
    """Все семейства — через `bcc.tools.REGISTRY` + `execute_tool`.

    Пишет строку в `bcc.db.tool_calls` (публичная таблица чеков V2), чтобы
    действие, инициированное V3, было видно тем же аудитом, UI и контрактом
    действий, что и действия самого V2. Ошибка инструмента — RuntimeError:
    для цепочки это «шаг не исполнен», не «исполнен с ошибкой»."""

    def __init__(self, rt: CommandCenterRuntime, svc: Any, *, task: Mapping[str, Any],
                 agent: Mapping[str, Any], run_id: int):
        self.rt, self.svc = rt, svc
        self.task, self.agent, self.run_id = dict(task), dict(agent), int(run_id)

    def supports(self, action_type: str) -> bool:
        from bcc.tools import REGISTRY
        return REGISTRY.get(action_type) is not None

    async def _record(self, spec, args: dict, call_id: str, *, status: str,
                      preview: str, error: str | None, started: datetime, finished: datetime) -> None:
        import sqlalchemy as sa
        from bcc.db import tool_calls as tool_calls_t
        from bcc.tools import args_hash
        async with self.svc.db.session() as s:
            await s.execute(sa.insert(tool_calls_t).values(
                run_id=self.run_id, task_id=self.task["id"], step=0, call_id=call_id,
                tool=spec.name, source=spec.source, args=args,
                args_hash=args_hash(spec.name, args), effect="v3", status=status,
                result_preview=preview[:2000], truncated=False,
                duration_ms=int((finished - started).total_seconds() * 1000), error=error,
                created_at=started, finished_at=finished))
            await s.commit()

    def execute(self, action: TypedAction) -> ExecutionReceipt:
        from bcc.tools import REGISTRY, ToolContext, execute_tool
        spec = REGISTRY.get(action.action_type)
        if spec is None:
            raise RuntimeError(f"инструмент {action.action_type!r} не зарегистрирован")
        args = {k: v for k, v in dict(action.args).items() if k != "expect"}
        call_id = f"v3-{uuid.uuid4().hex[:12]}"
        ctx = ToolContext(svc=self.svc, task=self.task, run_id=self.run_id,
                          agent=self.agent, call_id=call_id)
        started = _now()
        result = self.rt.call(execute_tool(spec, args, ctx), timeout=spec.timeout_seconds + 30)
        finished = _now()
        status = "error" if result.error else "executed"
        self.rt.call(self._record(spec, args, call_id, status=status, preview=result.one_line,
                                  error=result.content[:500] if result.error else None,
                                  started=started, finished=finished))
        if result.error:
            raise RuntimeError(f"{spec.name}: {result.one_line or result.content[:200]}")
        return ExecutionReceipt(action_type=action.action_type, started_at=started,
                                completed_at=finished, effect_id=call_id,
                                metadata={"one_line": result.one_line, "data": dict(result.data),
                                          "source": spec.source})


# --------------------------------------------------- observation/verification

class CommandCenterObserver:
    """Свежее наблюдение через `bcc/v2/verification` — тот же код, что у
    review_gate. Ожидание объявляется в `action.args["expect"]` в форме
    ExpectedState V2: {"kind": file|db|browser|app, "target": ..., "expect": {...}}.
    Нет ожидания — статус UNVERIFIED: то, что инструмент отработал, само по
    себе шаг не подтверждает."""

    def __init__(self, rt: CommandCenterRuntime, svc: Any, *, task: Mapping[str, Any]):
        self.rt, self.svc, self.task = rt, svc, dict(task)

    async def _verify(self, raw: Any) -> dict:
        from bcc.features.tools_terminal import _roots
        from bcc.v2.verification import parse_expected, verify_all
        expected = parse_expected(raw if isinstance(raw, list) else [raw])
        if not expected:
            return {"status": "UNVERIFIED", "reason": "ожидание не объявлено или невалидно"}
        roots = await _roots(self.svc)
        status, reason, results = await verify_all(expected, svc=self.svc, task=self.task, roots=roots)
        return {"status": status, "reason": reason,
                "evidence": [f"{r.expected.kind}:{r.expected.target}={r.status}" for r in results]}

    def observe_fresh(self, action: TypedAction, receipt: ExecutionReceipt) -> Observation:
        raw = dict(action.args).get("expect")
        state = self.rt.call(self._verify(raw)) if raw is not None else \
            {"status": "UNVERIFIED", "reason": "шаг не объявил проверяемого ожидания"}
        return Observation(observed_at=_now(), source="bcc.v2.verification", state=state)


class CommandCenterVerifier:
    def verify(self, action: TypedAction, receipt: ExecutionReceipt,
               observation: Observation) -> VerificationResult:
        status = str(observation.state.get("status", "UNVERIFIED"))
        return VerificationResult(passed=(status == "VERIFIED"),
                                  reason=str(observation.state.get("reason", "")),
                                  evidence_refs=tuple(observation.state.get("evidence", ())))


# ------------------------------------------------------------------ factory

def build_agent(rt: CommandCenterRuntime, svc: Any, *, task: Mapping[str, Any],
                agent: Mapping[str, Any], run_id: int) -> UniversalComputerAgent:
    """Собрать UniversalComputerAgent, полностью привязанный к живому V2."""
    return UniversalComputerAgent(
        policy=CommandCenterPolicy(agent),
        approval=CommandCenterApproval(rt, svc, task_id=task.get("id")),
        executor=CommandCenterExecutor(rt, svc, task=task, agent=agent, run_id=run_id),
        observer=CommandCenterObserver(rt, svc, task=task),
        verifier=CommandCenterVerifier(),
    )
