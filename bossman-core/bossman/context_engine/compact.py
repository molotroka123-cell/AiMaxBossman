from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Iterable, Protocol

from .models import MemoryKind, MemoryRecord
from .utils import normalize_space, token_estimate, unique_preserve


@dataclass(slots=True)
class Message:
    role: str
    content: str
    name: str = ""
    message_id: str = ""


@dataclass(slots=True)
class CompactResult:
    text: str
    input_tokens: int
    output_tokens: int
    preserved_recent_messages: int
    memory_refs: list[str] = field(default_factory=list)
    quality_checks: dict[str,bool] = field(default_factory=dict)


class CompactMemoryPlugin(Protocol):
    name: str
    def retrieve(self, query: str, project: str, limit: int) -> list[MemoryRecord]: ...


def _sentences(text: str) -> list[str]:
    parts=re.split(r"(?<=[.!?。！？])\s+|\n+", normalize_space(text))
    return [p.strip() for p in parts if p.strip()]


def _keywords(text: str) -> set[str]:
    stop={"this","that","with","from","have","will","как","что","это","для","или","при","уже","его","она","они","так"}
    return {w.lower() for w in re.findall(r"[\w\-./]{3,}",text) if w.lower() not in stop}


class CompactSkill:
    """Conversation compactor with memory-plugin hydration and quality guards.

    It is intentionally extractive by default: critical facts are copied, not
    paraphrased, reducing semantic drift. A future LLM summarizer may sit after
    this stage, but must pass the same anchors/quality checks.
    """
    def __init__(self, memory_plugins: list[CompactMemoryPlugin] | None=None) -> None:
        self.memory_plugins=memory_plugins or []

    def compact(self, messages: list[Message], *, project: str="", target_tokens: int=6000,
                keep_recent: int=8, query: str="") -> CompactResult:
        if not messages: return CompactResult("",0,0,0,quality_checks={"nonempty":True,"within_budget":True})
        raw="\n".join(f"{m.role}: {m.content}" for m in messages)
        input_tokens=token_estimate(raw)
        recent=messages[-keep_recent:] if keep_recent else []
        older=messages[:-keep_recent] if keep_recent else messages
        objective=query.strip() or next((m.content for m in reversed(messages) if m.role=="user"),"")
        keys=_keywords(objective)

        # High-signal extraction from older history: decisions, constraints,
        # errors, file paths, code symbols, numbers, explicit TODOs and user corrections.
        signal=[]
        marker=re.compile(r"\b(?:must|should|do not|don't|never|decision|todo|fix|bug|error|failed|pass|priority|require|"
                          r"нужно|надо|делаем|не делаем|обязательно|ошибка|баг|исправ|решение|приоритет|запомни)\b",re.I)
        technical=re.compile(r"(?:[\w.-]+/[\w./-]+|\b[A-Za-z_][A-Za-z0-9_]{2,}\(\)|\b\d+(?:\.\d+)?(?:%|GB|MB|k|K)?\b)")
        for m in older:
            for s in _sentences(m.content):
                sw=_keywords(s)
                relevance=len(keys&sw)/max(1,len(keys)) if keys else 0
                if marker.search(s) or technical.search(s) or relevance>=.20:
                    signal.append(f"[{m.role}] {s}")
        signal=unique_preserve(signal)

        memories: list[MemoryRecord]=[]
        for plugin in self.memory_plugins:
            try:
                memories.extend(plugin.retrieve(objective,project,12))
            except Exception:
                continue
        seen_mem=set(); dedup_mem=[]
        for m in memories:
            if m.memory_id not in seen_mem:
                seen_mem.add(m.memory_id); dedup_mem.append(m)
        memories=dedup_mem[:12]

        blocks=["# COMPACT HANDOFF",f"## Active objective\n{objective or '(not explicitly stated)'}"]
        if signal:
            blocks.append("## Preserved high-signal history\n"+"\n".join(f"- {x}" for x in signal))
        if memories:
            blocks.append("## Retrieved durable memory\n"+"\n".join(
                f"- [{m.kind.value}/{m.status.value}/{m.memory_id}] {m.text}" for m in memories))
        if recent:
            blocks.append("## Recent transcript (verbatim)\n"+"\n\n".join(f"[{m.role}] {m.content}" for m in recent))
        text="\n\n".join(blocks)

        # Budget reduction never removes recent verbatim messages first. Trim
        # extracted signal from the tail; memory/recent remain to preserve continuity.
        if token_estimate(text)>target_tokens and signal:
            while signal and token_estimate(text)>target_tokens:
                signal.pop()
                blocks=[b for b in blocks if not b.startswith("## Preserved high-signal history")]
                if signal: blocks.insert(2,"## Preserved high-signal history\n"+"\n".join(f"- {x}" for x in signal))
                text="\n\n".join(blocks)
        out_tokens=token_estimate(text)
        checks={
            "nonempty": bool(text.strip()),
            "within_budget": out_tokens<=target_tokens or input_tokens<=target_tokens,
            "recent_preserved": all(m.content in text for m in recent),
            "memory_provenance_preserved": all(m.memory_id in text for m in memories),
            "objective_preserved": (not objective) or objective in text,
        }
        return CompactResult(text,input_tokens,out_tokens,len(recent),[m.memory_id for m in memories],checks)
