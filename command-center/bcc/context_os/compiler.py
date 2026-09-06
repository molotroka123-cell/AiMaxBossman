"""Context Compiler — собирает prompt из слоёв + 5 каналов памяти."""
from __future__ import annotations

from dataclasses import dataclass

from .hierarchical import HierarchicalContextManager, TokenBudgeter
from .stores import DecisionStore, FailureStore


@dataclass
class CompiledContext:
    prompt: str
    tokens_est: int
    layers: list
    hash: str
    truncated: bool


class ContextCompiler:
    """Единственная точка сборки prompt. Агент сам не собирает контекст."""

    def __init__(self, hcm: HierarchicalContextManager,
                 decision_store: DecisionStore | None = None,
                 failure_store: FailureStore | None = None):
        self.hcm = hcm
        self.decisions = decision_store
        self.failures = failure_store

    async def request(self, *, task_id: int | None = None,
                      objective: str = "",
                      max_tokens: int = 8000,
                      include: list[str] | None = None,
                      project_id=None, run_id=None, step=None,
                      current_diff: str = "",
                      available_tools: list[str] | None = None) -> CompiledContext:
        include = include or []
        # 1. иерархия
        layers = await self.hcm.assemble(project_id=project_id, task_id=task_id,
                                         run_id=run_id, step=step, max_tokens=max_tokens)
        parts: list[str] = []
        for lyr in layers:
            if lyr.text:
                parts.append(f"[{lyr.name.upper()}]\n{lyr.text}")

        # 2. каналы по include (белый список, никакого all/*)
        if "decisions" in include and self.decisions:
            decs = await self.decisions.list()
            if decs:
                parts.append("[DECISIONS]\n" + "\n".join(
                    f"- {d['key']}: {d['decision']} (reason: {d['reason']})" for d in decs[-5:]))

        if "recent_failures" in include and self.failures:
            fails = await self.failures.list_recent(limit=3)
            if fails:
                parts.append("[RECENT_FAILURES]\n" + "\n".join(
                    f"- {f['symptom']} → {f['root_cause']} (fix: {f['attempted_fix']})" for f in fails))

        if "relevant_facts" in include:
            parts.append("[RELEVANT_FACTS]\n(facts via FactStore — stub for POC)")

        if "current_diff" in include and current_diff:
            parts.append(f"[CURRENT_DIFF]\n{current_diff[:2000]}")

        # Protected head: invariants, the objective and the tool surface are what
        # the model must never lose. They go FIRST, and the budget never cuts them.
        # The old code appended NEXT_ACTION and TOOLS last and then sliced the
        # tail — the comment said "objective already at the front" while the
        # objective was the first thing truncated.
        head: list[str] = ["[INVARIANTS]\nBe deterministic. Prefer typed actions over free text."]
        if "next_action" in include and objective:
            head.append(f"[NEXT_ACTION]\n{objective}")
        if available_tools:
            head.append(f"[TOOLS]\n{', '.join(available_tools)}")

        from .hierarchical import _estimate_tokens, _hash_text
        truncated = False
        # Budget: drop whole droppable parts from the END (step/task layers and
        # channels are appended after the stable layers), never slice a part in
        # the middle, and never touch the head. A dropped part is marked so the
        # loss is visible in the prompt and in `truncated`.
        body = list(parts)
        while body and _estimate_tokens("\n\n".join(head + body)) > max_tokens:
            body.pop()
            truncated = True
        if truncated:
            body.append("…[context parts dropped by ContextCompiler budget; invariants/objective/tools kept]")
        raw = "\n\n".join(head + body)
        est = _estimate_tokens(raw)

        return CompiledContext(prompt=raw, tokens_est=est, layers=layers,
                               hash=_hash_text(raw), truncated=truncated)
