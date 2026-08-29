from __future__ import annotations

import json
from pathlib import Path

from .models import MemoryKind, MemoryRecord, MemoryStatus
from .utils import stable_id


class MarkdownMemoryPlugin:
    """Read-only memory plugin for existing Markdown memory folders.

    Each non-empty bullet/paragraph becomes a retrievable memory item. Optional
    front matter is not required. This lets BOSSMAN reuse Obsidian-like memory
    folders without making them authoritative automatically.
    """
    name = "markdown-memory"
    def __init__(self, root: str | Path) -> None:
        self.root = Path(root)

    def retrieve(self, query: str, project: str, limit: int) -> list[MemoryRecord]:
        if not self.root.exists():
            return []
        q = {w.lower() for w in query.split() if len(w) > 2}
        scored: list[tuple[float, MemoryRecord]] = []
        for p in self.root.rglob("*.md"):
            text = p.read_text(encoding="utf-8", errors="replace")
            for block in [x.strip(" -\t") for x in text.split("\n\n") if x.strip()]:
                words = {w.lower().strip(".,:;!?()[]{}") for w in block.split()}
                score = len(q & words) / max(1, len(q)) if q else 0.0
                if score <= 0:
                    continue
                rel = str(p.relative_to(self.root))
                rec = MemoryRecord(
                    memory_id=stable_id("mdmem", rel, block), kind=MemoryKind.SUMMARY, text=block,
                    project=project, status=MemoryStatus.ACTIVE, confidence=.65, importance=.5,
                    source_refs=[f"file://{p}"], metadata={"plugin": self.name, "path": str(p)},
                )
                scored.append((score, rec))
        return [r for _, r in sorted(scored, key=lambda x: x[0], reverse=True)[:limit]]

    def write_candidate(self, record: MemoryRecord) -> None:
        raise RuntimeError("MarkdownMemoryPlugin is read-only; use StoreMemoryPlugin for writes")


class JsonMemoryPlugin:
    """Read-only bridge for exported plugin memories in a simple JSON format."""
    name = "json-memory"
    def __init__(self, path: str | Path) -> None:
        self.path = Path(path)

    def retrieve(self, query: str, project: str, limit: int) -> list[MemoryRecord]:
        if not self.path.exists(): return []
        data = json.loads(self.path.read_text(encoding="utf-8"))
        records = data if isinstance(data, list) else data.get("memories", [])
        q={w.lower() for w in query.split() if len(w)>2}; scored=[]
        for item in records:
            text=str(item.get("text","")).strip()
            if not text: continue
            words={w.lower().strip(".,:;!?()[]{}") for w in text.split()}
            score=len(q&words)/max(1,len(q)) if q else 0
            if score<=0: continue
            kind_raw=str(item.get("kind","summary"))
            try: kind=MemoryKind(kind_raw)
            except ValueError: kind=MemoryKind.SUMMARY
            rec=MemoryRecord(memory_id=str(item.get("memory_id") or stable_id("jsonmem",str(self.path),text)),kind=kind,
                             text=text,project=str(item.get("project") or project),status=MemoryStatus.ACTIVE,
                             confidence=float(item.get("confidence",.65)),importance=float(item.get("importance",.5)),
                             source_refs=list(item.get("source_refs") or [f"file://{self.path}"]),metadata={"plugin":self.name})
            scored.append((score,rec))
        return [r for _,r in sorted(scored,key=lambda x:x[0],reverse=True)[:limit]]

    def write_candidate(self, record: MemoryRecord) -> None:
        raise RuntimeError("JsonMemoryPlugin is read-only")
