from __future__ import annotations

from dataclasses import dataclass

from .compact import CompactSkill, Message
from .memory import MemoryManager
from .models import CompiledContext, ContextSection
from .retrieval import HybridRetriever
from .utils import token_estimate


@dataclass(slots=True)
class ContextBudget:
    total: int = 16000
    system: int = 2500
    task: int = 2500
    memory: int = 3500
    retrieval: int = 6500
    reserve_output: int = 1000

    @classmethod
    def adaptive(cls, model_window: int, desired_output: int = 2048) -> "ContextBudget":
        usable=max(2048,model_window-desired_output)
        return cls(total=usable,system=max(800,int(usable*.12)),task=max(800,int(usable*.13)),
                   memory=max(1000,int(usable*.20)),retrieval=max(1200,int(usable*.45)),reserve_output=desired_output)


class ContextCompiler:
    def __init__(self, retriever: HybridRetriever, memory: MemoryManager) -> None:
        self.retriever=retriever; self.memory=memory

    @staticmethod
    def _clip(text: str, budget: int) -> str:
        if token_estimate(text)<=budget: return text
        # conservative char approximation; never creates synthetic wording
        return text[:budget*3].rstrip()+"\n[context clipped by budget]"

    def compile(self, *, model: str, query: str, project: str="", system: str="", task_state: str="",
                model_window: int=32768, desired_output: int=2048) -> CompiledContext:
        b=ContextBudget.adaptive(model_window,desired_output)
        sections=[]
        if system:
            x=self._clip(system,b.system); sections.append(ContextSection("System",x,token_estimate(x),100))
        task=self._clip(f"Query: {query}\n\nState:\n{task_state}".strip(),b.task)
        sections.append(ContextSection("Active task",task,token_estimate(task),95))

        mems=self.memory.retrieve(query,project=project,limit=14)
        memtext="\n".join(f"- [{m.kind.value}/{m.status.value}/{m.memory_id}] {m.text} | sources={','.join(m.source_refs)}" for m in mems)
        memtext=self._clip(memtext,b.memory)
        if memtext: sections.append(ContextSection("Relevant memory",memtext,token_estimate(memtext),85,[m.memory_id for m in mems]))

        hits=self.retriever.search(query,project=project,result_limit=18)
        parts=[]; refs=[]; used=0
        for h in hits:
            block=f"### {h.chunk.source_uri} :: {h.chunk.heading or h.chunk.chunk_id}\n{h.chunk.text}\n[source={h.chunk.chunk_id}; score={h.final_score:.3f}]"
            t=token_estimate(block)
            if used+t>b.retrieval: continue
            used+=t; parts.append(block); refs.append(h.chunk.chunk_id)
        if parts: sections.append(ContextSection("Retrieved evidence","\n\n".join(parts),used,80,refs))

        total=sum(s.tokens for s in sections)
        telemetry={"raw_candidates":len(hits),"retrieved_sources":len(refs),"memories":len(mems),"used_tokens":total,
                   "budget_tokens":b.total,"reserve_output":b.reserve_output,"utilization":round(total/max(1,b.total),4)}
        return CompiledContext(model,b.total,total,sections,telemetry)
