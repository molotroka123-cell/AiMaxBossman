"""Wire Context OS into TaskEngine / Gateway — НЕ ПОДКЛЮЧЕНО (F-018).

Исторически: `attach_to_engine` собирал `before_call_hook` и... не регистрировал
его нигде (у bcc TaskEngine нет хука before_call) — только вешал компилятор
атрибутом на engine. Ни один вызывающий в репозитории его не звал. Чтобы никто
не поверил, что «Context OS защищает/фильтрует контекст», функция теперь бросает
NotImplementedError с явным сообщением. Каноничный контекст — bossman-core
`bossman.context_engine`.

`attach_state_machine` оставлен как библиотечный monkey-patch без вызывающих:
он тоже ничего не защищает (только state в checkpoint).
"""
from __future__ import annotations

from .state import StateMachine

NOT_WIRED_MESSAGE = (
    "bcc.context_os.integration.attach_to_engine is NOT WIRED — non-protective. "
    "bcc TaskEngine has no before_call hook; the canonical context engine is "
    "bossman-core bossman.context_engine. See docs/security/F018_DEAD_CODE_DISPOSITIONS.md"
)


async def attach_to_engine(engine, db) -> None:
    """Раньше: «Register before_run/on_step hooks that use Context OS».
    Хук никогда не регистрировался (dead by bug) — честный отказ вместо
    иллюзии защиты."""
    raise NotImplementedError(NOT_WIRED_MESSAGE)


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
