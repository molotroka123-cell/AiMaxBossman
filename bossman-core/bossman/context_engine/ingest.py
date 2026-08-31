from __future__ import annotations

from pathlib import Path

from .chunking import chunk_document
from .embeddings import Embedder
from .models import Document
from .store import ContextStore
from .utils import sha256_text, stable_id, utcnow

_TEXT_EXTS = {".md",".txt",".py",".js",".ts",".tsx",".jsx",".json",".yaml",".yml",".toml",".html",".css",".sql",".sh",".rs",".go",".java",".kt",".swift",".c",".h",".cpp",".hpp"}


class Ingestor:
    def __init__(self, store: ContextStore, embedder: Embedder) -> None:
        self.store = store
        self.embedder = embedder

    def ingest_text(self, text: str, *, source_uri: str, source_type: str = "text", project: str = "",
                    metadata: dict | None = None, sensitivity: str = "normal") -> Document:
        now = utcnow(); h = sha256_text(text)
        document_id = stable_id("doc", source_uri, h)
        doc = Document(
            document_id=document_id,source_type=source_type,source_uri=source_uri,text=text,project=project,
            created_at=now,updated_at=now,metadata=metadata or {},content_hash=h,sensitivity=sensitivity,
        )
        # Fast path: тот же source_uri + тот же контент → тот же document_id.
        # Если он уже проиндексирован, повторный chunk+embed не нужен (это ровно
        # те же данные). Убирает переэмбеддинг неизменного memory.md на каждой
        # задаче — см. docs/context/FABLE5_GENERAL_OPTIMIZATION_AUDIT.md.
        if self.store.document_indexed(document_id):
            return doc
        chunks = chunk_document(doc)
        vectors = self.embedder.embed([c.text for c in chunks]) if chunks else []
        self.store.upsert_document(doc)
        self.store.replace_chunks(doc.document_id, chunks, {c.chunk_id:v for c,v in zip(chunks,vectors)})
        return doc

    def ingest_file(self, path: str | Path, *, project: str = "", source_root: str | Path | None = None) -> Document | None:
        p = Path(path)
        if p.suffix.lower() not in _TEXT_EXTS or not p.is_file():
            return None
        text = p.read_text(encoding="utf-8", errors="replace")
        uri = str(p.relative_to(source_root)) if source_root else str(p)
        source_type = p.suffix.lower().lstrip(".") or "text"
        return self.ingest_text(text, source_uri=uri, source_type=source_type, project=project, metadata={"path": str(p)})

    def ingest_tree(self, root: str | Path, *, project: str = "", max_file_bytes: int = 2_000_000) -> list[Document]:
        root = Path(root)
        ignored = {".git","node_modules","vendor","dist","build","__pycache__",".venv","venv"}
        out: list[Document] = []
        for p in root.rglob("*"):
            if any(part in ignored for part in p.parts):
                continue
            if p.is_file() and p.suffix.lower() in _TEXT_EXTS and p.stat().st_size <= max_file_bytes:
                doc = self.ingest_file(p, project=project, source_root=root)
                if doc: out.append(doc)
        return out
