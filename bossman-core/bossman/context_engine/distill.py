from __future__ import annotations

import re
from dataclasses import dataclass

from .memory import MemoryManager
from .models import MemoryKind, MemoryRecord
from .utils import unique_preserve


@dataclass(slots=True)
class DistillReport:
    candidates: list[MemoryRecord]
    source_refs: list[str]


class KnowledgeDistiller:
    """Conservative candidate extractor. It never auto-promotes durable memory."""
    RULES=[
        (MemoryKind.DECISION,re.compile(r"\b(?:decision|decided|we will|делаем|решили|решение)\b",re.I),.82),
        (MemoryKind.FAILURE,re.compile(r"\b(?:bug|error|failed|failure|баг|ошибка|не сработ)\b",re.I),.76),
        (MemoryKind.CONSTRAINT,re.compile(r"\b(?:must|never|do not|required|обязательно|нельзя|не должен)\b",re.I),.82),
        (MemoryKind.TODO,re.compile(r"\b(?:todo|next|later|надо|нужно|потом|следующ)\b",re.I),.66),
        (MemoryKind.PROCEDURE,re.compile(r"\b(?:steps|procedure|workflow|pipeline|этап|порядок)\b",re.I),.70),
    ]
    def __init__(self, memory: MemoryManager) -> None: self.memory=memory
    def extract(self, text: str, *, project: str="", source_refs: list[str] | None=None) -> DistillReport:
        sentences=[s.strip() for s in re.split(r"(?<=[.!?])\s+|\n+",text) if len(s.strip())>=12]
        candidates=[]
        for s in unique_preserve(sentences):
            for kind,pat,conf in self.RULES:
                if pat.search(s):
                    candidates.append(self.memory.candidate(kind,s,project=project,source_refs=source_refs or [],confidence=conf))
                    break
        return DistillReport(candidates,source_refs or [])
