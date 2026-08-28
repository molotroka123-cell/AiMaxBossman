from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any, Protocol

from .context_pack import ContextPack, build_context_pack
from .memsearch_bridge import MemoryHit
from .obsidian import ObsidianVault
from .reranker import RerankerUnavailable


class MemoryBackend(Protocol):
    """Контракт backend'а: его выполняют и `MemSearchBridge` (внешний бинарь),
    и встроенный `LocalMemoryBackend` (BM25 на stdlib)."""

    def available(self) -> bool: ...
    async def index(self, paths: list[Path], *, force: bool = False) -> Any: ...
    async def search(self, query: str, *, top_k: int = 16) -> list[MemoryHit]: ...
    async def expand(self, chunk_hash: str) -> dict[str, Any]: ...
    async def stats(self) -> Any: ...


@dataclass
class ObsidianMemoryService:
    vault: ObsidianVault
    backend: MemoryBackend
    reranker: Any = None

    async def index(self, *, force: bool = False) -> Any:
        return await self.backend.index(self.vault.markdown_roots(), force=force)

    async def stats(self) -> Any:
        return await self.backend.stats()

    async def expand(self, chunk_hash: str) -> dict[str, Any]:
        return await self.backend.expand(chunk_hash)

    async def search(
        self,
        query: str,
        *,
        candidate_k: int = 16,
        rerank_k: int = 8,
        expand_k: int = 4,
        context_tokens: int = 7000,
    ) -> ContextPack:
        hits = await self.backend.search(query, top_k=candidate_k)
        if self.reranker is not None and hits:
            try:
                hits = self.reranker.rerank(query, hits, top_k=rerank_k)
            except RerankerUnavailable:
                hits = hits[:rerank_k]
            except Exception:
                # переранжировщик — улучшение, а не условие работы памяти
                hits = hits[:rerank_k]
        else:
            hits = hits[:rerank_k]

        # Progressive disclosure:
        # expand only the highest-ranked chunks that have an anchor.
        expanded: list[MemoryHit] = []
        for hit in hits[:expand_k]:
            if not hit.chunk_hash:
                expanded.append(hit)
                continue
            try:
                detail = await self.backend.expand(hit.chunk_hash)
                content = str(
                    detail.get("content")
                    or detail.get("section")
                    or detail.get("text")
                    or hit.content
                )
                expanded.append(MemoryHit(
                    content=content,
                    source=str(detail.get("source") or hit.source),
                    heading=str(detail.get("heading") or hit.heading),
                    score=hit.score,
                    chunk_hash=hit.chunk_hash,
                    metadata=detail,
                ))
            except Exception:
                expanded.append(hit)

        expanded.extend(hits[expand_k:])
        return build_context_pack(query, expanded, max_tokens=context_tokens)

    async def remember(
        self,
        *,
        title: str,
        content: str,
        kind: str = "note",
        project: str = "",
        tags: list[str] | None = None,
        source_run_id: str | int | None = None,
        filename: str | None = None,
    ) -> Path:
        path = self.vault.write_memory(
            title=title,
            content=content,
            kind=kind,  # type: ignore[arg-type]
            project=project,
            tags=tags,
            source_run_id=source_run_id,
            filename=filename,
        )
        # Критическая правка пути записи (замерено исследованием Qdrant:
        # пересканирование корня стоило 2.4 с на 6k чанков и ~38 с на 100k).
        # Если бэкенд умеет обновить ОДНУ заметку — обновляем только её.
        index_one = getattr(self.backend, "index_one", None)
        if callable(index_one):
            await index_one(path, force=False)
        else:
            await self.backend.index([self.vault.write_root], force=False)
        return path
