"""Wiring: the memory lifecycle over the REAL Command Center stores.

``learning.lifecycle`` deliberately knows nothing about Command Center — it is part of the
dependency-free ``bossman-shared`` distribution, and the root test suite is not allowed to
import ``bcc`` at all. This module is the other half: it adapts the real vault, the real
derived index and the real fact store to the ports that lifecycle expects.

Nothing here stores anything. Notes are written by ``ObsidianVault.write_memory`` (the
single canonical writer, atomic and path-contained), the derived index stays the one
``tools_memory.build_service`` already built, facts stay in ``bcc.db``, and
episodes/lessons/skills stay in ``LearningStore``.

Async boundary. ``UnifiedRetriever`` is synchronous on purpose: the root suite that proves
restart recall has no event loop and no async plugin. The vault backends already expose
``*_sync`` methods, so notes need no bridge. Facts are async-only, so they are read ONCE
per lifecycle boundary into a snapshot — which is what the contract asks for anyway
("refresh at task/phase boundaries, not every model token/call").

Version guard: ``learning`` is installed as a distribution (``bossman-shared``). If the
installed copy predates the lifecycle modules, ``build_lifecycle`` raises
``LifecycleUnavailable`` naming the fix, instead of failing later with an obscure
ImportError.
"""
from __future__ import annotations

from pathlib import Path
from typing import Any

DEFAULT_LEARNING_SUBDIR = "learning"
DEFAULT_STATE_SUBDIR = "memory-lifecycle"
DEFAULT_FACTS_SNAPSHOT = 500


class LifecycleUnavailable(RuntimeError):
    """The installed bossman-shared has no memory lifecycle."""


def _learning_modules():
    try:
        from learning import lesson_format, lifecycle, retrieval  # noqa: WPS433
        from learning.lessons import LessonBook                   # noqa: WPS433
    except ImportError as exc:                                    # pragma: no cover - env dependent
        raise LifecycleUnavailable(
            "the installed `bossman-shared` distribution has no memory lifecycle "
            f"({exc}); reinstall it from this checkout: pip install -e .") from exc
    return lifecycle, retrieval, lesson_format, LessonBook


def available() -> bool:
    try:
        _learning_modules()
    except LifecycleUnavailable:
        return False
    return True


# ---------------------------------------------------------------- notes port
class VaultNotes:
    """``NotesPort`` over ``ObsidianVault`` + the derived index backend.

    Reads go to the index when it can answer and to the canonical Markdown when they must
    (the exact pass). Writes go to ``ObsidianVault.write_memory`` and nowhere else.
    """

    def __init__(self, vault: Any, backend: Any) -> None:
        self.vault = vault
        self.backend = backend

    # -- read
    def iter_notes(self):
        root = Path(self.vault.root)
        for path in self.vault.iter_markdown():
            try:
                yield str(Path(path).relative_to(root)).replace("\\", "/"), \
                    Path(path).read_text(encoding="utf-8")
            except (OSError, ValueError):
                continue

    def search(self, query: str, *, top_k: int) -> list[dict]:
        search_sync = getattr(self.backend, "search_sync", None)
        if not callable(search_sync):
            # An async-only backend (the memsearch bridge) cannot be driven from here;
            # say so rather than silently returning nothing, and let the exact pass work.
            raise RuntimeError(f"{type(self.backend).__name__} has no synchronous search")
        hits = search_sync(query, top_k=top_k)
        return [{"ref": h.source, "title": h.heading or h.source, "text": h.content,
                 "score": float(h.score or 0.0), "chunk_hash": h.chunk_hash} for h in hits]

    def expand(self, ref: str) -> str:
        root = Path(self.vault.root).resolve()
        path = (root / ref).resolve()
        if root not in path.parents and path != root:
            raise PermissionError(f"note reference escapes the vault: {ref}")
        return path.read_text(encoding="utf-8") if path.is_file() else ""

    # -- write
    def writable(self) -> bool:
        try:
            return Path(self.vault.write_root).is_dir()
        except (OSError, PermissionError):
            return False

    def write_note(self, **kwargs) -> str:
        path = self.vault.write_memory(**kwargs)
        index_one = getattr(self.backend, "index_one_sync", None)
        if callable(index_one):
            # Keep the derived index current for exactly the one note that changed;
            # a full rescan here was measured in the vault work as the wrong default.
            try:
                index_one(path, force=False)
            except Exception:  # noqa: BLE001 — a derived index is rebuildable
                pass
        return str(Path(path).relative_to(Path(self.vault.root))).replace("\\", "/")

    def reindex(self) -> None:
        """BOOT calls this: the derived index is rebuilt from the canonical notes."""
        index_sync = getattr(self.backend, "index_sync", None)
        if callable(index_sync):
            index_sync(self.vault.markdown_roots(), force=False)

    def state_token(self) -> str:
        stats = getattr(self.backend, "stats_sync", None)
        try:
            return repr(stats()) if callable(stats) else "?"
        except Exception:  # noqa: BLE001
            return "?"


