"""Fable hardening — ContextCompiler keeps invariants, objective and tools under budget.

The compiler appended [NEXT_ACTION] and [TOOLS] last and then sliced the tail,
while its comment claimed the objective was "already at the front". Under a
tight budget the objective was the first thing lost. Now the protected head goes
first, the budget drops whole trailing parts, and nothing is cut mid-part.
"""
from __future__ import annotations

from bcc.context_os.compiler import ContextCompiler
from bcc.context_os.hierarchical import HierarchicalContextManager


def _hcm(project_text: str, task_text: str) -> HierarchicalContextManager:
    return HierarchicalContextManager(
        global_text="global rules",
        project_loader=lambda pid: project_text,
        task_loader=lambda tid: task_text,
        step_loader=lambda rid, st: "step notes " * 50,
    )


async def test_objective_and_tools_survive_a_tight_budget():
    compiler = ContextCompiler(_hcm("project " * 400, "task " * 400))
    ctx = await compiler.request(task_id=1, run_id=1, step=0, objective="fix the failing test in auth.py",
                                 max_tokens=120, include=["next_action"],
                                 available_tools=["terminal.run", "fs.read"])
    assert ctx.truncated is True
    assert ctx.prompt.startswith("[INVARIANTS]")
    assert "[NEXT_ACTION]\nfix the failing test in auth.py" in ctx.prompt
    assert "[TOOLS]\nterminal.run, fs.read" in ctx.prompt
    assert "…[truncated by ContextCompiler budget]" not in ctx.prompt
    assert "dropped by ContextCompiler budget" in ctx.prompt
    # whatever body parts remain are whole, never a sliced fragment
    for part in ctx.prompt.split("\n\n"):
        assert not part.endswith("…") or "dropped" in part


async def test_within_budget_nothing_is_dropped_and_head_still_leads():
    compiler = ContextCompiler(_hcm("short project", "short task"))
    ctx = await compiler.request(task_id=1, objective="do it", max_tokens=8000,
                                 include=["next_action"], available_tools=["fs.read"])
    assert ctx.truncated is False
    order = [ctx.prompt.index(tag) for tag in ("[INVARIANTS]", "[NEXT_ACTION]", "[TOOLS]", "[GLOBAL]")]
    assert order == sorted(order), "protected head precedes the layers"
