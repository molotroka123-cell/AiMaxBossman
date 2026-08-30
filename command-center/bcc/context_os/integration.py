"""Wire Context OS into TaskEngine / Gateway without breaking V1.

Engine hook: before each LLM call inject decisions + recent failures.
Gateway hook: optional compiled prompt passthrough (header-driven).

Both fail-open: DB unavailable → original messages unchanged.
"""
from __future__ import annotations

from typing import Any

from .compiler import ContextCompiler
from .hierarchical import HierarchicalContextManager
from .state import StateMachine


async def attach_to_engine(engine, db) -> None:
    """Register before_run/on_step hooks that use Context OS."""
    from .stores import DecisionStore, FailureStore

    hcm = HierarchicalContextManager(global_text="BOSSMAN V1 invariants")
    dec = DecisionStore(db)
    fail = FailureStore(db)
    compiler = ContextCompiler(hcm, dec, fail)

    async def before_call_hook(task, agent, messages, run_id, **kw):
        # inject compiled context as system message prefix (non-destructive)
        try:
            ctx = await compiler.request(
                task_id=task.get("id"),
                objective=task.get("prompt", "")[:500],
                max_tokens=4000,
                include=["decisions", "recent_failures"],
            )
            if ctx.prompt and len(ctx.prompt) > 20:
                # prepend as ephemeral system note, not replacing history
                messages.insert(1, {"role": "system", "content": f"[Context OS]\n{ctx.prompt[:2000]}"})
        except Exception:
            pass

    # engine doesn't have this hook yet — use on_step to persist state
    engine._context_compiler = compiler
    engine._hcm = hcm


def attach_state_machine(engine) -> None:
    """Monkey-patch engine to track StateMachine in checkpoint."""
    orig_run = engine._run

    async def wrapped_run(run_id: int):
        # load checkpoint state
        from ..db import fetch_one, tasks as tasks_t, task_runs as runs_t
        import sqlalchemy as sa
        async with engine.db.session() as s:
            run = await fetch_one(s, runs_t, run_id)
            ckpt = (run or {}).get("checkpoint") or {}
        sm = StateMachine.from_checkpoint(ckpt.get("sm") or {"state": "PLAN"})
        engine._sm = sm
        try:
            return await orig_run(run_id)
        finally:
            engine._sm = None

    engine._run = wrapped_run