# ---------------------------------------------------------------- facts port
class FactsSnapshot:
    """``FactsPort`` over rows already read from the async ``FactStore``.

    A snapshot, not a live handle: the lifecycle refreshes at task/phase boundaries, so
    one read per boundary is the intended shape rather than a limitation to apologise for.
    """

    def __init__(self, rows: list[dict]) -> None:
        self.rows = [dict(r) for r in rows]

    def search(self, query: str, *, limit: int) -> list[dict]:
        if not query:
            return self.rows[:limit]
        terms = [t for t in str(query).lower().split() if len(t) > 2]
        hits = []
        for row in self.rows:
            blob = " ".join(str(row.get(k) or "") for k in
                            ("subject", "predicate", "object", "statement")).lower()
            if any(t in blob for t in terms):
                hits.append(row)
        return hits[:limit]

    def state_token(self) -> str:
        return f"{len(self.rows)}:{self.rows[-1].get('id') if self.rows else ''}"


async def facts_snapshot(svc: Any, *, limit: int = DEFAULT_FACTS_SNAPSHOT) -> FactsSnapshot:
    """Read the current facts once. Failure degrades the layer, it never denies memory."""
    try:
        from .facts import FactStore  # noqa: WPS433 — local import keeps this module light
        rows = await FactStore(svc).search(limit=limit, current_only=True)
    except Exception:  # noqa: BLE001
        rows = []
    return FactsSnapshot(rows)


# ---------------------------------------------------------------- assembly
def learning_dir(svc: Any) -> Path:
    return Path(svc.settings.data_dir) / DEFAULT_LEARNING_SUBDIR


def state_dir(svc: Any) -> Path:
    return Path(svc.settings.data_dir) / DEFAULT_STATE_SUBDIR


async def build_lifecycle(svc: Any, *, context_tokens: int | None = None) -> Any:
    """The lifecycle over this instance's real stores.

    The memory service is the one ``tools_memory`` already configures, so the vault path
    still comes only from the owner's encrypted setting and no vault is ever discovered
    automatically. A memory service that is not configured yields a lifecycle with no
    notes layer, which BOOT reports as DEGRADED rather than pretending.
    """
    lifecycle, retrieval, _fmt, LessonBook = _learning_modules()

    notes = None
    try:
        from ...features import tools_memory  # noqa: WPS433 — optional feature
        service = await tools_memory.get_service(svc)
        notes = VaultNotes(service.vault, service.backend)
    except Exception:  # noqa: BLE001 — not configured / not installed: a degraded layer
        notes = None

    config = retrieval.RetrievalConfig(
        context_tokens=context_tokens or retrieval.DEFAULT_CONTEXT_TOKENS)
    return lifecycle.MemoryLifecycle(
        state_dir=state_dir(svc),
        notes=notes,
        facts=await facts_snapshot(svc),
        lessons=LessonBook(learning_dir(svc)),
        config=config,
    )


__all__ = ["DEFAULT_FACTS_SNAPSHOT", "FactsSnapshot", "LifecycleUnavailable", "VaultNotes",
           "available", "build_lifecycle", "facts_snapshot", "learning_dir", "state_dir"]
