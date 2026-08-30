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

        if "next_action" in include and objective:
            parts.append(f"[NEXT_ACTION]\n{objective}")

        if available_tools:
            parts.append(f"[TOOLS]\n{', '.join(available_tools)}")

        # invariants всегда
        parts.insert(0, "[INVARIANTS]\nBe deterministic. Prefer typed actions over free text.")

        raw = "\n\n".join(parts)
        # жёсткий бюджет — режем хвост, objective/invariants уже в начале
        from .hierarchical import _estimate_tokens, _hash_text
        est = _estimate_tokens(raw)
        truncated = False
        if est > max_tokens:
            raw = raw[: max_tokens * 4 - 200] + "\n…[truncated by ContextCompiler budget]"
            est = max_tokens
            truncated = True

        return CompiledContext(prompt=raw, tokens_est=est, layers=layers,
                               hash=_hash_text(raw), truncated=truncated)
