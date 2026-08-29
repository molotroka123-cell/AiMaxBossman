from __future__ import annotations

import re
from dataclasses import replace
from typing import Protocol

from .models import MemoryKind, MemoryRecord, MemoryStatus
from .store import ContextStore
from .utils import stable_id, utcnow


class MemoryPlugin(Protocol):
    name: str
    def retrieve(self, query: str, project: str, limit: int) -> list[MemoryRecord]: ...
    def write_candidate(self, record: MemoryRecord) -> None: ...


class StoreMemoryPlugin:
    name = "local-store"
    def __init__(self, store: ContextStore) -> None: self.store=store
    def retrieve(self, query: str, project: str, limit: int) -> list[MemoryRecord]:
        q = {w.lower() for w in re.findall(r"\w{2,}",query)}
        mems = self.store.memories(project, (MemoryStatus.ACTIVE, MemoryStatus.DISPUTED))
        scored=[]
        for m in mems:
            words={w.lower() for w in re.findall(r"\w{2,}",m.text)}
            score=len(q & words)/max(1,len(q)) + m.importance*0.25 + m.confidence*0.15
            scored.append((score,m))
        return [m for _,m in sorted(scored,key=lambda x:x[0],reverse=True)[:limit]]
    def write_candidate(self, record: MemoryRecord) -> None: self.store.upsert_memory(record)


class MemoryManager:
    def __init__(self, store: ContextStore, plugins: list[MemoryPlugin] | None = None) -> None:
        self.store=store
        self.plugins=plugins or [StoreMemoryPlugin(store)]

    def candidate(self, kind: MemoryKind, text: str, *, project: str="", source_refs: list[str] | None=None,
                  confidence: float=.6, importance: float=.5, metadata: dict | None=None) -> MemoryRecord:
        now=utcnow()
        m=MemoryRecord(memory_id=stable_id("mem",kind.value,project,text),kind=kind,text=text.strip(),project=project,
                       status=MemoryStatus.CANDIDATE,confidence=max(0,min(1,confidence)),importance=max(0,min(1,importance)),
                       source_refs=source_refs or [],created_at=now,updated_at=now,metadata=metadata or {})
        self._detect_conflicts(m)
        for p in self.plugins: p.write_candidate(m)
        return m

    def promote(self, memory_id: str, *, verified: bool=False) -> MemoryRecord:
        row=self.store.db.execute("SELECT * FROM memories WHERE memory_id=?",(memory_id,)).fetchone()
        if not row: raise KeyError(memory_id)
        m=self.store._row_memory(row)
        m.status=MemoryStatus.ACTIVE if not m.contradicted_by else MemoryStatus.DISPUTED
        m.updated_at=utcnow()
        if verified: m.last_verified_at=m.updated_at; m.confidence=max(m.confidence,.9)
        self.store.upsert_memory(m); return m

    def supersede(self, old_id: str, new_id: str) -> None:
        for mid in (old_id,new_id):
            row=self.store.db.execute("SELECT * FROM memories WHERE memory_id=?",(mid,)).fetchone()
            if not row: raise KeyError(mid)
        old=self.store._row_memory(self.store.db.execute("SELECT * FROM memories WHERE memory_id=?",(old_id,)).fetchone())
        new=self.store._row_memory(self.store.db.execute("SELECT * FROM memories WHERE memory_id=?",(new_id,)).fetchone())
        old.status=MemoryStatus.SUPERSEDED; old.updated_at=utcnow()
        if old_id not in new.supersedes: new.supersedes.append(old_id)
        new.updated_at=utcnow(); self.store.upsert_memory(old); self.store.upsert_memory(new)

    def retrieve(self, query: str, *, project: str="", limit: int=12) -> list[MemoryRecord]:
        merged: dict[str,MemoryRecord]={}
        for p in self.plugins:
            for m in p.retrieve(query,project,limit): merged.setdefault(m.memory_id,m)
        return sorted(merged.values(),key=lambda m:(m.importance,m.confidence),reverse=True)[:limit]

    def _detect_conflicts(self, candidate: MemoryRecord) -> None:
        # Conservative deterministic conflict marker. It avoids claiming logical
        # contradiction unless texts share a strong lexical core and polarity differs.
        neg=re.compile(r"\b(no|not|never|disable|disabled|false|without|нет|не|никогда|отключ)\b",re.I)
        cwords={w.lower() for w in re.findall(r"\w{3,}",candidate.text)}
        cneg=bool(neg.search(candidate.text))
        for old in self.store.memories(candidate.project,(MemoryStatus.ACTIVE,MemoryStatus.DISPUTED)):
            if old.kind != candidate.kind: continue
            owords={w.lower() for w in re.findall(r"\w{3,}",old.text)}
            overlap=len(cwords&owords)/max(1,min(len(cwords),len(owords)))
            if overlap>=.65 and bool(neg.search(old.text)) != cneg:
                candidate.status=MemoryStatus.DISPUTED
                candidate.contradicted_by.append(old.memory_id)
